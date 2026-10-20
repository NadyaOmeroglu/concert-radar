from datetime import datetime, timezone

from collector.diff import apply_diff, merge_archive

T1 = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 23, 6, tzinfo=timezone.utc)


def ev(id, date="2027-06-01", city="Madrid", venue="Arena", status="onsale", sid=None, onsale=None):
    return {"id": id, "artist": "Coldplay", "date": date, "time": "20:00", "city": city, "country": "ES",
            "venue": venue, "status": status, "onsale_date": onsale, "sources": ["ticketmaster"],
            "source_ids": [sid] if sid else [], "ticket_urls": [{"source": "ticketmaster", "url": "https://t"}]}


def always(_):
    return True


def test_new_events_get_first_seen():
    events, changes, _ = apply_diff([], [ev("a")], T1, always)
    assert events[0]["first_seen"] == "2026-10-19T06:00:00Z"
    assert [c["id"] for c in changes["new"]] == ["a"]


def test_venue_and_status_changes_keep_first_seen():
    prev, _, _ = apply_diff([], [ev("a")], T1, always)
    events, changes, _ = apply_diff(prev, [ev("a", venue="Stadium", status="cancelled")], T2, always)
    assert events[0]["first_seen"] == "2026-10-19T06:00:00Z"
    fields = {c["field"]: (c["from"], c["to"]) for c in changes["changed"][0]["changes"]}
    assert fields == {"venue": ("Arena", "Stadium"), "status": ("onsale", "cancelled")}
    assert not changes["new"] and not changes["removed"]


def test_moved_date_is_a_change_not_new_plus_removed():
    prev, _, _ = apply_diff([], [ev("old-id", date="2027-06-10", sid="ticketmaster:1")], T1, always)
    events, changes, _ = apply_diff(prev, [ev("new-id", date="2027-06-12", sid="ticketmaster:1")], T2, always)
    assert not changes["new"] and not changes["removed"]
    assert changes["changed"][0]["changes"][0] | {"at": None} == {"field": "date", "from": "2027-06-10",
                                                                    "to": "2027-06-12", "at": None}
    assert [e["id"] for e in events] == ["new-id"]


def test_removed_only_after_two_healthy_misses():
    prev, _, _ = apply_diff([], [ev("a")], T1, always)
    run2, ch2, _ = apply_diff(prev, [], T2, always)
    assert [e["id"] for e in run2] == ["a"] and not ch2["removed"]
    run3, ch3, _ = apply_diff(run2, [], T2, always)
    assert run3 == [] and [e["id"] for e in ch3["removed"]] == ["a"]


def test_missing_while_source_is_down_does_not_count():
    prev, _, _ = apply_diff([], [ev("a")], T1, always)
    for _ in range(5):
        prev, changes, _ = apply_diff(prev, [], T2, lambda e: False)
    assert [e["id"] for e in prev] == ["a"] and prev[0]["missing_runs"] == 0


def test_past_events_go_to_archive():
    prev, _, _ = apply_diff([], [ev("past", date="2026-10-20"), ev("future")], T1, always)
    events, changes, archived = apply_diff(prev, [ev("future")], T2, always)
    assert [e["id"] for e in events] == ["future"]
    assert [e["id"] for e in archived] == ["past"] and not changes["removed"]
    assert [e["id"] for e in merge_archive([], archived)] == ["past"]


def test_onsale_soon_window():
    current = [ev("soon", onsale="2026-10-25T08:00:00Z"), ev("later", city="Rome", onsale="2026-11-30T08:00:00Z"),
               ev("past", city="Oslo", onsale="2026-10-01T08:00:00Z"), ev("none", city="Nice")]
    _, changes, _ = apply_diff([], current, T1, always)
    assert [c["id"] for c in changes["onsale_soon"]] == ["soon"]
