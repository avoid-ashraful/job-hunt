"""A guessed ATS board is only accepted when its landing page names the company."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scanner import board_owner, watchlist
from scanner.sources import ats
from scanner.board_owner import names_match, owns


@pytest.mark.parametrize("owner, company, same", [
    ("Jobs at Kentik", "Kentik Technologies Inc.", True),
    ("Openings at Cal.com", "Cal.com", True),
    ("Supabase Jobs", "Supabase", True),
    ("Rocket.Chat", "RocketChat", True),
    ("Toyota Research Institute", "Modern Tribe", False),
    ("Jobs at Cadence Solutions", "Rakuten Travel Xchange", False),
    ("Jobs bei base camp management consulting GmbH", "Basecamp", False),
    ("Build Technologies Jobs", "Grou.ps", False),
    ("BambooHR", "Deel", False),
])
def test_names_match(owner, company, same):
    assert names_match(owner, company) is same


class Resp:
    def __init__(self, text, url, code=200):
        self.headers = {}
        self.text, self.url, self.status_code, self.ok = text, url, code, code < 400


class Session:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, **kwargs):
        return self.resp


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(ats, "_pace", lambda url: None)
    monkeypatch.setattr(ats.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ats, "_NEXT_REQUEST", {})
    monkeypatch.setattr(ats, "_BACKOFF", {})


def test_owns_reads_the_landing_page():
    page = '<title>Jobs at GitLab</title>'
    assert owns(Session(Resp(page, "https://job-boards.greenhouse.io/gitlab")), "greenhouse", "gitlab", "GitLab")
    other = '<title>Jobs bei base camp management consulting GmbH</title>'
    assert not owns(Session(Resp(other, "https://basecamp.jobs.personio.de/")), "personio", "basecamp", "Basecamp",
                    "https://basecamp.com")
    # no name match, but the board links to the company's own website
    linked = '<title>Careers</title><a href="https://www.bandzoogle.com/">home</a>'
    assert owns(Session(Resp(linked, "https://jobs.lever.co/bz")), "lever", "bz", "Bandzoogle", "https://bandzoogle.com")


def test_demo_boards_and_foreign_redirects_are_rejected():
    demo = '<title>Jobs at Shopify</title> Head of DEI - UK at Hooli'
    assert not owns(Session(Resp(demo, "https://shopify.pinpointhq.com/")), "pinpoint", "shopify", "Shopify")
    moved = '<title>Wij zijn Prisma</title>'
    assert not owns(Session(Resp(moved, "https://werkenbij.prismanet.nl/")), "recruitee", "prisma", "Prisma",
                    "https://www.prisma.io")


def test_unreadable_landing_page_is_unknown_not_accepted():
    assert owns(Session(Resp("Too many requests", "https://apply.workable.com/amazon/", 429)),
                "workable", "amazon", "Amazon") is None


def test_verify_owners_drops_old_slug_guesses_that_fail(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    entries = {
        "gitlab": {"company": "GitLab", "ats": "greenhouse", "board": "gitlab"},
        "modern-tribe": {"company": "Modern Tribe", "ats": "lever", "board": "tri"},
        "found-on-site": {"company": "Found", "ats": "lever", "board": "found"},
    }
    for key in ("gitlab", "modern-tribe"):
        (cache / f"{key}.json").write_text(json.dumps({"ats": "x", "board": "y", "reason": "verified slug"}))
    (cache / "found-on-site.json").write_text(json.dumps({"ats": "lever", "board": "found", "reason": "careers link"}))
    monkeypatch.setattr(watchlist, "owns", lambda s, ats, board, company, site="": company == "GitLab")
    stats = watchlist.verify_owners(entries, cache)
    assert stats == {"checked": 2, "kept": 1, "dropped": 1, "unknown": 0}
    assert set(entries) == {"gitlab", "found-on-site"}  # careers-page links are trusted
    assert json.loads((cache / "gitlab.json").read_text())["owner_check"] == watchlist.OWNER_CHECK
    assert json.loads((cache / "modern-tribe.json").read_text())["status"] == "miss"


def test_redirect_to_the_companys_own_careers_site_counts_as_owned():
    assert owns(Session(Resp("", "https://careers.godaddy/jobs/search")), "greenhouse", "godaddy", "GoDaddy",
                "https://www.godaddy.com")
    assert owns(Session(Resp("Forbidden", "https://www.epicgames.com/careers/jobs", 403)), "greenhouse", "epicgames",
                "Epic Games", "https://epicgames.com")


def test_nameless_board_needs_an_exact_distinctive_slug():
    page = "<title>Jobs</title>"
    assert owns(Session(Resp(page, "https://jobs.ashbyhq.com/posthog")), "ashby", "posthog", "PostHog")
    assert not owns(Session(Resp(page, "https://jobs.ashbyhq.com/build")), "ashby", "build", "Grou.ps")
    assert not owns(Session(Resp(page, "https://jobs.ashbyhq.com/ghost")), "ashby", "ghost", "Ghost")  # too generic


FIXTURES = Path(__file__).parent / "fixtures" / "board_owner"


class FixtureSession:
    """Serves the trimmed landing page, Personio feed, Pinpoint postings, or a linked company's homepage."""

    def __init__(self, name):
        self.name = name

    def get(self, url, **kwargs):
        host = urlparse(url).hostname
        if url.endswith("/xml"):
            path = f"{self.name}.xml"
        elif url.endswith("postings.json"):
            path = f"{self.name}.json"
        elif host.endswith(board_owner.ATS_DOMAINS):
            path = f"{self.name}.html"
        else:
            path = f"site.{host}.html"
        return Resp((FIXTURES / path).read_text(), url)


