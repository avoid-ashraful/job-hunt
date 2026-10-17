"""Jobicy's public seven-day remote jobs API; retain its source URL."""

import time
from collections.abc import Iterator

from scanner.models import Job
from scanner.sources._boards import board_job, recent
from scanner.sources._public import get, session


def to_job(raw: dict) -> Job | None:
    url = raw.get("url") or ""
    types = raw.get("jobType") or []
    return board_job(
        source="jobicy", key=str(raw.get("id") or url), url=url, source_url=url,
        company=raw.get("companyName") or "", title=raw.get("jobTitle") or "",
        description=raw.get("jobDescription") or raw.get("jobExcerpt") or "",
        location=raw.get("jobGeo") or "Remote", posted=raw.get("pubDate"),
        skills=raw.get("jobIndustry") or [], seniority=raw.get("jobLevel"),
        employment_type={"Full-Time": "full_time", "Part-Time": "part_time", "Contract": "contract"}.get(
            types[0] if types else ""),
        salary_min=raw.get("salaryMin"), salary_max=raw.get("salaryMax"),
        salary_currency=raw.get("salaryCurrency"),
        salary_period={"yearly": "year", "monthly": "month", "hourly": "hour"}.get(raw.get("salaryPeriod")),
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    s = session()
    params = {"count": cfg.get("count", 50), "tag": cfg.get("tag", "python")}
    if cfg.get("geo"):
        params["geo"] = cfg["geo"]
    for page in range(cfg.get("max_pages", 1)):
        if page:
            time.sleep(cfg.get("request_delay_s", 1.0))
        data = get(s, cfg.get("base_url", "https://jobicy.com/api/v2/remote-jobs"), params).json()
        for raw in data.get("jobs") or []:
            job = to_job(raw)
            if recent(job, days):
                yield job
        if not data.get("nextCursor"):
            break
        params["cursor"] = data["nextCursor"]
