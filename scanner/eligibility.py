"""LLM fallback for location eligibility, used only when the rules are stuck.

The model sees the title, the source's location tags and the location
sentences the rules extracted, not the whole description. It returns one
eligibility label with a verbatim quote. The same call is used to spot-check a
sample of rule decisions (audits), which is how rule accuracy is measured.
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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

from scanner.models import Job
from scanner.rules import normalize_text

log = logging.getLogger(__name__)

ELIGIBILITY = ["anywhere", "includes_candidate", "timezone_only", "unclear", "region_locked",
               "work_authorization_required"]
SCHEMA = {
    "type": "object",
    "properties": {
        "eligibility": {"type": "string", "enum": ELIGIBILITY},
        "eligibility_evidence": {"type": "string"},
        "allowed_locations": {"type": "string", "description": "short, e.g. 'worldwide', 'US only', 'EU + UK'"},
    },
    "required": ["eligibility", "eligibility_evidence", "allowed_locations"],
}
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
    "\n\n## Batch format\nYou will receive several postings, each starting with `### JOB <ref>`, followed by "
    "its title, tags and the sentences from its description that mention location. Judge each one "
    "independently and return one entry per posting in `results`, with `job_ref` set to its ref."
    " Snippets include eligibility requirements and location lists as well as geographic sentences."
    "\n\n## Evidence safeguards\n"
    "A bare 'work from anywhere' perk is unclear unless the text establishes worldwide hiring. "
    "Existing employees' locations and optional office locations do not establish hiring restrictions. "
    "A partial country list introduced by 'including' does not prove an unlisted country is excluded. "
    "A location label alone is unclear, even when repeated in the description. "
    "No visa sponsorship or conditional export/security checks alone do not prove that foreign work "
    "authorization is required. Quote an explicit requirement for work_authorization_required. "
    "Role-specific residence and authorization requirements take precedence over global-team slogans "
    "and working-hours flexibility. 'Based between' geographic endpoints restricts residence; "
    "working hours or overlap alone does not. When the supplied evidence conflicts, use unclear."
)
CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_location (
    job_id TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, prompt_hash)
);
"""
POSITIVE = {"anywhere", "includes_candidate"}
# "work from anywhere" alone is usually a perk or a home-office note, not proof they hire in every country
BARE_ANYWHERE = re.compile(r"\b(work|working|live|based)\b.{0,25}\banywhere\b")
WORLDWIDE = re.compile(r"world|any country|countries|global|international|wherever you (are|live)")
NEGATIVE = {"region_locked", "work_authorization_required"}


class QuotaExhausted(Exception):
    """This model is out of quota for a long time; move on to the next one."""


def _norm(text: str) -> str:
    text = normalize_text(text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.lower().split())


def check_evidence(job: Job, r: dict) -> dict:
    """Positive and negative verdicts must quote the description body, not the title or tags.

    Otherwise it is downgraded to "unclear", so the job lands in Check instead of
    being dropped (or trusted) on metadata alone.
    """
    if r["eligibility"] not in POSITIVE | NEGATIVE:
        return r
    ev = _norm(r.get("eligibility_evidence") or "").strip("\"' .")
    meta = [_norm(x) for x in (job.title, job.location, *job.regions, *job.countries) if x]
    from_meta = not ev or ev.startswith(("location tag", "title:", "region tags", "country tags")) or any(
        ev == m or ev in m for m in meta
    )
    # A copied location header is still a label, even when it appears in the body.
    location_header = re.match(r"^\W*location(?:\(s\))?[*_\s]*[:\-–]", ev)
    hiring_statement = re.search(r"\b(?:role|position|candidates?|applicants?|must|hiring|hire)\b", ev)
    from_meta = from_meta or bool(location_header and not hiring_statement)
    body = _norm(job.description)
    parts = [part.strip(" .,;") for part in re.split(r"\.\.\.|…", ev) if part.strip(" .,;")]
    quoted = bool(parts) and all(part in body for part in parts)
    if from_meta or not quoted:
        return {**r, "eligibility": "unclear",
                "allowed_locations": f"{r['allowed_locations']} (model said {r['eligibility']}, no quote from the text)"}
    if r["eligibility"] == "anywhere" and BARE_ANYWHERE.search(ev) and not WORLDWIDE.search(ev):
        return {**r, "eligibility": "unclear",
                "allowed_locations": f"{r['allowed_locations']} (model said anywhere, but only from a bare 'work from anywhere')"}
    return r


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


