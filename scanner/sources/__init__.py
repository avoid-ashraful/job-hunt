"""Job sources. Each module here exposes `fetch(cfg: dict, days: int) -> Iterator[Job]`.

A source is enabled by a `sources.<module name>` block in config with `enabled: true`.
Sources run in config order; when two sources return the same job id, the first one wins,
so list the most authoritative source (the employer's own ATS) first.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Iterator

from scanner.models import Job

log = logging.getLogger(__name__)


def fetch_all(sources_cfg: dict, days: int, source_stats: list[dict] | None = None) -> Iterator[Job]:
    for name, cfg in sources_cfg.items():
        if not (cfg or {}).get("enabled", False):
            continue
        started = time.perf_counter()
        count = 0
        error = None
        try:
            module = importlib.import_module(f"scanner.sources.{name}")
            for job in module.fetch(cfg, days):
                count += 1
                yield job
        except Exception as exc:  # one broken source must not stop the daily run
            error = f"{type(exc).__name__}: {exc}"
        seconds = time.perf_counter() - started
        if source_stats is not None:
            source_stats.append({"name": name, "count": count, "seconds": seconds, "error": error})
        log_error = " ".join(error.split()) if error else None
        log.info("source %s: %s jobs (%.1fs)%s", name, count, seconds,
                 f" failed: {log_error}" if error else "")
        if error:
            log.warning("source %s failed after %s jobs: %s", name, count, log_error)
        elif count == 0:
            log.warning("source %s returned 0 jobs", name)
