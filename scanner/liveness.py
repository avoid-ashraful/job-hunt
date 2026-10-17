"""Is the posting still open? Asked of the employer's ATS right before the report is written.

Only a clear answer counts: a 404/410 from the ATS API (or the posting page itself), or
the job missing from a non-empty Ashby board. Timeouts, rate limits and server errors
are "unknown" and the job is kept unless its age requires confirmation.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from urllib.parse import urlsplit

import requests

from scanner.models import Job
from scanner.sources._public import ats_id

log = logging.getLogger(__name__)
OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"
TIMEOUT = 20


def _status(url: str) -> str:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "job-hunt-scanner (personal use)"})
    except requests.RequestException:
        return UNKNOWN
    if resp.status_code in (404, 410):
        return CLOSED
    return OPEN if resp.ok else UNKNOWN


@lru_cache(maxsize=256)
def _ashby_board(board: str) -> frozenset[str] | None:
    try:
        resp = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=TIMEOUT)
        jobs = resp.json().get("jobs") if resp.ok else None
    except (requests.RequestException, ValueError):
        return None
    return frozenset(j["id"] for j in jobs) if jobs else None  # an empty board is inconclusive


def _ats_identity(job: Job) -> tuple[str, str, str] | None:
    ats, _, rest = job.id.partition(":")
    board, _, ext = rest.partition(":")
    if ats in {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday", "rippling", "dover"} and board and ext:
        return ats, board, ext
    if identity := ats_id(job.url, job.company):
        return tuple(identity.split(":", 2))
    return None


def can_verify(job: Job) -> bool:
    return _ats_identity(job) is not None


def check(job: Job) -> str:
    ats, board, ext = _ats_identity(job) or ("", "", "")
    if ats == "workday" and board and ext:
        from scanner.sources._more_ats import base, landing
        try:
            api, _ = base(ats, board)
            prefix = landing(ats, board)
        except ValueError:
            return UNKNOWN
        parsed = urlsplit(job.url)
        if parsed.hostname == urlsplit(prefix).hostname and "/job/" in parsed.path:
            return _status(api.removesuffix("/jobs") + "/job/" + parsed.path.split("/job/", 1)[1])
        return UNKNOWN
    if ats == "rippling" and board and ext:
        return _status(f"https://ats.rippling.com/api/v2/board/{board}/jobs/{ext}")
    if ats == "dover" and board and ext:
        return _status(f"https://app.dover.com/api/v1/inbound/application-portal-job/{ext}")
    if ats == "greenhouse" and board and ext:
        return _status(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{ext}")
    if ats == "lever" and board and ext:
        return _status(f"https://api.lever.co/v0/postings/{board}/{ext}")
    if ats == "workable" and board and ext:
        return _status(f"https://apply.workable.com/api/v2/accounts/{board}/jobs/{ext}")
    if ats == "smartrecruiters" and board and ext:
        return _status(f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{ext}")
    if ats == "ashby" and board and ext:
        ids = _ashby_board(board)
        return UNKNOWN if ids is None else OPEN if ext in ids else CLOSED
    return _status(job.url)


def check_all(jobs: list[Job], workers: int = 8) -> dict[str, str]:
    with ThreadPoolExecutor(workers) as pool:
        results = dict(zip((j.id for j in jobs), pool.map(check, jobs)))
    counts = {s: sum(1 for v in results.values() if v == s) for s in (OPEN, CLOSED, UNKNOWN)}
    log.info("liveness: %(open)s open, %(closed)s closed, %(unknown)s unknown", counts)
    return results
