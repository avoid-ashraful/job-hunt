"""Polite board discovery and cache retries, with no network or real sleeps."""

import json
import subprocess
import sys
from datetime import date
from email.utils import formatdate
from pathlib import Path

import pytest
import requests
import yaml

from scanner import board_owner, watchlist
from scanner.sources import ats, _more_ats


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ats, "_NEXT_REQUEST", {})
    monkeypatch.setattr(ats, "_BACKOFF", {})
    monkeypatch.setattr(ats, "_BLOCKED", {})
    monkeypatch.setattr(ats.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ats.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def response(code=200, data=None, text=None, headers=None, url="https://example.com"):
    resp = requests.Response()
    resp.status_code = code
    resp.url = url
    resp.headers.update(headers or {})
    resp._content = (text if text is not None else json.dumps(data)).encode()
    return resp


class Session:
    def __init__(self, routes=None):
        self.headers = {}
        self.routes = routes or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        replies = self.routes.get(url, [response(404)])
        resp = replies.pop(0) if len(replies) > 1 else replies[0]
        resp.url = url
        return resp

    def close(self):
        pass


def test_workday_post_honors_a_block_set_by_a_detail_request(clock):
    url, _ = ats._base("workday", "acme|wd5|External")
    ats._feedback(url, response(429, headers={"Retry-After": "3600"}))
    calls = []

    class WorkdaySession:
        def post(self, target, **kwargs):
            calls.append(target)
            return response(data={"jobPostings": [], "total": 0})

    with pytest.raises(requests.HTTPError) as err:
        _more_ats.rows(WorkdaySession(), "workday", "acme|wd5|External")
    assert err.value.response.status_code == 429
    assert calls == []


def test_workable_markdown_detail_honors_a_quota_block(clock):
    url = "https://apply.workable.com/acme/jobs/view/ABC.md"
    ats._feedback(url, response(429, headers={"Retry-After": "3600"}))
    session = Session({url: [response(text="Build Python services")]})
    raw = {"shortcode": "ABC", "title": "Python Engineer", "location": "Remote",
           "url": url.removesuffix(".md"), "_markdown_detail": url}
    with pytest.raises(requests.HTTPError) as err:
        ats._record(raw, {"company": "Acme", "ats": "workable", "board": "acme"}, {}, session)
    assert err.value.response.status_code == 429
    assert session.calls == []


def test_interrupted_watchlist_save_preserves_the_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.yml"
    original = [{"company": "Acme", "ats": "lever", "board": "acme"}]
    watchlist._write(path, original)
    before = path.read_bytes()

    def partial_write(target, contents, *args, **kwargs):
        target.write_bytes(contents[:10].encode())
        raise OSError("disk full during save")

    monkeypatch.setattr(Path, "write_text", partial_write)
    with pytest.raises(OSError, match="disk full"):
        watchlist._write(path, original + [{"company": "New", "ats": "ashby", "board": "new"}])
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("getter", [ats.get, ats.get_text])
def test_429_retries_through_shared_pacing(getter, clock):
    url = "https://api.lever.co/test"
    session = Session({url: [response(429), response(data={"jobs": []})]})
    getter(session, url, retries=2)
    assert session.calls == [url, url]
    assert clock[0] == 1005
    assert ats._BACKOFF["lever.co"] == 2.5


@pytest.mark.parametrize("header, expected", [(None, 5), ("invalid", 5), ("120", 60), ("3", 3), ("-1", 0)])
def test_retry_after_bounds(header, expected, clock):
    ats._feedback("https://acme.recruitee.com", response(429, headers={"Retry-After": header} if header else {}))
    assert ats._NEXT_REQUEST["recruitee.com"] == 1000 + expected


def test_retry_after_http_date(monkeypatch, clock):
    monkeypatch.setattr(ats.time, "time", lambda: 1700000000)
    ats._feedback("https://api.lever.co", response(429, headers={"Retry-After": formatdate(1700000030, usegmt=True)}))
    assert ats._NEXT_REQUEST["lever.co"] == 1030


def test_backoff_grows_caps_and_decays(clock):
    url = "https://api.lever.co/test"
    delays = []
    for _ in range(6):
        ats._feedback(url, response(429))
        delays.append(ats._BACKOFF["lever.co"])
    assert delays == [5, 10, 20, 40, 60, 60]
    for _ in range(9):
        ats._feedback(url, response())
    assert "lever.co" not in ats._BACKOFF


def test_missing_slugs_decay_backoff_without_bypassing_current_wait(clock):
    url = "https://acme.jobs.personio.de/xml"
    ats._feedback(url, response(429))
    ats._pace(url)
    assert clock[0] == 1005
    ats._feedback(url, response(404))
    assert ats._BACKOFF["personio.de"] == 2.5
    ats._pace(url)
    assert clock[0] == 1010
    for _ in range(4):
        ats._feedback(url, response(404))
    assert "personio.de" not in ats._BACKOFF
    ats._pace(url)
    assert clock[0] == 1012.5
    ats._pace(url)
    assert clock[0] == 1012.75


@pytest.mark.parametrize("code", [403, 429, 500, 503])
def test_errors_do_not_decay_provider_backoff(code, clock):
    url = "https://acme.jobs.personio.de/xml"
    ats._feedback(url, response(429))
    ats._feedback(url, response(code))
    assert ats._BACKOFF["personio.de"] >= 5


def test_waiting_thread_rechecks_provider_backoff(monkeypatch, clock):
    ats._pace("https://one.recruitee.com")
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) == 1:
            ats._feedback("https://one.recruitee.com", response(429))

    monkeypatch.setattr(ats.time, "sleep", sleep)
    ats._pace("https://two.recruitee.com")
    assert sleeps == [0.25, 5]
    ats._pace("https://api.lever.co")
    assert sleeps == [0.25, 5]


