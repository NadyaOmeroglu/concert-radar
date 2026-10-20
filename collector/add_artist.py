"""Append an artist to config/artists.yaml (used by the "Add artist" workflow).

Usage: ARTIST_NAME="Coldplay" OFFICIAL_TOUR_URL="https://..." python -m collector.add_artist
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import load_artists
from .normalize import slug

ROOT = Path(__file__).resolve().parents[1]


def add_artist(path: Path, name: str, tour_url: str = "") -> str:
    name, tour_url = " ".join((name or "").split()), (tour_url or "").strip()
    if not name:
        raise ValueError("Artist name is empty")
    if tour_url and not tour_url.startswith(("https://", "http://")):
        raise ValueError(f"Tour page must start with https:// — got: {tour_url}")
    for a in load_artists(path):
        if slug(name) in {slug(n) for n in a.all_names()}:
            raise ValueError(f"{name} is already in the list (as {a.name})")
    q = lambda s: json.dumps(s, ensure_ascii=False)  # a JSON string is a valid quoted YAML string
    block = (f"\n  - name: {q(name)}\n    aliases: [{q(name)}]\n"
             f"    official_tour_url: {q(tour_url) if tour_url else 'null'}\n"
             f"    ticketmaster_id: null\n    active: true\n")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    load_artists(path)  # make sure the file is still valid
    return name


def main() -> int:
    try:
        name = add_artist(ROOT / "config" / "artists.yaml", os.environ.get("ARTIST_NAME", ""),
                          os.environ.get("OFFICIAL_TOUR_URL", ""))
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1
    print(f"Added {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
