"""Job identity: URL normalization, title fingerprints, seniority ranking and per-company collapse."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scanner.models import Job

TRACKING_PARAMS = re.compile(r"^(utm_|gh_src$|source$|ref$|lever-source|src$)", re.I)

# Words that vary between copies of the same posting but don't change the job.
TITLE_NOISE = re.compile(
    r"\b(remote|hybrid|anywhere|worldwide|global|usa?|uk|eu|emea|apac|latam|americas|europe|united states|"
    r"united kingdom|canada|india|germany|poland|spain|portugal|brazil|mexico|argentina|ukraine|philippines|"
    r"m/?f/?[dx]|f/?m/?[dx]|w/?m/?d|all genders?|full[\s-]?time|contract)\b",
    re.I,
)

SENIORITY_FROM_TITLE = [
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("staff", re.compile(r"\bstaff\b", re.I)),
    ("lead", re.compile(r"\b(lead|tech lead|team lead)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?)\b", re.I)),
    ("middle", re.compile(r"\b(mid|middle|intermediate|ii|2)\b", re.I)),
]


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalize_company(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(inc|llc|ltd|gmbh|corp|corporation|co|limited|plc|bv|ag|sa)\b\.?", " ", name)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\bsr\.?\b", "senior", t)
    t = re.sub(r"\bjr\.?\b", "junior", t)
    t = re.sub(r"\bback[\s-]end\b", "backend", t)
    t = re.sub(r"\bfull[\s-]stack\b", "fullstack", t)
    t = TITLE_NOISE.sub(" ", t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return " ".join(t.split())


def fingerprint(company_key: str, title: str) -> str:
    return f"{company_key}|{normalize_title(title)}"


def description_fingerprint(job) -> str | None:
    """Title + the start of the description: the same job posted under two company names
    (an ATS tenant name vs the brand, a parent vs a subsidiary) has the same text."""
    description = job.description
    try:
        description = description.encode("latin1").decode("utf-8")
    except UnicodeError:
        pass
    text = re.sub(r"[^a-z0-9]+", " ", description.lower()).split()
    if len(text) < 60:  # too short to be distinctive
        return None
    return f"{normalize_title(job.title)}|{' '.join(text[:80])}"


def description_variant_fingerprint(job: Job) -> str | None:
    """A client/brand suffix repeated in the opening line needs matching text too."""
    match = re.search(r"\s+\(([^()]+)\)$", job.title)
    if not match or not re.fullmatch(r"[A-Z][a-zA-Z]+", match[1]):
        return None
    if seniority_level(match[1], None) != "unknown" or match[1].lower() == "junior":
        return None
    opening = job.description.split("\n", 1)[0]
    if not re.search(rf"\b{re.escape(match[1])}\b", opening):
        return None
    if desc := description_fingerprint(job):
        return f"{normalize_title(job.title[:match.start()])}|{desc.split('|', 1)[1]}"
    return None


def merge_jobs(jobs: Iterable[Job], fill_missing: Callable[[Job, Job], None]) -> dict[str, Job]:
    """Merge all posting identities before filtering; employer copies supply the text/link."""
    boards = {"remoteok", "wwr", "workingnomads", "jobicy"}
    ordered = sorted(jobs, key=lambda j: 0 if j.source == "ats" else 2 if j.source in boards else 1)
    merged: dict[str, Job] = {}
    identities: dict[tuple[str, str], str] = {}
    positions: dict[str, int] = {}
    for job in ordered:
        company = normalize_company(job.company) or job.company_key or job.company.casefold()
        keys = {("id", job.id), ("url", normalize_url(job.url)),
                ("title", fingerprint(company, job.title))}
        if desc := description_fingerprint(job):
            keys.add(("description", desc))
            keys.add(("variant", f"{company}|{desc}"))
        if variant := description_variant_fingerprint(job):
            keys.add(("variant", f"{company}|{variant}"))
        matches = {identities[k] for k in keys if k in identities}
        exact = {identities[k] for k in keys if k[0] in {"id", "url"} and k in identities}
        # Employer requisitions can share a title but have different hiring locations.
        if job.source == "ats":
            matches = exact
        elif exact:
            matches = exact | {jid for jid in matches if merged[jid].source != "ats"}
        if matches:
            winner = min(matches, key=positions.__getitem__)
            for jid in sorted(matches, key=positions.__getitem__):
                if jid != winner:
                    fill_missing(merged[winner], merged.pop(jid))
            fill_missing(merged[winner], job)
            if len(matches) > 1:
                for key, jid in identities.items():
                    if jid in matches:
                        identities[key] = winner
        else:
            winner = job.id
            merged[winner] = job
            positions[winner] = len(positions)
        for key in keys:
            identities[key] = winner
    return merged


def seniority_level(title: str, enriched: str | None) -> str:
    for level, pattern in SENIORITY_FROM_TITLE:
        if pattern.search(title):
            return level
    if enriched in {"principal", "staff", "lead", "senior", "middle"}:
        return enriched
    return "unknown"


def collapse_by_seniority(jobs, reported_best: dict[tuple[str, str], tuple[int, str]]):
    """Keep only the most senior postings per (company, role family).

    `reported_best` maps (company_key, family) to the best (rank, title) already
    reported recently, so a mid-level opening doesn't surface a day after the
    senior one at the same company. Returns (kept, dropped) where dropped is a
    list of (job, reason).
    """
    best: dict[tuple[str, str], tuple[int, str]] = dict(reported_best)
    for job in jobs:
        key = (job.company_key, job.family)
        if key not in best or job.rank > best[key][0]:
            best[key] = (job.rank, job.title)

    kept, dropped = [], []
    for job in jobs:
        top_rank, top_title = best[(job.company_key, job.family)]
        if job.rank < top_rank:
            dropped.append((job, f"lower seniority than '{top_title}' at same company"))
        else:
            kept.append(job)
    return kept, dropped
