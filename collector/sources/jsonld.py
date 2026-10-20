"""Shared parser for schema.org Event data embedded as JSON-LD in web pages."""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..models import RawEvent
from ..normalize import country_code, name_matches

EVENT_TYPES = {"Event", "MusicEvent", "Festival", "SocialEvent", "TheaterEvent"}

_STATUS = {
    "eventcancelled": "cancelled", "eventpostponed": "postponed", "eventrescheduled": "rescheduled",
}
_AVAILABILITY = {
    "instock": "onsale", "limitedavailability": "onsale", "onlineonly": "onsale",
    "soldout": "offsale", "outofstock": "offsale", "discontinued": "offsale",
    "preorder": "announced", "presale": "announced",
}


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "subEvent", "event", "events"):
            if key in node:
                yield from _walk(node[key])


def extract_events(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _walk(data):
            types = obj.get("@type")
            types = set(types) if isinstance(types, list) else {types}
            if types & EVENT_TYPES and id(obj) not in seen:
                seen.add(id(obj))
                found.append(obj)
    return found


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _tail(v) -> str:
    return re.sub(r".*/", "", str(v or "")).lower()


def _name(v) -> str | None:
    v = _first(v)
    if isinstance(v, dict):
        v = v.get("name")
    return str(v).strip() if v else None


def to_raw_event(obj: dict, source: str, artist_name: str, artist_names: list[str], page_url: str,
                 require_name_in_title: bool = False) -> RawEvent | None:
    performers = obj.get("performer")
    performers = performers if isinstance(performers, list) else [performers] if performers else []
    performer_names = [_name(p) for p in performers if _name(p)]
    title = _name(obj.get("name"))
    if performer_names:
        if not any(name_matches(artist_names, p) for p in performer_names):
            return None
    elif require_name_in_title and not name_matches(artist_names, title):
        return None

    start = str(obj.get("startDate") or "")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?", start)
    date, time = (m.group(1), m.group(2)) if m else (None, None)

    location = _first(obj.get("location")) or {}
    if not isinstance(location, dict):
        location = {}
    address = location.get("address") or {}
    city = country = None
    if isinstance(address, dict):
        city = address.get("addressLocality")
        country = country_code(_name(address.get("addressCountry")))

    offers = _first(obj.get("offers")) or {}
    if not isinstance(offers, dict):
        offers = {}
    status = _STATUS.get(_tail(obj.get("eventStatus"))) or _AVAILABILITY.get(_tail(offers.get("availability")))
    if status is None and date:
        status = "announced"
    price = offers.get("lowPrice", offers.get("price"))
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price = None

    return RawEvent(
        source=source,
        artist=artist_name,
        url=offers.get("url") or obj.get("url") or page_url,
        date=date,
        time=time,
        city=str(city).strip() if city else None,
        country=country,
        venue=_name(location.get("name")),
        title=title,
        status=status,
        onsale_date=offers.get("validFrom"),
        price_min=price,
        currency=offers.get("priceCurrency") if price is not None else None,
        source_id=f"{source}:{obj['@id']}" if obj.get("@id") else None,
    )
