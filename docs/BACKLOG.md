# Improvement backlog

Highest priority first. Each item names its owner once picked up; agents work on their own branch, Claude reviews and merges.

| # | Item | Why | Status |
|---|------|-----|--------|
| 1 | Watchlist resolver: polite probing. Retry 429 with backoff, slow a host down after it answers 429, probe cheapest/likeliest ATSs first, a rate-limited company is never marked "dead", manual `ats`/`board`/`aliases` overrides in `watchlist.seeds.yml` (Stack Overflow, Veeva) | ~260 companies were tagged "rate limited" because one build sent ~26k probes in 20 min, not because the boards are blocked | done (t3/polite-watchlist-probing) |
| 2 | More ATS adapters with public JSON: Workday (cxs API), Rippling, JazzHR, Homerun, Dover, Comeet, Factorial (only those that work from the VPS) | Big employers (Workday) and small ones are invisible to the watchlist today | done (Codex, merged): Workday, Rippling, JazzHR, Homerun, Dover, Comeet, Factorial |
| 3 | More worldwide-hiring seed companies (Himalayas company directory, other maintained public lists), ownership-checked like slug guesses | remoteintech gives 403 seeds; the watchlist only has 149 | done (Codex, merged): +12 seeds (awesome-remote-job, remoteintech regional incl. APAC); Himalayas/Remotive/WWR/established-remote rejected, see SOURCES.md |
| 4 | Recall audit: sample jobs dropped by "no Python" / "title not in target role families" / "region locked" and measure false negatives against the user's profile | 863 + 437 drops a day; one bad rule hides good jobs | done 2026-10-17: AI/LLM/agentic engineer, founding engineer, member of technical staff, product engineer added to role families; next: >21-day worldwide Python jobs on live ATS boards |
| 5 | Run time and failure visibility: per-source timing + counts in the daily log and report footer; alert line when a source returns 0 | silent source failures look like "no jobs" | done (Codex, merged) |
| 6 | GitHub Actions CI: `uv run pytest` on push | agent branches get merged by hand | done (Codex, merged) |
| 7 | Remote boards as native sources: RemoteOK, Remotive, We Work Remotely, Working Nomads, Jobicy (replaces "career-ops as second source": our watchlist already covers its ATS scanning) | redundancy + worldwide-tagged boards | done (Codex, merged): RemoteOK, WWR, Working Nomads, Jobicy; Remotive skipped (robots.txt) |
| 8 | Age window: a worldwide Python/backend job older than 21 days on a live-checkable ATS board stays if the live check says open (cap ~45 days) | audit found real worldwide Python roles dropped at 22-40 days | done (Codex, merged) |
| 9 | HQ coverage: measure how many reported jobs have unknown HQ; fill from ATS board metadata / company website footer, cached per company | unknown HQ can't be ranked by pay market | Codex |
| 10 | Salary parsing from description text (ranges, currency, period) so pay-ranked sorting works when the source has no salary field | user wants well-paid roles | done (Codex, merged) |
| 11 | Watchlist hygiene: boards with 0 jobs for 4+ weeks or repeated 404 are re-resolved or dropped; report the count | stale boards waste requests | open |
| 12 | Check tier triage: 98 Check jobs/day is too many to read; rank Check by evidence strength and collapse low-signal ones | user time | Codex |
