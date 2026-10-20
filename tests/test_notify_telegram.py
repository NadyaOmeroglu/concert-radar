from datetime import datetime, timezone

import pytest

from collector import notify_telegram as nt

# Tuesday 20 Oct 2026, 06:17 UTC = 10:17 in Dubai
TUE = datetime(2026, 10, 20, 6, 17, tzinfo=timezone.utc)
MON = datetime(2026, 10, 19, 6, 17, tzinfo=timezone.utc)
STAMP = "2026-10-20T06:17:00Z"


def ev(id, artist="Coldplay", city="Rome", cc="IT", date="2027-07-14", venue="Stadio Olimpico", onsale=None, status="onsale"):
    return {"id": id, "artist": artist, "city": city, "country": cc, "date": date, "venue": venue, "status": status,
            "onsale_date": onsale, "url": f"https://tickets.test/{id}", "ticket_urls": [{"url": f"https://tickets.test/{id}"}]}


def meta(**kw):
    base = {"lastRun": STAMP, "sources": {"ticketmaster": {"status": "ok"}, "platinumlist": {"status": "ok"}}}
    return base | kw


def changes(**kw):
    return {"generatedAt": STAMP, "new": [], "changed": [], "onsale_soon": [], "removed": []} | kw


def test_full_message_format():
    new = [ev("a", onsale="2026-10-25T08:00:00Z"), ev("b", artist="Dua Lipa", city="Dubai", cc="AE",
                                                     date="2026-08-02", venue="Coca-Cola Arena", onsale=None)]
    changed = [ev("c", artist="The Weeknd", city="Madrid", date="2027-06-12") | {
        "changes": [{"field": "date", "from": "2027-06-10", "to": "2027-06-12"}]}]
    m = meta(sources={"ticketmaster": {"status": "ok"},
                      "platinumlist": {"status": "unavailable", "message": "HTTP 403 (anti-bot protection)"}})
    msgs, state = nt.build(m, changes(new=new, changed=changed), new + changed, {}, TUE)
    text = msgs[0]
    assert text.startswith("🎤 <b>Concert Radar</b> · 20 Oct")
    assert "<b>New concerts (2):</b>" in text
    assert "• <b>Dua Lipa</b> — Sun, 2 Aug 2026, Dubai 🇦🇪, Coca-Cola Arena" in text
    assert "• <b>Coldplay</b> — Wed, 14 Jul 2027, Rome 🇮🇹, Stadio Olimpico · 🎟 sale starts 25 Oct, 12:00 Dubai time" in text
    assert "<b>The Weeknd</b>, Madrid: date Thu, 10 Jun 2027 → Sat, 12 Jun 2027" in text
    assert "⚠️ Platinumlist is unavailable: HTTP 403 (anti-bot protection)" in text
    assert text.endswith('<a href="https://nadyaomeroglu.github.io/concert-radar/">Open the app →</a>')
    assert state["saleAnnounced"] == {"a": "2026-10-25T08:00:00Z"}


def test_sale_is_announced_once_then_reminded_once():
    events = [ev("a", onsale="2026-10-25T08:00:00Z")]
    msgs, state = nt.build(meta(), changes(), events, {}, TUE)
    assert "Sale dates announced (1)" in msgs[0]
    # next days: nothing new
    msgs, state = nt.build(meta(), changes(), events, state, datetime(2026, 10, 21, 6, 17, tzinfo=timezone.utc))
    assert msgs == []
    # the morning before the sale: reminder, once
    day_before = datetime(2026, 10, 24, 6, 17, tzinfo=timezone.utc)
    msgs, state = nt.build(meta(), changes(), events, state, day_before)
    assert "Tickets go on sale soon (1)" in msgs[0] and "tomorrow, 12:00 Dubai time" in msgs[0]
    msgs, _ = nt.build(meta(), changes(), events, state, datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc))
    assert msgs == []


def test_moved_sale_date_is_announced_again():
    _, state = nt.build(meta(), changes(), [ev("a", onsale="2026-11-25T08:00:00Z")], {}, TUE)
    msgs, _ = nt.build(meta(), changes(), [ev("a", onsale="2026-11-27T08:00:00Z")], state, TUE)
    assert "Sale dates announced (1)" in msgs[0] and "27 Nov" in msgs[0]


