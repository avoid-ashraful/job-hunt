# Eligibility decision audit — 2026-10-17

Audited against hiring from Bangladesh without relocation or foreign work authorization. Worldwide hiring, Asia/APAC including Bangladesh, or an explicit allowed-country list including Bangladesh qualifies. Working-hours overlap is acceptable. Country tags, current team distribution, country-specific benefits and a bare work-anywhere perk do not establish hiring eligibility. An incomplete country list does not prove exclusion. Unresolved conflicting scope is `unclear`.

## Data and sampling

Opened `/home/ubuntu/job_hunt/private/state/scanner.sqlite` using SQLite URI `mode=ro`. Read all 232 `stage='llm'` decisions (216 distinct jobs): 185 on October 16 and 47 on October 17. Read their joined `jobs.payload`, all 348 cached `llm_location` outputs, and all 37 `llm_calls` rows (25 successful requests; 557 job attempts including failures, screening and audits). Every LLM decision has a cached output. No private file was written; raw exports remain outside the repository.

`llm_calls` stores model, purpose, counts, duration and character totals, not raw request/response bodies. Per-job cached responses retain model, prompt hash, evidence and allowed locations. Decision records retain the final label and evidence, with negative evidence truncated to 160 characters. Job payloads are the latest stored descriptions, not versioned historical inputs. Therefore this is an audit of stored evidence and current descriptions, not a claim to reconstruct exact historical prompts, batches or descriptions. All sampled cache entries use prompt hash `73ad7322c1e0`; the latest private prompt was inspected read-only but cannot be proven identical to its historical version. No live job pages or new model calls were used.

Reproducible selection: within each stratum, sort ascending by SHA-256 of `run_date + '|' + job_id`. LLM quotas: all 14 anywhere, 20 region_locked, 12 unclear, all 7 timezone_only, all 7 work_authorization_required. For rules, group eligible and drop decisions separately by rule name, sort each group by the same hash, then round-robin alphabetically sorted rule groups until 20 per outcome. No eligible `includes_candidate` LLM decision exists in the decision log (one exists in the wider cache, which also includes rule audits). Samples contain 56 unique LLM jobs and 39 unique rule jobs; repeated job/run decisions are explicitly retained because decisions are the sampling unit. This stratification overrepresents rare outcomes and repeated employer templates; do not interpret the pooled result as population precision or compare model providers from it.

Each row was manually judged from the description, including adjacent list items and text beyond the logged quote. Correct means the stored label is supported by that evidence; supported negative decisions with a misleading geography explanation are called out separately. Location-stage `drop` is presented as `locked`. `timezone_only` remains Check and is not treated as confirmed worldwide hiring. Accuracy of an `unclear` label is reported as label precision, not conventional positive predictive value.

## Results before changes

| Stage / outcome | Population | Reviewed | Correct | Precision |
| --- | ---: | ---: | ---: | ---: |
| LLM / anywhere | 14 | 14 | 6 | 42.9% |
| LLM / region_locked | 95 | 20 | 12 | 60.0% |
| LLM / work_authorization_required | 7 | 7 | 4 | 57.1% |
| LLM / unclear | 109 | 12 | 7 | 58.3% |
| LLM / timezone_only | 7 | 7 | 5 | 71.4% |
| Rules / eligible | 24 | 20 | 15 | 75.0% |
| Rules / locked | 212 | 20 | 18 | 90.0% |

LLM: 34/60 (56.7%) supported labels; rules: 33/40 (82.5%). These numbers describe historical decisions before any fix. No claim of improved LLM precision is made without rerunning models and reviewing fresh outputs.

## Error patterns and examples

- Overstated worldwide hiring: 8/14 LLM eligible decisions lack conclusive permission. DunePays, Conference App, Simpul Technologies, InvestEngine and others quote flexibility perks. RemoteJobsOne has a partial 55-country list alongside an anywhere label; Bangladesh cannot be assumed included or excluded. Predikt AI is a useful counterexample: its full description explicitly removes region/country limits despite its weak quoted evidence.
- Unsupported locks: 8/20 region_locked decisions use labels, optional Europe work, or existing employees' locations. ODAIA's “employees located across Canada and the US” is not a hiring restriction. Scotiabank's office label is similarly insufficient. Some correct drops still have unreliable explanations: GoCharting has conflicting Chennai/Europe text, Phoxygen includes Euro-country alternatives, and RemoteJobsOne quotes US/UK/CAN despite a UAE-specific candidate scope.
- Overstated authorization: 3/7 authorization decisions infer a requirement from KeyBank's no-sponsorship wording (two run decisions) or Stack AV's conditional security review. Valence and the US citizen/green-card posting do contain explicit requirements.
- Missed restrictions: 5/12 unclear and 2/7 timezone_only decisions overlook full-body restrictions. Opaxa explicitly requires US authorization; Anadyr Horizon requires UK/EU authorization; Wintermute (listed under Pantera Capital) rejects fully remote candidates. Cloudflare's allowed locations are in a multiline list. ElevenLabs' role-specific geographic endpoints differ from permission to work overlapping hours.
- Rule errors: three Tether decisions promote current worldwide team distribution to eligibility; SmartRecruiters captures “Europe, collaborating with a global team” and incorrectly recognizes “global” as candidate permission; RemoteJobsOne's full “United Arab Emirates” is missing from `geo`, allowing a conflicting global label to win. Upstart's US-only stock purchase benefit becomes a residence lock. Premier Global Links' remote-first footprint becomes a residence requirement.

## Errors by rule

