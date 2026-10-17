"""Resolve remote-friendly companies to their public ATS boards and grow that list from evidence."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

from scanner.dedup import normalize_company
from scanner.sources.ats import USER_AGENT, _base, _items, get

log = logging.getLogger(__name__)
ATS_LINKS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#\"'<>]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([^/?#\"'<>]+)", re.I),
    "lever_eu": re.compile(r"jobs\.eu\.lever\.co/([^/?#\"'<>]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#\"'<>]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([^/?#\"'<>]+)", re.I),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/([^/?#\"'<>]+)", re.I),
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
    return {"company": front.get("title") or front.get("slug") or path.stem,
            "website": front.get("website") or "", "careers_url": front.get("careers_url") or "",
            "hq_country": front.get("hq_country") or "", "hires": "worldwide", "source": "remoteintech"}


def _slugs(company: dict) -> list[str]:
    name = normalize_company(company["company"])
    domain = urlparse(company.get("website") or "").hostname or ""
    stem = domain.removeprefix("www.").split(".")[0]
    return list(dict.fromkeys(x for x in (name, name.replace("-", ""), stem) if x))


def _links(html: str) -> list[tuple[str, str]]:
    found = []
    for ats, pattern in ATS_LINKS.items():
        found.extend((ats, m.group(1).strip("/")) for m in pattern.finditer(html))
    return list(dict.fromkeys(found))


def _valid_board(session: requests.Session, ats: str, board: str) -> bool:
    try:
        url, params = _base(ats, board)
        data = get(session, url, params=params, retries=1, timeout=7)
        if ats in {"lever", "lever_eu"}:
            return isinstance(data, list) and bool(data)
        if not isinstance(data, dict):
            return False
        key = "content" if ats == "smartrecruiters" else "jobs"
        return isinstance(data.get(key), list) and bool(data[key])
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return False


def resolve_company(company: dict, session: requests.Session | None = None) -> dict:
    """Prefer actual careers links, then a bounded set of slug guesses verified by API."""
    own = session is None
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    try:
        links = []
        failures = []
        for url in dict.fromkeys(filter(None, (company.get("careers_url"), company.get("website")))):
            try:
                response = session.get(url, timeout=8)
                response.raise_for_status()
                links.extend(_links(response.url + "\n" + response.text))
            except requests.RequestException as exc:
                failures.append(f"careers page: {type(exc).__name__}")
        guesses = [(ats, slug) for slug in _slugs(company) for ats in ("ashby", "greenhouse", "lever", "lever_eu", "smartrecruiters")]
        for ats, board in dict.fromkeys(links + guesses):
            if _valid_board(session, ats, board):
                return {"ats": ats, "board": board, "reason": "careers link" if (ats, board) in links else "verified slug"}
        reason = "no supported ATS board found"
        if failures:
            reason += f" ({', '.join(dict.fromkeys(failures))})"
        return {"reason": reason}
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


def build(vendor_dir: Path, watchlist_file: Path, cache_dir: Path, *, workers: int = 8,
          deny_countries: tuple[str, ...] = ("in", "bd"), hq_lookup=None) -> dict:
    """Seed from remoteintech worldwide entries; cache both successes and misses."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    current = _read(watchlist_file)
    by_key = {normalize_company(c["company"]): c for c in current}
    files = sorted(vendor_dir.glob("*.md"))
    seeds = [c for path in files if (c := _company_file(path))]
    if not files:
        raise FileNotFoundError(f"remoteintech company files missing: {vendor_dir}")
    stats = {"worldwide": len(seeds), "resolved": 0, "failed": 0, "excluded_hq": 0, "cached": 0, "careers_link": 0, "verified_slug": 0}
    failures: Counter[str] = Counter()
    pending = []
    for seed in seeds:
        key = normalize_company(seed["company"])
        existing = by_key.get(key)
        seed["hq_country"] = _hq(existing or seed, hq_lookup)
        if seed["hq_country"] in deny_countries:
            stats["excluded_hq"] += 1
            by_key.pop(key, None)
            continue
        if existing and existing.get("ats") and existing.get("board"):
            stats["resolved"] += 1
            continue
        cached = cache_dir / f"{key}.json"
        if cached.exists():
            result = json.loads(cached.read_text())
            stats["cached"] += 1
            if result.get("ats"):
                by_key[key] = {**seed, **{k: result[k] for k in ("ats", "board")}, "added": date.today().isoformat()}
                stats["resolved"] += 1
            else:
                stats["failed"] += 1
                failures[result.get("reason", "unknown")] += 1
            continue
        pending.append((seed, cached))
    with ThreadPoolExecutor(max_workers=min(12, max(1, workers))) as pool:
        futures = {pool.submit(resolve_company, seed): (seed, cache) for seed, cache in pending}
        for future in as_completed(futures):
            seed, cached = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"reason": f"resolver error: {type(exc).__name__}"}
            cached.write_text(json.dumps(result))
            if result.get("ats"):
                by_key[normalize_company(seed["company"])] = {
                    **seed, "ats": result["ats"], "board": result["board"], "added": date.today().isoformat()}
                stats["resolved"] += 1
                stats["careers_link" if result["reason"] == "careers link" else "verified_slug"] += 1
            else:
                stats["failed"] += 1
                failures[result.get("reason", "unknown")] += 1
    _write(watchlist_file, list(by_key.values()))
    stats["failure_reasons"] = dict(failures)
    return stats


