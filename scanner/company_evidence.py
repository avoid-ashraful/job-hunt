"""Bounded public company lookups; posting locations never establish headquarters."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

import requests

from scanner.company_profile import COUNTRIES, DEFAULT_PATTERNS, PLACE
from scanner.models import Job

TIMEOUT = 5
COUNTRY_NAMES = {name: code for name, code in COUNTRIES.items() if name not in {
    "bengaluru", "bangalore", "pune", "hyderabad", "noida", "gurugram", "gurgaon", "chennai",
    "mumbai", "kolkata", "ahmedabad", "delhi", "dhaka", "chattogram", "chittagong", "new york",
    "san francisco", "london", "berlin", "jacksonville", "boston", "nashville", "amsterdam",
    "hamburg", "toronto", "sydney", "zurich", "dubai",
}}
COUNTRY_RX = re.compile(r"\b(" + "|".join(re.escape(x) for x in sorted(COUNTRY_NAMES, key=len, reverse=True)) + r")\b", re.I)


def country_code(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("addressCountry") or value.get("country") or value.get("name") or value.get("code")
        if isinstance(value, dict):
            return country_code(value)
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value in set(COUNTRY_NAMES.values()):
        return value
    matches = {COUNTRY_NAMES[m.group().lower()] for m in COUNTRY_RX.finditer(value)}
    return matches.pop() if len(matches) == 1 else None


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.scripts = []
        self.text = []
        self.address = []
        self._script = None
        self._hidden = 0
        self._address = 0
        self._link = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._hidden += 1
            if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
                self._script = []
        if tag in {"footer", "address"}:
            self._address += 1
        if tag == "a":
            self._link = [attrs.get("href", ""), ""]
        if tag in {"p", "br", "div", "li", "address", "footer"}:
            self.text.append("\n")
            if self._address:
                self.address.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)
            if self._script is not None:
                self.scripts.append("".join(self._script))
                self._script = None
        if tag in {"footer", "address"}:
            self._address = max(0, self._address - 1)
        if tag == "a" and self._link is not None:
            self.links.append(tuple(self._link))
            self._link = None

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        if self._hidden:
            return
        self.text.append(data)
        if self._address:
            self.address.append(data)
        if self._link is not None:
            self._link[1] += data


def website_country(text: str, url: str, company: str) -> tuple[str | None, str]:
    from scanner.board_owner import names_match

    page = Page(text)
    codes = set()
    for script in page.scripts:
        try:
            data = json.loads(script)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else [data]
        nodes += [child for node in nodes if isinstance(node, dict) and isinstance(node.get("@graph"), list)
                  for child in node["@graph"] if isinstance(child, dict)]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list):
                continue
            if not any(t in {"Organization", "Corporation", "LocalBusiness"} for t in types):
                continue
            if node.get("name") and (not isinstance(node["name"], str) or not names_match(node["name"], company)):
                continue
            addresses = node.get("address", [])
            for address in addresses if isinstance(addresses, list) else [addresses]:
                if isinstance(address, dict) and (code := country_code(address.get("addressCountry"))):
                    codes.add(code)
    if len(codes) == 1:
        return codes.pop(), f"Organization.address.addressCountry: {url}"
    text = "".join(page.text)
    for key in ("headquarters", "sole_office"):
        for match in re.finditer(DEFAULT_PATTERNS[key], text, re.I):
            first = re.split(r"\b(?:and|with|but|while|which|offices?)\b", match.group("place"), maxsplit=1, flags=re.I)[0]
            if place := PLACE.search(first):
                return COUNTRIES[place.group().lower()], f"{match.group(0)[:130]} ({url})"
    address = "".join(page.address)
    if re.search(r"/(?:imprint|impressum|contact)(?:[/.\-]|$)", urlparse(url).path, re.I):
        address += "\n" + text
    # A country in navigation or marketing prose is not a postal address.
    matches = []
    for line in address.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if re.search(r"\b(?:jobs?|hiring|remote|candidates?|vacancies|openings?)\b", line, re.I):
            continue
        postal = re.search(r"\b\d{1,6}\s+[\w .'-]{1,60}\b(?:street|st|road|rd|avenue|ave|lane|drive|boulevard|strasse|straße)\b", line, re.I)
        for match in COUNTRY_RX.finditer(line):
            city = re.fullmatch(r"(?:\d{4,6}\s+)?[A-Z][\w .'-]{1,40},\s*", line[:match.start()])
            if postal or (city and not line[match.end():].strip(" .")):
                matches.append((COUNTRY_NAMES[match.group().lower()], line))
    if len({code for code, _ in matches}) == 1:
        return matches[0][0], f"company postal address: {matches[0][1][:130]} ({url})"
    return None, "no company HQ evidence"


def _fetch(session, url):
    try:
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response
    except requests.RequestException:
        return None


def _company_metadata(data: dict) -> tuple[str | None, str, str]:
    if isinstance(data.get("offer"), dict):
        data = data["offer"]
    company = data.get("company") or data.get("account") or {}
    if not isinstance(company, dict):
        company = {}
    location = company.get("location") or company.get("address") or {}
    code = country_code(company.get("hq_country") or company.get("country") or
                        data.get("company_country") or data.get("company_country_code") or location)
    website = company.get("website") or company.get("website_url") or data.get("company_website") or ""
    return code, "ATS company location", website if isinstance(website, str) else ""


def lookup(job: Job) -> tuple[str | None, str]:
    from scanner.board_owner import AGGREGATORS, ATS_DOMAINS, LANDING, _domain, _tokens, names_match
    from scanner.liveness import _ats_identity

    with requests.Session() as session:
        session.headers["User-Agent"] = "job-hunt-scanner (personal use)"
        metadata = job.ats_metadata or {}
        website = metadata.get("company_website") or metadata.get("website") or ""
        if website and _domain(website) in ATS_DOMAINS + AGGREGATORS:  # a seed's "website" can be a job link
            website = ""
        identity = _ats_identity(job)
        parts = job.id.split(":", 2)
        if len(parts) == 3 and parts[0] in {"recruitee", "lever_eu", "personio"} and parts[1]:
            identity = tuple(parts)
        landing = job.url
        if identity:
            ats, board, ext = identity
            if ats in {"workday", "comeet"}:
                from scanner.sources._more_ats import landing as board_landing
                try:
                    landing = board_landing(ats, board)
                except ValueError:
                    pass
            else:
                landing = LANDING.get(ats, "").format(b=quote(board, safe="")) or landing
            board = quote(board, safe="")
            endpoint = {
                "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{board}",
                "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{quote(ext, safe='')}",
                "workable": f"https://apply.workable.com/api/v1/widget/accounts/{board}",
                "recruitee": f"https://{board}.recruitee.com/api/offers/{quote(ext, safe='')}",
            }.get(ats)
            if endpoint and (response := _fetch(session, endpoint)) is not None:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                if isinstance(data, dict):
                    code, evidence, site = _company_metadata(data)
                    if code:
                        return code, f"{evidence}: {endpoint}"
                    website = website or site
                    if ats == "workable":
                        code = country_code(data.get("location"))
                        website = website or data.get("website", "")
                        if code:
                            return code, f"ATS account location: {endpoint}"
                    if ats == "greenhouse" and data.get("content"):
                        code, evidence = website_country(data["content"], endpoint, job.company)
                        if code:
                            return code, evidence
        wanted = "".join(_tokens(job.company))

        def employer_url(url):
            host = (urlparse(url).hostname or "").lower()
            return (urlparse(url).scheme in {"http", "https"} and
                    not any(host == d or host.endswith("." + d) for d in ATS_DOMAINS) and
                    len(wanted) >= 4 and wanted in re.sub(r"[^a-z0-9]", "", host))

        if job.source == "freehire":
            slug = metadata.get("freehire_company_slug") or job.company_key
            endpoint = f"https://freehire.me/api/v1/companies/{quote(slug, safe='')}?limit=1"
            if (response := _fetch(session, endpoint)) is not None:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                data = data.get("data") if isinstance(data, dict) else None
                company = data.get("company") if isinstance(data, dict) else None
                if (isinstance(company, dict) and isinstance(company.get("name"), str) and
                        names_match(company["name"], job.company)):
                    if code := country_code(company.get("hq_country")):
                        return code, f"freehire company hq_country: {endpoint}"
                    info = company.get("company_info")
                    site = info.get("website") if isinstance(info, dict) else None
                    if isinstance(site, str) and site.strip():
                        site = site.strip()
                        if "://" not in site:
                            site = "https://" + site
                        # Aggregated company records can contain another employer's website.
                        if employer_url(site):
                            website = website or site

        if not website and employer_url(job.url):
            parsed = urlparse(job.url)
            website = f"{parsed.scheme}://{parsed.netloc}/"
        if not website:
            response = _fetch(session, landing)
            if response is not None:
                for href, label in Page(response.text).links:
                    link = urljoin(response.url, href)
                    if employer_url(link):
                        website = link
                        break
        if not website or urlparse(website).scheme not in {"http", "https"}:
            return None, "no company website found"
        response = _fetch(session, website)
        if response is None:
            return None, "company website unavailable"
        code, evidence = website_country(response.text, response.url, job.company)
        if code:
            return code, evidence
        links = []
        for href, label in Page(response.text).links:
            link = urljoin(response.url, href)
            if urlparse(link).netloc != urlparse(response.url).netloc:
                continue
            if match := re.search(r"imprint|impressum|contact|about", href + " " + label, re.I):
                links.append((0 if match.group().lower() in {"imprint", "impressum"} else 1, link))
        if links:
            response = _fetch(session, min(links)[1])
            if response is not None:
                return website_country(response.text, response.url, job.company)
        return None, "no company HQ evidence"
