"""Keep local-only postings out and show comparable pay hints."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scanner.models import Job

DEFAULT_ALLOW = ["us", "ca", "gb", "de", "fr", "nl", "ie", "es", "pt", "it", "pl", "se", "no", "dk", "fi", "at", "be", "lu", "cz", "ee", "lv", "lt", "si", "sk", "hr", "gr", "mt", "cy", "ro", "bg", "hu", "is", "li", "ch", "sg", "my", "au", "nz", "jp", "il", "ae"]
LOCAL = {"in", "bd"}
_PLACES = {
    "in": "india|bengaluru|bangalore|pune|hyderabad|noida|gurugram|gurgaon|chennai|mumbai|kolkata|ahmedabad|delhi",
    "bd": "bangladesh|dhaka|chattogram|chittagong",
}
_PREFIX = r"^(?:(?:remote|work from home)\s*[-–,:()]?\s*)?"
LOCAL_PLACE = {code: re.compile(_PREFIX + rf"(?:(?:{names})\s*[,/&+\-– ]*\s*)+$", re.I) for code, names in _PLACES.items()}
ANY_LOCAL = re.compile(_PREFIX + rf"(?:(?:{'|'.join(_PLACES.values())})\s*[,/&+\-– ]*\s*)+$", re.I)


def local_only(job: Job) -> set[str]:
    """The local countries (in/bd) a posting is limited to; empty when it isn't local-only.

    Source countries are hiring locations, so a mixed list ("in, us, gb") is not local-only."""
    if "global" in job.regions:
        return set()
    countries = {str(x).lower() for x in job.countries}
    if countries:
        return countries if countries <= LOCAL else set()
    places = [str(c).strip() for c in job.cities] or [job.location.strip()]
    if not all(ANY_LOCAL.fullmatch(p) for p in places if p) or not any(places):
        return set()
    return {code for code, rx in LOCAL_PLACE.items() if any(rx.search(p) for p in places)}


def salary_usd(job: Job, cfg: dict) -> str:
    """Convert a stated salary range using the same rates as the hard filter."""
    rate = cfg.get("usd_rates", {}).get((job.salary_currency or "").upper())
    mult = cfg.get("period_multipliers", {}).get(job.salary_period or "year")
    if rate is None or mult is None or not (job.salary_min or job.salary_max):
        return ""
    lo = round(job.salary_min * rate * mult / 1000) if job.salary_min else None
    hi = round(job.salary_max * rate * mult / 1000) if job.salary_max else None
    if lo and hi:
        return f"${lo}–{hi}k/yr"
    return f"${lo or hi}k/yr"


def levels_tiers(directory: Path) -> dict[str, str]:
    """Read optional normalized-company JSON cache; ignore records without a tier."""
    from scanner.dedup import normalize_company

    tiers = {}
    if not directory.is_dir():
        return tiers
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        records = data if isinstance(data, list) else data.get("companies", data)
        if isinstance(records, dict):
            records = [dict(value, company=key) for key, value in records.items() if isinstance(value, dict)]
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            key = record.get("company_key") or record.get("company") or record.get("name")
            tier = record.get("pay_tier") or record.get("tier")
            if key and tier:
                tiers[normalize_company(str(key))] = str(tier)
    return tiers
