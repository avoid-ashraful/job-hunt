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

`filters.max_age_days_live` (45 by default) lets non-watchlist ATS jobs older than `max_age_days` (21) but below that cap proceed only with a confirmed-open liveness check; ghost rules still apply.

## Giving feedback (takes seconds, makes the biggest difference)

Each job has a **Mark** cell in the daily Markdown file. Open `private/jobs/YYYY-MM-DD.md` on a phone or laptop and replace `[ ]` with one letter. An optional note goes after a space, inside the brackets.

| Mark | Meaning |
|---|---|
| `[a]` | applied; creates an application |
| `[g]` | good match |
| `[x US only]` | not eligible, with a note |
| `[e]` | eligible, despite the scanner's doubt |
| `[r]` | not relevant |
| `[d]` | duplicate |
| `[c]` | closed |

The Start here jobs also appear in the full tables. Mark either copy; if both are marked, the Start here mark wins. The next daily scan imports all report marks before filtering. Run `uv run python -m scanner sync` to import them sooner. Re-importing is safe, and changing a mark updates its feedback. A malformed mark is ignored. Clearing a mark back to `[ ]` removes report feedback; an existing application remains in its history.

Check jobs are ranked by evidence strength; `report.check_top` (default 25) limits expanded rows, with the rest grouped by reason in a collapsed block that still accepts marks.

Use `uv run python -m scanner applied <ref> [note]` if you applied without editing the report. Use `uv run python -m scanner app <ref> interview|rejected|offer|ghosted|withdrawn [note]` as the process changes. `uv run python -m scanner status` lists active applications, days since applying, and the response rate. Applications still at `applied` after `applications.ghosted_after_days` become ghosted. Multiple ghosted applications at one company add a configurable weak signal to future ghost scores.

The optional `scanner feedback <ref> <label> [note]` command still works.

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
8. **Your feedback**, with the rule that made each decision, plus marks by rule.
9. **Application outcomes**: response rates by source, deciding rule and report bucket.
9. **Suggested actions**, generated from the numbers above.

Improvements are applied by editing `rules:` in `private/config/config.yml` (no code change),
recording the decision in `private/config/instructions.md`, running `uv run pytest`, and
comparing a dry run: `uv run python -m scanner run --dry-run --days 3`.

The weekly script is `scripts/weekly-learn.sh`. Install `0 5 * * 6 /home/ubuntu/job_hunt/scripts/weekly-learn.sh` in the UTC server crontab for 11:00 Saturday in Asia/Dhaka.

Salary ranges missing from source fields are conservatively parsed from descriptions before filtering; Pay preserves the matched text, while comparisons use configured currency rates and annual multipliers (currencyless ranges are display-only).

Unknown report HQs get public company metadata and website evidence (at most two website pages, five workers, 100 companies/run); positive results persist in SQLite company_profiles, empty results retry next run, and job locations never establish HQ.

## Watchlist probing

`scanner watchlist build` shares adaptive backoff across each ATS provider after a 429,
using Retry-After (up to 60 seconds; a longer Retry-After skips that host until it ends) or exponential delays, then reducing delays after
successful requests. Probes retry once before recording a rate limit. Later builds
retry only the unresolved boards, after fresh seeds, without the website-failure
cooldown or attempt limit. Older rate-limited caches are also retried, even if marked dead.
Common ATSs are tried first, using existing resolved boards to order providers, and
duplicate probe URLs are skipped. No additional retry flag is needed.

Tracked seeds can supply `ats` and `board` to validate an exact board without guessing,
and `aliases` to accept alternate company names in board ownership checks.

Daily ATS fetches save raw board counts and health in `<state_dir>/watchlist_health/`; weekly builds re-resolve boards after three consecutive 404/410 runs or 28 empty days, retain unresolved companies as inactive for the next build, and `scanner watchlist list` shows health.
