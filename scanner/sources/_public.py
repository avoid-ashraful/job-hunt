"""Small shared helpers for polite public job feeds and their HTML descriptions."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests

from scanner.dedup import normalize_company

log = logging.getLogger(__name__)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "br", "li", "div", "h1", "h2", "h3"}:
            self.parts.append("\n")


def plain_html(value: str) -> str:
    parser = _Text()
    parser.feed(value or "")
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()


def date(value: str | int | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, int):
        return datetime.fromtimestamp(value, timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "job-hunt-scanner (personal use)"
    return s


def get(s: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    for attempt in range(3):
        response = s.get(url, params=params, timeout=30)
        if response.status_code == 429 or response.status_code >= 500:
            wait = min(int(response.headers.get("Retry-After") or 0) or 2 ** (attempt + 1), 60)
            log.warning("%s returned %s; retrying in %ss", urlsplit(url).netloc, response.status_code, wait)
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError(f"public feed failed after retries: {urlsplit(url).netloc}")


def ats_id(url: str, company: str) -> str | None:
    """Recognize employer links only when their actual ATS identifier is visible."""
    p = urlsplit(url)
    host, path = p.netloc.lower(), p.path.strip("/").split("/")
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and len(path) >= 2:
        return f"greenhouse:{path[0]}:{path[-1]}"
    if host == "jobs.lever.co" and len(path) >= 2:
        return f"lever:{path[0]}:{path[1]}"
    if host == "jobs.ashbyhq.com" and len(path) >= 2:
        return f"ashby:{path[0]}:{path[1]}"
    if host == "apply.workable.com" and len(path) >= 2 and path[-2] == "j":
        return f"workable:{normalize_company(company)}:{path[-1]}"
    if host == "jobs.smartrecruiters.com" and len(path) >= 2:
        return f"smartrecruiters:{path[0]}:{path[1]}"
    return None
