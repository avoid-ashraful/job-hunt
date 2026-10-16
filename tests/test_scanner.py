from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scanner.dedup import collapse_by_seniority, fingerprint, normalize_url
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


def test_staffing_company_names_dropped(filters):
    assert filters.evaluate(make_job(company="Come Back Agency", company_key="come-back-agency")).startswith("staffing")
    assert filters.evaluate(make_job(company="Turing", company_key="turing")).startswith("staffing")


def test_country_suffix_is_same_job():
    assert fingerprint("acme", "Principal Backend Engineer (United States)") == fingerprint("acme", "Principal Backend Engineer")


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


BASE = dict(eligibility="anywhere", eligibility_evidence="Work from anywhere", allowed_locations="worldwide")


@pytest.fixture(scope="module")
def location_rules(cfg):
    from scanner.rules import LocationRules

    return LocationRules(cfg["rules"]["location"])


@pytest.mark.parametrize(
    "text, outcome",
    [
        ("You must be located in the United States.", "locked"),
        ("This role is remote within Canada.", "locked"),
        ("We can only hire in the following countries: US, UK, Germany.", "locked"),
        ("This role can be worked remotely from anywhere in the UK only.", "locked"),
        ("The role is based in Europe.", "locked"),
        ("Удаленная работа на территории РФ.", "locked"),
        ("Work from anywhere in the world. We are async-first.", "positive"),
        ("Remote (Worldwide)", "positive"),
        ("Candidates must be located in Europe or Asia.", "positive"),
        ("We are hiring in Bangladesh, India and Pakistan.", "positive"),
        ("You need at least 4 hours of overlap with EST.", "timezone"),
        ("We build developer tools. You will write Python.", "silent"),
        ("We hire through Deel.", "ambiguous"),
        ("Location: Europe", "ambiguous"),
        ("Must be located in the US. Work from anywhere in the world!", "ambiguous"),
        # weaknesses found in the first real report
        ("* Work anywhere in the world for up to 3 months!", "silent"),
        ("We can hire people in any country where we have a legal entity.", "ambiguous"),
        ("We do not administer lie detector tests to applicants in any location.", "silent"),
        ("Location: can be based anywhere in Europe / EU Timezone", "locked"),
        ("This role is remote (Europe or USA) with overlap with EST timezone.", "ambiguous"),
        ("Remote-first and flexible: work from anywhere, at your own pace.", "ambiguous"),
        ("We're a fully remote, globally distributed team.", "ambiguous"),
        ("You must be located in a US time zone.", "timezone"),
    ],
)
def test_location_rules(location_rules, text, outcome):
    assert location_rules.classify(make_job(description=text)).outcome == outcome


def test_perk_is_not_eligibility(location_rules):
    text = "You must be based in the UK. Work remotely anywhere in the world for up to 1 month per year."
    assert location_rules.classify(make_job(description=text)).outcome == "locked"


def test_generic_place_is_not_a_lock(location_rules):
    assert location_rules.classify(make_job(description="You must be based in a quiet place.")).outcome == "silent"


def test_us_abbreviation_does_not_break_sentences(location_rules):
    v = location_rules.classify(make_job(description="Candidates must reside in the U.S. and work EST hours."))
    assert v.outcome == "locked" and "US" in v.place


@pytest.fixture(scope="module")
def role_rules(cfg):
    from scanner.rules import RoleRules

    return RoleRules(cfg["rules"]["role"])


def test_role_rules(role_rules):
    assert role_rules.evaluate(make_job(description="We are hiring for our client, a fintech."))[0].startswith("agency.")
    assert role_rules.evaluate(make_job(description="Python dev", posting_language="de")) is not None
    assert role_rules.evaluate(make_job(description="Fluent English required. Python.", posting_language="de")) is None
    fe = make_job(description="React React React CSS HTML Figma UI UX Tailwind frontend. Some API.")
    fe.family = "fullstack"
    assert role_rules.evaluate(fe)[0] == "role.frontend_heavy"
    good = make_job(title="Senior Python Engineer", description="Python, Django, Celery, Postgres, Redis, AWS, Docker")
    good.family = "python"
    assert role_rules.fit_score(good)[0] >= 4


def test_postings_not_seen_live_recently_are_dropped(filters):
    job = make_job(last_seen_at=NOW - timedelta(days=12))
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

    store.log_run("2026-10-15T05:00:00", 1, 1, 1, 1, "r.md", 0)
    assert not store.is_empty()


