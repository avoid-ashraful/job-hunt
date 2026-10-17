"""Infer employer headquarters only from company evidence, never hiring geography."""

from __future__ import annotations

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from scanner.models import Job

log = logging.getLogger(__name__)

COUNTRIES = {
    "india": "in", "bangladesh": "bd", "united states": "us", "usa": "us", "u.s.": "us",
    "canada": "ca", "united kingdom": "gb", "uk": "gb", "germany": "de",
    "france": "fr", "netherlands": "nl", "ireland": "ie", "switzerland": "ch",
    "singapore": "sg", "malaysia": "my", "australia": "au", "new zealand": "nz",
    "japan": "jp", "israel": "il", "uae": "ae", "united arab emirates": "ae",
    "spain": "es", "portugal": "pt", "italy": "it", "poland": "pl", "sweden": "se",
    "norway": "no", "denmark": "dk", "finland": "fi", "austria": "at", "belgium": "be",
    "brazil": "br", "vietnam": "vn", "pakistan": "pk", "philippines": "ph",
    "bengaluru": "in", "bangalore": "in", "pune": "in", "hyderabad": "in",
    "noida": "in", "gurugram": "in", "gurgaon": "in", "chennai": "in",
    "mumbai": "in", "kolkata": "in", "ahmedabad": "in", "delhi": "in",
    "dhaka": "bd", "chattogram": "bd", "chittagong": "bd",
    "new york": "us", "san francisco": "us", "london": "gb", "berlin": "de",
    "jacksonville": "us", "boston": "us", "nashville": "us", "amsterdam": "nl",
    "hamburg": "de", "toronto": "ca", "sydney": "au", "zurich": "ch", "dubai": "ae",
}
PLACE = re.compile(r"\b(" + "|".join(re.escape(x) for x in sorted(COUNTRIES, key=len, reverse=True)) + r")\b", re.I)
DEFAULT_PATTERNS = {
    "headquarters": r"\b(?:headquartered|headquarters|head office|global hq|company is based)\s*(?:is|are|in|at|located in|:|,)\s*(?:the\s+)?(?P<place>[^.;\n]{2,90})",
    "sole_office": r"\b(?:our|its|company's)\s+(?:only|sole|single|main)\s+(?:office|base)\s+(?:is|in|at|located in)\s+(?P<place>[^.;\n]{2,90})",
    "private_limited": r"\b(?:pvt\.?\s*ltd\.?|private limited)\b",
    "india_domain": r"\b[a-z0-9-]+\.in\b",
    "bangladesh_domain": r"\b[a-z0-9-]+\.(?:com\.)?bd\b",
}


def _candidate(job: Job, cfg: dict | None = None) -> tuple[str | None, str, str, int]:
    override = (cfg or {}).get("hq_overrides", {}).get(job.company_key)
    if override:
        return str(override).lower(), f"configured HQ: {str(override).upper()}", "config", 6
    patterns = {**DEFAULT_PATTERNS, **(cfg or {}).get("evidence_patterns", {})}
    text = job.description or ""
    metadata = getattr(job, "ats_metadata", None) or {}
    if isinstance(metadata, dict):
        code = str(metadata.get("hq_country") or metadata.get("headquarters_country") or "").lower()
        if code in set(COUNTRIES.values()):
            return code, f"ATS company metadata: {code.upper()}", "ats_metadata", 5
    # An explicit statement about the employer takes precedence over office and pay clues.
    for name in ("headquarters", "sole_office"):
        for match in re.finditer(patterns[name], text, re.I):
            first_place = re.split(r"\b(?:and|with|but|while|which|offices?)\b", match.group("place"), maxsplit=1, flags=re.I)[0]
            place = PLACE.search(first_place)
            if place:
                strength = 5 if name == "headquarters" else 3
                return COUNTRIES[place.group().lower()], match.group(0)[:130], name, strength
    name = job.company or ""
    if re.search(patterns["private_limited"], name, re.I):
        countries = set(job.countries)
        if countries == {"bd"}:
            return "bd", f"company name and country: {name}", "private_limited", 3
        if countries == {"in"}:
            return "in", f"company name and country: {name}", "private_limited", 3
    # A domain is evidence only when attached to the employer name or its website, not any URL in a job ad.
    intro = text[:1200]
    for key, code in (("bangladesh_domain", "bd"), ("india_domain", "in")):
        if match := re.search(patterns[key], intro, re.I):
            nearby = intro[max(0, match.start()-100):match.start()].lower()
            if "website" in nearby or "visit us" in nearby or job.company_key.replace("-", "") in match.group().replace("-", ""):
                return code, f"company domain: {match.group()}", key, 2
    currency = (job.salary_currency or "").upper()
    if currency in {"INR", "BDT"}:
        return None, f"weak pay clue: {currency}", "salary_currency", 1
    if re.search(r"\b(?:lpa|lakhs?)\b", text[:3000], re.I):
        return None, "weak pay clue: LPA/lakh", "salary_wording", 1
    return None, "no company HQ evidence", "unknown", 0


