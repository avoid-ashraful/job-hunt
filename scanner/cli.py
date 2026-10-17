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
import tempfile
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scanner.applications import Applications, STATUSES
from scanner.apply_evidence import ApplyEvidence
from scanner.marks import sync as sync_marks
from scanner.companies import load_region_map
from scanner.company_profile import CompanyProfiles
from scanner import liveness
from scanner.dedup import collapse_by_seniority, description_fingerprint, fingerprint, merge_jobs
from scanner.eligibility import NEGATIVE, POSITIVE, Screener, check_evidence
from scanner.filters import Filters
from scanner.ghost import GhostRules
from scanner.models import Job
from scanner.pay_market import DEFAULT_ALLOW, levels_tiers, salary_usd
from scanner.report import reason_bucket, write_drop_log, write_report
from scanner.salary import fill_salary
from scanner.rules import AMBIGUOUS, LOCKED, SILENT, LocationRules, RoleRules
from scanner import sources
from scanner.store import Store
from scanner import board_health, seed_sources, watchlist

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


def _hq_lookup(cfg: dict, db_path: Path):
    """Read known HQ profiles and checked overrides when the profile module is installed."""
    try:
        from scanner.company_profile import hq_country
    except ImportError:
        return None
    profiles = {}
    if db_path.exists():
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_profiles'").fetchone():
                profiles = dict(db.execute("SELECT company_key, hq_country FROM company_profiles WHERE hq_country IS NOT NULL"))
        finally:
            db.close()
    market_cfg = cfg.get("filters", {}).get("company_market", {})

    def lookup(name: str) -> str | None:
        key = watchlist.normalize_company(name)
        if key in profiles:
            return profiles[key]
        stub = Job(id="", source="watchlist", url="", title="", company=name,
                   company_key=key, description="")
        country, _ = hq_country(stub, market_cfg)
        return country

    return lookup


# Enrichment a later source may know when the first copy (e.g. the live employer board) doesn't.
FILLABLE = ("regions", "countries", "skills", "posted_at", "first_seen_at", "seniority", "category", "employment_type",
            "company_type", "domains", "posting_language", "summary", "salary_min", "salary_max", "salary_currency",
            "salary_period", "requires_clearance", "reality")