def test_location_verdicts_need_a_quote_from_the_description():
    from scanner.eligibility import check_evidence

    job = make_job(location="UA", description="We’re hiring engineers. Work from anywhere in the world... async-first team.")
    tag_only = check_evidence(job, {**BASE, "eligibility": "region_locked", "eligibility_evidence": "Location tag: UA"})
    assert tag_only["eligibility"] == "unclear"
    made_up = check_evidence(job, {**BASE, "eligibility": "anywhere", "eligibility_evidence": "Hire globally via Deel"})
    assert made_up["eligibility"] == "unclear"
    real = check_evidence(job, {**BASE, "eligibility": "anywhere",
                                "eligibility_evidence": "“Work from anywhere in the world… async-first team”"})
    assert real["eligibility"] == "anywhere"
    tz = check_evidence(job, {**BASE, "eligibility": "timezone_only", "eligibility_evidence": ""})
    assert tz["eligibility"] == "timezone_only"


def test_screener_runs_batches_in_threads_and_logs_calls(tmp_path, monkeypatch):
    """Regression: call logging used to touch sqlite from worker threads."""
    from scanner import eligibility
    from scanner.store import Store

    class FakeClient:
        name = "fake:model"

        def complete(self, user):
            refs = [line.split()[2] for line in user.splitlines() if line.startswith("### JOB")]
            return {"results": [{"job_ref": r, "eligibility": "anywhere", "eligibility_evidence": "x",
                                 "allowed_locations": "worldwide"} for r in refs]}

    store = Store(tmp_path / "s.sqlite")
    prompt = tmp_path / "p.md"
    prompt.write_text("judge location")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []
    screener = eligibility.Screener({"chain": [{"provider": "gemini", "model": "m"}], "batch_size": 2, "workers": 3},
                                    prompt, store.db, log_call=lambda *a: (calls.append(a), store.log_llm_call("d", *a)))
    screener.clients = [FakeClient()]
    jobs = [(make_job(id=f"j{i}"), ["Work from anywhere"]) for i in range(7)]
    results = screener.screen(jobs)
    assert len(results) == 7 and len(calls) == 4
    assert store.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 4


def test_json_is_recovered_from_fences_and_prose():
    from scanner.eligibility import _parse_json

    assert _parse_json('```json\n{"results": []}\n```') == {"results": []}
    assert _parse_json('Here you go: {"results": [1]} hope that helps') == {"results": [1]}


def test_openai_compatible_entry_reads_key_file_and_skips_when_missing(tmp_path, monkeypatch):
    from scanner import eligibility
    from scanner.store import Store

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    prompt = tmp_path / "p.md"
    prompt.write_text("judge location")
    key = tmp_path / "orca.key"
    key.write_text("secret\n")
    entry = {"provider": "openai-compatible", "name": "orca", "base_url": "https://x/v1/", "model": "m-free"}
    chain = [{**entry, "api_key_file": str(tmp_path / "missing.key")}, {**entry, "api_key_file": str(key)}]
    screener = eligibility.Screener({"chain": chain}, prompt, Store(tmp_path / "s.sqlite").db)
    [client] = screener.clients
    assert client.name == "orca:m-free" and client.url == "https://x/v1/chat/completions"
    assert client.api_key == "secret"


def test_openai_compatible_timeouts_move_on_to_the_next_model(monkeypatch):
    import pytest
    import requests

    from scanner import eligibility

    client = eligibility.OpenAICompatClient("orca", "https://x/v1", "m", "k", "sys")
    tries = []

    def hang(*a, **kw):
        tries.append(1)
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(client.session, "post", hang)
    with pytest.raises(eligibility.QuotaExhausted):
        client.complete("### JOB J1")
    assert len(tries) == 2


@pytest.mark.parametrize("evidence, kept", [
    ("Enjoy the freedom to work from anywhere, with flexible hours", False),
    ("Work from anywhere to facilitate your work life balance", False),
    ("Work from anywhere in the world", True),
    ("We hire people based anywhere, in any country", True),
    ("Remote-Global", True),
])
def test_bare_work_from_anywhere_is_not_worldwide(evidence, kept):
    from scanner.eligibility import check_evidence

    job = make_job(description=f"About us. {evidence}. Apply now.")
    r = check_evidence(job, {**BASE, "eligibility": "anywhere", "eligibility_evidence": evidence})
    assert (r["eligibility"] == "anywhere") is kept
