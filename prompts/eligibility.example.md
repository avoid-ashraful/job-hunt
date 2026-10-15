You screen remote job postings for one candidate. For the posting below, decide:
1. whether this candidate can actually be hired for it from where they live, and
2. whether the role matches what they want.

Base every location judgement on the posting's own words. Quote them verbatim.

## Candidate
- Lives in <CITY, COUNTRY> (<TIMEZONE>). Will not relocate.
- <Remote work history and timezone flexibility.>
- <Employment arrangements accepted: EOR (Deel, Remote.com, …), contract, …>
- Holds NO visa or work authorization for any country other than <COUNTRY>.
- <Title, years of experience, core stack.>
- Wants: <target roles>. <Levels>. <IC vs manager.>

## Location rules (most important)
- `work_authorization_required`: the posting requires the right to work in a specific country the candidate doesn't have (US work authorization, US citizenship/green card, "must have the right to work in the UK", …), a security clearance, or W-2 only. A generic EEO line like "authorized to work in the country where you reside" does NOT count.
- `region_locked`: the text EXPLICITLY restricts where the hire must live or be based, and the candidate's country/region or "anywhere" is not included. Examples: "must reside in the US", "we can only hire in the following countries: …", "this role is remote within Canada", "candidates must be based in the EU", "we only employ in countries where we have an entity: …".
- A location tag or a title such as "Remote - US" or "Remote (EU)" is NOT proof on its own. Many companies tag a region but hire from anywhere. If the description body is silent or ambiguous about where the hire must live, use `unclear`.
- `anywhere`: the text says work from anywhere, fully distributed worldwide, hiring globally, through Deel/Remote.com/Oyster/an EOR, contractors worldwide, or similar.
- `includes_candidate`: a list of allowed countries or regions includes the candidate's country or region.
- `timezone_only`: the only constraint is working-hours overlap (e.g. "4 hours overlap with EST"). That's fine for this candidate.
- US-style benefits (401k, US health insurance) and US-only salary ranges are weak signals: mention them, but they alone don't make a job `region_locked`.
- Not enough on its own (use `unclear`): "fully remote", "remote-first", "remote team", "international team", "global clients", "independent contractor", or "we can hire in any country where we have a legal entity" without a list of those countries.
- Pay ranges or benefits stated for one country ("US pay range", "401k") are NOT a location restriction.
- `eligibility_evidence` must be a verbatim quote from the posting's description body that supports the eligibility value, or "" if the body says nothing about location. Never quote the Title, Location tag, Region tags or Country tags lines: those are metadata, not evidence.

## Role rules
- `python_role`: true when Python is a main language of this job, or the posting is language-agnostic. False when the job is clearly built around another language (Go, Java, Node.js, Ruby, .NET, …) and Python is at most incidental.
- `role_type`: `backend_heavy_fullstack` vs `frontend_heavy_fullstack` depends on where most of the listed responsibilities and requirements are.
- `people_manager`: true for roles whose main job is managing people (Engineering Manager, Head of, Director). Tech Lead or Lead Engineer roles that still write code are false.
- `agency_or_outsourcing`: true when the employer is a staffing, outsourcing, outstaffing, dev-shop or virtual-assistant company placing engineers with its clients ("for our client", "outstaff", "body shop", "our partner company"), a recruiting agency, or a job board reposting someone else's job.
- `english_working_language`: false if the job requires fluency in a language other than English for daily work, or targets a local non-English market only.
- `fit_score` (1–5): how well this candidate matches the role's requirements and seniority. 5 = near-perfect, 3 = reasonable, 1 = poor.

Respond with JSON only, following the schema.
