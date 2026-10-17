"""Read live employer boards so older worldwide openings remain discoverable."""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import yaml

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job

log = logging.getLogger(__name__)
USER_AGENT = "job-hunt-scanner (personal use)"
_RATE_LOCK = threading.Lock()
_NEXT_REQUEST: dict[str, float] = {}


def _pace(url: str) -> None:
    host = urlparse(url).hostname or ""
    interval = 1.0 if host == "apply.workable.com" else 0.1
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


def _date(value: str | int | None) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, int):
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
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
            return sal.get("min_cents", 0) / 100 or None, sal.get("max_cents", 0) / 100 or None, sal.get("currency_type"), "year"
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
    if raw.get("isRemote") is True or workplace == "remote":
        return True
    return bool(re.search((cfg.get("location_patterns") or {}).get("remote", r"\bremote\b"), location, re.I))


def _title_ok(title: str, cfg: dict) -> bool:
    filters = cfg.get("filters") or {}
    if any(re.search(p, title, re.I) for p in filters.get("title_exclude", [])):
        return False
    families = filters.get("role_families") or {}
    return not families or any(re.search(p, title, re.I) for patterns in families.values() for p in patterns)


def _base(ats: str, board: str) -> tuple[str, dict]:
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
    raise ValueError(f"unsupported ATS: {ats}")


def _items(data: dict | list, ats: str) -> list[dict]:
    if isinstance(data, list):
        return data
    if ats == "workable":
        return data.get("results") or data.get("jobs") or []
    if ats == "smartrecruiters":
        return data.get("content") or []
    return data.get("jobs") or []


def _record(raw: dict, company: dict, cfg: dict, session: requests.Session) -> Job | None:
    ats, board = company["ats"].lower(), company["board"]
    title = raw.get("title") or raw.get("name") or ""
    if not _title_ok(title, cfg):
        return None
    location_raw = raw.get("location") or ""
    if isinstance(location_raw, dict):
        location = location_raw.get("name") or location_raw.get("city") or ""
        country = location_raw.get("country") or ""
        if country and country.lower() not in location.lower():
            location = f"{location}, {country}".strip(", ")
    else:
        location = str(location_raw)
    if ats in {"lever", "lever_eu"}:
        location = location or (raw.get("categories") or {}).get("location") or ""
        all_locations = (raw.get("categories") or {}).get("allLocations") or []
        location = ", ".join(dict.fromkeys([location, *all_locations]).keys()).strip(", ")
    if ats == "workable":
        location = raw.get("location", {}).get("location_str", "") if isinstance(raw.get("location"), dict) else location
        location = location or ", ".join(filter(None, (raw.get("city"), raw.get("country"))))
        location = location or ("Remote" if raw.get("telecommuting") else "")
        raw["isRemote"] = raw.get("telecommuting", raw.get("is_remote", raw.get("isRemote")))
    if ats == "smartrecruiters":
        location = location or (raw.get("location") or {}).get("fullLocation", "")
        raw["isRemote"] = (raw.get("location") or {}).get("remote") is True
    if not _is_remote(raw, location, cfg):
        return None
    jid = raw.get("id") or raw.get("shortcode")
    if not jid:
        return None
    if ats == "workable":
        jid = raw.get("shortcode") or jid
    if ats == "ashby":
        jid = raw.get("jobUrl", "").rstrip("/").rsplit("/", 1)[-1] or jid
    if ats == "smartrecruiters":
        jid = raw.get("id") or jid
    desc = raw.get("descriptionPlain") or raw.get("descriptionHtml") or raw.get("description") or raw.get("content") or ""
    if isinstance(desc, dict):
        desc = "\n".join(str(x) for x in desc.values())
    if ats in {"lever", "lever_eu"}:
        desc = "\n".join(filter(None, [raw.get("descriptionPlain"), raw.get("additionalPlain"),
                                           *[x.get("text", "") + "\n" + plain(x.get("content", "")) for x in raw.get("lists", [])]]))
    if ats == "smartrecruiters" and not desc:
        detail = get(session, f"https://api.smartrecruiters.com/v1/companies/{quote(board)}/postings/{jid}")
        sections = detail.get("jobAd", {}).get("sections", {})
        desc = "\n".join(str(s.get("text", "")) for s in sections.values() if isinstance(s, dict))
        raw = {**detail, **raw}
    if ats == "workable" and not desc:
        detail = get(session, f"https://apply.workable.com/api/v1/accounts/{quote(board)}/jobs/{jid}")
        desc = detail.get("description") or ""
        raw = {**detail, **raw}
    desc = plain(desc) if "<" in str(desc) else str(desc).strip()
    url = raw.get("absolute_url") or raw.get("hostedUrl") or raw.get("jobUrl") or raw.get("applyUrl") or raw.get("url") or raw.get("shortlink") or raw.get("applicationUrl")
    if not url:
        if ats == "smartrecruiters":
            url = f"https://jobs.smartrecruiters.com/{board}/{jid}"
        elif ats == "workable":
            url = f"https://apply.workable.com/{board}/j/{jid}/"
        else:
            return None
    regions, countries = _location_tags(location, cfg)
    minimum, maximum, currency, period = _salary(raw, ats)
    employment = raw.get("employmentType") or raw.get("commitment")
    if isinstance(employment, dict):
        employment = employment.get("title")
    employment = str(employment or "").lower().replace("fulltime", "full_time") or None
    job = Job(
        id=f"{'lever' if ats == 'lever_eu' else ats}:{board}:{jid}", source="ats",
        url=normalize_url(url), title=title, company=company["company"],
        company_key=normalize_company(company["company"]), description=desc,
        location=location, regions=regions, countries=countries,
        posted_at=_date(raw.get("publishedAt") or raw.get("published_on") or raw.get("created_at") or raw.get("createdAt") or raw.get("releasedDate") or raw.get("releasedAt") or raw.get("updated_at")),
        salary_min=minimum, salary_max=maximum, salary_currency=currency, salary_period=period,
        employment_type=employment, last_seen_at=datetime.now(timezone.utc), watchlist=True,
    )
    return job


def fetch_board(company: dict, cfg: dict) -> list[Job]:
    """One company is an isolation boundary: failures only hide that company's jobs."""
    ats, board = company["ats"].lower(), company["board"]
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    try:
        url, params = _base(ats, board)
        data = get(session, url, params=params)
        raws = _items(data, ats)
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


def fetch(cfg: dict, days: int):
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