| Rule | Reviewed | Errors | Error IDs |
| --- | ---: | ---: | --- |
| lock.anywhere_within | 2 | 0 | — |
| lock.candidate_based | 2 | 0 | — |
| lock.entity_countries | 2 | 0 | — |
| lock.following_places | 2 | 0 | — |
| lock.hiring_in | 2 | 0 | — |
| lock.must_reside | 2 | 0 | — |
| lock.only_hiring | 1 | 0 | — |
| lock.open_to | 1 | 0 | — |
| lock.place_only | 1 | 1 | R29 |
| lock.remote_from | 1 | 0 | — |
| lock.remote_within | 1 | 1 | R31 |
| lock.right_to_work | 1 | 0 | — |
| lock.russia | 1 | 0 | — |
| lock.ukraine | 1 | 0 | — |
| lock_place_ok.anywhere_within | 6 | 0 | — |
| lock_place_ok.remote_from | 3 | 3 | R02, R07, R11 |
| lock_place_ok.remote_within | 1 | 1 | R03 |
| positive.worldwide | 5 | 0 | — |
| weak_lock_place_ok.location_line | 5 | 1 | R16 |

## Changes and validation

Committed changes in `scanner/eligibility.py` extend description-quote validation to `work_authorization_required` and reject ellipsis-only evidence (previously an empty `all(...)` could pass). An appended runtime prompt section addresses perks, incomplete lists, team/office distribution, conditional security/no-sponsorship language, conflicting scope and geographic residence versus working hours. It applies to private prompts without modifying them and changes the existing prompt hash so prior cached results are not reused under the new instructions. Anonymized regression cases cover authorization evidence and the ellipsis bypass; a fake-key, local-only screener test verifies prompt delivery and old-cache invalidation. No new dependencies.

The existing guard already downgrades seven sampled anywhere outputs on replay (L01/L03/L05/L08/L10/L11/L14); this does not retroactively repair the historical decisions. L10 is a false downgrade because the complete description supplies broader permission than its selected quote. The new quote guard cannot determine whether a real quote logically supports the verdict; the runtime instructions address that semantic problem, but model behavior has not been validated by a new live run. Snippet extraction still misses some multiline lists and authorization wording, and truncation can omit decisive text. Those remain limitations; this change does not send full descriptions or add a second model pass.

The proposed private-config patch below was applied only to an in-memory copy. Replaying all 40 sampled rule jobs changes exactly the seven erroneous decisions: R03 becomes locked; R02/R07/R11/R16/R29/R31 become ambiguous. The other 33 outcomes stay unchanged. The proposed configuration also preserves all 24 existing location regression examples and two added in-memory cases for Europe/Asia and Europe/including Bangladesh country lists. This is a regression replay on the audited sample, not independent validation. The parenthesis exclusion intentionally routes parenthesized country-only phrases to weaker rules/LLM; the team and remote-first exclusions are similarly conservative. The capture stops before comma clauses beginning with collaborating/working, preserving comma-separated country lists such as Europe, Asia. These targeted safeguards do not constitute a full geography parser.

Validation: `uv run pytest -q` — **336 passed**. Backlog row 15 marked `done (branch)`. Private files unchanged; no push.

## Private-config patch to apply

This is an exact diff against the read-only configuration inspected for this audit. It is not applied or copied as a tracked configuration file.

```diff
--- a/private/config/config.yml
+++ b/private/config/config.yml
@@ -227,2 +227,2 @@
-      remote_within: '\b(?:remote|work options|working)\b[^.;\n]{0,25}?\b(?:within|in|across|spanning|throughout)\s+(?:the\s+)?(?P<place>(?:continental\s+)?(?:u\.?s\.?a?|united states|canada|uk|united kingdom|eu|europe|european union|eea|germany|france|spain|portugal|poland|netherlands|ireland|india|brazil|mexico|argentina|colombia|latam|latin america|australia|new zealand|philippines|ukraine|israel|south africa|nigeria|kenya|pakistan|egypt|turkey|romania)\b[^.;\n]{0,40})'
-      place_only: '\b(?P<place>u\.?s\.?a?|united states|canada|uk|eu|europe|latam|emea|north america)[\s-]+(?:only|based only|residents only)\b'
+      remote_within: '\b(?:remote(?!-first)|work options|working)\b[^.;\n]{0,25}?\b(?:within|in|across|spanning|throughout)\s+(?:the\s+)?(?P<place>(?:continental\s+)?(?:u\.?s\.?a?|united states|canada|uk|united kingdom|eu|europe|european union|eea|germany|france|spain|portugal|poland|netherlands|ireland|india|brazil|mexico|argentina|colombia|latam|latin america|australia|new zealand|philippines|ukraine|israel|south africa|nigeria|kenya|pakistan|egypt|turkey|romania)\b(?:(?!,\s*(?:collaborating|working)\b)[^.;\n]){0,40})'
+      place_only: '\b(?P<place>u\.?s\.?a?|united states|canada|uk|eu|europe|latam|emea|north america)[\s-]+(?:only|based only|residents only)\b(?!\s*\))'
@@ -236 +236 @@
-      remote_from: '\b(?:work(?:ing)?\s+)?remote(?:ly)?\s+from\s+(?:the\s+)?(?P<place>[^.;:\n]{2,120})'
+      remote_from: '^(?!.*\b(?:our|the)\s+team\b).*?\b(?:work(?:ing)?\s+)?remote(?:ly)?\s+from\s+(?:the\s+)?(?P<place>[^.;:\n]{2,120})'
@@ -265 +265 @@
-    geo: '\b(u\.?s\.?a?|united states|america[s]?|canada|uk|united kingdom|eu|eea|emea|europe(an)?|latam|latin america|south america|apac|asia|africa|middle east|mena|india|pakistan|bangladesh|philippines|vietnam|indonesia|singapore|australia|new zealand|brazil|mexico|argentina|colombia|chile|peru|germany|france|spain|portugal|poland|netherlands|ireland|italy|sweden|norway|denmark|finland|switzerland|austria|belgium|czech\w*|romania|bulgaria|ukraine|serbia|croatia|greece|turkey|israel|egypt|nigeria|kenya|south africa|japan|korea|china|taiwan|hong kong|malaysia|thailand|uae|dubai|saudi|qatar|deutschland|україн\w*|рф|росси\w*|following (?:states|countries|locations|regions|cities)|entit(?:y|ies))\b'
+    geo: '\b(u\.?s\.?a?|united states|united arab emirates|america[s]?|canada|uk|united kingdom|eu|eea|emea|europe(an)?|latam|latin america|south america|apac|asia|africa|middle east|mena|india|pakistan|bangladesh|philippines|vietnam|indonesia|singapore|australia|new zealand|brazil|mexico|argentina|colombia|chile|peru|germany|france|spain|portugal|poland|netherlands|ireland|italy|sweden|norway|denmark|finland|switzerland|austria|belgium|czech\w*|romania|bulgaria|ukraine|serbia|croatia|greece|turkey|israel|egypt|nigeria|kenya|south africa|japan|korea|china|taiwan|hong kong|malaysia|thailand|uae|dubai|saudi|qatar|deutschland|україн\w*|рф|росси\w*|following (?:states|countries|locations|regions|cities)|entit(?:y|ies))\b'
```

