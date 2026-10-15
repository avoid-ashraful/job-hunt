"""Daily markdown list of new jobs, plus a TSV log of everything dropped."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from scanner.models import Job


def _cell(text: str) -> str:
    return " ".join(str(text).replace("|", "/").split())


def _salary(job: Job) -> str:
    if not (job.salary_min or job.salary_max) or not job.salary_currency:
        return ""
    lo = f"{job.salary_min:,.0f}" if job.salary_min else "?"
    hi = f"{job.salary_max:,.0f}" if job.salary_max else "?"
    return f"{lo}–{hi} {job.salary_currency}/{job.salary_period or 'year'}"


def _sort_key(job: Job):
    fit = (job.screen or {}).get("fit_score", 0)
    return (-fit, "global" not in job.regions, -(job.age_anchor.timestamp() if job.age_anchor else 0))


def _table(jobs: list[Job]) -> list[str]:
    lines = [
        "| # | Fit | Company | Title | Who can apply | Evidence | Salary | First seen | Apply |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, job in enumerate(sorted(jobs, key=_sort_key), 1):
        sc = job.screen or {}
        who = sc.get("allowed_locations") or job.location or ", ".join(job.regions)
        evidence = sc.get("eligibility_evidence") or ""
        evidence = f"“{evidence[:180]}”" if evidence else f"_tag only: {job.location or '-'}_"
        seen = f"{job.age_anchor:%b %d}" if job.age_anchor else ""
        fit = f"{sc['fit_score']}/5" if sc.get("fit_score") else ""
        title = job.title + (f" ({sc['hiring_model']})" if sc.get("hiring_model") in {"contractor", "eor"} else "")
        lines.append(
            f"| {i} | {fit} | {_cell(job.company)} | {_cell(title)} | {_cell(who)} | {_cell(evidence)} | "
            f"{_salary(job)} | {seen} | [apply]({job.url}) |"
        )
    return lines


def write_report(path: Path, run_at: datetime, jobs: list[Job], stats: dict, drop_counts: Counter, country: str) -> None:
    lines = []
    if path.exists():
        lines.append(f"\n---\n\n## Re-run at {run_at:%H:%M}\n")
    else:
        lines.append(f"# New jobs, {run_at:%Y-%m-%d}\n")
    apply = [j for j in jobs if j.bucket == "apply"]
    check = [j for j in jobs if j.bucket != "apply"]
    lines.append(
        f"Fetched **{stats['fetched']}** unique postings → **{stats['passed']}** passed filters → "
        f"**{len(apply)}** to apply + **{len(check)}** to check.\n"
    )

    lines.append(f"## ✅ Apply ({len(apply)})\n")
    lines.append(f"The posting says (or clearly implies) someone in {country} can be hired.\n")
    lines += _table(apply) if apply else ["_None today._"]

    lines.append(f"\n## 🟡 Check ({len(check)})\n")
    lines.append("Location unclear (often a region tag with nothing in the text), or a weaker fit. Read before applying.\n")
    lines += _table(check) if check else ["_None today._"]

    lines.append("\n<details><summary>Why jobs were dropped</summary>\n")
    for reason, count in drop_counts.most_common():
        lines.append(f"- {count} × {reason}")
    lines.append("\n</details>\n")

    with path.open("a") as fh:
        fh.write("\n".join(lines) + "\n")


def write_drop_log(path: Path, dropped: list[tuple[Job, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["reason", "company", "title", "location", "url"])
        for job, reason in dropped:
            w.writerow([reason, job.company, job.title, job.location, job.url])


def reason_bucket(reason: str) -> str:
    """Collapse per-job details so the summary groups similar reasons."""
    if reason.startswith("llm: "):
        return reason.split(' "')[0].split(" (")[0]
    for prefix in ("older than", "title excluded", "gig/excluded title", "no Python", "salary max",
                   "already reported", "lower seniority", "excluded company", "excluded domain",
                   "excluded industry", "company type", "employment type", "not seen live",
                   "us_work_authorization", "security_clearance", "w2_only"):
        if reason.startswith(prefix):
            return prefix
    return reason
