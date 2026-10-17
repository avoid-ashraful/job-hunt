"""Conservative salary extraction; comparison conversions stay in the pay/filter code."""

from __future__ import annotations

import html
import re

from scanner.models import Job

_CURRENCY = r"(?:USD|EUR|GBP|CAD|AUD|SGD|US\$|CA\$|C\$|AU\$|A\$|S\$|[$€£])"
_NUMBER = r"(?:\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)"
_AMOUNT = rf"(?P<currency1>{_CURRENCY})?\s*(?P<number1>{_NUMBER})\s*(?P<k1>[kK])?"
_RANGE = rf"(?:\s*(?:-|–|—|\bto\b)\s*(?P<currency2>{_CURRENCY})?\s*(?P<number2>{_NUMBER})\s*(?P<k2>[kK])?)?"
_PERIOD = r"(?:(?:per\s+|/\s*|a\s+)(?P<period>year|annum|yr|month|mo|week|wk|day|hour|hr)|(?P<adverb>annually|yearly|monthly|weekly|daily|hourly))"
_PAY = re.compile(rf"(?<![\w.,])(?P<upper>up\s+to\s+)?{_AMOUNT}{_RANGE}(?:\s*(?P<currency3>{_CURRENCY}))?(?:\s*{_PERIOD})?(?!\w|[.,]\d)", re.I)
_CUE = re.compile(r"\b(salary|compensation|pay|base|range|OTE)\b", re.I)
_NON_PAY = re.compile(r"\b(raised|funding|funded|revenue|valuation|budget|bonus|equity|stock|sign.on|relocation)\b", re.I)
_NOT_AMOUNT = re.compile(r"\s*(?:%|employees?\b|people\b|years?\b|users?\b|customers?\b|(?:signing\s+)?bonus\b|(?:in\s+)?(?:equity|stock)\b)", re.I)
_CODES = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "CA$": "CAD", "C$": "CAD",
          "AU$": "AUD", "A$": "AUD", "S$": "SGD"}
_PERIODS = {"annum": "year", "yr": "year", "annually": "year", "yearly": "year", "mo": "month",
            "monthly": "month", "wk": "week", "weekly": "week", "daily": "day", "hourly": "hour", "hr": "hour"}


def _number(value: str, thousands: bool) -> float:
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", value):
        value = value.replace(",", "").replace(".", "")
    return float(value) * (1000 if thousands else 1)


def _extract(text: str) -> tuple[tuple[float | None, float, str | None, str], str] | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    candidates = []
    for match in _PAY.finditer(text):
        g = match.groupdict()
        if g["number1"] == "401" and g["k1"] and not g["number2"]:
            continue
        before = re.split(r"(?<=[.!?;])\s+|\n", text[max(0, match.start() - 60):match.start()])[-1]
        after = text[match.end():match.end() + 30]
        if _NOT_AMOUNT.match(after) or _NON_PAY.search(before):
            continue
        currencies = [c.upper() for c in (g["currency1"], g["currency2"], g["currency3"]) if c]
        codes = {_CODES.get(c, c) for c in currencies if c != "$"}
        if not codes and "$" in currencies:
            codes = {"USD"}
        if len(codes) > 1 or (not currencies and not (g["number2"] and g["k2"])):
            continue
        currency = next(iter(codes), None)
        period = g["period"] or g["adverb"] or "year"
        period = _PERIODS.get(period.lower(), period.lower())
        k1, k2 = bool(g["k1"]), bool(g["k2"])
        # A trailing K scales both ends of a compact range such as 150-190K.
        if g["number2"] and k2 and not k1 and float(g["number1"].replace(",", "")) < 1000:
            k1 = True
        lo = _number(g["number1"], k1)
        hi = _number(g["number2"], k2) if g["number2"] else lo
        floor = {"year": 10000, "month": 1000, "week": 200, "day": 50, "hour": 10}[period]
        if not floor <= lo <= hi or hi > {"year": 2000000, "month": 200000, "week": 50000, "day": 10000, "hour": 2000}[period]:
            continue
        cue = bool(_CUE.search(before) or _CUE.match(after.lstrip()))
        result = (None if g["upper"] else lo, hi, currency, period)
        candidates.append((cue, result, match.group().strip()))
    if not candidates:
        return None
    preferred = [c for c in candidates if c[0]] or candidates
    # Multiple different regional/level ranges cannot safely stand for one salary.
    if len({c[1] for c in preferred}) != 1:
        return None
    return preferred[0][1], preferred[0][2]


def parse_salary(text: str) -> tuple[float | None, float, str | None, str] | None:
    """Return original-unit bounds, currency and period; $ means USD, bare K has no currency."""
    parsed = _extract(text)
    return parsed[0] if parsed else None


def fill_salary(job: Job) -> None:
    """Fill only absent source salaries, preserving the stated text and provenance."""
    if job.salary_min is not None or job.salary_max is not None:
        return
    parsed = _extract(job.description)
    if parsed:
        values, original = parsed
        job.salary_min, job.salary_max, job.salary_currency, job.salary_period = values
        job.ats_metadata["salary_from_text"] = original