## Reviewed decisions

Quotes below are short excerpts; judgments use the full stored description. Company labels are those in source data and may be aggregators. IDs map to the source keys in the manifest after the tables.

| ID | Company | Logged label | Reviewed label | Supported | Short evidence | Judgment |
| --- | --- | --- | --- | --- | --- | --- |
| L01 | DunePays | anywhere | unclear | no | “Work from anywhere with flexible hours” | Bare perk on a multi-role careers page; country hiring permission absent. |
| L02 | RemoteJobsOne | anywhere | unclear | no | “open to candidates based in 55 countries, including” | 55-country hiring scope is incomplete; worldwide label conflicts with it. |
| L03 | Remote Worker LTD. | anywhere | unclear | no | “Work from anywhere (WFA)” | Bare perk; hybrid environment also mentioned. |
| L04 | Naphora Games Group | anywhere | anywhere | yes | “open to candidates from any location” | Any location explicitly allowed; KSA working hours are acceptable. |
| L05 | Energy Jobline ZR | anywhere | unclear | no | “offering the freedom to work from anywhere” | Bare anywhere flexibility; no worldwide hiring commitment. |
| L06 | bforeai | anywhere | anywhere | yes | “For countries not supported by our EOR partner, talk to us about being a contractor.” | Global EOR hiring and contractor fallback explicitly provided. |
| L07 | Devconnectplatform | anywhere | anywhere | yes | “You can work from anywhere globally” | Explicit global work permission. |
| L08 | Conference App | anywhere | unclear | no | “Work from anywhere with flexible hours that fit your lifestyle” | Bare flexibility perk; worldwide customers and team size do not prove hiring scope. |
| L09 | Kibbi | anywhere | anywhere | yes | “If you’re based elsewhere, you’ll be hired via Remote.com” | Global remote employer and explicit elsewhere EOR hiring. |
| L10 | Predikt AI | anywhere | anywhere | yes | “you aren’t limited to a particular region or country” | Full description says developers are not limited to a region or country. |
| L11 | Simpul Technologies | anywhere | unclear | no | “Work from anywhere” | Bare perk; benefits and currency do not establish or disprove worldwide hiring. |
| L12 | RemoteJobsOne | anywhere | unclear | no | “open to candidates based in 55 countries, including” | Same incomplete 55-country scope as L02, on another run. |
| L13 | Sezzle | anywhere | anywhere | yes | “we hire accordingly” | Explicit anywhere hiring commitment; country tag alone cannot override it. |
| L14 | InvestEngine | anywhere | unclear | no | “work from anywhere, at your own pace” | Bare flexibility perk without hiring scope. |
| L15 | SafeFleet s.r.l. Italia | region_locked | region_locked | yes | “candidates must be resident in Italy” | Explicit residence requirement. |
| L16 | Wynploy | region_locked | unclear | no | “Location: Remote India India” | Country label alone; no residence requirement. |
| L17 | EPAM | region_locked | unclear | no | “Remote - Colombia, Mexico” | Country label alone; global teams are not hiring scope. |
| L18 | Platform.sh | region_locked | region_locked | yes | “currently focused on hiring for this role” | Role-specific country hiring list and authorization requirement. |
| L19 | jobs.scotiabank.com | region_locked | unclear | no | “Location(s): Canada : Ontario : Toronto” | Office location label alone. |
| L20 | Platform.sh | region_locked | region_locked | yes | “currently focused on hiring for this role” | Role-specific country hiring list and authorization requirement. |
| L21 | Ruzora | region_locked | region_locked | yes | “open to candidates across Latin America” | Explicit candidate scope across Latin America. |
| L22 | jobs.scotiabank.com | region_locked | unclear | no | “Location(s): Canada : Ontario : Toronto” | Same office label as L19, on another run. |
| L23 | GoCharting | region_locked | region_locked | yes | “In-person in our Chennai office” | Full body requires in-person Chennai work; either conflicting region excludes the candidate. Europe-only explanation is unreliable. |
| L24 | Canonical Group Ltd | region_locked | region_locked | yes | “remote role based in the EMEA region” | Role-specific EMEA residence overrides global-company boilerplate. |
| L25 | Remote Jobs | region_locked | region_locked | yes | “within the country of employment” | Country-of-employment remote policy plus Spain role location; Spain hiring scope supported by combined text. |
| L26 | ODAIA | region_locked | unclear | no | “employees located across Canada” | Existing employee distribution does not restrict new hires. |
| L27 | DearDoc | region_locked | unclear | no | “Mexico/Remote” | Country label alone. |
| L28 | RemoteJobsOne | region_locked | region_locked | yes | “open to candidates based in United Arab Emirates” | Explicit UAE candidate scope excludes the candidate; quoted US/UK/CAN label is inconsistent. |
| L29 | greenhouse | region_locked | region_locked | yes | “Location:* Argentina (remote)” | Argentina-specific contractor engagement; Deel does not expand the hiring scope. |
| L30 | Kibbi | region_locked | region_locked | yes | “open only to candidates residing in Alberta” | Explicit province residence requirement in full description supports the drop. |
| L31 | Lever, Inc. | region_locked | unclear | no | “with the opportunity to work from Europe” | Opportunity to work from Europe does not say Europe only. |
| L32 | Phoxygen | region_locked | region_locked | yes | “Home based in Euro countries.” | France/Euro-country home-based options both exclude the candidate; France-only explanation overstates restriction. |
| L33 | Lockwood Publishing Ltd | region_locked | unclear | no | “Remote, UK” | UK label and optional meetups do not establish a residence requirement. |
| L34 | Physitrack | region_locked | region_locked | yes | “Poland* only” | Explicit Poland-only applications. |
| L35 | EPAM Systems | unclear | unclear | yes | “Relocation opportunities within our offices in 55+ countries” | Relocation perk and country tags do not establish hiring scope. |
| L36 | Promptly Health | unclear | unclear | yes | “Promptly Health, based in Portugal” | Company headquarters, not candidate residence. |
| L37 | FCE Global | unclear | unclear | yes | “Remote, with potential relocation to the UAE” | Potential relocation is not a mandatory relocation requirement. |
| L38 | SS&C | unclear | unclear | yes | “Remote/Hybrid Work Model” | Country tag and remote/hybrid policy without explicit hiring scope. |
| L39 | Cloudflare | unclear | region_locked | no | “Available Locations” | Available Locations section lists US cities and Canada; multiline list was missed. |
| L40 | Opaxa | unclear | work_authorization_required | no | “US work authorization required.” | Explicit US authorization in full description was missed. |
| L41 | Pantera Capital | unclear | region_locked | no | “unable to accept fully remote candidates” | Fully remote candidates explicitly rejected; relocation cannot satisfy the candidate constraint. |
| L42 | ElevenLabs | unclear | region_locked | no | “anywhere between the US East Coast and Europe” | Role location is bounded by US East Coast and Europe; overlap flexibility does not remove that bound. |
| L43 | EPAM Systems | unclear | unclear | yes | “Relocation opportunities within our offices in 55+ countries” | Country tag and relocation perk only. |
| L44 | GiveDirectly | unclear | unclear | yes | “one of the countries we're able to hire from” | Allowed-country list is external and absent from stored text; broader salary policy does not prove permission. |
| L45 | Atlassian | unclear | unclear | yes | “any country where we have a legal entity” | Legal-entity countries unspecified. |
| L46 | Placements24 | unclear | region_locked | no | “from anywhere in SA” | Full body says collaboration from anywhere in SA. |
| L47 | EPAM Systems | timezone_only | timezone_only | yes | “Flexibility to collaborate across US, LatAm, and CET time zones” | Collaboration hours only; remains Check, not proof of worldwide hiring. |
| L48 | Anadyr Horizon | timezone_only | work_authorization_required | no | “must have UK/EU work authorization” | Full body explicitly requires UK/EU work authorization. |
| L49 | Tailored Tech | timezone_only | timezone_only | yes | “UA/EU time zone” | Time zone wording alone does not require living there. |
| L50 | ElevenLabs | timezone_only | region_locked | no | “anywhere between the US East Coast and India” | Role location bounded by US East Coast and India; candidate country is outside the stated range. |
| L51 | Spd Technology | timezone_only | timezone_only | yes | “Work fully remote around the globe” | Worldwide remote work plus Kyiv working time is compatible. |
| L52 | Jobfinder AI (jobfinder-ai.com) | timezone_only | timezone_only | yes | “4+ hours of overlap with IST” | Overlap only, explicitly anywhere. |
| L53 | Verifran | timezone_only | timezone_only | yes | “We care about overlap, not postcode” | Overlap only; explicitly says postcode does not matter. |
| L54 | KeyBank | work_authorization_required | unclear | no | “NOT eligible for employment visa sponsorship” | No sponsorship alone does not require foreign work authorization. |
| L55 | KeyBank | work_authorization_required | unclear | no | “NOT eligible for employment visa sponsorship” | Same sponsorship-only evidence as L54, on another run. |
| L56 | Valence | work_authorization_required | work_authorization_required | yes | “without work authorization restrictions in the US” | Explicit absence of US work-authorization restrictions is required. |
| L57 | Stack AV | work_authorization_required | unclear | no | “may be contingent upon” | Conditional security/export review does not establish that this applicant is prohibited. |
| L58 | Valence | work_authorization_required | work_authorization_required | yes | “without work authorization restrictions in the US” | Explicit absence of US work-authorization restrictions is required. |
| L59 | Valence | work_authorization_required | work_authorization_required | yes | “without work authorization restrictions in the US” | Same explicit requirement as L56, on another run. |
| L60 | decircletalentpartner | work_authorization_required | work_authorization_required | yes | “US citizen / Green Card” | Explicit US citizen/green-card requirement. |
| R01 | Hivex | eligible | eligible | yes | “* *Remote job with the freedom to work from anywhere in the world.*” | Worldwide hiring supported. |
| R02 | Job  Tether Operations Limited | eligible | unclear | no | “Our team is a global talent powerhouse, working remotely from every corner of the world.” | Existing global team, not explicit hiring permission. |
| R03 | SmartRecruiters, Inc. | eligible | locked | no | “This role is fully remote within Europe, collaborating with a global team to develop high-performance software across” | Captured place includes unrelated global-team clause after Europe. |
| R04 | ElevenLabs | eligible | eligible | yes | “This role is remote and can be executed globally.” | Worldwide hiring supported. |
| R05 | Xsolla | eligible | eligible | yes | “*Location:* Remote (worldwide)” | Worldwide hiring supported. |
| R06 | Hivex | eligible | eligible | yes | “* Remote job with the freedom to work from anywhere in the world” | Worldwide hiring supported. |
| R07 | tether | eligible | unclear | no | “Our team is a global talent powerhouse, working remotely from every corner of the world.” | Same team-distribution wording as R02. |
| R08 | ElevenLabs | eligible | eligible | yes | “This role is remote and can be executed globally.” | Worldwide hiring supported. |
| R09 | WorkCapIT | eligible | eligible | yes | “*Location:* Global, Fully Remote” | Worldwide hiring supported. |
| R10 | geniusee | eligible | eligible | yes | “* Remote work from anywhere in the world or in one of our offices in Kyiv, Lviv,” | Worldwide hiring supported. |
| R11 | Tether Operations Limited | eligible | unclear | no | “Our team is a global talent powerhouse, working remotely from every corner of the world.” | Same team-distribution wording as R02. |
| R12 | ElevenLabs | eligible | eligible | yes | “This role is remote and can be executed globally.” | Worldwide hiring supported. |
| R13 | Your Software Supplier | eligible | eligible | yes | “*Location:* Global, fully remote” | Worldwide hiring supported. |
| R14 | Hivex | eligible | eligible | yes | “* ﻿Remote job with the freedom to work from anywhere in the world.” | Worldwide hiring supported. |
| R15 | ElevenLabs | eligible | eligible | yes | “This role is remote and can be executed globally.” | Worldwide hiring supported. |
| R16 | RemoteJobsOne | eligible | unclear | no | “Location: Remote (Globally)” | UAE candidate scope conflicts with worldwide label; full UAE name missing from geo. |
| R17 | Hivex | eligible | eligible | yes | “* *Remote job with the freedom to work from anywhere in the world.*” | Worldwide hiring supported. |
| R18 | Devconnectplatform | eligible | eligible | yes | “Instead, we are seeking enthusiastic software engineers who can showcase solving impressively hard problems with artifacts such” | Worldwide hiring supported. |
| R19 | HireEase Services | eligible | eligible | yes | “*Location:* Remote - Anywhere” | Worldwide hiring supported. |
| R20 | Hivex | eligible | eligible | yes | “* *Remote job with the freedom to work from anywhere in the world.*” | Worldwide hiring supported. |
| R21 | Cross Care | drop | locked | yes | “Applications are welcome from anywhere in the Philippines;” | Explicit country, residence or authorization constraint excludes the candidate. |
| R22 | FitNext Co. | drop | locked | yes | “* Based in LATAM.” | Explicit country, residence or authorization constraint excludes the candidate. |
| R23 | Liftoff | drop | locked | yes | “however, the role is eligible for full-time remote work in one of our entities: CA, CO, ID,” | Explicit country, residence or authorization constraint excludes the candidate. |
| R24 | MojoTech | drop | locked | yes | “*This job is remote, but requires you to be located in one of the following states: RI,” | Explicit country, residence or authorization constraint excludes the candidate. |
| R25 | alan | drop | locked | yes | “If it speaks to you: we're hiring across France, Spain, Belgium, and Canada.” | Explicit country, residence or authorization constraint excludes the candidate. |
| R26 | Pixoate | drop | locked | yes | “Applicants must be based in Ireland.” | Explicit country, residence or authorization constraint excludes the candidate. |
| R27 | Cast AI | drop | locked | yes | “We are currently accepting applications from candidates residing in the following European countries: Bulgaria, Croatia, Estonia, Greece,” | Explicit country, residence or authorization constraint excludes the candidate. |
| R28 | Sela | drop | locked | yes | “This fully remote, full-time position is open to candidates based in Argentina, available to work on Eastern” | Explicit country, residence or authorization constraint excludes the candidate. |
| R29 | Upstart | drop | unclear | no | “* Employee Stock Purchase Plan (ESPP) with discounted stock purchase options for eligible employees (US only)” | US-only stock purchase benefit, not job eligibility. |
| R30 | Cito | drop | locked | yes | “* *Work 100% remotely from Croatia*” | Explicit country, residence or authorization constraint excludes the candidate. |
| R31 | Premier Global Links | drop | unclear | no | “* Remote-first work environment across the United States and Canada.” | Remote-first team footprint and working-hours requirement, not residence. |
| R32 | vytelle | drop | locked | yes | “In compliance with federal law, all persons hired will be required to verify identity and eligibility to” | Explicit country, residence or authorization constraint excludes the candidate. |
| R33 | ООО ЛАБОРАТОРИЯ НАНОСЕМАНТИКА | drop | locked | yes | “* Оформление по ТК РФ с первого рабочего дня;” | Explicit country, residence or authorization constraint excludes the candidate. |
| R34 | Sii Ukraine | drop | locked | yes | “Tax residence in Ukraine required” | Explicit country, residence or authorization constraint excludes the candidate. |
| R35 | radai | drop | locked | yes | “This role is open to candidates located anywhere in the United States.” | Explicit country, residence or authorization constraint excludes the candidate. |
| R36 | eworgmbh | drop | locked | yes | “You are based in Europe or the Americas or open to relocate” | Explicit country, residence or authorization constraint excludes the candidate. |
| R37 | Liftoff | drop | locked | yes | “however, the role is eligible for full-time remote work in one of our entities: CA, CO, ID,” | Explicit country, residence or authorization constraint excludes the candidate. |
| R38 | c968050c-15a9-468b-acaf-b26b765aff76:19000101_000001 | drop | locked | yes | “We are hiring in the following states.” | Explicit country, residence or authorization constraint excludes the candidate. |
| R39 | alan | drop | locked | yes | “If it speaks to you: we're hiring across France, Spain, Belgium, and Canada.” | Explicit country, residence or authorization constraint excludes the candidate. |
| R40 | Go-Get Data Consultancy | drop | locked | yes | “- Must be based in the United States and authorized to work without visa sponsorship.” | Explicit country, residence or authorization constraint excludes the candidate. |

