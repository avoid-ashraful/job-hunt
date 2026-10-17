# Apply evidence audit, 2026-10-16 and 2026-10-17

Reviewed every Apply row in the last two reports: `2026-10-16.md` (11)
and `2026-10-17.md` (1). Read the private SQLite database through
`file:/home/ubuntu/job_hunt/private/state/scanner.sqlite?mode=ro` and read the
reports without modifying either. Matched report refs to historical report,
location and LLM decisions; judged Bangladesh eligibility independently from
the complete stored description, rather than accepting the deciding verdict.
These are posting-supported hiring judgments, not employer confirmations.

Before: **9/12 (75%)** have sufficient evidence someone in Bangladesh can be
hired; three are unconfirmed and count as precision failures. After replaying
only the new final guard on those same Apply decisions: **6/6 (100%)** retained,
six demoted to Check. This is a retrospective guard replay, not a new live run.
The existing LLM evidence validator was not replayed, so its separate changes
cannot inflate this guard's measured improvement. Current stored descriptions
are used because the database does not snapshot descriptions per run.

On October 16: 8/11 before (72.7%), 6/6 after (100%). On October 17: 1/1 before
(100%), zero retained after (precision undefined). The sample is small; the
higher precision costs three supported jobs being moved to Check, not dropped.

All quotes below are the complete historical decision evidence (the report
truncates some at 180 characters). Outcomes refer to the new guard.

1. **2026-10-16 `d76a6d`, Geniusee — Middle AI/Python Engineer 2.**
   Decided by `lock_place_ok.anywhere_within`.
   Quote: “* Remote work from anywhere in the world or in one of our offices in Kyiv, Lviv, or Warsaw.”
   Judgment: **yes**; worldwide remote work is explicit, offices are optional.
   After: **Apply**.
2. **2026-10-16 `8264a9`, InvestEngine — Middle Backend Engineer, Backoffice team (Python).**
   Decided by `llm:claude:sonnet`.
   Quote: “*Remote-first and flexible* — work from anywhere, at your own pace”.
   Judgment: **unconfirmed**; flexibility perk without hiring-country scope.
   A London HQ alone does not prove a restriction either.
   After: **Check**.
3. **2026-10-16 `a14118`, Sezzle — Senior Software Engineer, Security Platform.**
   Decided by `llm:claude:sonnet`.
   Quote: “We believe the best engineers can do world-class work from anywhere, and we hire accordingly.”
   Judgment: **yes**; explicitly links anywhere work to hiring accordingly.
   After: **Check**; conservative false demotion, selected quote lacks an accepted strong pattern.
4. **2026-10-16 `e8afb3`, CloudLinux — Full Stack Engineer (Angular & Python) (remote work).**
   Decided by `positive.worldwide`.
   Quote: “* Fully remote work with flexible working hours, that allows you to schedule your day and work from any location worldwide.”
   Judgment: **yes**; worldwide permission, without a role-specific country constraint.
   After: **Apply**.
5. **2026-10-16 `6a161b`, BforeAI — Back-End Software Engineer - Product Services and Detection.**
   Decided by `llm:cache` (the historical decision stores no original model).
   Quote: “We are a location-independent company with a fully distributed team.”
   Judgment: **yes**, based on additional body text: “We use an Employee of Record service to facilitate seamless global hiring processes” and “For countries not supported by our EOR partner, talk to us about being a contractor.”
   No foreign visa is needed when working in one's home country.
   After: **Check**; conservative false demotion because the selected quote only describes the team.
6. **2026-10-16 `2be5e8`, Xsolla — Middle/Senior Backend Engineer.**
   Decided by `weak_lock_place_ok.location_line`.
   Quote: “*Location:* Remote (worldwide)”.
   Judgment: **yes**; body employment details explicitly allow worldwide work
   and an employment entity or EOR arranged per candidate location.
   After: **Apply**; this quote is in the description, not just metadata.
7. **2026-10-16 `4c31d5`, Naphora Games Group — Senior Backend Engineer.**
   Decided by `llm:cache` (original model unavailable).
   Quote: “This is a *fully remote position open to candidates from any location* , with availability to work in alignment with our Saudi Arabia working schedule (Sunday–Thursday, 9:00 AM–5:00 PM KSA time).”
   Judgment: **yes**; candidate scope is unrestricted, KSA working hours are compatible.
   After: **Apply**; explicit candidates-from-any-location wording is a strong equivalent of worldwide hiring.
8. **2026-10-16 `a46a6b`, Hivex — Senior AI Python Developer.**
   Decided by `lock_place_ok.anywhere_within`.
   Quote: “* ﻿Remote job with the freedom to work from anywhere in the world.”
   Judgment: **yes**; unrestricted worldwide remote work is explicit.
   After: **Apply**.
9. **2026-10-16 `74bc69`, Hivex — Senior Python developer.**
   Decided by `lock_place_ok.anywhere_within`.
   Quote: “* *Remote job with the freedom to work from anywhere in the world.*”
   Judgment: **yes**, for the same explicit permission on this separate role.
   After: **Apply**.
10. **2026-10-16 `6d5de1`, DunePays — Senior Backend Engineer.**
    Decided by `llm:claude:sonnet`.
    Quote: “Work from anywhere with flexible hours”.
    Judgment: **unconfirmed**; generic perk on a multi-role careers page.
    The engineering opening says “Remote / London Full-time”; no positive Bangladesh hiring evidence.
    After: **Check**.
11. **2026-10-16 `f421f1`, Simpul Technologies — Backend Engineer.**
    Decided by `llm:claude:sonnet`.
    Quote: “*100% Remote Work:* Work from anywhere”.
    Judgment: **unconfirmed**; benefits-list perk without hiring scope.
    Indonesian salary and social security benefits do not independently prove a residence restriction.
    After: **Check**.
12. **2026-10-17 `102472`, CreativeWorks Systems — Full-Stack Python Developer.**
    Decided by `weak_lock_place_ok.location_line`.
    Quote: “*Location:* Remote - Anywhere”.
    Judgment: **yes**; unrestricted location is explicitly stated in the role's body,
    with no conflicting hiring restriction.
    After: **Check**; conservative false demotion: bare Anywhere falls short of the
    configured strong body patterns, and `source=freehire` is not an employer ATS source.

## Guard boundaries

Missing `rules.apply_evidence` uses built-in defaults. Configured patterns replace
those defaults. Quotes must be contiguous normalized body text; title-only quotes,
ellipsis-stitched fragments and aggregator location metadata do not qualify.
Exact global locations from `ats` or `workable` sources qualify independently of
the selected quote. Merely setting `watchlist=True` does not establish ATS provenance.
Negations, conditional entity-country wording, temporary travel perks and obvious
team/customer context are conservatively rejected. The guard does not resolve every
possible semantic contradiction; it is an additional gate after eligibility screening.

Demotions are counted before subsequent collapse/liveness/HQ filtering, included in
report run stats and the run log, and persisted as `apply_evidence` Check decisions.
