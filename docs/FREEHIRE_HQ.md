# Freehire company HQ

Inspected on 2026-10-17 against the public API and the vendor Git objects
(`internal/api/handler/companies.go`). The vendor working tree was unpopulated;
no vendor files were changed.

- `/api/v1/agent/jobs/search` exposes company name and `company_slug`, the original
  source/external ID and job URL. It does not supply company HQ, website or logo.
- `/api/v1/companies/{company_slug}?limit=1` returns `data.company` with `name`,
  `slug`, nullable `hq_country`, and `company_info.website` (sometimes without a
  scheme). Other profile fields include industries, year founded, employee count,
  tagline, social links and sometimes `company_info.locations`. There is no
  dedicated website/domain/logo field in the company detail schema.
- `countries`, `regions` and `remote_regions` describe hiring reach. Neither those
  arrays, `company_info.locations`, nor a job's location establish HQ.
- Company data can be incomplete or misattributed: IBM's record had no HQ and
  pointed to `https://foo.com`. The resolver checks the company name and requires
  website domains to match the employer using the existing website discovery
  check. It accepts only the explicit `hq_country` as direct country evidence.

The adapter now retains the exact company slug in `ats_metadata`. Older stored
jobs fall back to `company_key`; normalization can remove legal suffixes, so some
old records remain unavailable until fetched again. Company websites feed the
existing JSON-LD, postal address and HQ sentence lookup. ATS identity detection
already handles freehire job IDs/URLs, including Greenhouse, Lever, Ashby,
SmartRecruiters and Workable; its metadata path remains in use. All network work
stays under the existing 100 unknown companies per report run limit and positive
company cache.

The private SQLite `jobs.payload` holds normalized `Job` records, not raw API
responses. All 10,131 stored freehire postings lacked useful `ats_metadata` at
inspection, so none retained company websites or the original company slug.

Measured the latest private report, `2026-10-17.md`, by matching its 35 Apply/Check
refs to stored jobs, opening SQLite with `file:...?mode=ro`, copying company
profiles into an in-memory database, then calling the old and new public lookup
on each unknown company. No private files or database rows were written.

| Measurement | Existing lookup | With freehire company records |
|---|---:|---:|
| Unknown jobs/companies before public lookup | 25 | 25 |
| Resolved by public lookup | 4 | 7 |
| Remaining unknown report jobs/companies | 21 | 18 |

The three additional resolutions were Srijan Technologies (`us`), Employment Hero
(`au`) and Accurate Background (`us`), all from explicit freehire `hq_country`.
Cross River, Globaltize, Ancient and Antixo Global resolved with either version.
These are live-network observations; unavailable or incomplete company data
continues to leave HQ unknown.
