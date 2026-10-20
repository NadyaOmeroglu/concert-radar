"""Reading config/artists.yaml and config/regions.yaml (and writing back found Ticketmaster ids)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import Artist, City, Regions
from .normalize import country_code


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def load_artists(path: Path) -> list[Artist]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    artists, seen = [], set()
    for item in data.get("artists") or []:
        name = _text(item.get("name"))
        if not name:
            continue
        if name.lower() in seen:
            raise ValueError(f"Артист указан дважды: {name}")
        seen.add(name.lower())
        aliases = [str(a) for a in (item.get("aliases") or []) if a not in (None, "")]
        artists.append(Artist(
            name=name,
            aliases=aliases or [name],
            official_tour_url=_text(item.get("official_tour_url")),
            ticketmaster_id=_text(item.get("ticketmaster_id")),
            platinumlist_url=_text(item.get("platinumlist_url")),
            selectors=item.get("selectors") or None,
            active=item.get("active", True) is not False,
        ))
    return artists


def load_regions(path: Path) -> Regions:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    countries = {country_code(c) for c in (data.get("schengen") or []) + (data.get("extra_countries") or [])}
    cities = {}
    for code, items in (data.get("cities") or {}).items():
        cities[country_code(code)] = [
            City(name=str(c["name"]), aliases=[str(a) for a in c.get("aliases") or []]) for c in items or []
        ]
    return Regions(countries={c for c in countries if c}, cities=cities)


_NAME_LINE = re.compile(r'^\s*-\s*name:\s*["\']?(.*?)["\']?\s*$')
_TM_LINE = re.compile(r"^(\s*)ticketmaster_id:.*$")


def save_ticketmaster_ids(path: Path, ids: dict[str, str]) -> None:
    """Fill ticketmaster_id lines in place so comments and layout of artists.yaml stay intact."""
    if not ids:
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    current = None
    for i, line in enumerate(lines):
        m = _NAME_LINE.match(line)
        if m:
            current = m.group(1)
            continue
        m = _TM_LINE.match(line)
        if m and current in ids:
            lines[i] = f'{m.group(1)}ticketmaster_id: "{ids[current]}"\n'
    path.write_text("".join(lines), encoding="utf-8")
