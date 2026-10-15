"""SQLite state: every job ever fetched and every job ever reported."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from scanner.dedup import fingerprint
from scanner.models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    company_key TEXT NOT NULL,
    title TEXT NOT NULL,
    first_fetched TEXT NOT NULL,
    last_fetched TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reported (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    company_key TEXT NOT NULL,
    family TEXT,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    reported_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS reported_url ON reported(url);
CREATE INDEX IF NOT EXISTS reported_fp ON reported(fingerprint);
CREATE TABLE IF NOT EXISTS runs (
    run_at TEXT NOT NULL,
    fetched INTEGER, passed INTEGER, new INTEGER, reported INTEGER,
    report TEXT,
    deferred INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
        if "deferred" not in cols:
            self.db.execute("ALTER TABLE runs ADD COLUMN deferred INTEGER NOT NULL DEFAULT 0")

    def is_empty(self) -> bool:
        """No completed (non-dry) run yet, so the next run should backfill."""
        return self.db.execute("SELECT 1 FROM runs LIMIT 1").fetchone() is None

    def last_run_incomplete(self) -> bool:
        """True when the previous run left jobs unscreened, so the next one must re-fetch the full window."""
        row = self.db.execute("SELECT deferred FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
        return bool(row and row[0])

    def upsert(self, job: Job, today: str) -> None:
        payload = json.dumps(asdict(job), default=str)
        self.db.execute(
            """INSERT INTO jobs (id, url, fingerprint, company_key, title, first_fetched, last_fetched, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET last_fetched = excluded.last_fetched, payload = excluded.payload""",
            (job.id, job.url, fingerprint(job.company_key, job.title), job.company_key, job.title, today, today, payload),
        )

    def already_reported(self, job: Job) -> str | None:
        """Which identity matched a previously reported job, if any."""
        row = self.db.execute(
            "SELECT id, url, fingerprint, reported_on FROM reported WHERE id = ? OR url = ? OR fingerprint = ? LIMIT 1",
            (job.id, job.url, fingerprint(job.company_key, job.title)),
        ).fetchone()
        if not row:
            return None
        how = "same id" if row[0] == job.id else "same link" if row[1] == job.url else "same company+title"
        return f"already reported on {row[3]} ({how})"

    def reported_best(self, window_days: int) -> dict[tuple[str, str], tuple[int, str]]:
        since = (date.today() - timedelta(days=window_days)).isoformat()
        best: dict[tuple[str, str], tuple[int, str]] = {}
        for company_key, family, rank, title in self.db.execute(
            "SELECT company_key, family, rank, title FROM reported WHERE reported_on >= ?", (since,)
        ):
            key = (company_key, family)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, title)
        return best

    def mark_reported(self, job: Job, today: str) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO reported (id, url, fingerprint, company_key, family, rank, title, reported_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (job.id, job.url, fingerprint(job.company_key, job.title), job.company_key, job.family, job.rank, job.title, today),
        )

    def log_run(self, run_at: str, fetched: int, passed: int, new: int, reported: int, report: str,
                deferred: int) -> None:
        self.db.execute(
            "INSERT INTO runs (run_at, fetched, passed, new, reported, report, deferred) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_at, fetched, passed, new, reported, report, deferred),
        )

    def commit(self) -> None:
        self.db.commit()
