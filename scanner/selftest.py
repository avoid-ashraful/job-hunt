"""Run shared deterministic regression cases without fetching or opening scanner state."""

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scanner.filters import Filters
from scanner.models import Job
from scanner.report import reason_bucket
from scanner.rules import LocationRules, RoleRules

CASES = Path(__file__).resolve().parent.parent / "tests" / "cases" / "rules.yml"


def load_cases() -> list[dict]:
    return yaml.safe_load(CASES.read_text())


class RuleCases:
    def __init__(self, cfg: dict):
        self.filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], datetime.now(timezone.utc))
        self.location = LocationRules(cfg["rules"]["location"])
        self.role = RoleRules(cfg["rules"]["role"])

    def evaluate(self, case: dict) -> dict:
        fields = dict(id="selftest:1", source="selftest", url="https://example.com/job",
                      company="Example", company_key="example", title="Senior Backend Engineer",
                      description="We use Python and Django.")
        fields.update(case["job"])
        job = Job(**fields)
        stage = case["stage"]
        if stage == "location":
            verdict = self.location.classify(job)
            return {"outcome": verdict.outcome, "rule": verdict.rule}
        # Filters annotate the role family before the daily run evaluates role rules and fit.
        reason = self.filters.evaluate(job)
        if stage == "filter":
            return {"outcome": "drop" if reason else "keep", "rule": reason_bucket(reason.split(":", 1)[0]) if reason else "",
                    "family": job.family}
        if stage == "role":
            hit = self.role.evaluate(job)
            return {"outcome": "drop" if hit else "keep", "rule": hit[0] if hit else ""}
        if stage == "fit":
            score, why = self.role.fit_score(job)
            return {"outcome": score, "rule": why}
        raise ValueError(f"unknown rule case stage: {stage}")


def matches(case: dict, actual: dict) -> bool:
    return all(actual.get(key) == value for key, value in case["expected"].items())


def run(config_path: Path, failure_count_file: Path | None = None) -> int:
    runner = RuleCases(yaml.safe_load(config_path.read_text()))
    cases = load_cases()
    failed = 0
    for case in cases:
        actual = runner.evaluate(case)
        if not matches(case, actual):
            failed += 1
            print(f"FAIL {case['name']}: expected {case['expected']}; got {actual}; "
                  f"rule={actual.get('rule', '') or '-'}; job={case['job']}")
    print(f"selftest: {len(cases) - failed} passed, {failed} failed ({len(cases)} cases)")
    if failure_count_file is not None:
        failure_count_file.write_text(f"{failed}\n")
    return failed
