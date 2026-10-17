"""Read small, editable marks from daily Markdown tables into durable feedback."""

from __future__ import annotations

import re
from pathlib import Path

from scanner.applications import Applications

LABELS = {"a": "applied", "g": "good", "x": "not-eligible", "e": "eligible",
          "r": "not-relevant", "d": "duplicate", "c": "closed"}
REF = re.compile(r"^`?([0-9a-f]{6})`?$")
MARK = re.compile(r"^\[\s*([agxerdc])(?:\s+(.+?))?\s*\]$", re.I)
BLANK = re.compile(r"^\[\s*\]$")


def parse(path: Path) -> dict[str, tuple[str, str] | None]:
    """Return latest mark per ref; None means a deliberately emptied mark."""
    marks = {}
    ref_col = mark_col = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            ref_col = mark_col = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if "Ref" in cells and "Mark" in cells:
            ref_col, mark_col = cells.index("Ref"), cells.index("Mark")
            continue
        if ref_col is None or max(ref_col, mark_col) >= len(cells):
            continue
        ref = REF.fullmatch(cells[ref_col])
        if not ref:
            continue
        cell = cells[mark_col]
        match = MARK.fullmatch(cell)
        if match:
            if ref[1] not in marks or marks[ref[1]] is None:
                marks[ref[1]] = (LABELS[match[1].lower()], (match[2] or "").strip())
        elif BLANK.fullmatch(cell):
            marks.setdefault(ref[1], None)
    return marks


def sync(store, jobs_dir: Path) -> tuple[int, int]:
    """Read all reports oldest first, so the latest edit of a repeated ref wins."""
    apps = Applications(store)
    marks = {}
    for path in sorted(jobs_dir.glob("????-??-??.md")):
        marks.update(parse(path))
    saved = 0
    for ref, mark in marks.items():
        if mark is None:
            apps.clear_report_feedback(ref)
        elif apps.feedback(ref, *mark):
            saved += 1
    store.commit()
    return saved, len(marks)
