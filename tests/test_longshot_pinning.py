from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import yaml

from scanner import cli
from scanner.models import Job
from scanner.rules import Verdict
from scanner.sources import ats


def test_smartrecruiters_country_code_becomes_a_country(monkeypatch):
    monkeypatch.setattr(ats, "get", lambda *a, **kw: {"jobAd": {"sections": {"jobDescription": {"text": "Python"}}}})
    raw = {"id": "1", "name": "Senior Backend Engineer", "location": {"city": "", "country": "lk", "remote": True},
           "jobAd": {"sections": {"jobDescription": {"text": "Python"}}}}
    job = ats._record(raw, {"ats": "smartrecruiters", "board": "acme", "company": "Acme"}, {}, requests.Session())
    assert "lk" in job.countries


@pytest.mark.parametrize("source,countries,regions,expected", [
    ("ats", ["lk"], [], "longshot"),   # the employer's own board names another country
    ("ats", [], ["north_america"], "longshot"),  # "Americas Remote"
    ("ats", [], ["north_america", "apac"], "check"),  # "Americas or APAC"
    ("ats", [], [], "check"),
    ("freehire", ["lk"], [], "check"),  # an aggregator's country tag alone stays as before
])
def test_employer_listed_countries_make_a_silent_job_a_long_shot(tmp_path, monkeypatch, source, countries, regions,
                                                                  expected):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["sources"] = {"freehire": {"first_run_days": 3}}
    cfg["output"] = {"jobs_dir": "jobs", "state_dir": "state"}
    cfg["liveness"]["enabled"] = False
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli.CompanyProfiles, "enrich_report", lambda self, jobs: None)
    job = Job(id="smartrecruiters:acme:1", source=source, url="https://jobs.smartrecruiters.com/acme/1",
              title="Senior Python Engineer", company="Acme", company_key="acme",
              description="Build Python Django PostgreSQL Redis services. " * 20, location="Remote",
              countries=countries, regions=regions, watchlist=source == "ats", first_seen_at=datetime.now(timezone.utc))
    monkeypatch.setattr(cli.sources, "fetch_all", lambda *args: iter([job]))
    monkeypatch.setattr(cli.LocationRules, "classify", lambda self, job: Verdict("silent", "silent"))
    cli.run(config, dry_run=False, days=3, skip_llm=True)
    assert job.bucket == expected


def test_norway_is_a_country_code_not_yaml_false():
    # An unquoted `no:` key loads as False; a Norway-tagged long shot then crashed ", ".join(job.countries).
    patterns = yaml.safe_load("countries:\n  no: '\\bnorway\\b'\n")
    assert ats._location_tags("Remote - Norway", {"location_patterns": patterns}) == ([], ["no"])
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    assert all(isinstance(code, str) for code in cfg["sources"]["ats"]["location_patterns"]["countries"])


@pytest.mark.parametrize("location, countries", [
    ("Florida - Remote, New Mexico - Remote, Idaho - Remote", []),  # US states, not Mexico (a Salesforce posting)
    ("Mexico City, Mexico", ["mx"]),
])
def test_new_mexico_is_not_mexico(location, countries):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())["sources"]["ats"]
    assert ats._location_tags(location, cfg)[1] == countries


@pytest.mark.parametrize("location,expected", [
    ("REMOTE (US, Canada)", "longshot"),
    ("Remote (Europe)", "longshot"),
    ("REMOTE (Global)", "check"),
    ("Remote", "check"),
])
def test_untagged_sources_get_tags_from_their_location_text(tmp_path, monkeypatch, location, expected):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["sources"] = {"freehire": {"first_run_days": 3}, "ats": cfg["sources"]["ats"]}
    cfg["sources"]["ats"]["enabled"] = False
    cfg["output"] = {"jobs_dir": "jobs", "state_dir": "state"}
    cfg["liveness"]["enabled"] = False
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli.CompanyProfiles, "enrich_report", lambda self, jobs: None)
    job = Job(id="hn::1", source="hn", url="https://news.ycombinator.com/item?id=1", title="Senior Python Engineer",
              company="Acme", company_key="acme", description="Build Python Django PostgreSQL Redis services. " * 20,
              location=location, first_seen_at=datetime.now(timezone.utc))
    monkeypatch.setattr(cli.sources, "fetch_all", lambda *args: iter([job]))
    monkeypatch.setattr(cli.LocationRules, "classify", lambda self, job: Verdict("silent", "silent"))
    cli.run(config, dry_run=False, days=3, skip_llm=True)
    assert job.bucket == expected