def hq_country(job: Job, cfg: dict | None = None) -> tuple[str | None, str]:
    """Return ISO2 HQ and its evidence; weak job-pay clues leave HQ unknown."""
    country, evidence, _, _ = _candidate(job, cfg)
    return country, evidence


class CompanyProfiles:
    """Keep the strongest observed company evidence across postings."""

    def __init__(self, db: sqlite3.Connection, cfg: dict | None = None, vendor_dir: Path | None = None):
        self.db, self.cfg = db, cfg or {}
        db.execute("""CREATE TABLE IF NOT EXISTS company_profiles (
            company_key TEXT PRIMARY KEY, hq_country TEXT, evidence TEXT NOT NULL,
            source TEXT NOT NULL, updated TEXT NOT NULL, strength INTEGER NOT NULL DEFAULT 0)""")
        self.vendor = _vendor_profiles(vendor_dir) if vendor_dir else {}
        self.attempted: set[str] = set()

    def enrich_report(self, jobs: list[Job]) -> None:
        """Look up at most 100 still-unknown report companies, once per run."""
        from scanner.company_evidence import lookup

        unknown = {}
        for job in jobs:
            if not self.resolve(job)[0] and job.company_key not in self.attempted:
                unknown.setdefault(job.company_key, job)
        pending = [job for job in unknown.values() if not self.resolve(job)[0]][:max(0, 100 - len(self.attempted))]
        self.attempted.update(job.company_key for job in pending)

        def lookup_safely(job):
            try:
                return lookup(job)
            except Exception as exc:
                log.warning("HQ lookup for %s failed: %s: %s", job.company_key, type(exc).__name__, exc)
                return None, "company lookup failed"

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lookup_safely, pending))
        for job, (country, evidence) in zip(pending, results):
            if country:
                self.db.execute("""UPDATE company_profiles SET hq_country=?, evidence=?, source=?,
                    updated=?, strength=? WHERE company_key=? AND hq_country IS NULL""",
                    (country, evidence, "public_company", datetime.now(timezone.utc).isoformat(), 3,
                     job.company_key))
        for job in jobs:
            self.resolve(job)

    def resolve(self, job: Job) -> tuple[str | None, str]:
        key = job.company_key
        current = self.db.execute(
            "SELECT hq_country, evidence, source, strength FROM company_profiles WHERE company_key = ?", (key,)
        ).fetchone()
        found = _candidate(job, self.cfg)
        if key in self.vendor:
            vendor = self.vendor[key]
            if vendor[3] > found[3]:
                found = vendor
        if current and current[3] >= found[3]:
            country, evidence = current[:2]
        else:
            country, evidence, source, strength = found
            self.db.execute("""INSERT INTO company_profiles VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_key) DO UPDATE SET hq_country=excluded.hq_country,
                evidence=excluded.evidence, source=excluded.source, updated=excluded.updated,
                strength=excluded.strength""",
                (key, country, evidence, source, datetime.now(timezone.utc).isoformat(), strength))
        job.screen["hq_country"] = country
        job.screen["hq_evidence"] = evidence
        return country, evidence


def _vendor_profiles(directory: Path) -> dict[str, tuple[str, str, str, int]]:
    """remoteintech's region is hiring reach; use only explicit HQ frontmatter."""
    from scanner.dedup import normalize_company

    found = {}
    if not directory.is_dir():
        return found
    for path in directory.glob("*.md"):
        contents = path.read_text(errors="ignore")
        head = contents.split("\n---", 1)[0]
        name = re.search(r"^(?:title|name):\s*['\"]?([^'\"\n]+)", head, re.M)
        hq = re.search(r"^(?:headquarters|hq_country|country):\s*['\"]?([^'\"\n]+)", contents, re.M | re.I)
        if name and hq and (place := PLACE.search(hq.group(1))):
            code = COUNTRIES[place.group().lower()]
            found[normalize_company(name.group(1))] = (code, f"remoteintech HQ: {hq.group(1)}", "remoteintech", 4)
    return found