def _fill_missing(first: Job, other: Job) -> None:
    """The first source's copy wins; empty fields are filled from another copy of the same job."""
    for name in FILLABLE:
        if getattr(first, name) in (None, "", [], {}) and getattr(other, name) not in (None, "", [], {}):
            setattr(first, name, getattr(other, name))
    first.watchlist = first.watchlist or other.watchlist


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
    sync_marks(store, ROOT / cfg["output"]["jobs_dir"])
    apps = Applications(store)
    apps.ghost_overdue(cfg.get("applications", {}).get("ghosted_after_days", 21))
    store.commit()
    store.dry_run = dry_run
    now = datetime.now(timezone.utc)
    local_now = datetime.now(ZoneInfo(cfg["candidate"].get("timezone", "UTC")))
    today = local_now.date().isoformat()
    dropped: list[tuple[Job, str]] = []

    def drop(job: Job, stage: str, rule: str, reason: str) -> None:
        dropped.append((job, reason))
        store.log_decision(today, job.id, stage, rule, "drop", reason)

    # 1. fetch and merge copies before filtering so employer text and board enrichment survive
    src = cfg["sources"]["freehire"]
    if days is None:
        days = src["first_run_days"] if store.is_empty() else src["daily_lookback_days"]
    log.info("fetching jobs first seen in the last %s days", days)
    fetched_jobs: list[Job] = []
    source_cfg = dict(cfg["sources"])
    watch_cfg = cfg.get("sources", {}).get("ats", {})
    watch_path = ROOT / watch_cfg.get("watchlist_file", "private/config/watchlist.yml")
    if "ats" in source_cfg:
        source_cfg["ats"] = {**source_cfg["ats"], "filters": cfg["filters"], "watchlist_file": str(watch_path),
                             "health_dir": str(ROOT / cfg["output"]["state_dir"] / "watchlist_health")}
    denied = {str(c).lower() for c in watch_cfg.get("hq_deny_countries", ["in", "bd"])}
    watched = [c for c in watchlist.list_entries(watch_path) if c.get("status") != "inactive" and str(c.get("hq_country") or "").lower() not in denied]
    watched_keys = {watchlist.normalize_company(c["company"]) for c in watched}
    watched_boards = {(c.get("ats"), c.get("board")) for c in watched}
    source_stats = []
    run_cfg = cfg.get("run", {})
    for job in sources.fetch_all(source_cfg, days, source_stats,
                                 run_cfg.get("fetch_budget_minutes", 10),
                                 run_cfg.get("fetch_workers", 4)):
        if job.company_key in watched_keys or tuple(job.id.split(":", 2)[:2]) in watched_boards:
            job.watchlist = True
        fetched_jobs.append(job)
    fetched = merge_jobs(fetched_jobs, _fill_missing)
    for job in fetched.values():
        fill_salary(job)
        store.upsert(job, today)

    # 2. hard filters: title, stack, age, salary, company, hard drops
    market_cfg = cfg["filters"].get("company_market", {})
    profiles = CompanyProfiles(store.db, market_cfg,
                               ROOT / cfg["candidate"].get("company_priors", "vendor/remote-jobs/src/companies"))
    filters = Filters(cfg["filters"], cfg["dedup"]["seniority_rank"], now, profiles)
    for job in fetched.values():
        profiles.resolve(job)
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
    ghosts = GhostRules(ghost_cfg, lambda j: store.role_history(j, ghost_cfg.get("repost_gap_days", 7)),
                        apps.silent_count)
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
    apply_evidence = ApplyEvidence(cfg["rules"].get("apply_evidence", {}))
    evidence_demotions = 0
    for job in eligible:
        ghost_score, signals = job.screen["ghost"]
        if ghost_score >= ghosts.flag_at:
            job.bucket = "check"
            job.screen["why"] = "eligible, but may be a ghost posting"
        elif job.screen["fit"] >= min_fit:
            if reason := apply_evidence.weak_reason(job):
                job.bucket = "check"
                job.screen["why"] = f"eligible, but evidence is weak: {reason}"
                evidence_demotions += 1
                store.log_decision(today, job.id, "apply_evidence", "weak_evidence", "check", job.screen["why"])
            else:
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
    if kept:
        status = (liveness.check_all(kept, cfg.get("liveness", {}).get("workers", 8))
                  if cfg.get("liveness", {}).get("enabled", True) else {})
        open_jobs = []
        for job in kept:
            if job.screen.get("needs_live") and status.get(job.id) != liveness.OPEN:
                drop(job, "liveness", "not_confirmed_open",
                     f"older than {filters.max_age.days} days and not confirmed open")
            elif status.get(job.id) == liveness.CLOSED:
                drop(job, "liveness", "closed", "closed: the posting is gone from the employer's site")
            else:
                open_jobs.append(job)
        kept = open_jobs

    profiles.enrich_report(kept)
    denied_hq = set(market_cfg.get("deny_hq_countries", ["in", "bd"]))
    report_jobs = []
    for job in kept:
        if job.screen.get("hq_country") in denied_hq:
            drop(job, "filter", "company_market", f"company based in {job.screen['hq_country'].upper()}")
        else:
            report_jobs.append(job)
    kept = report_jobs

    drop_counts = Counter(reason_bucket(r) for _, r in dropped)
    stats = {"fetched": len(fetched), "passed": len(passed), "ambiguous": len(ambiguous),
             "llm_calls": screener.calls if screener else 0, "sources": source_stats,
             "apply_evidence_demotions": evidence_demotions}
    tiers = Counter(j.bucket for j in kept)
    pay_tiers = levels_tiers(ROOT / cfg["output"]["state_dir"] / "levels")
    allow = set(market_cfg.get("allow_hq_countries", DEFAULT_ALLOW))
    for job in kept:
        job.screen["preferred_hq"] = job.screen.get("hq_country") in allow
        job.screen["pay_hint"] = salary_usd(job, cfg["filters"]["salary"])
        job.screen["pay_tier"] = pay_tiers.get(job.company_key, "")
    log.info("fetched %s, passed filters %s, ambiguous→AI %s, kept %s (apply %s, check %s, long shots %s)",
             len(fetched), len(passed), len(ambiguous), len(kept), tiers["apply"], tiers["check"], tiers["longshot"])
    for reason, count in drop_counts.most_common():
        log.info("  dropped %5d  %s", count, reason)
    log.info("Apply evidence guard demoted %s jobs to Check", evidence_demotions)

    if dry_run:
        store.commit()  # keep LLM cache and logs; nothing is marked as reported
        with tempfile.TemporaryDirectory() as temp_dir:
            preview = Path(temp_dir) / "preview.md"
            write_report(preview, local_now, kept, stats, drop_counts, cfg["candidate"]["country"],
                         cfg.get("report", {}).get("start_here_limit", 15),
                         cfg.get("report", {}).get("check_top", 25))
            print("\n".join(preview.read_text().splitlines()[:35]))
        return None

    jobs_dir = ROOT / cfg["output"]["jobs_dir"]
    jobs_dir.mkdir(parents=True, exist_ok=True)
    report = jobs_dir / f"{today}.md"
    write_report(report, local_now, kept, stats, drop_counts, cfg["candidate"]["country"],
                 cfg.get("report", {}).get("start_here_limit", 15),
                 cfg.get("report", {}).get("check_top", 25))
    write_drop_log(ROOT / cfg["output"]["state_dir"] / "dropped" / f"{today}.tsv", dropped)
    for job in kept:
        store.mark_reported(job, today)
        store.log_decision(today, job.id, "report", job.screen.get("decided_by", ""), job.bucket,
                           job.screen.get("why", ""))
    store.log_run(now.isoformat(), len(fetched), len(passed), len(kept), len(kept),
                  str(report.relative_to(ROOT)), 0, source_stats)
    store.commit()
    if watch_cfg.get("enabled"):
        stats_grow = watchlist.grow(store.db, watch_path, deny_countries=tuple(denied), cfg=cfg)
        log.info("watchlist grew by %s companies (%s total)", stats_grow["added"], stats_grow["total"])
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
    if label == "applied" and found:
        Applications(store).set_status(ref, "applied", note)
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
    sub.add_parser("sync", help="read marks from all daily reports")
    ap = sub.add_parser("applied", help="record an application by Ref")
    ap.add_argument("ref")
    ap.add_argument("note", nargs="*")
    app = sub.add_parser("app", help="change an application status")
    app.add_argument("ref")
    app.add_argument("status", choices=sorted(STATUSES))
    app.add_argument("note", nargs="*")
    sub.add_parser("status", help="show application status and response rate")
    lr = sub.add_parser("learn", help="write the self-improvement report from the logs")
    lr.add_argument("--days", type=int, default=7)
    wl = sub.add_parser("watchlist", help="build, grow or list direct ATS company boards")
    wl.add_argument("action", choices=["build", "grow", "list", "seeds"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.cmd == "run":
        run(args.config, args.dry_run, args.days, args.skip_llm)
    elif args.cmd == "feedback":
        feedback(args.config, args.ref, args.label, " ".join(args.note))
    elif args.cmd in {"sync", "applied", "app", "status", "learn"}:
        cfg, store = load(args.config)
        saved, parsed = sync_marks(store, ROOT / cfg["output"]["jobs_dir"])
        apps = Applications(store)
        apps.ghost_overdue(cfg.get("applications", {}).get("ghosted_after_days", 21))
        if args.cmd == "sync":
            print(f"Synced {saved} marks from {parsed} marked or blank cells")
        elif args.cmd in {"applied", "app"}:
            status = "applied" if args.cmd == "applied" else args.status
            try:
                found = apps.set_status(args.ref, status, " ".join(args.note))
            except ValueError as exc:
                parser.error(str(exc))
            if not found:
                parser.error(f"unknown Ref: {args.ref}")
            print(f"{args.ref}: {status}")
        elif args.cmd == "status":
            print(apps.summary())
        else:
            from scanner.learn import learn
            print(learn(cfg, store, ROOT, args.days))
        store.commit()
    elif args.cmd == "watchlist":
        cfg = yaml.safe_load(args.config.read_text())
        ats_cfg = cfg.get("sources", {}).get("ats", {})
        path = ROOT / ats_cfg.get("watchlist_file", "private/config/watchlist.yml")
        denied = tuple(str(c).lower() for c in ats_cfg.get("hq_deny_countries", ["in", "bd"]))
        if args.action == "seeds":
            print(seed_sources.update_seeds(ROOT / "watchlist.seeds.yml", ROOT / "watchlist.seeds.auto.yml", cfg))
        elif args.action == "build":
            vendor = ROOT / cfg["candidate"].get("company_priors", "vendor/remote-jobs/src/companies")
            cache = ROOT / cfg["output"]["state_dir"] / "watchlist_resolution"
            db_path = ROOT / cfg["output"]["state_dir"] / "scanner.sqlite"
            with seed_sources.build_seeds(ROOT / "watchlist.seeds.yml", ROOT / "watchlist.seeds.auto.yml") as seeds:
                print(watchlist.build(vendor, path, cache, workers=ats_cfg.get("build_workers", 8), seeds_file=seeds,
                                      deny_countries=denied, hq_lookup=_hq_lookup(cfg, db_path), db_path=db_path, cfg=cfg,
                                      max_seconds=60 * ats_cfg.get("build_max_minutes", 20)))
        elif args.action == "grow":
            _, store = load(args.config)
            print(watchlist.grow(store.db, path, deny_countries=denied, cfg=cfg))
        else:
            health_dir = ROOT / cfg["output"]["state_dir"] / "watchlist_health"
            print("Company\tATS/board\tHQ\tHires\tStatus\tLast ok\tJobs\tFailures (404/410 / other)")
            for company in watchlist.list_entries(path):
                board = company.get("previous_board") if company.get("status") == "inactive" else None
                board = board or [company.get("ats", ""), company.get("board", "")]
                health = board_health.read(health_dir, *board)
                print(f"{company['company']}\t{company.get('ats', '-')}/{company.get('board', '-')}\t{company.get('hq_country', '')}\t{company.get('hires', '')}\t{company.get('status', 'active')}"
                      f"\t{health.get('last_ok', '-')}\t{health.get('jobs', '-')}"
                      f"\t{health.get('not_found_failures', 0)}/{health.get('other_failures', 0)}")
