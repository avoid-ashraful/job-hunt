"""Resolve remote-friendly companies to their public ATS boards and grow that list from evidence."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
import yaml

from scanner.board_owner import AGGREGATORS, DEMO, _domain, owns

OWNER_CHECK = 2  # bump when the ownership check changes, so earlier verdicts are redone
from scanner.dedup import normalize_company, normalize_url
from scanner.sources import _more_ats
from scanner.sources.ats import (
    USER_AGENT,
    _base,
    _parse_personio_xml,
    _parse_teamtailor_rss,
    get,
    get_text,
    workable_markdown,
)

log = logging.getLogger(__name__)

ATS_LINKS = {
    "workday": re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I),
    "rippling": re.compile(r"ats\.rippling\.com/([a-z0-9_-]+)/jobs", re.I),
    "jazzhr": re.compile(r"([a-z0-9-]+)\.applytojob\.com/apply", re.I),
    "homerun": re.compile(r"([a-z0-9-]+)\.homerun\.co", re.I),
    "dover": re.compile(r"app\.dover\.com/(?:jobs/([a-z0-9_-]+)|[^/\s\"<>]+/careers/([0-9a-f-]{36}))", re.I),
    "comeet": re.compile(r"(?:www\.)?comeet\.com/jobs/([a-z0-9-]+)/([A-Z0-9.]+)", re.I),
    "factorial": re.compile(r"([a-z0-9-]+)\.factorial(?:hr)?\.com", re.I),
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#\"'<>&\s]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([^/?#\"'<>&\s]+)", re.I),
    "lever_eu": re.compile(r"jobs\.eu\.lever\.co/([^/?#\"'<>&\s]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#\"'<>&\s]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([^/?#\"'<>&\s]+)|([a-z0-9-]+)\.workable\.com", re.I),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/([^/?#\"'<>&\s]+)", re.I),
    "recruitee": re.compile(r"([a-z0-9-]+)\.recruitee\.com", re.I),
    "breezy": re.compile(r"([a-z0-9-]+)\.breezy\.hr", re.I),
    "personio": re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)", re.I),
    "bamboohr": re.compile(r"([a-z0-9-]+)\.bamboohr\.com", re.I),
    "pinpoint": re.compile(r"([a-z0-9-]+)\.pinpointhq\.com", re.I),
    "teamtailor": re.compile(r"([a-z0-9-]+)\.teamtailor\.com", re.I),
}


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    return raw.get("companies", []) if isinstance(raw, dict) else raw


def _write(path: Path, companies: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(companies, key=lambda c: normalize_company(c["company"]))
    path.write_text(yaml.safe_dump({"companies": ordered}, sort_keys=False, allow_unicode=True))


def _company_file(path: Path) -> dict | None:
    text = path.read_text(errors="ignore")
    if not text.startswith("---"):
        return None
    try:
        front = yaml.safe_load(text.split("---", 2)[1]) or {}
    except yaml.YAMLError:
        return None
    if str(front.get("region", "")).lower() != "worldwide":
        return None
    raw_name = front.get("title") or front.get("slug") or path.stem
    clean_name = str(raw_name).rstrip("> \t\r\n").strip()
    return {
        "company": clean_name,
        "website": front.get("website") or "",
        "careers_url": front.get("careers_url") or "",
        "hq_country": front.get("hq_country") or "",
        "company_type": front.get("company_type") or "",
        "hires": "worldwide",
        "source": "remoteintech",
    }


def _slugs(company: dict) -> list[str]:
    name = normalize_company(company["company"])
    domain = urlparse(company.get("website") or "").hostname or ""
    stem = domain.removeprefix("www.").split(".")[0].lower()
    alphanumeric = re.sub(r"[^a-z0-9]", "", company["company"].lower())
    candidates = [
        name,
        name.replace("-", ""),
        stem,
        alphanumeric,
        f"{name}hq",
        f"{stem}hq",
    ]
    return list(dict.fromkeys(x for x in candidates if x and len(x) >= 2))


def _links(html: str) -> list[tuple[str, str]]:
    found = []
    # 1. Direct ATS link regexes
    for ats, pattern in ATS_LINKS.items():
        for m in pattern.finditer(html):
            val = (m.group(1) or (m.group(2) if m.lastindex and m.lastindex >= 2 else None) or "").strip("/ \t'\"")
            if ats in {"workday", "comeet"}:
                val = "|".join(m.groups())
            if val and val.lower() not in {"jobs", "careers", "embed", "api", "widget", "assets", "www", "app", "view", "search"}:
                found.append((ats, val))

    # 2. Embedded boards and widget tokens
    for m in re.finditer(r"(?:boards\.greenhouse\.io/embed/job_board(?:/[a-z]+)?\?(?:[^\"'>\s]*&)?for=|grnhse_app\s*=\s*['\"]|boardToken:\s*['\"])([^/?#\"'<>&\\s]+)", html, re.I):
        val = m.group(1).strip("/ \t'\"")
        if val and val.lower() not in {"jobs", "careers"}:
            found.append(("greenhouse", val))

    for m in re.finditer(r"(?:data-lever-account|lever-jobs-container)\s*=\s*['\"]([^/?#\"'<>&\\s]+)", html, re.I):
        val = m.group(1).strip("/ \t'\"")
        if val:
            found.append(("lever", val))

    for m in re.finditer(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)(?:/embed)?", html, re.I):
        val = m.group(1).strip("/ \t'\"")
        if val and val.lower() not in {"api"}:
            found.append(("ashby", val))

    for m in re.finditer(r"(?:account:\s*['\"]|data-workable-account\s*=\s*['\"]|whr_embed_hook.*?account\s*:\s*['\"])([^/?#\"'<>&\\s]+)", html, re.I):
        val = m.group(1).strip("/ \t'\"")
        if val:
            found.append(("workable", val))

    return list(dict.fromkeys(found))


def _find_careers_link(html: str, base_url: str) -> str | None:
    """Find 1-hop career/jobs link in homepage HTML."""
    base_host = (urlparse(base_url).hostname or "").removeprefix("www.").lower()
    for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href, text = m.group(1).strip(), m.group(2).strip()
        clean_text = re.sub(r"<[^>]+>", "", text).strip().lower()
        href_lower = href.lower()
        if any(kw in href_lower for kw in ("/careers", "/jobs", "/about/careers", "/join-us", "/open-roles", "/work-with-us")) or \
           any(kw in clean_text for kw in ("careers", "jobs", "open positions", "work with us", "join our team", "we are hiring")):
            full_url = urljoin(base_url, href)
            thost = (urlparse(full_url).hostname or "").removeprefix("www.").lower()
            if thost == base_host or any(ats_host in thost for ats_host in (
                "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                "bamboohr.com", "breezy.hr", "recruitee.com", "personio", "pinpointhq.com", "teamtailor.com",
                "myworkdayjobs.com", "rippling.com", "applytojob.com", "homerun.co", "dover.com", "comeet.com", "factorialhr.com", "factorial.com"
            )):
                return full_url
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def company_match(cand_name: str, target_name: str, target_website: str = "", cand_website: str = "",
                  aliases: list[str] | None = None) -> bool:
    """Verify company candidate matches target to reject slug collisions."""
    if any(company_match(cand_name, alias, target_website, cand_website) for alias in aliases or []):
        return True
    if not cand_name and not cand_website:
        return True
    nc = _norm(cand_name)
    nt = _norm(target_name)
    if nc and nt:
        if nc == nt:
            return True
        if len(nt) >= 4 and (nt in nc or nc in nt):
            return True
        tc = set(re.findall(r"[a-z0-9]+", cand_name.lower()))
        tt = set(re.findall(r"[a-z0-9]+", target_name.lower()))
        corp = {"inc", "ltd", "llc", "corp", "corporation", "gmbh", "co", "group", "technologies", "technology", "labs", "software", "hq"}
        tc -= corp
        tt -= corp
        if tt and tc and (tt.issubset(tc) or tc.issubset(tt)):
            return True
    if target_website and cand_website:
        dt = (urlparse(target_website).hostname or "").removeprefix("www.").lower()
        dc = (urlparse(cand_website).hostname or "").removeprefix("www.").lower()
        if dt and dc and (dt == dc or dt.split(".")[0] == dc.split(".")[0]):
            return True
    return False


def _valid_board(session: requests.Session, ats: str, board: str, *args,
                 company_name: str = "", website: str = "", aliases: list[str] | None = None, **kwargs) -> bool | None:
    try:
        if ats == "workable":
            session.headers["Origin"] = "https://apply.workable.com"
        elif ats == "bamboohr":
            session.headers["Accept"] = "application/json"

        if ats in _more_ats.NAMES:
            return bool(_more_ats.rows(session, ats, board, limit=1))
        url, params = _base(ats, board)
        if ats in {"lever", "lever_eu", "smartrecruiters"}:
            params = {**params, "limit": 1}
        if ats == "personio":
            try:
                text = get_text(session, url, retries=2, timeout=7)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    alt = f"https://{quote(board, safe='')}.jobs.personio.com/xml"
                    text = get_text(session, alt, retries=2, timeout=7)
                elif exc.response is not None and exc.response.status_code == 429:
                    return None
                else:
                    return False
            positions = _parse_personio_xml(text, board)
            return bool(positions)
        elif ats == "teamtailor":
            try:
                text = get_text(session, url, retries=2, timeout=7)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    return None
                return False
            items = _parse_teamtailor_rss(text, board)
            return bool(items)

        try:
            data = get(session, url, params=params, retries=2, timeout=7)
        except requests.RequestException as exc:
            if ats == "workable":
                try:
                    if workable_markdown(session, board):
                        return True
                except requests.RequestException:
                    if exc.response is None or exc.response.status_code != 429:
                        raise
            raise

        if ats in {"lever", "lever_eu"}:
            return isinstance(data, list) and bool(data)
        if ats == "recruitee":
            if isinstance(data, dict) and "offers" in data:
                offers = data.get("offers") or []
                if offers and offers[0].get("company_name"):
                    if not company_match(offers[0]["company_name"], company_name, website, aliases=aliases):
                        return False
                return bool(offers)
            return False
        if ats == "breezy":
            if isinstance(data, list):
                if data and isinstance(data[0], dict):
                    cname = (data[0].get("company") or {}).get("name")
                    if cname and not company_match(cname, company_name, website, aliases=aliases):
                        return False
                return bool(data)
            return False
        if ats == "bamboohr":
            return isinstance(data, dict) and bool(data.get("result"))
        if ats == "pinpoint":
            return isinstance(data, dict) and bool(data.get("data")) and not DEMO.search(json.dumps(data))
        if not isinstance(data, dict):
            return False
        if ats == "greenhouse":
            jobs = data.get("jobs") or []
            if company_name and jobs:
                try:
                    info = get(session, f"https://boards-api.greenhouse.io/v1/boards/{quote(board, safe='')}", retries=2, timeout=5)
                    if isinstance(info, dict) and info.get("name"):
                        if not company_match(info["name"], company_name, website, aliases=aliases):
                            return False
                except Exception:
                    pass
            return bool(jobs)
        if ats == "workable":
            cand_name = data.get("name") or ""
            cand_web = data.get("website") or ""
            if cand_name or cand_web:
                if not company_match(cand_name, company_name, website, cand_web, aliases=aliases):
                    return False
            return bool(data.get("results") or data.get("jobs") or data.get("name"))
        key = "content" if ats == "smartrecruiters" else "jobs"
        return isinstance(data.get(key), list) and bool(data[key])
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            return None
        return False
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return False


# Slug-guess order. No Workable: its apply.workable.com quota (~12h lockout) is shared with the daily scan.
ATS_ORDER = ("greenhouse", "ashby", "lever", "lever_eu", "smartrecruiters",
             "recruitee", "breezy", "bamboohr", "personio", "teamtailor", "pinpoint",
             "rippling", "jazzhr", "homerun", "factorial", "dover")  # Workday/Comeet: careers links only


def _check_board(session: requests.Session, ats: str, board: str, cname: str, cweb: str,
                 aliases: list[str] | None = None) -> bool | None:
    try:
        return _valid_board(session, ats, board, company_name=cname, website=cweb, aliases=aliases)
    except TypeError:
        return _valid_board(session, ats, board)


def resolve_company(company: dict, session: requests.Session | None = None,
                    hints: list[tuple[str, str]] | None = None) -> dict:
    """Prefer actual careers links and 1-hop crawl, then bounded slug guesses verified by API."""
    own = session is None
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept-Language"] = "en-US,en;q=0.9"
    try:
        links: list[tuple[str, str]] = []
        failures: list[str] = []
        cname = company.get("company", "")
        cweb = company.get("website", "")

        unresolved: set[tuple[str, str]] = set()
        trusted: set[tuple[str, str]] = set()
        seen: set[str] = set()
        aliases = company.get("aliases") or []
        owner_website = "" if _domain(cweb).endswith(AGGREGATORS) else cweb

        def probe(ats, board, reason, trust=False):
            url = normalize_url(_base(ats, board)[0])
            if url in seen:
                return None
            seen.add(url)
            valid = _check_board(session, ats, board, cname, cweb, aliases)
            if valid is None:
                unresolved.add((ats, board))
                if trust:
                    trusted.add((ats, board))
            if not valid:
                return None
            if not trust:
                try:
                    owner = owns(session, ats, board, cname, owner_website, aliases=aliases, raise_on_limit=True)
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 429:
                        unresolved.add((ats, board))
                    return None
                if not owner:
                    return None
            result = {"ats": ats, "board": board, "reason": reason}
            if not trust:
                result["owner_check"] = OWNER_CHECK
            return result

        def miss():
            reason = "rate limited while probing a board" if unresolved else "no supported ATS board found"
            if failures:
                reason += f" ({', '.join(dict.fromkeys(failures))})"
            result = {"reason": reason}
            if unresolved:
                result["retry_probes"] = [list(p) for p in sorted(unresolved)]
                result["retry_trusted"] = [list(p) for p in sorted(trusted)]
            return result

        if company.get("ats") and company.get("board"):
            return probe(company["ats"], company["board"], "seed override", trust=True) or miss()

        if "retry_probes" in company:
            retry_trusted = {tuple(p) for p in company.get("retry_trusted", [])}
            for ats, board in company["retry_probes"]:
                trust = (ats, board) in retry_trusted
                result = probe(ats, board, "careers link" if trust else "verified slug", trust)
                if result:
                    return result
            return miss()

        for ats, board in hints or []:
            result = probe(ats, board, "database hint", trust=True)
            if result:
                return result

        # 1. Direct careers_url and website probe (with 1-hop crawl)
        visited_urls = set()
        careers_url = company.get("careers_url")
        website = company.get("website")

        for url in dict.fromkeys(filter(None, (careers_url, website))):
            if url in visited_urls:
                continue
            visited_urls.add(url)
            try:
                response = session.get(url, timeout=8)
                response.raise_for_status()
                html = response.url + "\n" + response.text
                page_links = _links(html)
                links.extend(page_links)

                # If no ATS link found on homepage, do 1-hop crawl to careers page
                if not page_links and (url == website or "/careers" not in url.lower()):
                    careers_hop = _find_careers_link(response.text, response.url)
                    if careers_hop and careers_hop not in visited_urls:
                        visited_urls.add(careers_hop)
                        try:
                            hop_resp = session.get(careers_hop, timeout=8)
                            hop_resp.raise_for_status()
                            hop_links = _links(hop_resp.url + "\n" + hop_resp.text)
                            links.extend(hop_links)
                        except requests.RequestException as exc:
                            failures.append(f"1-hop page: {type(exc).__name__}")
            except requests.RequestException as exc:
                failures.append(f"careers page: {type(exc).__name__}")

        # 2. Check candidate boards found in HTML (careers link / embed). A link on a page that lists many
        #    companies (an HN thread, an aggregator) is just a guess and must pass the ownership check.
        shared_page = any(_domain(u).endswith(AGGREGATORS) for u in filter(None, (careers_url, website)))
        for ats, board in dict.fromkeys(links):
            result = probe(ats, board, "verified slug" if shared_page else "careers link", not shared_page)
            if result:
                return result

        slugs = _slugs(company)
        for ats in company.get("_ats_order", ATS_ORDER):
            for board in slugs:
                result = probe(ats, board, "verified slug")
                if result:
                    return result
        return miss()
    finally:
        if own:
            session.close()


def _hq(company: dict, lookup=None) -> str:
    if company.get("hq_country"):
        return str(company["hq_country"]).lower()
    if lookup:
        try:
            return str(lookup(company["company"]) or "").lower()
        except Exception as exc:
            log.warning("HQ lookup failed for %s: %s", company["company"], exc)
    return ""


def _database_hints(path: Path | None) -> dict[str, list[tuple[str, str]]]:
    if not path or not path.exists():
        return {}
    hints: dict[str, list[tuple[str, str]]] = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT company_key, id FROM jobs")
        for key, job_id in rows:
            if parsed := _ats_from_id(job_id):
                boards = hints.setdefault(key, [])
                if parsed not in boards:
                    boards.append(parsed)
    return hints


def _is_excluded(seed: dict, filters_cfg: dict) -> bool:
    cname = seed.get("company", "")
    exclude_cfg = filters_cfg.get("exclude") or {}
    kw_patterns = exclude_cfg.get("company_name_keywords") or []
    if any(re.search(pat, cname, re.I) for pat in kw_patterns):
        return True
    ctypes = {str(x).lower() for x in (exclude_cfg.get("company_types") or [])}
    ctype = str(seed.get("company_type") or "").lower()
    if ctype and ctype in ctypes:
        return True
    return False


def _unverified_guess(cached: Path) -> dict | None:
    """The cached result of a slug guess made before boards were checked for ownership."""
    if not cached.exists():
        return None
    try:
        result = json.loads(cached.read_text())
    except ValueError:
        return None
    if result.get("ats") and result.get("reason") == "verified slug" and result.get("owner_check") != OWNER_CHECK:
        return result
    return None


def verify_owners(by_key: dict[str, dict], cache_dir: Path, workers: int = 8,
                  known: set[str] | None = None) -> dict[str, int]:
    """Drop watched boards that a slug guess attached to the wrong company (or a demo board).

    A board whose page can't be read right now stays only if it was already watched (`known`)."""
    todo = [(key, entry, cache_dir / f"{key}.json") for key, entry in by_key.items()
            if entry.get("ats") and _unverified_guess(cache_dir / f"{key}.json")]
    stats = {"checked": len(todo), "kept": 0, "dropped": 0, "unknown": 0}
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    def check(item):
        key, entry, _ = item
        return owns(session, entry["ats"], entry["board"], entry["company"], entry.get("website", ""),
                    **({"aliases": entry["aliases"]} if entry.get("aliases") else {}))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (key, entry, cached), owner in zip(todo, pool.map(check, todo)):
            if owner is None:
                stats["unknown"] += 1
                if known is not None and key not in known:
                    by_key.pop(key)
                continue
            result = json.loads(cached.read_text())
            if owner:
                result["owner_check"] = OWNER_CHECK
                stats["kept"] += 1
            else:
                log.info("watchlist: %s/%s isn't %s's board, dropped", entry["ats"], entry["board"], entry["company"])
                result = {"reason": "guessed board belongs to another company", "status": "miss",
                          "rejected": f"{entry['ats']}/{entry['board']}", "resolver_version": 2}
                by_key.pop(key)
                stats["dropped"] += 1
            cached.write_text(json.dumps(result))
    return stats


