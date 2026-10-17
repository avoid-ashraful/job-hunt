"""We Work Remotely's public category RSS feeds with board application links."""

import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from email.utils import parsedate_to_datetime

from scanner.models import Job
from scanner.sources._boards import board_job, recent
from scanner.sources._public import get, session

FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
]


def to_job(item: ET.Element) -> Job | None:
    title = item.findtext("title") or ""
    company, separator, title = title.partition(": ")
    if not separator:
        return None
    url = item.findtext("link") or item.findtext("guid") or ""
    region = item.findtext("region") or ""
    countries = item.findtext("country") or ""
    state = item.findtext("state") or ""
    # Some listings say Anywhere while enumerating hiring countries or states.
    location = "; ".join(x for x in (countries, state) if x) or region or "Remote"
    posted = item.findtext("pubDate")
    return board_job(
        source="wwr", key=item.findtext("guid") or url, url=url, source_url=url,
        company=company, title=title, description=item.findtext("description") or "",
        location=location, posted=parsedate_to_datetime(posted).isoformat() if posted else None,
        skills=[x.strip() for x in (item.findtext("skills") or "").split(",") if x.strip()],
        category=item.findtext("category"),
        employment_type={"Full-Time": "full_time", "Part-Time": "part_time", "Contract": "contract"}.get(
            item.findtext("type")),
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    s = session()
    seen = set()
    for index, url in enumerate(cfg.get("feeds", FEEDS)):
        if index:
            time.sleep(cfg.get("request_delay_s", 1.0))
        for item in ET.fromstring(get(s, url).content).findall(".//item"):
            job = to_job(item)
            if recent(job, days) and job.id not in seen:
                seen.add(job.id)
                yield job