@pytest.mark.parametrize("name, kind, board, company, website, ours", [
    # same-name companies: the board links its own, different domain
    ("krakentech", "ashby", "krakentech", "Kraken", "https://kraken.com", False),  # kraken.tech, Octopus Energy
    ("ghost", "ashby", "ghost", "Ghost", "https://ghost.org", False),  # ghost.ai
    ("lightspeed", "ashby", "lightspeed", "Lightspeed", "https://www.lightspeedhq.com/", False),  # lightspeed.build
    ("ready", "ashby", "ready", "Ready", "https://joinready.com", False),  # ready.net
    ("calibre", "ashby", "calibre", "Calibre", "https://calibreapp.com/", False),  # calibre.ac
    ("firstpoint", "smartrecruiters", "firstpoint", "First Point", "https://firstpoint.com.tr", False),  # firstpoint.ch
    ("conversio", "personio", "conversio", "Conversio", "https://conversio.com", False),  # conversio.dk, in the postings
    ("yonder", "teamtailor", "yonder", "Yonder", "https://www.yonder.io", False),  # yonder.com
    pytest.param("connexa", "bamboohr", "connexa", "Connexa", "https://www.connexa.com/", False,
                 marks=pytest.mark.xfail(strict=True, reason="board and postings link no domain at all")),
    # the real boards, including ones that link another domain of the same company
    ("saasglobal", "teamtailor", "saasglobal", "WP-Media", "https://wp-media.me/", True),  # group board links wp-media.me
    ("supabase", "ashby", "supabase", "Supabase", "", True),
    ("gitlab", "greenhouse", "gitlab", "GitLab", "https://about.gitlab.com/", True),
    ("canonical", "greenhouse", "canonical", "Canonical", "https://www.canonical.com/", True),
    ("posthog", "ashby", "posthog", "PostHog", "https://posthog.com/", True),
    ("railway", "ashby", "railway", "Railway", "https://railway.com/", True),  # railway.app, which links railway.com
    ("spotify", "lever", "spotify", "Spotify", "https://www.spotify.com/", True),  # spotifyjobs.com
    ("cabify", "greenhouse", "cabify", "Cabify", "https://cabify.com/", True),  # cabify.careers
    ("bandlab", "pinpoint", "bandlab", "BandLab", "https://bandlab.com/", True),  # bandlabtechnologies.com
])
def test_same_name_company_with_another_domain_is_not_ours(name, kind, board, company, website, ours):
    assert owns(FixtureSession(name), kind, board, company, website) is ours


@pytest.mark.parametrize("url,expected", [
    ("https://www.firstpoint.com.tr/careers", "firstpoint.com.tr"),
    ("https://jobs.example.co.uk", "example.co.uk"),
    ("https://careers.ghost.org/", "ghost.org"),
    ("https://kraken.tech", "kraken.tech"),
    ("acme.io", "acme.io"),
])
def test_domain_keeps_second_level_country_suffixes(url, expected):
    assert board_owner._domain(url) == expected
