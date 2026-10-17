"""Himalayas public API: search worldwide remote roles without a browser or login."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job
from scanner.sources._public import ats_id, date, get, plain_html, session

log = logging.getLogger(__name__)


def to_job(raw: dict) -> Job | None:
    link = raw.get("applicationLink") or raw.get("guid") or ""
    zones = raw.get("timezoneRestrictions") or []
    if not link or raw.get("locationRestrictions") or (zones and 6 not in zones):
        return None
    company = raw.get("companyName") or ""
    posted = date(raw.get("pubDate"))
    salary_period = {"yearly": "year", "monthly": "month", "hourly": "hour"}.get(raw.get("salaryPeriod"))
    employment = {"full time": "full_time", "contractor": "contract", "contract": "contract"}.get(
        str(raw.get("employmentType") or "").lower()
    )
    return Job(
        id=ats_id(link, company) or f"himalayas::{raw.get('guid') or link}",
        source="himalayas",
        url=normalize_url(link),
        title=raw.get("title") or "",
        company=company,
        company_key=normalize_company(raw.get("companySlug") or company),
        description=plain_html(raw.get("description") or raw.get("excerpt") or ""),
        location="Remote worldwide",
        regions=["global"],
        posted_at=posted,
        first_seen_at=posted,
        last_seen_at=datetime.now(timezone.utc),
        employment_type=employment,
        salary_min=raw.get("minSalary"),
        salary_max=raw.get("maxSalary"),
        salary_currency=raw.get("currency"),
        salary_period=salary_period,
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    s = session()
    if cfg.get("api_key"):
        s.headers["Authorization"] = f"Bearer {cfg['api_key']}"
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for term in cfg.get("queries", ["python", "django"]):
        for page in range(1, cfg.get("max_pages_per_query", 3) + 1):
            data = get(s, cfg.get("base_url", "https://himalayas.app/jobs/api/search"),
                       {"q": term, "worldwide": "true", "sort": "recent", "page": page}).json()
            jobs = data.get("jobs") or []
            if not jobs:
                break
            for raw in jobs:
                job = to_job(raw)
                if job and (not job.posted_at or job.posted_at >= cutoff):
                    yield job
            if len(jobs) < data.get("limit", len(jobs)):
                break
            time.sleep(cfg.get("request_delay_s", 1.0))
