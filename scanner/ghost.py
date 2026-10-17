"""Ghost-job score: is this posting likely an opening someone is actually hiring for?

Each signal is weak alone, so signals add up (weights in `rules.ghost` of config):
a score at or above `drop_at` drops the job; at or above `flag_at` it is kept but
flagged, and can't be in Apply.

Signals
    freehire_evergreen   freehire's own reality class says likely-evergreen
    fake_freshness       freehire: an old posting whose "posted" date was refreshed
    reposted             the same role was posted under new ids over time (freehire history or ours)
    mass_posted          many concurrent copies of one role (same company + title)
    evergreen_text       wording of a pipeline/talent-pool posting rather than a real opening
    scraped_repost       a third party re-posted the job (page chrome of another site in the text)
    aggregator_source    only seen on an aggregator that rewrites postings, never on an ATS
    thin_description     too short to describe a real job
"""

from __future__ import annotations

import re

from scanner.models import Job


class GhostRules:
    def __init__(self, cfg: dict, history):
        """`history(job)` returns (reposts, concurrent): other ids with the same company+title
        first seen well before this one, and copies first seen around the same time."""
        self.weights = cfg["weights"]
        self.drop_at = cfg["drop_at"]
        self.flag_at = cfg["flag_at"]
        self.repost_min = cfg.get("repost_min", 2)
        self.mass_min = cfg.get("mass_min", 5)
        self.min_chars = cfg.get("min_description_chars", 400)
        self.aggregators = re.compile(cfg["aggregator_sources"], re.I)
        self.evergreen = [re.compile(p, re.I) for p in cfg["evergreen_text"]]
        self.scraped = [re.compile(p, re.I) for p in cfg["scraped_repost_text"]]
        self.history = history

    def score(self, job: Job) -> tuple[int, list[str]]:
        reality = job.reality or {}
        reposts, concurrent = self.history(job)
        fh_reposts = max(0, (reality.get("repost_count") or 1) - (reality.get("mass_posting_count") or 1))
        fired: list[str] = []
        if reality.get("class") == "likely-evergreen":
            fired.append("freehire_evergreen")
        if reality.get("fake_freshness"):
            fired.append("fake_freshness")
        if max(reposts, fh_reposts) >= self.repost_min:
            fired.append(f"reposted ({max(reposts, fh_reposts)}x)")
        if max(concurrent, reality.get("mass_posting_count") or 1) >= self.mass_min:
            fired.append(f"mass_posted ({max(concurrent, reality.get('mass_posting_count') or 1)} copies)")
        if m := _first(self.evergreen, job.description):
            fired.append(f"evergreen_text (\"{m.group(0)[:60]}\")")
        if _first(self.scraped, job.description):
            fired.append("scraped_repost")
        source = job.id.split(":", 1)[0]
        if not job.watchlist and self.aggregators.fullmatch(source):
            fired.append(f"aggregator_source ({source})")
        if len(job.description.strip()) < self.min_chars:
            fired.append("thin_description")
        score = sum(self.weights.get(f.split(" ")[0], 0) for f in fired)
        return score, fired


def _first(patterns: list[re.Pattern], text: str) -> re.Match | None:
    for p in patterns:
        if m := p.search(text):
            return m
    return None
