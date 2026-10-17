from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    """A posting normalized across sources."""

    id: str  # "<ats>:<external id>", stable across sources that read the same ATS
    source: str  # where we fetched it (freehire, career-ops, ...)
    url: str
    title: str
    company: str
    company_key: str  # normalized company identifier used for grouping
    description: str
    location: str = ""
    regions: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    first_seen_at: datetime | None = None  # when the source first recorded it
    last_seen_at: datetime | None = None  # when the source last saw it live
    seniority: str | None = None
    category: str | None = None
    employment_type: str | None = None
    company_type: str | None = None
    domains: list[str] = field(default_factory=list)
    posting_language: str | None = None
    summary: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    requires_clearance: bool | None = None
    reality: dict = field(default_factory=dict)
    watchlist: bool = False  # live employer board, or a freehire copy from a watched company

    # filled in by the pipeline
    family: str | None = None
    rank: int = 0
    tags: list[str] = field(default_factory=list)
    screen: dict = field(default_factory=dict)  # LLM facts (Stage 2)
    bucket: str = ""  # apply / check

    @property
    def age_anchor(self) -> datetime | None:
        """Earliest known date: a restated "posted" date cannot make a job look fresh."""
        dates = [d for d in (self.posted_at, self.first_seen_at) if d]
        return min(dates) if dates else None
