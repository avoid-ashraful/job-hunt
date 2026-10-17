"""Job sources. Each module here exposes `fetch(cfg: dict, days: int) -> Iterator[Job]`.

A source is enabled by a `sources.<module name>` block in config with `enabled: true`.
Sources run in config order; when two sources return the same job id, the first one wins,
so list the most authoritative source (the employer's own ATS) first.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator

from scanner.models import Job

log = logging.getLogger(__name__)


def fetch_all(sources_cfg: dict, days: int) -> Iterator[Job]:
    for name, cfg in sources_cfg.items():
        if not (cfg or {}).get("enabled", False):
            continue
        module = importlib.import_module(f"scanner.sources.{name}")
        count = 0
        try:
            for job in module.fetch(cfg, days):
                count += 1
                yield job
        except Exception:  # one broken source must not stop the daily run
            log.exception("source %s failed after %s jobs", name, count)
        log.info("source %s: %s jobs", name, count)