## Decision manifest

Use `(run_date, job_id, stage)` to retrieve each reviewed row.

| ID | Run date | Job ID |
| --- | --- | --- |
| L01 | 2026-10-16 | `whatjobs-uk:backend developer:560088630` |
| L02 | 2026-10-16 | `adzuna:it-jobs:5928991355` |
| L03 | 2026-10-16 | `whatjobs-uk:full stack developer:559145824` |
| L04 | 2026-10-16 | `greenhouse:alemnafora:4988507101` |
| L05 | 2026-10-16 | `energyjobline:www.energyjobline.com:31816302` |
| L06 | 2026-10-16 | `bamboohr:bforeai:141` |
| L07 | 2026-10-16 | `whatjobs-pl:full stack developer:29959307` |
| L08 | 2026-10-16 | `whatjobs-uk:full stack developer:562903865` |
| L09 | 2026-10-16 | `whatjobs-ca:data engineer:151281691` |
| L10 | 2026-10-16 | `himalayas::https://himalayas.app/companies/predikt-ai/jobs/software-engineer` |
| L11 | 2026-10-16 | `jobstreet:6287:94973439` |
| L12 | 2026-10-17 | `adzuna:it-jobs:5928991355` |
| L13 | 2026-10-16 | `greenhouse:sezzle:8031664003` |
| L14 | 2026-10-16 | `teamtailor:careers.investengine.com:8504649` |
| L15 | 2026-10-16 | `whatjobs-it:data engineer:237619828` |
| L16 | 2026-10-16 | `himalayas::https://himalayas.app/companies/wynploy/jobs/backend-python-ai` |
| L17 | 2026-10-17 | `torre::JWOqGRZd` |
| L18 | 2026-10-16 | `greenhouse:platformsh:8844069002` |
| L19 | 2026-10-17 | `successfactors:jobs.scotiabank.com:602664117` |
| L20 | 2026-10-16 | `greenhouse:platformsh:8845093002` |
| L21 | 2026-10-17 | `torre::GdjMG7Yw` |
| L22 | 2026-10-16 | `successfactors:jobs.scotiabank.com:602664117` |
| L23 | 2026-10-16 | `bamboohr:gocharting:67` |
| L24 | 2026-10-16 | `whatjobs-hk:python developer:4515099` |
| L25 | 2026-10-16 | `whatjobs-es:full stack developer:358782523` |
| L26 | 2026-10-17 | `whatjobs-ca:full stack developer:152998270` |
| L27 | 2026-10-16 | `remotedotcom::full-stack-developer-j15b6cda` |
| L28 | 2026-10-16 | `energyjobline:www.energyjobline.com:31803149` |
| L29 | 2026-10-17 | `greenhouse:greenhouse:8290931` |
| L30 | 2026-10-16 | `whatjobs-ca:backend developer:151442566` |
| L31 | 2026-10-16 | `whatjobs-it:python developer:239582044` |
| L32 | 2026-10-16 | `whatjobs-fr:java developer:157772759` |
| L33 | 2026-10-16 | `workable:lockwood:BBF1D212FC` |
| L34 | 2026-10-16 | `teamtailor:physitrack.teamtailor.com:8464832` |
| L35 | 2026-10-16 | `epam:careers.epam.com:blt90479mcqt2w2mhno` |
| L36 | 2026-10-16 | `whatjobs-pt:data engineer:16797659` |
| L37 | 2026-10-16 | `djinni::853248` |
| L38 | 2026-10-16 | `workday:ssctech.wd1.myworkdayjobs.com/ssctechnologies:/job/Remote---Karnataka/Senior-Software-Engineer_R45449` |
| L39 | 2026-10-16 | `greenhouse:cloudflare:8243201` |
| L40 | 2026-10-16 | `hackernews::49961408` |
| L41 | 2026-10-16 | `whatjobs-uk:full stack developer:567158094` |
| L42 | 2026-10-16 | `ashby:elevenlabs:34dbe44c-9815-40b2-81f4-91c607fd84b2` |
| L43 | 2026-10-16 | `epam:careers.epam.com:blt1bfp0skoxv838lrq` |
| L44 | 2026-10-16 | `greenhouse:givedirectly:4738257005` |
| L45 | 2026-10-16 | `icims:atlassian:27567` |
| L46 | 2026-10-16 | `whatjobs-za:full stack developer:58616112` |
| L47 | 2026-10-16 | `epam:careers.epam.com:blt35b9o9n3xvu40e17` |
| L48 | 2026-10-16 | `whatjobs-nl:python developer:171484786` |
| L49 | 2026-10-16 | `djinni::851399` |
| L50 | 2026-10-16 | `ashby:elevenlabs:3abf96ca-2937-4290-9d23-5208aece08b2` |
| L51 | 2026-10-16 | `workable:spd-technology:27B08D69AE` |
| L52 | 2026-10-16 | `whatjobs:backend engineer:3165206083` |
| L53 | 2026-10-16 | `whatjobs-ca:backend developer:150983935` |
| L54 | 2026-10-16 | `workday:keybank.wd5.myworkdayjobs.com/External_Career_Site:/job/Remote-United-States/Sr-Full-Stack-Engineer_R-42449` |
| L55 | 2026-10-17 | `workday:keybank.wd5.myworkdayjobs.com/External_Career_Site:/job/Remote-United-States/Sr-Full-Stack-Engineer_R-42449` |
| L56 | 2026-10-17 | `startupjobs::10435938` |
| L57 | 2026-10-16 | `greenhouse:stackav:5254676007` |
| L58 | 2026-10-16 | `startupjobs::10435936` |
| L59 | 2026-10-16 | `startupjobs::10435938` |
| L60 | 2026-10-16 | `recruitee:decircletalentpartner:2780721` |
| R01 | 2026-10-16 | `himalayas::https://himalayas.app/companies/hivex/jobs/full-stack-python-react-developer-880151218` |
| R02 | 2026-10-16 | `startupjobs::10467336` |
| R03 | 2026-10-16 | `whatjobs-pl:full stack developer:30005862` |
| R04 | 2026-10-16 | `ashby:elevenlabs:77f23be6-16c0-40c5-87b6-29dc2ee752ab` |
| R05 | 2026-10-16 | `lever:xsolla:0f788b88-4a30-4ed3-bdfb-29b39d96b60b` |
| R06 | 2026-10-16 | `himalayas::https://himalayas.app/companies/hivex/jobs/full-stack-developer-angular-django-1050171861` |
| R07 | 2026-10-16 | `recruitee:tether:2785216` |
| R08 | 2026-10-16 | `ashby:elevenlabs:108c99e6-ba2b-44a0-b830-7d535e14a152` |
| R09 | 2026-10-16 | `whatjobs-cl:full stack developer:48456402` |
| R10 | 2026-10-16 | `peopleforce:geniusee:230978` |
| R11 | 2026-10-17 | `himalayas::https://himalayas.app/companies/tether-operations-limited/jobs/fullstack-engineer-agenticops-100-remote` |
| R12 | 2026-10-16 | `ashby:elevenlabs:35813150-a851-4821-b732-a037b4e6c4fe` |
| R13 | 2026-10-16 | `join:163368:16796020` |
| R14 | 2026-10-16 | `himalayas::https://himalayas.app/companies/hivex/jobs/senior-ai-python-developer-584900448` |
| R15 | 2026-10-16 | `ashby:elevenlabs:0b3a97d4-193c-4b47-9888-7ef5803ed945` |
| R16 | 2026-10-16 | `whatjobs-ae:python developer:93760713` |
| R17 | 2026-10-16 | `himalayas::https://himalayas.app/companies/hivex/jobs/senior-python-developer-2556587688` |
| R18 | 2026-10-16 | `whatjobs-co:full stack developer:46356722` |
| R19 | 2026-10-17 | `torre::8W3Pn3Rw` |
| R20 | 2026-10-16 | `himalayas::https://himalayas.app/companies/hivex/jobs/python-developer-1783009587` |
| R21 | 2026-10-17 | `jobstreet:6287:95190881` |
| R22 | 2026-10-16 | `loxo:fitnext.app.loxo.co/fitnext:42474-k07vb23ki63hqrii` |
| R23 | 2026-10-17 | `greenhouse:liftoff:8279099` |
| R24 | 2026-10-16 | `jazzhr:mojotech:uAnE78iR01` |
| R25 | 2026-10-16 | `ashby:alan:e5daaf2e-a7fc-4823-bf6f-de056e137c44` |
| R26 | 2026-10-16 | `whatjobs-ie:python developer:6085255` |
| R27 | 2026-10-16 | `greenhouse:castaigroupinc:4423859009` |
| R28 | 2026-10-16 | `whatjobs-ar:full stack developer:34307090` |
| R29 | 2026-10-16 | `greenhouse:upstart:8274702` |
| R30 | 2026-10-16 | `personio:cito-transport-technologies:2830182` |
| R31 | 2026-10-16 | `manatal:premier-global-links:7X7Y9935` |
| R32 | 2026-10-16 | `bamboohr:vytelle:272` |
| R33 | 2026-10-17 | `potok:tehnopark-skolkovo:454105` |
| R34 | 2026-10-17 | `djinni::852708` |
| R35 | 2026-10-16 | `ashby:radai:1379cf08-88cb-45b2-b940-e54a5523e7e7` |
| R36 | 2026-10-16 | `recruitee:eworgmbh:2777535` |
| R37 | 2026-10-16 | `greenhouse:liftoff:8279099` |
| R38 | 2026-10-17 | `adp:c968050c-15a9-468b-acaf-b26b765aff76:19000101_000001:9205243098577_1` |
| R39 | 2026-10-16 | `ashby:alan:583a9a1c-433e-4b76-a1b7-4d9e6a30f1da` |
| R40 | 2026-10-17 | `torre::8wDa9z9d` |

