"""Is the posting still open? Asked of the employer's ATS right before the report is written.

Only a clear answer counts: a 404/410 from the ATS API (or the posting page itself), or
the job missing from a non-empty Ashby board. Timeouts, rate limits and server errors
are "unknown" and the job is kept.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import requests

from scanner.models import Job

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


def check(job: Job) -> str:
    ats, _, rest = job.id.partition(":")
    board, _, ext = rest.partition(":")
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
