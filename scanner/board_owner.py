"""Does this ATS board belong to the company we guessed it for?

A slug guess ("basecamp" on Personio) can hit a different company's board, a demo
board, or a board that redirects to an unrelated custom domain. The board's own
landing page names its owner (<title>, og:site_name), so a guessed board is accepted
only when that name matches the company, or the page links to the company's website.
A board that links a different company domain (and never ours) belongs to a same-name company.
Boards found on the company's own careers page are trusted and skip this check.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote, urlparse

import requests

from scanner.sources.ats import _request
from scanner.sources import _more_ats

LANDING = {
    "workday": "https://{tenant}.{wd}.myworkdayjobs.com/{site}",
    "rippling": "https://ats.rippling.com/{b}/jobs",
    "jazzhr": "https://{b}.applytojob.com/apply",
    "homerun": "https://{b}.homerun.co",
    "dover": "https://app.dover.com/jobs/{b}",
    "comeet": "https://www.comeet.com/jobs/{slug}/{uid}",
    "factorial": "https://{b}.factorialhr.com",
    "greenhouse": "https://job-boards.greenhouse.io/{b}",
    "lever": "https://jobs.lever.co/{b}",
    "lever_eu": "https://jobs.eu.lever.co/{b}",
    "ashby": "https://jobs.ashbyhq.com/{b}",
    "workable": "https://apply.workable.com/{b}/",
    "smartrecruiters": "https://careers.smartrecruiters.com/{b}",
    "recruitee": "https://{b}.recruitee.com/",
    "breezy": "https://{b}.breezy.hr/",
    "bamboohr": "https://{b}.bamboohr.com/careers",
    "pinpoint": "https://{b}.pinpointhq.com/",
    "personio": "https://{b}.jobs.personio.de/",
    "teamtailor": "https://{b}.teamtailor.com/",
}
ATS_DOMAINS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com", "recruitee.com",
               "breezy.hr", "bamboohr.com", "pinpointhq.com", "personio.de", "personio.com", "teamtailor.com",
               "myworkdayjobs.com", "rippling.com", "applytojob.com", "homerun.co", "dover.com", "comeet.com", "factorialhr.com", "factorial.com")
# Words that don't identify a company: legal forms, "Jobs at", domain suffixes.
NOISE = set("""inc ltd llc gmbh corp corporation co company limited plc bv ag sa srl ab oy as aps
technologies technology tech labs lab software hq group holdings io ai app com net org dev
the jobs job careers career openings current at bei with join us work team search page
greenhouse lever ashby workable smartrecruiters recruitee breezy bamboohr pinpoint personio teamtailor""".split())
# Pages that list many companies: an ATS link found there says nothing about who owns it.
AGGREGATORS = ("ycombinator.com", "himalayas.app", "startup.jobs", "workable.com", "linkedin.com", "angel.co",
               "wellfound.com", "notion.site")
DEMO = re.compile(r"\b(?:hooli|acme corp(?:oration)?|lorem ipsum|sample job|\(sample\)|demo company)\b|\"acme\"", re.I)
# Linked domains that never name the board's owner.
NOT_OWNERS = ATS_DOMAINS + AGGREGATORS + ("greenhouse.com", "ashbyprd.com", "teamtailor-cdn.com", "facebook.com",
              "instagram.com", "twitter.com", "x.com", "tiktok.com", "youtube.com", "github.com", "google.com",
              "cloudfront.net", "amazonaws.com", "glassdoor.com", "medium.com")
# The board's own links: <a href>/mailto, plus Ashby's publicWebsite and Greenhouse's logo href in the page JSON.
LINKED = re.compile(r"""<a\s[^>]*href=["'](?:https?://|mailto:[^@"'\s]+@)([a-z0-9.-]+)"""
                    r'''|"(?:publicWebsite|href)":"https?:(?:\\?/){2}([a-z0-9.-]+)''', re.I)
OWNER_FIELDS = (
    re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S),
    re.compile(r"<meta[^>]+property=[\"']og:(?:site_name|title)[\"'][^>]+content=[\"']([^\"']+)", re.I),
)


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", html.unescape(name).lower()) if t not in NOISE]


def _domain(url: str) -> str:
    host = (urlparse(url if "//" in url else f"//{url}").hostname or "").removeprefix("www.")
    parts = host.split(".")
    # shortcut: two-letter-TLD second levels (co.uk, com.tr, com.au); a public-suffix list if this misjudges
    keep = 3 if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "ac", "gov", "edu"} else 2
    return ".".join(parts[-keep:]) if host else ""