def test_5xx_retries(clock):
    url = "https://api.lever.co/test"
    session = Session({url: [response(503), response(data=[])]})
    assert ats.get(session, url, retries=2) == []
    assert clock[0] == 1001
    assert not ats._BACKOFF


@pytest.fixture
def one_slug(monkeypatch):
    monkeypatch.setattr(watchlist, "_slugs", lambda company: ["acme"])


@pytest.mark.parametrize("code, reason", [(200, "verified slug"), (404, "no supported ATS board found"), (429, "rate limited while probing a board")])
def test_resolution_after_429_retry(code, reason, one_slug):
    url = ats._base("greenhouse", "acme")[0]
    session = Session({
        url: [response(429), response(code, data={"jobs": [{"id": 1}]})],
        "https://boards-api.greenhouse.io/v1/boards/acme": [response(data={"name": "Acme"})],
        "https://job-boards.greenhouse.io/acme": [response(text="<title>Jobs at Acme</title>")],
    })
    result = watchlist.resolve_company({"company": "Acme"}, session)
    assert result["reason"] == reason
    assert session.calls.count(url) == 2
    if code == 429:
        assert result["retry_probes"] == [["greenhouse", "acme"]]
    else:
        assert "retry_probes" not in result


@pytest.mark.parametrize("code", [429, 503])
def test_ownership_rate_limit_is_distinct_from_unreadable_page(code, one_slug):
    session = Session({
        ats._base("lever", "acme")[0]: [response(data=[{"id": 1}])],
        "https://jobs.lever.co/acme": [response(code)],
    })
    result = watchlist.resolve_company({"company": "Acme"}, session)
    assert ("rate limited" in result["reason"]) is (code == 429)
    if code == 429:
        assert result["retry_probes"] == [["lever", "acme"]]
    assert session.calls.count("https://jobs.lever.co/acme") == 2


def test_board_owner_retries_429_then_accepts_alias():
    url = "https://jobs.lever.co/acme"
    session = Session({url: [response(429), response(text="<title>Jobs at Other Name</title>")]})
    assert board_owner.owns(session, "lever", "acme", "Acme", aliases=["Other Name"])
    assert session.calls == [url, url]
    assert watchlist.company_match("Stack Overflow", "Stack Exchange", aliases=["Stack Overflow"])
    assert not watchlist.company_match("Unrelated", "Stack Exchange", aliases=["Stack Overflow"])


