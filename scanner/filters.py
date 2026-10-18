"""Deterministic filters. Each returns a drop reason, or None to keep the job."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from scanner import liveness
from scanner.dedup import seniority_level
from scanner.models import Job
from scanner.pay_market import DEFAULT_ALLOW, local_only


def _rx(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def _first(patterns: list[re.Pattern], text: str) -> re.Match | None:
    for p in patterns:
        if m := p.search(text):
            return m
    return None


# Role separators split a title only when both sides name a role, so "Senior Engineer, Payments" stays whole.
_ROLE_SEP = re.compile(r"( [|/·] |; |, | & )")
_IC_NOUN = re.compile(r"\b(engineer|developer|architect|programmer|designer|scientist|analyst)s?\b", re.I)
_ROLE_NOUN = re.compile(rf"{_IC_NOUN.pattern}|\b(manager|director)s?\b", re.I)


def _role_segments(title: str) -> list[str]:
    """Split a multi-role title ("Backend Engineer | Junior Frontend Developer") into one title per role."""
    parts = _ROLE_SEP.split(title)
    segments = [parts[0]]
    for sep, part in zip(parts[1::2], parts[2::2]):
        # after a comma, "Manager"/"Director" qualifies the role ("Manager, AI Architect") rather than listing another
        noun = _IC_NOUN if sep == ", " else _ROLE_NOUN
        if noun.search(segments[-1]) and noun.search(part):
            segments.append(part)
        else:
            segments[-1] += sep + part
    return segments


class Filters:
    def __init__(self, cfg: dict, seniority_rank: dict[str, int], now: datetime, profiles=None):
        self.now = now
        self.profiles = profiles
        self.market = cfg.get("company_market", {})
        self.max_age = timedelta(days=cfg["max_age_days"])
        self.max_age_live = timedelta(days=cfg.get("max_age_days_live", 45))
        self.max_age_watchlist = timedelta(days=cfg.get("max_age_days_watchlist", 120))
        self.stale_after = timedelta(days=cfg.get("not_seen_live_days", 10))
        self.families = {name: _rx(pats) for name, pats in cfg["role_families"].items()}
        self.title_exclude = _rx(cfg["title_exclude"])

        stack = cfg["stack"]
        self.python = _rx(stack["python_patterns"])
        self.other_langs = _rx(stack["other_language_patterns"])
        self.title_langs = _rx(stack.get("title_other_language_patterns", []))
        self.other_lang_tags = set(stack.get("other_language_skill_tags", []))
        self.strong = [s.lower() for s in stack.get("strong", [])]

        sal = cfg["salary"]
        self.salary_floor = sal["min_usd_year"]
        self.usd_rates = sal["usd_rates"]
        self.period_mult = sal["period_multipliers"]

        self.employment_types = set(cfg.get("employment_types") or [])
        self.hard_drop = {reason: _rx(pats) for reason, pats in cfg["hard_drop_patterns"].items()}

        ex = cfg["exclude"]
        self.companies = [re.compile(rf"\b{re.escape(c.lower())}\b") for c in ex.get("companies", [])]
        self.company_types = set(ex.get("company_types", []))
        self.domains = set(ex.get("domains", []))
        self.title_keywords = _rx(ex.get("title_keywords", []))
        self.company_name_keywords = _rx(ex.get("company_name_keywords", []))
        self.summary_keywords = _rx(ex.get("summary_keywords", []))
        self.description_domains = {name: (re.compile(d["pattern"], re.I), d.get("min_hits", 3))
                                    for name, d in (ex.get("description_domains") or {}).items()}
        self.non_english = cfg.get("non_english", "flag")
        self.seniority_rank = seniority_rank

    def evaluate(self, job: Job) -> str | None:
        """Annotate the job (family, rank, tags) and return a drop reason or None."""
        for check in (
            self._market,
            self._company,
            self._age,
            self._title,
            self._employment,
            self._company_type,
            self._domain,
            self._hard_drop,
            self._stack,
            self._salary,
        ):
            if reason := check(job):
                return reason
        self._tag(job)
        return None

    def _market(self, job: Job) -> str | None:
        country, _ = self.profiles.resolve(job) if self.profiles else (None, "")
        if country in set(self.market.get("deny_hq_countries", ["in", "bd"])):
            return f"company based in {country.upper()}"
        if self.market.get("drop_local_only_locations", True) and (local := local_only(job)):
            # A foreign company hiring in Bangladesh is the best case; a local one isn't.
            allow = set(self.market.get("allow_hq_countries", DEFAULT_ALLOW))
            if local == {"bd"} and country in allow:
                return None
            return f"location only in {'/'.join(sorted(c.upper() for c in local))}"
        return None

    def _company(self, job: Job) -> str | None:
        names = f"{job.company} {job.company_key.replace('-', ' ')}".lower()
        for p in self.companies:
            if p.search(names):
                return f"excluded company ({job.company})"
        if m := _first(self.company_name_keywords, names):
            return f"staffing/agency company name ('{m.group(0)}': {job.company})"
        return None

    def _age(self, job: Job) -> str | None:
        job.screen.pop("needs_live", None)
        anchor = job.age_anchor
        max_age = self.max_age_watchlist if job.watchlist else self.max_age
        if anchor and self.now - anchor > max_age:
            live_board = job.watchlist and job.source == "ats"
            if (live_board or (not job.watchlist and self.now - anchor < self.max_age_live)) and liveness.can_verify(job):
                job.screen["needs_live"] = True
            else:
                return f"older than {max_age.days} days (first seen {anchor:%Y-%m-%d})"
        if not job.watchlist and job.last_seen_at and self.now - job.last_seen_at > self.stale_after:
            return f"not seen live since {job.last_seen_at:%Y-%m-%d} (probably closed)"
        return None

    def _title(self, job: Job) -> str | None:
        first = None
        for segment in _role_segments(job.title):
            if (reason := self._title_segment(job, segment)) is None:
                return None
            first = first or reason
        return first

    def _title_segment(self, job: Job, title: str) -> str | None:
        if m := _first(self.title_exclude, title):
            return f"title excluded ('{m.group(0).strip()}')"
        if m := _first(self.title_keywords, title):
            return f"gig/excluded title ('{m.group(0)}')"
        for name, patterns in self.families.items():
            if _first(patterns, title):
                job.family = name
                break
        else:
            return "title not in target role families"
        level = seniority_level(title, job.seniority)
        job.rank = self.seniority_rank.get(level, self.seniority_rank.get("unknown", 0))
        if level != "unknown":
            job.tags.append(level)
        return None

    def _employment(self, job: Job) -> str | None:
        # Recruitee distinguishes permanent employment and B2B freelance contracts.
        job.employment_type = {"full_time_permanent": "full_time", "freelance": "contract"}.get(
            job.employment_type, job.employment_type)
        if self.employment_types and job.employment_type and job.employment_type not in self.employment_types:
            return f"employment type {job.employment_type}"
        return None

    def _company_type(self, job: Job) -> str | None:
        if job.company_type in self.company_types:
            return f"company type {job.company_type}"
        return None

    def _domain(self, job: Job) -> str | None:
        if hit := self.domains.intersection(job.domains):
            return f"excluded domain {sorted(hit)}"
        if m := _first(self.summary_keywords, job.summary):
            return f"excluded industry ('{m.group(0)}')"
        if not job.domains:  # sources without industry tags (live ATS boards, HN): judge from the text
            for name, (rx, min_hits) in self.description_domains.items():
                if len(rx.findall(job.description)) >= min_hits:
                    return f"excluded domain ['{name}'] (from description)"
        return None

    def _hard_drop(self, job: Job) -> str | None:
        if job.requires_clearance:
            return "security_clearance (tagged by source)"
        text = job.description
        for reason, patterns in self.hard_drop.items():
            if m := _first(patterns, text):
                return f"{reason}: \"{_snippet(text, m)}\""
        return None

    def _stack(self, job: Job) -> str | None:
        if not _first(self.python, job.title) and (m := _first(self.title_langs, job.title)):
            return f"no Python; title names {m.group(0)}"
        text = f"{job.title}\n{job.description}"
        skills = {s.lower() for s in job.skills}
        if "python" in skills or _first(self.python, text):
            job.tags.append("python")
        else:
            langs = sorted({m.group(0).lower() for p in self.other_langs if (m := p.search(text))})
            langs += sorted(self.other_lang_tags.intersection(skills) - set(langs))
            if langs:
                return f"no Python; stack is {', '.join(langs[:4])}"
            job.tags.append("no-language-named")
        lowered = text.lower()
        matched = [s for s in self.strong if s in skills or re.search(rf"\b{re.escape(s)}\b", lowered)]
        if matched:
            job.tags.append(f"stack:{len(matched)}")
        return None

    def _salary(self, job: Job) -> str | None:
        top = job.salary_max or job.salary_min
        if not top or not job.salary_currency:
            return None
        rate = self.usd_rates.get(job.salary_currency.upper())
        mult = self.period_mult.get(job.salary_period or "year")
        if rate is None or mult is None:
            return None
        usd_year = top * rate * mult
        if usd_year < self.salary_floor:
            return f"salary max ~${usd_year:,.0f}/yr below floor"
        return None

    def _tag(self, job: Job) -> None:
        if job.posting_language and job.posting_language != "en" and self.non_english == "flag":
            job.tags.append(f"lang:{job.posting_language}")
        if (job.reality or {}).get("class") == "likely-evergreen":
            job.tags.append("evergreen")


def _snippet(text: str, m: re.Match, pad: int = 60) -> str:
    start, end = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return " ".join(text[start:end].split())
