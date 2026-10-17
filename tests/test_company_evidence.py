"""Public HQ enrichment is scoped to company evidence and bounded report work."""

import json
import sqlite3
from dataclasses import replace

import pytest
import requests

from scanner import company_evidence
from scanner.company_profile import CompanyProfiles
from scanner.models import Job


def job(**kwargs):
    values = dict(id="test:example:1", source="test", url="https://example.com/jobs/1",
                  title="Python Engineer", company="Example", company_key="example", description="")
    return Job(**(values | kwargs))


def network(monkeypatch, routes):
    calls = []

    def get(session, url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 5
        body = routes[url]
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = (body if isinstance(body, str) else json.dumps(body)).encode()
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    return calls


@pytest.mark.parametrize("country", ["DE", "Germany", {"@type": "Country", "name": "Germany"}])
def test_organization_json_ld_country(country):
    data = {"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "name": "Example", "address": {"addressCountry": country}}]}
    text = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    assert company_evidence.website_country(text, "https://example.com/", "Example")[0] == "de"


def test_imprint_postal_address_and_two_page_limit(monkeypatch):
    calls = network(monkeypatch, {
        "https://example.com/": '<a href="/about">About</a><a href="/imprint">Imprint</a>',
        "https://example.com/imprint": '<h1>Example GmbH</h1><p>Berlin, Germany</p>',
    })
    assert company_evidence.lookup(job())[0] == "de"
    assert calls == ["https://example.com/", "https://example.com/imprint"]


def test_smartrecruiters_company_location(monkeypatch):
    endpoint = "https://api.smartrecruiters.com/v1/companies/example/postings/123"
    calls = network(monkeypatch, {endpoint: {
        "company": {"name": "Example", "location": {"city": "Berlin", "country": "de"}},
        "location": {"city": "Dhaka", "country": "bd", "remote": True},
    }})
    country, evidence = company_evidence.lookup(job(id="smartrecruiters:example:123"))
    assert country == "de"
    assert "ATS company location" in evidence
    assert calls == [endpoint]


def test_job_location_is_not_company_location(monkeypatch):
    network(monkeypatch, {
        "https://api.smartrecruiters.com/v1/companies/example/postings/123": {
            "company": {"name": "Example"}, "location": {"city": "Dhaka", "country": "bd"}},
        "https://example.com/": '<p>Open positions: Berlin, Germany</p><script type="application/ld+json">'
        '{"@type":"JobPosting","jobLocation":{"address":{"addressCountry":"DE"}}}</script>',
    })
    assert company_evidence.lookup(job(id="smartrecruiters:example:123", location="Dhaka", countries=["bd"]))[0] is None


def test_unrelated_organization_is_not_employer():
    text = '<script type="application/ld+json">{"@type":"Organization","name":"Recruiter",' \
           '"address":{"addressCountry":"IN"}}</script><footer>We hire remotely in Germany</footer>'
    assert company_evidence.website_country(text, "https://example.com/", "Example")[0] is None


def test_workable_account_location(monkeypatch):
    endpoint = "https://apply.workable.com/api/v1/widget/accounts/example"
    network(monkeypatch, {endpoint: {"name": "Example", "location": {"country": "Canada"},
                                    "jobs": [{"location": {"country": "India"}}]}})
    assert company_evidence.lookup(job(id="workable:example:123"))[0] == "ca"


def test_lever_board_discovers_company_website(monkeypatch):
    network(monkeypatch, {
        "https://jobs.lever.co/example": '<a href="https://example.com/">Company website</a>',
        "https://example.com/": '<footer><address>Berlin, Germany</address></footer>',
    })
    assert company_evidence.lookup(job(id="lever:example:123", url="https://jobs.lever.co/example/123"))[0] == "de"


def test_positive_cache_survives_new_resolver_and_avoids_network(monkeypatch, tmp_path):
    calls = network(monkeypatch, {"https://example.com/": '<address>Berlin, Germany</address>'})
    path = tmp_path / "scanner.sqlite"
    db = sqlite3.connect(path)
    first = job()
    CompanyProfiles(db).enrich_report([first])
    db.commit()
    db.close()
    later = job()
    CompanyProfiles(sqlite3.connect(path)).enrich_report([later])
    assert later.screen["hq_country"] == "de"
    assert calls == ["https://example.com/"]


def test_empty_retries_next_run_but_only_once_per_company(monkeypatch):
    calls = []

    def lookup(j):
        calls.append(j.company_key)
        return None, "empty"

    monkeypatch.setattr(company_evidence, "lookup", lookup)
    db = sqlite3.connect(":memory:")
    profiles = CompanyProfiles(db)
    profiles.enrich_report([job(), job(id="test:example:2")])
    profiles.enrich_report([job()])
    assert calls == ["example"]
    CompanyProfiles(db).enrich_report([job()])
    assert calls == ["example", "example"]


def test_existing_evidence_and_overrides_skip_network(monkeypatch):
    def unexpected(j):
        pytest.fail("known company fetched")

    monkeypatch.setattr(company_evidence, "lookup", unexpected)
    profiles = CompanyProfiles(sqlite3.connect(":memory:"))
    profiles.enrich_report([job(description=""), job(description="We are headquartered in Germany.")])
    overridden = job(company_key="override")
    CompanyProfiles(sqlite3.connect(":memory:"), {"hq_overrides": {"override": "us"}}).enrich_report([overridden])
    assert overridden.screen["hq_country"] == "us"


def test_max_100_companies_per_run(monkeypatch):
    calls = []
    monkeypatch.setattr(company_evidence, "lookup", lambda j: (calls.append(j.company_key), "empty"))
    profiles = CompanyProfiles(sqlite3.connect(":memory:"))
    jobs = [replace(job(), company_key=f"company-{i}") for i in range(105)]
    profiles.enrich_report(jobs)
    profiles.enrich_report(jobs)
    assert len(calls) == len(set(calls)) == 100


def test_website_timeout_leaves_unknown(monkeypatch):
    def timeout(*args, **kwargs):
        raise requests.Timeout()

    monkeypatch.setattr(requests.Session, "get", timeout)
    assert company_evidence.lookup(job())[0] is None


def test_footer_copyright_and_country_navigation_are_not_address():
    text = '<footer>Copyright 2026 Example. Serving customers in Germany</footer>'
    assert company_evidence.website_country(text, "https://example.com/", "Example")[0] is None


def test_explicit_website_hq_statement_reuses_company_patterns(monkeypatch):
    network(monkeypatch, {"https://example.com/": '<p>We are headquartered in Berlin, Germany.</p>'})
    assert company_evidence.lookup(job())[0] == "de"


def test_malformed_json_ld_shapes_leave_unknown():
    text = '<script type="application/ld+json">[{"@graph":42},{"@type":42},' \
           '{"@type":"Organization","name":{"bad":"shape"}}]</script>'
    assert company_evidence.website_country(text, "https://example.com/", "Example")[0] is None


def test_recruitee_explicit_company_country_not_offer_country(monkeypatch):
    endpoint = "https://example.recruitee.com/api/offers/123"
    network(monkeypatch, {endpoint: {"offer": {"company_name": "Example", "company_country": "Germany",
                                               "country": "India", "city": "Bangalore"}}})
    assert company_evidence.lookup(job(id="recruitee:example:123"))[0] == "de"


def test_offer_country_without_company_country_does_not_establish_hq():
    assert company_evidence._company_metadata({"offer": {"company_name": "Example", "country": "India"}})[0] is None
