"""Working Nomads' public exposed-jobs feed."""

from collections.abc import Iterator

from scanner.models import Job
from scanner.sources._boards import board_job, recent
from scanner.sources._public import get, session


def to_job(raw: dict) -> Job | None:
    source_url = raw.get("url") or ""
    tags = raw.get("tags") or ""
    return board_job(
        source="workingnomads", key=source_url, url=raw.get("apply_url") or source_url,
        source_url=source_url, company=raw.get("company_name") or "", title=raw.get("title") or "",
        description=raw.get("description") or "", location=raw.get("location") or "Remote",
        posted=raw.get("pub_date"), skills=[tag.strip() for tag in tags.split(",") if tag.strip()],
        category=raw.get("category_name"),
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    url = cfg.get("base_url", "https://www.workingnomads.com/api/exposed_jobs/")
    for raw in get(session(), url).json():
        job = to_job(raw)
        if recent(job, days):
            yield job
