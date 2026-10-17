# Remote-board duplicate audit, 2026-10-17

Called each of `remoteok.fetch`, `wwr.fetch`, `workingnomads.fetch`, and
`jobicy.fetch` with its `config.example.yml` source block and `days=7`.
All four succeeded: 26 + 21 + 6 + 50 = **103 feed jobs**. Compared with
**10,783 jobs-table payloads**, read through SQLite's `file:...?mode=ro` URI.
No scanner run or private-state write was performed.

Counts below count each board job once per matching identity; URL and title
columns overlap. ATS ID means an ATS ID recognized from the exposed apply URL.

| Board | Jobs | Same ATS ID | Same normalized URL | Same normalized company/title | Additional confirmed variant |
|---|---:|---:|---:|---:|---:|
| RemoteOK | 26 | 0 | 0 | 2 | 1 |
| WWR | 21 | 0 | 0 | 2 | 0 |
| Working Nomads | 6 | 0 | 2 | 2 | 0 |
| Jobicy | 50 | 0 | 0 | 3 | 0 |
| Total vs stored jobs | 103 | 0 | 2 | 9 | 1 |

The mutually exclusive breakdown is **0 ATS ID, 2 URL, 7 additional
company/title, 1 additional variant, 93 without a confirmed stored match**.
The variant has a client name in trailing title parentheses on Himalayas but
not RemoteOK; the description identifies that client in its opening line.
RemoteOK also contains UTF-8 text misdecoded as Latin-1. After repairing that
encoding for comparison, both descriptions share the same first 80 words.
Other superficially similar titles were checked against descriptions and kept
separate, including two different technical-leadership roles at one employer.

Also fetched five live watchlist boards, without a health output directory:
Canonical (96 jobs), Close (6), GitLab (204), Supabase (54), Vercel (10).
This adds one Jobicy/company-title overlap missing from stored jobs. Against
stored jobs plus these live boards the exclusive counts are **0 / 2 / 8 / 1**:
**11 known duplicate board jobs**. Existing identities recognized 10; the
additional variant escaped. After the fix, all 11 match, with **0 known escapes**.
Against the DB alone, recognized overlaps increase **9 → 10**, escapes **1 → 0**.
These are identity-replay counts before eligibility filtering, not a claim
that all these jobs would pass filters or had previously been reported.

The feeds also contain three repeated jobs internally. Merging the 103 board
records yields 100 jobs; merging them into the DB reference identities adds
90 jobs, or 89 with the live watchlist reference included. All 11 known external
overlaps and three internal copies merge before filtering. Previously, only
same-ID copies reached `_fill_missing`; other copies could lose salary/skills
when discarded later or an earlier board copy could win.

The merge now prefers `source=ats`, then other existing sources, then the four
new boards, preserving source order within each group. It uses IDs, normalized
URLs, normalized company/title, and the existing title/text identity. The
additional client suffix rule requires a single capitalized word repeated in
the description's opening line and matching substantial description text;
seniority and level suffixes remain distinct. It also checks stored reported
payloads for this variant, so historical fingerprints need no migration.
Company legal suffixes and Remote/Worldwide title labels were already normalized;
no broader fuzzy company or title matching was added.

All 103 exposed feed links point to board pages or board redirects, so **zero
live feed records directly exercise an ATS apply URL**. Working Nomads redirects
were inspected; this sample led to company signup pages, a form, and another job
platform, rather than recognized ATS job URLs. No speculative ID was assigned.
For five board/employer matches, replaying the live employer's actual apply URL
through `board_job` produced exactly the live ATS record's ID: three Greenhouse
jobs and two Ashby jobs. This verifies the existing ATS-ID path without claiming
that the feeds expose links they do not provide. The normal merge retains these
live employer URLs/IDs, descriptions, and watchlist status. An anonymized test
also checks ATS ID generation, tracking removal, and retained board attribution.

Regression tests use anonymized examples and cover source order, ID/URL/title
matches, company suffixes, salary/skill fill, encoding and client-title variants,
historical reports in either direction, seniority/level separation, transitive
aliases, and identical text under different company names.
