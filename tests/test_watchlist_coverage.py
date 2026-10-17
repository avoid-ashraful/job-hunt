"""Tests for expanded ATS adapters, resolver discoveries, and watchlist quality."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
import yaml
import requests

from scanner.sources import ats
from scanner import watchlist

CONFIG = yaml.safe_load(Path("config.example.yml").read_text())
ATS_CFG = {**CONFIG["sources"]["ats"], "filters": CONFIG["filters"]}


def _entry(kind: str) -> dict:
    return {"company": "Example", "ats": kind, "board": "example", "hq_country": "us"}


def test_recruitee_adapter(monkeypatch):
    sample = {
        "offers": [
            {
                "id": 101,
                "title": "Senior Python Backend Engineer",
                "careers_url": "https://example.recruitee.com/o/senior-python-backend-engineer",
                "location": "Remote, Global",
                "remote": True,
                "description": "<p>Build scalable Python services.</p>",
                "requirements": "<p>Strong Django/FastAPI skills.</p>",
                "published_at": "2026-10-15T10:00:00.000Z",
                "salary": {"min": 120000, "max": 160000, "currency": "USD", "period": "year"},
            },
            {
                "id": 102,
                "title": "(Sample) Product Manager",
                "remote": True,
            },
        ]
    }
    monkeypatch.setattr(ats, "get", lambda *a, **k: sample)
    jobs = ats.fetch_board(_entry("recruitee"), ATS_CFG)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "recruitee:example:101"
    assert "Django/FastAPI" in job.description
    assert job.salary_min == 120000 and job.salary_max == 160000
    assert job.regions == ["global"]
    assert job.url == "https://example.recruitee.com/o/senior-python-backend-engineer"


def test_breezy_adapter(monkeypatch):
    sample = [
        {
            "id": "b1",
            "name": "Senior Cloud Backend Engineer (Python)",
            "url": "https://example.breezy.hr/p/b1-senior-cloud-backend-engineer",
            "published_date": "2026-10-14T08:30:00Z",
            "location": {
                "name": "Worldwide",
                "is_remote": True,
            },
            "salary": {
                "min": 130000,
                "max": 170000,
                "currency": "USD",
                "interval": "year",
            },
            "description": "<p>AWS, Terraform, Python backend.</p>",
        }
    ]
    monkeypatch.setattr(ats, "get", lambda *a, **k: sample)
    jobs = ats.fetch_board(_entry("breezy"), ATS_CFG)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "breezy:example:b1"
    assert job.salary_min == 130000
    assert "Worldwide" in job.location
    assert job.regions == ["global"]


def test_personio_adapter(monkeypatch):
    xml_sample = """
    <workzag-jobs>
      <position>
        <id>501</id>
        <name><![CDATA[Senior Software Engineer (Python)]]></name>
        <office>Munich</office>
        <additionalOffices>
          <office>Remote</office>
        </additionalOffices>
        <createdAt>2026-10-12T14:00:00+02:00</createdAt>
        <jobDescriptions>
          <jobDescription>
            <name>Role</name>
            <value><![CDATA[<p>Design modern microservices.</p>]]></value>
          </jobDescription>
        </jobDescriptions>
      </position>
    </workzag-jobs>
    """
    monkeypatch.setattr(ats, "get_text", lambda *a, **k: xml_sample)
    jobs = ats.fetch_board(_entry("personio"), ATS_CFG)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "personio:example:501"
    assert "Remote" in job.location
    assert "microservices" in job.description
    assert job.url == "https://example.jobs.personio.de/job/501"


def test_bamboohr_adapter(monkeypatch):
    list_sample = {
        "result": [
            {
                "id": "77",
                "jobOpeningName": "Staff Python Engineer",
                "location": {"city": "New York", "state": "NY"},
                "isRemote": True,
            }
        ]
    }
    detail_sample = {
        "result": {
            "jobOpening": {
                "datePosted": "2026-10-11T12:00:00Z",
                "description": "<p>Django and PostgreSQL architect.</p>",
            }
        }
    }

    def mock_get(session, url, **k):
        if url.endswith("/detail"):
            return detail_sample
        return list_sample

    monkeypatch.setattr(ats, "get", mock_get)
    jobs = ats.fetch_board(_entry("bamboohr"), ATS_CFG)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "bamboohr:example:77"
    assert "Remote" in job.location
    assert "Django and PostgreSQL" in job.description


def test_pinpoint_adapter(monkeypatch):
    sample = {
        "data": [
            {
                "id": "p1",
                "title": "Principal Python Engineer",
                "url": "https://example.pinpointhq.com/en/postings/p1",
                "location": {"name": "Remote, Global"},
                "description": "Develop high throughput backend services.",
            },
            {
                "id": "p2",
                "title": "Head of DEI - UK",
                "url": "https://example.pinpointhq.com/en/postings/p2",
                "location": {"name": "Remote"},
                "description": "<div><figure data-trix-attachment=\"{&quot;content&quot;:&quot;<div>youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw</div>&quot;}\"></figure></div>",
            },
        ]
    }
    monkeypatch.setattr(ats, "get", lambda *a, **k: sample)
    jobs = ats.fetch_board(_entry("pinpoint"), ATS_CFG)
    assert len(jobs) == 1
    assert jobs[0].id == "pinpoint:example:p1"
    assert jobs[0].regions == ["global"]


def test_teamtailor_adapter(monkeypatch):
    rss_sample = """
    <rss version="2.0" xmlns:tt="https://teamtailor.com/">
      <channel>
        <title>Example Jobs</title>
        <item>
          <title><![CDATA[Senior Backend Developer (Python)]]></title>
          <link>https://career.example.com/jobs/99-senior-backend</link>
          <guid>99</guid>
          <pubDate>Thu, 15 Oct 2026 12:00:00 GMT</pubDate>
          <tt:city>London</tt:city>
          <tt:country>UK</tt:country>
          <remoteStatus>fully</remoteStatus>
          <description><![CDATA[<p>FastAPI development.</p>]]></description>
        </item>
      </channel>
    </rss>
    """
    monkeypatch.setattr(ats, "get_text", lambda *a, **k: rss_sample)
    jobs = ats.fetch_board(_entry("teamtailor"), ATS_CFG)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "teamtailor:example:99"
    assert "Remote" in job.location
    assert "FastAPI development." in job.description


def test_embed_detection_and_1hop_crawl(monkeypatch):
    """Test 1-hop link following from homepage to careers page and Greenhouse embed detection."""
    class MockSession:
        headers = {}
        def get(self, url, **kwargs):
            if url == "https://acme.io":
                return SimpleNamespace(url="https://acme.io", text='<a href="/careers">Join our team</a>', raise_for_status=lambda: None)
            if url == "https://acme.io/careers":
                return SimpleNamespace(url="https://acme.io/careers", text='<div id="grnhse_app"><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script></div>', raise_for_status=lambda: None)
            raise requests.HTTPError(f"Unexpected url {url}")

    def mock_valid(session, ats_name, board, **kwargs):
        return (ats_name, board) == ("greenhouse", "acme")

    monkeypatch.setattr(watchlist, "_valid_board", mock_valid)
    result = watchlist.resolve_company({"company": "Acme", "website": "https://acme.io"}, MockSession())
    assert result == {"ats": "greenhouse", "board": "acme", "reason": "careers link"}


def test_slug_collision_rejection():
    """Test candidate boards with different names are rejected on slug collision."""
    # When candidate board name is different from target company
    assert not watchlist.company_match("Random Dental Clinic", "Acme", "https://acme.com", "https://randomdental.com")
    # When candidate board name matches target company
    assert watchlist.company_match("Acme Corp", "Acme", "https://acme.com", "https://acme.com")
    assert watchlist.company_match("Canonical Group Ltd", "Canonical")


def test_transient_failure_retry_and_dead_marking(tmp_path, monkeypatch):
    """Test transient errors retry after cooldown and are marked dead after max attempts."""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "flaky.md").write_text('---\ntitle: "FlakyCo"\nwebsite: https://flaky.com\nregion: worldwide\n---\n')

    cache = tmp_path / "cache"
    cache.mkdir()
    watch_file = tmp_path / "watch.yml"

    # Seed an existing transient failure that occurred yesterday (attempts=1)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (cache / "flakyco.json").write_text(json.dumps({
        "reason": "careers page: ConnectionError",
        "attempts": 1,
        "last_attempt": yesterday,
        "status": "transient"
    }))

    # Resolver succeeds on retry
    monkeypatch.setattr(watchlist, "resolve_company", lambda seed, hints=None: {"ats": "ashby", "board": "flaky", "reason": "verified slug"})

    res = watchlist.build(vendor, watch_file, cache)
    assert res["resolved"] == 1
    cached_data = json.loads((cache / "flakyco.json").read_text())
    assert cached_data.get("ats") == "ashby"

    # Now test dead marking: if already 3 attempts, mark dead and do not resolve
    (cache / "deadco.json").write_text(json.dumps({
        "reason": "careers page: ConnectionError",
        "attempts": 3,
        "last_attempt": yesterday,
        "status": "transient"
    }))
    (vendor / "dead.md").write_text('---\ntitle: "DeadCo"\nwebsite: https://dead.com\nregion: worldwide\n---\n')

    res2 = watchlist.build(vendor, watch_file, cache)
    cached_dead = json.loads((cache / "deadco.json").read_text())
    assert cached_dead.get("status") == "dead"


def test_agency_and_staffing_exclusion(tmp_path, monkeypatch):
    """Test staffing, agencies, and talent marketplaces are excluded at build and grow time."""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "andela.md").write_text('---\ntitle: "Andela"\nwebsite: https://andela.com\nregion: worldwide\n---\n')
    (vendor / "normal.md").write_text('---\ntitle: "NormalTech"\nwebsite: https://normal.com\nregion: worldwide\n---\n')

    cache = tmp_path / "cache"
    watch_file = tmp_path / "watch.yml"

    monkeypatch.setattr(watchlist, "resolve_company", lambda seed, hints=None: {"ats": "ashby", "board": seed["company"].lower(), "reason": "verified slug"})

    cfg = {
        "filters": {
            "exclude": {
                "company_name_keywords": [r"\bandela\b", r"\btoptal\b"],
                "company_types": ["agency", "outsource", "marketplace"],
            }
        }
    }
    res = watchlist.build(vendor, watch_file, cache, cfg=cfg)
    assert res["excluded_quality"] == 1
    entries = watchlist.list_entries(watch_file)
    assert len(entries) == 1
    assert entries[0]["company"] == "NormalTech"

    # Test grow exclusion
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE jobs(id TEXT, payload TEXT)")
    db.execute("CREATE TABLE decisions(job_id TEXT, outcome TEXT, stage TEXT)")
    db.execute("CREATE TABLE feedback(job_id TEXT, label TEXT)")
    db.execute("INSERT INTO jobs VALUES ('greenhouse:toptal:1', '{\"company\": \"Toptal\", \"regions\": [\"global\"]}')")
    db.execute("INSERT INTO jobs VALUES ('ashby:legit:2', '{\"company\": \"LegitCo\", \"regions\": [\"global\"]}')")
    db.commit()

    grow_res = watchlist.grow(db, watch_file, cfg=cfg)
    assert grow_res["excluded_quality"] == 1
    assert grow_res["added"] == 1
    grown_entries = [e["company"] for e in watchlist.list_entries(watch_file)]
    assert "LegitCo" in grown_entries
    assert "Toptal" not in grown_entries


def test_vendor_name_cleaning(tmp_path):
    """Test company name trailing > and whitespace is stripped."""
    f = tmp_path / "alphasights.md"
    f.write_text('---\ntitle: "AlphaSights>"\nwebsite: https://alphasights.com\nregion: worldwide\n---\n')
    parsed = watchlist._company_file(f)
    assert parsed["company"] == "AlphaSights"


def test_watchlist_seeds_loading(tmp_path, monkeypatch):
    """Test loading tracked seeds file watchlist.seeds.yml."""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    cache = tmp_path / "cache"
    watch_file = tmp_path / "watch.yml"
    seeds_file = tmp_path / "seeds.yml"
    seeds_file.write_text("""
companies:
  - company: Automattic
    website: https://automattic.com
    why: 100% remote worldwide
""")
    monkeypatch.setattr(watchlist, "resolve_company", lambda seed, hints=None: {"ats": "greenhouse", "board": "automattic", "reason": "verified slug"})

    res = watchlist.build(vendor, watch_file, cache, seeds_file=seeds_file)
    assert res["resolved"] == 1
    entries = watchlist.list_entries(watch_file)
    assert entries[0]["company"] == "Automattic"
    assert entries[0]["ats"] == "greenhouse"
