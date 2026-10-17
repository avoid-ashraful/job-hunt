"""Board health and weekly retirement without network or private state."""

import json
from datetime import date, timedelta

import pytest
import requests
import yaml

from scanner import board_health, watchlist
from scanner.sources import ats


ENTRY = {"company": "Example", "ats": "ashby", "board": "example", "website": "https://example.com",
         "aliases": ["Example Inc"], "added": "2026-01-01"}


def fail(status):
    response = requests.Response()
    response.status_code = status
    raise requests.HTTPError(str(status), response=response)


@pytest.mark.parametrize("status", [404, 410, 429, 503])
def test_failed_fetch_health(tmp_path, monkeypatch, status):
    monkeypatch.setattr(ats, "get", lambda *a, **kw: fail(status))
    for _ in range(3):
        assert ats.fetch_board(ENTRY, {"health_dir": str(tmp_path)}) == []
    health = board_health.read(tmp_path, "ashby", "example")
    assert health["not_found_failures"] == (3 if status in (404, 410) else 0)
    assert health["other_failures"] == (0 if status in (404, 410) else 3)
    assert board_health.stale(health) == (status in (404, 410))


@pytest.mark.parametrize("trigger", ["404", "empty", "429"])
def test_build_health_reresolution(tmp_path, monkeypatch, trigger, capsys):
    path = tmp_path / "watch.yml"
    watchlist._write(path, [ENTRY])
    seeds = tmp_path / "seeds.yml"
    seeds.write_text(yaml.safe_dump({"companies": [ENTRY]}))
    cache = tmp_path / "watchlist_resolution"
    cache.mkdir()
    (cache / "example.json").write_text(json.dumps({"ats": "ashby", "board": "example"}))
    health_dir = tmp_path / "watchlist_health"
    if trigger == "empty":
        board_health.record(health_dir, "ashby", "example", jobs=0,
                            today=(date.today() - timedelta(days=30)).isoformat())
        board_health.record(health_dir, "ashby", "example", jobs=0)
    else:
        for _ in range(3):
            board_health.record(health_dir, "ashby", "example", error=int(trigger))
    calls = []

    def resolve(seed, hints=None):
        calls.append(seed)
        assert "ats" not in seed and "board" not in seed
        assert seed["website"] == ENTRY["website"]
        assert seed["aliases"] == ENTRY["aliases"]
        assert not (cache / "example.json").exists()
        return {"ats": "greenhouse", "board": "new", "reason": "careers link"}

    monkeypatch.setattr(watchlist, "resolve_company", resolve)
    stats = watchlist.build(tmp_path / "vendor", path, cache, seeds_file=seeds)
    expected = int(trigger != "429")
    assert len(calls) == stats["re_resolved"] == stats["moved"] == expected
    assert watchlist.list_entries(path)[0]["ats"] == ("greenhouse" if expected else "ashby")
    assert f"re-resolved {expected}" in capsys.readouterr().out


def test_inactive_history_retried_and_not_polled(tmp_path, monkeypatch):
    path = tmp_path / "watch.yml"
    watchlist._write(path, [ENTRY])
    for _ in range(3):
        board_health.record(tmp_path / "watchlist_health", "ashby", "example", error=404)
    calls = []
    monkeypatch.setattr(watchlist, "resolve_company", lambda seed, hints=None: calls.append(seed) or {"reason": "no board"})
    for _ in range(2):
        stats = watchlist.build(tmp_path / "vendor", path, tmp_path / "watchlist_resolution")
        assert stats["inactive"] == 1
    assert len(calls) == 2
    entry = watchlist.list_entries(path)[0]
    assert entry["status"] == "inactive" and entry["inactive_since"] == date.today().isoformat()
    assert entry["website"] == ENTRY["website"] and entry["added"] == ENTRY["added"]
    assert "ats" not in entry and "board" not in entry
    monkeypatch.setattr(ats, "fetch_board", lambda *a: pytest.fail("inactive board polled"))
    assert list(ats.fetch({"watchlist_file": str(path)}, 1)) == []


def test_fetch_without_health_store(tmp_path, monkeypatch):
    path = tmp_path / "watch.yml"
    watchlist._write(path, [ENTRY])
    monkeypatch.setattr(ats, "get", lambda *a, **kw: {"jobs": []})
    assert list(ats.fetch({"watchlist_file": str(path)}, 1)) == []
    monkeypatch.setattr(ats, "get", lambda *a, **kw: fail(404))
    assert list(ats.fetch({"watchlist_file": str(path)}, 1)) == []
    assert list(tmp_path.iterdir()) == [path]


def test_health_counts_unfiltered_jobs_and_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(ats, "get", lambda *a, **kw: {"jobs": [{"title": "Marketing Manager"}]})
    board_health.record(tmp_path, "ashby", "example", jobs=0,
                        today=(date.today() - timedelta(days=30)).isoformat())
    board_health.record(tmp_path, "ashby", "example", error=404)
    assert ats.fetch_board(ENTRY, {"health_dir": str(tmp_path)}) == []
    health = board_health.read(tmp_path, "ashby", "example")
    assert health["jobs"] == 1 and health["last_ok"] == date.today().isoformat()
    assert health["first_empty"] is None and health["not_found_failures"] == 0
    assert not board_health.stale(health)


def test_transient_breaks_404_streak_and_preserves_last_success(tmp_path):
    board_health.record(tmp_path, "ashby", "example", jobs=5)
    for _ in range(2):
        board_health.record(tmp_path, "ashby", "example", error=404)
    board_health.record(tmp_path, "ashby", "example", error=429)
    board_health.record(tmp_path, "ashby", "example", error=404)
    health = board_health.read(tmp_path, "ashby", "example")
    assert health["jobs"] == 5 and health["not_found_failures"] == 1
    assert not board_health.stale(health)


def test_unwritable_health_store_keeps_fetch_working(tmp_path, monkeypatch):
    path = tmp_path / "not-a-directory"
    path.write_text("occupied")
    monkeypatch.setattr(ats, "get", lambda *a, **kw: {"jobs": []})
    assert ats.fetch_board(ENTRY, {"health_dir": str(path)}) == []


def test_watchlist_list_shows_health(tmp_path, monkeypatch, capsys):
    import sys
    from scanner import cli

    path = tmp_path / "watch.yml"
    watchlist._write(path, [ENTRY])
    board_health.record(tmp_path / "watchlist_health", "ashby", "example", jobs=7)
    board_health.record(tmp_path / "watchlist_health", "ashby", "example", error=404)
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump({"sources": {"ats": {"watchlist_file": str(path)}},
                                     "output": {"state_dir": str(tmp_path)}}))
    monkeypatch.setattr(sys, "argv", ["scanner", "--config", str(config), "watchlist", "list"])
    cli.main()
    output = capsys.readouterr().out
    assert "Last ok\tJobs\tFailures" in output
    assert f"{date.today().isoformat()}\t7\t1/0" in output
