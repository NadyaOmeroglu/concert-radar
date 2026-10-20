"""Ticketmaster Discovery API v2 — main source for Europe.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
Quota: 5000 calls/day; the docs quote both 2 and 5 requests/second, so we stay under 2/s.
"""
from __future__ import annotations

import os
import re

from ..models import AmbiguousArtist, Artist, ArtistNotFound, RawEvent, Regions, SourceUnavailable
from ..net import PoliteSession, redact
from ..normalize import slug

NAME = "ticketmaster"
BASE = "https://app.ticketmaster.com/discovery/v2"
PAGE_SIZE = 200
MAX_ITEMS = 1000  # deep paging limit: size * page must stay below 1000

_SKIP_TITLES = re.compile(r"\b(parking|parken|parkplatz|shuttle|bus to|camping|locker)\b", re.IGNORECASE)

_session: PoliteSession | None = None


def _http() -> PoliteSession:
    global _session
    if _session is None:
        _session = PoliteSession(min_interval=0.6)
    return _session


def _api_key() -> str:
    key = os.environ.get("TICKETMASTER_API_KEY", "").strip()
    if not key:
        raise SourceUnavailable("TICKETMASTER_API_KEY is not set")
    return key


def _get(http, path: str, params: dict) -> dict:
    key = _api_key()
    try:
        resp = http.get(f"{BASE}/{path}", params={**params, "apikey": key})
    except Exception as exc:  # network errors: report without leaking the key
        raise RuntimeError(redact(exc, key)) from None
    if resp.status_code == 401:
        raise SourceUnavailable("Ticketmaster rejected the API key (401)")
    if resp.status_code == 429:
        raise SourceUnavailable("Ticketmaster quota exceeded (429)")
    if resp.status_code != 200:
        raise RuntimeError(f"Ticketmaster HTTP {resp.status_code} on {path}")
    return resp.json()


def resolve_attraction(artist: Artist, http) -> str:
    """Find the Ticketmaster attraction id. Exact name match only — never guess."""
    data = _get(http, "attractions.json",
                {"keyword": artist.name, "classificationName": "music", "size": 50})
    attractions = (data.get("_embedded") or {}).get("attractions") or []
    wanted = {slug(n) for n in artist.all_names()}
    exact = [a for a in attractions if slug(a.get("name")) in wanted]

    def describe(a):
        return {"id": a.get("id"), "name": a.get("name"), "url": a.get("url"),
                "upcomingEvents": (a.get("upcomingEvents") or {}).get("_total")}

    if len(exact) == 1:
        return exact[0]["id"]
    if not exact:
        raise ArtistNotFound(f"No exact Ticketmaster match for {artist.name}",
                             [describe(a) for a in attractions[:5]])
    raise AmbiguousArtist(f"{len(exact)} Ticketmaster attractions named {artist.name}",
                          [describe(a) for a in exact])


def map_event(item: dict, artist: Artist) -> RawEvent | None:
    title = item.get("name")
    if _SKIP_TITLES.search(title or ""):
        return None
    segments = {(c.get("segment") or {}).get("name") for c in item.get("classifications") or []}
    if segments and "Music" not in segments:
        return None
    dates = item.get("dates") or {}
    start = dates.get("start") or {}
    venues = (item.get("_embedded") or {}).get("venues") or [{}]
    venue = venues[0]
    local_time = start.get("localTime")
    prices = item.get("priceRanges") or []
    status = (dates.get("status") or {}).get("code")
    if status == "canceled":
        status = "cancelled"
    return RawEvent(
        source=NAME,
        artist=artist.name,
        url=item.get("url"),
        date=start.get("localDate"),
        time=local_time[:5] if local_time and not start.get("timeTBA") else None,
        timezone=dates.get("timezone") or venue.get("timezone"),
        city=(venue.get("city") or {}).get("name"),
        country=(venue.get("country") or {}).get("countryCode"),
        venue=venue.get("name"),
        title=title,
        status=status,
        onsale_date=((item.get("sales") or {}).get("public") or {}).get("startDateTime"),
        price_min=prices[0].get("min") if prices else None,
        currency=prices[0].get("currency") if prices else None,
        source_id=f"{NAME}:{item['id']}" if item.get("id") else None,
    )


def fetch(artist: Artist, regions: Regions, http=None) -> list[RawEvent]:
    """All upcoming events for the artist worldwide; region filtering happens later.

    One request per artist (plus paging) is far fewer calls than one request per country.
    Side effect: sets artist.ticketmaster_id when it was found for the first time.
    """
    http = http or _http()
    if not artist.ticketmaster_id:
        artist.ticketmaster_id = resolve_attraction(artist, http)
    events, page = [], 0
    while (page + 1) * PAGE_SIZE <= MAX_ITEMS:
        data = _get(http, "events.json", {"attractionId": artist.ticketmaster_id, "size": PAGE_SIZE,
                                          "page": page, "sort": "date,asc"})
        for item in (data.get("_embedded") or {}).get("events") or []:
            ev = map_event(item, artist)
            if ev:
                events.append(ev)
        total_pages = (data.get("page") or {}).get("totalPages", 0)
        page += 1
        if page >= total_pages:
            break
    return events
