# Daily Job Scanner — Plan

Goal: every day, on the VPS, find **live** remote tech jobs that match a set of
editable parameters, keep one best-fit posting per company/role, and produce a
short ranked list to apply to manually in the evening.

Out of scope for now: auto-filling or auto-submitting (later phase, laptop side).

---

## 1. Tool choices (researched 2026-10-15)

| Tool | Role in this setup | Why |
|---|---|---|
| **freehire** public API (`freehire.me/api/v1`, MIT, ~800★, active) | **Primary source** | 3.3M open postings from 225 sources (Workday, Greenhouse, Lever, Ashby, iCIMS, remote boards…). Keyless. Already has filters for remote, region, seniority, category, skills, age, plus a freshness signal that flags reposted and evergreen postings. |
| **career-ops** scanner (MIT, ~75k★, released weekly) | **Secondary source + liveness** | Reads ATS APIs directly for a watchlist of companies you name, plus remote boards (RemoteOK, Remotive, WWR, Himalayas, Working Nomads, Jobicy). Zero tokens. `liveness-api.mjs` and `check-liveness.mjs` confirm a posting is still open. Gives redundancy if freehire is down or misses a company. |
| **remoteintech/remote-jobs** (~41k★) | Company prior | Curated list of remote-friendly companies with their hiring regions. Used as a hint for "does this company hire globally". |
| **LLM** (Gemini Flash via API key, or Claude via `claude -p` on the VPS) | Judgement step | Reads the full description only for jobs that passed the cheap filters. Decides who can actually apply ("remote anywhere"), checks stack and seniority fit, and quotes the sentence it based that on. |
| JobSync (MIT, ~1.4k★) | Not used for scanning | Nice tracker UI, but its discovery only covers Greenhouse, Lever and Ashby company directories, with no way to judge "remote anywhere". Could be added later as a tracker/UI. |
| JobSpy (LinkedIn/Indeed) | Not now | LinkedIn rate-limits datacenter IPs. Most of those jobs also appear on the company's own ATS board, which freehire already covers. |

Live check today: `work_mode=remote, category=backend, seniority=senior,staff,
open_within_days=21` → **481 jobs**. Only **3** are tagged `regions=global`.
Location tags alone would discard most real opportunities, which is why step 5
below exists.

---

## 2. Pipeline (runs daily via cron on the VPS)

```
fetch ─► normalize ─► hard filters ─► dedup & collapse ─► LLM eligibility+fit ─► liveness ─► daily report
(free)     (free)         (free)            (free)           (tokens, bounded)       (free)
```

1. **Fetch.** Query freehire with broad filters: remote, your categories, your
   seniorities, `open_within_days=21`, every region. Run the career-ops scanner
   on your watchlist and the remote boards. Save everything raw.
2. **Normalize** into one SQLite table: company, title, url, source, posted_at,
   first_seen, location text, region tags, salary, description. This is also
   where the state lives: seen, applied, dismissed, not-eligible. A job you
   already handled never comes back.
3. **Hard filters** (deterministic, from `config.yml`):
   - title include/exclude keywords (e.g. include `backend|platform|api`,
     exclude `frontend|junior|intern|manager`)
   - stack must-have / nice-to-have keywords, matched in the title and description
   - age ≤ 21 days, using the *earlier* of the stated post date and the date the
     job was first seen. Postings freehire flags as `fake_freshness` or reposted
     are dropped.
   - employment type, salary floor (when stated), excluded companies, excluded
     domains (e.g. gambling)
4. **Dedup and collapse.**
   - The same job from several sources is merged by normalized URL, then by
     company + normalized title.
   - **Keep the best seniority per company and role.** Within one company, roles
     in the same family (backend / platform) are grouped and only the best match
     to your seniority ranking is kept, e.g. `staff > senior > mid`. So "Senior
     Backend Engineer" wins over "Backend Engineer II". A company that only has
     a mid-level opening is either dropped or kept as a lower tier; you choose.
5. **LLM eligibility and fit** (only on the jobs that survive, usually tens per
   day). The model receives the full description plus your profile and returns
   structured JSON:
   - `eligibility`: `anywhere` | `includes_my_country` | `region_locked` | `unclear`
   - `evidence`: the exact sentence(s) it relied on
   - timezone requirement, and the overlap with your timezone
   - work-authorization requirement, contractor vs employee, hiring entity
     (Deel, Remote.com, Oyster, …)
   - stack match (0–5), seniority match, and a one-line reason