def _website_failure(reason: str) -> bool:
    return any(f"{page}: {error}" in reason
               for page in ("careers page", "1-hop page")
               for error in ("ConnectionError", "SSLError", "ConnectTimeout", "ReadTimeout", "Timeout"))


def _seed_fields(seed: dict) -> dict:
    return {k: v for k, v in seed.items() if not k.startswith("_") and not k.startswith("retry_")}


def build(vendor_dir: Path, watchlist_file: Path, cache_dir: Path, *, workers: int = 8,
          deny_countries: tuple[str, ...] = ("in", "bd"), hq_lookup=None,
          db_path: Path | None = None, seeds_file: Path | None = None,
          cfg: dict | None = None, max_attempts: int = 3, force_retry: bool = False,
          max_seconds: float = 1200, save_every: int = 20) -> dict:
    """Seed from remoteintech, tracked seeds, and DB evidence; cache results and retry transient failures."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    current = _read(watchlist_file)
    by_key = {normalize_company(c["company"]): c for c in current}
    files = sorted(vendor_dir.glob("*.md")) if vendor_dir.exists() else []
    seeds = [c for path in files if (c := _company_file(path))]

    # Load tracked seeds_file if provided
    if seeds_file and seeds_file.exists():
        raw_seeds = yaml.safe_load(seeds_file.read_text()) or {}
        seed_list = raw_seeds.get("companies", []) if isinstance(raw_seeds, dict) else raw_seeds
        existing_keys = {normalize_company(s["company"]) for s in seeds}
        for s in seed_list:
            if s.get("company"):
                clean_name = str(s["company"]).rstrip("> \t\r\n").strip()
                matching = next((seed for seed in seeds if normalize_company(seed["company"]) == normalize_company(clean_name)), None)
                if matching is not None:
                    matching.update({k: s[k] for k in ("ats", "board", "aliases", "website") if k in s})
                if normalize_company(clean_name) not in existing_keys:
                    seeds.append({
                        "company": clean_name,
                        "website": s.get("website", ""),
                        "careers_url": s.get("careers_url", ""),
                        "hq_country": s.get("hq_country", ""),
                        "hires": "worldwide",
                        "source": "seeds_yml",
                        "why": s.get("why", ""),
                        **{k: s[k] for k in ("ats", "board", "aliases") if k in s},
                    })
                    existing_keys.add(normalize_company(clean_name))

    # Load worldwide company evidence from SQLite DB
    if db_path and db_path.exists():
        existing_keys = {normalize_company(s["company"]) for s in seeds}
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "decisions" in tables and "jobs" in tables:
                    cur = db.execute(
                    "SELECT DISTINCT j.company_key, j.id, j.payload FROM jobs j "
                    "LEFT JOIN decisions d ON j.id = d.job_id "
                    "WHERE d.outcome IN ('eligible', 'apply') "
                    "OR j.id LIKE 'hn:%' OR j.id LIKE 'himalayas:%' OR j.id LIKE 'workable:%' "
                    "OR j.id LIKE 'freehire:%'"
                )
                for ckey, jid, payload_str in cur:
                    if ckey in existing_keys or not ckey:
                        continue
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        continue
                    regions = payload.get("regions") or []
                    is_global = "global" in regions or any(
                        jid.startswith(prefix) for prefix in (
                            "recruitee:", "breezy:", "personio:", "bamboohr:", "pinpoint:", "teamtailor:",
                            "workday:", "rippling:", "jazzhr:", "homerun:", "dover:", "comeet:", "factorial:"
                        )
                    )
                    if is_global:
                        comp_name = payload.get("company") or ckey
                        clean_name = str(comp_name).rstrip("> \t\r\n").strip()
                        seeds.append({
                            "company": clean_name,
                            "website": payload.get("url", ""),
                            "careers_url": "",
                            "hq_country": payload.get("country", ""),
                            "hires": "worldwide",
                            "source": "sqlite_db",
                        })
                        existing_keys.add(ckey)
        except Exception as exc:
            log.warning("Could not read worldwide DB evidence: %s", exc)

    hints = _database_hints(db_path)
    filters_cfg = (cfg or {}).get("filters") or {}

    stats = {
        "worldwide": len(seeds),
        "resolved": 0,
        "failed": 0,
        "excluded_hq": 0,
        "excluded_quality": 0,
        "cached": 0,
        "careers_link": 0,
        "verified_slug": 0,
        "database_hint": 0,
    }
    failures: Counter[str] = Counter()
    pending: list[tuple[dict, Path, int]] = []
    today_str = date.today().isoformat()
    counts = Counter(c.get("ats") for c in current)
    ats_order = sorted(ATS_ORDER, key=lambda ats: (-counts[ats], ATS_ORDER.index(ats)))
    limited_pending = []

    for seed in seeds:
        key = normalize_company(seed["company"])
        seed["_ats_order"] = ats_order
        existing = by_key.get(key)
        seed["hq_country"] = _hq(existing or seed, hq_lookup)

        # Quality check: exclude staffing, agencies, marketplaces
        if _is_excluded(seed, filters_cfg):
            stats["excluded_quality"] += 1
            by_key.pop(key, None)
            continue

        if seed["hq_country"] in deny_countries:
            stats["excluded_hq"] += 1
            by_key.pop(key, None)
            continue

        override = bool(seed.get("ats") and seed.get("board"))
        if existing and existing.get("ats") and existing.get("board") and (
            not override or (existing["ats"], existing["board"]) == (seed["ats"], seed["board"])
        ):
            if seed.get("aliases"):
                existing["aliases"] = seed["aliases"]
            stats["resolved"] += 1
            continue

        if override:
            by_key.pop(key, None)

        cached = cache_dir / f"{key}.json"
        cached_result = None
        if cached.exists():
            try:
                cached_result = json.loads(cached.read_text())
            except Exception:
                cached_result = None

        if override and (cached_result or {}).get("seed_override") != [seed["ats"], seed["board"]] and (
            (cached_result or {}).get("ats"), (cached_result or {}).get("board")
        ) != (seed["ats"], seed["board"]):
            cached_result = None

        if cached_result:
            if cached_result.get("ats"):
                by_key[key] = {**_seed_fields(seed), **{k: cached_result[k] for k in ("ats", "board")}, "added": today_str}
                stats["resolved"] += 1
                stats["cached"] += 1
                continue
            if cached_result.get("status") == "limited" or "rate limited" in str(cached_result.get("reason", "")):
                for field in ("retry_probes", "retry_trusted"):
                    if field in cached_result:
                        seed[field] = cached_result[field]
                limited_pending.append((seed, cached, 1))
                continue
            if cached_result.get("status") == "dead":
                stats["failed"] += 1
                stats["cached"] += 1
                failures["dead website"] += 1
                continue

            reason_str = str(cached_result.get("reason", ""))
            is_transient = _website_failure(reason_str)
            attempts = cached_result.get("attempts", 1)
            last_attempt = cached_result.get("last_attempt")

            if is_transient:
                if attempts >= max_attempts:
                    cached_result["status"] = "dead"
                    cached.write_text(json.dumps(cached_result))
                    stats["failed"] += 1
                    stats["cached"] += 1
                    failures[f"dead website after {max_attempts} attempts"] += 1
                    continue
                if last_attempt == today_str and not force_retry:
                    stats["failed"] += 1
                    stats["cached"] += 1
                    failures[cached_result.get("reason", "transient failure in cooldown")] += 1
                    continue
                pending.append((seed, cached, attempts + 1))
                continue
            else:
                if cached_result.get("resolver_version", 1) < 2:
                    pending.append((seed, cached, 1))
                    continue
                stats["failed"] += 1
                stats["cached"] += 1
                failures[cached_result.get("reason", "unknown")] += 1
                continue

        pending.append((seed, cached, 1))

    pending.extend(limited_pending)

    owners = verify_owners(by_key, cache_dir, workers, known={normalize_company(c["company"]) for c in current})
    stats["resolved"] -= owners["dropped"]
    stats["owner_check"] = owners

    # Resolve in a bounded time, saving as we go: a killed or timed-out build keeps its progress.
    deadline = time.monotonic() + max_seconds
    pool = ThreadPoolExecutor(max_workers=min(12, max(1, workers)))
    futures = {
        pool.submit(resolve_company, seed, hints=hints.get(normalize_company(seed["company"]))):
        (seed, cache, attempts) for seed, cache, attempts in pending
    }
    done = 0
    try:
        for future in as_completed(futures, timeout=max(1.0, deadline - time.monotonic())):
            seed, cached, attempts = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"reason": f"resolver error: {type(exc).__name__}"}

            if seed.get("ats") and seed.get("board"):
                result["seed_override"] = [seed["ats"], seed["board"]]
            result["resolver_version"] = 2
            result["attempts"] = attempts
            result["last_attempt"] = today_str
            reason_str = str(result.get("reason", ""))
            if not result.get("ats"):
                is_transient = _website_failure(reason_str)
                if "rate limited" in reason_str:
                    result["status"] = "limited"
                    result["attempts"] = 0
                elif is_transient:
                    result["status"] = "dead" if attempts >= max_attempts else "transient"
                else:
                    result["status"] = "miss"

            cached.write_text(json.dumps(result))
            if result.get("ats"):
                by_key[normalize_company(seed["company"])] = {
                    **_seed_fields(seed), "ats": result["ats"], "board": result["board"], "added": today_str
                }
                stats["resolved"] += 1
                method = {"careers link": "careers_link", "database hint": "database_hint"}.get(
                    result.get("reason"), "verified_slug"
                )
                stats[method] += 1
            else:
                stats["failed"] += 1
                failures[result.get("reason", "unknown")] += 1
            done += 1
            if done % save_every == 0:
                _write(watchlist_file, list(by_key.values()))
    except TimeoutError:
        stats["unfinished"] = len(futures) - done
        log.warning("watchlist build: time limit reached, %s companies left for the next build", stats["unfinished"])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    _write(watchlist_file, list(by_key.values()))
    stats["failure_reasons"] = dict(failures)
    return stats


def _ats_from_id(job_id: str) -> tuple[str, str] | None:
    parts = job_id.split(":", 2)
    if len(parts) < 3 or parts[0] not in {
        "ashby", "greenhouse", "lever", "lever_eu", "workable", "smartrecruiters",
        "recruitee", "breezy", "personio", "bamboohr", "pinpoint", "teamtailor",
        "workday", "rippling", "jazzhr", "homerun", "dover", "comeet", "factorial"
    }:
        return None
    return parts[0], parts[1]


def grow(db: sqlite3.Connection, watchlist_file: Path, *, deny_countries: tuple[str, ...] = ("in", "bd"),
         hq_lookup=None, cfg: dict | None = None) -> dict[str, int]:
    """Add ATS companies with a global tag, eligible decision, or positive feedback."""
    current = _read(watchlist_file)
    by_key = {normalize_company(c["company"]): c for c in current}
    filters_cfg = (cfg or {}).get("filters") or {}

    selected: dict[str, set[str]] = {}
    for job_id, payload in db.execute("SELECT id, payload FROM jobs"):
        try:
            job = json.loads(payload)
        except (TypeError, ValueError):
            continue
        src = job.get("source") or "ats"
        regions = job.get("regions") or []
        if "global" in regions or src == "freehire":
            selected.setdefault(job_id, set()).add(src)

    for job_id, in db.execute("SELECT DISTINCT job_id FROM decisions WHERE outcome IN ('eligible', 'apply') AND stage IN ('location', 'llm', 'report')"):
        selected.setdefault(job_id, set()).add("decisions")
    for job_id, in db.execute("SELECT DISTINCT job_id FROM feedback WHERE label IN ('eligible', 'good', 'applied') AND job_id IS NOT NULL"):
        selected.setdefault(job_id, set()).add("feedback")

    added = excluded = excluded_quality = unsupported = 0
    for job_id, sources in selected.items():
        parsed = _ats_from_id(job_id)
        if not parsed:
            unsupported += 1
            continue
        row = db.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            continue
        try:
            job = json.loads(row[0])
        except Exception:
            continue
        company = job.get("company") or ""
        key = normalize_company(company)
        if not key or key in by_key:
            continue

        if _is_excluded({"company": company}, filters_cfg):
            excluded_quality += 1
            continue

        country = _hq({"company": company, "hq_country": job.get("hq_country")}, hq_lookup)
        if country in deny_countries:
            excluded += 1
            continue

        ats, board = parsed
        by_key[key] = {
            "company": company,
            "ats": ats,
            "board": board,
            "hq_country": country,
            "hires": "worldwide" if "global" in (job.get("regions") or []) else [],
            "source": "feedback" if "feedback" in sources else (list(sources)[0] if sources else "freehire"),
            "added": date.today().isoformat(),
        }
        added += 1

    if added:
        _write(watchlist_file, list(by_key.values()))
    return {"added": added, "excluded_hq": excluded, "excluded_quality": excluded_quality, "unsupported_id": unsupported, "total": len(by_key)}


def list_entries(path: Path) -> list[dict]:
    return _read(path)
