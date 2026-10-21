"""Telegram report after each collector run: python -m collector.notify_telegram

Sends a message when there is news (new / changed / removed concerts, sale dates, sources breaking or
recovering); on Mondays it also sends "all quiet" so you can see the radar is alive.
Sale reminders are sent once per event, not every day.
"""
from __future__ import annotations

import html
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .net import redact

log = logging.getLogger("notify")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
STATE_FILE = "notify-state.json"
SITE_URL = os.environ.get("SITE_URL", "https://nadyaomeroglu.github.io/concert-radar/")
DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "Asia/Dubai"))
TZ_LABEL = os.environ.get("DISPLAY_TZ_LABEL", "Dubai time")
LIMIT = 4096
MAX_ITEMS = 40  # per section; the rest is in the app
REMIND_WITHIN = timedelta(hours=36)  # "tickets on sale today/tomorrow"

SOURCE_LABEL = {"ticketmaster": "Ticketmaster", "official": "Official sites", "platinumlist": "Platinumlist"}
BAD = {"unavailable", "error"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STATUS_LABEL = {"onsale": "on sale", "offsale": "off sale", "cancelled": "cancelled", "postponed": "postponed",
                "rescheduled": "rescheduled", "announced": "announced", None: "—"}


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def flag(cc: str | None) -> str:
    return "".join(chr(127397 + ord(c)) for c in cc) if cc and len(cc) == 2 and cc.isalpha() else ""


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} {d.year}"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def fmt_sale(dt: datetime, now: datetime) -> str:
    local, today = dt.astimezone(DISPLAY_TZ), now.astimezone(DISPLAY_TZ).date()
    day = ("today" if local.date() == today else "tomorrow" if local.date() == today + timedelta(days=1)
           else f"{local.day} {MONTHS[local.month - 1]}")
    return f"{day}, {local:%H:%M} {TZ_LABEL}"


def link(url: str | None, text: str = "tickets") -> str:
    return f' · <a href="{esc(url)}">{text}</a>' if url and url.startswith(("http://", "https://")) else ""


def first_url(e: dict) -> str | None:
    return e.get("url") or ((e.get("ticket_urls") or [{}])[0]).get("url")


def place(e: dict) -> str:
    city = " ".join(x for x in (esc(e.get("city")), flag(e.get("country"))) if x)
    return ", ".join(x for x in (city, esc(e.get("venue") or "")) if x)


