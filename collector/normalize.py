"""Normalisation, region filtering and de-duplication of events."""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata

from .models import RawEvent, Regions

log = logging.getLogger(__name__)

SOURCE_PRIORITY = {"official": 0, "ticketmaster": 1, "platinumlist": 2}
STATUSES = {"onsale", "offsale", "cancelled", "postponed", "rescheduled", "announced"}

_SPECIAL = str.maketrans({"ß": "ss", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ł": "l", "Ł": "l",
                          "đ": "d", "Đ": "d", "þ": "th", "ð": "d", "œ": "oe", "ı": "i"})


def slug(text: str | None) -> str:
    """'München' -> 'munchen', 'P!nk' -> 'pink', 'ROSÉ' -> 'rose'."""
    if not text:
        return ""
    text = re.sub(r"(?<=[A-Za-z])!(?=[A-Za-z])", "i", str(text))  # P!nk -> Pink
    text = unicodedata.normalize("NFKD", text.translate(_SPECIAL))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


# Local-language city names -> English slug, so that "München" and "Munich" are the same city.
CITY_SLUGS = {
    "munchen": "munich", "koln": "cologne", "wien": "vienna", "praha": "prague", "roma": "rome",
    "milano": "milan", "firenze": "florence", "napoli": "naples", "torino": "turin",
    "venezia": "venice", "genova": "genoa", "lisboa": "lisbon", "kobenhavn": "copenhagen",
    "bruxelles": "brussels", "brussel": "brussels", "antwerpen": "antwerp", "gent": "ghent",
    "warszawa": "warsaw", "bucuresti": "bucharest", "sofiya": "sofia", "athina": "athens",
    "athinai": "athens", "s-gravenhage": "the-hague", "den-haag": "the-hague", "geneve": "geneva",
    "genf": "geneva", "luzern": "lucerne", "goteborg": "gothenburg", "nurnberg": "nuremberg",
    "frankfurt-am-main": "frankfurt", "helsingfors": "helsinki", "sevilla": "seville",
    "zaragoza": "zaragoza", "lyon": "lyon", "marseille": "marseille", "vilnius": "vilnius",
    "riga": "riga", "tallinn": "tallinn", "ljubljana": "ljubljana", "zagreb": "zagreb",
    "bratislava": "bratislava", "budapest": "budapest", "krakow": "krakow", "wroclaw": "wroclaw",
    "dubayy": "dubai", "abu-zabi": "abu-dhabi", "ar-riyad": "riyadh", "jiddah": "jeddah",
    "jedda": "jeddah", "masqat": "muscat",
}

COUNTRY_NAMES = {
    "austria": "AT", "osterreich": "AT", "belgium": "BE", "belgique": "BE", "belgie": "BE",
    "bulgaria": "BG", "croatia": "HR", "hrvatska": "HR", "czech-republic": "CZ", "czechia": "CZ",
    "denmark": "DK", "danmark": "DK", "estonia": "EE", "finland": "FI", "suomi": "FI",
    "france": "FR", "germany": "DE", "deutschland": "DE", "greece": "GR", "hungary": "HU",
    "magyarorszag": "HU", "iceland": "IS", "italy": "IT", "italia": "IT", "latvia": "LV",
    "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU", "malta": "MT",
    "netherlands": "NL", "the-netherlands": "NL", "nederland": "NL", "holland": "NL",
    "norway": "NO", "norge": "NO", "poland": "PL", "polska": "PL", "portugal": "PT",
    "romania": "RO", "slovakia": "SK", "slovenia": "SI", "spain": "ES", "espana": "ES",
    "sweden": "SE", "sverige": "SE", "switzerland": "CH", "schweiz": "CH", "suisse": "CH",
    "united-arab-emirates": "AE", "uae": "AE", "saudi-arabia": "SA", "kingdom-of-saudi-arabia": "SA",
    "ksa": "SA", "oman": "OM", "united-kingdom": "GB", "great-britain": "GB", "uk": "GB",
    "ireland": "IE", "cyprus": "CY", "united-states": "US", "usa": "US", "canada": "CA",
}


def city_key(city: str | None) -> str:
    s = slug(city)
    return CITY_SLUGS.get(s, s)


def country_code(value) -> str | None:
    if value is None or value == "":
        return None
    if value is False:  # YAML reads a bare NO (Norway) as False
        return "NO"
    text = str(value).strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return COUNTRY_NAMES.get(slug(text))


