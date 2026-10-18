"""Per-board fetch health, separate from company resolution results."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def _path(directory: Path, ats: str, board: str) -> Path:
    key = hashlib.sha256(f"{ats.lower()}:{board}".encode()).hexdigest()
    return directory / f"{key}.json"


def read(directory: Path | None, ats: str, board: str) -> dict:
    if directory is None:
        return {}
    try:
        return json.loads(_path(directory, ats, board).read_text())
    except (OSError, ValueError):
        return {}


def record(directory: Path | None, ats: str, board: str, *, jobs: int | None = None,
           error: int | str | None = None, today: str | None = None, **extra) -> None:
    if directory is None:
        return
    today = today or date.today().isoformat()
    health = read(directory, ats, board)
    health.update(ats=ats, board=board, last_checked=today, error=error, **extra)
    health["not_found_failures"] = health.get("not_found_failures", 0) + 1 if error in (404, 410) else 0
    health["other_failures"] = health.get("other_failures", 0) + 1 if error is not None and error not in (404, 410) else 0
    if error is None:
        health.update(last_ok=today, jobs=jobs)
        health["first_empty"] = (health.get("first_empty") or today) if jobs == 0 else None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = _path(directory, ats, board)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(health))
        temporary.replace(path)
    except OSError as exc:
        log.warning("Cannot save ATS health for %s/%s: %s", ats, board, exc)


def stale(health: dict) -> bool:
    if health.get("not_found_failures", 0) >= 3:
        return True
    # A transient error must never retire an otherwise watched board.
    if health.get("error") is not None:
        return False
    try:
        return (date.today() - date.fromisoformat(health["first_empty"])).days >= 28
    except (KeyError, TypeError, ValueError):
        return False