## Fresh validation

Validated on 2026-10-17 against the fixed auditor labels in L01–L60, with fresh model output rather than replaying historical cached verdicts. Loaded the latest `jobs.payload` through a SQLite `mode=ro` connection and reconstructed `Job` objects. Used the current private configuration and prompt read-only, `LocationRules(cfg['rules']['location']).classify(job).snippets` for the production sentence extraction, and `Screener.screen` followed by `check_evidence`. Forced all sampled jobs through the screener even if today's rules would decide them. No full descriptions or extra context were added to model inputs.

The 60 decision rows represent 56 distinct jobs. Screened each distinct job once per run, in manifest first-occurrence order, in production-sized batches of 20/20/16 with three workers. Reused that fresh result for repeated job/run rows when scoring all 60 decisions. The two runs' extracted inputs were byte-identical. Used separate initially empty caches at `/tmp/job-hunt-validation-19/{initial,final}/cache.sqlite`; no historical verdict was reused. The local harness, input exports, raw results and request counts remain under `/tmp/job-hunt-validation-19/`, outside the repository. Credentials were loaded in place using the scanner's environment/key-file loading, with no `opencode run` calls or private writes.

Both runs used the unmodified configured production chain: `orca:deepseek/deepseek-v4-flash-free`, then `orca:z-ai/glm-5.3-flash-free`, then `gemini:gemini-flash-latest`. Both free models returned HTTP 429 for every attempt; all 56 successful job results per run came from `gemini-flash-latest`. Later fallbacks (`gemini-3.5-flash`, Claude Sonnet) were not reached. Runtime prompt hash: `b499203ccfd0`. Each run made nine screener calls (six failed free-model calls, three successful Gemini batches), comprising 33 HTTP requests (30 rate-limited attempts, three successful requests). **Total: 18 screener calls, 66 HTTP requests, six successful LLM responses.**