def test_alias_reaches_board_validation_and_override_skips_guessing():
    url = ats._base("greenhouse", "exact-board")[0]
    session = Session({
        url: [response(data={"jobs": [{"id": 1}]})],
        "https://boards-api.greenhouse.io/v1/boards/exact-board": [response(data={"name": "Stack Overflow"})],
    })
    result = watchlist.resolve_company({"company": "Stack Exchange", "website": "https://example.com",
                                        "ats": "greenhouse", "board": "exact-board", "aliases": ["Stack Overflow"]}, session)
    assert result == {"ats": "greenhouse", "board": "exact-board", "reason": "seed override"}
    assert len(session.calls) == 2


def test_invalid_override_is_validated_without_guessing():
    session = Session()
    result = watchlist.resolve_company({"company": "Acme", "ats": "lever", "board": "exact"}, session)
    assert result["reason"] == "no supported ATS board found"
    assert session.calls == [ats._base("lever", "exact")[0]]


def test_guess_order_and_normalized_url_deduplication(monkeypatch):
    calls = []
    monkeypatch.setattr(watchlist, "_slugs", lambda company: ["Acme", "acme", "acme"])
    monkeypatch.setattr(watchlist, "_valid_board", lambda s, kind, slug, **kw: calls.append((kind, slug)) or False)
    watchlist.resolve_company({"company": "Acme"}, Session())
    kinds = list(dict.fromkeys(kind for kind, slug in calls))
    assert kinds == list(watchlist.ATS_ORDER)
    assert set(kinds) == set(watchlist.ATS_LINKS) - {"workable", "workday", "comeet"}
    assert calls.count(("recruitee", "Acme")) == 1
    assert ("recruitee", "acme") not in calls
    assert calls.count(("greenhouse", "acme")) == 1  # URL paths retain case.


@pytest.mark.parametrize("company, expected", [
    ({"company": "Acme Labs", "website": "https://www.acme.io"}, ["acme-labs", "acmelabs", "acme"]),
    ({"company": "Acme, Inc.", "website": "https://acme.io"}, ["acme", "acmeinc"]),
    ({"company": "Acme HQ"}, ["acme-hq", "acmehq"]),
    ({"company": "A", "website": "https://example.com"}, ["example"]),
])
def test_slug_variants_keep_name_domain_and_literal_hq(company, expected):
    assert watchlist._slugs(company) == expected


def test_ats_link_scan_handles_large_embedded_tokens():
    subprocess.run(
        [sys.executable, "-c", "from scanner.watchlist import _links; assert _links('a' * 100_000) == []"],
        check=True, timeout=5,
    )


@pytest.mark.parametrize("host, kind", [
    ("acme.applytojob.com/apply", "jazzhr"), ("acme.homerun.co", "homerun"),
    ("acme.factorialhr.com", "factorial"), ("acme.workable.com", "workable"),
    ("acme.recruitee.com", "recruitee"), ("acme.breezy.hr", "breezy"),
    ("acme.jobs.personio.de", "personio"), ("acme.bamboohr.com", "bamboohr"),
    ("acme.pinpointhq.com", "pinpoint"), ("acme.teamtailor.com", "teamtailor"),
])
def test_subdomain_links_preserve_complete_labels(host, kind):
    assert watchlist._links(f'<a href="https://{host}">Jobs</a>') == [(kind, "acme")]


def seed_file(tmp_path, companies):
    path = tmp_path / "seeds.yml"
    path.write_text(yaml.safe_dump({"companies": companies}))
    return path


