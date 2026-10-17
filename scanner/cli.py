"""Entry point.

    uv run python -m scanner run [--dry-run] [--days N] [--skip-llm]
    uv run python -m scanner feedback <ref> <label> [note...]
    uv run python -m scanner learn [--days 7]
"""

from __future__ import annotations

import argparse
import logging
import os
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scanner.companies import load_region_map
from scanner import liveness
from scanner.dedup import collapse_by_seniority, description_fingerprint, fingerprint
from scanner.eligibility import NEGATIVE, POSITIVE, Screener, check_evidence
from scanner.filters import Filters
from scanner.ghost import GhostRules
from scanner.models import Job
from scanner.report import reason_bucket, write_drop_log, write_report
from scanner.rules import AMBIGUOUS, LOCKED, SILENT, LocationRules, RoleRules
from scanner.sources import freehire
from scanner.store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "private" / "config" / "config.yml"
FEEDBACK_LABELS = {
    "applied": "I applied",
    "good": "good match",
    "not-eligible": "I can't be hired for it (location/authorization) but it was listed",
    "eligible": "it was listed as Check/dropped but I can be hired",
    "not-relevant": "wrong role/stack/seniority",
    "duplicate": "I've seen this job before",
    "closed": "posting was closed",
}
log = logging.getLogger("scanner")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load(config_path: Path) -> tuple[dict, Store]:
    load_env(ROOT / "private" / "config" / ".env")
    cfg = yaml.safe_load(config_path.read_text())
    return cfg, Store(ROOT / cfg["output"]["state_dir"] / "scanner.sqlite")


