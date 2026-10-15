"""Company-level priors from vendor/remote-jobs (remoteintech): who hires worldwide."""

from __future__ import annotations

import re
from pathlib import Path

from scanner.dedup import normalize_company


def load_region_map(companies_dir: Path) -> dict[str, str]:
    """{normalized company key: region} from remoteintech's company files."""
    regions: dict[str, str] = {}
    if not companies_dir.is_dir():
        return regions
    for f in companies_dir.glob("*.md"):
        head = f.read_text(errors="ignore").split("\n---", 1)[0]
        region = re.search(r"^region:\s*(\S+)", head, re.M)
        if not region:
            continue
        for key in ("title", "slug"):
            if m := re.search(rf"^{key}:\s*\"?([^\"\n]+)", head, re.M):
                regions[normalize_company(m.group(1))] = region.group(1)
    return regions