def test_build_retries_only_unresolved_probes_on_same_day(tmp_path, monkeypatch, one_slug):
    seeds = seed_file(tmp_path, [{"company": "Acme"}])
    url = ats._base("greenhouse", "acme")[0]
    session = Session({url: [response(429)]})
    monkeypatch.setattr(watchlist.requests, "Session", lambda: session)
    args = (tmp_path / "vendor", tmp_path / "watch.yml", tmp_path / "cache")
    watchlist.build(*args, seeds_file=seeds, workers=1)
    cached = json.loads((args[2] / "acme.json").read_text())
    assert cached["status"] == "limited" and cached["attempts"] == 0
    session.calls.clear()
    session.routes[url] = [response(404)]
    watchlist.build(*args, seeds_file=seeds, workers=1)
    assert session.calls == [url]
    assert json.loads((args[2] / "acme.json").read_text())["status"] == "miss"
    session.calls.clear()
    watchlist.build(*args, seeds_file=seeds, workers=1)
    assert session.calls == []


@pytest.mark.parametrize("status", ["dead", "transient", "limited"])
def test_old_limited_cache_never_dead_and_runs_after_fresh(tmp_path, monkeypatch, status):
    seeds = seed_file(tmp_path, [{"company": "LimitedCo"}, {"company": "Fresh"}])
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / "limitedco.json"
    cached.write_text(json.dumps({"status": status, "attempts": 99, "last_attempt": date.today().isoformat(),
                                  "reason": "rate limited while probing a board", "retry_probes": [["lever", "limited"]]}))
    calls = []

    def resolve(seed, hints=None):
        calls.append(seed["company"])
        return {"reason": "rate limited while probing a board", "retry_probes": [["lever", "limited"]]}

    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    for _ in range(2):
        watchlist.build(tmp_path / "vendor", tmp_path / "watch.yml", cache, seeds_file=seeds, workers=1)
        assert json.loads(cached.read_text())["status"] == "limited"
    assert calls == ["Fresh", "LimitedCo", "LimitedCo", "Fresh"]


def test_trusted_link_remains_trusted_when_retried():
    url = ats._base("lever", "acme")[0]
    site = "https://acme.com"
    session = Session({site: [response(text='<a href="https://jobs.lever.co/acme">Jobs</a>')], url: [response(429)]})
    first = watchlist.resolve_company({"company": "Acme", "website": site}, session)
    assert first["retry_trusted"] == [["lever", "acme"]]
    session.calls.clear()
    session.routes[url] = [response(data=[{"id": 1}])]
    result = watchlist.resolve_company({"company": "Acme", **first}, session)
    assert result["reason"] == "careers link"
    assert session.calls == [url]


def test_build_merges_vendor_override_and_ignores_stale_miss(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "acme.md").write_text('---\ntitle: Acme\nregion: worldwide\n---\n')
    seeds = seed_file(tmp_path, [{"company": "Acme", "ats": "lever", "board": "exact", "aliases": ["Other Name"]}])
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "acme.json").write_text(json.dumps({"reason": "miss", "status": "miss", "resolver_version": 2}))

    def resolve(seed, hints=None):
        assert seed["aliases"] == ["Other Name"]
        assert seed["ats"] == "lever" and seed["board"] == "exact"
        return {"ats": "lever", "board": "exact", "reason": "seed override"}

    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    path = tmp_path / "watch.yml"
    assert watchlist.build(vendor, path, cache, seeds_file=seeds)["resolved"] == 1
    entry = watchlist.list_entries(path)[0]
    assert entry["aliases"] == ["Other Name"]
    assert "_ats_order" not in entry


def test_existing_boards_determine_provider_order(tmp_path, monkeypatch):
    seeds = seed_file(tmp_path, [{"company": "Acme"}])
    path = tmp_path / "watch.yml"
    path.write_text(yaml.safe_dump({"companies": [{"company": "Known", "ats": "lever", "board": "known"}]}))

    def resolve(seed, hints=None):
        assert seed["_ats_order"][0] == "lever"
        return {"reason": "no supported ATS board found"}

    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    watchlist.build(tmp_path / "vendor", path, tmp_path / "cache", seeds_file=seeds)