Precision below means correct labels divided by predictions of that outcome in that run; denominators change as predictions change. Overall is exact-label accuracy across the same 60 decision rows. There were no `includes_candidate` predictions in any run.

| Predicted outcome | Historical before safeguards | Fresh safeguards, before header fix | Fresh rerun, final guard |
| --- | ---: | ---: | ---: |
| anywhere | 6/14 (42.9%) | 5/6 (83.3%) | 5/7 (71.4%) |
| region_locked | 12/20 (60.0%) | 11/15 (73.3%) | 11/11 (100%) |
| work_authorization_required | 4/7 (57.1%) | 4/4 (100%) | 4/4 (100%) |
| unclear | 7/12 (58.3%) | 21/30 (70.0%) | 25/35 (71.4%) |
| timezone_only | 5/7 (71.4%) | 5/5 (100%) | 3/3 (100%) |
| includes_candidate | — (0) | — (0) | — (0) |
| **Overall** | **34/60 (56.7%)** | **46/60 (76.7%)** | **48/60 (80.0%)** |

### Small general fix and one fresh rerun

The first fresh run still locked L16/L17/L19/L22 using only `Location:` / `Location(s):` headers copied from the body. Extended `check_evidence` to treat those headers as labels even when they occur verbatim in the description. Headers containing an explicit hiring/role statement remain usable, preserving L24's actual EMEA role requirement. Added anonymized regression cases for plain, Markdown and plural headers and for an explicit role statement. No prompt, configuration, sentence extraction or dependency changes were made.

