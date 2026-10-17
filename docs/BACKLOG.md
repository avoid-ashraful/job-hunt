# Improvement backlog

Highest priority first. Each item names its owner once picked up; agents work on their own branch, Claude reviews and merges.

| # | Item | Why | Status |
|---|------|-----|--------|
| 1 | Watchlist resolver: polite probing. Retry 429 with backoff, slow a host down after it answers 429, probe cheapest/likeliest ATSs first, a rate-limited company is never marked "dead", manual `ats`/`board`/`aliases` overrides in `watchlist.seeds.yml` (Stack Overflow, Veeva) | ~260 companies were tagged "rate limited" because one build sent ~26k probes in 20 min, not because the boards are blocked | Codex |
| 2 | More ATS adapters with public JSON: Workday (cxs API), Rippling, JazzHR, Homerun, Dover, Comeet, Factorial (only those that work from the VPS) | Big employers (Workday) and small ones are invisible to the watchlist today | Codex |
| 3 | More worldwide-hiring seed companies (Himalayas company directory, other maintained public lists), ownership-checked like slug guesses | remoteintech gives 403 seeds; the watchlist only has 149 | Codex |
| 4 | Recall audit: sample jobs dropped by "no Python" / "title not in target role families" / "region locked" and measure false negatives against the user's profile | 863 + 437 drops a day; one bad rule hides good jobs | done 2026-10-17: AI/LLM/agentic engineer, founding engineer, member of technical staff, product engineer added to role families; next: >21-day worldwide Python jobs on live ATS boards |
| 5 | Run time and failure visibility: per-source timing + counts in the daily log and report footer; alert line when a source returns 0 | silent source failures look like "no jobs" | open |
| 6 | GitHub Actions CI: `uv run pytest` on push | agent branches get merged by hand | open |
| 7 | Native remote job boards (replaces career-ops as a second source) | redundancy + more remote boards | done (branch): RemoteOK, We Work Remotely, Working Nomads, Jobicy; Remotive skipped (robots) |
