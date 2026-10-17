"""Summarize the user's marks and application outcomes by scanner decision."""

from __future__ import annotations

import json
from collections import Counter, defaultdict


def _table(headers: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + [
        "| " + " | ".join(str(v).replace("|", "/") for v in row) + " |" for row in rows]


def section(db, since: str) -> tuple[list[str], list[str]]:
    rows = db.execute("""SELECT source, decision_rule, bucket, status_history
                         FROM applications WHERE applied_date >= ?""", (since,)).fetchall()
    out = ["## Application outcomes", ""]
    for label, index in (("source", 0), ("decision rule", 1), ("bucket", 2)):
        groups = defaultdict(lambda: [0, 0])
        for row in rows:
            key = row[index] or "unknown"
            groups[key][0] += 1
            history = json.loads(row[3])
            groups[key][1] += any(h["status"] in {"interview", "rejected", "offer"} for h in history)
        out += [f"### By {label}", ""]
        out += _table([label, "applied", "responded", "response rate"],
                      [[key, n, response, f"{response / n:.0%}"] for key, (n, response) in
                       sorted(groups.items(), key=lambda kv: -kv[1][0])]) if groups else ["_No applications yet._"]
        out.append("")

    feedback = db.execute("""SELECT f.label, COALESCE(d.rule, 'unknown') FROM feedback f
        LEFT JOIN reported r ON r.id=f.job_id
        LEFT JOIN decisions d ON d.job_id=f.job_id AND d.stage='report'
          AND d.run_date=r.reported_on
        WHERE f.created_at >= ? AND f.rowid=(SELECT MAX(f2.rowid) FROM feedback f2 WHERE f2.ref=f.ref)""",
                          (since,)).fetchall()
    per_rule = defaultdict(Counter)
    for label, rule in feedback:
        per_rule[rule][label] += 1
    out += ["## Marks by decision rule", ""]
    out += _table(["rule", "applied", "not eligible", "not relevant", "good", "eligible"],
                  [[rule, c["applied"], c["not-eligible"], c["not-relevant"], c["good"], c["eligible"]]
                   for rule, c in sorted(per_rule.items())]) if per_rule else ["_No marks yet._"]
    out.append("")
    suggestions = []
    for rule, c in per_rule.items():
        for label in ("not-eligible", "not-relevant"):
            if c[label]:
                suggestions.append(f"Rule `{rule}` produced {c[label]} `{label}` marks: review its examples and tighten the rule.")
    return out, suggestions
