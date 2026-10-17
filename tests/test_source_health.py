import json
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import yaml

from scanner import cli, sources
from scanner.models import Job
from scanner.store import Store


def test_run_records_source_health(tmp_path, monkeypatch, caplog):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yml").read_text())
    cfg["sources"] = {"broken": {"enabled": True}, "freehire": {"enabled": True},
                      "empty": {"enabled": True}, "disabled": {"enabled": False}}
    cfg["output"] = {"jobs_dir": "jobs", "state_dir": "state"}
    cfg["liveness"]["enabled"] = False
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg, sort_keys=False))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    job = Job(id="test:1", source="freehire", url="https://example.com/job", company="Example",
              company_key="example", title="Sales manager", description="Sales")

    def broken(cfg, days):
        yield job
        raise RuntimeError("HTTP 403\nblocked")

    modules = {"broken": SimpleNamespace(fetch=broken),
               "freehire": SimpleNamespace(fetch=lambda cfg, days: iter([job])),
               "empty": SimpleNamespace(fetch=lambda cfg, days: iter([]))}
    monkeypatch.setattr(sources.importlib, "import_module", lambda name: modules[name.rsplit(".", 1)[1]])
    clock = iter([0, 3, 3, 7, 7, 9])
    monkeypatch.setattr(sources.time, "perf_counter", lambda: next(clock))
    with caplog.at_level(logging.INFO, logger="scanner.sources"):
        report = cli.run(config, dry_run=False, days=1, skip_llm=True)

    db = sqlite3.connect(tmp_path / "state" / "scanner.sqlite")
    stats = json.loads(db.execute("SELECT source_stats FROM runs").fetchone()[0])
    assert stats == [
        {"name": "broken", "count": 1, "seconds": 3, "error": "RuntimeError: HTTP 403\nblocked"},
        {"name": "freehire", "count": 1, "seconds": 4, "error": None},
        {"name": "empty", "count": 0, "seconds": 2, "error": None},
    ]
    assert report.read_text().rstrip().splitlines()[-1] == (
        "Sources: broken failed: RuntimeError: HTTP 403 blocked ⚠ (3s) · freehire 1 (4s) · empty 0 ⚠ (2s)"
    )
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(infos) == 3
    assert all("\n" not in line for line in infos + warnings)
    assert "source freehire: 1 jobs (4.0s)" in infos
    assert len(warnings) == 2
    assert "source empty returned 0 jobs" in warnings
    assert any("source broken failed after 1 jobs: RuntimeError: HTTP 403" in line for line in warnings)


def test_source_import_failure_does_not_stop_next_source(monkeypatch):
    def import_module(name):
        if name.endswith("missing"):
            raise ModuleNotFoundError("missing plugin")
        return SimpleNamespace(fetch=lambda cfg, days: iter([]))

    monkeypatch.setattr(sources.importlib, "import_module", import_module)
    stats = []
    assert list(sources.fetch_all({"missing": {"enabled": True}, "empty": {"enabled": True}}, 1, stats)) == []
    assert [s["name"] for s in stats] == ["missing", "empty"]
    assert stats[0]["error"] == "ModuleNotFoundError: missing plugin"
    assert all(s["seconds"] >= 0 for s in stats)


def test_store_migrates_run_source_stats(tmp_path):
    path = tmp_path / "state.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE runs (run_at TEXT, fetched INTEGER, passed INTEGER, new INTEGER, "
               "reported INTEGER, report TEXT, deferred INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO runs (run_at) VALUES ('old run')")
    db.commit()
    db.close()
    store = Store(path)
    assert store.db.execute("SELECT source_stats FROM runs").fetchone() == ("[]",)
    store.log_run("new run", 0, 0, 0, 0, "report.md", 0)
    assert store.db.execute("SELECT source_stats FROM runs WHERE run_at='new run'").fetchone() == ("[]",)
