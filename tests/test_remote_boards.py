"""Captured public-feed fixtures; no live requests or private state."""

import copy
import importlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.dedup import normalize_url
from scanner.report import _table
from scanner.sources import jobicy, remoteok, workingnomads, wwr

FIXTURES = Path(__file__).parent / "fixtures" / "remote_boards"


def fixture(source):
    if source == "wwr":
        return ET.parse(FIXTURES / "wwr.xml").getroot()
    return json.loads((FIXTURES / f"{source}.json").read_text())


@pytest.mark.parametrize("source", ["remoteok", "wwr", "workingnomads", "jobicy"])
def test_captured_board_parser(source):
    raw = fixture(source)
    module = importlib.import_module(f"scanner.sources.{source}")
    job = module.to_job(raw)
    assert job and job.source == source
    assert job.title and job.company and job.description
    assert "<p>" not in job.description and "<strong>" not in job.description
    assert job.posted_at.tzinfo and job.first_seen_at == job.posted_at
    assert normalize_url(job.ats_metadata["source_url"]) == job.url
    assert job.id.startswith(source + "::")
    if source in {"remoteok", "jobicy"}:
        assert job.salary_min > 0 and job.salary_max >= job.salary_min
        assert job.salary_currency == "USD" and job.salary_period == "year"
    if source in {"remoteok", "workingnomads", "jobicy"}:
        assert job.skills
    if source in {"remoteok", "wwr", "jobicy"}:
        assert {"remoteok": "Remote OK apply", "wwr": "We Work Remotely apply",
                "jobicy": "Jobicy apply"}[source] in "\n".join(_table([job], False))


@pytest.mark.parametrize("source,field", [("remoteok", "location"), ("workingnomads", "location"),
                                         ("jobicy", "jobGeo"), ("wwr", "region")])
def test_worldwide_evidence_requires_unrestricted_location(source, field):
    module = importlib.import_module(f"scanner.sources.{source}")
    for location, expected in [("Worldwide", ["global"]), ("Anywhere in the World", ["global"]),
                               ("Global", ["global"]), ("Remote", []), ("Anywhere in India", []),
                               ("USA", []), ("Europe, LATAM, APAC", ["apac", "emea", "latam"])]:
        raw = fixture(source)
        if source == "wwr":
            raw.find(field).text = location
        else:
            raw[field] = location
        assert module.to_job(raw).regions == expected


def test_wwr_country_and_state_override_anywhere_label():
    raw = fixture("wwr")
    raw.find("country").text = "Germany, France"
    raw.find("state").text = "Berlin"
    job = wwr.to_job(raw)
    assert job.location == "Germany, France; Berlin" and job.regions == []


def test_workingnomads_employer_link_uses_ats_id():
    raw = fixture("workingnomads")
    raw["apply_url"] = "https://jobs.lever.co/acme/1234"
    job = workingnomads.to_job(raw)
    assert job.id == "lever:acme:1234" and job.url == raw["apply_url"]
    assert job.ats_metadata["source_url"] == raw["url"]


def test_remoteok_legal_record_and_missing_identity_are_not_jobs():
    assert remoteok.to_job({"legal": "API terms", "last_updated": 1792224050}) is None
    assert remoteok.to_job({**fixture("remoteok"), "company": ""}) is None


@pytest.mark.parametrize("source,date_field", [("remoteok", "date"), ("workingnomads", "pub_date"),
                                              ("jobicy", "pubDate"), ("wwr", "pubDate")])
def test_fetch_filters_dates_without_network(monkeypatch, source, date_field):
    module = importlib.import_module(f"scanner.sources.{source}")
    fresh, old = fixture(source), copy.deepcopy(fixture(source))
    now = datetime.now(timezone.utc)
    if source == "wwr":
        fresh.find(date_field).text = format_datetime(now - timedelta(days=1))
        old.find(date_field).text = format_datetime(now - timedelta(days=60))
        content = b"<rss><channel>" + ET.tostring(fresh) + ET.tostring(old) + b"</channel></rss>"
        response = SimpleNamespace(content=content)
    else:
        fresh[date_field] = (now - timedelta(days=1)).isoformat()
        old[date_field] = (now - timedelta(days=60)).isoformat()
        rows = [fresh, old]
        data = {"jobs": rows} if source == "jobicy" else rows
        response = SimpleNamespace(json=lambda: data)
    calls, sleeps = [], []
    monkeypatch.setattr(module, "get", lambda *args: calls.append(copy.deepcopy(args[1:])) or response)
    monkeypatch.setattr(module, "session", lambda: object())
    if source in {"wwr", "jobicy"}:
        monkeypatch.setattr(module.time, "sleep", sleeps.append)
    cfg = {"request_delay_s": 2, "geo": "apac"}
    jobs = list(module.fetch(cfg, 7))
    assert len(jobs) == 1 and jobs[0].posted_at >= now - timedelta(days=7)
    assert len(calls) == (2 if source == "wwr" else 1)
    if source == "wwr":
        assert sleeps == [2]
    if source == "jobicy":
        assert calls[0][1] == {"count": 50, "tag": "python", "geo": "apac"}


def test_jobicy_bounded_cursor_pagination(monkeypatch):
    responses = iter([{"jobs": [fixture("jobicy")], "nextCursor": "opaque"}, {"jobs": []}])
    calls, sleeps = [], []
    monkeypatch.setattr(jobicy, "session", lambda: object())
    monkeypatch.setattr(jobicy, "get", lambda s, url, params: calls.append(dict(params)) or
                        SimpleNamespace(json=lambda: next(responses)))
    monkeypatch.setattr(jobicy.time, "sleep", sleeps.append)
    assert len(list(jobicy.fetch({"max_pages": 2, "request_delay_s": 3}, 100000))) == 1
    assert "cursor" not in calls[0] and calls[1]["cursor"] == "opaque"
    assert sleeps == [3]


def test_remoteok_prefers_exposed_employer_link_and_keeps_attribution():
    raw = fixture("remoteok")
    raw["apply_url"] = "https://jobs.lever.co/acme/1234"
    job = remoteok.to_job(raw)
    assert job.id == "lever:acme:1234" and job.url == raw["apply_url"]
    row = "\n".join(_table([job], False))
    assert f"[apply]({job.url})" in row
    assert f"[Remote OK]({raw['url']})" in row