def _ats_from_id(job_id: str) -> tuple[str, str] | None:
    parts = job_id.split(":", 2)
    if len(parts) < 3 or parts[0] not in {"ashby", "greenhouse", "lever", "lever_eu", "workable", "smartrecruiters"}:
        return None
    return parts[0], parts[1]


def grow(db: sqlite3.Connection, watchlist_file: Path, *, deny_countries: tuple[str, ...] = ("in", "bd"),
         hq_lookup=None) -> dict[str, int]:
    """Add ATS companies with a global tag, eligible decision, or positive feedback."""
    current = _read(watchlist_file)
    by_key = {normalize_company(c["company"]): c for c in current}
    selected: dict[str, set[str]] = {}
    for job_id, payload in db.execute("SELECT id, payload FROM jobs"):
        try:
            job = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if job.get("source") == "freehire" and "global" in (job.get("regions") or []):
            selected.setdefault(job_id, set()).add("freehire")
    for job_id, in db.execute("SELECT DISTINCT job_id FROM decisions WHERE outcome IN ('eligible', 'apply') AND stage IN ('location', 'llm', 'report')"):
        selected.setdefault(job_id, set()).add("freehire")
    for job_id, in db.execute("SELECT DISTINCT job_id FROM feedback WHERE label IN ('eligible', 'good', 'applied') AND job_id IS NOT NULL"):
        selected.setdefault(job_id, set()).add("feedback")
    added = excluded = unsupported = 0
    for job_id, sources in selected.items():
        parsed = _ats_from_id(job_id)
        if not parsed:
            unsupported += 1
            continue
        row = db.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            continue
        job = json.loads(row[0])
        company = job.get("company") or ""
        key = normalize_company(company)
        if not key or key in by_key:
            continue
        country = _hq({"company": company, "hq_country": job.get("hq_country")}, hq_lookup)
        if country in deny_countries:
            excluded += 1
            continue
        ats, board = parsed
        by_key[key] = {"company": company, "ats": ats, "board": board, "hq_country": country,
                       "hires": "worldwide" if "global" in (job.get("regions") or []) else [],
                       "source": "feedback" if "feedback" in sources else "freehire",
                       "added": date.today().isoformat()}
        added += 1
    if added:
        _write(watchlist_file, list(by_key.values()))
    return {"added": added, "excluded_hq": excluded, "unsupported_id": unsupported, "total": len(by_key)}


def list_entries(path: Path) -> list[dict]:
    return _read(path)