def run(config_path: Path, dry_run: bool, days: int | None, skip_llm: bool = False) -> Path | None:
    cfg, store = load(config_path)
    store.dry_run = dry_run
    now = datetime.now(timezone.utc)
    local_now = datetime.now(ZoneInfo(cfg["candidate"].get("timezone", "UTC")))
    today = local_now.date().isoformat()
    dropped: list[tuple[Job, str]] = []

    def drop(job: Job, stage: str, rule: str, reason: str) -> None:
        dropped.append((job, reason))
        store.log_decision(today, job.id, stage, rule, "drop", reason)

    # 1. fetch, deduplicating by id (one job can appear under several categories)
    src = cfg["sources"]["freehire"]
    if days is None:
        days = src["first_run_days"] if store.is_empty() else src["daily_lookback_days"]
    log.info("fetching jobs first seen in the last %s days", days)
    fetched: dict[str, Job] = {}
    for job in freehire.fetch(src, days):
        fetched.setdefault(job.id, job)
    for job in fetched.values():
        store.upsert(job, today)

    # 2. hard filters: title, stack, age, salary, company, hard drops
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], now)
    passed = []
    for job in fetched.values():
        if reason := filters.evaluate(job):
            drop(job, "filter", reason_bucket(reason), reason)
        else:
            passed.append(job)

    # 3. never report the same posting twice (across runs and within this run)
    new, seen_fp = [], set()
    for job in passed:
        if reason := store.already_reported(job):
            drop(job, "dedup", "already_reported", reason)
            continue
        fps = {fingerprint(job.company_key, job.title), description_fingerprint(job)} - {None}
        if fps & seen_fp:
            drop(job, "dedup", "same_run_duplicate", "already reported (duplicate posting in this run)")
            continue
        seen_fp |= fps
        new.append(job)

    # 3b. ghost jobs: reposted, mass-posted, evergreen or re-posted by a third party
    ghost_cfg = cfg["rules"]["ghost"]
    ghosts = GhostRules(ghost_cfg, lambda j: store.role_history(j, ghost_cfg.get("repost_gap_days", 7)))
    real = []
    for job in new:
        score, signals = ghosts.score(job)
        job.screen["ghost"] = (score, signals)
        if score >= ghosts.drop_at:
            drop(job, "ghost", "ghost", f"ghost job (score {score}): {', '.join(signals)}")
        else:
            real.append(job)
    new = real

    # 4. role rules: agency wording, working language, frontend-heavy fullstack; fit score
    role_rules = RoleRules(cfg["rules"]["role"])
    candidates = []
    for job in new:
        if hit := role_rules.evaluate(job):
            rule, evidence = hit
            drop(job, "role", rule, f"{rule}: {evidence}")
            continue
        job.screen["fit"], job.screen["fit_why"] = role_rules.fit_score(job)
        candidates.append(job)

    # 5. location rules; only ambiguous wording goes to the LLM
    loc_rules = LocationRules(cfg["rules"]["location"])
    eligible, check, ambiguous, rule_decided = [], [], [], []
    for job in candidates:
        v = loc_rules.classify(job)
        job.screen.update(decided_by=v.rule, evidence=v.evidence, allowed=v.place, snippets=v.snippets)
        if v.outcome == LOCKED:
            drop(job, "location", v.rule, f"region locked ({v.place}): \"{v.evidence[:160]}\"")
            rule_decided.append((job, v))
        elif v.outcome == AMBIGUOUS:
            ambiguous.append(job)
            store.log_decision(today, job.id, "location", v.rule, "ambiguous", " | ".join(v.snippets)[:500])
        elif v.outcome == SILENT:
            job.screen["why"] = "description says nothing about location"
            check.append(job)
            store.log_decision(today, job.id, "location", v.rule, "check", "")
        elif v.outcome == "timezone":  # only working hours are stated: not proof they hire abroad
            job.screen.update(eligibility="timezone_only", why="only a working-hours requirement; location not stated")
            check.append(job)
            rule_decided.append((job, v))
            store.log_decision(today, job.id, "location", v.rule, "check", v.evidence)
        else:  # positive
            job.screen["eligibility"] = "anywhere"
            eligible.append(job)
            rule_decided.append((job, v))
            store.log_decision(today, job.id, "location", v.rule, "eligible", v.evidence)

    llm_cfg = cfg["llm"]
    screener = None
    if not skip_llm and ambiguous:
        screener = Screener(llm_cfg, ROOT / llm_cfg["prompt"], store.db,
                            log_call=lambda *a: store.log_llm_call(today, *a))
        results = screener.screen([(j, j.screen["snippets"]) for j in ambiguous])
    else:
        results = {}
    for job in ambiguous:
        if job.id not in results:
            job.screen["why"] = "location wording unclear (AI not available this run)"
            check.append(job)
            continue
        raw, model = results[job.id]
        r = check_evidence(job, raw)
        job.screen.update(decided_by=f"llm:{model}", evidence=r["eligibility_evidence"],
                          allowed=r["allowed_locations"], eligibility=r["eligibility"])
        if r["eligibility"] in NEGATIVE:
            drop(job, "llm", r["eligibility"], f"llm: {r['eligibility']} ({r['allowed_locations']}): "
                                               f"\"{r['eligibility_evidence'][:160]}\"")
        elif r["eligibility"] in ("unclear", "timezone_only"):
            job.screen["why"] = ("AI: only a working-hours requirement" if r["eligibility"] == "timezone_only"
                                 else "AI couldn't tell from the text")
            check.append(job)
            store.log_decision(today, job.id, "llm", r["eligibility"], "check", r["eligibility_evidence"])
        else:
            eligible.append(job)
            store.log_decision(today, job.id, "llm", r["eligibility"], "eligible", r["eligibility_evidence"])

    # 6. spot-check a few rule decisions with the LLM: this measures rule accuracy over time
    if not skip_llm and rule_decided and llm_cfg.get("audit_per_run", 0):
        audit(store, today, rule_decided, llm_cfg, screener)

    # 7. buckets: clear eligibility + good fit = Apply; everything else kept = Check
    min_fit = cfg["rules"]["role"].get("min_fit_for_apply", 3)
    for job in eligible:
        ghost_score, signals = job.screen["ghost"]
        if ghost_score >= ghosts.flag_at:
            job.bucket = "check"
            job.screen["why"] = "eligible, but may be a ghost posting"
        elif job.screen["fit"] >= min_fit:
            job.bucket = "apply"
        else:
            job.bucket = "check"
            job.screen["why"] = f"eligible, but low stack fit ({job.screen['fit_why']})"
    # Check is split: tags compatible with the candidate (or a company known to hire worldwide)
    # stay in Check; jobs whose only signal is a region tag that excludes the candidate are long shots.
    priors = load_region_map(ROOT / cfg["candidate"].get("company_priors", "vendor/remote-jobs/src/companies"))
    ok_tags = set(cfg["candidate"].get("tag_regions_ok", ["global"]))
    code = cfg["candidate"].get("country_code", "").lower()
    for job in check:
        prior = priors.get(job.company_key)
        if prior:
            job.screen["prior"] = f"remoteintech lists this company as hiring: {prior}"
        # a region tag like apac is compatible, unless the source pinned it to other countries (apac + India)
        tag_ok = "global" in job.regions or (ok_tags & set(job.regions) and (not job.countries or code in job.countries))
        if prior == "worldwide" or not job.regions or tag_ok:
            job.bucket = "check"
        else:
            job.bucket = "longshot"
            where = ", ".join(job.countries) if ok_tags & set(job.regions) else ", ".join(job.regions)
            job.screen["why"] = (job.screen.get("why", "") + f"; tagged {where} only").lstrip("; ")
    kept = eligible + check

    # 8. keep only the most senior opening per company + role family
    kept, lower = collapse_by_seniority(kept, store.reported_best(cfg["dedup"]["collapse_window_days"]))
    for job, reason in lower:
        drop(job, "collapse", "lower_seniority", reason)

    # 9. ask the employer's ATS whether each posting is still open
    if cfg.get("liveness", {}).get("enabled", True) and kept:
        status = liveness.check_all(kept, cfg.get("liveness", {}).get("workers", 8))
        open_jobs = []
        for job in kept:
            if status[job.id] == liveness.CLOSED:
                drop(job, "liveness", "closed", "closed: the posting is gone from the employer's site")
            else:
                open_jobs.append(job)
        kept = open_jobs

    drop_counts = Counter(reason_bucket(r) for _, r in dropped)
    stats = {"fetched": len(fetched), "passed": len(passed), "ambiguous": len(ambiguous),
             "llm_calls": screener.calls if screener else 0}
    tiers = Counter(j.bucket for j in kept)
    log.info("fetched %s, passed filters %s, ambiguous→AI %s, kept %s (apply %s, check %s, long shots %s)",
             len(fetched), len(passed), len(ambiguous), len(kept), tiers["apply"], tiers["check"], tiers["longshot"])
    for reason, count in drop_counts.most_common():
        log.info("  dropped %5d  %s", count, reason)

    if dry_run:
        store.commit()  # keep LLM cache and logs; nothing is marked as reported
        for job in kept[:40]:
            log.info("  %-5s %-26s %-55s %s", job.bucket, job.company[:26], job.title[:55], job.screen.get("decided_by"))
        return None

    jobs_dir = ROOT / cfg["output"]["jobs_dir"]
    jobs_dir.mkdir(parents=True, exist_ok=True)
    report = jobs_dir / f"{today}.md"
    write_report(report, local_now, kept, stats, drop_counts, cfg["candidate"]["country"])
    write_drop_log(ROOT / cfg["output"]["state_dir"] / "dropped" / f"{today}.tsv", dropped)
    for job in kept:
        store.mark_reported(job, today)
        store.log_decision(today, job.id, "report", job.screen.get("decided_by", ""), job.bucket,
                           job.screen.get("why", ""))
    store.log_run(now.isoformat(), len(fetched), len(passed), len(kept), len(kept),
                  str(report.relative_to(ROOT)), 0)
    store.commit()
    log.info("wrote %s", report)
    return report


