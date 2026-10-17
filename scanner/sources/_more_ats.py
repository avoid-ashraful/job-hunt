"""Public boards with POST lists or job data embedded in their HTML."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import quote, urljoin, urlsplit

NAMES = {"workday", "rippling", "jazzhr", "homerun", "dover", "comeet", "factorial"}


def canonical(ats: str, board: str) -> str:
    """Our board form; freehire's job ids use host/path forms for some of these ATSs."""
    if ats == "workday":
        if m := re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/]+)", board, re.I):
            return f"{m[1].lower()}|{m[2].lower()}|{m[3]}"
        if board.count("|") != 2:
            raise ValueError(f"invalid Workday board: {board}")
    elif ats == "comeet":
        board = board.replace("/", "|", 1) if "|" not in board else board
        if board.count("|") != 1:
            raise ValueError(f"invalid Comeet board: {board}")
    elif ats == "factorial" and "." in board:
        board = board.split(".", 1)[0]
    return board


def _workday(board: str) -> tuple[str, str, str]:
    tenant, wd, site = canonical("workday", board).split("|")
    return tenant, wd, site


def landing(ats: str, board: str) -> str:
    if ats == "workday":
        tenant, wd, site = _workday(board)
        if not re.fullmatch(r"[a-z0-9-]+", tenant) or not re.fullmatch(r"wd\d+", wd):
            raise ValueError("invalid Workday host")
        return f"https://{tenant}.{wd}.myworkdayjobs.com/{quote(site, safe='')}"
    if ats == "comeet":
        slug, uid = canonical(ats, board).split("|")
        return f"https://www.comeet.com/jobs/{quote(slug, safe='')}/{quote(uid, safe='')}"
    if ats == "dover":
        return f"https://app.dover.com/company/careers/{quote(board, safe='')}"
    raise ValueError(ats)


def base(ats: str, board: str) -> tuple[str, dict]:
    board = canonical(ats, board)
    slug = quote(board, safe="")
    if ats == "workday":
        tenant, wd, site = _workday(board)
        landing(ats, board)
        return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{quote(site, safe='')}/jobs", {}
    if ats == "rippling":
        return f"https://ats.rippling.com/api/v2/board/{slug}/jobs", {"page": 0, "pageSize": 100}
    if ats == "jazzhr":
        return f"https://{slug}.applytojob.com/apply", {}
    if ats == "homerun":
        return f"https://{slug}.homerun.co", {}
    if ats == "factorial":
        return f"https://{slug}.factorialhr.com", {}
    if ats == "comeet":
        return landing(ats, board), {}
    if ats == "dover":
        if re.fullmatch(r"[0-9a-f-]{36}", board):
            return f"https://app.dover.com/api/v1/careers-page/{slug}/jobs", {}
        return f"https://app.dover.com/api/v1/careers-page-slug/{slug}", {}
    raise ValueError(ats)


def _attrs(tag: str) -> dict:
    return {k: unescape(v) for k, _, v in re.findall(r"([\w-]+)\s*=\s*([\"'])(.*?)\2", tag, re.S)}


def _jsonld(text: str) -> dict:
    for block in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else [data, *(data.get("@graph") or [])]
        for node in nodes:
            if isinstance(node, dict) and "JobPosting" in node.get("@type", []):
                return node
    return {}


def _html_rows(text: str, ats: str, board: str) -> list[dict]:
    from scanner.sources.ats import plain

    if ats == "comeet":
        match = re.search(r"\bCOMPANY_POSITIONS_DATA\s*=\s*", text)
        data = json.JSONDecoder().raw_decode(text[match.end():])[0] if match else []
        return [{**r, "id": r["uid"], "title": r["name"],
                 "url": r.get("url_comeet_hosted_page"),
                 "isRemote": (r.get("location") or {}).get("is_remote", False),
                 "description": "\n".join((x.get("value") or "") for x in (r.get("custom_fields") or {}).get("details", [])),
                 "publishedAt": r.get("time_created")} for r in data]
    if ats == "homerun":
        match = re.search(r'<job-list\b[^>]*v-bind="([^"]+)"', text)
        data = json.loads(unescape(match[1]))["content"] if match else {}
        locations = {r["id"]: r["name"] for r in data.get("locations", [])}
        return [{**r, "id": urlsplit(r["url"]).path.strip("/"),
                 "location": locations.get(r.get("location_id"), "")} for r in data.get("vacancies", [])]
    rows = []
    locations = dict(re.findall(r"<option\b[^>]*value=['\"]([^'\"]+)['\"][^>]*>(.*?)</option>", text, re.S))
    for card in re.findall(r"<li\b[^>]*>.*?</li>", text, re.I | re.S):
        attrs = _attrs(card.split(">", 1)[0])
        if ats == "factorial":
            url = attrs.get("data-job-postings-url")
            title = re.search(r'<div\b[^>]*factorial__headingFontFamily[^>]*>(.*?)</div>', card, re.S)
            if not url or not title:
                continue
            rows.append({"id": urlsplit(url).path.rsplit("-", 1)[-1], "title": plain(title[1]), "url": url,
                         "location": plain(locations.get(attrs.get("data-location-id"), "")),
                         "isRemote": attrs.get("data-is-remote") == "true"})
        elif "list-group-item" in attrs.get("class", ""):
            link = re.search(r'<a\b[^>]*href=["\']([^"\']+/apply/([^/"\']+)[^"\']*)["\'][^>]*>(.*?)</a>', card, re.S)
            if link:
                loc = re.search(r'fa-map-marker[^<]*</i>\s*([^<]+)', card)
                rows.append({"id": link[2], "title": plain(link[3]), "url": link[1],
                             "location": plain(loc[1]) if loc else ""})
    return rows


