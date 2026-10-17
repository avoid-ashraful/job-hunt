"""Tests for expanded ATS adapters, resolver discoveries, and watchlist quality."""

from __future__ import annotations

from pathlib import Path
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
