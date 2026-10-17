"""Read live employer boards so older worldwide openings remain discoverable."""

from __future__ import annotations

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import yaml

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job
from scanner.sources import _more_ats

log = logging.getLogger(__name__)
USER_AGENT = "job-hunt-scanner (personal use)"
_RATE_LOCK = threading.Lock()
_NEXT_REQUEST: dict[str, float] = {}


def _pace(url: str) -> None:
    """Space requests per provider: every <company>.recruitee.com shares Recruitee's rate limit."""
    host = ".".join((urlparse(url).hostname or "").split(".")[-2:])
    interval = 1.0 if host == "workable.com" else 0.25
    with _RATE_LOCK:
        now = time.monotonic()
        scheduled = max(now, _NEXT_REQUEST.get(host, 0))
        _NEXT_REQUEST[host] = scheduled + interval
    if scheduled > now:
        time.sleep(scheduled - now)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"p", "br", "li", "div", "h1", "h2", "h3"}:
            self.parts.append("\n")


def plain(value: str | None) -> str:
    parser = _Text()
    parser.feed(value or "")
    return re.sub(r"[ \t]+", " ", unescape("".join(parser.parts))).strip()


def get(session: requests.Session, url: str, *, params: dict | None = None, retries: int = 3,
        timeout: int = 12) -> dict | list:
    for attempt in range(retries):
        _pace(url)
        response = session.get(url, params=params, timeout=timeout)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 < retries:
                try:
                    wait = float(response.headers.get("Retry-After") or 2 ** attempt)
                except ValueError:
                    wait = 2 ** attempt
                time.sleep(min(8, max(0, wait)))
                continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"ATS request failed: {url}")


def get_text(session: requests.Session, url: str, *, params: dict | None = None, retries: int = 3,
             timeout: int = 12) -> str:
    for attempt in range(retries):
        _pace(url)
        response = session.get(url, params=params, timeout=timeout)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 < retries:
                try:
                    wait = float(response.headers.get("Retry-After") or 2 ** attempt)
                except ValueError:
                    wait = 2 ** attempt
                time.sleep(min(8, max(0, wait)))
                continue
        response.raise_for_status()
        return response.text
    raise RuntimeError(f"ATS request failed: {url}")


def workable_markdown(session: requests.Session, board: str) -> list[dict]:
    """Use Workable's small public feed when its widget API is rate limited."""
    url = f"https://apply.workable.com/{quote(board, safe='')}/jobs.md"
    _pace(url)
    response = session.get(url, timeout=10)
    response.raise_for_status()
    rows = []
    for line in response.text.splitlines():
        if not line.startswith("|") or "[View]" not in line:
            continue
        cols = [x.strip() for x in line.split("|")]
        match = re.search(r"\[View\]\((https://apply\.workable\.com/[^)]+)\)", line)
        if len(cols) < 8 or not match or not cols[1] or cols[1] == "Title":
            continue
        detail_url = match.group(1)
        jid = detail_url.removesuffix(".md").rstrip("/").rsplit("/", 1)[-1]
        rows.append({"shortcode": jid, "title": cols[1], "location": cols[3],
                     "published_on": cols[6], "url": detail_url.removesuffix(".md"),
                     "_markdown_detail": detail_url})
    return rows