Applying the final guard to the first run's saved raw outputs changes exactly those four erroneous locks to `unclear`: 50/60 (83.3%). Then performed one complete fresh model rerun with a separate empty cache, and applied the final guard to its collected raw outputs. Its result is 48/60 (80.0%). The four header errors remain fixed, but L52/L53 newly misclassify timezone-only permission. Thus the observed fresh gain is 14 correct rows versus history (+23.3 percentage points); the isolated guard replay gains four rows, while variation between fresh model responses loses two. Temperature zero did not produce identical responses. This remains validation on the existing stratified audit set, not an independent holdout or an estimate of population precision.

### Every remaining error in the fresh rerun

| ID | Final label | Auditor label | One-line reason |
| --- | --- | --- | --- |
| L10 | unclear | anywhere | Extraction supplies only “Work anywhere”; the full-body removal of country/region limits is absent. |
| L25 | unclear | region_locked | Extraction retains the Spain label but omits the country-of-employment remote policy supporting the restriction. |
| L28 | unclear | region_locked | UAE candidate scope conflicts with a US/UK/CAN label; the model abstains although both scopes exclude Bangladesh. |
| L30 | unclear | region_locked | Extraction retains only a Canada label and omits the explicit Alberta-only residence requirement. |
| L39 | unclear | region_locked | The multiline Available Locations list is absent; the supplied snippet concerns the company's charitable program. |
| L40 | unclear | work_authorization_required | The explicit US work-authorization sentence is absent from extracted input. |
| L41 | unclear | region_locked | Extraction supplies relocation assistance but omits the rejection of fully remote candidates. |
| L44 | anywhere | unclear | The model treats salary benchmarking outside three countries as worldwide permission; the external allowed-country scope is absent. |
| L46 | unclear | region_locked | Extraction supplies team distribution but omits the full-body “anywhere in SA” role restriction. |
| L48 | unclear | work_authorization_required | Extraction supplies EU hours and a location label but omits mandatory UK/EU work authorization. |
| L52 | unclear | timezone_only | The model chooses anywhere for explicit IST overlap; the bare-anywhere guard then downgrades its quote to unclear. |
| L53 | anywhere | timezone_only | The model chooses anywhere despite explicit Eastern Time overlap; the auditor's exact label is timezone_only. |

The first fresh run's complete error list was L10/L16/L17/L19/L22/L25/L28/L30/L39/L40/L41/L44/L46/L48. Reasons for the ten errors shared with the rerun are listed above; L16/L17/L19/L22 were unsupported locks from bare body location headers. Neither run had missing job results. Eight final errors involve decisive text absent from the production snippets; further prompt tuning cannot reconstruct that text. L52 remains Check after the guard; L53 incorrectly asserts positive permission instead of retaining the timezone-only label.

Validation: `uv run pytest -q` — **479 passed**. Backlog row 19 marked `done (branch)`. No private files changed; no push.
