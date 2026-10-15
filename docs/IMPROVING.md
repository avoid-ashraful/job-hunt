# Improving the scanner (the weekly learning loop)

The scanner is built to be tuned from data, not guesswork. Every run records:

| Log (SQLite `private/state/scanner.sqlite`) | What it holds |
|---|---|
| `decisions` | every job × stage (filter, dedup, role, location, llm, collapse, report): which rule decided, the outcome and the evidence |
| `audits` | each run, the AI double-checks a random sample of rule decisions (`llm.audit_per_run`): does it agree? |
| `llm_calls` | every AI request: model, purpose (screen/audit), jobs, success, seconds, input size |
| `feedback` | your labels on reported jobs |
| `runs` | per-run funnel numbers |

Plus `private/state/dropped/<date>.tsv` (every dropped job with its reason) and `private/state/logs/<date>.log`.

## Giving feedback (takes seconds, makes the biggest difference)

Each job in the daily list has a **Ref**:

```bash
uv run python -m scanner feedback 3fa9c1 applied
uv run python -m scanner feedback 3fa9c1 not-eligible "they only hire in the US"
uv run python -m scanner feedback 77b2e0 eligible "was a long shot but they hire globally via Deel"
uv run python -m scanner feedback 9c01aa not-relevant "mostly frontend"
```

Labels: `applied`, `good`, `not-eligible`, `eligible`, `not-relevant`, `duplicate`, `closed`.

## The weekly review

```bash
uv run python -m scanner learn --days 7      # writes private/state/learning/<date>.md
```

Then ask Claude: *"Read the latest learning report and improve the scanner."* The report covers:

1. **Funnel**: volumes per run.
2. **Drops by stage and rule**: where jobs are lost. A rule dropping a surprising number of jobs is worth a look.
3. **Location decisions**: how many were settled by rules, how many were silent, and how many went to the AI.
4. **AI usage**: requests and models per purpose.
5. **Rule accuracy**: agreement between each rule and the AI spot-checks, with the disagreements listed.
6. **Ambiguity triggers vs AI verdicts**: a trigger the AI always resolves the same way can become a firm rule, which saves AI calls.
7. **Uncovered phrases**: recurring wording in AI-decided cases that no rule matches. These are candidate new rules.
8. **Your feedback**, with the rule that made each decision.
9. **Suggested actions**, generated from the numbers above.

Improvements are applied by editing `rules:` in `private/config/config.yml` (no code change),
recording the decision in `private/config/instructions.md`, running `uv run pytest`, and
comparing a dry run: `uv run python -m scanner run --dry-run --days 3`.