def test_new_concert_with_sale_tomorrow_is_not_reminded_twice():
    e = ev("a", onsale="2026-10-21T08:00:00Z")
    msgs, state = nt.build(meta(), changes(new=[e]), [e], {}, TUE)
    assert "sale starts tomorrow" in msgs[0] and "Tickets go on sale soon" not in msgs[0]
    assert nt.build(meta(), changes(), [e], state, TUE)[0] == []


def test_quiet_days():
    events = [ev("a")]
    assert nt.build(meta(), changes(), events, {}, TUE)[0] == []  # Tuesday: nothing to say
    msgs, _ = nt.build(meta(), changes(), events, {}, MON)
    assert "✅ Checked — no new concerts. 1 concert(s) on the radar." in msgs[0]


def test_source_problem_reported_once_and_recovery_reported():
    bad = meta(sources={"platinumlist": {"status": "unavailable", "message": "blocked"}})
    msgs, state = nt.build(bad, changes(), [], {}, TUE)
    assert "Platinumlist is unavailable" in msgs[0]
    assert nt.build(bad, changes(), [], state, TUE)[0] == []  # not every day
    msgs, _ = nt.build(bad, changes(), [], state, MON + (TUE - MON) * 7)  # next Monday: reminder
    assert "Platinumlist is unavailable" in msgs[0]
    msgs, _ = nt.build(meta(), changes(), [], state, TUE)
    assert "✅ Platinumlist works again" in msgs[0]


def test_ambiguous_artist_asks_once():
    m = meta(ambiguousArtists=[{"artist": "Sia", "candidates": [
        {"id": "K1", "name": "Sia", "url": "https://tm.test/1", "upcomingEvents": 3},
        {"id": "K2", "name": "SIA", "url": "https://tm.test/2", "upcomingEvents": 0}]}])
    msgs, state = nt.build(m, changes(), [], {}, TUE)
    assert "Several artists with the same name" in msgs[0] and "id K1, 3 events" in msgs[0]
    assert nt.build(m, changes(), [], state, TUE)[0] == []


def test_total_failure_does_not_repeat_old_changes():
    old = changes(new=[ev("a")]) | {"generatedAt": "2026-10-19T06:17:00Z"}
    m = meta(totalFailure=True, sources={"ticketmaster": {"status": "unavailable", "message": "quota exceeded (429)"}})
    msgs, _ = nt.build(m, old, [ev("a")], {}, TUE)
    assert "Nothing could be collected" in msgs[0] and "quota exceeded" in msgs[0]
    assert "New concerts" not in msgs[0]


def test_html_is_escaped():
    e = ev("a", artist="Tom & <Jerry>", venue='Hall "A"')
    msgs, _ = nt.build(meta(), changes(new=[e]), [e], {}, TUE)
    assert "Tom &amp; &lt;Jerry&gt;" in msgs[0] and "Hall &quot;A&quot;" in msgs[0]


def test_long_reports_are_split_and_capped():
    new = [ev(f"id{i}", artist=f"Artist {i:03d}", venue="V" * 120) for i in range(60)]
    msgs, _ = nt.build(meta(), changes(new=new), new, {}, TUE)
    assert len(msgs) > 1 and all(len(m) <= 4096 for m in msgs)
    joined = "\n".join(msgs)
    assert "<b>New concerts (60):</b>" in joined and "…and 20 more in the app" in joined
    assert all(m.count("<b>") == m.count("</b>") and m.count("<a ") == m.count("</a>") for m in msgs)


class FakeSession:
    def __init__(self, status=200):
        self.status, self.sent = status, []

    def post(self, url, json, timeout):
        self.sent.append((url, json))
        return type("R", (), {"status_code": self.status, "text": '{"ok":false,"description":"chat not found"}'})()


def test_send_uses_html_mode():
    s = FakeSession()
    nt.send(["hello"], "TOKEN123", "42", session=s)
    url, body = s.sent[0]
    assert url == "https://api.telegram.org/botTOKEN123/sendMessage"
    assert body == {"chat_id": "42", "text": "hello", "parse_mode": "HTML", "disable_web_page_preview": True}


def test_send_error_does_not_leak_token():
    with pytest.raises(RuntimeError) as exc:
        nt.send(["hello"], "TOKEN123", "42", session=FakeSession(400))
    assert "TOKEN123" not in str(exc.value) and "chat not found" in str(exc.value)
