from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scanner.dedup import collapse_by_seniority, fingerprint, normalize_url
from scanner.eligibility import decide
from scanner.filters import Filters
from scanner.models import Job

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((ROOT / "config.example.yml").read_text())


@pytest.fixture(scope="module")
def filters(cfg):
    f = cfg["filters"]
    f["exclude"]["companies"] = ["Initech", "Globex"]
    return Filters(f, cfg["dedup"]["seniority_rank"], NOW)


def make_job(title="Senior Backend Engineer", description="We use Python and Django.", **kw) -> Job:
    defaults = dict(
        id="greenhouse:acme:1", source="freehire", url="https://boards.greenhouse.io/acme/jobs/1",
        title=title, company="Acme", company_key="acme", description=description,
        first_seen_at=NOW - timedelta(days=2),
    )
    defaults.update(kw)
    return Job(**defaults)


@pytest.mark.parametrize(
    "title, expected_family",
    [
        ("Senior Backend Engineer", "backend"),
        ("Staff Software Engineer, Payments", "software"),
        ("Senior Python Developer", "python"),
        ("Lead Full-Stack Engineer", "fullstack"),
        ("Applied AI Engineer", "applied_ai"),
        ("Senior Engineer", "generic"),
    ],
)
def test_target_titles_kept(filters, title, expected_family):
    job = make_job(title=title)
    assert filters.evaluate(job) is None
    assert job.family == expected_family


@pytest.mark.parametrize(
    "title",
    ["Junior Backend Engineer", "Senior Frontend Engineer", "Engineering Manager, Backend",
     "Senior DevOps Engineer", "Backend Intern", "AI Trainer - Python", "Senior Backend Engineer (Go)",
     "Staff Product Designer"],
)
def test_non_target_titles_dropped(filters, title):
    assert filters.evaluate(make_job(title=title)) is not None


def test_mid_level_titles_are_kept(filters):
    assert filters.evaluate(make_job(title="Backend Engineer II")) is None


@pytest.mark.parametrize(
    "description",
    [
        "Candidates must be authorized to work in the United States.",
        "This role requires an active Secret clearance.",
        "This is a W2 only position.",
        "Must be a U.S. citizen.",
    ],
)
def test_hard_drops(filters, description):
    reason = filters.evaluate(make_job(description="Python. " + description))
    assert reason and reason.split(":")[0] in {"us_work_authorization", "security_clearance", "w2_only"}


def test_generic_work_authorization_line_is_not_a_hard_drop(filters):
    job = make_job(description="Python. You must be authorized to work in the country where you reside.")
    assert filters.evaluate(job) is None


def test_stack_rules(filters):
    assert filters.evaluate(make_job(description="Our services are written in Golang on Kubernetes.")).startswith("no Python")
    language_agnostic = make_job(description="We care about fundamentals, not languages.")
    assert filters.evaluate(language_agnostic) is None
    assert "no-language-named" in language_agnostic.tags


def test_excluded_company_and_age_and_salary(filters):
    assert filters.evaluate(make_job(company="Globex", company_key="globex")).startswith("excluded company")
    assert filters.evaluate(make_job(first_seen_at=NOW - timedelta(days=30))).startswith("older than")
    low = make_job(salary_max=80_000, salary_currency="USD", salary_period="year")
    assert filters.evaluate(low).startswith("salary max")
    hourly = make_job(salary_max=70, salary_currency="USD", salary_period="hour")
    assert filters.evaluate(hourly) is None  # 70 * 2080 = 145,600


def test_restated_posted_date_does_not_make_job_fresh(filters):
    job = make_job(posted_at=NOW, first_seen_at=NOW - timedelta(days=40))
    assert filters.evaluate(job).startswith("older than")


def test_url_and_fingerprint_normalization():
    assert normalize_url("https://Jobs.Lever.co/acme/123/?utm_source=x&lever-source=y") == "https://jobs.lever.co/acme/123"
    assert fingerprint("acme", "Sr. Back-End Engineer (Remote, US)") == fingerprint("acme", "Senior Backend Engineer - Remote")


def test_collapse_keeps_most_senior_per_company_family(filters):
    senior = make_job(id="a", title="Senior Backend Engineer")
    mid = make_job(id="b", title="Backend Engineer")
    other_co = make_job(id="c", title="Backend Engineer", company_key="other")
    for j in (senior, mid, other_co):
        assert filters.evaluate(j) is None
    kept, dropped = collapse_by_seniority([senior, mid, other_co], {})
    assert {j.id for j in kept} == {"a", "c"}
    assert dropped[0][0].id == "b"


def test_collapse_respects_already_reported_seniority(filters):
    mid = make_job(id="b", title="Backend Engineer")
    filters.evaluate(mid)
    kept, dropped = collapse_by_seniority([mid], {("acme", "backend"): (3, "Senior Backend Engineer")})
    assert not kept and dropped


BASE = dict(eligibility="anywhere", eligibility_evidence="Work from anywhere", allowed_locations="worldwide",
            hiring_model="contractor", primary_language="Python", python_role=True, role_type="backend",
            seniority="senior", people_manager=False, agency_or_outsourcing=False,
            english_working_language=True, fit_score=4, fit_reason="")


@pytest.mark.parametrize(
    "overrides, bucket",
    [
        ({}, "apply"),
        ({"eligibility": "unclear"}, "check"),
        ({"eligibility": "region_locked"}, "drop"),
        ({"eligibility": "work_authorization_required"}, "drop"),
        ({"python_role": False}, "drop"),
        ({"role_type": "frontend_heavy_fullstack"}, "drop"),
        ({"people_manager": True}, "drop"),
        ({"agency_or_outsourcing": True}, "drop"),
        ({"english_working_language": False}, "drop"),
        ({"fit_score": 2}, "check"),
    ],
)
def test_decide(overrides, bucket):
    assert decide({**BASE, **overrides})[0] == bucket


def test_postings_not_seen_live_recently_are_dropped(filters):
    job = make_job(last_seen_at=NOW - timedelta(days=5))
    assert filters.evaluate(job).startswith("not seen live")
    assert filters.evaluate(make_job(last_seen_at=NOW - timedelta(hours=6))) is None


def test_store_never_reports_twice(tmp_path, filters):
    from scanner.store import Store

    store = Store(tmp_path / "s.sqlite")
    assert store.is_empty()
    job = make_job(id="greenhouse:acme:1")
    filters.evaluate(job)
    store.mark_reported(job, "2026-10-15")

    same_link_new_id = make_job(id="lever:acme:9")
    reposted = make_job(id="ashby:acme:7", url="https://jobs.ashbyhq.com/acme/7", title="Sr. Back-End Engineer (Remote)")
    different = make_job(id="ashby:acme:8", url="https://jobs.ashbyhq.com/acme/8", title="Senior Python Engineer")
    assert "same link" in store.already_reported(same_link_new_id)
    assert "same company+title" in store.already_reported(reposted)
    assert store.already_reported(different) is None

    store.log_run("2026-10-15T05:00:00", 1, 1, 1, 1, "r.md", deferred=3)
    assert not store.is_empty() and store.last_run_incomplete()
    store.log_run("2026-10-16T05:00:00", 1, 1, 1, 1, "r.md", deferred=0)
    assert not store.last_run_incomplete()
