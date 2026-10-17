"""freehire public API (https://freehire.me/docs/api): keyless, full descriptions via /agent/jobs/search."""

from __future__ import annotations

import html
import logging
import time
from collections.abc import Iterator
from datetime import datetime

import requests

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job

log = logging.getLogger(__name__)

MAX_DEPTH = 10_000  # API rejects offset + limit beyond this


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get(session: requests.Session, url: str, params: dict, retries: int = 5) -> dict:
    for attempt in range(retries):
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = int(resp.headers.get("Retry-After") or 0) or 2 ** (attempt + 1)
            log.warning("freehire %s, retrying in %ss", resp.status_code, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"freehire: giving up after {retries} attempts: {url} {params}")


def to_job(raw: dict) -> Job:
    enr = raw.get("enrichment") or {}
    return Job(
        id=f"{raw['source']}:{raw['external_id']}",
        source="freehire",
        url=normalize_url(raw["url"]),
        title=raw.get("title") or "",
        company=raw.get("company") or raw.get("company_slug") or "",
        company_key=normalize_company(raw.get("company_slug") or raw.get("company") or ""),
        description=html.unescape(raw.get("description") or ""),  # text format still carries &#xa; etc.
        location=raw.get("location") or "",
        regions=raw.get("regions") or [],
        countries=raw.get("countries") or [],
        cities=raw.get("cities") or [],
        ats_metadata={"freehire_company_slug": raw["company_slug"]} if raw.get("company_slug") else {},
        skills=raw.get("skills") or [],
        posted_at=_dt(raw.get("posted_at")),
        first_seen_at=_dt(raw.get("created_at")),
        last_seen_at=_dt(raw.get("last_seen_at")),
        seniority=enr.get("seniority"),
        category=enr.get("category"),
        employment_type=enr.get("employment_type"),
        company_type=enr.get("company_type"),
        domains=enr.get("domains") or [],
        posting_language=enr.get("posting_language"),
        summary=enr.get("summary") or "",
        salary_min=enr.get("salary_min"),
        salary_max=enr.get("salary_max"),
        salary_currency=enr.get("salary_currency"),
        salary_period=enr.get("salary_period"),
        requires_clearance=enr.get("requires_clearance"),
        reality=raw.get("reality") or {},
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    """Yield remote jobs first recorded within `days`, one category at a time."""
    url = cfg["base_url"].rstrip("/") + "/agent/jobs/search"
    limit = cfg.get("page_size", 100)
    delay = cfg.get("request_delay_s", 1.0)
    session = requests.Session()
    session.headers["User-Agent"] = "job-hunt-scanner (personal use)"

    for category in cfg["categories"]:
        offset, total = 0, None
        while total is None or offset < total:
            if offset + limit > MAX_DEPTH:
                log.warning("freehire: %s has %s jobs, only the first %s are reachable", category, total, MAX_DEPTH)
                break
            params = {
                "work_mode": cfg.get("work_mode", "remote"),
                "category": category,
                "open_within_days": days,
                "sort": "created_at",
                "order": "desc",
                "limit": limit,
                "offset": offset,
                "description_format": "text",
            }
            if cfg.get("seniority_exclude"):
                params["seniority_exclude"] = ",".join(cfg["seniority_exclude"])
            data = _get(session, url, params)
            meta = data.get("meta") or {}
            if meta.get("ignored_params"):
                raise RuntimeError(f"freehire ignored params {meta['ignored_params']}: the API changed")
            total = meta.get("total", 0)
            page = data.get("data") or []
            log.info("freehire %s: %s-%s of %s", category, offset, offset + len(page), total)
            for raw in page:
                yield to_job(raw)
            if not page:
                break
            offset += limit
            time.sleep(delay)
