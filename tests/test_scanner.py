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
    assert filters.evaluate(make_job(first_seen_at=NOW - timedelta(days=50))).startswith("older than")
    low = make_job(salary_max=80_000, salary_currency="USD", salary_period="year")
    assert filters.evaluate(low).startswith("salary max")
    hourly = make_job(salary_max=70, salary_currency="USD", salary_period="hour")
    assert filters.evaluate(hourly) is None  # 70 * 2080 = 145,600


def test_restated_posted_date_does_not_make_job_fresh(filters):
    job = make_job(posted_at=NOW, first_seen_at=NOW - timedelta(days=50))
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
        ("Candidates must reside in the Eastern or Central time zone.", "locked"),
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


@pytest.mark.parametrize("evidence, expected", [
    ("Location tag: US", "unclear"),
    ("US work authorization is required.", "unclear"),
    ("…", "unclear"),
    ("Applicants must have the right to work in Canada.", "work_authorization_required"),
])
def test_authorization_verdict_requires_body_evidence(evidence, expected):
    from scanner.eligibility import check_evidence

    job = make_job(location="US", description="Applicants must have the right to work in Canada.")
    result = check_evidence(job, {**BASE, "eligibility": "work_authorization_required",
                                "eligibility_evidence": evidence})
    assert result["eligibility"] == expected


@pytest.mark.parametrize("evidence", [
    "Location: Remote Japan Japan",
    "*Location:* Remote - Chile, Peru",
    "Location(s): Australia : Victoria : Melbourne",
])
def test_body_location_headers_are_not_hiring_restrictions(evidence):
    from scanner.eligibility import check_evidence

    requirement = "Applicants must reside in Japan."
    job = make_job(description=f"{evidence}\n{requirement}")
    result = check_evidence(job, {**BASE, "eligibility": "region_locked",
                                "eligibility_evidence": evidence})
    assert result["eligibility"] == "unclear"
    supported = check_evidence(job, {**BASE, "eligibility": "region_locked",
                                   "eligibility_evidence": requirement})
    assert supported["eligibility"] == "region_locked"


def test_location_header_with_explicit_role_scope_is_evidence():
    from scanner.eligibility import check_evidence

    evidence = "*Location* : This is a remote role based in the EMEA region."
    job = make_job(description=evidence)
    result = check_evidence(job, {**BASE, "eligibility": "region_locked",
                                "eligibility_evidence": evidence})
    assert result["eligibility"] == "region_locked"