def audit(store: Store, today: str, rule_decided: list, llm_cfg: dict, screener: Screener | None) -> None:
    """Ask the LLM about a random sample of rule decisions and record whether it agrees."""
    if screener is None:
        screener = Screener(llm_cfg, ROOT / llm_cfg["prompt"], store.db,
                            log_call=lambda *a: store.log_llm_call(today, *a))
    sample = random.sample(rule_decided, min(llm_cfg["audit_per_run"], len(rule_decided)))
    results = screener.screen([(job, v.snippets or [v.evidence]) for job, v in sample], purpose="audit")
    for job, v in sample:
        if job.id not in results:
            continue
        r = results[job.id][0]
        llm = r["eligibility"]
        if llm == "unclear":
            agree = None
        elif v.outcome == LOCKED:
            agree = llm in NEGATIVE
        else:
            agree = llm in POSITIVE | {"timezone_only"}
        store.log_audit(today, job.id, v.rule, v.outcome, llm, agree, v.evidence, r["eligibility_evidence"])
    store.commit()


def feedback(config_path: Path, ref: str, label: str, note: str) -> None:
    cfg, store = load(config_path)
    found = store.find_ref(ref)
    if label not in FEEDBACK_LABELS:
        log.warning("unusual label %r; known labels: %s", label, ", ".join(FEEDBACK_LABELS))
    store.add_feedback(datetime.now(timezone.utc).isoformat(), ref, found[0] if found else None, label, note)
    store.commit()
    print(f"saved: {ref} = {label}" + (f" ({found[1]})" if found else " (ref not found in reported jobs; kept anyway)"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="scanner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch, filter and write today's job list")
    r.add_argument("--dry-run", action="store_true", help="print results; nothing is marked as reported")
    r.add_argument("--days", type=int, help="override the fetch window")
    r.add_argument("--skip-llm", action="store_true", help="rules only: ambiguous jobs go to Check")
    f = sub.add_parser("feedback", help="label a reported job by its Ref",
                       description="labels: " + "; ".join(f"{k} = {v}" for k, v in FEEDBACK_LABELS.items()))
    f.add_argument("ref")
    f.add_argument("label")
    f.add_argument("note", nargs="*")
    lr = sub.add_parser("learn", help="write the self-improvement report from the logs")
    lr.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.cmd == "run":
        run(args.config, args.dry_run, args.days, args.skip_llm)
    elif args.cmd == "feedback":
        feedback(args.config, args.ref, args.label, " ".join(args.note))
    elif args.cmd == "learn":
        from scanner.learn import learn

        cfg, store = load(args.config)
        print(learn(cfg, store, ROOT, args.days))
