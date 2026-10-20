"""Data models shared by the collector modules."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Artist:
    name: str
    aliases: list[str] = field(default_factory=list)
    official_tour_url: str | None = None
    ticketmaster_id: str | None = None
    platinumlist_url: str | None = None
    selectors: dict | None = None
    active: bool = True

    def all_names(self) -> list[str]:
        return [self.name, *[a for a in self.aliases if a != self.name]]


@dataclass
class City:
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Regions:
    # Countries where every city is allowed (Schengen + extra_countries).
    countries: set[str] = field(default_factory=set)
    # Countries where only the listed cities are allowed, e.g. {"AE": [City("Dubai"), ...]}.
    cities: dict[str, list[City]] = field(default_factory=dict)


@dataclass
class RawEvent:
    """One event exactly as a source reported it (after field mapping)."""
    source: str
    artist: str
    url: str
    date: str | None  # YYYY-MM-DD, local date of the event
    time: str | None = None  # HH:MM, local time
    timezone: str | None = None
    city: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2 when known
    venue: str | None = None
    title: str | None = None
    status: str | None = None
    onsale_date: str | None = None
    price_min: float | None = None
    currency: str | None = None
    source_id: str | None = None  # stable id inside the source, e.g. "ticketmaster:Z698xZ..."


class SourceUnavailable(Exception):
    """The whole source is unusable this run (no key, blocked, quota exhausted)."""


class ArtistNotFound(Exception):
    def __init__(self, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class AmbiguousArtist(Exception):
    def __init__(self, message: str, candidates: list[dict]):
        super().__init__(message)
        self.candidates = candidates


class SourceNote(Exception):
    """Not an error: the source had nothing usable for this artist (e.g. no structured data)."""
