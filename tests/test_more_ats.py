"""Additional public ATS boards, using captured response shapes without network access."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from scanner import board_owner, liveness, watchlist
from scanner.sources import ats, _more_ats
from scanner.sources._public import ats_id

FIXTURES = Path(__file__).parent / "fixtures" / "ats"


@pytest.fixture(params=sorted(_more_ats.NAMES))
def board(request, monkeypatch):
    kind = request.param
    fixture = json.loads((FIXTURES / f"{kind}.json").read_text())
    calls = []
    url, _ = ats._base(kind, fixture["board"])

    class Session:
        headers = {}

        def get(self, target, **kwargs):
            calls.append(("GET", target, kwargs))
            data = fixture["response"] if target == url else fixture["detail"]
            assert data is not None, target
            return SimpleNamespace(json=lambda: data, text=data if isinstance(data, str) else "",
                                   status_code=200, headers={}, raise_for_status=lambda: None)

        def post(self, target, **kwargs):
            calls.append(("POST", target, kwargs))
            assert target == url
            return SimpleNamespace(json=lambda: fixture["response"], raise_for_status=lambda: None)

        def close(self):
            pass

    monkeypatch.setattr(ats.requests, "Session", Session)
    monkeypatch.setattr(ats, "_pace", lambda url: None)
    return kind, fixture, calls, Session


def test_adapter_jobs(board):
    kind, fixture, calls, _ = board
    jobs = ats.fetch_board({"ats": kind, "board": fixture["board"], "company": "Example"}, {})
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == fixture["expected"]["id"]
    assert job.title == "Backend Engineer"
    assert job.location == fixture["expected"]["location"]
    assert job.url == fixture["expected"]["url"]
    assert "Build Python & Django services." in job.description
    assert job.watchlist
    if kind != "factorial":
        assert job.posted_at.isoformat() == "2026-10-16T12:00:00+00:00"
    if kind == "workday":
        assert calls[0][2]["json"] == {"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}}


def test_board_discovery_and_public_ids(board):
    kind, fixture, _, _ = board
    assert (kind, fixture["board"]) in watchlist._links(f'<a href="{fixture["link"]}">Jobs</a>')
    assert watchlist._ats_from_id(fixture["expected"]["id"]) == (kind, fixture["board"])
    assert ats_id(fixture["expected"]["url"], "Example") == fixture["expected"]["id"]


def test_board_validation(board):
    kind, fixture, _, session = board
    assert watchlist._valid_board(session(), kind, fixture["board"])


def test_landing_and_liveness(board, monkeypatch):
    kind, fixture, _, _ = board
    calls = []

    def landing_get(session, url):
        calls.append(url)
        return SimpleNamespace(url=url, text="<title>Jobs at Example</title>", status_code=200, ok=True)

    monkeypatch.setattr(board_owner, "_get", landing_get)
    assert board_owner.owns(None, kind, fixture["board"], "Example")
    assert calls[0].startswith("https://") and "|" not in calls[0]
    monkeypatch.setattr(liveness, "_status", lambda url: calls.append(url) or liveness.OPEN)
    job = SimpleNamespace(**{"company": "Example", **fixture["expected"]})
    assert liveness.check(job) == liveness.OPEN
    if kind in {"workday", "rippling", "dover"}:
        assert "/api/" in calls[-1] or "/wday/cxs/" in calls[-1]
    else:
        assert calls[-1] == job.url


def test_wrong_titles_skip_details(board):
    kind, fixture, calls, _ = board
    jobs = ats.fetch_board({"ats": kind, "board": fixture["board"], "company": "Example"},
                           {"filters": {"title_exclude": ["Engineer"]}})
    assert jobs == []
    assert len(calls) == 1


def test_workday_pagination_is_capped(monkeypatch):
    offsets = []
    monkeypatch.setattr(ats, "_pace", lambda url: None)

    class Session:
        def post(self, url, json, **kwargs):
            offsets.append(json["offset"])
            page = [{"title": "Engineer", "externalPath": f"/job/Remote/R{i}"}
                    for i in range(json["offset"], json["offset"] + json["limit"])]
            return SimpleNamespace(json=lambda: {"total": 2000, "jobPostings": page}, raise_for_status=lambda: None)

    assert len(_more_ats.rows(Session(), "workday", "example|wd5|External", 999)) == 200
    assert offsets == list(range(0, 200, 20))


def test_workday_rate_limit_is_unknown(monkeypatch):
    class Session:
        headers = {}
        def post(self, *args, **kwargs):
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError(response=response)

    monkeypatch.setattr(ats, "_pace", lambda url: None)
    assert watchlist._valid_board(Session(), "workday", "example|wd5|External") is None


def test_homerun_detail_without_jsonld(monkeypatch):
    monkeypatch.setattr(ats, "get_text", lambda *a: '<div class="article"><p>Python services.</p></div>')
    detail = _more_ats.enrich(None, "homerun", "example", {"url": "https://example.homerun.co/backend", "location": "Remote"})
    assert ats.plain(detail["description"]) == "Python services."


def test_dover_slug_resolves_public_board_id(monkeypatch):
    calls = []
    fixture = json.loads((FIXTURES / "dover.json").read_text())

    def get(session, url, **kwargs):
        calls.append(url)
        return {"id": fixture["board"]} if len(calls) == 1 else fixture["response"]

    monkeypatch.setattr(ats, "get", get)
    rows = _more_ats.rows(None, "dover", "example")
    assert calls == ["https://app.dover.com/api/v1/careers-page-slug/example",
                     f'https://app.dover.com/api/v1/careers-page/{fixture["board"]}/jobs']
    assert rows[0]["url"] == "https://app.dover.com/apply/example/d1"
    assert ats_id(rows[0]["url"], "Example") == "dover:example:d1"


def test_workday_and_comeet_are_not_slug_guessed(monkeypatch):
    tried = []
    monkeypatch.setattr(watchlist, "_valid_board", lambda session, kind, board, **kw: tried.append(kind) or False)
    watchlist.resolve_company({"company": "Example"})
    assert "workday" not in tried and "comeet" not in tried
    assert {"rippling", "jazzhr", "homerun", "factorial", "dover"} <= set(tried)


def test_workday_liveness_accepts_localized_links(monkeypatch):
    urls = []
    monkeypatch.setattr(liveness, "_status", lambda url: urls.append(url) or liveness.OPEN)
    job = SimpleNamespace(id="workday:example|wd5|External:R1",
                          url="https://example.wd5.myworkdayjobs.com/en-US/External/job/Remote/R1")
    assert liveness.check(job) == liveness.OPEN
    assert urls == ["https://example.wd5.myworkdayjobs.com/wday/cxs/example/External/job/Remote/R1"]


def test_freehire_workday_ids_are_usable():
    from scanner import watchlist
    from scanner.sources import _more_ats

    fid = "workday:keybank.wd5.myworkdayjobs.com/External_Career_Site:/job/Remote-United-States/Sr-Engineer_R-1"
    assert watchlist._ats_from_id(fid) == ("workday", "keybank|wd5|External_Career_Site")
    assert _more_ats.base("workday", "keybank.wd5.myworkdayjobs.com/External_Career_Site")[0] == \
        "https://keybank.wd5.myworkdayjobs.com/wday/cxs/keybank/External_Career_Site/jobs"
    assert watchlist._ats_from_id("workday:garbage:1") is None


@pytest.mark.parametrize("job_id,expected", [
    ("comeet:Dario.Health/34.006:6E.F6D", ("comeet", "Dario.Health|34.006")),
    ("factorial:natulim.factorial.es:325580", ("factorial", "natulim")),
    ("comeet:bad:1", None),
])
def test_freehire_board_forms_are_normalized(job_id, expected):
    from scanner import watchlist
    assert watchlist._ats_from_id(job_id) == expected