def _parse_personio_xml(text: str, board: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except Exception:
        return []
    rows = []
    for pos in root.findall(".//position"):
        pid = pos.findtext("id")
        name = pos.findtext("name")
        if not pid or not name:
            continue
        office = (pos.findtext("office") or "").strip()
        add_offices = [o.text.strip() for o in pos.findall("additionalOffices/office") if o.text and o.text.strip()]
        all_offices = [office] + [o for o in add_offices if o != office] if office else add_offices
        location = ", ".join(filter(None, all_offices))
        created = pos.findtext("createdAt")
        descs = [jd.findtext("value") or "" for jd in pos.findall("jobDescriptions/jobDescription")]
        desc = "\n\n".join(filter(None, descs))
        rows.append({
            "id": pid,
            "title": name,
            "location": location,
            "createdAt": created,
            "description": desc,
            "url": f"https://{quote(board, safe='')}.jobs.personio.de/job/{pid}",
        })
    return rows


def _parse_teamtailor_rss(text: str, board: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except Exception:
        return []
    rows = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        if not title or not link:
            continue
        guid = item.findtext("guid") or link.rstrip("/").rsplit("/", 1)[-1]
        pub_date = item.findtext("pubDate")
        city = (item.findtext("{*}city") or "").strip()
        country = (item.findtext("{*}country") or "").strip()
        remote = (item.findtext("remoteStatus") or "").strip().lower()
        loc = ", ".join(filter(None, [city, country]))
        if remote in ("fully", "temporary") and "remote" not in loc.lower():
            loc = f"{loc}, Remote".strip(", ")
        desc = item.findtext("description") or ""
        rows.append({
            "id": guid,
            "title": title,
            "url": link,
            "location": loc,
            "pubDate": pub_date,
            "description": desc,
            "isRemote": remote in ("fully", "temporary") or "remote" in loc.lower(),
        })
    return rows


def _date(value: str | int | None) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, int):
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = parsedate_to_datetime(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _salary(raw: dict, ats: str) -> tuple[float | None, float | None, str | None, str | None]:
    if ats == "ashby":
        components = (raw.get("compensation") or {}).get("summaryComponents") or []
        for comp in components:
            if comp.get("compensationType") == "Salary":
                interval = str(comp.get("interval") or "").lower()
                period = "year" if "year" in interval else "month" if "month" in interval else "hour" if "hour" in interval else None
                return comp.get("minValue"), comp.get("maxValue"), comp.get("currencyCode"), period
    if ats in {"lever", "lever_eu"}:
        sal = raw.get("salaryRange") or {}
        return sal.get("min"), sal.get("max"), sal.get("currency"), (sal.get("interval") or "year").lower()
    if ats == "smartrecruiters":
        sal = raw.get("compensation") or raw.get("salary") or {}
        return sal.get("min"), sal.get("max"), sal.get("currency"), (sal.get("period") or "year").lower()
    if ats == "greenhouse":
        ranges = raw.get("pay_input_ranges") or []
        if ranges:
            sal = ranges[0]
            minimum, maximum = sal.get("min_cents"), sal.get("max_cents")
            return (minimum / 100 if isinstance(minimum, (int, float)) else None,
                    maximum / 100 if isinstance(maximum, (int, float)) else None,
                    sal.get("currency_type"), "year")
    if ats == "breezy":
        sal = raw.get("salary") or {}
        if isinstance(sal, dict):
            return sal.get("min"), sal.get("max"), sal.get("currency"), (sal.get("interval") or "year").lower()
    if ats == "recruitee":
        sal = raw.get("salary") or {}
        if isinstance(sal, dict):
            return sal.get("min"), sal.get("max"), sal.get("currency"), (sal.get("period") or "year").lower()
    return None, None, None, None


def _location_tags(location: str, cfg: dict) -> tuple[list[str], list[str]]:
    patterns = cfg.get("location_patterns") or {}
    if re.search(patterns.get("global", r"\b(global|worldwide|anywhere)\b"), location, re.I):
        return ["global"], []
    regions = [name for name, pat in (patterns.get("regions") or {}).items() if re.search(pat, location, re.I)]
    countries = [code for code, pat in (patterns.get("countries") or {}).items() if re.search(pat, location, re.I)]
    for code in countries:
        region = (patterns.get("country_regions") or {}).get(code)
        if region and region not in regions:
            regions.append(region)
    return regions, countries


def _is_remote(raw: dict, location: str, cfg: dict) -> bool:
    workplace = str(raw.get("workplaceType") or raw.get("workplace_type") or "").lower()
    if workplace in {"onsite", "on_site", "hybrid"}:
        return False
    if raw.get("isRemote") is True or workplace == "remote" or raw.get("remote") is True:
        return True
    return bool(re.search((cfg.get("location_patterns") or {}).get("remote", r"\bremote\b"), location, re.I))


def _title_ok(title: str, cfg: dict) -> bool:
    filters = cfg.get("filters") or {}
    if any(re.search(p, title, re.I) for p in filters.get("title_exclude", [])):
        return False
    families = filters.get("role_families") or {}
    return not families or any(re.search(p, title, re.I) for patterns in families.values() for p in patterns)


def _base(ats: str, board: str) -> tuple[str, dict]:
    if ats in _more_ats.NAMES:
        return _more_ats.base(ats, board)
    slug = quote(board, safe="")
    if ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true", "pay_transparency": "true"}
    if ats in {"lever", "lever_eu"}:
        domain = "api.eu.lever.co" if ats == "lever_eu" else "api.lever.co"
        return f"https://{domain}/v0/postings/{slug}", {"mode": "json"}
    if ats == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"}
    if ats == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"}
    if ats == "smartrecruiters":
        return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", {"limit": 100, "offset": 0}
    if ats == "recruitee":
        return f"https://{slug}.recruitee.com/api/offers/", {}
    if ats == "breezy":
        return f"https://{slug}.breezy.hr/json", {}
    if ats == "bamboohr":
        return f"https://{slug}.bamboohr.com/careers/list", {}
    if ats == "pinpoint":
        return f"https://{slug}.pinpointhq.com/postings.json", {}
    if ats == "personio":
        return f"https://{slug}.jobs.personio.de/xml", {}
    if ats == "teamtailor":
        return f"https://{slug}.teamtailor.com/jobs.rss", {}
    raise ValueError(f"unsupported ATS: {ats}")


def _items(data: dict | list, ats: str) -> list[dict]:
    if isinstance(data, list):
        return data
    if ats == "workable":
        return data.get("results") or data.get("jobs") or []
    if ats == "smartrecruiters":
        return data.get("content") or []
    if ats == "recruitee":
        return data.get("offers") or []
    if ats == "bamboohr":
        return data.get("result") or []
    if ats == "pinpoint":
        return data.get("data") or []
    return data.get("jobs") or []


def _record(raw: dict, company: dict, cfg: dict, session: requests.Session) -> Job | None:
    ats, board = company["ats"].lower(), company["board"]
    title = raw.get("title") or raw.get("name") or raw.get("jobOpeningName") or ""
    if not _title_ok(title, cfg):
        return None
    if ats == "recruitee" and "(sample)" in title.lower():
        return None
    if ats == "pinpoint" and "youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw" in str(raw.get("description", "")):
        return None

    if ats in _more_ats.NAMES:
        raw = _more_ats.enrich(session, ats, board, raw)

    location_raw = raw.get("location") or ""
    if isinstance(location_raw, dict):
        location = location_raw.get("name") or location_raw.get("city") or ""
        country = location_raw.get("country") or ""
        if isinstance(country, dict):
            country = country.get("name") or ""
        if country and country.lower() not in location.lower():
            location = f"{location}, {country}".strip(", ")
    else:
        location = str(location_raw)

    if ats in {"lever", "lever_eu"}:
        location = location or (raw.get("categories") or {}).get("location") or ""
        all_locations = (raw.get("categories") or {}).get("allLocations") or []
        location = ", ".join(dict.fromkeys([location, *all_locations]).keys()).strip(", ")
    elif ats == "workable":
        location = raw.get("location", {}).get("location_str", "") if isinstance(raw.get("location"), dict) else location
        location = location or ", ".join(filter(None, (raw.get("city"), raw.get("country"))))
        location = location or ("Remote" if raw.get("telecommuting") else "")
        raw["isRemote"] = raw.get("telecommuting", raw.get("is_remote", raw.get("isRemote")))
    elif ats == "smartrecruiters":
        location = location or (raw.get("location") or {}).get("fullLocation", "")
        raw["isRemote"] = (raw.get("location") or {}).get("remote") is True
    elif ats == "recruitee":
        if not location:
            locs = raw.get("locations") or []
            if locs and isinstance(locs[0], dict):
                location = locs[0].get("name") or ", ".join(filter(None, [locs[0].get("city"), locs[0].get("country")]))
            else:
                location = ", ".join(filter(None, [raw.get("city"), raw.get("country")]))
        if raw.get("remote") or "remote" in location.lower():
            raw["isRemote"] = True
            if "remote" not in location.lower():
                location = f"{location}, Remote".strip(", ")
    elif ats == "breezy":
        loc = raw.get("location") or {}
        if isinstance(loc, dict) and loc.get("is_remote"):
            raw["isRemote"] = True
            if "remote" not in location.lower():
                location = f"{location}, Remote".strip(", ")
    elif ats == "bamboohr":
        loc = raw.get("location") or {}
        if isinstance(loc, dict):
            location = ", ".join(filter(None, [loc.get("city"), loc.get("state")]))
        if raw.get("isRemote") or "remote" in location.lower():
            raw["isRemote"] = True
            if "remote" not in location.lower():
                location = f"{location}, Remote".strip(", ")
    elif ats in {"personio", "teamtailor"}:
        if "remote" in location.lower():
            raw["isRemote"] = True

    if not _is_remote(raw, location, cfg):
        return None

    jid = raw.get("id") or raw.get("shortcode")
    if ats == "workable":
        jid = raw.get("shortcode") or jid
    if ats == "ashby":
        jid = raw.get("jobUrl", "").rstrip("/").rsplit("/", 1)[-1] or jid
    if ats == "smartrecruiters":
        jid = raw.get("id") or jid
    if ats == "breezy" and not jid:
        jid = raw.get("_id")
    if not jid and raw.get("url"):
        jid = raw["url"].rstrip("/").rsplit("/", 1)[-1]
    if not jid:
        return None
    jid = str(jid)

    desc = raw.get("descriptionPlain") or raw.get("descriptionHtml") or raw.get("description") or raw.get("content") or ""
    if isinstance(desc, dict):
        desc = "\n".join(str(x) for x in desc.values())
    if ats in {"lever", "lever_eu"}:
        desc = "\n".join(filter(None, [raw.get("descriptionPlain"), raw.get("additionalPlain"),
                                       *[x.get("text", "") + "\n" + plain(x.get("content", "")) for x in raw.get("lists", [])]]))
    elif ats == "recruitee":
        desc = "\n\n".join(filter(None, [raw.get("description"), raw.get("requirements")]))
    elif ats == "smartrecruiters" and not desc:
        detail = get(session, f"https://api.smartrecruiters.com/v1/companies/{quote(board)}/postings/{jid}")
        sections = detail.get("jobAd", {}).get("sections", {})
        desc = "\n".join(str(s.get("text", "")) for s in sections.values() if isinstance(s, dict))
        raw = {**detail, **raw}
    elif ats == "workable" and not desc:
        if raw.get("_markdown_detail"):
            _pace(raw["_markdown_detail"])
            response = session.get(raw["_markdown_detail"], timeout=10)
            response.raise_for_status()
            desc = response.text
        else:
            detail = get(session, f"https://apply.workable.com/api/v1/accounts/{quote(board)}/jobs/{jid}")
            desc = detail.get("description") or ""
            raw = {**detail, **raw}
    elif ats == "bamboohr":
        try:
            detail = get(session, f"https://{quote(board, safe='')}.bamboohr.com/careers/{jid}/detail")
            opening = (detail.get("result") or {}).get("jobOpening") or {}
            desc = opening.get("description") or ""
            raw = {**raw, **opening}
        except Exception:
            pass

    desc = plain(desc) if "<" in str(desc) else str(desc).strip()
    url = raw.get("absolute_url") or raw.get("hostedUrl") or raw.get("jobUrl") or raw.get("applyUrl") or raw.get("url") or raw.get("shortlink") or raw.get("applicationUrl") or raw.get("careers_url")
    if not url:
        if ats == "smartrecruiters":
            url = f"https://jobs.smartrecruiters.com/{board}/{jid}"
        elif ats == "workable":
            url = f"https://apply.workable.com/{board}/j/{jid}/"
        elif ats == "recruitee":
            url = f"https://{board}.recruitee.com/o/{jid}"
        elif ats == "bamboohr":
            url = f"https://{board}.bamboohr.com/careers/{jid}"
        elif ats == "breezy":
            url = f"https://{board}.breezy.hr/p/{jid}"
        elif ats == "personio":
            url = f"https://{board}.jobs.personio.de/job/{jid}"
        else:
            return None

    regions, countries = _location_tags(location, cfg)
    minimum, maximum, currency, period = _salary(raw, ats)
    employment = raw.get("employmentType") or raw.get("commitment") or raw.get("employment_type_code")
    if isinstance(employment, dict):
        employment = employment.get("title")
    employment = str(employment or "").lower().replace("fulltime", "full_time") or None

    posted_date = (raw.get("publishedAt") or raw.get("published_on") or raw.get("created_at") or
                   raw.get("createdAt") or raw.get("releasedDate") or raw.get("releasedAt") or
                   raw.get("updated_at") or raw.get("published_at") or raw.get("published_date") or
                   raw.get("datePosted") or raw.get("pubDate"))

    job = Job(
        id=f"{'lever' if ats == 'lever_eu' else ats}:{board}:{jid}", source="ats",
        url=normalize_url(url), title=title, company=company["company"],
        company_key=normalize_company(company["company"]), description=desc,
        location=location, regions=regions, countries=countries,
        posted_at=_date(posted_date),
        salary_min=minimum, salary_max=maximum, salary_currency=currency, salary_period=period,
        employment_type=employment, last_seen_at=datetime.now(timezone.utc), watchlist=True,
    )
    return job


def fetch_board(company: dict, cfg: dict) -> list[Job]:
    """One company is an isolation boundary: failures only hide that company's jobs."""
    ats, board = company["ats"].lower(), company["board"]
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    if ats == "workable":
        session.headers.update({"Accept-Language": "en-US,en;q=0.9", "Origin": "https://apply.workable.com"})
    elif ats == "bamboohr":
        session.headers.update({"Accept": "application/json"})
    try:
        url, params = _base(ats, board)
        used_fallback = False
        if ats in _more_ats.NAMES:
            raws = _more_ats.rows(session, ats, board, cfg.get("max_board_jobs", 200))
        elif ats == "personio":
            try:
                text = get_text(session, url)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    alt_url = f"https://{quote(board, safe='')}.jobs.personio.com/xml"
                    text = get_text(session, alt_url)
                else:
                    raise
            raws = _parse_personio_xml(text, board)
        elif ats == "teamtailor":
            text = get_text(session, url)
            raws = _parse_teamtailor_rss(text, board)
        else:
            try:
                data = get(session, url, params=params)
                raws = _items(data, ats)
            except requests.RequestException:
                if ats != "workable":
                    raise
                data = {}
                raws = workable_markdown(session, board)
                used_fallback = True
            if ats == "workable" and not raws and not used_fallback:
                raws = workable_markdown(session, board)
            if ats == "smartrecruiters" and isinstance(data, dict):
                total = min(data.get("totalFound", len(raws)), cfg.get("max_board_jobs", 500))
                for offset in range(100, total, 100):
                    time.sleep(cfg.get("request_delay_s", 0.2))
                    params["offset"] = offset
                    raws.extend(_items(get(session, url, params=params), ats))
        out = []
        for raw in raws:
            try:
                if job := _record(raw, company, cfg, session):
                    out.append(job)
            except Exception as exc:
                log.warning("ATS %s/%s posting skipped: %s", ats, board, exc)
        return out
    except Exception as exc:
        log.warning("ATS %s/%s failed: %s", ats, board, exc)
        return []
    finally:
        session.close()


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    """Yield all live remote jobs, including older posts, from watched boards."""
    path = Path(cfg.get("watchlist_file", "private/config/watchlist.yml"))
    if not path.exists():
        log.warning("ATS watchlist missing: %s", path)
        return
    data = yaml.safe_load(path.read_text()) or {}
    companies = data.get("companies", []) if isinstance(data, dict) else data
    denied = {str(x).lower() for x in cfg.get("hq_deny_countries", ["in", "bd"])}
    companies = [c for c in companies if c.get("ats") and c.get("board") and str(c.get("hq_country") or "").lower() not in denied]
    with ThreadPoolExecutor(max_workers=min(8, max(1, cfg.get("workers", 5)))) as pool:
        futures = {pool.submit(fetch_board, c, cfg): c for c in companies}
        for future in as_completed(futures):
            try:
                yield from future.result()
            except Exception as exc:
                log.warning("ATS company %s failed: %s", futures[future].get("company"), exc)
