"""Entry point: `uv run python -m scanner run [--dry-run] [--days N]`."""

from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scanner.dedup import collapse_by_seniority, fingerprint
from scanner.eligibility import Screener, decide
from scanner.filters import Filters
from scanner.report import reason_bucket, write_drop_log, write_report
from scanner.sources import freehire
from scanner.store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "private" / "config" / "config.yml"
log = logging.getLogger("scanner")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def run(config_path: Path, dry_run: bool, days: int | None, skip_llm: bool = False) -> Path | None:
    load_env(ROOT / "private" / "config" / ".env")
    cfg = yaml.safe_load(config_path.read_text())
    out = cfg["output"]
    jobs_dir, state_dir = ROOT / out["jobs_dir"], ROOT / out["state_dir"]
    store = Store(state_dir / "scanner.sqlite")

    now = datetime.now(timezone.utc)
    local_now = datetime.now()
    today = local_now.date().isoformat()

    src = cfg["sources"]["freehire"]
    if days is None:
        full = store.is_empty() or store.last_run_incomplete()
        days = src["first_run_days"] if full else src["daily_lookback_days"]
    log.info("fetching jobs first seen in the last %s days", days)

    # 1. fetch, deduplicating by id (one job can appear under several categories)
    fetched: dict[str, object] = {}
    for job in freehire.fetch(src, days):
        fetched.setdefault(job.id, job)
    for job in fetched.values():
        store.upsert(job, today)

    # 2. filters
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], now)
    dropped, passed = [], []
    for job in fetched.values():
        if reason := filters.evaluate(job):
            dropped.append((job, reason))
        else:
            passed.append(job)

    # 3. never report the same posting twice (across runs and within this run)
    new, seen_fp = [], set()
    for job in passed:
        if reason := store.already_reported(job):
            dropped.append((job, reason))
            continue
        fp = fingerprint(job.company_key, job.title)
        if fp in seen_fp:
            dropped.append((job, "already reported (duplicate posting in this run)"))
            continue
        seen_fp.add(fp)
        new.append(job)

    # 4. LLM: can I be hired for it from where I live, and is it the right role?
    deferred = []
    if not skip_llm:
        llm_cfg = cfg["llm"]
        screener = Screener(llm_cfg, ROOT / llm_cfg["prompt"], store.db)
        results, deferred = screener.screen(new)
        screened = []
        for job in new:
            if job.id not in results:
                continue
            job.screen = results[job.id]
            bucket, why = decide(job.screen)
            if bucket == "drop":
                evidence = job.screen.get("eligibility_evidence") or ""
                dropped.append((job, f"llm: {why}" + (f' "{evidence[:160]}"' if evidence else "")))
            else:
                job.bucket = bucket
                screened.append(job)
        new = screened
        if deferred:
            log.warning("%s jobs not screened this run (cap or errors); they'll be retried next run", len(deferred))

    # 5. keep only the most senior opening per company + role family
    new, lower = collapse_by_seniority(new, store.reported_best(cfg["dedup"]["collapse_window_days"]))
    dropped.extend(lower)

    drop_counts = Counter(reason_bucket(r) for _, r in dropped)
    stats = {"fetched": len(fetched), "passed": len(passed)}
    log.info("fetched %s, passed %s, new %s", len(fetched), len(passed), len(new))
    for reason, count in drop_counts.most_common():
        log.info("  dropped %5d  %s", count, reason)

    if dry_run:
        store.db.rollback()
        for job in new[:30]:
            log.info("  %-5s %-28s %-60s %s", job.bucket or "new", job.company[:28], job.title[:60], job.location[:40])
        return None

    jobs_dir.mkdir(parents=True, exist_ok=True)
    report = jobs_dir / f"{today}.md"
    write_report(report, local_now, new, stats, drop_counts, cfg["candidate"]["country"])
    write_drop_log(state_dir / "dropped" / f"{today}.tsv", dropped)
    for job in new:
        store.mark_reported(job, today)
    store.log_run(now.isoformat(), len(fetched), len(passed), len(new), len(new), str(report.relative_to(ROOT)), len(deferred))
    store.commit()
    log.info("wrote %s", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch, filter and write today's job list")
    r.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    r.add_argument("--dry-run", action="store_true", help="print results; write nothing, mark nothing reported")
    r.add_argument("--days", type=int, help="override the fetch window")
    r.add_argument("--skip-llm", action="store_true", help="Stage 1 only: no eligibility screening")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.cmd == "run":
        run(args.config, args.dry_run, args.days, args.skip_llm)
