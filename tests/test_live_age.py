from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
import yaml

from scanner import cli, liveness
from scanner.filters import Filters
from scanner.dedup import merge_jobs
from scanner.models import Job


@pytest.mark.parametrize("ats,url", [
    ("greenhouse", "https://boards.greenhouse.io/acme/jobs/123"),
    ("lever", "https://jobs.lever.co/acme/123"),
    ("ashby", "https://jobs.ashbyhq.com/acme/123"),
    ("workable", "https://apply.workable.com/acme/j/123"),
    ("smartrecruiters", "https://jobs.smartrecruiters.com/acme/123"),
])
def test_ats_url_and_id_use_the_same_live_check(monkeypatch, ats, url):
    job = Job(id=f"{ats}:acme:123", source="freehire", url=url, title="Python Engineer",
              company="Acme", company_key="acme", description="Python")
    calls = []
    monkeypatch.setattr(liveness, "_status", lambda url: calls.append(url) or liveness.OPEN)
    monkeypatch.setattr(liveness, "_ashby_board", lambda board: calls.append(board) or {"123"})
    assert liveness.can_verify(job)
    assert liveness.check(job) == liveness.OPEN
    job.id = "freehire:123"
    assert liveness.can_verify(job)
    assert liveness.check(job) == liveness.OPEN
    assert calls[0] == calls[1]


def test_workable_url_uses_actual_board_slug_for_liveness(monkeypatch):
    job = Job(id="freehire:123", source="freehire",
              url="https://apply.workable.com/acme-labs-2/j/ABC123/", title="Python Engineer",
              company="Acme Labs", company_key="acme-labs", description="Python")
    actual = "https://apply.workable.com/api/v2/accounts/acme-labs-2/jobs/ABC123"
    monkeypatch.setattr(liveness, "_status", lambda url: liveness.OPEN if url == actual else liveness.CLOSED)
    assert liveness.can_verify(job)
    assert liveness.check(job) == liveness.OPEN


def test_fresh_cross_source_copy_prevents_a_stale_source_age_drop():
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    stale = Job(id="freehire:123", source="freehire", url="https://jobs.lever.co/acme/123",
                title="Python Engineer", company="Acme", company_key="acme", description="Python",
                first_seen_at=now - timedelta(days=2), last_seen_at=now - timedelta(days=11))
    fresh = replace(stale, id="lever:acme:123", source="hn", last_seen_at=now)
    merged = list(merge_jobs([stale, fresh], cli._fill_missing).values())
    assert len(merged) == 1
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], now)
    assert filters._age(merged[0]) is None
    assert merged[0].last_seen_at == now


@pytest.mark.parametrize("age,status,ats,watched,enabled,ghost,expected", [
    (30, "open", True, False, True, False, None),
    (30, "unknown", True, False, True, False, "older than 21 days and not confirmed open"),
    (30, "error", True, False, True, False, "older than 21 days and not confirmed open"),
    (30, "closed", True, False, True, False, "older than 21 days and not confirmed open"),
    (30, "open", False, False, True, False, "older than 21 days (first seen"),
    (50, "open", True, False, True, False, "older than 21 days (first seen"),
    (30, "unknown", True, True, True, False, None),
    (50, "unknown", True, True, True, False, None),
    (121, "open", True, True, True, False, "older than 120 days (first seen"),
    (2, "unknown", True, False, True, False, None),
    (2, "closed", True, False, True, False, "closed: the posting is gone"),
    (30, "open", True, False, False, False, "older than 21 days and not confirmed open"),
    (30, "open", True, False, True, True, "ghost job"),
])
def test_live_age_pipeline(tmp_path, monkeypatch, age, status, ats, watched, enabled, ghost, expected):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["sources"] = {"freehire": {"first_run_days": 60}}
    cfg["output"] = {"jobs_dir": "jobs", "state_dir": "state"}
    cfg["liveness"]["enabled"] = enabled
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    job = Job(id="greenhouse:acme:123" if ats else "freehire:123", source="freehire",
              url="https://boards.greenhouse.io/acme/jobs/123" if ats else "https://example.com/job",
              title="Senior Backend Python Engineer", company="Acme", company_key="acme",
              description="Build Python and Django services for customers around the world. " * 10,
              first_seen_at=datetime.now(timezone.utc) - timedelta(days=age), watchlist=watched,
              reality={"class": "likely-evergreen"} if ghost else {})
    monkeypatch.setattr(cli.sources, "fetch_all", lambda *args: iter([job]))
    checked = []

    def check_all(jobs, workers):
        checked.extend(jobs)
        return {j.id: status for j in jobs}

    monkeypatch.setattr(liveness, "check_all", check_all)
    cli.run(config, dry_run=False, days=60, skip_llm=True)
    db = sqlite3.connect(tmp_path / "state" / "scanner.sqlite")
    decisions = db.execute("SELECT stage, detail FROM decisions WHERE outcome='drop'").fetchall()
    if expected:
        assert len(decisions) == 1
        assert decisions[0][1].startswith(expected)
        assert not db.execute("SELECT 1 FROM decisions WHERE stage='report'").fetchall()
    else:
        assert decisions == []
        assert db.execute("SELECT 1 FROM decisions WHERE stage='report'").fetchone()
    if expected and ("first seen" in expected or ghost):
        assert checked == []


def test_live_age_default_and_configured_cap():
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["filters"].pop("max_age_days_live")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    job = Job(id="greenhouse:acme:123", source="freehire", url="https://example.com/job",
              title="Python Engineer", company="Acme", company_key="acme", description="Python",
              posted_at=now, first_seen_at=now - timedelta(days=30))
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], now)
    assert filters.evaluate(job) is None
    assert job.screen["needs_live"] is True
    cfg["filters"]["max_age_days_live"] = 30
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], now)
    assert filters.evaluate(job).startswith("older than 21 days")
    assert "needs_live" not in job.screen
