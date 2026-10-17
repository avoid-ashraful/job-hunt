"""Keep local-only postings out and show comparable pay hints."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scanner.models import Job

DEFAULT_ALLOW = ["us", "ca", "gb", "de", "fr", "nl", "ie", "es", "pt", "it", "pl", "se", "no", "dk", "fi", "at", "be", "lu", "cz", "ee", "lv", "lt", "si", "sk", "hr", "gr", "mt", "cy", "ro", "bg", "hu", "is", "li", "ch", "sg", "my", "au", "nz", "jp", "il", "ae"]
LOCAL = {"in", "bd"}
LOCAL_PLACE = re.compile(r"^(?:(?:remote|work from home)\s*[-–,:()]?\s*)?(?:(?:india|bangladesh|bengaluru|bangalore|pune|hyderabad|noida|gurugram|gurgaon|chennai|mumbai|kolkata|ahmedabad|delhi|dhaka|chattogram|chittagong)\s*[,/&+\-– ]*\s*)+$", re.I)


def local_only(job: Job) -> bool:
    """Source countries are hiring locations; a mixed list is not local-only."""
    countries = {str(x).lower() for x in job.countries}
    if countries:
        return countries <= LOCAL and "global" not in job.regions
    if job.cities:
        return all(LOCAL_PLACE.fullmatch(str(city).strip()) for city in job.cities) and "global" not in job.regions
    location = job.location.strip()
    return bool(LOCAL_PLACE.fullmatch(location))


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