@pytest.mark.parametrize("reason, expected", [("careers page: ConnectionError", True), ("1-hop page: ReadTimeout", True),
                                              ("resolver error: Timeout", False), ("rate limited", False)])
def test_only_website_network_failures_count_toward_dead(reason, expected):
    assert watchlist._website_failure(reason) is expected


@pytest.mark.parametrize("fallback_code", [200, 404, 429])
def test_workable_fallback_preserves_an_unresolved_widget(fallback_code):
    session = Session({
        ats._base("workable", "acme")[0]: [response(429)],
        "https://apply.workable.com/acme/jobs.md": [response(fallback_code, text="No jobs")],
    })
    assert watchlist._valid_board(session, "workable", "acme") is None


def test_workable_fallback_can_resolve_limited_widget():
    table = ("| Backend Engineer | Eng | Remote | Full-time | — | 2026-10-16 | "
             "[View](https://apply.workable.com/acme/jobs/view/ABC123.md) |")
    session = Session({
        ats._base("workable", "acme")[0]: [response(429)],
        "https://apply.workable.com/acme/jobs.md": [response(text=table)],
    })
    assert watchlist._valid_board(session, "workable", "acme") is True


def test_workable_fallback_429_is_recorded_after_widget_404():
    session = Session({"https://apply.workable.com/acme/jobs.md": [response(429)]})
    assert watchlist._valid_board(session, "workable", "acme") is None


def test_pinpoint_ownership_records_postings_rate_limit():
    session = Session({
        "https://acme.pinpointhq.com/": [response(text="<title>Jobs at Acme</title>")],
        "https://acme.pinpointhq.com/postings.json": [response(429)],
    })
    with pytest.raises(requests.HTTPError) as err:
        board_owner.owns(session, "pinpoint", "acme", "Acme", raise_on_limit=True)
    assert err.value.response.status_code == 429


def test_shared_page_link_requires_ownership_without_aggregator_website(one_slug):
    site = "https://himalayas.app/companies/acme"
    session = Session({
        site: [response(text='<a href="https://jobs.lever.co/acme">Jobs</a>')],
        ats._base("lever", "acme")[0]: [response(data=[{"id": 1}])],
        "https://jobs.lever.co/acme": [response(text='<title>Another Company</title><a href="https://himalayas.app">Jobs</a>')],
    })
    result = watchlist.resolve_company({"company": "Acme", "website": site}, session)
    assert result["reason"] == "no supported ATS board found"


def test_limited_override_is_deferred_on_next_build(tmp_path, monkeypatch):
    seeds = seed_file(tmp_path, [{"company": "Acme", "ats": "lever", "board": "exact"}, {"company": "Fresh"}])
    cache = tmp_path / "cache"
    watch_file = tmp_path / "watch.yml"
    calls = []

    def resolve(seed, hints=None):
        calls.append(seed["company"])
        return {"reason": "rate limited while probing a board", "retry_probes": [["lever", "exact"]]}

    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    watchlist.build(tmp_path / "vendor", watch_file, cache, seeds_file=seeds, workers=1)
    assert json.loads((cache / "acme.json").read_text())["seed_override"] == ["lever", "exact"]
    (cache / "fresh.json").unlink()
    calls.clear()
    watchlist.build(tmp_path / "vendor", watch_file, cache, seeds_file=seeds, workers=1)
    assert calls == ["Fresh", "Acme"]


def test_changed_override_removes_old_board_when_validation_fails(tmp_path, monkeypatch):
    seeds = seed_file(tmp_path, [{"company": "Acme", "ats": "lever", "board": "new"}])
    watch_file = tmp_path / "watch.yml"
    watch_file.write_text(yaml.safe_dump({"companies": [{"company": "Acme", "ats": "lever", "board": "old"}]}))
    monkeypatch.setattr(watchlist, "resolve_company", lambda seed, hints=None: {"reason": "no supported ATS board found"})
    watchlist.build(tmp_path / "vendor", watch_file, tmp_path / "cache", seeds_file=seeds)
    assert watchlist.list_entries(watch_file) == []


