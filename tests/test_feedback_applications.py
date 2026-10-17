"""Editing daily marks and tracking applications must survive repeated scans."""

from datetime import date, datetime, timedelta, timezone

from scanner.applications import Applications
from scanner.ghost import GhostRules
from scanner.marks import parse, sync
from scanner.models import Job
from scanner.report import write_report
from scanner.store import Store, job_ref


def job(id="greenhouse:acme:1", company="Acme"):
    return Job(id=id, source="freehire", url=f"https://example.com/{id}", title="Senior Python Engineer",
               company=company, company_key=company.lower(), description="Python Django AWS " * 40)


def setup(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    first = job()
    store.upsert(first, "2026-01-01")
    store.mark_reported(first, "2026-01-01")
    store.log_decision("2026-01-01", first.id, "report", "positive.worldwide", "apply")
    store.commit()
    return store, first, job_ref(first.id)


def report(path, ref, mark):
    path.write_text(f"# New jobs\n\n| Ref | Mark | Title |\n|---|---|---|\n| `{ref}` | {mark} | Job |\n")


def test_marks_notes_edits_idempotency_and_malformed(tmp_path):
    store, _, ref = setup(tmp_path)
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    path = jobs / "2026-01-01.md"
    report(path, ref, "[x US only]")
    assert parse(path)[ref] == ("not-eligible", "US only")
    assert sync(store, jobs) == (1, 1)
    assert sync(store, jobs) == (1, 1)
    assert store.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    report(path, ref, "[a applied via referral]")
    sync(store, jobs)
    assert store.db.execute("SELECT label,note FROM feedback").fetchone() == ("applied", "applied via referral")
    assert store.db.execute("SELECT status FROM applications").fetchone()[0] == "applied"
    report(path, ref, "[q unsupported]")
    assert parse(path) == {}
    sync(store, jobs)
    assert store.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    report(path, ref, "[ ]")
    sync(store, jobs)
    assert store.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_applications_status_history_and_ghosting(tmp_path):
    store, first, ref = setup(tmp_path)
    apps = Applications(store)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert apps.set_status(ref, "applied", "sent", at)
    assert apps.set_status(ref, "applied", "sent again", at)
    assert apps.ghost_overdue(21, date(2026, 1, 21)) == 0
    assert apps.ghost_overdue(21, date(2026, 1, 22)) == 1
    assert apps.ghost_overdue(21, date(2026, 1, 23)) == 0
    assert apps.silent_count(first) == 1
    apps.set_status(ref, "interview", "replied", at + timedelta(days=22))
    assert apps.silent_count(first) == 0
    raw = store.db.execute("SELECT status_history FROM applications WHERE ref=?", (ref,)).fetchone()[0]
    import json
    assert [h["status"] for h in json.loads(raw)] == ["applied", "ghosted", "interview"]
    assert "response rate: 1/1" in apps.summary(date(2026, 1, 25))


def test_report_layout_and_ghost_signal(tmp_path):
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    store, first, ref = setup(tmp_path)
    first.bucket = "apply"
    first.screen = {"fit": 5, "ghost": (0, [])}
    path = tmp_path / "jobs.md"
    write_report(path, datetime(2026, 1, 1), [first], {"fetched": 1, "passed": 1, "ambiguous": 0, "llm_calls": 0},
                 __import__("collections").Counter(), "Bangladesh", 3)
    assert "## Start here" in path.read_text()
    assert "| Mark |" in path.read_text()
    assert f"| `{ref}` | [ ] |" in path.read_text()
    ghost_cfg = cfg["rules"]["ghost"]
    ghost_cfg["silent_applications_min"] = 2
    ghosts = GhostRules(ghost_cfg, lambda j: (0, 1), lambda j: 2)
    score, signals = ghosts.score(first)
    assert "silent_applications (2x)" in signals
    assert score >= ghost_cfg["weights"]["silent_applications"]


def test_start_here_and_detail_marks_share_one_ref(tmp_path):
    store, _, ref = setup(tmp_path)
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    path = jobs / "2026-01-01.md"
    path.write_text(f"""# Jobs
| Ref | Mark | Job |
|---|---|---|
| `{ref}` | [a sent today] | Acme |

| Ref | Mark | Title |
|---|---|---|
| `{ref}` | [ ] | Senior Python Engineer |
""")
    assert parse(path)[ref] == ("applied", "sent today")
    sync(store, jobs)
    assert store.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1


def test_learning_counts_cli_applications_and_changed_mark_date(tmp_path):
    from scanner.learning_outcomes import section

    store, _, ref = setup(tmp_path)
    apps = Applications(store)
    apps.set_status(ref, "applied", at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    lines, _ = section(store.db, "2026-01-01")
    assert any("positive.worldwide | 1 | 0 | 0" in line for line in lines)
    apps.feedback(ref, "good", "", datetime(2026, 1, 2, tzinfo=timezone.utc))
    apps.feedback(ref, "not-eligible", "US only", datetime(2026, 1, 4, tzinfo=timezone.utc))
    apps.feedback(ref, "not-eligible", "US only", datetime(2026, 1, 5, tzinfo=timezone.utc))
    assert store.db.execute("SELECT created_at FROM feedback WHERE ref=?", (ref,)).fetchone()[0].startswith("2026-01-04")
    _, suggestions = section(store.db, "2026-01-03")
    assert any("not-eligible" in item for item in suggestions)
