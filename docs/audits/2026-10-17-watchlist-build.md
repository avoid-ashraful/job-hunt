# Watchlist build throughput — backlog 22

Measured 2026-10-17, baseline `1732b70`, eight workers in both runs.

## Method

Copied the watchlist and resolution cache to `/tmp/watchlist-22`, then used
`build`'s pending selection with remote-jobs company files and tracked seeds.
Selected the first 30 of 122 pending companies in submission order: 27 full
resolutions and three cached-probe retries. Timed `resolve_company` concurrently
with the saved inputs, including the build's popularity-based ATS order. The
runs were sequential, with fresh process-local pacing state and live HTTP.
Database hints and HQ lookup were outside this measurement. Measurement writes
went to `/tmp`; production watchlist/cache files were read for the input copy
and cache audit.

Wrapped `Session.get`, `_pace` sleeps, and retry sleeps to count calls/statuses
and elapsed time by the existing `_host` pacing groups. Website calls use the
resolver's eight-second timeout. Counted each `Session.get` invocation, including
retries; redirects remain inside that invocation. Recorded timeout-call elapsed
time separately. The after run also timed `_links`; process CPU was recorded in
both runs. Diagnostic stack samples identified the baseline parsing stall.

Inputs, instrumentation, JSON results, and logs remain in `/tmp/watchlist-22`.
The harness is `measure.py`; baseline source is `watchlist-before.py`.

## Results

| Measurement | Before | After |
|---|---:|---:|
| Wall time, seconds | 948.15 | 142.13 |
| Companies/minute | 1.90 | 12.66 |
| Process CPU, seconds | 217.94 | 16.01 |
| ATS request calls | 1,411 | 753 |
| Website request calls | 54 | 54 |
| ATS HTTP elapsed, worker-seconds | 2,325.70 | 760.10 |
| Website HTTP elapsed, worker-seconds | 51.63 | 52.98 |
| Normal pacing sleeps, worker-seconds | 25.92 | 13.84 |
| Backoff pacing sleeps, worker-seconds | 4,486.96 | 176.71 |
| Separate 5xx retry sleeps, worker-seconds | 0 | 0 |
| ATS timeout calls / elapsed worker-seconds | 4 / 206.75 | 2 / 14.99 |
| Website timeout calls / elapsed worker-seconds | 0 / 0 | 0 / 0 |
| HTTP 429 responses | 4 | 4 |

Throughput improved **6.67×**. Timed calls and sleeps overlap across workers;
these are aggregate worker-seconds, not additive wall-time components. Their
elapsed times also include delays regaining the GIL during the baseline regex
stall; timeout-call time is a subset of HTTP time.

All 30 **complete result dictionaries** were identical, including failure reasons
and retry probes. This pending sample found zero supported boards in both runs.
The separate positive-cache audit below checks preservation of existing slug
candidates; this live sample measures unresolved-company throughput.

## Causes and changes

- Personio `.de` alone accumulated **4,485.92 → 175.71 worker-seconds** of backoff.
  Both runs received two 429s there. The baseline then received 78 404s, but only
  responses below 400 decayed its shared backoff. A missing slug now halves
  backoff just like a successful response. Already-scheduled waits, the 0.25s
  minimum interval, 429 growth/Retry-After handling, and quota blocks remain.
  Responses such as 403 and 5xx do not decay backoff. Workable returned a long
  quota window in both runs; `_BLOCKED` skipped it after one HTTP request each,
  without sleeping through that window.
- ATS subdomain regexes scanned suffixes of large embedded HTML tokens
  quadratically. Stack samples caught On The Go Systems' careers page in
  `_links`, with its worker consuming about **190 CPU seconds**. A negative
  lookbehind at the start of each variable hostname label prevents repeated
  suffix scans. All after-run link extraction took **4.65 wall / 4.44 CPU
  seconds** combined. The old parser exceeded five seconds on a 100,000-character
  synthetic token; the regression runs the new parser in a subprocess with a
  five-second deadline.
- Of **88 cached verified-slug boards**, the first matching candidate source was
  normalized company name for 74, compact name for 10, and website stem for 4.
  In the deduplicated candidate lists, their ranks were 74 first, 11 second,
  and 3 third. None required either synthetic `hq` suffix. Removed those two
  additions while retaining literal HQ names, explicit boards, and careers-link
  extraction. Full-resolution candidates in this sample fell **83 → 44**.

Normal interval pacing and website fetching were small costs. Provider order,
worker count, website timeouts, and request retry counts are unchanged.

## Per-host request calls

Host names use `_host`'s grouping, so Lever's EU and default endpoints share one
row, while Personio `.de` and `.com` have separate rows. Website calls are included.

| Host group | Before | After |
|---|---:|---:|
| angel.co | 1 | 1 |
| apply.workable.com | 1 | 1 |
| applytojob.com | 83 | 44 |
| ashbyhq.com | 83 | 44 |
| bamboohr.com | 83 | 44 |
| breezy.hr | 83 | 44 |
| dover.com | 85 | 46 |
| factorialhr.com | 83 | 44 |
| goldfireagency.com | 1 | 1 |
| greenhouse.io | 83 | 44 |
| homerun.co | 83 | 44 |
| invicti.com | 2 | 2 |
| join.com | 1 | 1 |
| lever.co | 166 | 88 |
| madewithover.com | 1 | 1 |
| mobilejazz.com | 2 | 2 |
| molteo.com | 1 | 1 |
| netapp.com | 1 | 1 |
| netguru.com | 2 | 2 |
| netsparker.com | 2 | 2 |
| nocodenoprob.com | 1 | 1 |
| nodesource.com | 1 | 1 |
| nozbe.com | 2 | 2 |
| ombulabs.ai | 1 | 1 |
| ombulabs.com | 2 | 2 |
| omniti.com | 2 | 2 |
| onthegosystems.com | 2 | 2 |
| opencraft.com | 2 | 2 |
| optoro.com | 1 | 1 |
| oracle.com | 3 | 3 |
| oreilly.com | 3 | 3 |
| outsourcingdev.com | 3 | 3 |
| parabol.co | 1 | 1 |
| parexel.com | 3 | 3 |
| parkassist.com | 2 | 2 |
| parse.ly | 3 | 3 |
| particular.net | 2 | 2 |
| pathable.com | 2 | 2 |
| payfully.co | 1 | 1 |
| payu.com | 2 | 2 |
| personio.com | 78 | 44 |
| personio.de | 85 | 46 |
| pinpointhq.com | 83 | 44 |
| recruitee.com | 83 | 44 |
| rippling.com | 83 | 44 |
| smartrecruiters.com | 83 | 44 |
| teamtailor.com | 83 | 44 |
| webdesignedinburgh.com | 1 | 1 |

## Validation

`uv run pytest -q`: **564 passed** (baseline: 544). New cases cover 404 backoff
decay without bypassing scheduled waits, minimum interval recovery, preservation
of backoff for other errors, retained slug variants, large embedded tokens, and
subdomain link extraction. Existing quota, retry, ordering, override, and cache
coverage also passes.

Sample companies: GoldFire Agency, OutsourcingDev, Human Made, Intuition Machines, Inc., Iterative, Mobile Jazz, Molteo, NetApp, Netguru, Netsparker, Nettl Edinburgh, No Code No Problem, NodeSource, Nozbe, Ombu Labs, OmniTI, On The Go Systems, OpenCraft, Optoro, Oracle, O'Reilly Online Learning, Over, Parabol, Parexel, Park Assist, Parsely, Particular Software, Pathable, Payfully, PayU.
