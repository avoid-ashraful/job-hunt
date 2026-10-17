"""Employer board adapters and watchlist rules without network calls."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml
import requests

from scanner.filters import Filters
from scanner.models import Job
from scanner.rules import LocationRules
from scanner.sources import ats
from scanner import watchlist
from scanner.cli import _hq_lookup

CONFIG = yaml.safe_load(Path("config.example.yml").read_text())
ATS_CFG = {**CONFIG["sources"]["ats"], "filters": CONFIG["filters"]}


def _entry(kind: str) -> dict:
    return {"company": "Example", "ats": kind, "board": "example", "hq_country": "us"}


def test_ashby_adapter(monkeypatch):
    monkeypatch.setattr(ats, "get", lambda *a, **k: {"jobs": [{
        "id": "internal", "title": "SDK Engineer - Python", "jobUrl": "https://jobs.ashbyhq.com/example/uuid",
        "location": "Remote, Global", "isRemote": True, "descriptionHtml": "<p>Python &amp; Django</p>",
        "publishedAt": "2026-10-16T12:00:00Z", "compensation": {"summaryComponents": [{
            "compensationType": "Salary", "minValue": 150000, "maxValue": 190000,
            "currencyCode": "USD", "interval": "1 YEAR"}]}}]})
    jobs = ats.fetch_board(_entry("ashby"), ATS_CFG)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "ashby:example:uuid"
    assert job.description == "Python & Django"
    assert job.regions == ["global"] and job.watchlist
    assert job.salary_max == 190000 and job.salary_period == "year"


def test_greenhouse_adapter(monkeypatch):
    monkeypatch.setattr(ats, "get", lambda *a, **k: {"jobs": [{
        "id": 123, "title": "Backend Engineer", "absolute_url": "https://job-boards.greenhouse.io/example/jobs/123",
        "location": {"name": "Remote - APAC"}, "content": "<p>Python APIs</p>",
        "updated_at": "2026-10-10T00:00:00Z"}]})
    jobs = ats.fetch_board(_entry("greenhouse"), ATS_CFG)
    assert len(jobs) == 1
    assert jobs[0].id == "greenhouse:example:123"
    assert jobs[0].regions == ["apac"]
    assert jobs[0].description == "Python APIs"


def test_lever_and_eu_adapter(monkeypatch):
    monkeypatch.setattr(ats, "get", lambda *a, **k: [{
        "id": "u1", "text": "Backend Engineer", "title": "Backend Engineer",
        "hostedUrl": "https://jobs.lever.co/example/u1", "categories": {"location": "Remote, Worldwide"},
        "descriptionPlain": "Build Python services", "createdAt": 1792108800000,
        "salaryRange": {"min": 120000, "max": 160000, "currency": "USD", "interval": "year"}}])
    for kind in ("lever", "lever_eu"):
        jobs = ats.fetch_board(_entry(kind), ATS_CFG)
        assert len(jobs) == 1
        assert jobs[0].id == "lever:example:u1"
        assert jobs[0].regions == ["global"]
        assert jobs[0].salary_min == 120000


def test_workable_adapter_skips_detail_for_wrong_title(monkeypatch):
    calls = []
    def fake_get(session, url, **kwargs):
        calls.append(url)
        if url.endswith("/example"):
            return {"jobs": [
                {"shortcode": "bad", "title": "Marketing Manager", "telecommuting": True},
                {"shortcode": "good", "title": "Backend Engineer", "telecommuting": True,
                 "description": "<p>Python backend</p>"},
            ]}
        return {"description": "<p>Python backend</p>"}
    monkeypatch.setattr(ats, "get", fake_get)
    jobs = ats.fetch_board(_entry("workable"), ATS_CFG)
    assert len(jobs) == 1 and jobs[0].id == "workable:example:good"
    assert len(calls) == 1


def test_workable_falls_back_when_widget_is_limited(monkeypatch):
    def limited(*args, **kwargs):
        raise requests.HTTPError("429")
    monkeypatch.setattr(ats, "get", limited)
    monkeypatch.setattr(ats, "workable_markdown", lambda *a: [{
        "shortcode": "good", "title": "Backend Engineer", "location": "Remote, Global",
        "description": "Python services", "published_on": "2026-10-16",
        "url": "https://apply.workable.com/example/jobs/view/good"}])
    jobs = ats.fetch_board(_entry("workable"), ATS_CFG)
    assert len(jobs) == 1 and jobs[0].id == "workable:example:good"
    assert jobs[0].posted_at.tzinfo == timezone.utc


def test_workable_markdown_feed_parser(monkeypatch):
    monkeypatch.setattr(ats, "_pace", lambda url: None)
    table = ("| Title | Department | Location | Type | Salary | Posted | Details |\n"
             "|---|---|---|---|---|---|---|\n"
             "| Backend Engineer | Eng | Remote, Global | Full-time | — | 2026-10-16 | "
             "[View](https://apply.workable.com/example/jobs/view/ABC123.md) |")
    class Session:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200, text=table, raise_for_status=lambda: None)
    rows = ats.workable_markdown(Session(), "example")
    assert rows[0]["shortcode"] == "ABC123"
    assert rows[0]["_markdown_detail"].endswith("ABC123.md")


def test_smartrecruiters_adapter_skips_detail_for_wrong_title(monkeypatch):
    calls = []
    def fake_get(session, url, **kwargs):
        calls.append(url)
        if url.endswith("/postings"):
            return {"content": [
                {"id": "bad", "name": "Marketing Manager", "location": {"remote": True, "fullLocation": "Remote"}},
                {"id": "good", "name": "Software Engineer", "location": {"remote": True, "fullLocation": "Remote, Global"}},
            ], "totalFound": 2}
        return {"jobAd": {"sections": {"jobDescription": {"text": "<p>Python services</p>"}}}}
    monkeypatch.setattr(ats, "get", fake_get)
    jobs = ats.fetch_board(_entry("smartrecruiters"), ATS_CFG)
    assert len(jobs) == 1 and jobs[0].id == "smartrecruiters:example:good"
    assert len(calls) == 2 and "bad" not in calls[1]


def test_resolver_prefers_careers_link(monkeypatch):
    class Session:
        headers = {}
        def get(self, url, **kwargs):
            return SimpleNamespace(url=url, text='<a href="https://jobs.ashbyhq.com/realboard">Jobs</a>',
                                   raise_for_status=lambda: None)
    calls = []
    def valid(session, ats_name, board):
        calls.append((ats_name, board))
        return (ats_name, board) == ("ashby", "realboard")
    monkeypatch.setattr(watchlist, "_valid_board", valid)
    result = watchlist.resolve_company({"company": "Example", "careers_url": "https://example.com/careers"}, Session())
    assert result == {"ats": "ashby", "board": "realboard", "reason": "careers link"}
    assert calls == [("ashby", "realboard")]


def test_resolver_reports_rate_limit(monkeypatch):
    class Session:
        headers = {}
    monkeypatch.setattr(watchlist, "_valid_board",
                        lambda session, ats_name, board: None if ats_name == "ashby" else False)
    result = watchlist.resolve_company({"company": "Example"}, Session())
    assert result["reason"] == "rate limited while probing a board"


def test_build_cache_and_hq_exclusion(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "one.md").write_text('---\ntitle: "Example"\nwebsite: https://example.com\nregion: worldwide\n---\n')
    (vendor / "two.md").write_text('---\ntitle: "Excluded"\nregion: worldwide\nhq_country: in\n---\n')
    called = []
    def resolve(seed, hints=None):
        called.append(seed["company"])
        return {"ats": "ashby", "board": "example", "reason": "verified slug", "owner_check": watchlist.OWNER_CHECK}
    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    path = tmp_path / "watch.yml"
    cache = tmp_path / "cache"
    first = watchlist.build(vendor, path, cache)
    second = watchlist.build(vendor, path, cache)
    assert first["resolved"] == 1 and first["excluded_hq"] == 1
    assert second["resolved"] == 1 and called == ["Example"]
    assert len(watchlist.list_entries(path)) == 1


def test_build_uses_database_board_hint(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "one.md").write_text('---\ntitle: "Example"\nregion: worldwide\n---\n')
    db_path = tmp_path / "jobs.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE jobs(company_key TEXT, id TEXT)")
    db.execute("INSERT INTO jobs VALUES ('example', 'greenhouse:unusualboard:123')")
    db.commit()
    db.close()
    def resolve(seed, hints=None):
        assert hints == [("greenhouse", "unusualboard")]
        return {"ats": "greenhouse", "board": "unusualboard", "reason": "database hint"}
    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    stats = watchlist.build(vendor, tmp_path / "watch.yml", tmp_path / "cache", db_path=db_path)
    assert stats["database_hint"] == 1


def test_watchlist_age_and_global_location():
    now = datetime.now(timezone.utc)
    def job(watched):
        return Job(id="ashby:example:id", source="ats", url="https://x", title="Python Engineer",
                   company="Example", company_key="example", description="Build Python APIs.",
                   location="Remote, Global", posted_at=now - timedelta(days=45), watchlist=watched)
    filters = Filters(CONFIG["filters"], CONFIG["dedup"]["seniority_rank"], now)
    assert filters.evaluate(job(False)).startswith("older than 21 days")
    watched = job(True)
    assert filters.evaluate(watched) is None
    verdict = LocationRules(CONFIG["rules"]["location"]).classify(watched)
    assert verdict.outcome == "positive"
    assert verdict.rule == "positive.ats_location_global"
    assert verdict.evidence == "Remote, Global"
    assert ats._location_tags("Remote, Singapore", ATS_CFG) == (["apac"], ["sg"])


def test_grow_uses_evidence_and_ats_ids(tmp_path):
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE jobs(id TEXT, payload TEXT); CREATE TABLE decisions(job_id TEXT, outcome TEXT, stage TEXT); CREATE TABLE feedback(job_id TEXT, label TEXT);")
    payload = {"company": "Example", "source": "freehire", "regions": ["global"]}
    db.execute("INSERT INTO jobs VALUES (?, ?)", ("greenhouse:example:123", json.dumps(payload)))
    untagged = {"company": "Other", "source": "freehire", "regions": ["north_america"]}
    db.execute("INSERT INTO jobs VALUES (?, ?)", ("greenhouse:other:9", json.dumps(untagged)))
    result = watchlist.grow(db, tmp_path / "watch.yml")
    assert result["added"] == 1  # a freehire job without global evidence doesn't make its company watched
    assert watchlist.list_entries(tmp_path / "watch.yml")[0]["board"] == "example"


def test_hq_lookup_reads_profile_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE company_profiles(company_key TEXT, hq_country TEXT)")
    db.execute("INSERT INTO company_profiles VALUES ('example', 'in')")
    db.commit()
    db.close()
    monkeypatch.setitem(sys.modules, "scanner.company_profile",
                        SimpleNamespace(hq_country=lambda job, cfg: ("us", "fallback")))
    lookup = _hq_lookup(CONFIG, db_path)
    assert lookup("Example") == "in"
    assert lookup("Other") == "us"


def test_first_source_copy_wins_and_borrows_missing_enrichment():
    from scanner.cli import _fill_missing

    live = Job(id="ashby:supabase:1", source="ats", url="https://jobs.ashbyhq.com/supabase/1", title="SDK Engineer - Python",
               company="Supabase", company_key="supabase", description="live text", location="Remote, Global",
               regions=["global"], watchlist=True)
    crawled = Job(id="ashby:supabase:1", source="freehire", url="https://jobs.ashbyhq.com/supabase/1", title="SDK Engineer",
                  company="Supabase", company_key="supabase", description="old text", regions=["north_america"],
                  seniority="senior", salary_max=200000.0, salary_currency="USD")
    _fill_missing(live, crawled)
    assert (live.description, live.regions, live.seniority, live.salary_max) == ("live text", ["global"], "senior", 200000.0)


def test_untagged_crypto_posting_is_dropped_from_its_text():
    cfg = yaml.safe_load(Path("config.example.yml").read_text())
    f = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], datetime.now(timezone.utc))
    text = "Python platform for our crypto wallet. Blockchain, on-chain tokens and stablecoins. " * 2
    job = Job(id="ashby:tw:1", source="ats", url="u", title="Senior Platform Engineer", company="TW",
              company_key="tw", description=text, watchlist=True)
    assert "crypto" in f.evaluate(job)
    tagged = Job(id="ashby:tw:2", source="ats", url="u", title="Senior Platform Engineer", company="TW",
                 company_key="tw", description=text, domains=["fintech"], watchlist=True)
    assert f.evaluate(tagged) is None  # source tags win over word counts
