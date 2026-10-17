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
| 9 | HQ coverage: measure how many reported jobs have unknown HQ; fill from ATS board metadata / company website footer, cached per company | unknown HQ can't be ranked by pay market | done (Codex, merged): JSON-LD/imprint/HQ sentences, unknown HQ 71% → 60% on Oct 17 report |
| 10 | Salary parsing from description text (ranges, currency, period) so pay-ranked sorting works when the source has no salary field | user wants well-paid roles | done (Codex, merged) |
| 11 | Watchlist hygiene: boards with 0 jobs for 4+ weeks or repeated 404 are re-resolved or dropped; report the count | stale boards waste requests | done (Codex, merged): per-board health JSON, 3x404 or 28 days empty → re-resolve, else inactive |
| 12 | Check tier triage: 98 Check jobs/day is too many to read; rank Check by evidence strength and collapse low-signal ones | user time | done (Codex, merged): top 25 by evidence score, rest folded by reason |
| 13 | Weekly cron refreshes seed lists (`watchlist seeds`) before the rebuild | new companies arrive without manual runs | done 2026-10-17 |
| 14 | Duplicate audit after adding 4 remote boards: same job via RemoteOK/WWR/Jobicy and the employer ATS must be reported once, with the ATS copy winning | boards re-post ATS jobs | open |
| 15 | AI-decision audit: sample LLM eligibility verdicts and their quoted evidence from the decisions/llm_calls tables; measure wrong "eligible"/"locked" calls and fix prompts or rules | AI is the least predictable step | done (Codex, merged): LLM 57% / rules 83% correct on sample; prompt safeguards + 4 rule fixes; see docs/audits/2026-10-17-ai-decisions.md |
| 16 | Daily run budget: measure end-to-end time with all sources; keep it under ~15 min (parallel fetch per source, cap detail fetches) | more sources = slower run | done (t3/parallelize-daily-fetch): sequential estimate 211s → live concurrent 147s without LLM; fetch 160s → 99s; 10-minute soft fetch budget |
| 17 | HQ for freehire jobs: use freehire company data / website / ATS behind the URL to fill unknown HQ | 24 of 25 unknown-HQ report jobs came from freehire | done (Codex, merged): freehire company hq_country/website; unknown 21/35 → 18/35 |

### Item 16 timing (2026-10-17)

Measured live with a copy of the private config and the authorized 155-entry ATS watchlist in `/tmp/job-hunt-budget`; output, state and board health also stayed under `/tmp`. Used `days=1`, first-run backfill 1 day, `--dry-run --skip-llm`, fresh state, and the existing source request limits/pacing. LLM time is excluded.

Sequential source timings: ATS 84.5s (476 jobs), Freehire 24.2s (1,027), HN 20.8s (57), Himalayas 11.2s (30), Workable 14.0s (0), RemoteOK 0.8s (0), WWR 1.9s (2), Working Nomads 2.7s (0), Jobicy 0.2s (12). ATS was measured separately after watchlist read permission arrived; its initial example-board measurement was discarded. Workable's ATS host returned the expected ~12-hour quota block and failed fast.

The original post-fetch dry run exposed a Workday HQ URL `KeyError: tenant`; fixed it using the existing board URL helper. Replaying the captured source jobs after that fix took 51.2s for post-fetch stages (19.7s liveness, 19.5s HQ, 12.0s other), giving a reconstructed sequential total of 211.4s (160.2s fetching + 51.2s post-fetch).

Fetching now uses four concurrent sources, yields in config order to retain first-source dedup priority, and preserves partial results and source health stats. `run.fetch_budget_minutes` defaults to 10: queued sources starting after the deadline are skipped with a warning in the footer; active sources finish normally. Liveness and HQ already used bounded pools (8 and 5), so those pools and per-host ATS pacing are unchanged.

The live concurrent dry run finished in **146.8s** (99.1s fetching, 18.9s liveness, 18.5s HQ, 10.3s other), about **31% faster** than the reconstructed sequential total. Per-source concurrent durations: ATS 99.1s, Freehire 31.1s, HN 10.2s, Himalayas 8.2s, Workable 14.1s, RemoteOK 1.0s, WWR 2.5s, Working Nomads 2.4s, Jobicy 0.2s. Requests and job counts vary between live samples; these are measurements, not a guaranteed upper bound. No sources hit the fetch budget in this sample. `uv run pytest -q`: **389 passed**, including concurrent priority/partial results, queued budget skips, footer warning and Workday HQ regression coverage.