def test_existing_board_receives_seed_aliases(tmp_path):
    seeds = seed_file(tmp_path, [{"company": "Acme", "aliases": ["Other Name"]}])
    path = tmp_path / "watch.yml"
    path.write_text(yaml.safe_dump({"companies": [{"company": "Acme", "ats": "lever", "board": "exact"}]}))
    watchlist.build(tmp_path / "vendor", path, tmp_path / "cache", seeds_file=seeds)
    assert watchlist.list_entries(path)[0]["aliases"] == ["Other Name"]


@pytest.mark.parametrize("kind", ["lever", "lever_eu", "smartrecruiters"])
def test_validation_fetches_only_one_posting(monkeypatch, kind):
    def get(session, url, **kwargs):
        assert kwargs["params"]["limit"] == 1
        return [{"id": 1}] if kind.startswith("lever") else {"content": [{"id": 1}]}

    monkeypatch.setattr(watchlist, "get", get)
    assert watchlist._valid_board(Session(), kind, "acme")


def test_verify_owners_uses_seed_aliases(tmp_path, monkeypatch):
    cached = tmp_path / "acme.json"
    cached.write_text(json.dumps({"ats": "lever", "board": "acme", "reason": "verified slug"}))
    entries = {"acme": {"company": "Acme", "ats": "lever", "board": "acme", "aliases": ["Other Name"]}}
    session = Session({"https://jobs.lever.co/acme": [response(text="<title>Jobs at Other Name</title>")]})
    monkeypatch.setattr(watchlist.requests, "Session", lambda: session)
    assert watchlist.verify_owners(entries, tmp_path)["kept"] == 1


@pytest.mark.parametrize("header, delay", [("5", 5), ("invalid", 1)])
def test_5xx_retains_retry_after_handling(header, delay, clock):
    url = "https://api.lever.co/test"
    session = Session({url: [response(503, headers={"Retry-After": header}), response(data=[])]})
    assert ats.get(session, url, retries=2) == []
    assert clock[0] == 1000 + delay


def test_quota_window_fails_fast_instead_of_stalling(clock):
    widget = "https://apply.workable.com/api/v1/widget/accounts/a"
    session = Session({widget: [response(429, headers={"Retry-After": "45810"})]})
    with pytest.raises(requests.HTTPError):
        ats.get(session, widget, retries=3)
    assert session.calls == [widget]  # no retry into a 12-hour quota
    other = "https://apply.workable.com/api/v1/widget/accounts/b"
    with pytest.raises(requests.HTTPError):
        ats.get(session, other)
    assert session.calls == [widget] and clock[0] < 1010
    search = "https://jobs.workable.com/api/v1/jobs"  # separate service, separate quota
    session.routes[search] = [response(data={"jobs": []})]
    ats.get(session, search)
    assert session.calls[-1] == search
    clock[0] += 50000
    session.routes[other] = [response(data={"jobs": []})]
    ats.get(session, other)
    assert session.calls[-1] == other


def test_retry_skips_guesses_for_ats_no_longer_guessed():
    session = Session()
    result = watchlist.resolve_company({"company": "Acme", "retry_probes": [["workable", "acme"]], "retry_trusted": []}, session)
    assert session.calls == [] and result == {"reason": "no supported ATS board found"}


def test_thread_waiting_in_pace_sees_a_new_block(monkeypatch, clock):
    url = "https://apply.workable.com/api/v1/widget/accounts/a"
    ats._NEXT_REQUEST["apply.workable.com"] = clock[0] + 60
    blocked = response(429)

    def sleep(seconds):  # another thread gets a 12-hour Retry-After while this one waits
        clock[0] += seconds
        ats._BLOCKED["apply.workable.com"] = (clock[0] + 40000, blocked)

    monkeypatch.setattr(ats.time, "sleep", sleep)
    session = Session()
    got = ats._request(session, url)
    assert got.status_code == 429 and got.url == url and session.calls == []
