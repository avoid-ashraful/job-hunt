"""Stage 2: LLM reads each surviving posting and judges location eligibility and role fit.

The model fills in facts (with a verbatim quote for location); the final
apply / check / drop decision is made here in code, so it stays consistent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

from scanner.models import Job

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "eligibility": {
            "type": "string",
            "enum": ["anywhere", "includes_candidate", "timezone_only", "unclear",
                     "region_locked", "work_authorization_required"],
        },
        "eligibility_evidence": {"type": "string"},
        "allowed_locations": {"type": "string", "description": "short, e.g. 'worldwide', 'US only', 'EU + UK'"},
        "hiring_model": {"type": "string", "enum": ["employee", "eor", "contractor", "unknown"]},
        "primary_language": {"type": "string"},
        "python_role": {"type": "boolean"},
        "role_type": {
            "type": "string",
            "enum": ["backend", "backend_heavy_fullstack", "frontend_heavy_fullstack", "applied_ai",
                     "ml_research", "data", "devops", "other"],
        },
        "seniority": {"type": "string", "enum": ["junior", "mid", "senior", "staff_plus", "lead", "unknown"]},
        "people_manager": {"type": "boolean"},
        "agency_or_outsourcing": {"type": "boolean"},
        "english_working_language": {"type": "boolean"},
        "fit_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "fit_reason": {"type": "string", "description": "at most 25 words"},
    },
    "required": ["eligibility", "eligibility_evidence", "allowed_locations", "hiring_model", "primary_language",
                 "python_role", "role_type", "seniority", "people_manager", "agency_or_outsourcing",
                 "english_working_language", "fit_score", "fit_reason"],
}

ELIGIBLE = {"anywhere", "includes_candidate", "timezone_only"}
DROP_ROLE_TYPES = {"frontend_heavy_fullstack", "ml_research", "data", "devops", "other"}
MAX_DESCRIPTION_CHARS = 20_000

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_screen (
    job_id TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, prompt_hash)
);
"""

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"job_ref": {"type": "string"}, **SCHEMA["properties"]},
                "required": ["job_ref", *SCHEMA["required"]],
            },
        }
    },
    "required": ["results"],
}

BATCH_NOTE = (
    "\n\n## Batch format\nYou will receive several postings, each starting with `### JOB <ref>`. "
    "Judge each one independently, using only its own text, and return one entry per posting in "
    "`results`, with `job_ref` set to its ref."
)


class QuotaExhausted(Exception):
    """This model is out of quota for a long time; move on to the next one."""


POSITIVE = {"anywhere", "includes_candidate"}


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.lower().split())


def check_evidence(job: Job, r: dict) -> dict:
    """Location verdicts must rest on the description body, not on tags or the title.

    A region lock or "anywhere" whose quote is empty, or only repeats the title or
    location tag, is downgraded to "unclear" so the job lands in Check instead of
    being dropped (or trusted) on metadata alone.
    """
    if r["eligibility"] not in POSITIVE | {"region_locked"}:
        return r
    ev = _norm(r.get("eligibility_evidence") or "").strip("\"' .")
    meta = [_norm(x) for x in (job.title, job.location, *job.regions, *job.countries) if x]
    from_meta = not ev or ev.startswith(("location tag", "title:", "region tags", "country tags")) or any(
        ev == m or ev in m for m in meta
    )
    body = _norm(job.description)
    quoted = all(part.strip(" .,;") in body for part in re.split(r"\.\.\.|…", ev) if part.strip(" .,;"))
    if from_meta or not quoted:
        note = f" [model said {r['eligibility']}, but the quote isn't from the description]"
        return {**r, "eligibility": "unclear", "fit_reason": r["fit_reason"] + note}
    return r


def decide(r: dict) -> tuple[str, str]:
    """Map the model's facts to (bucket, reason). Bucket is apply / check / drop."""
    if r["eligibility"] == "work_authorization_required":
        return "drop", f"needs work authorization ({r['allowed_locations']})"
    if r["eligibility"] == "region_locked":
        return "drop", f"region locked ({r['allowed_locations']})"
    if not r["python_role"]:
        return "drop", f"not a Python role ({r['primary_language']})"
    if r["role_type"] in DROP_ROLE_TYPES:
        return "drop", f"role type {r['role_type']}"
    if r["people_manager"]:
        return "drop", "people-manager role"
    if r["agency_or_outsourcing"]:
        return "drop", "agency / outsourcing employer"
    if not r["english_working_language"]:
        return "drop", "working language is not English"
    if r["seniority"] == "junior":
        return "drop", "junior role"
    if r["fit_score"] <= 1:
        return "drop", "poor fit"
    if r["eligibility"] in ELIGIBLE and r["fit_score"] >= 3:
        return "apply", r["eligibility"]
    return "check", "location unclear" if r["eligibility"] == "unclear" else "low fit"