def test_audit_safeguards_reach_private_prompts_and_invalidate_cache(tmp_path, monkeypatch):
    import hashlib
    import json
    import sqlite3

    from scanner import eligibility

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    prompt = tmp_path / "eligibility.md"
    prompt.write_text("Judge location only.")
    db = sqlite3.connect(":memory:")
    screener = eligibility.Screener({"chain": [{"provider": "gemini", "model": "test"}]}, prompt, db)
    system = screener.clients[0].system
    assert "No visa sponsorship or conditional export/security checks alone" in system
    assert "A partial country list introduced by 'including'" in system
    old_system = system.split("\n\n## Evidence safeguards")[0]
    old_hash = hashlib.sha256((old_system + json.dumps(eligibility.BATCH_SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]
    db.execute("INSERT INTO llm_location (job_id, prompt_hash, model, result, created_at) VALUES (?, ?, ?, ?, ?)",
               ("job", old_hash, "test", json.dumps(BASE), "2026-10-16"))
    assert screener._cached(make_job(id="job"), []) is None


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


def test_screener_cache_hit_on_same_content_and_miss_on_changed_snippet(tmp_path, monkeypatch):
    from scanner import eligibility
    from scanner.store import Store

    class CountingClient:
        name = "fake:model"

        def __init__(self):
            self.calls = 0

        def complete(self, user):
            self.calls += 1
            refs = [line.split()[2] for line in user.splitlines() if line.startswith("### JOB")]
            results = []
            for r in refs:
                elig = "region_locked" if "US only" in user else "anywhere"
                results.append({
                    "job_ref": r,
                    "eligibility": elig,
                    "eligibility_evidence": "US only" if elig == "region_locked" else "work from anywhere in the world",
                    "allowed_locations": "US" if elig == "region_locked" else "worldwide",
                })
            return {"results": results}

    store = Store(tmp_path / "cache.sqlite")
    prompt = tmp_path / "p.md"
    prompt.write_text("judge location")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    screener = eligibility.Screener({"chain": [{"provider": "gemini", "model": "m"}]}, prompt, store.db)
    client = CountingClient()
    screener.clients = [client]

    job = make_job(id="j1", title="Backend Dev", description="Python role.")
    snippets = ["Work from anywhere in the world."]

    # 1. First screen: cache miss, calls model
    res1 = screener.screen([(job, snippets)])
    assert res1["j1"][1] == "fake:model"
    assert res1["j1"][0]["eligibility"] == "anywhere"
    assert client.calls == 1

    # Same content -> hit (0 additional LLM calls)
    res2 = screener.screen([(job, snippets)])
    assert res2["j1"][1] == "cache"
    assert res2["j1"][0]["eligibility"] == "anywhere"
    assert client.calls == 1

    # 2. Changed snippet -> miss and re-screen
    res3 = screener.screen([(job, ["US only. Must reside in US."])])
    assert res3["j1"][1] == "fake:model"
    assert res3["j1"][0]["eligibility"] == "region_locked"
    assert client.calls == 2

    # Subsequent screen with new snippet -> hit
    res4 = screener.screen([(job, ["US only. Must reside in US."])])
    assert res4["j1"][1] == "cache"
    assert res4["j1"][0]["eligibility"] == "region_locked"
    assert client.calls == 2


def test_screener_legacy_cache_row_without_hash_hits_and_stores_hash(tmp_path, monkeypatch):
    import json
    import sqlite3

    from scanner import eligibility

    prompt = tmp_path / "p.md"
    prompt.write_text("judge location")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    # Legacy table without content_hash column
    db = sqlite3.connect(tmp_path / "legacy.sqlite")
    db.execute("""
        CREATE TABLE llm_location (
            job_id TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (job_id, prompt_hash)
        )
    """)
    job = make_job(id="legacy:1", title="Python Engineer")
    snippets = ["Work from anywhere in the world."]

    # Instantiating Screener migrates the table to add content_hash
    screener = eligibility.Screener({"chain": [{"provider": "gemini", "model": "m"}]}, prompt, db)
    cols = {r[1] for r in db.execute("PRAGMA table_info(llm_location)")}
    assert "content_hash" in cols

    # Insert a legacy row with NULL content_hash
    legacy_res = {"eligibility": "anywhere", "eligibility_evidence": "Work from anywhere in the world.", "allowed_locations": "worldwide"}
    db.execute(
        "INSERT INTO llm_location (job_id, prompt_hash, model, result, created_at) VALUES (?, ?, ?, ?, ?)",
        (job.id, screener.prompt_hash, "gemini:old", json.dumps(legacy_res), "2026-10-16"),
    )
    db.commit()

    # Legacy row without hash -> hit, and hash gets stored
    cached = screener._cached(job, snippets)
    assert cached == legacy_res

    # Hash is now stored
    stored_hash = db.execute("SELECT content_hash FROM llm_location WHERE job_id = ?", (job.id,)).fetchone()[0]
    expected_hash = screener.content_hash(job, snippets)
    assert stored_hash == expected_hash

    # With the hash stored, changed snippet now misses
    assert screener._cached(job, ["US only."]) is None


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


def test_openai_compatible_long_retry_after_moves_on_without_sleeping(monkeypatch):
    import pytest
    import requests

    from scanner import eligibility

    client = eligibility.OpenAICompatClient("orca", "https://x/v1", "m", "k", "sys")
    resp = requests.Response()
    resp.status_code, resp._content = 429, b'{"error": {"code": "free_rate_limited"}}'
    resp.headers["Retry-After"] = "9096"
    tries = []
    monkeypatch.setattr(client.session, "post", lambda *a, **kw: tries.append(1) or resp)
    monkeypatch.setattr(eligibility.time, "sleep", lambda s: pytest.fail("slept on an hours-long quota"))
    with pytest.raises(eligibility.QuotaExhausted):
        client.complete("### JOB J1")
    assert len(tries) == 1


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


LONG_TEXT = "We build billing software in Python and Django for hospitals. " * 12


@pytest.fixture
def ghost_store(tmp_path):
    from scanner.store import Store

    return Store(tmp_path / "s.sqlite")


def test_ghost_signals_add_up(cfg, ghost_store):
    from scanner.ghost import GhostRules

    ghosts = GhostRules(cfg["rules"]["ghost"], ghost_store.role_history)
    real = make_job(description=LONG_TEXT)
    assert ghosts.score(real) == (0, [])

    evergreen = make_job(description=LONG_TEXT, reality={"class": "likely-evergreen", "repost_count": 1})
    assert ghosts.score(evergreen)[0] >= ghosts.drop_at

    # an aggregator copy alone is not a ghost; re-posted by a third party with its page chrome is
    agg = make_job(id="torre::abc", description=LONG_TEXT)
    assert ghosts.score(agg)[0] < ghosts.flag_at
    scraped = make_job(id="torre::abd", description=LONG_TEXT + " Similar jobs Real-time AI thinking Match and rank")
    assert ghosts.score(scraped)[0] >= ghosts.drop_at

    pipeline = make_job(description=LONG_TEXT + " This is an evergreen requisition.")
    score, signals = ghosts.score(pipeline)
    assert ghosts.flag_at <= score < ghosts.drop_at and signals[0].startswith("evergreen_text")


def test_ghost_reposts_and_mass_posting_come_from_history(cfg, ghost_store):
    from scanner.ghost import GhostRules

    ghosts = GhostRules(cfg["rules"]["ghost"], ghost_store.role_history)
    for i, age in enumerate([40, 25]):  # the same role posted twice before under new ids
        ghost_store.upsert(make_job(id=f"lever:acme:old{i}", first_seen_at=NOW - timedelta(days=age)), "2026-10-15")
    for i in range(5):  # and sprayed five times now
        ghost_store.upsert(make_job(id=f"lever:acme:now{i}", first_seen_at=NOW - timedelta(days=1)), "2026-10-15")
    job = make_job(id="lever:acme:new", description=LONG_TEXT)
    assert ghost_store.role_history(job) == (2, 6)
    score, signals = ghosts.score(job)
    assert score >= ghosts.drop_at
    assert [s.split(" ")[0] for s in signals] == ["reposted", "mass_posted"]


def test_same_job_under_two_company_names_is_reported_once(ghost_store):
    brand = make_job(id="workday:verisk:1", company="Verisk", company_key="verisk", description=LONG_TEXT)
    tenant = make_job(id="oracle:fa-ewmy:1", url="https://fa-ewmy.oraclecloud.com/1", company="fa-ewmy",
                      company_key="fa-ewmy", description=LONG_TEXT)
    ghost_store.mark_reported(brand, "2026-10-15")
    assert "same title+text" in ghost_store.already_reported(tenant)


def test_liveness_reads_each_ats_and_only_trusts_clear_answers(monkeypatch):
    from scanner import liveness

    calls = []

    class Resp:
        def __init__(self, code, body=None):
            self.status_code, self.ok, self._body = code, code < 400, body

        def json(self):
            return self._body

    def fake_get(url, **kw):
        calls.append(url)
        if "ashbyhq" in url:
            return Resp(200, {"jobs": [{"id": "open-id"}]})
        if url.endswith("/gone"):
            return Resp(404)
        if url.endswith("/busy"):
            return Resp(429)
        return Resp(200)

    monkeypatch.setattr(liveness.requests, "get", fake_get)
    liveness._ashby_board.cache_clear()
    assert liveness.check(make_job(id="greenhouse:acme:gone")) == liveness.CLOSED
    assert calls[-1] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/gone"
    assert liveness.check(make_job(id="lever:acme:busy")) == liveness.UNKNOWN
    assert liveness.check(make_job(id="ashby:acme:open-id")) == liveness.OPEN
    assert liveness.check(make_job(id="ashby:acme:other-id")) == liveness.CLOSED
    assert liveness.check(make_job(id="himalayas::x", url="https://himalayas.app/x/gone")) == liveness.CLOSED


@pytest.mark.parametrize("title", ["Software Engineer (GitHub Contributor)", "Senior Software Engineer | AI Evaluation",
                                   "Marketing Specialist (Backend & Digital Ads)", "Open Application - Backend Engineer",
                                   "Senior Back-End Developer (RPG / IBM i)"])
def test_gig_and_non_engineering_titles_dropped(filters, title):
    assert filters.evaluate(make_job(title=title)) is not None


def test_task_paid_ai_lab_gigs_dropped(filters):
    gig = make_job(description="Python experts to work with a leading AI lab. Task-based compensation, 100 openings.")
    assert "ai_gig" in filters.evaluate(gig)
