"""Deterministic parsers: decide what can be decided from the posting text without an LLM.

Location
    Every sentence of the description is matched against named rule sets from
    config (`rules.location`). A captured place is checked against the
    candidate's own country/region, so "must be located in Europe or Asia" counts
    as allowed. Outcomes:
      locked / positive / timezone   decided by a rule (rule id + evidence kept)
      ambiguous                      location wording the rules can't settle: send to the LLM
      silent                         the description says nothing about location: Check bucket

Role
    agency/outsourcing wording, working language, frontend-heavy fullstack,
    and a stack-based fit score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scanner.models import Job

SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
LOCKED, POSITIVE, TIMEZONE, AMBIGUOUS, SILENT = "locked", "positive", "timezone", "ambiguous", "silent"


@dataclass
class Verdict:
    outcome: str  # locked / positive / timezone / ambiguous / silent
    rule: str  # rule id, e.g. "lock.must_reside"
    evidence: str = ""
    place: str = ""
    snippets: list[str] = field(default_factory=list)  # location sentences, for the LLM when ambiguous


def _compile(patterns: dict[str, str]) -> dict[str, re.Pattern]:
    return {name: re.compile(p, re.I | re.M) for name, p in (patterns or {}).items()}


ABBREVIATIONS = [
    (re.compile(r"\bU\.S\.A\.?", re.I), "USA"),
    (re.compile(r"\bU\.S\.(?=\W|$)", re.I), "US"),
    (re.compile(r"\bU\.K\.(?=\W|$)", re.I), "UK"),
    (re.compile(r"\b(e\.g|i\.e|etc|approx|incl)\.", re.I), r"\1"),
]


def normalize_text(text: str) -> str:
    """Remove dots that would split sentences ("U.S.", "e.g.") before matching."""
    for rx, repl in ABBREVIATIONS:
        text = rx.sub(repl, text)
    return text


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(normalize_text(text)) if len(s.strip()) > 3]


class LocationRules:
    def __init__(self, cfg: dict):
        self.lock = _compile(cfg["lock"])
        self.positive = _compile(cfg["positive"])
        self.weak_positive = _compile(cfg.get("weak_positive", {}))
        self.weak_lock = _compile(cfg.get("weak_lock", {}))
        self.timezone = _compile(cfg["timezone"])
        self.candidate_place = re.compile(cfg["candidate_place"], re.I)
        self.neutral_place = re.compile(cfg["neutral_place"], re.I)
        self.timezone_place = re.compile(cfg["timezone_place"], re.I)
        self.cue = re.compile(cfg["cue"], re.I)
        self.perk = re.compile(cfg.get("perk", r"(?!x)x"), re.I)
        self.ignore = re.compile(cfg.get("ignore_sentence", r"(?!x)x"), re.I)
        self.geo = re.compile(cfg["geo"], re.I)
        self.max_snippet_chars = cfg.get("max_snippet_chars", 1500)
        self.residence_rules = set(cfg.get("residence_rules", []))
        self.residence_zone = re.compile(cfg.get("residence_zone_lock", r"(?!x)x"), re.I)

    def _place_kind(self, m: re.Match, rule: str = "") -> str:
        """lock / candidate / timezone / neutral for the place a lock rule captured."""
        place = _place(m)
        if self.neutral_place.search(place):
            return "neutral"
        if self.candidate_place.search(place):
            return "candidate"
        if rule in self.residence_rules and self.residence_zone.search(m.group(0)):
            return "lock"  # "must reside in the Eastern time zone" means living there; "located in" stays timezone
        if re.match(r"(?:an?\s+|the\s+)?(?:\w+\s+){0,2}time ?zones?\b", place, re.I):
            return "timezone"  # "located in a US time zone" is a working-hours constraint
        if self.geo.search(place):
            return "lock"
        if self.timezone_place.search(place + " "):
            return "timezone"
        return "neutral"  # "must be based in a quiet place" is not a location restriction

    def classify(self, job: Job) -> Verdict:
        locks, positives, weak, zones, snippets = [], [], [], [], []
        for sent in sentences(job.description):
            if self.ignore.search(sent):
                continue
            hit = False
            perk = bool(self.perk.search(sent))
            for name, rx in self.lock.items():
                for m in rx.finditer(sent):
                    kind = self._place_kind(m, name)
                    hit = hit or (kind != "neutral" and not (perk and kind == "candidate"))
                    if kind == "lock":
                        locks.append((f"lock.{name}", sent, _place(m)))
                    elif kind == "candidate" and not perk:
                        positives.append((f"lock_place_ok.{name}", sent, _place(m)))
                    elif kind == "timezone":
                        zones.append((f"lock_place_tz.{name}", sent, _place(m)))
            for name, rx in self.weak_lock.items():
                for m in rx.finditer(sent):
                    kind = self._place_kind(m)
                    if kind == "lock":
                        weak.append((f"weak_lock.{name}", sent, _place(m)))
                        hit = True
                    elif kind == "candidate":
                        positives.append((f"weak_lock_place_ok.{name}", sent, _place(m)))
                        hit = True
            for name, rx in self.positive.items():
                if not perk and rx.search(sent):
                    positives.append((f"positive.{name}", sent, ""))
                    hit = True
            for name, rx in self.weak_positive.items():
                if not perk and rx.search(sent):
                    weak.append((f"weak.{name}", sent, ""))
                    hit = True
            for name, rx in self.timezone.items():
                if rx.search(sent):
                    zones.append((f"timezone.{name}", sent, ""))
                    hit = True
            if hit or (self.cue.search(sent) and self.geo.search(sent)):
                snippets.append(sent)

        snippet_text = _cap(snippets, self.max_snippet_chars)
        if locks and not positives:
            rule, ev, place = locks[0]
            return Verdict(LOCKED, rule, ev, place, snippet_text)
        if positives and not locks:
            rule, ev, place = positives[0]
            return Verdict(POSITIVE, rule, ev, place, snippet_text)
        if locks and positives:
            return Verdict(AMBIGUOUS, "conflict.lock_and_positive", snippets=snippet_text)
        if weak:
            return Verdict(AMBIGUOUS, weak[0][0], weak[0][1], snippets=snippet_text)
        if zones:
            rule, ev, place = zones[0]
            return Verdict(TIMEZONE, rule, ev, place, snippet_text)
        if snippets:
            return Verdict(AMBIGUOUS, "unmatched_location_wording", snippets=snippet_text)
        return Verdict(SILENT, "silent")


def _place(m: re.Match) -> str:
    return (m.groupdict().get("place") or m.group(0)).strip()


def _cap(snippets: list[str], limit: int) -> list[str]:
    out, total = [], 0
    for s in dict.fromkeys(snippets):  # dedupe, keep order
        s = s[:400]
        if total + len(s) > limit:
            break
        out.append(s)
        total += len(s)
    return out


class RoleRules:
    def __init__(self, cfg: dict):
        self.agency = _compile(cfg["agency"])
        self.frontend_terms = re.compile(cfg["frontend_terms"], re.I)
        self.backend_terms = re.compile(cfg["backend_terms"], re.I)
        self.frontend_ratio = cfg.get("frontend_heavy_ratio", 2.0)
        self.english_required = re.compile(cfg["english_mentioned"], re.I)
        self.non_latin = re.compile(r"[Ѐ-ӿ؀-ۿ぀-ヿ一-鿿가-힯]")
        fit = cfg["fit"]
        self.core = [re.compile(p, re.I) for p in fit["core"]]
        self.strong = [re.compile(p, re.I) for p in fit["strong"]]

    def evaluate(self, job: Job) -> tuple[str, str] | None:
        """Return (rule id, evidence) for a role-level drop, or None."""
        text = job.description
        for name, rx in self.agency.items():
            if m := rx.search(text):
                return f"agency.{name}", _around(text, m)
        lang = job.posting_language
        if lang and lang != "en":
            if not self.english_required.search(text):
                return f"language.{lang}", "posting is not in English and doesn't ask for English"
        elif len(self.non_latin.findall(text)) > 0.3 * max(1, len(re.findall(r"[A-Za-z]", text))):
            return "language.non_latin", "description is mostly in a non-Latin script"
        if job.family == "fullstack":
            fe, be = len(self.frontend_terms.findall(text)), len(self.backend_terms.findall(text))
            if fe >= 6 and fe > self.frontend_ratio * be:
                return "role.frontend_heavy", f"{fe} frontend vs {be} backend mentions"
        return None

    def fit_score(self, job: Job) -> tuple[int, str]:
        text = f"{job.title}\n{job.description}"
        core = [rx.pattern for rx in self.core if rx.search(text)]
        strong = [rx.pattern for rx in self.strong if rx.search(text)]
        score = 1
        score += 1 if core else 0
        score += 1 if job.family in {"python", "backend", "applied_ai"} else 0
        score += 1 if len(strong) >= 3 else 0
        score += 1 if len(strong) >= 6 else 0
        why = f"core: {', '.join(_short(core)) or '-'}; stack matches: {len(strong)}"
        return min(score, 5), why


def _short(patterns: list[str]) -> list[str]:
    return [re.sub(r"\\b|[()?:\\]", "", p) for p in patterns]


def _around(text: str, m: re.Match, pad: int = 60) -> str:
    return " ".join(text[max(0, m.start() - pad) : m.end() + pad].split())