class GeminiClient:
    def __init__(self, model: str, api_key: str, system: str):
        self.name = f"gemini:{model}"
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        self.api_key = api_key
        self.system = system
        self.session = requests.Session()

    def complete(self, user: str, retries: int = 5) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": self.system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": BATCH_SCHEMA},
        }
        for attempt in range(retries):
            resp = self.session.post(self.url, json=body, headers={"X-goog-api-key": self.api_key}, timeout=300)
            if resp.status_code == 429:
                msg = resp.text
                # daily quotas say "retry in 2h38m"; per-minute ones say "retry in 41s"
                if re.search(r"retry in \d+h", msg, re.I) or "per_day" in msg.lower() or "PerDay" in msg:
                    raise QuotaExhausted(self.name)
                wait = _retry_seconds(msg) or min(60, 2 ** (attempt + 3))
                log.warning("%s rate limited, waiting %ss", self.name, wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(min(60, 2 ** (attempt + 2)))
                continue
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        raise QuotaExhausted(f"{self.name} kept failing")


class ClaudeCliClient:
    """Uses the logged-in Claude Code CLI (Claude subscription), no API key."""

    def __init__(self, model: str, system: str):
        self.name = f"claude:{model}"
        self.model = model
        self.system = system

    def complete(self, user: str) -> dict:
        cmd = ["claude", "-p", "--model", self.model, "--output-format", "json", "--tools", "",
               "--system-prompt", self.system, "--json-schema", json.dumps(BATCH_SCHEMA)]
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            if "limit" in (proc.stdout + proc.stderr).lower():
                raise QuotaExhausted(self.name)
            raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[-300:]}")
        out = json.loads(proc.stdout)
        if out.get("is_error"):
            raise QuotaExhausted(f"{self.name}: {str(out.get('result'))[:200]}")
        return out.get("structured_output") or json.loads(out["result"])


def _retry_seconds(msg: str) -> int | None:
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg, re.I)
    return int(float(m.group(1))) + 1 if m else None


class Screener:
    """Screens jobs in batches, walking down a chain of models as quotas run out."""

    def __init__(self, cfg: dict, prompt_path: Path, db: sqlite3.Connection):
        self.batch_size = cfg.get("batch_size", 8)
        self.cap = cfg.get("call_cap", 300)
        self.workers = cfg.get("workers", 3)
        self.lock = threading.Lock()
        system = prompt_path.read_text() + BATCH_NOTE
        self.prompt_hash = hashlib.sha256((system + json.dumps(BATCH_SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]
        self.clients = []
        for entry in cfg["chain"]:
            if entry["provider"] == "gemini":
                key = os.environ.get("GEMINI_API_KEY")
                if key:
                    self.clients.append(GeminiClient(entry["model"], key, system))
            elif entry["provider"] == "claude-cli":
                self.clients.append(ClaudeCliClient(entry["model"], system))
        if not self.clients:
            raise RuntimeError("no usable LLM in llm.chain (missing GEMINI_API_KEY?)")
        self.db = db
        self.db.executescript(CACHE_SCHEMA)

    def _cached(self, job: Job) -> dict | None:
        row = self.db.execute(
            "SELECT result FROM llm_screen WHERE job_id = ? AND prompt_hash = ?", (job.id, self.prompt_hash)
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _job_text(ref: str, job: Job) -> str:
        return (
            f"### JOB {ref}\nTitle: {job.title}\nCompany: {job.company}\n"
            f"Location tag: {job.location or '-'}\nRegion tags: {', '.join(job.regions) or '-'}\n"
            f"Country tags: {', '.join(job.countries) or '-'}\n\n"
            f"{job.description[:MAX_DESCRIPTION_CHARS]}\n"
        )

    def _screen_batch(self, batch: list[Job]) -> tuple[dict[str, dict], str]:
        refs = {f"J{i + 1}": job for i, job in enumerate(batch)}
        user = "\n".join(self._job_text(ref, job) for ref, job in refs.items())
        while True:
            with self.lock:
                if not self.clients:
                    raise QuotaExhausted("every model in llm.chain is out of quota")
                client = self.clients[0]
            try:
                data = client.complete(user)
            except QuotaExhausted as exc:
                with self.lock:
                    if self.clients and self.clients[0] is client:
                        log.warning("llm: %s exhausted (%s), switching to next model", client.name, exc)
                        self.clients.pop(0)
                continue
            out = {}
            for item in data.get("results", []):
                job = refs.get(item.pop("job_ref", ""))
                if job and all(k in item for k in SCHEMA["required"]):
                    out[job.id] = item
            return out, client.name

    def screen(self, jobs: list[Job]) -> tuple[dict[str, dict], list[Job]]:
        """Return ({job_id: result}, deferred_jobs). Results are cached per prompt version."""
        results, todo = {}, []
        for job in jobs:
            if (cached := self._cached(job)) is not None:
                results[job.id] = cached
            else:
                todo.append(job)
        batches = [todo[i : i + self.batch_size] for i in range(0, len(todo), self.batch_size)]
        log.info("llm: %s cached, %s to screen in %s batches", len(results), len(todo), len(batches))

        deferred: list[Job] = [job for batch in batches[self.cap :] for job in batch]
        batches = batches[: self.cap]
        done = 0
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(self._screen_batch, batch): batch for batch in batches}
            for fut in as_completed(futures):
                batch = futures[fut]
                try:
                    out, model = fut.result()
                except Exception as exc:  # quota gone or a bad response: retry these next run
                    log.error("llm: batch failed: %s", exc)
                    deferred.extend(batch)
                    continue
                now = datetime.now(timezone.utc).isoformat()
                for job in batch:
                    if job.id in out:
                        results[job.id] = out[job.id]
                        self.db.execute("INSERT OR REPLACE INTO llm_screen VALUES (?, ?, ?, ?, ?)",
                                        (job.id, self.prompt_hash, model, json.dumps(out[job.id]), now))
                    else:
                        deferred.append(job)
                self.db.commit()
                done += 1
                log.info("llm: batch %s/%s done with %s", done, len(batches), model)
        return results, deferred
