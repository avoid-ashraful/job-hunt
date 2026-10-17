"""`scanner learn`: turn the decision logs into an improvement report.

The report is written for a human (or Claude) reviewing the last N days:
where jobs are lost, how often each rule is right (AI spot-checks), which
AI decisions could become rules, and what the user's feedback says.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from scanner.learning_outcomes import section as outcome_section
from scanner.rules import LocationRules, sentences

STOP = set("a an the and or of to in on for with at by from is are be this that our we you your will as it its".split())


def _rows(db, sql: str, params=()) -> list[tuple]:
    return db.execute(sql, params).fetchall()


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |")
    return out


def _ngrams(text: str, n_min: int = 3, n_max: int = 5) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9+#]+", text.lower())
    grams = set()
    for n in range(n_min, n_max + 1):
        for i in range(len(words) - n + 1):
            g = words[i : i + n]
            if g[0] in STOP or g[-1] in STOP:
                continue
            grams.add(" ".join(g))
    return grams


def learn(cfg: dict, store, root: Path, days: int = 7) -> Path:
    db = store.db
    since = (date.today() - timedelta(days=days)).isoformat()
    loc_rules = LocationRules(cfg["rules"]["location"])
    title_of = {}

    def title(job_id: str) -> str:
        if job_id not in title_of:
            row = db.execute("SELECT title, url, payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
            title_of[job_id] = (row[0], row[1], json.loads(row[2]).get("company", "")) if row else (job_id, "", "")
        t, url, company = title_of[job_id]
        return f"[{company}: {t}]({url})" if url else t

    out = [f"# Scanner learning report, last {days} days (since {since})", ""]

    # 1. funnel
    runs = _rows(db, "SELECT run_at, fetched, passed, new, report FROM runs WHERE run_at >= ? ORDER BY run_at", (since,))
    buckets = dict(_rows(db, "SELECT outcome, COUNT(*) FROM decisions WHERE stage = 'report' AND run_date >= ? "
                             "GROUP BY outcome", (since,)))
    out += ["## 1. Funnel", ""]
    out += _table(["run", "fetched", "passed filters", "reported", "file"], [list(r) for r in runs]) if runs else ["_No runs yet._"]
    out += ["", f"Reported in total: **{buckets.get('apply', 0)} Apply**, **{buckets.get('check', 0)} Check**, "
                f"{buckets.get('longshot', 0)} long shots.", ""]

    # 2. where jobs are dropped
    drops = _rows(db, "SELECT stage, rule, COUNT(*) n FROM decisions WHERE outcome = 'drop' AND run_date >= ? "
                      "GROUP BY stage, rule ORDER BY n DESC", (since,))
    out += ["## 2. Drops by stage and rule", ""]
    out += _table(["stage", "rule", "jobs"], [list(r) for r in drops[:40]]) if drops else ["_None._"]
    out += [""]

    # 3. location: rules vs AI
    loc = Counter(dict(((s, o), n) for s, o, n in _rows(
        db, "SELECT stage, outcome, COUNT(*) FROM decisions WHERE stage IN ('location', 'llm') AND run_date >= ? "
            "GROUP BY stage, outcome", (since,))))
    loc_drop = Counter(dict(_rows(db, "SELECT stage, COUNT(*) FROM decisions WHERE stage IN ('location','llm') "
                                      "AND outcome = 'drop' AND run_date >= ? GROUP BY stage", (since,))))
    total_loc = sum(n for (s, _), n in loc.items() if s == "location")
    by_rules = loc[("location", "eligible")] + loc_drop["location"]
    out += ["## 3. Location decisions", "",
            f"- Jobs reaching the location step: **{total_loc}**",
            f"- Decided by rules: **{by_rules}** (eligible {loc[('location', 'eligible')]}, locked {loc_drop['location']})",
            f"- Silent (no location wording, straight to Check): **{loc[('location', 'check')]}**",
            f"- Sent to AI as ambiguous: **{loc[('location', 'ambiguous')]}** → AI eligible "
            f"{loc[('llm', 'eligible')]}, AI dropped {loc_drop['llm']}, AI unsure {loc[('llm', 'check')]}",
            ""]

    # 4. AI usage
    calls = _rows(db, "SELECT model, purpose, COUNT(*), SUM(ok), SUM(jobs), ROUND(AVG(seconds),1), SUM(chars) "
                      "FROM llm_calls WHERE run_date >= ? GROUP BY model, purpose", (since,))
    out += ["## 4. AI usage", ""]
    out += _table(["model", "purpose", "requests", "ok", "jobs", "avg s", "input chars"], [list(r) for r in calls]) \
        if calls else ["_No AI calls._"]
    out += [""]

    # 5. rule accuracy from spot-checks
    audits = _rows(db, "SELECT rule, rule_outcome, llm_eligibility, agree, job_id, rule_evidence, llm_evidence "
                       "FROM audits WHERE run_date >= ?", (since,))
    per_rule: dict[str, Counter] = defaultdict(Counter)
    for rule, _, _, agree, *_ in audits:
        per_rule[rule]["agree" if agree == 1 else "disagree" if agree == 0 else "unsure"] += 1
    out += ["## 5. Rule accuracy (AI spot-checks of rule decisions)", ""]
    if per_rule:
        rows = []
        for rule, c in sorted(per_rule.items(), key=lambda kv: -sum(kv[1].values())):
            decided = c["agree"] + c["disagree"]
            rows.append([rule, sum(c.values()), c["agree"], c["disagree"], c["unsure"],
                         f"{100 * c['agree'] // decided}%" if decided else "-"])
        out += _table(["rule", "checked", "AI agrees", "AI disagrees", "AI unsure", "agreement"], rows)
        dis = [a for a in audits if a[3] == 0]
        if dis:
            out += ["", "Disagreements (rule vs AI):", ""]
            for rule, outcome, llm, _, job_id, rev, lev in dis[:25]:
                out.append(f"- `{rule}` said **{outcome}**, AI said **{llm}**: {title(job_id)}\n"
                           f"  - rule: “{rev[:200]}”\n  - AI: “{lev[:200]}”")
    else:
        out += ["_No spot-checks yet._"]
    out += [""]

    # 6. what the AI decides for each ambiguity trigger: promote or tighten rules
    trig = _rows(db, """SELECT l.rule, COALESCE(a.rule, 'not screened') AS ai, COUNT(*)
                        FROM decisions l LEFT JOIN decisions a
                          ON a.job_id = l.job_id AND a.run_date = l.run_date AND a.stage = 'llm'
                        WHERE l.stage = 'location' AND l.outcome = 'ambiguous' AND l.run_date >= ?
                        GROUP BY l.rule, ai ORDER BY l.rule""", (since,))
    by_trigger: dict[str, Counter] = defaultdict(Counter)
    for rule, ai, n in trig:
        by_trigger[rule][ai] += n
    out += ["## 6. Ambiguity triggers and what the AI decided", "",
            "If the AI almost always decides one way for a trigger, that trigger can become a firm rule.", ""]
    if by_trigger:
        rows = [[rule, sum(c.values()), ", ".join(f"{k} {v}" for k, v in c.most_common())]
                for rule, c in sorted(by_trigger.items(), key=lambda kv: -sum(kv[1].values()))]
        out += _table(["trigger", "jobs", "AI verdicts"], rows)
    else:
        out += ["_Nothing went to the AI yet._"]
    out += [""]

    # 7. candidate phrases from AI evidence that no rule matches yet
    ai_ev = _rows(db, "SELECT rule, outcome, detail FROM decisions WHERE stage = 'llm' AND run_date >= ? "
                      "AND detail != ''", (since,))
    phrase_count: dict[str, Counter] = defaultdict(Counter)
    examples: dict[tuple[str, str], str] = {}
    for rule, outcome, detail in ai_ev:
        evidence = detail.split(': "', 1)[-1].rstrip('"')
        kind = "lock" if outcome == "drop" else "positive" if outcome == "eligible" else "unclear"
        if kind == "unclear":
            continue
        covered = any(
            rx.search(s) for s in sentences(evidence)
            for group in (loc_rules.lock, loc_rules.positive) for rx in group.values()
        )
        if covered:
            continue
        for g in _ngrams(evidence):
            phrase_count[kind][g] += 1
            examples.setdefault((kind, g), evidence[:160])
    out += ["## 7. Phrases the AI relied on that no rule covers (candidate new rules)", ""]
    any_phrase = False
    for kind in ("lock", "positive"):
        common = [(g, n) for g, n in phrase_count[kind].most_common(40) if n >= 2][:15]
        if common:
            any_phrase = True
            out += [f"**{kind}**", ""]
            out += _table(["phrase", "times", "example"], [[g, n, f"“{examples[(kind, g)]}”"] for g, n in common])
            out += [""]
    if not any_phrase:
        out += ["_Not enough AI decisions yet._", ""]

    # 8. user feedback
    fb = _rows(db, """SELECT f.label, f.note, f.job_id, f.ref,
                             (SELECT rule || ' → ' || outcome FROM decisions d WHERE d.job_id = f.job_id
                              AND d.stage IN ('location','llm') ORDER BY d.run_date DESC LIMIT 1)
                      FROM feedback f WHERE f.created_at >= ?""", (since,))
    out += ["## 8. Your feedback", ""]
    if fb:
        out += [", ".join(f"{k}: {v}" for k, v in Counter(r[0] for r in fb).most_common()), ""]
        for label, note, job_id, ref, decided in fb:
            out.append(f"- **{label}** `{ref}` {title(job_id) if job_id else ''} (location decided by: {decided or '-'})"
                       + (f": {note}" if note else ""))
    else:
        out += ["_No feedback yet. Edit a `[ ]` Mark cell in a daily report, then run `scanner sync`._"]
    out += [""]

    outcome_lines, outcome_suggestions = outcome_section(db, since)
    out += outcome_lines

    # 9. suggestions
    sugg = list(outcome_suggestions)
    for rule, c in per_rule.items():
        decided = c["agree"] + c["disagree"]
        if decided >= 5 and c["agree"] / decided < 0.8:
            sugg.append(f"Rule `{rule}` agrees with the AI only {100 * c['agree'] // decided}% of the time: "
                        f"tighten or remove it (see disagreements).")
    for rule, c in by_trigger.items():
        n = sum(v for k, v in c.items() if k != "not screened")
        top, top_n = (c.most_common(1) or [("", 0)])[0]
        if n >= 10 and top != "unclear" and top_n / n >= 0.9:
            sugg.append(f"Trigger `{rule}` → AI says `{top}` {top_n}/{n} times: consider making it a firm rule.")
    for label in ("not-eligible", "eligible", "not-relevant", "duplicate"):
        n = sum(1 for r in fb if r[0] == label)
        if n:
            sugg.append(f"{n} × `{label}` feedback: check the deciding rule for each (section 8).")
    out += ["## 9. Suggested actions", ""] + ([f"- {s}" for s in sugg] or ["- Nothing urgent. Keep collecting data."])
    out += ["", "## How to apply improvements", "",
            "1. Record the decision in `private/config/instructions.md`.",
            "2. Edit `rules:` in `private/config/config.yml` (and `config.example.yml` if it's not personal).",
            "3. `uv run pytest`, then `uv run python -m scanner run --dry-run --days 3` to compare counts.",
            "4. Commit the code/example changes; personal config stays local.", ""]

    path = root / cfg["output"]["state_dir"] / "learning" / f"{date.today().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out))
    return path
