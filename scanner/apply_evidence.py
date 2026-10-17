"""Conservative evidence gate for the Apply tier, independent of eligibility verdicts."""

from __future__ import annotations

import re

from scanner.eligibility import _norm
from scanner.models import Job


DEFAULT_PATTERNS = {
    "worldwide": r"\b(?:worldwide|world-wide|anywhere in the world|any country|all countries)\b",
    "global_hiring": r"\b(?:global(?:ly)? hiring|hir(?:e|ing) globally|recruit(?:ing)? globally)\b",
    "candidate_region": r"\b(?:remote|hir(?:e|ing)|candidates|applicants|countries|locations)\b[^.;\n]{0,60}\b(?:apac|asia[- ]pacific|south asia|(?<!southeast )(?<!south-east )(?<!east )(?<!central )asia|bangladesh)\b",
    "any_location_candidates": r"\bopen to (?:candidates|applicants) from any location\b",
    "anywhere_hiring": r"\bfrom anywhere\b[^.;\n]{0,40}\bhire\b|\bhire\b[^.;\n]{0,40}\bfrom anywhere\b",
    "location_anywhere": r"\blocation\W+(?:remote\W+)?anywhere\b",
    "eor_countries": r"\b(?:eor|employer of record|employee of record)\b[^.;\n]{0,80}\b(?:countr(?:y|ies)|global(?:ly)?|worldwide)\b",
}
DEFAULT_ATS_LOCATION = r"^(?:remote[\s,;:()/–-]*)?(?:worldwide|anywhere|global)(?:[\s)]*)$"
WEAK_CONTEXT = re.compile(
    r"\b(?:not|cannot|can't|except|excluding|outside|only|workation|per year|a year|each year|"
    r"for up to|weeks? per|days per|countr(?:y|ies) where|countr(?:y|ies) in which|"
    r"customers?|clients?|users?|our team|existing employees)\b", re.I
)


class ApplyEvidence:
    def __init__(self, cfg: dict):
        self.patterns = [re.compile(p, re.I) for p in cfg.get("patterns", DEFAULT_PATTERNS).values()]
        self.ats_location = re.compile(cfg.get("ats_location", DEFAULT_ATS_LOCATION), re.I)

    def weak_reason(self, job: Job) -> str | None:
        """Return a demotion reason unless a body quote or employer ATS location is strong."""
        # A watched company's aggregator copy is still not an employer-supplied ATS location.
        if job.source in {"ats", "workable"} and self.ats_location.fullmatch(job.location.strip()):
            return None
        evidence = _norm(job.screen.get("evidence") or "").strip("\"' .")
        if evidence and evidence in _norm(job.description) and self._strong(evidence):
            return None
        # The quote picked by a rule/AI may be the weaker sentence; another body sentence can still settle it.
        if any(self._strong(_norm(s)) for s in re.split(r"[.!?;\n]+", job.description)):
            return None
        if not evidence or evidence not in _norm(job.description):
            return "no verbatim quote from the description body"
        return "quote does not establish worldwide or Bangladesh-compatible hiring"

    def _strong(self, text: str) -> bool:
        return bool(text) and not WEAK_CONTEXT.search(text) and any(p.search(text) for p in self.patterns)