def region_city(regions: Regions, country: str | None, city: str | None) -> str | None:
    """Return the city name to show if the event is inside our regions, otherwise None."""
    if not country or not city:
        return None
    if country in regions.countries:
        return city
    for c in regions.cities.get(country, []):
        keys = {city_key(c.name), *(city_key(a) for a in c.aliases)}
        if city_key(city) in keys:
            return c.name
    return None


def event_id(artist: str, date: str, city: str) -> str:
    return hashlib.sha1(f"{slug(artist)}|{date}|{city_key(city)}".encode()).hexdigest()


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def to_event(raw: RawEvent, regions: Regions) -> dict | None:
    """Validate a raw event and convert it to the public event model; None if it must be skipped."""
    where = f"{raw.source}/{raw.artist} {raw.date} {raw.city},{raw.country}"
    if not raw.url:
        log.info("skip (no source link): %s", where)
        return None
    if not raw.date or not _DATE.match(raw.date):
        log.info("skip (no date): %s", where)
        return None
    if not raw.country or not raw.city:
        log.info("skip (no country/city): %s", where)
        return None
    shown_city = region_city(regions, raw.country, raw.city)
    if shown_city is None:
        return None
    return {
        "id": event_id(raw.artist, raw.date, shown_city),
        "artist": raw.artist,
        "title": raw.title,
        "date": raw.date,
        "time": raw.time,
        "timezone": raw.timezone,
        "city": shown_city,
        "country": raw.country,
        "venue": raw.venue,
        "status": raw.status if raw.status in STATUSES else None,
        "onsale_date": raw.onsale_date,
        "price_min": raw.price_min,
        "currency": raw.currency,
        "ticket_urls": [{"source": raw.source, "url": raw.url}],
        "sources": [raw.source],
        "source_ids": [raw.source_id] if raw.source_id else [],
    }


MERGED_FIELDS = ("title", "time", "timezone", "city", "venue", "status", "onsale_date", "price_min", "currency")


def merge_events(events: list[dict]) -> list[dict]:
    """One artist + one date + one city = one event. Higher-priority sources win; links are all kept."""
    groups: dict[str, list[dict]] = {}
    for e in events:
        groups.setdefault(e["id"], []).append(e)
    merged = []
    for group in groups.values():
        group.sort(key=lambda e: (SOURCE_PRIORITY.get(e["sources"][0], 99), is_add_on(e.get("title"))))
        base = dict(group[0])
        base["ticket_urls"], base["sources"], base["source_ids"] = [], [], []
        for e in group:
            for f in MERGED_FIELDS:
                if base.get(f) is None and e.get(f) is not None:
                    base[f] = e[f]
            # "announced" only says the show exists; a concrete sales status from any source is better.
            if base.get("status") == "announced" and e.get("status") not in (None, "announced"):
                base["status"] = e["status"]
            for t in e["ticket_urls"]:
                if t["url"] not in {x["url"] for x in base["ticket_urls"]}:
                    base["ticket_urls"].append(t)
            for s in e["sources"]:
                if s not in base["sources"]:
                    base["sources"].append(s)
            for sid in e["source_ids"]:
                if sid not in base["source_ids"]:
                    base["source_ids"].append(sid)
        merged.append(base)
    merged.sort(key=lambda e: (e["date"], e["time"] or "", e["artist"]))
    return merged


_ADD_ON = re.compile(
    r"\b(box seat|suite|vip|hospitality|lounge|package|upgrade|premium|meet (and|&) greet|early entry)\b", re.IGNORECASE)


def is_add_on(title: str | None) -> bool:
    """VIP / box seat / hospitality listings: real tickets, but not the main sale page."""
    return bool(title and _ADD_ON.search(title))


_TRIBUTE = re.compile(
    r"\b(tribute|candlelight|cover band|covers|homage|hommage|the music of|songs of|"
    r"a night of|symphonic|orchestra plays)\b", re.IGNORECASE)


def looks_like_tribute(title: str | None) -> bool:
    return bool(title and _TRIBUTE.search(title))


def name_matches(artist_names: list[str], text: str | None) -> bool:
    """True if one of the artist's names appears as whole words in text."""
    t = f"-{slug(text)}-"
    return any(slug(n) and f"-{slug(n)}-" in t for n in artist_names)
