"""Workable cross-company search, limited to published remote jobs."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job
from scanner.sources._public import ats_id, date, get, plain_html, session

log = logging.getLogger(__name__)


def to_job(raw: dict) -> Job | None:
    if raw.get("state") != "published" or raw.get("workplace") != "remote":
        return None
    company = (raw.get("company") or {}).get("title") or ""
    url = raw.get("linkoutUrl") or raw.get("url") or ""
    if not url:
        return None
    parts = urlsplit(url).path.split("/")
    view_id = parts[2] if len(parts) > 2 and parts[1] == "view" else str(raw.get("id") or "")
    location = raw.get("location") or {}
    places = [x for x in raw.get("locations") or [] if x != "TELECOMMUTE"]
    # A country on Workable's remote listing is a hiring-location clue, not proof of worldwide access.
    loc = ", ".join(places) or location.get("countryName") or "Remote"
    posted = date(raw.get("created"))
    employment = {"full-time": "full_time", "contract": "contract", "part-time": "part_time"}.get(
        str(raw.get("employmentType") or "").lower()
    )
    return Job(
        id=ats_id(url, company) or f"workable:{normalize_company(company)}:{view_id}",
        source="workable",
        url=normalize_url(url),
        title=raw.get("title") or "",
        company=company,
        company_key=normalize_company(company),
        description=plain_html(" ".join(str(raw.get(k) or "") for k in
                                ("description", "requirementsSection", "benefitsSection"))),
        location=f"Remote; {loc}",
        posted_at=posted,
        first_seen_at=posted,
        last_seen_at=datetime.now(timezone.utc),
        employment_type=employment,
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    s = session()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for term in cfg.get("queries", ["python", "django"]):
        token = None
        for _ in range(cfg.get("max_pages_per_query", 3)):
            params = {"query": term, "workplace": "remote"}
            if token:
                params["pageToken"] = token
            data = get(s, cfg.get("base_url", "https://jobs.workable.com/api/v1/jobs"), params).json()
            for raw in data.get("jobs") or []:
                job = to_job(raw)
                if job and (not job.posted_at or job.posted_at >= cutoff):
                    yield job
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(cfg.get("request_delay_s", 1.0))
