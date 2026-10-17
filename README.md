# job-hunt

A personal daily scanner for remote tech jobs. Every morning it fetches recent
remote postings, filters them against my profile, asks an LLM whether I can
actually be hired from my country, and writes a short list of links to apply to.

## How it works

```
fetch ─► hard filters ─► never-seen-before ─► ghost check ─► role rules ─► location rules ─► AI (only if ambiguous) ─► most senior per company ─► still open? ─► daily list
```

Rules first, AI last: deterministic parsers decide almost everything, and the AI is
only asked when the location wording is ambiguous. Even then it only sees the
relevant sentences, not the whole posting.

1. **Fetch.** Remote jobs first seen in the last N days from the
   [freehire](https://github.com/strelov1/freehire) public API (3M+ postings
   crawled from Greenhouse, Lever, Ashby, Workday, remote boards, …).
2. **Hard filters** (`filters:` in config): title role families and exclusions,
   stack (Python required unless the posting names no language), posting age
   (earliest of posted / first-seen), not seen live for 10 days, salary floor,
   employment and company type, industries, excluded companies, and hard drops
   (US work authorization, clearance, W-2, task-paid AI-lab gigs).
3. **Never twice.** A job is new only if its ATS id, normalized link,
   company + normalized title, and title + opening text were never reported. This
   catches reposts, the same job listed on several boards, and one job posted under
   two company names (e.g. an Oracle tenant name and the brand).
   **Ghost check** (`rules.ghost`): weak signals add up to a score. They are
   reposts of the same role under new ids (freehire's history and ours),
   mass-posted copies, freehire's evergreen/fake-freshness verdicts, pipeline
   wording, a third party re-posting the page, aggregator-only sources, and very
   short descriptions. A high score drops the job. A medium score keeps it,
   flagged ⚠, and out of Apply.
4. **Role rules** (`rules.role`): agency/outsourcing wording, working language,
   frontend-heavy fullstack, plus a stack-based fit score (1–5).
5. **Location rules** (`rules.location`): every sentence is matched against named
   lock / positive / timezone patterns. A captured place is checked against the
   candidate's region ("located in Europe or Asia" is fine), and perk wording
   ("anywhere for 4 weeks a year") never counts. Outcomes:
   - locked: dropped
   - positive or timezone-only: eligible
   - silent: Check
   - ambiguous (conflicting or unrecognised location wording): sent to the AI
6. **AI fallback** (`llm:`): a free OpenAI-compatible model → Gemini → Claude Code CLI
   chain judges only the ambiguous jobs, in batches of 20, from the extracted sentences. Its quote must
   come from the description, or the job goes to Check. It also spot-checks a
   few rule decisions each run, which is how rule accuracy is measured.
7. **Most senior opening per company + role family** (aware of the last 60 days).
   **Still open?** (`liveness:`): each remaining job is checked against the
   employer's ATS API (Greenhouse, Lever, Ashby, Workable, SmartRecruiters; other
   sources: the posting page). Only a clear 404/410 drops it.
8. **Output:** `private/jobs/YYYY-MM-DD.md` with three tiers:
   - ✅ **Apply**: the text confirms eligibility and the fit is good
   - 🟡 **Check**: not confirmed, but the tags are compatible or the company is known to hire worldwide
   - ⚪ **Long shots**: only a region tag that excludes you
   Every job has a Ref and an editable Mark cell for feedback. The report opens with a short Start here list.

## Self-learning

Every decision, AI call, spot-check and piece of feedback is logged. Edit a daily report’s Mark cell (`[a]` applied, `[g]` good, `[x]` not eligible, `[e]` eligible, `[r]` not relevant, `[d]` duplicate, `[c]` closed; add a note like `[x US only]`). The next run syncs marks, or run `uv run python -m scanner sync` immediately. Applications have status history, and unanswered applications become ghosted after the configured number of days. After a week,
`uv run python -m scanner learn --days 7` writes an improvement report: rule
accuracy, triggers that could become rules, uncovered phrases and feedback.
See [docs/IMPROVING.md](docs/IMPROVING.md).

## Layout

```
scanner/            pipeline code (sources/, filters, rules, eligibility, dedup, report, learn)
docs/IMPROVING.md   the weekly self-learning loop
prompts/            example LLM prompt (the real one lives in private/config/prompts/)
config.example.yml  example config (the real one lives in private/config/config.yml)
scripts/daily.sh            cron entry point
scripts/update-vendors.sh   pull latest upstream of every linked project
vendor/             open-source projects linked as git submodules
private/            tracked folder, contents gitignored:
  config/             config.yml, instructions.md, prompts/, .env (API keys)
  profile/            resume, cover letters, personal documents
  jobs/               daily job lists
  state/              SQLite state, drop logs, run logs
```

### Linked open-source projects (`vendor/`)

| Project | Used for |
|---|---|
| [strelov1/freehire](https://github.com/strelov1/freehire) | Primary job source (its hosted API). The code is linked as a self-host fallback. |
| [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops) | Planned second source: direct ATS scanning of a company watchlist, plus liveness checks. |
| [remoteintech/remote-jobs](https://github.com/remoteintech/remote-jobs) | Remote-friendly companies tagged by hiring region. |

`scripts/update-vendors.sh` (weekly via cron) moves every submodule to its latest
upstream commit and pushes the new pointers.

## Setup

```bash
git clone --recurse-submodules git@github.com:avoid-ashraful/job-hunt.git && cd job-hunt
uv sync
cp config.example.yml private/config/config.yml          # then edit
mkdir -p private/config/prompts && cp prompts/eligibility.example.md private/config/prompts/eligibility.md  # fill in the candidate section
echo "GEMINI_API_KEY=..." > private/config/.env
# set llm.prompt in config.yml to private/config/prompts/eligibility.md
```

## Usage

```bash
uv run python -m scanner run              # full run: writes today's list, marks jobs as reported
uv run python -m scanner run --dry-run    # print only; nothing is marked as reported
uv run python -m scanner run --skip-llm   # rules only (ambiguous jobs go to Check)
uv run python -m scanner sync              # import marks edited in daily files
uv run python -m scanner applied <ref> [note]
uv run python -m scanner app <ref> <status> [note]  # interview/rejected/offer/ghosted/withdrawn
uv run python -m scanner status            # active applications and response rate
uv run python -m scanner feedback <ref> <label> [note]  # optional CLI alternative
uv run python -m scanner learn --days 7
uv run python -m scanner run --days 7     # override the fetch window
uv run pytest
```

Cron (server in UTC): `0 5 * * * scripts/daily.sh` runs at 11:00 Asia/Dhaka.
The first run backfills 21 days; later runs look back 3 days and rely on dedup.

Weekly learning cron (server in UTC): `0 5 * * 6 /home/ubuntu/job_hunt/scripts/weekly-learn.sh` runs at 11:00 Saturday in Asia/Dhaka. The script writes the learning report and a dated log.
