"""Remote OK's public JSON feed, retaining its required attribution link."""

from collections.abc import Iterator

from scanner.models import Job
from scanner.sources._boards import board_job, recent
from scanner.sources._public import get, session


def to_job(raw: dict) -> Job | None:
    if not raw.get("id"):
        return None
    source_url = raw.get("url") or ""
    return board_job(
        source="remoteok", key=str(raw["id"]), url=raw.get("apply_url") or source_url,
        source_url=source_url, company=raw.get("company") or "", title=raw.get("position") or "",
        description=raw.get("description") or "", location=raw.get("location") or "Remote",
        posted=raw.get("date") or raw.get("epoch"), skills=raw.get("tags") or [],
        salary_min=raw.get("salary_min") or None, salary_max=raw.get("salary_max") or None,
        salary_currency="USD" if raw.get("salary_min") or raw.get("salary_max") else None,
        salary_period="year" if raw.get("salary_min") or raw.get("salary_max") else None,
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    for raw in get(session(), cfg.get("base_url", "https://remoteok.com/api")).json():
        job = to_job(raw)
        if recent(job, days):
            yield job
