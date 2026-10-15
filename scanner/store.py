"""SQLite state: jobs fetched and reported, plus the self-learning logs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from scanner.dedup import fingerprint
from scanner.models import Job

def job_ref(job_id: str) -> str:
    """Short stable id shown in reports, used for feedback."""
    return hashlib.sha1(job_id.encode()).hexdigest()[:6]


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
    status TEXT NOT NULL DEFAULT 'new',
    ref TEXT
);
CREATE INDEX IF NOT EXISTS reported_url ON reported(url);
CREATE INDEX IF NOT EXISTS reported_fp ON reported(fingerprint);
-- self-learning logs: every decision, every LLM call, every spot-check, every piece of feedback
CREATE TABLE IF NOT EXISTS decisions (
    run_date TEXT NOT NULL,
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,       -- filter / dedup / role / location / llm / collapse / report
    rule TEXT NOT NULL,        -- which rule or model decided
    outcome TEXT NOT NULL,     -- drop / apply / check / ambiguous
    detail TEXT,
    PRIMARY KEY (run_date, job_id, stage)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    run_date TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,     -- screen / audit
    jobs INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    seconds REAL NOT NULL,
    chars INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audits (
    run_date TEXT NOT NULL,
    job_id TEXT NOT NULL,
    rule TEXT NOT NULL,
    rule_outcome TEXT NOT NULL,
    llm_eligibility TEXT NOT NULL,
    agree INTEGER,             -- 1 agree, 0 disagree, NULL llm unsure
    rule_evidence TEXT,
    llm_evidence TEXT,
    PRIMARY KEY (run_date, job_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    created_at TEXT NOT NULL,
    ref TEXT NOT NULL,
    job_id TEXT,
    label TEXT NOT NULL,
    note TEXT
);
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
        self.dry_run = False  # dry runs don't write decisions/audits, so they can't skew `learn`
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
        if "deferred" not in cols:
            self.db.execute("ALTER TABLE runs ADD COLUMN deferred INTEGER NOT NULL DEFAULT 0")
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(reported)")}
        if "ref" not in cols:
            self.db.execute("ALTER TABLE reported ADD COLUMN ref TEXT")

    def is_empty(self) -> bool:
        """No completed (non-dry) run yet, so the next run should backfill."""
        return self.db.execute("SELECT 1 FROM runs LIMIT 1").fetchone() is None

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
            """INSERT OR IGNORE INTO reported (id, url, fingerprint, company_key, family, rank, title, reported_on, ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job.id, job.url, fingerprint(job.company_key, job.title), job.company_key, job.family, job.rank,
             job.title, today, job_ref(job.id)),
        )

    def log_run(self, run_at: str, fetched: int, passed: int, new: int, reported: int, report: str,
                deferred: int) -> None:
        self.db.execute(
            "INSERT INTO runs (run_at, fetched, passed, new, reported, report, deferred) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_at, fetched, passed, new, reported, report, deferred),
        )

    def log_decision(self, run_date: str, job_id: str, stage: str, rule: str, outcome: str, detail: str = "") -> None:
        if self.dry_run:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
            (run_date, job_id, stage, rule, outcome, detail[:500]),
        )

    def log_llm_call(self, run_date: str, model: str, purpose: str, jobs: int, ok: bool, seconds: float, chars: int) -> None:
        self.db.execute("INSERT INTO llm_calls VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (run_date, model, purpose, jobs, int(ok), round(seconds, 1), chars))

    def log_audit(self, run_date: str, job_id: str, rule: str, rule_outcome: str, llm_eligibility: str,
                  agree: bool | None, rule_evidence: str, llm_evidence: str) -> None:
        if self.dry_run:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO audits VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_date, job_id, rule, rule_outcome, llm_eligibility, None if agree is None else int(agree),
             rule_evidence[:500], llm_evidence[:500]),
        )

    def find_ref(self, ref: str) -> tuple[str, str, str] | None:
        """(job_id, title, url) for a report Ref."""
        return self.db.execute(
            "SELECT id, title, url FROM reported WHERE ref = ? ORDER BY reported_on DESC LIMIT 1", (ref,)
        ).fetchone()

    def add_feedback(self, created_at: str, ref: str, job_id: str | None, label: str, note: str) -> None:
        self.db.execute("INSERT INTO feedback VALUES (?, ?, ?, ?, ?)", (created_at, ref, job_id, label, note))

    def commit(self) -> None:
        self.db.commit()
