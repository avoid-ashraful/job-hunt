# Job source feasibility (probed from this VPS, 2026-10-17)

The scanner only reads public endpoints, identifies itself, and follows published access rules. A remote label alone does not establish Bangladesh eligibility; the existing location and company rules make that decision.

| Site | Feasible? / method | Auth | Robots and terms | Value for this candidate | Decision |
|---|---|---|---|---|---|
| [HN jobs](https://news.ycombinator.com/jobs) | Yes; posts indexed as `job` stories in the public Algolia API | None | `robots.txt` allows `/jobs` with 30 s crawl delay; scanner uses Algolia JSON | Medium; direct startup posts, but many US only | Add via job index |
| [Ask HN: Who is hiring?](https://hn.algolia.com/api) | Yes; Algolia story/comment JSON, current and previous month | None | Algolia `robots.txt` returned 404; API is [documented](https://hn.algolia.com/api) | High; direct employer comments often state remote geography | Add, top-level remote comments |
| [Himalayas](https://himalayas.app/docs/remote-jobs-api) | Yes; `/jobs/api/search` with `worldwide=true` | No key needed; free key raises quota | [Robots](https://himalayas.app/robots.txt) allows API path; [terms](https://himalayas.app/terms) require a Himalayas application link and attribution | High for location, mixed quality for pay/company | Add; retain Himalayas link as application URL |
| [Workable search](https://jobs.workable.com/) | Yes; public `/api/v1/jobs?query=…&workplace=remote` JSON | None | [Robots](https://jobs.workable.com/robots.txt) restricts search-page query URLs, not API; use low-rate API calls | Medium; employer ATS jobs but many country restricted | Add; `remote=true` yielded zero, `workplace=remote` works |
| [Recruitee](https://recruitee.com) | Yes; public `https://<board>.recruitee.com/api/offers/` JSON | None | Public employer board feed; respects standard pacing | High; remote EU/worldwide employers | Add ATS adapter |
| [Breezy HR](https://breezy.hr) | Yes; public `https://<board>.breezy.hr/json` JSON | None | Public board feed, zero auth | High; tech startups hiring remotely | Add ATS adapter |
| [Personio](https://personio.com) | Yes; public `https://<board>.jobs.personio.(de\|com)/xml` XML feed | None | Documented public XML feed for job boards | High; DACH & European remote employers | Add ATS adapter |
| [BambooHR](https://bamboohr.com) | Yes; public `https://<board>.bamboohr.com/careers/list` JSON | None | Requires `Accept: application/json` header | Medium-High; mid-size remote employers | Add ATS adapter |
| [Pinpoint](https://pinpointhq.com) | Yes; public `https://<board>.pinpointhq.com/postings.json` JSON | None | Public board feed, zero auth | Medium; tech companies | Add ATS adapter |
| [Teamtailor](https://teamtailor.com) | Yes; public `https://<board>.teamtailor.com/jobs.rss` XML feed | None | Public RSS feed per tenant | Medium-High; European remote companies | Add ATS adapter |
| [Homerun](https://homerun.co) | No; no public JSON/XML API endpoint; routes wildcard fallback to SPA HTML | None | Dynamic client-side rendering only | Low | Skip automation |
| [Jobvite](https://jobvite.com) | No; public JSON API retired; XML feed requires scraping internal companyEId and hits aggressive 429 rate limiting (30s cooldown) | None | Unstable vanity routing, heavy rate limits | Low | Skip automation |
| [Wellfound](https://wellfound.com/jobs) | Limited; public HTML loads but search paths are restricted | Search/login may require account | [Robots](https://wellfound.com/robots.txt) disallows `/search` and `/_jobs/`; no bypass | Medium if signed in, low as feed | Skip automation |
| [Built In](https://builtin.com/jobs/remote) | Limited; public HTML and job sitemap | None for public pages | [Robots](https://builtin.com/robots.txt) blocks `/search/` and some job query patterns | Low; primarily US market and US hiring restrictions | Skip broad crawling |
| [Indeed](https://www.indeed.com/) | No reliable feed from this IP; search returned 403 | None for browsing | [Robots](https://www.indeed.com/robots.txt) restricts some endpoints; no anti-bot evasion | Low; mostly US authorization | Skip JobSpy dependency |
| [ZipRecruiter](https://www.ziprecruiter.com/) | No; search returned Cloudflare 403 | None for browsing | [Robots](https://www.ziprecruiter.com/robots.txt) disallows `/` for generic agents | Low; mostly US authorization | Skip JobSpy dependency |
| [Jobright](https://jobright.ai/remote-jobs) | Public landing page, no verified public job API | Account for personalized matching | [Robots](https://jobright.ai/robots.txt) allows public jobs but restricts recommendations/profile | Low; largely US market | Skip until supported public feed exists |
| [FlexJobs](https://www.flexjobs.com/) | VPS request timed out | Paid account for full listings | Robots could not be checked from this VPS; no paid access automation | Low until subscribed | User may subscribe manually |
| [G2i](https://www.g2i.co/) | Public marketplace landing page; no public job feed found | Developer application | `robots.txt` returned 404; no listing endpoint verified | Possible high-paying contracts after vetting | User should apply to join |
| [MyGreenhouse](https://my.greenhouse.io/users/sign_in) | Sign-in returned 406; no public search endpoint verified | Job-seeker login | [Robots](https://my.greenhouse.io/robots.txt) allows `/`; access still requires service consent | Low incremental value over public Greenhouse boards | Skip; use ATS boards |
| [Levels.fyi](https://www.levels.fyi/?tab=levels) | Public site, no stable documented salary JSON verified | Public browsing | [Robots](https://www.levels.fyi/robots.txt) allows generic agents; API terms/access unverified | Salary context, not jobs | No fetcher until a supported API is found |
| Overleaf software-engineer resume template | Not a job source | Usually Overleaf account to edit | Not relevant to scanner | Resume aid only | Skip |

The [Himalayas API](https://himalayas.app/api) refreshes daily and warns that unauthenticated callers share a public allowance. A free key is optional if the daily request budget becomes unreliable.

## Company seed lists

Run `uv run python -m scanner --config config.example.yml watchlist seeds` to refresh
`watchlist.seeds.auto.yml`. Existing entries win by normalized company name; the
hand-written `watchlist.seeds.yml`, including comments and board overrides, stays
untouched. `watchlist build` combines both files through its existing `seeds_file`
interface. These are leads for ownership-checked ATS resolution, not guarantees of
current Bangladesh eligibility, suitable roles, or Deel/contract payment.

Used (curl probes on this VPS, 2026-10-17):

- [awesome-remote-job](https://github.com/lukasz-madon/awesome-remote-job): public raw
  Markdown returned 200; repository last pushed September 2026. Only company-section
  entries explicitly describing worldwide hiring are imported. The live run added
  3 tracked entries (Ergeon, Iterative, TestGrid); Ergeon and Iterative also occur in
  remoteintech's existing worldwide population, so only TestGrid expands that pool.
- [remoteintech](https://github.com/remoteintech/remote-jobs): public source ZIP
  returned 200. Added 9 entries whose original `Region` section explicitly includes
  Asia/APAC and whose frontmatter is not worldwide (the latter is already read by
  build). Coarse `asia-pacific` tags alone are unsafe: several mean India or Australia
  only. Narrow subregions/country lists and qualifying timezone restrictions are
  skipped. Agency/marketplace/crowdsourcing evidence and known India/Bangladesh HQ
  are excluded using source evidence and configured company filters/HQ overrides.

Rejected/deferred:

- [Himalayas](https://himalayas.app/docs/remote-jobs-mcp): docs, robots, and public
  jobs API returned 200; `/companies?worldwide=true` and `/companies/api` returned
  403. The documented no-login MCP `get_companies` and `search_companies` work, but
  `worldwide=true` returned 33,186 company entries, including universities and a
  community bank with zero jobs, labeled “Global.” Company details expose HQ and
  website but no explicit hiring-country evidence. Do not turn that label into a
  worldwide hiring assertion. The JSON API documents jobs endpoints only; job
  ingestion remains handled by the existing source. [Terms](https://himalayas.app/terms)
  permit documented public API/MCP use; no blocked company pages were scraped.
- [established-remote](https://github.com/yanirs/established-remote): raw Markdown
  returned 200, repository last pushed July 2026. Its general introduction describes
  global hiring, but individual rows lack worldwide/APAC eligibility; includes
  consultancies and staffing platforms. Insufficient row-level evidence.
- [Remotive](https://remotive.com/robots.txt): robots returned 200 and disallows
  `/api/*`, company filter feeds, and `/remote-companies/page`; no directory crawl.
- [Working Nomads](https://www.workingnomads.com/robots.txt): robots allows access,
  but `/companies` returned 404; no usable structured company directory found.
- [We Work Remotely](https://weworkremotely.com/robots.txt): robots permits public
  pages, but `/remote-companies` returned 403 from this VPS; no bypass attempted.
- Workable/Ashby/Greenhouse directories: ATS presence establishes neither worldwide
  hiring nor Bangladesh eligibility, so these are resolution targets only.

The committed import contains 12 additions to the hand seed list, 10 beyond
remoteintech's worldwide pool. Older community entries and unknown HQ remain data
quality limits; current job location/timezone checks still apply.
