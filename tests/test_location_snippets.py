"""Eligibility evidence extraction, independent of deterministic rule decisions."""

from pathlib import Path

import pytest
import yaml

from scanner.models import Job
from scanner.rules import LocationRules


@pytest.fixture
def rules():
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.example.yml").read_text())
    return LocationRules(cfg["rules"]["location"])


def extract(rules, text):
    job = Job(id="test:1", source="test", url="https://example.com/job", title="Engineer",
              company="Example", company_key="example", description=text)
    return rules.classify(job).snippets


@pytest.mark.parametrize("sentence", [
    "Developers aren't limited to a particular region or country.",
    "Most roles can be done from anywhere within the country of employment.",
    "This role is open to candidates based in United Arab Emirates.",
    "This role is open only to candidates residing in Alberta or Ontario.",
    "US work authorization required.",
    "Candidates must be authorized to work in the US.",
    "Mandatory UK/EU work authorisation is required.",
    "We are not considering fully remote candidates.",
    "Although we are unable to accept fully remote candidates, home working is flexible.",
    "We offer work permits and help with relocation.",
    "You have the right to work in one of the countries we can hire from (see the list).",
    "Collaborate with designers from anywhere in SA.",
    "Visa support is available.",
    "We cannot provide sponsorship.",
    "Citizenship is required.",
    "You must be a citizen.",
    "Residence here is mandatory.",
    "You must reside here.",
    "The candidate resides here.",
    "You must be located in our service area.",
    "You must relocate before starting.",
    "This is an on-site role.",
    "This is an onsite role.",
    "Hybrid attendance is required.",
    "Hiring in Germany.",
    "Candidates from any region can apply.",
    "Candidates from any country can apply.",
    "Hiring is limited to our service area.",
    "Hiring is not limited to our service area.",
    "This state has a residence requirement.",
    "The province determines your employment contract.",
])
def test_eligibility_sentences_reach_input(rules, sentence):
    assert sentence in extract(rules, "We build useful software.\n" + sentence)


@pytest.mark.parametrize("header", [
    "Locations:", "Available locations:", "Eligible countries:",
    "**Available Locations**", "Available Locations\n-------------------",
])
def test_location_list_keeps_header_and_items(rules, header):
    text = header + "\n\n* Eastport, US\n* Westport, US\n* Canada\n\nAbout the role\nBuild tools."
    snippets = extract(rules, text)
    assert snippets[0].splitlines() == [header.splitlines()[0], "* Eastport, US", "* Westport, US", "* Canada"]
    assert all("About the role" not in s for s in snippets)


def test_list_bound_and_following_section(rules):
    items = [f"* Regional district {i:02d}, Canada" for i in range(17)]
    snippets = extract(rules, "Locations:\n" + "\n".join(items))
    block = snippets[0]
    assert block.splitlines() == ["Locations:", *items[:15]]
    assert len(block) > 400
    snippets = extract(rules, "Locations:\n* Eastport\nResponsibilities:\nBuild tools.")
    assert snippets == ["Locations:\n* Eastport"]


def test_plain_location_list(rules):
    snippets = extract(rules, "Eligible countries:\nFrance\nGermany\n\nResponsibilities:\nBuild tools.")
    assert snippets[0] == "Eligible countries:\nFrance\nGermany"


def test_decisive_evidence_precedes_incidental_geography_and_obeys_cap(rules):
    rules.max_snippet_chars = 160
    sentence = "Candidates must have work authorization."
    snippets = extract(rules, "\n".join(f"Our customer {i} is in Canada." for i in range(30))
                       + "\n" + sentence + "\n" + sentence)
    assert snippets[0] == sentence
    assert snippets.count(sentence) == 1
    assert sum(map(len, snippets)) <= 160


def test_extraction_preserves_rule_outcome(rules):
    job = Job(id="test:1", source="test", url="https://example.com/job", title="Engineer",
              company="Example", company_key="example", description="We offer visa sponsorship.")
    assert rules.classify(job).outcome == "silent"
    assert rules.classify(job).snippets == [job.description]


def test_extraction_uses_configured_geography(rules):
    import re

    rules.geo = re.compile(r"\bExampleprovince\b", re.I)
    assert extract(rules, "Hiring in Exampleprovince.") == ["Hiring in Exampleprovince."]


def test_header_stops_at_long_prose_and_total_cap(rules):
    rules.max_snippet_chars = 120
    snippets = extract(rules, "Locations:\n* Eastport\n" + "Build tools " * 20)
    assert snippets == ["Locations:\n* Eastport"]
    text = "Locations:\n" + "\n".join("* " + "X" * 90 for _ in range(15))
    assert sum(map(len, extract(rules, text))) <= 120


def test_extraction_update_invalidates_cached_verdicts(tmp_path, monkeypatch):
    import hashlib
    import json
    import sqlite3

    from scanner.eligibility import BATCH_SCHEMA, Screener
    from scanner.models import Job

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Judge eligibility.")
    db = sqlite3.connect(":memory:")
    screener = Screener({"chain": [{"provider": "gemini", "model": "test"}]}, prompt, db)
    old_system = screener.clients[0].system.replace(
        " Snippets include eligibility requirements and location lists as well as geographic sentences.", "",
    )
    old_hash = hashlib.sha256((old_system + json.dumps(BATCH_SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]
    assert old_hash != screener.prompt_hash
    db.execute("INSERT INTO llm_location (job_id, prompt_hash, model, result, created_at) VALUES (?, ?, ?, ?, ?)",
               ("test:1", old_hash, "test", '{}', "2026-10-17"))
    job = Job(id="test:1", source="test", url="https://example.com/job", title="Engineer",
              company="Example", company_key="example", description="US work authorization required.")
    assert screener._cached(job) is None