def section(title: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    shown = lines[:MAX_ITEMS]
    more = [f"…and {len(lines) - MAX_ITEMS} more in the app"] if len(lines) > MAX_ITEMS else []
    return ["", f"<b>{title} ({len(lines)}):</b>", *shown, *more]


def describe_change(c: dict) -> str:
    f, a, b = c.get("field"), c.get("from"), c.get("to")
    if f == "date":
        a, b = fmt_date(a), fmt_date(b)
    elif f == "status":
        a, b = STATUS_LABEL.get(a, a), STATUS_LABEL.get(b, b)
    return f"{esc(f)} {esc(a if a is not None else '—')} → {esc(b if b is not None else '—')}"


def welcome(events: list[dict]) -> list[str]:
    """First message ever: what is on the radar right now, one line per artist."""
    by_artist: dict[str, list[dict]] = {}
    for e in sorted(events, key=lambda x: (x.get("date") or "", x.get("time") or "")):
        by_artist.setdefault(e["artist"], []).append(e)
    lines = [f"• <b>{esc(a)}</b> — {len(evs)} concert{'s' if len(evs) > 1 else ''}, next {fmt_date(evs[0].get('date'))}, "
             f"{esc(evs[0].get('city'))} {flag(evs[0].get('country'))}".rstrip()
             for a, evs in sorted(by_artist.items(), key=lambda kv: kv[1][0].get("date") or "")]
    head = ["", "👋 <b>Concert Radar is connected!</b> From now on you’ll hear about new concerts, changes and ticket sales."]
    if not lines:
        return head + ["None of your artists has shows in your regions right now."]
    return head + section(f"On the radar now: {len(events)} concerts", lines)


def build(meta: dict, changes: dict | None, events: list[dict], state: dict, now: datetime,
          first_time: bool = False) -> tuple[list[str], dict]:
    """Returns (messages to send, new state). Pure function: no network, no files."""
    state = json.loads(json.dumps(state or {}))
    announced: dict = state.setdefault("saleAnnounced", {})
    reminded: dict = state.setdefault("saleReminded", {})
    fresh = bool(changes) and changes.get("generatedAt") == meta.get("lastRun") and not meta.get("totalFailure")
    changes = changes if fresh else {}
    body: list[str] = []

    if meta.get("totalFailure"):
        reasons = [f"• {SOURCE_LABEL.get(k, k)}: {esc(v.get('message') or v.get('status'))}"
                   for k, v in (meta.get("sources") or {}).items() if v.get("status") != "skipped"]
        body += ["", "⛔ <b>Nothing could be collected this time.</b> The app still shows the previous data.", *reasons]

    # New concerts (sale start mentioned inline and remembered, so it is not announced again)
    new_lines = []
    for e in sorted(changes.get("new") or [], key=lambda x: (x.get("date") or "", x.get("artist") or "")):
        sale = parse_dt(e.get("onsale_date"))
        sale_txt = ""
        if sale and sale > now:
            sale_txt = f" · 🎟 sale starts {fmt_sale(sale, now)}"
            announced[e["id"]] = e["onsale_date"]
            if sale - now <= REMIND_WITHIN:
                reminded[e["id"]] = e["onsale_date"]
        new_lines.append(f"• <b>{esc(e.get('artist'))}</b> — {fmt_date(e.get('date'))}, {place(e)}{sale_txt}"
                         f"{link(first_url(e))}")
    if first_time:
        body += welcome(events)  # covers the new concerts of this run too
    else:
        body += section("New concerts", new_lines)

    body += section("Changes", [
        f"• <b>{esc(e.get('artist'))}</b>, {esc(e.get('city'))}: " + "; ".join(describe_change(c) for c in e.get("changes") or [])
        + link(first_url(e)) for e in changes.get("changed") or []])
    body += section("No longer listed", [
        f"• <b>{esc(e.get('artist'))}</b>, {esc(e.get('city'))} — {fmt_date(e.get('date'))}" for e in changes.get("removed") or []])

    # Sale reminders: once per event when the sale is within 36 hours; sale dates: once when they appear.
    remind_lines, announce_lines = [], []
    for e in sorted(events, key=lambda x: x.get("onsale_date") or ""):
        sale = parse_dt(e.get("onsale_date"))
        if not sale or sale <= now or e.get("status") == "cancelled":
            continue
        line = f"• <b>{esc(e.get('artist'))}</b>, {esc(e.get('city'))} ({fmt_date(e.get('date'))}) — {fmt_sale(sale, now)}" + link(first_url(e))
        if sale - now <= REMIND_WITHIN and reminded.get(e["id"]) != e["onsale_date"]:
            remind_lines.append(line)
            reminded[e["id"]] = announced[e["id"]] = e["onsale_date"]
        elif announced.get(e["id"]) != e["onsale_date"]:
            announce_lines.append(line)
            announced[e["id"]] = e["onsale_date"]
    body += section("🎟 Tickets go on sale soon", remind_lines)
    body += section("📅 Sale dates announced", announce_lines)

    # Sources: report when a source breaks or recovers (not every day); Mondays repeat open problems.
    monday = now.astimezone(DISPLAY_TZ).weekday() == 0
    prev_status = state.get("sourceStatus") or {}
    source_lines = []
    for name, s in (meta.get("sources") or {}).items():
        label, status = SOURCE_LABEL.get(name, name), s.get("status")
        was_bad = prev_status.get(name) in BAD
        if status in BAD and (not was_bad or monday):
            source_lines.append(f"⚠️ {label} is unavailable: {esc(s.get('message') or 'errors')}")
        elif status == "partial" and (prev_status.get(name) != "partial" or monday):
            source_lines.append(f"⚠️ {label}: errors for {s.get('errors')} artist(s), the rest worked")
        elif status in ("ok", "partial") and was_bad:
            source_lines.append(f"✅ {label} works again")
    state["sourceStatus"] = {k: v.get("status") for k, v in (meta.get("sources") or {}).items()}
    if source_lines and not meta.get("totalFailure"):
        body += ["", *source_lines]

    # Artists that need a decision: only when the list changes (or on Mondays).
    ambiguous = meta.get("ambiguousArtists") or []
    names = sorted(a["artist"] for a in ambiguous)
    if ambiguous and (names != state.get("ambiguous") or monday):
        body += ["", "❓ <b>Several artists with the same name on Ticketmaster</b> — tell Claude which one is yours:"]
        for a in ambiguous:
            options = ", ".join(f'<a href="{esc(c.get("url"))}">{esc(c.get("name"))}</a> (id {esc(c.get("id"))}, '
                                f'{c.get("upcomingEvents") if c.get("upcomingEvents") is not None else "?"} events)'
                                for c in a.get("candidates") or [])
            body.append(f"• <b>{esc(a['artist'])}</b>: {options}")
    state["ambiguous"] = names

    if not body and monday:
        body = ["", f"✅ Checked — no new concerts. {len(events)} concert(s) on the radar."]

    # forget events that are gone
    ids = {e["id"] for e in events}
    for d in (announced, reminded):
        for k in [k for k in d if k not in ids]:
            del d[k]

    if not body:
        return [], state
    local = now.astimezone(DISPLAY_TZ)
    lines = [f"🎤 <b>Concert Radar</b> · {local.day} {MONTHS[local.month - 1]}", *body, "",
             f'<a href="{esc(SITE_URL)}">Open the app →</a>']
    return split_messages(lines), state


def split_messages(lines: list[str], limit: int = LIMIT) -> list[str]:
    """Telegram allows 4096 characters; split on line boundaries so HTML tags stay balanced."""
    messages, current = [], ""
    for line in lines:
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            messages.append(current.strip("\n"))
            current = line
        else:
            current = candidate
    if current.strip():
        messages.append(current.strip("\n"))
    return messages


def send(messages: list[str], token: str, chat_id: str, session=None) -> None:
    session = session or requests.Session()
    for text in messages:
        try:
            resp = session.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        except requests.RequestException as exc:
            raise RuntimeError(redact(exc, token)) from None
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram answered HTTP {resp.status_code}: {redact(resp.text[:300], token)}")


def _read(name: str, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    now = datetime.now(timezone.utc)
    meta, changes, events = _read("meta.json", {}), _read("changes.json", None), _read("events.json", [])
    state = _read(STATE_FILE, {})
    messages, new_state = build(meta, changes, events, state, now, first_time="lastNotified" not in state)
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not messages:
        log.info("nothing to report today")
    elif not token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — message not sent:\n%s", "\n---\n".join(messages))
        return 0
    else:
        try:
            send(messages, token, chat_id)
        except RuntimeError as exc:
            log.error("Telegram message failed: %s", exc)
            return 1  # red mark in GitHub Actions; state is kept so the message is retried next run
        log.info("sent %d Telegram message(s)", len(messages))
    new_state["lastNotified"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    (DATA / STATE_FILE).write_text(json.dumps(new_state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