def rows(session, ats: str, board: str, limit: int = 200) -> list[dict]:
    from scanner.sources.ats import _request, get, get_text

    url, params = base(ats, board)
    limit = min(200, max(1, limit))
    if ats in {"jazzhr", "homerun", "factorial", "comeet"}:
        return _html_rows(get_text(session, url), ats, board)[:limit]
    if ats == "workday":
        out = []
        for offset in range(0, limit, 20):
            response = _request(session, url, method="post",
                                json={"limit": min(20, limit - offset), "offset": offset,
                                      "searchText": "", "appliedFacets": {}})
            response.raise_for_status()
            data = response.json()
            page = data.get("jobPostings") or []
            out.extend({**r, "id": r["externalPath"].rstrip("/").rsplit("/", 1)[-1],
                        "url": landing(ats, board) + r["externalPath"], "location": r.get("locationsText", "")} for r in page)
            if not page or len(out) >= data.get("total", len(out)):
                break
        return out[:limit]
    if ats == "dover":
        data = get(session, url)
        if "results" not in data:
            board_id = data["id"]
            url = f"https://app.dover.com/api/v1/careers-page/{quote(board_id, safe='')}/jobs"
            data = get(session, url)
        out, fetched = [], 0
        while True:
            fetched += len(data.get("results", []))
            out.extend({**r, "url": f"https://app.dover.com/apply/{quote(board, safe='')}/{r['id']}",
                        "location": ", ".join(x.get("name", "") for x in r.get("locations", []))}
                       for r in data.get("results", []) if r.get("is_published") and not r.get("is_sample"))
            next_url = urljoin(url, data.get("next") or "")
            if not data.get("next") or fetched >= limit or not data.get("results") or urlsplit(next_url).netloc != "app.dover.com":
                return out[:limit]
            data = get(session, next_url)
    out, seen = [], {}
    for page in range((limit + 99) // 100):
        data = get(session, url, params={**params, "page": page, "pageSize": min(100, limit)})
        for r in data.get("items", []):
            if r["id"] in seen:
                seen[r["id"]]["locations"].extend(r.get("locations", []))
            else:
                item = {**r, "locations": list(r.get("locations", []))}
                seen[r["id"]] = item
                out.append(item)
        if page + 1 >= data.get("totalPages", 1):
            break
    for r in out:
        r["location"] = ", ".join(dict.fromkeys(x.get("name", "") for x in r["locations"]))
        r["isRemote"] = any(x.get("workplaceType") == "REMOTE" for x in r["locations"])
    return out


def enrich(session, ats: str, board: str, raw: dict) -> dict:
    from scanner.sources.ats import get, get_text, _is_remote

    raw = dict(raw)
    if ats == "workday":
        url, _ = base(ats, board)
        detail = get(session, url.removesuffix("/jobs") + raw["externalPath"])["jobPostingInfo"]
        raw.update(description=detail.get("jobDescription", ""), publishedAt=detail.get("startDate"),
                   location=", ".join(filter(None, [detail.get("location") or raw.get("location"), *(detail.get("additionalLocations") or [])])))
        raw["isRemote"] = "remote" in str(detail.get("remoteType", "")).lower() or "remote" in raw["location"].lower()
    elif ats == "rippling" and _is_remote(raw, raw["location"], {}):
        url, _ = base(ats, board)
        detail = get(session, url + "/" + quote(str(raw["id"]), safe=""))
        raw.update(description=detail.get("description", ""), publishedAt=detail.get("createdOn"))
    elif ats == "dover" and _is_remote(raw, raw["location"], {}):
        detail = get(session, f"https://app.dover.com/api/v1/inbound/application-portal-job/{quote(str(raw['id']), safe='')}")
        raw["description"] = detail.get("user_provided_description") or detail.get("description") or ""
        raw["publishedAt"] = detail.get("created")
    elif ats in {"jazzhr", "homerun", "factorial"}:
        text = get_text(session, raw["url"])
        detail = _jsonld(text)
        raw.update(description=detail.get("description", ""), datePosted=detail.get("datePosted"))
        locs = detail.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        location = ", ".join(dict.fromkeys(str(v) for loc in locs for k, v in (loc.get("address") or {}).items()
                                             if k in {"addressLocality", "addressRegion", "addressCountry"} and isinstance(v, str)))
        raw["location"] = raw.get("location") or location
        if detail.get("jobLocationType") == "TELECOMMUTE":
            raw["isRemote"] = True
        if ats == "homerun" and not raw["description"]:
            raw["description"] = "\n".join(re.findall(r'<div\b[^>]*class=["\']article["\'][^>]*>(.*?)</div>', text, re.S))
        if ats == "factorial":
            match = re.search(r'<div[^>]*class=["\'][^"\']*styledText[^"\']*["\'][^>]*>(.*?)</div>', text, re.S)
            if match:
                raw["description"] = match[1]
    return raw
