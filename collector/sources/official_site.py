"""Artists' official tour pages: JSON-LD first, hand-picked CSS selectors as a fallback."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Artist, RawEvent, Regions, SourceNote
from ..net import PoliteSession, RobotsUnreachable, looks_like_bot_challenge
from ..normalize import country_code
from .jsonld import extract_events, to_raw_event

NAME = "official"

# Third-party tour widgets. We only detect them; using their data needs a terms-of-service check first.
WIDGETS = {
    "Bandsintown": re.compile(r"widget\.bandsintown\.com|bit-widget", re.IGNORECASE),
    "Songkick": re.compile(r"widget-app\.songkick\.com|songkick-widget", re.IGNORECASE),
    "Seated": re.compile(r"widget\.seated\.com|seated-widget", re.IGNORECASE),
}

_session: PoliteSession | None = None


def _http() -> PoliteSession:
    global _session
    if _session is None:
        _session = PoliteSession(min_interval=2.0)
    return _session


def parse_with_selectors(html: str, artist: Artist, page_url: str) -> list[RawEvent]:
    """selectors in artists.yaml, e.g.
        selectors: {item: ".tour-row", date: ".date", date_format: "%d %b %Y",
                    city: ".city", country: ".country", venue: ".venue", link: "a.tickets"}
    'country' may also be a fixed code: {country_code: "FR"}.
    """
    sel = artist.selectors or {}
    soup = BeautifulSoup(html, "html.parser")
    events = []

    def text(node, key):
        if not sel.get(key):
            return None
        found = node.select_one(sel[key])
        return found.get_text(" ", strip=True) if found else None

    for node in soup.select(sel["item"]):
        raw_date = text(node, "date")
        date = None
        if raw_date:
            try:
                date = datetime.strptime(raw_date, sel.get("date_format", "%Y-%m-%d")).strftime("%Y-%m-%d")
            except ValueError:
                date = None
        link = node.select_one(sel["link"]) if sel.get("link") else None
        href = urljoin(page_url, link["href"]) if link is not None and link.get("href") else page_url
        events.append(RawEvent(
            source=NAME, artist=artist.name, url=href, date=date,
            city=text(node, "city"),
            country=sel.get("country_code") or country_code(text(node, "country")),
            venue=text(node, "venue"),
            status="announced" if date else None,
        ))
    return events


def fetch(artist: Artist, regions: Regions, http=None) -> list[RawEvent]:
    if not artist.official_tour_url:
        return []
    http = http or _http()
    url = artist.official_tour_url
    try:
        allowed = http.allowed_by_robots(url)
    except RobotsUnreachable as exc:
        raise SourceNote(f"{exc}; skipped") from None
    if not allowed:
        raise SourceNote(f"robots.txt does not allow {url}")
    resp = http.get(url)
    if looks_like_bot_challenge(resp):
        raise SourceNote(f"{url} answered HTTP {resp.status_code} (blocked); not bypassing")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
    html = resp.text

    if artist.selectors and artist.selectors.get("item"):
        return parse_with_selectors(html, artist, url)

    events = [e for obj in extract_events(html)
              if (e := to_raw_event(obj, NAME, artist.name, artist.all_names(), url))]
    if events:
        return events
    widgets = [name for name, rx in WIDGETS.items() if rx.search(html)]
    if widgets:
        raise SourceNote(f"{url}: no JSON-LD, uses {', '.join(widgets)} widget (not used without ToS check)")
    raise SourceNote(f"{url}: no JSON-LD events found (page may need JavaScript or custom selectors)")
