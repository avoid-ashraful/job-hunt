"""Public HQ enrichment is scoped to company evidence and bounded report work."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
import requests

from scanner import company_evidence
from scanner.company_profile import CompanyProfiles
from scanner.models import Job
from scanner.sources.freehire import to_job


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


def freehire_fixtures():
    directory = Path(__file__).parent / "fixtures" / "freehire"
    return to_job(json.loads((directory / "job.json").read_text())), json.loads((directory / "company.json").read_text())


def test_freehire_preserves_exact_company_slug_and_resolves_hq(monkeypatch):
    posting, data = freehire_fixtures()
    assert posting.company_key == "example"
    assert posting.ats_metadata == {"freehire_company_slug": "example-inc"}
    endpoint = "https://freehire.me/api/v1/companies/example-inc?limit=1"
    calls = network(monkeypatch, {endpoint: data})
    country, evidence = company_evidence.lookup(posting)
    assert country == "de"
    assert "freehire company hq_country" in evidence
    assert calls == [endpoint]


def test_freehire_website_without_scheme_uses_existing_lookup(monkeypatch):
    posting, data = freehire_fixtures()
    data["data"]["company"]["hq_country"] = None
    calls = network(monkeypatch, {
        "https://freehire.me/api/v1/companies/example-inc?limit=1": data,
        "https://example.com": '<a href="/imprint">Imprint</a>',
        "https://example.com/imprint": '<address>Berlin, Germany</address>',
    })
    assert company_evidence.lookup(posting)[0] == "de"
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["hiring_countries", "wrong_company", "wrong_website", "malformed", "invalid_json"])
def test_freehire_unsafe_or_missing_metadata_falls_back(monkeypatch, change):
    posting, data = freehire_fixtures()
    posting.ats_metadata = {}  # Old stored postings lack the original freehire slug.
    company = data["data"]["company"]
    company["hq_country"] = None
    company["company_info"] = {}
    if change == "wrong_company":
        company.update(name="Other Employer", hq_country="IN")
    if change == "wrong_website":
        company["company_info"] = {"website": "https://unrelated.com"}
    if change == "malformed":
        data = {"data": []}
    if change == "invalid_json":
        data = "not JSON"
    calls = network(monkeypatch, {
        "https://freehire.me/api/v1/companies/example?limit=1": data,
        posting.url: "<p>We hire in India</p>",
    })
    assert company_evidence.lookup(posting)[0] is None
    assert calls[-1] == posting.url


def test_freehire_url_reuses_ats_company_metadata(monkeypatch):
    posting, _ = freehire_fixtures()
    posting.url = "https://jobs.smartrecruiters.com/Example/123"
    endpoint = "https://api.smartrecruiters.com/v1/companies/Example/postings/123"
    calls = network(monkeypatch, {endpoint: {"company": {"name": "Example", "location": {"country": "DE"}},
                                          "location": {"country": "IN"}}})
    assert company_evidence.lookup(posting)[0] == "de"
    assert calls == [endpoint]


def test_workday_hq_lookup_uses_board_landing(monkeypatch):
    calls = network(monkeypatch, {
        "https://example.wd1.myworkdayjobs.com/Careers": '<a href="https://example.com/">Company</a>',
        "https://example.com/": '<footer><address>Berlin, Germany</address></footer>',
    })
    assert company_evidence.lookup(job(id="workday:example|wd1|Careers:123",
                                      url="https://example.wd1.myworkdayjobs.com/Careers/job/123"))[0] == "de"
    assert calls[0] == "https://example.wd1.myworkdayjobs.com/Careers"


def test_comeet_hq_lookup_uses_composite_board_landing(monkeypatch):
    calls = network(monkeypatch, {
        "https://www.comeet.com/jobs/example/12.003": '<a href="https://example.com/">Company</a>',
        "https://example.com/": '<address>Berlin, Germany</address>',
    })
    posting = job(id="comeet:example|12.003:AB.CDE",
                  url="https://www.comeet.com/jobs/example/12.003/backend/AB.CDE")
    assert company_evidence.lookup(posting)[0] == "de"
    assert calls[0] == "https://www.comeet.com/jobs/example/12.003"


def test_malformed_company_page_cannot_abort_other_report_enrichment(monkeypatch, caplog):
    network(monkeypatch, {
        "https://example.com/": '<script type>untyped script</script>',
        "https://healthy.com/": '<address>Berlin, Germany</address>',
    })
    malformed = job()
    healthy = job(id="test:healthy:1", company="Healthy", company_key="healthy",
                  url="https://healthy.com/jobs/1")
    profiles = CompanyProfiles(sqlite3.connect(":memory:"))
    profiles.enrich_report([malformed, healthy])
    assert malformed.screen["hq_country"] is None
    assert healthy.screen["hq_country"] == "de"
    assert "example" in caplog.text and "AttributeError" in caplog.text


def test_freehire_description_entities_are_decoded():
    raw = json.loads((Path(__file__).parent / "fixtures" / "freehire" / "job.json").read_text())
    raw["description"] = "Location:&#xa;- Remote, Worldwide&#xa;Salary &amp; equity"
    assert to_job(raw).description == "Location:\n- Remote, Worldwide\nSalary & equity"


def test_watchlist_website_reaches_hq_lookup_but_job_links_do_not(monkeypatch):
    import requests
    from scanner import company_evidence
    from scanner.sources import ats

    raw = {"id": 1, "title": "Senior Backend Engineer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
           "content": "Python", "location": {"name": "Remote"}}
    job = ats._record(raw, {"ats": "greenhouse", "board": "acme", "company": "Acme", "website": "https://acme.io"},
                      {}, requests.Session())
    assert job.ats_metadata["company_website"] == "https://acme.io"
    fetched = []
    monkeypatch.setattr(company_evidence, "_fetch", lambda session, url: fetched.append(url))
    company_evidence.lookup(job)
    assert "https://acme.io" in fetched
    fetched.clear()
    job.ats_metadata["company_website"] = "https://news.ycombinator.com/item?id=1"
    company_evidence.lookup(job)
    assert not any("ycombinator" in u for u in fetched)
