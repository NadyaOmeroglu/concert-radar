"""Platinumlist — main source for the UAE and the Gulf.

There is no public API (only a partner/affiliate feed by agreement), so we read public pages:
robots.txt is respected, at most one request every 2 seconds, and anti-bot pages are never bypassed.

The search URL lives in config/sources.yaml; an artist can also have a direct `platinumlist_url`.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

import yaml

from ..models import Artist, RawEvent, Regions, SourceNote, SourceUnavailable
from ..net import PoliteSession, RobotsUnreachable, looks_like_bot_challenge
from ..normalize import looks_like_tribute
from .jsonld import extract_events, to_raw_event

NAME = "platinumlist"
CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"

_session: PoliteSession | None = None


def _http() -> PoliteSession:
    global _session
    if _session is None:
        _session = PoliteSession(min_interval=2.0, max_retries=1)
    return _session


def _search_url() -> str | None:
    try:
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return None
    return ((data.get("platinumlist") or {}).get("search_url")) or None


def fetch(artist: Artist, regions: Regions, http=None, search_url: str | None = None) -> list[RawEvent]:
    http = http or _http()
    if artist.platinumlist_url:
        url = artist.platinumlist_url
    else:
        template = search_url or _search_url()
        if not template:
            raise SourceNote("platinumlist.search_url is not configured")
        url = template.format(query=quote_plus(artist.name))

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
        raise SourceNote(f"{url} not found (404)")
    if resp.status_code != 200:
        raise RuntimeError(f"Platinumlist HTTP {resp.status_code}")

    events = []
    for obj in extract_events(resp.text):
        ev = to_raw_event(obj, NAME, artist.name, artist.all_names(), url, require_name_in_title=True)
        if ev and not looks_like_tribute(ev.title):
            events.append(ev)
    return events
