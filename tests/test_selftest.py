from collections import Counter
from datetime import datetime
from pathlib import Path
import os
import subprocess
import sys

import pytest
import yaml

from scanner import cli
from scanner.report import write_report
from scanner.selftest import RuleCases, load_cases, matches

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def runner():
    return RuleCases(yaml.safe_load((ROOT / "config.example.yml").read_text()))


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["name"])
def test_shared_rule_cases(runner, case):
    actual = runner.evaluate(case)
    assert matches(case, actual), f"{case['expected']=}, {actual=}"


@pytest.mark.parametrize("global_config", [False, True])
def test_selftest_command_passes_without_writing_state(tmp_path, global_config):
    cfg = yaml.safe_load((ROOT / "config.example.yml").read_text())
    cfg["output"]["state_dir"] = str(tmp_path / "must-not-exist")
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    args = ["--config", str(config)]
    args = args + ["selftest"] if global_config else ["selftest"] + args
    result = subprocess.run([sys.executable, "-m", "scanner", *args], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"selftest: {len(load_cases())} passed, 0 failed" in result.stdout
    assert not (tmp_path / "must-not-exist").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yml"]


def test_selftest_command_reports_failure_and_exact_count(tmp_path):
    cfg = yaml.safe_load((ROOT / "config.example.yml").read_text())
    cfg["rules"]["location"]["positive"].pop("ats_location_global")
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(cfg))
    count_file = tmp_path / "count"
    result = subprocess.run([sys.executable, "-m", "scanner", "selftest", "--config", str(config),
                             "--failure-count-file", str(count_file)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert "FAIL watchlist-global-location:" in result.stdout
    assert "expected {'outcome': 'positive', 'rule': 'positive.ats_location_global'}" in result.stdout
    assert "got {'outcome': 'silent', 'rule': 'silent'}" in result.stdout
    assert "rule=silent" in result.stdout
    assert f"{len(load_cases()) - 1} passed, 1 failed" in result.stdout
    assert count_file.read_text() == "1\n"


def test_selftest_defaults_to_private_config(monkeypatch):
    from scanner import selftest

    seen = []
    monkeypatch.setattr(selftest, "run", lambda path, count_file: seen.append((path, count_file)) or 0)
    monkeypatch.setattr(sys, "argv", ["scanner", "selftest"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    assert seen == [(cli.DEFAULT_CONFIG, None)]


@pytest.mark.parametrize("failed", [0, 3])
def test_report_selftest_footer(tmp_path, failed):
    report = tmp_path / "report.md"
    write_report(report, datetime(2026, 10, 17), [],
                 dict(fetched=0, passed=0, ambiguous=0, llm_calls=0, selftest_failed=failed),
                 Counter(), "Bangladesh")
    text = report.read_text()
    if failed:
        assert text.rstrip().endswith("⚠ selftest: 3 rule cases failed")
    else:
        assert "selftest" not in text


@pytest.mark.parametrize("failed", [0, 3])
def test_daily_logs_selftest_and_scans_even_on_failure(tmp_path, failed):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "daily.sh"
    script.write_text((ROOT / "scripts" / "daily.sh").read_text())
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    uv = bin_dir / "uv"
    uv.write_text('''#!/usr/bin/env bash
if [[ "$5" == "selftest" ]]; then
    echo "$TEST_FAILURES" > "$7"
    echo "selftest output: $TEST_FAILURES failed"
    [[ "$TEST_FAILURES" == 0 ]]
else
    echo "scan ran with $SCANNER_SELFTEST_FAILURES selftest failures"
fi
''')
    uv.chmod(0o755)
    state = tmp_path / "private" / "state"
    state.mkdir(parents=True)
    (state / "selftest-failures").write_text("999\n")
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                            env={**os.environ, "HOME": str(tmp_path), "TEST_FAILURES": str(failed)})
    assert result.returncode == 0, result.stderr
    [log] = (state / "logs").glob("*.log")
    assert log.read_text().splitlines() == [f"selftest output: {failed} failed",
                                           f"scan ran with {failed} selftest failures"]
