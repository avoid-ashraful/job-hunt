# job-hunt

A personal daily scanner for remote tech jobs. Every morning it fetches recent
remote postings from several sources, filters them against a candidate profile
(a candidate in Bangladesh), uses an LLM only for genuinely ambiguous location
wording, and writes a short list of links to apply to.

## How it works

```
fetch (9 sources, parallel) ─► merge copies ─► hard filters ─► never-seen-before ─► ghost check ─► role rules ─► location rules ─► AI (only if ambiguous) ─► most senior per company ─► still open? ─► daily list
```

Rules first, AI last: deterministic parsers decide almost everything. The AI is
asked only when the location wording is ambiguous, and it sees just the relevant
sentences, not the whole posting.

### Sources (priority order)

Sources run in parallel (10-minute soft budget), but when two return the same job
the first in this list wins, so the employer's own board beats an aggregator copy.
Each is switched on under `sources:` in the config. Feasibility, terms and rejected
sites are in [docs/SOURCES.md](docs/SOURCES.md).

| # | Source | What it is |
|---|---|---|
| 1 | **ATS watchlist** | Public job-board APIs of companies that hire worldwide, read directly. 19 adapters: Greenhouse, Lever (+EU), Ashby, Workable, SmartRecruiters, Recruitee, Breezy, BambooHR, Pinpoint, Personio, Teamtailor, Workday, Rippling, JazzHR, Homerun, Dover, Comeet, Factorial. Watched companies get a longer age window. |
| 2 | [freehire](https://github.com/strelov1/freehire) | Public API aggregating millions of postings from many ATSs and boards |
| 3 | Hacker News | Jobs index and the monthly "Who is hiring?" threads |
| 4 | Himalayas | Worldwide remote search |
| 5 | Workable search | Cross-company remote search |
| 6 | Remote OK | Public JSON |
| 7 | We Work Remotely | RSS |
| 8 | Working Nomads | Public JSON |
| 9 | Jobicy | Public feed |

### Pipeline

1. **Hard filters** (`filters:`): title role families and exclusions, stack
   (Python required unless the posting names no language), posting age (earliest of
   posted / first-seen), not seen live for 10 days, salary floor, employment and
   company type, industries, excluded companies, and hard drops (US work
   authorization, clearance, W-2, task-paid AI-lab gigs).
2. **Never twice.** A job is new only if its ATS id, normalized link, company +
   normalized title, and title + opening text were never reported. This catches
   reposts, one job on several boards, and one job under two company names.
3. **Ghost check** (`rules.ghost`): weak signals (reposts, mass-posted copies,
   evergreen verdicts, pipeline wording, aggregator-only, very short text) add up.
   A high score drops the job; a medium score keeps it flagged ⚠ and out of Apply.
4. **Role rules** (`rules.role`): agency/outsourcing wording, working language,
   frontend-heavy fullstack, plus a stack-based fit score (1-5).
5. **Location rules** (`rules.location`): each sentence is matched against named
   lock / positive / timezone patterns, and a captured place is checked against the
   candidate's region. Outcomes: locked (dropped), positive or timezone-only
   (eligible), silent (Check), ambiguous (to the AI).
6. **AI fallback** (`llm:`): a free OpenAI-compatible model, then Gemini, then the
   Claude Code CLI judge ambiguous jobs in batches of 20 from the extracted
   sentences. Its quote must appear in the description, or the job goes to Check.
   There is a per-run call cap; extra jobs go to Check. A few rule decisions are
   spot-checked each run to measure rule accuracy.
7. **Most senior opening per company + role family** (remembered for 60 days), then
   **still open?** (`liveness:`): the employer's ATS API, or the posting page. Only
   a clear 404/410 drops a job.
8. **Report** `private/jobs/YYYY-MM-DD.md`, opening with a Start here list:

| Tier | Meaning |
|---|---|
| ✅ **Apply** | The text gives strong, quoted evidence that the candidate can be hired (worldwide / APAC / Bangladesh) and the fit is good. Weak evidence is demoted to Check. |
| 🟡 **Check** | Not confirmed, but tags are compatible or the company hires worldwide. Ranked by evidence score: the top 25 (`report.check_top`) are expanded, the rest folded by reason (marks still work). |
| ⚪ **Long shots** | Only a region tag that excludes the candidate |

Every job has a Ref and an editable Mark cell. The footer shows per-source counts,
warns when a source returns nothing, and reports the selftest failure count.

## Feedback and learning

Edit a report's Mark cell: `[a]` applied, `[g]` good, `[x]` not eligible, `[e]`
eligible, `[r]` not relevant, `[d]` duplicate, `[c]` closed, plus an optional note
(`[x US only]`). The next run imports marks, or run `sync` now. Every decision, AI
call and spot-check is logged; `learn` turns the logs into a weekly improvement
report (rule accuracy, triggers that could become rules, uncovered phrases,
feedback, application outcomes). See [docs/IMPROVING.md](docs/IMPROVING.md).

## Commands

Run as `uv run python -m scanner [--config FILE] <command>`.

| Command | What it does |
|---|---|
| `run [--dry-run] [--days N] [--skip-llm]` | Full scan and report. `--dry-run` prints only and marks nothing; `--skip-llm` sends ambiguous jobs to Check |
| `selftest [--config FILE]` | Run the rule regression cases (`tests/cases/rules.yml`) against the active config; no network, exits non-zero on failure |
| `watchlist build` | Resolve seed companies to public ATS boards (ownership-checked, cached, resumable) |
| `watchlist grow` | Add eligible companies seen in the database |
| `watchlist list` | Show watched boards with health |
| `watchlist seeds` | Refresh `watchlist.seeds.auto.yml` from public company lists |
| `sync` | Import marks edited in daily reports |
| `feedback <ref> <label> [note]` | Label a job from the CLI instead of the report |
| `applied <ref> [note]` | Record an application |
| `app <ref> <status> [note]` | Set status: applied, interview, rejected, offer, ghosted, withdrawn |
| `status` | Active applications and response rate |
| `learn [--days 7]` | Write the improvement report to `private/state/learning/` |

`uv run pytest` runs the tests (also run in CI).

## Setup

```bash
git clone --recurse-submodules <repo-url> job-hunt && cd job-hunt
uv sync
cp config.example.yml private/config/config.yml          # then edit
cp watchlist.example.yml private/config/watchlist.yml    # optional starting watchlist
mkdir -p private/config/prompts && cp prompts/eligibility.example.md private/config/prompts/eligibility.md  # fill in the candidate section
echo "GEMINI_API_KEY=..." > private/config/.env         # plus any other key used in llm.chain
# set llm.prompt in config.yml to private/config/prompts/eligibility.md
uv run python -m scanner watchlist build                 # a full build can take a few runs
uv run python -m scanner run --dry-run
```

The first run backfills 21 days; later runs look back 3 days and rely on dedup.
Windows, caps and every other knob are in the config.

## Scheduled jobs (cron, server in UTC)

| When (UTC) | Script | What |
|---|---|---|
| Daily 05:00 (11:00 Dhaka) | `scripts/daily.sh` | `selftest` first (failures are logged and shown in the report footer; they do not stop the scan), then `run` |
| Saturday 06:00 | `scripts/weekly-learn.sh` | `learn --days 7` |
| Sunday 04:00 | `scripts/weekly-watchlist.sh` | `watchlist seeds`, then `watchlist build` (retries rate-limited boards, re-checks owners, re-resolves dead boards) |
| Monday 04:30 | `scripts/update-vendors.sh` | Move every `vendor/` submodule to its latest upstream commit and push the pointers |

Logs go to `private/state/logs/`.

## Layout

```
scanner/                  pipeline code (sources/, filters, rules, eligibility, dedup, report, learn, watchlist, selftest)
tests/                    pytest suite; tests/cases/rules.yml is the shared rule regression corpus
docs/                     IMPROVING.md (weekly loop, selftest), SOURCES.md (source research), BACKLOG.md, audits/
prompts/                  example LLM prompt
config.example.yml        example config (the real one is private/config/config.yml)
watchlist.example.yml     example watchlist
watchlist.seeds.yml       hand-written seed companies and board overrides (watchlist.seeds.auto.yml is generated)
scripts/                  cron entry points
vendor/                   open-source projects linked as git submodules
private/                  contents gitignored (only the folder layout is tracked):
  config/                   config.yml, watchlist.yml, instructions.md, prompts/, .env (API keys)
  profile/                  resume, cover letters, personal documents
  jobs/                     daily reports
  state/                    SQLite state, dropped-job logs, board health, learning reports, run logs
```

### Linked projects (`vendor/`)

| Project | Used for |
|---|---|
| [strelov1/freehire](https://github.com/strelov1/freehire) | Self-host fallback for the freehire source |
| [remoteintech/remote-jobs](https://github.com/remoteintech/remote-jobs) | Companies tagged by hiring region: watchlist seeds and a "hires worldwide" prior |
| [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops) | Not used by the code. Its ATS scanning idea was replaced by the native watchlist and remote-board sources |
