"""Company HQ evidence and local-only posting rules."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from scanner.company_profile import CompanyProfiles, hq_country
from scanner.filters import Filters
from scanner.models import Job
from scanner.pay_market import levels_tiers, local_only, salary_usd
from scanner.report import _sort_key
from datetime import datetime, timezone


def job(company="Example", description="", location="Remote", countries=None, **kwargs):
    return Job(id="test:1", source="test", url="https://example.com/job", title="Senior Python Engineer",
               company=company, company_key=company.lower().replace(" ", "-"), description=description,
               location=location, countries=countries or [], **kwargs)


def test_explicit_hq_beats_india_office_and_currency():
    j = job(description="We are headquartered in San Francisco, USA. Our Bangalore office is hiring.",
            countries=["in"], salary_currency="INR")
    assert hq_country(j)[0] == "us"


def test_ats_metadata_beats_description():
    j = job(description="We are headquartered in India.")
    j.ats_metadata = {"hq_country": "de"}
    assert hq_country(j)[0] == "de"


def test_verified_override_is_highest_priority():
    j = job(company="Atlas", description="Atlas is headquartered in India.")
    assert hq_country(j, {"hq_overrides": {"atlas": "us"}})[0] == "us"


def test_legal_name_and_bangladesh_domain():
    assert hq_country(job(company="Nimbus Pvt Ltd", countries=["in"]))[0] == "in"
    assert hq_country(job(company="Summit Private Limited", countries=["sg"]))[0] is None
    assert hq_country(job(description="Our website is https://example.com.bd"))[0] == "bd"


def test_only_company_office_counts():
    assert hq_country(job(description="Our only office is in Bengaluru, India."))[0] == "in"
    assert hq_country(job(description="Our London and India offices work together."))[0] is None
    assert hq_country(job(description="Candidates in Bangalore welcome. Visit python.org.in for docs."))[0] is None


def test_salary_is_weak_evidence():
    assert hq_country(job(description="Salary is 20 LPA"))[0] is None
    assert "weak" in hq_country(job(salary_currency="BDT"))[1]


def test_cache_strong_evidence_wins(tmp_path: Path):
    db = sqlite3.connect(tmp_path / "profile.db")
    profiles = CompanyProfiles(db)
    weak = job(company="Acme", description="Salary: 20 LPA")
    assert profiles.resolve(weak)[0] is None
    strong = job(company="Acme", description="Acme is headquartered in London, United Kingdom.")
    assert profiles.resolve(strong)[0] == "gb"
    later = job(company="Acme", salary_currency="INR")
    assert profiles.resolve(later)[0] == "gb"
    assert db.execute("SELECT source FROM company_profiles").fetchone()[0] == "headquarters"


def test_vendor_uses_explicit_hq_not_hiring_region(tmp_path: Path):
    (tmp_path / "nimbus.md").write_text('---\ntitle: Nimbus\nregion: worldwide\n---\n## Office locations\nheadquarters: Germany\n')
    (tmp_path / "other.md").write_text('title: Other\nregion: India\n---\n')
    profiles = CompanyProfiles(sqlite3.connect(":memory:"), vendor_dir=tmp_path)
    assert profiles.resolve(job(company="Nimbus"))[0] == "de"
    assert profiles.resolve(job(company="Other"))[0] is None


def test_location_rule_keeps_mixed_and_foreign_hq():
    assert local_only(job(location="Bengaluru, India", countries=["in"]))
    assert local_only(job(location="Remote - Dhaka", countries=["bd"]))
    assert local_only(job(location="Remote", cities=["Bengaluru"]))
    assert not local_only(job(location="Remote", cities=["Bengaluru", "Berlin"]))
    assert not local_only(job(location="India and Germany", countries=["in", "de"]))
    assert not local_only(job(location="India, Pakistan"))
    assert not local_only(job(location="Worldwide", countries=["in"], regions=["global"]))


def test_filter_distinguishes_hq_and_job_location():
    cfg = yaml.safe_load(Path("config.example.yml").read_text())
    db = sqlite3.connect(":memory:")
    f = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], datetime.now(timezone.utc),
                CompanyProfiles(db, cfg["filters"]["company_market"]))
    assert f.evaluate(job(company="Atlas", description="Atlas is headquartered in New York, USA.",
                          location="Bangalore", countries=["in"])) == "location only in IN"
    mixed = job(company="Acme", description="Acme is headquartered in Berlin, Germany. We also hire in India.",
                countries=["de", "in"])
    assert f.evaluate(mixed) is None
    assert mixed.screen["hq_country"] == "de"
    # a foreign company hiring in Bangladesh is what the candidate wants; a Bangladesh-only job with
    # no known foreign HQ is most likely a local company
    foreign = job(company="Orbit", description="Orbit is headquartered in Austin, USA.", location="Dhaka", countries=["bd"])
    assert f.evaluate(foreign) is None
    assert f.evaluate(job(company="Nameless", location="Dhaka", countries=["bd"])) == "location only in BD"


def test_pay_hint_and_sort(tmp_path: Path):
    paid = job(salary_min=100000, salary_max=150000, salary_currency="EUR")
    cfg = {"usd_rates": {"EUR": 1.08}, "period_multipliers": {"year": 1}}
    assert salary_usd(paid, cfg) == "$108–162k/yr"
    (tmp_path / "tiers.json").write_text('{"companies": {"example": {"pay_tier": "high"}}}')
    assert levels_tiers(tmp_path)["example"] == "high"
    paid.screen = {"fit": 3, "preferred_hq": True}
    other = job(company="Other")
    other.screen = {"fit": 5, "preferred_hq": False}
    assert _sort_key(paid) < _sort_key(other)
