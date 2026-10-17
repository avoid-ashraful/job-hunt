"""Public source normalization and eligibility clues without network calls."""

from scanner.sources._public import ats_id
from scanner.sources.himalayas import to_job as himalayas_job
from scanner.sources.hn import _comment_job
from scanner.sources.workable import to_job as workable_job


def test_himalayas_worldwide_and_application_link():
    raw = {
        "guid": "https://himalayas.app/companies/acme/jobs/senior-python-engineer",
        "applicationLink": "https://himalayas.app/companies/acme/jobs/senior-python-engineer",
        "companyName": "Acme", "title": "Senior Python Engineer",
        "locationRestrictions": [], "description": "<p>Work from anywhere.</p>",
        "pubDate": 1792000000, "employmentType": "Contractor",
    }
    job = himalayas_job(raw)
    assert job and job.regions == ["global"] and job.employment_type == "contract"
    assert job.url == raw["applicationLink"] and job.description == "Work from anywhere."
    assert himalayas_job({**raw, "locationRestrictions": ["US"]}) is None
    assert himalayas_job({**raw, "timezoneRestrictions": [-8, 0, 5]}) is None


def test_workable_remote_country_is_preserved():
    raw = {
        "state": "published", "workplace": "remote", "company": {"title": "Acme"},
        "url": "https://jobs.workable.com/view/ABC123/senior-python-engineer",
        "title": "Senior Python Engineer", "description": "<p>Python and Django</p>",
        "locations": ["TELECOMMUTE", "Toronto, Canada"],
    }
    job = workable_job(raw)
    assert job and job.id == "workable:acme:ABC123"
    assert job.location == "Remote; Toronto, Canada"
    assert workable_job({**raw, "workplace": "onsite"}) is None


def test_hn_comment_uses_ats_id_when_present():
    cfg = {
        "remote_pattern": r"\bremote\b",
        "worldwide_pattern": r"\bworldwide\b",
        "role_pattern": r"\b(?:Senior )?Backend Engineer\b",
    }
    raw = {
        "objectID": "123", "created_at": "2026-10-01T00:00:00Z",
        "comment_text": "Acme | Senior Backend Engineer | REMOTE worldwide<br>Python/Django. https://jobs.lever.co/acme/1234",
    }
    job = _comment_job(raw, cfg)
    assert job and job.id == "lever:acme:1234"
    assert job.regions == ["global"] and job.source == "hn"
    assert _comment_job({**raw, "comment_text": "Acme | Senior Backend Engineer | onsite"}, cfg) is None
    assert _comment_job({**raw, "comment_text": "Senior Backend Engineer | REMOTE"}, cfg) is None


def test_ats_id_recognizes_public_greenhouse_host():
    assert ats_id("https://job-boards.greenhouse.io/acme/jobs/123", "Acme") == "greenhouse:acme:123"
