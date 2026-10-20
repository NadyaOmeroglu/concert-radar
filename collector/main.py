"""Collector entry point: python -m collector.main [--artist NAME]"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config
from .diff import apply_diff, merge_archive
from .models import AmbiguousArtist, ArtistNotFound, SourceNote, SourceUnavailable
from .net import redact
from .normalize import merge_events, to_event
from .sources import official_site, platinumlist, ticketmaster

log = logging.getLogger("collector")

ROOT = Path(__file__).resolve().parents[1]
# Order matters only for logging; merge priority is defined in normalize.SOURCE_PRIORITY.
SOURCES = {"ticketmaster": ticketmaster, "official": official_site, "platinumlist": platinumlist}


@dataclass
class RunResult:
    meta: dict
    changes: dict
    events_written: bool
    total_failure: bool = False
    warnings: list[str] = field(default_factory=list)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _enabled_sources(root: Path) -> dict:
    try:
        cfg = yaml.safe_load((root / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        cfg = {}
    return {name: mod for name, mod in SOURCES.items() if (cfg.get(name) or {}).get("enabled", True)}


def run(root: Path = ROOT, now: datetime | None = None, sources: dict | None = None,
        only_artist: str | None = None) -> RunResult:
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    data_dir = root / "docs" / "data"
    artists_path = root / "config" / "artists.yaml"
    artists = [a for a in config.load_artists(artists_path) if a.active]
    all_names = [a.name for a in artists]
    if only_artist:
        artists = [a for a in artists if a.name.lower() == only_artist.lower()]
        if not artists:
            raise SystemExit(f"Artist not found in artists.yaml: {only_artist}")
    regions = config.load_regions(root / "config" / "regions.yaml")
    sources = sources if sources is not None else _enabled_sources(root)
    tm_key = os.environ.get("TICKETMASTER_API_KEY")

    raws = []
    ok: set[tuple[str, str]] = set()  # (source, artist) pairs that answered normally
    status: dict[str, dict] = {}
    errors, notes, ambiguous, not_found = [], [], [], []
    new_tm_ids: dict[str, str] = {}

    for name, module in sources.items():
        st = status[name] = {"status": "ok", "events": 0, "errors": 0, "message": None}
        attempted = 0
        for artist in artists:
            if st["status"] == "unavailable":
                break
            if name == "official" and not artist.official_tour_url:
                continue
            attempted += 1
            had_id = artist.ticketmaster_id
            try:
                found = module.fetch(artist, regions)
            except SourceUnavailable as exc:
                st["status"], st["message"] = "unavailable", redact(exc, tm_key)
                log.warning("%s unavailable: %s", name, st["message"])
                continue
            except AmbiguousArtist as exc:
                ambiguous.append({"artist": artist.name, "source": name, "candidates": exc.candidates})
                continue
            except ArtistNotFound as exc:
                not_found.append({"artist": artist.name, "source": name, "candidates": exc.candidates})
                ok.add((name, artist.name))
                continue
            except SourceNote as exc:
                notes.append({"artist": artist.name, "source": name, "message": redact(exc, tm_key)})
                continue
            except Exception as exc:  # one broken artist/source must not stop the run
                msg = redact(f"{type(exc).__name__}: {exc}", tm_key)
                log.exception("%s failed for %s", name, artist.name)
                errors.append({"artist": artist.name, "source": name, "message": msg})
                st["errors"] += 1
                continue
            if name == "ticketmaster" and artist.ticketmaster_id and not had_id:
                new_tm_ids[artist.name] = artist.ticketmaster_id
            ok.add((name, artist.name))
            raws.extend(found)
            st["events"] += len(found)
        if st["status"] == "ok" and not attempted:
            st["status"] = "skipped"
        elif st["status"] == "ok" and st["errors"]:
            st["status"] = "error" if st["errors"] == attempted else "partial"

    config.save_ticketmaster_ids(artists_path, new_tm_ids)

    candidates = [e for r in raws if (e := to_event(r, regions))]
    skipped_no_location = sum(1 for r in raws if not (r.city and r.country))
    current = merge_events(candidates)

    previous = _read_json(data_dir / "events.json", [])
    total_failure = not ok
    meta = _read_json(data_dir / "meta.json", {})
    meta.update({
        "lastRun": stamp,
        "lastRunArtist": only_artist,
        "sources": status,
        "artists": all_names,
        "ambiguousArtists": ambiguous,
        "notFound": not_found,
        "errors": errors,
        "notes": notes,
        "rawEvents": len(raws),
        "skippedNoLocation": skipped_no_location,
        "totalFailure": total_failure,
    })

    if total_failure:
        # Keep the old data: an empty run must never wipe events.json.
        changes = {"generatedAt": stamp, "new": [], "changed": [], "onsale_soon": [], "removed": []}
        _write_json(data_dir / "meta.json", meta)
        return RunResult(meta, changes, events_written=False, total_failure=True)

    def healthy(event: dict) -> bool:
        return all((s, event["artist"]) in ok for s in event.get("sources") or [])

    events, changes, archived = apply_diff(previous, current, now, healthy)
    meta["lastSuccess"] = stamp
    meta["eventsCount"] = len(events)
    _write_json(data_dir / "events.json", events)
    _write_json(data_dir / "changes.json", changes)
    _write_json(data_dir / "archive.json", merge_archive(_read_json(data_dir / "archive.json", []), archived))
    _write_json(data_dir / "meta.json", meta)
    return RunResult(meta, changes, events_written=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect concerts for Concert Radar")
    parser.add_argument("--artist", help="collect only this artist (others are kept as they are)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = run(only_artist=args.artist)
    c = result.changes
    log.info("done: %s events, %d new, %d changed, %d on sale soon, %d removed%s",
             result.meta.get("eventsCount", "?"), len(c["new"]), len(c["changed"]),
             len(c["onsale_soon"]), len(c["removed"]), " (TOTAL FAILURE, data kept)" if result.total_failure else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
