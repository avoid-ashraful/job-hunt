from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scanner.filters import Filters
from scanner.models import Job
from scanner.pay_market import salary_usd
from scanner.report import _table
from scanner.salary import fill_salary, parse_salary


@pytest.mark.parametrize("text, expected", [
    ("$150,000 - $190,000", (150000, 190000, "USD", "year")),
    ("$150k–$190k USD", (150000, 190000, "USD", "year")),
    ("USD 120,000 to 160,000 per year", (120000, 160000, "USD", "year")),
    ("€70.000 - €90.000", (70000, 90000, "EUR", "year")),
    ("Base salary: £80k", (80000, 80000, "GBP", "year")),
    ("£80k", (80000, 80000, "GBP", "year")),
    ("$75/hour", (75, 75, "USD", "hour")),
    ("$8,000 - $10,000/month", (8000, 10000, "USD", "month")),
    ("150-190K", (150000, 190000, None, "year")),
    ("up to $200k", (None, 200000, "USD", "year")),
    ("Salary: CAD 140k to 180k", (140000, 180000, "CAD", "year")),
    ("Compensation: A$140,000–A$180,000", (140000, 180000, "AUD", "year")),
    ("Pay: SGD 120k–150k annually", (120000, 150000, "SGD", "year")),
    ("Base: $120k–$160k CAD", (120000, 160000, "CAD", "year")),
    ("<p>Salary: &pound;80k</p>", (80000, 80000, "GBP", "year")),
    ("The salary is $120k.", (120000, 120000, "USD", "year")),
    ("Pay: $75.50/hr", (75.5, 75.5, "USD", "hour")),
    ("Pay: €400/day", (400, 400, "EUR", "day")),
    ("Pay: $2,000/week", (2000, 2000, "USD", "week")),
    ("5-8 years of experience", None),
    ("Salary and 401k benefits", None),
    ("We raised $50M in funding", None),
    ("We have 150-190K users", None),
    ("Equity: 0.1-0.5%", None),
    ("Salary competitive. Team has 150-190K employees", None),
    ("Our budget is $150,000 - $190,000", None),
    ("Salary: $190k-$150k", None),
    ("Salary: USD 120k - EUR 160k", None),
    ("Salary: 120000-160000", None),
    ("$50,000 signing bonus. Base salary: $150k–$190k", (150000, 190000, "USD", "year")),
    ("Equity: $50k-$80k. Salary: $150k-$190k", (150000, 190000, "USD", "year")),
    ("US salary: $150k-$190k. UK salary: £80k-£100k", None),
    ("", None),
])
def test_parse_salary(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize("text", [
    "Salary starts at $50k, depending on experience.",
    "Base salary: from $50,000 per year.",
    "Minimum salary: $50k.",
    "Salary: at least $50k.",
    "Base salary: $50k+",
    "Salary: $50k or more.",
])
def test_lower_bound_is_not_invented_as_a_salary_ceiling(text):
    # The hard filter treats a lone salary_min as the top too, so leave these unparsed.
    assert parse_salary(text) is None


def make_job(**kw):
    return Job(id="test:1", source="test", url="https://example.com/job", title="Engineer",
               company="Example", company_key="example", **kw)


def test_parsed_salary_uses_existing_conversion_filter_and_original_display():
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], datetime.now(timezone.utc))
    job = make_job(description="Pay: $8,000 - $10,000/month")
    fill_salary(job)
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (8000, 10000, "USD", "month")
    assert filters._salary(job) is None
    assert salary_usd(job, cfg["filters"]["salary"]) == "$96–120k/yr"
    job.screen["pay_hint"] = salary_usd(job, cfg["filters"]["salary"])
    assert "$8,000 - $10,000/month (parsed from text)" in "\n".join(_table([job], False))
    low = make_job(description="Salary: €10,000–€20,000")
    fill_salary(low)
    assert filters._salary(low).startswith("salary max")
    unknown = make_job(description="150-190K")
    fill_salary(unknown)
    assert filters._salary(unknown) is None
    assert "150-190K (parsed from text)" in "\n".join(_table([unknown], False))


@pytest.mark.parametrize("salary", [dict(salary_min=100000), dict(salary_max=200000),
                                  dict(salary_min=100000, salary_max=200000, salary_currency="EUR", salary_period="year")])
def test_source_salary_is_preserved(salary):
    job = make_job(description="Salary: $50k-$60k", **salary)
    fill_salary(job)
    for key, value in salary.items():
        assert getattr(job, key) == value
    assert "salary_from_text" not in job.ats_metadata


def test_pipeline_parses_after_source_enrichment_before_store_and_filter(tmp_path, monkeypatch):
    import json
    import sqlite3

    from scanner import cli

    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["output"] = {"jobs_dir": "jobs", "state_dir": "state"}
    cfg["liveness"]["enabled"] = False
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    job = make_job(description="Python. Salary: $10k-$20k", posted_at=datetime.now(timezone.utc), regions=["global"])
    job.title = "Senior Backend Engineer"
    monkeypatch.setattr(cli.sources, "fetch_all", lambda *args: iter([job]))
    cli.run(config, dry_run=False, days=1, skip_llm=True)
    db = sqlite3.connect(tmp_path / "state" / "scanner.sqlite")
    payload = json.loads(db.execute("SELECT payload FROM jobs").fetchone()[0])
    assert payload["salary_max"] == 20000
    assert payload["ats_metadata"]["salary_from_text"] == "$10k-$20k"
    assert db.execute("SELECT detail FROM decisions WHERE stage='filter'").fetchone()[0].startswith("salary max")
    db.close()
