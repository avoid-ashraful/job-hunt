"""Job identity: URL normalization, title fingerprints, seniority ranking and per-company collapse."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    text = re.sub(r"[^a-z0-9]+", " ", job.description.lower()).split()
    if len(text) < 60:  # too short to be distinctive
        return None
    return f"{normalize_title(job.title)}|{' '.join(text[:80])}"


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
