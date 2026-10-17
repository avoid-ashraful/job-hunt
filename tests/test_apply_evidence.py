from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
import yaml

from scanner import cli
from scanner.apply_evidence import ApplyEvidence, DEFAULT_ATS_LOCATION, DEFAULT_PATTERNS
from scanner.models import Job
from scanner.rules import Verdict


@pytest.mark.parametrize("body,evidence,source,location,watched,expected", [
    ("Work from anywhere with flexible hours", "Work from anywhere with flexible hours", "freehire", "", False, "check"),
    ("Build Python services", "Remote, Worldwide", "ats", "Remote, Worldwide", True, "apply"),
    ("Build Python services", "Python Engineer - worldwide", "freehire", "", False, "check"),
    ("We hire worldwide.", "We hire worldwide.", "freehire", "", False, "apply"),
    ("Build Python services", "Remote, Worldwide", "freehire", "Remote, Worldwide", True, "check"),
    ("We do not hire worldwide.", "We do not hire worldwide.", "freehire", "", False, "check"),
    ("Work from anywhere in the world for up to 4 weeks per year.",
     "Work from anywhere in the world for up to 4 weeks per year.", "freehire", "", False, "check"),
])
def test_apply_evidence_pipeline(tmp_path, monkeypatch, caplog, body, evidence, source, location, watched, expected):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["sources"] = {"freehire": {"first_run_days": 3}}
    cfg["output"] = {"jobs_dir": "jobs", "state_dir": "state"}
    cfg["liveness"]["enabled"] = False
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli.CompanyProfiles, "enrich_report", lambda self, jobs: None)
    job = Job(id="greenhouse:acme:123", source=source, url="https://example.com/job",
              title="Python Engineer - worldwide", company="Acme", company_key="acme",
              description=body + "\nBuild Python Django PostgreSQL Redis services. " * 15,
              location=location, watchlist=watched, first_seen_at=datetime.now(timezone.utc))
    monkeypatch.setattr(cli.sources, "fetch_all", lambda *args: iter([job]))
    monkeypatch.setattr(cli.LocationRules, "classify", lambda self, job: Verdict("positive", "test.positive", evidence))
    with caplog.at_level("INFO", logger="scanner"):
        report = cli.run(config, dry_run=False, days=3, skip_llm=True)
    assert job.bucket == expected
    demoted = int(expected == "check")
    assert f"Apply evidence guard: **{demoted}** demoted to Check" in report.read_text()
    assert f"Apply evidence guard demoted {demoted} jobs to Check" in caplog.text
    db = sqlite3.connect(tmp_path / "state" / "scanner.sqlite")
    assert db.execute("SELECT count(*) FROM decisions WHERE stage='apply_evidence'").fetchone()[0] == demoted
    if demoted:
        assert job.screen["why"].startswith("eligible, but evidence is weak: ")


def test_apply_evidence_defaults_and_config():
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())["rules"]["apply_evidence"]
    assert cfg == {"patterns": DEFAULT_PATTERNS, "ats_location": DEFAULT_ATS_LOCATION}
    job = Job(id="1", source="freehire", url="", title="", company="", company_key="",
              description="We hire worldwide.", screen={"evidence": "We hire worldwide."})
    assert ApplyEvidence({}).weak_reason(job) is None
    assert ApplyEvidence({"patterns": {}}).weak_reason(job)


@pytest.mark.parametrize("evidence,strong", [
    ("We hire contractors from any country.", True),
    ("We are globally hiring.", True),
    ("Remote across APAC.", True),
    ("Hiring in Asia.", True),
    ("Eligible countries: India, Bangladesh, Pakistan.", True),
    ("We hire in Southeast Asia.", False),
    ("Our team works remotely from Europe with colleagues in Asia.", False),
    ("Our customers are worldwide.", False),
    ("We hire in any country where we have an entity.", False),
])
def test_strong_patterns(evidence, strong):
    job = Job(id="1", source="freehire", url="", title="", company="", company_key="",
              description=evidence, screen={"evidence": evidence})
    assert (ApplyEvidence({}).weak_reason(job) is None) == strong
