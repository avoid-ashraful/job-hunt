"""Conservative company seeds from public, explicitly eligible remote-company lists."""

from __future__ import annotations

import logging

import io
import re
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import requests
import yaml

from scanner.company_profile import hq_country
from scanner.dedup import normalize_company
from scanner.models import Job
from scanner.watchlist import _is_excluded

AWESOME_URL = "https://raw.githubusercontent.com/lukasz-madon/awesome-remote-job/master/README.md"
REMOTEINTECH_URL = "https://codeload.github.com/remoteintech/remote-jobs/zip/refs/heads/main"
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
QUALITY = re.compile(r"\b(?:agenc\w*|staffing|outsourc\w*|crowdsourc\w*|outstaff\w*|marketplace|consultanc\w*|consulting|freelancer\w*|talent network|recruitment company|software house)\b", re.I)
REGIONS = {"worldwide", "anywhere", "asia", "apac", "asia-pacific", "asia pacific", "bangladesh"}

log = logging.getLogger(__name__)


def _eligible(seed: dict, evidence: str, cfg: dict) -> bool:
    filters = cfg.get("filters", {})
    country, _ = hq_country(Job(id="", source="seeds", url=seed["website"], title="", company=seed["company"],
                              company_key=normalize_company(seed["company"]), description=evidence),
                            filters.get("company_market", {}))
    seed["hq_country"] = str(seed.get("hq_country") or country or "").lower()
    return (seed["hq_country"] not in {"in", "bd", "india", "bangladesh"}
            and not _is_excluded(seed, filters)
            and not QUALITY.search(seed["company"] + " " + evidence))


def parse_awesome(text: str, cfg: dict | None = None) -> list[dict]:
    seeds, in_companies = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            in_companies = line.strip() == '## Companies with "remote DNA"'
        if not in_companies or not (match := LINK.search(line)):
            continue
        description = line[match.end():].strip(" -–")
        # A worldwide customer base or talent shortage does not establish hiring eligibility.
        if not re.search(r"\b(?:hiring\b[^.]*worldwide|remotely worldwide|work from anywhere)\b", description, re.I):
            continue
        if re.search(r"\b(?:not|no longer)\s+(?:hiring|remote|work)\b", description, re.I):
            continue
        name, url = match.groups()
        parts = urlsplit(url)
        website = f"{parts.scheme}://{parts.netloc}" if not parts.netloc.endswith(".notion.site") else ""
        seed = dict(company=name, website=website, careers_url=url, hq_country="", hires="worldwide",
                    source="awesome-remote-job", why=f"{AWESOME_URL}: {description}")
        if _eligible(seed, description, cfg or {}):
            seeds.append(seed)
    return seeds


def _section(text: str, title: str) -> str:
    match = re.search(r"^## " + re.escape(title) + r"\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match[1].strip() if match else ""


def parse_remoteintech(archive: bytes, cfg: dict | None = None) -> list[dict]:
    seeds = []
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for name in sorted(files.namelist()):
            if "/src/companies/" not in name or not name.endswith(".md"):
                continue
            text = files.read(name).decode("utf-8")
            parts = text.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                continue
            front = yaml.safe_load(parts[1]) or {}
            # Worldwide frontmatter is already loaded by build; coarse APAC tags include India-only entries.
            if front.get("region") == "worldwide":
                continue
            region = _section(parts[2], "Region")
            tokens = {s.strip().lower().rstrip(".") for s in re.split(r",|\n|\band\b", region)}
            if (not tokens.intersection(REGIONS) or not front.get("title")
                    or re.search(r"###|\b(?:only|primarily|except|excluding|restricted|timezone|timezones)\b", region, re.I)):
                continue
            blurb = _section(parts[2], "Company blurb")
            offices = _section(parts[2], "Office locations")
            country = front.get("hq_country") or ""
            # A sole listed India/Bangladesh office is sufficient reason to skip an uncertain seed.
            if re.search(r"\b(?:India|Bangladesh)\b", offices, re.I) and len(offices.splitlines()) == 1:
                country = "in" if re.search(r"\bIndia\b", offices, re.I) else "bd"
            seed = dict(company=front["title"], website=front.get("website") or "",
                        careers_url=front.get("careers_url") or "", hq_country=country, hires="worldwide",
                        source="remoteintech-regional", why=f"https://github.com/remoteintech/remote-jobs/blob/main/src/companies/{Path(name).name}: Region: {region}")
            if _eligible(seed, blurb + "\n" + _section(parts[2], "Remote status"), cfg or {}):
                seeds.append(seed)
    return seeds


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": "job-hunt-scanner/0.1 (personal remote company seed research)"}, timeout=60)
    response.raise_for_status()
    return response


def fetch_awesome(cfg: dict | None = None) -> list[dict]:
    return parse_awesome(_get(AWESOME_URL).text, cfg)


def fetch_remoteintech(cfg: dict | None = None) -> list[dict]:
    return parse_remoteintech(_get(REMOTEINTECH_URL).content, cfg)


def read_seeds(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return (raw.get("companies", []) if isinstance(raw, dict) else raw) or []


def merge_seeds(existing: list[dict], incoming: list[dict], reserved: list[dict] | None = None) -> tuple[list[dict], dict]:
    keys = {normalize_company(s["company"]) for s in (reserved or [])}
    merged, counts = [], Counter()
    for batch, is_new in ((existing, False), (incoming, True)):
        for seed in batch:
            key = normalize_company(seed["company"])
            if not key or key in keys:
                if key and not is_new and not reserved:
                    log.warning("seed %r is listed twice in the hand seeds; only the first entry is used", seed["company"])
                continue
            keys.add(key)
            merged.append(seed)
            if is_new:
                counts[seed.get("source", "seeds_yml")] += 1
    return sorted(merged, key=lambda s: normalize_company(s["company"])), dict(counts)


def update_seeds(hand_file: Path, auto_file: Path, cfg: dict) -> dict:
    incoming = fetch_awesome(cfg) + fetch_remoteintech(cfg)
    merged, counts = merge_seeds(read_seeds(auto_file), incoming, read_seeds(hand_file))
    auto_file.write_text("# Generated by scanner watchlist seeds; hand entries stay in watchlist.seeds.yml.\n"
                         + yaml.safe_dump({"companies": merged}, sort_keys=False, allow_unicode=True))
    return {"added": counts, "total": len(merged)}


@contextmanager
def build_seeds(hand_file: Path, auto_file: Path):
    """Supply both tracked lists through build's existing single-file interface."""
    merged, _ = merge_seeds(read_seeds(hand_file), read_seeds(auto_file))
    with TemporaryDirectory(prefix="scanner-seeds-") as directory:
        path = Path(directory) / "seeds.yml"
        path.write_text(yaml.safe_dump({"companies": merged}, sort_keys=False))
        yield path
