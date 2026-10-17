"""Direct HN job posts and top-level remote offers in monthly hiring threads."""

from __future__ import annotations

import html
import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from scanner.dedup import normalize_company, normalize_url
from scanner.models import Job
from scanner.sources._public import ats_id, date, get, plain_html, session

API = "https://hn.algolia.com/api/v1/search_by_date"


def _months(now: datetime) -> set[str]:
    previous = now.replace(day=1) - timedelta(days=1)
    return {now.strftime("%B %Y"), previous.strftime("%B %Y")}


def _pages(s, params: dict, max_pages: int, delay: float) -> Iterator[dict]:
    for page in range(max_pages):
        data = get(s, API, {**params, "page": page, "hitsPerPage": 100}).json()
        yield from data.get("hits") or []
        if page + 1 >= data.get("nbPages", 0):
            break
        time.sleep(delay)


def _comment_job(raw: dict, cfg: dict) -> Job | None:
    text = plain_html(raw.get("comment_text") or "")
    if not re.search(cfg["remote_pattern"], text, re.I):
        return None
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines:
        return None
    head = lines[0]
    # headers without a pipe: "Acme - we are hiring", "Acme — Role", "Acme is hiring"
    piped = bool(re.search(r"[|•·]", head))
    sep = r"\s*[|•·]\s*" if piped else r"\s+[—–-]\s+|\s+is hiring\b"
    fields = [x.strip() for x in re.split(sep, head) if x.strip()]
    # "Acme (https://acme.com)" / "Acme (YC S23, acme.com)" / "Acme (Remote US)": keep the name only
    company = re.sub(r"\s*\([^)]*(?:\.\w{2,}|\bYC\b|\bremote\b)[^)]*\)", "", fields[0], flags=re.I).strip()[:100]
    if re.search(cfg["role_pattern"], company, re.I) or len(company) > 70:
        return None
    role = next((x for x in fields[1:] if re.search(cfg["role_pattern"], x, re.I)), "")
    if not role:
        match = re.search(cfg["role_pattern"], text, re.I)
        role = match.group(0) if match else ""
    if not role or not company:
        return None
    # Algolia HTML escapes slashes (https:&#x2F;&#x2F;) and truncates link text, so read the hrefs
    links = [html.unescape(x) for x in re.findall(r'href="([^"]+)"', raw.get("comment_text") or "")]
    target = next((x for x in links if ats_id(x, company)), "")
    comment_url = f"https://news.ycombinator.com/item?id={raw['objectID']}"
    where = next((x for x in fields[1:] if piped and x != role and re.search(cfg["remote_pattern"], x, re.I)), "")
    # worldwide only in a sentence that is also about remote work, not "customers worldwide"
    worldwide = any(re.search(cfg["remote_pattern"], x, re.I) and re.search(cfg["worldwide_pattern"], x, re.I)
                    for x in re.split(r"(?<=[.!?])\s+|\n", text))
    posted = date(raw.get("created_at"))
    return Job(
        id=ats_id(target, company) if target else f"hn::{raw['objectID']}",
        source="hn",
        url=normalize_url(target or comment_url),
        title=role[:160],
        company=company,
        company_key=normalize_company(company),
        description=text,
        location="Remote worldwide" if worldwide else where[:80] or "Remote",
        regions=["global"] if worldwide else [],
        posted_at=posted,
        first_seen_at=posted,
        last_seen_at=datetime.now(timezone.utc),
    )


def _job_story(raw: dict, cfg: dict) -> Job | None:
    title = raw.get("title") or ""
    if not re.search(cfg["remote_pattern"], title, re.I):
        return None
    company = re.split(r"\s+(?:is\s+)?hiring\b", title, maxsplit=1, flags=re.I)[0]
    role_match = re.search(cfg["role_pattern"], title, re.I)
    if not role_match:
        return None
    target = raw.get("url") or ""
    posted = date(raw.get("created_at"))
    return Job(
        id=ats_id(target, company) or f"hn:jobs:{raw['objectID']}",
        source="hn",
        url=normalize_url(target or f"https://news.ycombinator.com/item?id={raw['objectID']}"),
        title=role_match.group(0),
        company=company,
        company_key=normalize_company(company),
        description=plain_html(raw.get("story_text") or title),
        location="Remote",
        posted_at=posted,
        first_seen_at=posted,
        last_seen_at=datetime.now(timezone.utc),
    )


def fetch(cfg: dict, days: int) -> Iterator[Job]:
    s = session()
    now = datetime.now(timezone.utc)
    delay = cfg.get("request_delay_s", 1.0)
    cutoff = int((now - timedelta(days=days)).timestamp())
    if cfg.get("jobs_page", True):
        for raw in _pages(s, {"tags": "job", "numericFilters": f"created_at_i>{cutoff}"},
                          cfg.get("max_job_pages", 2), delay):
            job = _job_story(raw, cfg)
            if job:
                yield job
    # Search a narrow, recent window; only exact monthly thread titles are accepted.
    thread_cutoff = int((now.replace(day=1) - timedelta(days=1)).replace(day=1).timestamp())
    for raw in _pages(s, {"tags": "story", "query": "Ask HN: Who is hiring?",
                          "numericFilters": f"created_at_i>{thread_cutoff}"}, 2, delay):
        created = date(raw.get("created_at"))
        title = raw.get("title") or ""
        if not created or title != f"Ask HN: Who is hiring? ({created.strftime('%B %Y')})" or created.strftime("%B %Y") not in _months(now):
            continue
        story_id = raw["objectID"]
        for comment in _pages(s, {"tags": f"comment,story_{story_id}"},
                              cfg.get("max_comment_pages", 15), delay):
            if str(comment.get("parent_id")) != str(story_id):
                continue
            job = _comment_job(comment, cfg)
            if job:
                yield job
