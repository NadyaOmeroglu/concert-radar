"""Platinumlist — main source for the UAE and the Gulf.

There is no public API (only a partner/affiliate feed by agreement), so we read public pages:
robots.txt is respected, at most one request every 2 seconds, and anti-bot pages are never bypassed.

Instead of one search per artist we read each city's concert listing once per run (a few pages)
and pick out our artists' events. Listing pages are configured in config/sources.yaml; an artist
can also have a direct `platinumlist_url` (e.g. an artist page).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from ..models import Artist, RawEvent, Regions, SourceNote, SourceUnavailable
from ..net import PoliteSession, RobotsUnreachable, looks_like_bot_challenge
from ..normalize import looks_like_tribute, name_matches
from .jsonld import extract_events, to_raw_event

log = logging.getLogger(__name__)

NAME = "platinumlist"
CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
DEFAULT_MAX_PAGES = 5

_session: PoliteSession | None = None
_cache: dict = {}  # listings already read during this run


def _http() -> PoliteSession:
    global _session
    if _session is None:
        _session = PoliteSession(min_interval=2.0, max_retries=1)
    return _session


def reset_cache() -> None:
    _cache.clear()


def _config() -> dict:
    try:
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    return data.get("platinumlist") or {}


def _get_page(http, url: str):
    """Returns the response, or None for a missing page. Raises SourceUnavailable when blocked."""
    try:
        allowed = http.allowed_by_robots(url)
    except RobotsUnreachable as exc:
        raise SourceUnavailable(f"Platinumlist is unreachable: {exc}") from None
    if not allowed:
        raise SourceUnavailable(f"robots.txt does not allow {url}")
    resp = http.get(url)
    if looks_like_bot_challenge(resp):
        raise SourceUnavailable(f"Platinumlist answered HTTP {resp.status_code} (anti-bot protection); not bypassing")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"Platinumlist HTTP {resp.status_code} for {url}")
    return resp


def _page_url(url: str, page: int) -> str:
    return url if page == 1 else f"{url}{'&' if '?' in url else '?'}page={page}"


_EVENT_LINK = re.compile(r"/event-tickets/\d+/[\w-]+", re.IGNORECASE)
MAX_EVENT_PAGES_PER_ARTIST = 10


def _event_links(html: str, base: str) -> list[tuple[str, str]]:
    """(absolute url, visible text) of links to event pages, for listings without JSON-LD."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        if _EVENT_LINK.search(a["href"]):
            links.append((urljoin(base, a["href"]).split("?")[0], a.get_text(" ", strip=True)))
    return links


def read_listings(http, listings: list[dict], max_pages: int) -> dict:
    """Everything on the listing pages: JSON-LD events and links to event pages."""
    key = tuple(l["url"] for l in listings)
    if key in _cache:
        return _cache[key]
    items, links, missing, seen = [], {}, [], set()
    for listing in listings:
        for page in range(1, max_pages + 1):
            url = _page_url(listing["url"], page)
            resp = _get_page(http, url)
            if resp is None:
                if page == 1:
                    missing.append(listing["url"])
                break
            fresh = 0
            for obj in extract_events(resp.text):
                ident = obj.get("url") or obj.get("@id") or (obj.get("name"), obj.get("startDate"))
                if ident in seen:
                    continue
                seen.add(ident)
                items.append((obj, listing, url))
                fresh += 1
            for href, text in _event_links(resp.text, url):
                if href not in links:
                    links[href] = (text, listing)
                    fresh += 1
                elif text and not links[href][0]:
                    links[href] = (text, listing)
            if not fresh:  # empty page or the site ignores ?page=
                break
    log.info("platinumlist: %d JSON-LD events and %d event links on listing pages; missing pages: %s",
             len(items), len(links), missing or "none")
    result = _cache[key] = {"items": items, "links": links, "missing": missing, "pages": {}}
    return result


def _event_page(http, data: dict, url: str) -> list[dict]:
    if url not in data["pages"]:
        resp = _get_page(http, url)
        data["pages"][url] = extract_events(resp.text) if resp is not None else []
    return data["pages"][url]


def _from_artist_page(http, artist: Artist) -> list[RawEvent]:
    resp = _get_page(http, artist.platinumlist_url)
    if resp is None:
        raise SourceNote(f"{artist.platinumlist_url} not found (404)")
    events = [e for obj in extract_events(resp.text)
              if (e := to_raw_event(obj, NAME, artist.name, artist.all_names(), artist.platinumlist_url,
                                    require_name_in_title=True))]
    return [e for e in events if not looks_like_tribute(e.title)]


def fetch(artist: Artist, regions: Regions, http=None, listings: list[dict] | None = None,
          max_pages: int | None = None) -> list[RawEvent]:
    http = http or _http()
    if artist.platinumlist_url:
        return _from_artist_page(http, artist)

    cfg = _config()
    listings = listings if listings is not None else cfg.get("listings") or []
    if not listings:
        raise SourceNote("platinumlist.listings is not configured")
    data = read_listings(http, listings, max_pages or cfg.get("max_pages") or DEFAULT_MAX_PAGES)
    if len(data["missing"]) == len(listings):
        raise SourceNote(f"no listing page could be opened (404): {', '.join(data['missing'])}")
    if not data["items"] and not data["links"]:
        raise SourceNote("listing pages contain no structured (JSON-LD) event data and no event links")

    found = list(data["items"])
    if not data["items"]:
        # Listing without JSON-LD: open only the event pages whose title or address mentions the artist.
        names = artist.all_names()
        matching = [(url, listing) for url, (text, listing) in data["links"].items()
                    if (name_matches(names, text) or name_matches(names, url.rsplit("/", 1)[-1]))
                    and not looks_like_tribute(text) and not looks_like_tribute(url.rsplit("/", 1)[-1].replace("-", " "))]
        for url, listing in matching[:MAX_EVENT_PAGES_PER_ARTIST]:
            objs = _event_page(http, data, url)
            if not objs:
                raise SourceNote(f"event page has no structured (JSON-LD) event data: {url}")
            found += [(obj, listing, url) for obj in objs]

    events = []
    for obj, listing, page_url in found:
        ev = to_raw_event(obj, NAME, artist.name, artist.all_names(), page_url, require_name_in_title=True)
        if not ev or looks_like_tribute(ev.title):
            continue
        # The listing belongs to one city (e.g. dubai.platinumlist.net) — use it when the event has no address.
        if not ev.city:
            ev.city, ev.country = listing.get("city"), listing.get("country")
        elif not ev.country and ev.city == listing.get("city"):
            ev.country = listing.get("country")
        events.append(ev)
    return events
