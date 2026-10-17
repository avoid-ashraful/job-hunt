"""Track applications and outcomes so job choices can improve future scans."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

STATUSES = {"applied", "interview", "rejected", "offer", "ghosted", "withdrawn"}
ACTIVE = {"applied", "interview", "offer"}


class Applications:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS applications (
            ref TEXT PRIMARY KEY, job_id TEXT NOT NULL, company TEXT NOT NULL,
            title TEXT NOT NULL, url TEXT NOT NULL, applied_date TEXT NOT NULL,
            status TEXT NOT NULL, status_history TEXT NOT NULL,
            company_key TEXT NOT NULL, source TEXT NOT NULL,
            decision_rule TEXT NOT NULL, bucket TEXT NOT NULL)""")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(feedback)")}
        if "source" not in cols:
            self.db.execute("ALTER TABLE feedback ADD COLUMN source TEXT NOT NULL DEFAULT 'cli'")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS feedback_report_ref ON feedback(ref) WHERE source = 'report'")

    def job(self, ref: str):
        row = self.db.execute("""SELECT r.id, r.title, r.url, r.company_key, j.payload,
               COALESCE((SELECT rule FROM decisions d WHERE d.job_id=r.id AND d.stage='report'
                         ORDER BY d.run_date DESC LIMIT 1), ''),
               COALESCE((SELECT outcome FROM decisions d WHERE d.job_id=r.id AND d.stage='report'
                         ORDER BY d.run_date DESC LIMIT 1), '')
               FROM reported r LEFT JOIN jobs j ON j.id=r.id WHERE r.ref=?
               ORDER BY r.reported_on DESC LIMIT 1""", (ref,)).fetchone()
        if not row:
            return None
        job_id, title, url, company_key, payload, rule, bucket = row
        data = json.loads(payload) if payload else {}
        return job_id, data.get("company") or company_key, title, url, company_key, data.get("source") or "unknown", rule, bucket

    def feedback(self, ref: str, label: str, note: str, at: datetime | None = None) -> bool:
        job = self.job(ref)
        if not job:
            return False
        stamp = (at or datetime.now(timezone.utc)).isoformat()
        self.db.execute("""INSERT INTO feedback (created_at, ref, job_id, label, note, source)
            VALUES (?, ?, ?, ?, ?, 'report') ON CONFLICT(ref) WHERE source='report'
            DO UPDATE SET label=excluded.label, note=excluded.note, job_id=excluded.job_id,
            created_at=CASE WHEN feedback.label != excluded.label OR COALESCE(feedback.note, '') != excluded.note
                            THEN excluded.created_at ELSE feedback.created_at END""",
            (stamp, ref, job[0], label, note))
        if label == "applied" and not self.db.execute("SELECT 1 FROM applications WHERE ref=?", (ref,)).fetchone():
            self.set_status(ref, "applied", note, at=at)
        return True

    def clear_report_feedback(self, ref: str) -> None:
        self.db.execute("DELETE FROM feedback WHERE ref=? AND source='report'", (ref,))

    def set_status(self, ref: str, status: str, note: str = "", at: datetime | None = None) -> bool:
        if status not in STATUSES:
            raise ValueError(f"unknown application status: {status}")
        job = self.job(ref)
        if not job:
            return False
        stamp = (at or datetime.now(timezone.utc)).isoformat()
        row = self.db.execute("SELECT status, status_history FROM applications WHERE ref=?", (ref,)).fetchone()
        if row:
            old_status, raw_history = row
            if old_status == status:
                return True
            history = json.loads(raw_history)
            history.append({"at": stamp, "status": status, "note": note})
            self.db.execute("UPDATE applications SET status=?, status_history=? WHERE ref=?",
                            (status, json.dumps(history), ref))
        else:
            if status != "applied":
                raise ValueError("record an application before changing its status")
            history = [{"at": stamp, "status": status, "note": note}]
            job_id, company, title, url, company_key, source, rule, bucket = job
            self.db.execute("INSERT INTO applications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (ref, job_id, company, title, url, stamp[:10], status,
                             json.dumps(history), company_key, source, rule, bucket))
        return True

    def ghost_overdue(self, days: int, today: date | None = None) -> int:
        today = today or date.today()
        changed = 0
        for ref, history_raw in self.db.execute("SELECT ref, status_history FROM applications WHERE status IN ('applied','interview')"):
            history = json.loads(history_raw)
            last = date.fromisoformat(history[-1]["at"][:10])
            if today - last >= timedelta(days=days):
                self.set_status(ref, "ghosted", f"No response after {days} days",
                                datetime.combine(today, datetime.min.time(), timezone.utc))
                changed += 1
        return changed

    def silent_count(self, job) -> int:
        return self.db.execute("SELECT COUNT(*) FROM applications WHERE company_key=? AND status='ghosted'",
                               (job.company_key,)).fetchone()[0]

    def summary(self, today: date | None = None) -> str:
        today = today or date.today()
        rows = self.db.execute("SELECT ref, company, title, applied_date, status FROM applications ORDER BY applied_date DESC").fetchall()
        responded = sum(status in {"interview", "rejected", "offer"} for *_, status in rows)
        lines = [f"Applications: {len(rows)}; active: {sum(s in ACTIVE for *_, s in rows)}; "
                 f"response rate: {responded}/{len(rows)} ({responded / len(rows):.0%})" if rows
                 else "Applications: 0; active: 0; response rate: 0/0"]
        for ref, company, title, applied, status in rows:
            if status in ACTIVE:
                lines.append(f"{ref}  {company} — {title}  {status}  {(today - date.fromisoformat(applied)).days} days since applying")
        return "\n".join(lines)
