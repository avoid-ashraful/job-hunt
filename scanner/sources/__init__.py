"""Job sources. Each module here exposes `fetch(cfg: dict, days: int) -> Iterator[Job]`.

A source is enabled by a `sources.<module name>` block in config with `enabled: true`.
Sources fetch concurrently and yield in config order; when two return the same job id,
the first wins, so list the most authoritative source (the employer's own ATS) first.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from scanner.models import Job

log = logging.getLogger(__name__)


def fetch_all(sources_cfg: dict, days: int, source_stats: list[dict] | None = None,
              budget_minutes: float = 10, workers: int = 4) -> Iterator[Job]:
    """Stop starting sources after the soft budget; in-flight requests finish normally."""
    deadline = time.monotonic() + max(0, budget_minutes) * 60

    def fetch_source(item):
        name, cfg = item
        if time.monotonic() >= deadline:
            return [], {"name": name, "count": 0, "seconds": 0,
                        "error": "skipped: fetch budget exceeded"}
        started = time.perf_counter()
        jobs = []
        error = None
        try:
            module = importlib.import_module(f"scanner.sources.{name}")
            for job in module.fetch(cfg, days):
                jobs.append(job)
        except Exception as exc:  # one broken source must not stop the daily run
            error = f"{type(exc).__name__}: {exc}"
        seconds = time.perf_counter() - started
        return jobs, {"name": name, "count": len(jobs), "seconds": seconds, "error": error}

    enabled = [(name, cfg) for name, cfg in sources_cfg.items() if (cfg or {}).get("enabled", False)]
    with ThreadPoolExecutor(max_workers=max(1, min(8, workers))) as pool:
        results = list(pool.map(fetch_source, enabled))
    for jobs, stats in results:
        name, count, seconds, error = (stats[k] for k in ("name", "count", "seconds", "error"))
        if source_stats is not None:
            source_stats.append(stats)
        log_error = " ".join(error.split()) if error else None
        log.info("source %s: %s jobs (%.1fs)%s", name, count, seconds,
                 f" failed: {log_error}" if error else "")
        if error:
            log.warning("source %s failed after %s jobs: %s", name, count, log_error)
        elif count == 0:
            log.warning("source %s returned 0 jobs", name)
        yield from jobs
