"""Daily markdown list of new jobs, plus a TSV log of everything dropped."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from scanner.models import Job
from scanner.store import job_ref


def _cell(text: str) -> str:
    return " ".join(str(text).replace("|", "/").split())


def _salary(job: Job) -> str:
    if not (job.salary_min or job.salary_max) or not job.salary_currency:
        return ""
    lo = f"{job.salary_min:,.0f}" if job.salary_min else "?"
    hi = f"{job.salary_max:,.0f}" if job.salary_max else "?"
    return f"{lo}–{hi} {job.salary_currency}/{job.salary_period or 'year'}"


def _sort_key(job: Job):
    fit = (job.screen or {}).get("fit", 0)
    return (not (job.screen or {}).get("preferred_hq"), -fit, "global" not in job.regions,
            -(job.age_anchor.timestamp() if job.age_anchor else 0))


def _decided_by(sc: dict) -> str:
    rule = sc.get("decided_by", "")
    return f"AI ({rule.split(':')[-1]})" if rule.startswith("llm:") else rule


def _table(jobs: list[Job], with_why: bool) -> list[str]:
    head = "| Ref | Mark | Fit | Company | HQ | Title | Who can apply | Evidence | Decided by |" + (" Why here |" if with_why else "")
    lines = [head + " Pay | First seen | Apply |", "|" + "---|" * (head.count("|") + 2)]
    for job in sorted(jobs, key=_sort_key):
        sc = job.screen or {}
        who = sc.get("allowed") or job.location or ", ".join(job.regions) or "-"
        evidence = sc.get("evidence") or ""
        evidence = f"“{evidence[:180]}”" if evidence else f"_tag only: {job.location or '-'}_"
        seen = f"{job.age_anchor:%b %d}" if job.age_anchor else ""
        ghost_score, signals = sc.get("ghost") or (0, [])
        ghost = f"⚠ possible ghost: {', '.join(signals)}" if ghost_score and signals else ""
        why_text = "; ".join(x for x in (sc.get("why", ""), sc.get("prior", ""), ghost) if x)
        why = f" {_cell(why_text)} |" if with_why else ""
        code = (sc.get("hq_country") or "").upper()
        hq = f"{''.join(chr(127397 + ord(c)) for c in code)} {code}" if len(code) == 2 else "?"
        pay = "; ".join(x for x in (sc.get("pay_hint") or _salary(job),
                                      f"Levels: {sc['pay_tier']}" if sc.get("pay_tier") else "") if x)
        lines.append(
            f"| `{job_ref(job.id)}` | [ ] | {sc.get('fit', '')}/5 | {_cell(job.company)} | {hq} | {_cell(job.title)} | {_cell(who)} | "
            f"{_cell(evidence)} | {_cell(_decided_by(sc))} |{why} {_cell(pay)} | {seen} | "
            f"[{('Himalayas apply' if job.source == 'himalayas' else 'apply')}]({job.url}) |"
        )
    return lines


def write_report(path: Path, run_at: datetime, jobs: list[Job], stats: dict, drop_counts: Counter, country: str, start_here_limit: int = 15) -> None:
    lines = []
    if path.exists():
        lines.append(f"\n---\n\n## Re-run at {run_at:%H:%M}\n")
    else:
        lines.append(f"# New jobs, {run_at:%Y-%m-%d}\n")
    apply = [j for j in jobs if j.bucket == "apply"]
    check = [j for j in jobs if j.bucket == "check"]
    longshot = [j for j in jobs if j.bucket == "longshot"]
    lines.append(
        f"Fetched **{stats['fetched']}** postings → **{stats['passed']}** passed filters → "
        f"**{len(apply)}** to apply, **{len(check)}** to check, {len(longshot)} long shots. "
        f"Rules decided most; {stats['ambiguous']} had unclear location wording "
        f"({stats['llm_calls']} AI requests).\n"
    )

    picks = sorted(apply, key=_sort_key) + sorted(check, key=_sort_key)[:max(0, start_here_limit - len(apply))]
    lines.append("Mark a job in its table: `[a]` applied, `[g]` good, `[x]` not eligible, "
                 "`[e]` eligible, `[r]` not relevant, `[d]` duplicate, `[c]` closed. "
                 "Add a note inside the brackets, e.g. `[x US only]`. Leave `[ ]` untouched. "
                 "Jobs in Start here also appear below; mark either copy. If both are marked, Start here wins. "
                 "Marks sync on the next run or with `uv run python -m scanner sync`.\n")
    lines.append(f"## Start here ({len(picks)})\n")
    if picks:
        lines += ["| Ref | Mark | Tier | Fit | Job |", "|---|---|---|---|---|"]
        for job in picks:
            lines.append(f"| `{job_ref(job.id)}` | [ ] | "
                         f"{'✅ Apply' if job.bucket == 'apply' else '🟡 Check'} | "
                         f"{job.screen.get('fit', '')}/5 | "
                         f"[{_cell(job.company)} — {_cell(job.title)}]({job.url}) |")
    else:
        lines.append("_No new jobs._")
    lines.append(f"\n## ✅ Apply ({len(apply)})\n")
    lines.append(f"The posting's own text says someone in {country} can be hired, or only asks for timezone overlap.\n")
    lines += _table(apply, with_why=False) if apply else ["_None today._"]

    lines.append(f"\n## 🟡 Check ({len(check)})\n")
    lines.append("Not ruled out, but eligibility isn't confirmed by the text (or the stack fit is weak). Read before applying.\n")
    lines += _table(check, with_why=True) if check else ["_None today._"]

    lines.append(f"\n<details><summary>⚪ Long shots ({len(longshot)}): the text says nothing about location and the "
                 f"source tags a region that doesn't include {country}</summary>\n")
    lines += _table(longshot, with_why=True) if longshot else ["_None today._"]
    lines.append("\n</details>\n")

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
    if reason.startswith("region locked"):
        return "region locked (rule)"
    if reason.startswith(("agency.", "language.", "role.")):
        return reason.split(":")[0]
    for prefix in ("company based in", "location only in", "ghost job", "closed", "older than", "title excluded", "gig/excluded title", "no Python", "salary max",
                   "already reported", "lower seniority", "excluded company", "excluded domain",
                   "excluded industry", "company type", "employment type", "not seen live", "staffing/agency company",
                   "us_work_authorization", "security_clearance", "w2_only"):
        if reason.startswith(prefix):
            return prefix
    return reason
