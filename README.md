# job-hunt

A personal daily scanner for remote tech jobs. Every morning it fetches recent
remote postings, filters them against my profile, asks an LLM whether I can
actually be hired from my country, and writes a short list of links to apply to.

## How it works

```
fetch ─► hard filters ─► never-seen-before ─► LLM eligibility + fit ─► most senior per company ─► daily list
```

1. **Fetch.** Remote jobs first seen in the last N days from the
   [freehire](https://github.com/strelov1/freehire) public API (3M+ postings
   crawled from Greenhouse, Lever, Ashby, Workday, remote boards, …).
2. **Hard filters** (`config.yml`): title role families and exclusions, stack
   (Python required unless the posting names no language), posting age (earliest of
   posted / first-seen date), salary floor, employment type, excluded companies,
   company types and industries, and hard drops such as US work authorization,
   security clearance and W-2 only.
3. **Never twice.** A job is new only if its ATS id, normalized link, and
   company + normalized title have never been reported. This catches reposts and
   the same job listed on several boards.
4. **LLM screening** (Gemini, structured JSON). Reads the full description and
   judges location eligibility with a verbatim quote, plus role type, primary
   language, seniority, agency, working language and fit (1–5). A region tag
   alone never drops a job. The apply / check / drop decision is made in code
   from those facts (`scanner/eligibility.py`), and results are cached per job
   and prompt version.
5. **Most senior opening per company + role family.** This also takes into
   account what was reported in the last 60 days.
6. **Output:** `private/jobs/YYYY-MM-DD.md` with ✅ Apply and 🟡 Check tables.
   Every dropped job and its reason is logged to `private/state/dropped/`.

## Layout

```
scanner/            pipeline code (sources/, filters, dedup, eligibility, report)
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
uv run python -m scanner run --skip-llm   # stage 1 only (no LLM)
uv run python -m scanner run --days 7     # override the fetch window
uv run pytest
```

Cron (server in UTC): `0 5 * * * scripts/daily.sh` runs at 11:00 Asia/Dhaka.
The first run backfills 21 days; later runs look back 3 days and rely on dedup.
