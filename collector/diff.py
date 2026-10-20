"""Compare this run with the previous events.json: new / changed / onsale_soon / removed / archived."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

TRACKED = ("date", "city", "venue", "status", "time")
REMOVE_AFTER_MISSES = 2
MAX_HISTORY = 10


def _summary(e: dict) -> dict:
    keys = ("id", "artist", "title", "date", "time", "city", "country", "venue", "status", "onsale_date")
    return {k: e.get(k) for k in keys} | {"url": (e.get("ticket_urls") or [{}])[0].get("url")}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def apply_diff(previous: list[dict], current: list[dict], now: datetime,
               healthy: Callable[[dict], bool]) -> tuple[list[dict], dict, list[dict]]:
    """Returns (events to publish, changes report, newly archived events).

    `healthy(event)` says whether every source of that event answered normally this run;
    only then does a missing event count as a miss (a source may just be down temporarily).
    """
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now.date().isoformat()
    prev_by_id = {e["id"]: e for e in previous}
    prev_by_sid = {sid: e for e in previous for sid in e.get("source_ids") or []}
    current_ids = {e["id"] for e in current}
    used_prev: set[str] = set()

    events, archived = [], []
    changes = {"generatedAt": stamp, "new": [], "changed": [], "onsale_soon": [], "removed": []}

    for cur in current:
        old = prev_by_id.get(cur["id"])
        if old is None:  # same source event under a new id => its date or city moved
            old = next((prev_by_sid[s] for s in cur.get("source_ids") or []
                        if s in prev_by_sid and prev_by_sid[s]["id"] not in current_ids
                        and prev_by_sid[s]["id"] not in used_prev), None)
        e = dict(cur)
        e["last_seen"] = stamp
        e["missing_runs"] = 0
        if old is None:
            e["first_seen"] = stamp
            e["changed"] = []
            changes["new"].append(_summary(e))
        else:
            used_prev.add(old["id"])
            e["first_seen"] = old.get("first_seen") or stamp
            diffs = [{"field": f, "from": old.get(f), "to": e.get(f), "at": stamp}
                     for f in TRACKED if old.get(f) != e.get(f) and not (f == "status" and e.get(f) is None)]
            e["changed"] = ((old.get("changed") or []) + diffs)[-MAX_HISTORY:]
            if diffs:
                changes["changed"].append(_summary(e) | {"changes": diffs})
        if e["date"] < today:
            archived.append(e)
        else:
            events.append(e)

    for old in previous:
        if old["id"] in current_ids or old["id"] in used_prev:
            continue
        if old["date"] < today:
            archived.append(old)
            continue
        kept = dict(old)
        if healthy(old):
            kept["missing_runs"] = (old.get("missing_runs") or 0) + 1
            if kept["missing_runs"] >= REMOVE_AFTER_MISSES:
                changes["removed"].append(_summary(old))
                continue
        events.append(kept)

    soon = now + timedelta(days=7)
    for e in events:
        onsale = _parse_dt(e.get("onsale_date"))
        if onsale and onsale.tzinfo and now < onsale <= soon:
            changes["onsale_soon"].append(_summary(e))

    events.sort(key=lambda e: (e["date"], e.get("time") or "", e["artist"]))
    return events, changes, archived


def merge_archive(archive: list[dict], archived: list[dict], limit: int = 1000) -> list[dict]:
    by_id = {e["id"]: e for e in archive}
    for e in archived:
        by_id[e["id"]] = e
    return sorted(by_id.values(), key=lambda e: e["date"], reverse=True)[:limit]