def names_match(owner: str, company: str) -> bool:
    """Same company name once legal forms and "Jobs at" are removed ("Jobs at Kentik" = "Kentik Technologies Inc.")."""
    a, b = _tokens(owner), _tokens(company)
    return bool(a and b) and (a == b or "".join(a) == "".join(b))


def owner_names(page: str) -> list[str]:
    names = []
    for rx in OWNER_FIELDS:
        for raw in rx.findall(page):
            names += [p.strip() for p in re.split(r"\s+[|\-–—:]\s+", html.unescape(raw).strip()) if p.strip()]
    return names


def _mentions(page: str, site: str) -> bool:
    return bool(re.search(rf"(?:https?://|[\"'/.@])(?:www\.)?{re.escape(site)}\b", page))


def linked_domains(page: str) -> set[str]:
    """Company domains the board links to as its own (website, apply email), minus ATS/social/CDN hosts."""
    return {d for m in LINKED.finditer(page) for h in m.groups() if h and (d := _domain(h)) and not d.endswith(NOT_OWNERS)}


def _same_owner(session: requests.Session, domain: str, site: str) -> bool | None:
    """Is a domain the board links to still our company (spotifyjobs.com, railway.app -> railway.com)?"""
    ours, theirs = site.split(".")[0], domain.split(".")[0]
    if theirs.startswith(ours) and theirs[len(ours):] in NOISE:  # bandlab -> bandlabtechnologies.com
        return True
    resp = _get(session, f"https://{domain}")
    return None if resp is None else _domain(resp.url) == site or _mentions(resp.text, site)


def _get(session: requests.Session, url: str):
    try:
        return _request(session, url, retries=2, timeout=7)
    except requests.RequestException:
        return None


def owns(session: requests.Session, ats: str, board: str, company: str, website: str = "",
         aliases: list[str] | None = None, raise_on_limit: bool = False) -> bool | None:
    """True/False when the landing page answers; None when it couldn't be read (retry later)."""
    template = LANDING.get(ats)
    if not template:
        return False
    if ats in {"workday", "comeet"} or (ats == "dover" and re.fullmatch(r"[0-9a-f-]{36}", board)):
        url = _more_ats.landing(ats, board)
    else:
        url = template.format(b=quote(board, safe=""))
    resp = _get(session, url)
    if raise_on_limit and resp is not None and resp.status_code == 429:
        raise requests.HTTPError("rate limited", response=resp)
    if resp is None or resp.status_code == 429 or resp.status_code >= 500:
        return None
    site, final = _domain(website), _domain(resp.url)
    wanted = "".join(_tokens(company))
    if final and not any(final.endswith(d) for d in ATS_DOMAINS):
        # The board forwards to a careers site: the company that set up the redirect owns the board.
        labels = {re.sub(r"[^a-z0-9]", "", x) for x in (urlparse(resp.url).hostname or "").lower().split(".")}
        return final == site or (len(wanted) >= 4 and wanted in labels)  # careers.godaddy, not prismanet.nl
    if not resp.ok:
        return False
    page = resp.text
    if DEMO.search(page):
        return False
    if ats == "pinpoint":  # demo accounts look real on the landing page; their postings don't
        postings = _get(session, f"https://{quote(board, safe='')}.pinpointhq.com/postings.json")
        if raise_on_limit and postings is not None and postings.status_code == 429:
            raise requests.HTTPError("rate limited", response=postings)
        if postings is None or postings.status_code == 429:
            return None
        if DEMO.search(postings.text):
            return False
    if site and not _mentions(page, site):
        if ats == "personio":  # its landing page links nobody; the postings carry the employer's domain
            feed = _get(session, f"https://{quote(board, safe='')}.jobs.personio.de/xml")
            page += feed.text if feed is not None and feed.ok else ""
        if not _mentions(page, site) and (foreign := linked_domains(page)):
            unknown = False
            for domain in sorted(foreign):
                same = _same_owner(session, domain, site)
                if same:
                    break
                unknown |= same is None
            else:  # a same-name company: the board links its own, different domain
                return None if unknown else False
    names = [n for n in owner_names(page) if _tokens(n)]
    if any(names_match(name, target) for name in names for target in [company, *(aliases or [])]):
        return True
    if site and _mentions(page, site):
        return True
    # A page that names nobody (a script-rendered board): only an exact, distinctive slug counts.
    return not names and len(wanted) >= 6 and re.sub(r"[^a-z0-9]", "", board.lower()) == wanted
