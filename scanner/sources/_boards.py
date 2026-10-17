"""Normalization shared by the remote board feeds."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job
from scanner.sources._public import ats_id, date, plain_html


def board_job(*, source: str, key: str, url: str, source_url: str, company: str,
              title: str, description: str, location: str, posted: str | int | None,
              skills: list[str] | None = None, **fields) -> Job | None:
    if not url or not title or not company:
        return None
    published = date(posted)
    if published and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    global_location = re.fullmatch(r"(?:remote[\s,;:()/-]*)?(?:worldwide|global|anywhere(?: in the world)?)",
                                   location.strip(), re.I)
    regions = ["global"] if global_location else [
        region for region, pattern in {
            "apac": r"\b(?:apac|asia|asia pacific)\b",
            "emea": r"\b(?:emea|europe|africa|middle east)\b",
            "latam": r"\b(?:latam|latin america)\b",
            "north_america": r"\bnorth america\b",
        }.items() if re.search(pattern, location, re.I)
    ]
    return Job(
        id=ats_id(url, company) or f"{source}::{key or source_url or url}",
        source=source, url=normalize_url(url), title=plain_html(title), company=company,
        company_key=normalize_company(company), description=plain_html(description),
        location=location or "Remote", regions=regions, skills=skills or [],
        ats_metadata={"source_url": source_url}, posted_at=published,
        first_seen_at=published, last_seen_at=datetime.now(timezone.utc), **fields,
    )


def recent(job: Job | None, days: int) -> bool:
    return job is not None and (job.posted_at is None or
                               job.posted_at >= datetime.now(timezone.utc) - timedelta(days=days))