6. **Liveness.** Right before the report is built, re-check each candidate's URL
   through the ATS API, with a browser fallback, and drop closed postings.
7. **Daily report** (markdown/HTML on the VPS, optionally pushed to Telegram or
   email), sorted by fit:
   - ✅ **Apply**: eligible, with quoted evidence
   - 🟡 **Check**: probably eligible, e.g. tagged "Remote US" but the
     description says "hire anywhere via Deel"; or unclear
   - 📁 **Excluded log**: everything dropped, with the reason, so the filters
     can be audited

---

## 3. "Remote anywhere": how it's handled

The real question is not "is it global?" but **"can someone in my country, in my
timezone, apply?"** Rules:

- **The description beats the location tag.** "Remote – USA" on the posting, but
  "we hire anywhere through Deel" in the text → 🟡 Check, not dropped.
- **Positive signals:** "work from anywhere", "fully distributed", named EOR or
  contractor platforms, "hire in N countries" (that includes yours), a timezone
  window that covers yours, a company listed as global-friendly in remoteintech.
- **Negative signals:** "must reside in / be located in", "authorized to work in
  the US without sponsorship", a list of US states, "must work EST hours" with no
  overlap with yours, onsite or hybrid clauses.
- **Weak signals** (they lower confidence, never drop a job alone): 401k or US
  health benefits, a US-only salary range.
- **No evidence → 🟡 Unclear, never silently dropped.** Every verdict must quote
  its sentence, so you can check it in 5 seconds.
- **Feedback loop:** marking a job "not eligible" or "actually eligible" records
  the company and phrase. Later runs use these as examples, so accuracy improves
  over the first weeks.

---

## 4. Config — editable any time

One `config.yml`. Edit it directly, or ask Claude "add Go to my stack" or
"drop crypto companies".

```yaml
candidate:
  country: "<your country>"
  timezone: "<e.g. Asia/Dhaka>"
  work_authorization: []          # countries you can work in without sponsorship
  contractor_ok: true             # many "anywhere" roles are contractor via Deel etc.
search:
  title_include: [backend, "back-end", platform, api, "software engineer"]
  title_exclude: [frontend, junior, intern, "engineering manager", sales]
  seniority_rank: [staff, senior, lead]       # best first
  mid_only_companies: drop                    # or: keep_low_tier
  stack_must: [python]
  stack_nice: [django, postgres, aws, kubernetes, go]
  max_age_days: 21
  salary_floor_usd: null
  exclude_companies: []
  exclude_domains: [gambling]
llm:
  provider: gemini        # or: claude-cli
  model: <flash-tier model>
  daily_budget_jobs: 150  # hard cap on LLM calls per run
delivery: [file]          # + telegram | email
```

---

## 5. Build phases

| Phase | Deliverable | How you'll know it works |
|---|---|---|
| 0 | Fill in `config.yml`; choose the LLM | — |
| 1 | freehire fetch → SQLite → hard filters → seniority collapse → markdown report (no LLM) | Run it manually and review recall: are jobs you'd want missing? |
| 2 | LLM eligibility and fit with quoted evidence; ✅ / 🟡 / 📁 buckets | Spot-check 20 verdicts against the descriptions |
| 3 | career-ops scanner as second source (watchlist + remote boards); liveness re-check | Both sources show up, deduplicated; no closed links in the report |
| 4 | cron schedule, Telegram/email delivery, `applied` / `dismiss` / `not-eligible` commands | A report arrives daily; handled jobs don't reappear |
| 5 (later) | Hand the ✅ list to the laptop autofill flow (Simplify + Claude in Chrome) | — |

Stack: Python 3.12 + SQLite in `~/job_hunt/scanner/`. career-ops is called as a
subprocess, and its output is read from its data files.

---

## 6. Open decisions / inputs needed

1. Your **country and timezone**. This drives the whole eligibility logic.
2. **Work authorization** you already hold, and whether **contractor** roles are OK.
3. Title keywords, **stack** (must vs nice), target **seniority** levels, and
   what to do with companies that only have a mid-level opening.
4. **LLM:** a Gemini API key from aistudio.google.com (a Gemini *app*
   subscription is not an API key), or use your Claude subscription via the
   `claude -p` already installed on this VPS (no key needed).
5. **Delivery:** a file on the VPS, Telegram bot, or email.
6. Optional watchlist of companies you particularly want covered.