class OpenAICompatClient:
    """Any OpenAI-compatible endpoint (e.g. OrcaRouter's free models). The key is read from a file."""

    def __init__(self, name: str, base_url: str, model: str, api_key: str, system: str):
        self.name = f"{name}:{model}"
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        # json_object mode has no schema parameter, so the schema goes in the system prompt
        self.system = system + "\n\nReturn a JSON object matching this JSON Schema:\n" + json.dumps(BATCH_SCHEMA)
        self.session = requests.Session()

    def complete(self, user: str, retries: int = 5) -> dict:
        body = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": user}]}
        for attempt in range(retries):
            try:
                resp = self.session.post(self.url, json=body, timeout=180,
                                         headers={"Authorization": f"Bearer {self.api_key}"})
            except (requests.Timeout, requests.ConnectionError) as exc:  # free endpoints hang now and then
                if attempt >= 1:
                    raise QuotaExhausted(f"{self.name}: {type(exc).__name__} twice") from exc
                continue
            if resp.status_code in (401, 402, 403, 404, 410):
                raise QuotaExhausted(f"{self.name}: HTTP {resp.status_code} {resp.text[:120]}")
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = resp.headers.get("Retry-After", "")
                if wait.isdigit() and int(wait) > 120:  # an hours-long quota window: move on now
                    raise QuotaExhausted(f"{self.name}: HTTP {resp.status_code}, retry after {wait}s")
                if attempt == retries - 1:
                    raise QuotaExhausted(f"{self.name}: HTTP {resp.status_code} after {retries} tries")
                time.sleep(_retry_seconds(resp.text) or min(60, 2 ** (attempt + 2)))
                continue
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"] or ""
            return _parse_json(text)
        raise QuotaExhausted(f"{self.name} kept failing")


def _parse_json(text: str) -> dict:
    """Models without schema enforcement sometimes wrap JSON in prose or code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start : end + 1])


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
    """Location-only LLM calls in batches, walking down a chain of models as quotas run out."""

    def __init__(self, cfg: dict, prompt_path: Path, db: sqlite3.Connection,
                 log_call: Callable[[str, str, int, bool, float, int], None] | None = None):
        self.batch_size = cfg.get("batch_size", 20)
        self.cap = cfg.get("call_cap", 50)
        self.workers = cfg.get("workers", 3)
        self.lock = threading.Lock()
        self.log_call = log_call or (lambda *a: None)
        self._pending_calls: list[tuple] = []  # written by the main thread: sqlite isn't thread-safe
        system = prompt_path.read_text() + BATCH_NOTE
        self.prompt_hash = hashlib.sha256((system + json.dumps(BATCH_SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]
        self.clients = []
        for entry in cfg["chain"]:
            if entry["provider"] == "gemini":
                if key := os.environ.get("GEMINI_API_KEY"):
                    self.clients.append(GeminiClient(entry["model"], key, system))
            elif entry["provider"] == "claude-cli":
                self.clients.append(ClaudeCliClient(entry["model"], system))
            elif entry["provider"] == "openai-compatible":
                key_file = Path(entry["api_key_file"]).expanduser()
                if key_file.exists():
                    self.clients.append(OpenAICompatClient(entry.get("name", "openai"), entry["base_url"],
                                                           entry["model"], key_file.read_text().strip(), system))
                else:
                    log.warning("llm: %s skipped, key file %s not found", entry.get("name"), key_file)
        if not self.clients:
            raise RuntimeError("no usable LLM in llm.chain (missing GEMINI_API_KEY?)")
        self.db = db
        self.db.executescript(CACHE_SCHEMA)
        self.calls = 0

    def _cached(self, job: Job) -> dict | None:
        row = self.db.execute(
            "SELECT result FROM llm_location WHERE job_id = ? AND prompt_hash = ?", (job.id, self.prompt_hash)
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _job_text(ref: str, job: Job, snippets: list[str]) -> str:
        body = "\n".join(f"- {s}" for s in snippets) or "- (no sentence in the description mentions location)"
        return (
            f"### JOB {ref}\nTitle: {job.title}\nCompany: {job.company}\n"
            f"Location tag: {job.location or '-'}\nRegion tags: {', '.join(job.regions) or '-'}\n"
            f"Location sentences from the description:\n{body}\n"
        )

    def _batch(self, batch: list[tuple[Job, list[str]]], purpose: str) -> tuple[dict[str, dict], str]:
        refs = {f"J{i + 1}": item for i, item in enumerate(batch)}
        user = "\n".join(self._job_text(ref, job, snippets) for ref, (job, snippets) in refs.items())
        while True:
            with self.lock:
                if not self.clients:
                    raise QuotaExhausted("every model in llm.chain is out of quota")
                if self.calls >= self.cap:
                    raise QuotaExhausted(f"call cap {self.cap} reached")
                self.calls += 1
                client = self.clients[0]
            started = time.monotonic()
            try:
                data = client.complete(user)
            except QuotaExhausted as exc:
                with self.lock:
                    self._pending_calls.append((client.name, purpose, len(batch), False, time.monotonic() - started, len(user)))
                    if self.clients and self.clients[0] is client:
                        log.warning("llm: %s exhausted (%s), switching to next model", client.name, exc)
                        self.clients.pop(0)
                continue
            with self.lock:
                self._pending_calls.append((client.name, purpose, len(batch), True, time.monotonic() - started, len(user)))
            out = {}
            for item in data.get("results", []):
                job_snip = refs.get(item.pop("job_ref", ""))
                if job_snip and all(k in item for k in SCHEMA["required"]) and item["eligibility"] in ELIGIBILITY:
                    out[job_snip[0].id] = item
            return out, client.name

    def screen(self, items: list[tuple[Job, list[str]]], purpose: str = "screen") -> dict[str, tuple[dict, str]]:
        """Return {job_id: (result, model)}. Jobs missing from the result couldn't be screened."""
        results: dict[str, tuple[dict, str]] = {}
        todo = []
        for job, snippets in items:
            if (cached := self._cached(job)) is not None:
                results[job.id] = (cached, "cache")
            else:
                todo.append((job, snippets))
        batches = [todo[i : i + self.batch_size] for i in range(0, len(todo), self.batch_size)]
        if batches:
            log.info("llm %s: %s cached, %s to screen in %s batches", purpose, len(results), len(todo), len(batches))
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(self._batch, batch, purpose): batch for batch in batches}
            for fut in as_completed(futures):
                self._flush_calls()
                try:
                    out, model = fut.result()
                except Exception as exc:  # quota gone or a bad response: those jobs go to Check
                    log.error("llm %s: batch failed: %s", purpose, exc)
                    continue
                now = datetime.now(timezone.utc).isoformat()
                for job_id, result in out.items():
                    results[job_id] = (result, model)
                    self.db.execute("INSERT OR REPLACE INTO llm_location VALUES (?, ?, ?, ?, ?)",
                                    (job_id, self.prompt_hash, model, json.dumps(result), now))
                self.db.commit()
        self._flush_calls()
        return results

    def _flush_calls(self) -> None:
        with self.lock:
            pending, self._pending_calls = self._pending_calls, []
        for call in pending:
            self.log_call(*call)
