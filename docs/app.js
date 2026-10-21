/* Concert Radar — static PWA. Data comes from data/*.json written by the collector. */
(() => {
  "use strict";

  const SOURCE_LABEL = { ticketmaster: "Ticketmaster", official: "Official site", platinumlist: "Platinumlist" };
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December"];
  const regionName = (() => {
    const SHORT = { AE: "UAE", CZ: "Czechia" };
    try { const dn = new Intl.DisplayNames(["en"], { type: "region" }); return (c) => SHORT[c] || dn.of(c) || c; }
    catch (e) { return (c) => c; }
  })();

  const app = document.getElementById("app");
  const state = {
    events: [], changes: null, meta: null,
    baseline: null, // ISO time of the previous visit: newer first_seen => "New"
    filters: { q: "", newOnly: false, onSale: false, artists: [], country: "", city: "", month: "" },
  };

  /* ---------- storage (never trusted to exist) ---------- */
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem("cr:" + key); return v === null ? fallback : JSON.parse(v); }
      catch (e) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem("cr:" + key, JSON.stringify(value)); } catch (e) { /* ignore */ } },
  };
  const session = {
    get(key) { try { return sessionStorage.getItem("cr:" + key); } catch (e) { return null; } },
    set(key, value) { try { sessionStorage.setItem("cr:" + key, value); } catch (e) { /* ignore */ } },
  };

  /* ---------- helpers ---------- */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
  const flag = (cc) => (cc && /^[A-Z]{2}$/.test(cc)
    ? String.fromCodePoint(...[...cc].map((c) => 127397 + c.charCodeAt(0))) : "");
  // Festive stage-light gradients only (no muddy greens/browns): [from hue, to hue].
  const PALETTE = [[320, 280], [280, 240], [250, 290], [345, 300], [15, 335], [195, 250], [300, 345], [230, 320]];
  const hue = (name) => {
    let h = 0; for (const c of name || "") h = (h * 31 + c.charCodeAt(0)) >>> 0;
    const [a, b] = PALETTE[h % PALETTE.length]; return `${a};--hue2:${b}`;
  };
  const initials = (name) => (name || "?").replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean)
    .slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "♪";
  const parts = (iso) => { const [y, m, d] = iso.split("-").map(Number); return { y, m, d, wd: new Date(Date.UTC(y, m - 1, d)).getUTCDay() }; };
  const fmtDate = (iso) => { const p = parts(iso); return `${WEEKDAYS[p.wd]}, ${p.d} ${MONTHS[p.m - 1]} ${p.y}`; };
  const fmtStamp = (iso) => {
    const d = new Date(iso); if (isNaN(d)) return "—";
    return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };
  const isFuture = (iso) => { const d = new Date(iso); return !isNaN(d) && d > new Date(); };
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  function toast(msg) {
    const t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2400);
  }

  /* ---------- "New" / "Changed" since the previous visit ---------- */
  function initBaseline() {
    // The baseline stays fixed for the whole session, so badges don't vanish on reload.
    let base = session.get("baseline");
    if (base === null) {
      base = store.get("lastVisit", "");
      session.set("baseline", base);
    }
    state.baseline = base || null;
    const remember = () => store.set("lastVisit", new Date().toISOString().replace(/\.\d+Z$/, "Z"));
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") remember(); });
    window.addEventListener("pagehide", remember);
  }
  const changeIds = (key) => new Set((state.changes?.[key] || []).map((c) => c.id));
  function isNew(e) {
    if (state.baseline) return (e.first_seen || "") > state.baseline;
    return changeIds("new").has(e.id); // first visit ever: show the latest run's news
  }
  function isChanged(e) {
    if (state.baseline) return (e.changed || []).some((c) => (c.at || "") > state.baseline);
    return changeIds("changed").has(e.id);
  }

  /* ---------- data ---------- */
  async function getJSON(name, fallback) {
    try {
      const r = await fetch(`data/${name}`, { cache: "no-cache" });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch (e) { return fallback; }
  }
  async function load() {
    const [events, changes, meta] = await Promise.all([
      getJSON("events.json", []), getJSON("changes.json", null), getJSON("meta.json", null)]);
    const today = new Date(); const t = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    state.events = (Array.isArray(events) ? events : []).filter((e) => e && e.date >= t);
    state.changes = changes; state.meta = meta;
    const newCount = state.events.filter(isNew).length;
    document.getElementById("new-dot").hidden = !newCount;
    try {
      if (newCount && navigator.setAppBadge) navigator.setAppBadge(newCount);
      else if (navigator.clearAppBadge) navigator.clearAppBadge();
    } catch (e) { /* unsupported */ }
  }

  /* ---------- filters ---------- */
  function applyFilters(list) {
    const f = state.filters; const q = f.q.trim().toLowerCase();
    return list.filter((e) => {
      if (f.newOnly && !isNew(e)) return false;
      if (f.onSale && e.status !== "onsale") return false;
      if (f.artists.length && !f.artists.includes(e.artist)) return false;
      if (f.country && e.country !== f.country) return false;
      if (f.city && e.city !== f.city) return false;
      if (f.month && e.date.slice(0, 7) !== f.month) return false;
      if (q) {
        const hay = [e.artist, e.title, e.city, e.venue, regionName(e.country)].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }
  const sheetFilterCount = () => { const f = state.filters; return f.artists.length + !!f.country + !!f.city + !!f.month; };
  function saveFilters() { const { q, ...rest } = state.filters; store.set("filters", rest); }

  /* ---------- views ---------- */
  function badgesFor(e) {
    const b = [];
    if (isNew(e)) b.push('<span class="badge new">New</span>');
    if (isChanged(e)) b.push('<span class="badge changed">Changed</span>');
    if (e.status === "cancelled") b.push('<span class="badge bad">Cancelled</span>');
    else if (e.status === "postponed") b.push('<span class="badge bad">Postponed</span>');
    else if (e.status === "rescheduled") b.push('<span class="badge changed">Rescheduled</span>');
    else if (e.onsale_date && isFuture(e.onsale_date)) b.push(`<span class="badge sale">On sale ${esc(fmtStamp(e.onsale_date))}</span>`);
    else if (e.status === "onsale") b.push('<span class="badge sale">On sale</span>');
    else if (e.status === "offsale") b.push('<span class="badge muted">Off sale</span>');
    return b.join("");
  }

  function ticketCard(e) {
    const p = parts(e.date);
    const seenSource = new Set(); // one button per source; the first link is the main sale page
    const buys = (e.ticket_urls || []).filter((t) => safeUrl(t.url) && !seenSource.has(t.source) && seenSource.add(t.source)).map((t, i) =>
      `<a class="btn ${i === 0 ? "primary" : ""}" href="${esc(t.url)}" target="_blank" rel="noopener">
         ${i === 0 ? "Buy tickets" : ""} ${i === 0 ? "· " : ""}${esc(SOURCE_LABEL[t.source] || t.source)}</a>`).join("");
    const sources = (e.sources || []).map((s) => SOURCE_LABEL[s] || s).join(", ");
    return `
    <article class="ticket ${e.status === "cancelled" ? "cancelled" : ""}" style="--hue:${hue(e.artist)}">
      <div class="stub" aria-hidden="true">
        <span class="wd">${WEEKDAYS[p.wd]}</span><span class="d">${p.d}</span>
        <span class="m">${MONTHS[p.m - 1]}</span><span class="y">${p.y}</span>
      </div>
      <div class="body">
        <h3 class="artist">${esc(e.artist)}</h3>
        ${e.title && e.title !== e.artist ? `<p class="title">${esc(e.title)}</p>` : ""}
        <p class="where"><span class="flag" aria-hidden="true">${flag(e.country)}</span>${esc(e.city)}, ${esc(regionName(e.country))}</p>
        ${e.venue ? `<p class="venue">${esc(e.venue)}</p>` : ""}
        <p class="when">${esc(fmtDate(e.date))}${e.time ? ` · ${esc(e.time)}` : ""}</p>
        <div class="badges">${badgesFor(e)}</div>
        <div class="actions">
          ${buys}
          <button class="icon-btn" data-ics="${esc(e.id)}" aria-label="Add to calendar" title="Add to calendar">
            <svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18M12 13v5M9.5 15.5h5"/></svg></button>
          <button class="icon-btn" data-share="${esc(e.id)}" aria-label="Share" title="Share">
            <svg viewBox="0 0 24 24"><path d="M12 15V3M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/></svg></button>
        </div>
        <p class="via">Data: ${esc(sources)}</p>
      </div>
    </article>`;
  }

  function emptyState(title, text) {
    return `<div class="empty"><div class="big" aria-hidden="true">🎤</div><h2>${esc(title)}</h2><p>${text}</p></div>`;
  }

  function viewUpcoming() {
    const f = state.filters;
    const list = applyFilters(state.events);
    const c = state.changes;
    const newN = state.events.filter(isNew).length;
    const changedN = c ? (c.changed || []).length : 0;
    const updated = state.meta?.lastRun ? fmtStamp(state.meta.lastRun) : null;
    const extra = sheetFilterCount();

    let body;
    if (!state.events.length) {
      body = state.meta?.lastSuccess
        ? emptyState("No concerts yet", "None of your artists has announced shows in the Schengen area or the Gulf right now. The radar checks every morning — you’ll get a Telegram message as soon as something appears.")
        : emptyState("Warming up the radar", "The first collection hasn’t run yet. Concerts will appear here after the next daily update.");
    } else if (!list.length) {
      body = emptyState("Nothing matches", "Try another search or reset the filters.");
    } else {
      let month = ""; body = "";
      for (const e of list) {
        const m = e.date.slice(0, 7);
        if (m !== month) { month = m; const p = parts(e.date); body += `<h2 class="month">${MONTHS_LONG[p.m - 1]} ${p.y}</h2>`; }
        body += ticketCard(e);
      }
    }

    app.innerHTML = `
      <section class="hero">
        <h1>Live &amp; <em>loud</em></h1>
        <p class="summary">${updated ? `Updated <b>${esc(updated)}</b>` : "Not updated yet"}
          ${newN ? ` · <b class="plus">+${newN} new</b>` : ""}${changedN ? ` · <b>${plural(changedN, "change")}</b>` : ""}</p>
      </section>
      <div class="toolbar">
        <label class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
          <input id="q" type="search" placeholder="Search artist, city, venue" value="${esc(f.q)}" aria-label="Search concerts" autocomplete="off"></label>
        <div class="chips" role="group" aria-label="Quick filters">
          <button class="chip" id="chip-filters" aria-haspopup="dialog">Filters${extra ? ` <span class="count">${extra}</span>` : ""}</button>
          <button class="chip" id="chip-new" aria-pressed="${f.newOnly}">✨ New only</button>
          <button class="chip" id="chip-sale" aria-pressed="${f.onSale}">🎟 On sale</button>
        </div>
      </div>
      ${state.events.length ? `<p class="result-count" aria-live="polite">${plural(list.length, "concert")}</p>` : ""}
      ${body}`;

    const q = document.getElementById("q");
    q.addEventListener("input", () => {
      state.filters.q = q.value; const pos = q.selectionStart; render(); const nq = document.getElementById("q");
      nq.focus(); try { nq.setSelectionRange(pos, pos); } catch (e) { /* ignore */ }
    });
    document.getElementById("chip-new").onclick = () => { f.newOnly = !f.newOnly; saveFilters(); render(); };
    document.getElementById("chip-sale").onclick = () => { f.onSale = !f.onSale; saveFilters(); render(); };
    document.getElementById("chip-filters").onclick = openFilters;
  }

  function viewArtists() {
    const counts = new Map();
    for (const e of state.events) counts.set(e.artist, (counts.get(e.artist) || 0) + 1);
    const names = new Set([...(state.meta?.artists || []), ...counts.keys()]);
    const rows = [...names].sort((a, b) => (counts.get(b) || 0) - (counts.get(a) || 0) || a.localeCompare(b));
    const nextFor = (name) => state.events.find((e) => e.artist === name);
    app.innerHTML = `
      <h1 class="section-title">Your artists</h1>
      <p class="lead">${plural(rows.length, "artist")} on the radar. Tap one to see their concerts.</p>
      ${rows.length ? `<ul class="list">${rows.map((name) => {
        const n = counts.get(name) || 0; const next = nextFor(name);
        const sub = next ? `Next: ${esc(fmtDate(next.date))}, ${esc(next.city)}` : "No dates in your regions yet";
        const inner = `<span class="avatar" style="--hue:${hue(name)}" aria-hidden="true">${esc(initials(name))}</span>
          <span class="main"><span class="name">${esc(name)}</span><span class="sub">${sub}</span></span>
          <span class="count ${n ? "" : "zero"}">${n || "—"}</span>`;
        return `<li>${n ? `<button class="row linked" data-artist="${esc(name)}" aria-label="${esc(name)}: ${plural(n, "concert")}">${inner}<span class="arrow" aria-hidden="true">›</span></button>`
          : `<div class="row">${inner}</div>`}</li>`;
      }).join("")}</ul>` : emptyState("No artists yet", "Artists appear here after the first collection.")}`;
    app.querySelectorAll("[data-artist]").forEach((b) => b.addEventListener("click", () => {
      Object.assign(state.filters, { artists: [b.dataset.artist], country: "", city: "", month: "", newOnly: false, onSale: false, q: "" });
      saveFilters(); location.hash = "#upcoming";
    }));
  }

  function changeRow(item, extraHtml) {
    const url = safeUrl(item.url);
    const inner = `<span class="avatar" style="--hue:${hue(item.artist)}" aria-hidden="true">${esc(initials(item.artist))}</span>
      <span class="main"><span class="name">${esc(item.artist)}</span>
      <span class="sub">${flag(item.country)} ${esc(item.city)} · ${esc(fmtDate(item.date))}${item.venue ? ` · ${esc(item.venue)}` : ""}</span>
      ${extraHtml || ""}</span>`;
    return `<li>${url ? `<a class="row linked" href="${esc(url)}" target="_blank" rel="noopener">${inner}<span class="arrow" aria-hidden="true">›</span></a>` : `<div class="row">${inner}</div>`}</li>`;
  }
  const FIELD = { date: "Date", city: "City", venue: "Venue", status: "Status", time: "Time" };
  const showValue = (field, v) => (v == null ? "—" : field === "date" ? fmtDate(v) : v);

  function viewNew() {
    const c = state.changes;
    const sections = [];
    if (c) {
      const add = (title, items, extra) => { if (items?.length) sections.push(`<h2 class="section-title">${title} <span class="arrow">${items.length}</span></h2><ul class="list">${items.map((i) => changeRow(i, extra && extra(i))).join("")}</ul>`); };
      add("New concerts", c.new);
      add("Changes", c.changed, (i) => (i.changes || []).map((ch) =>
        `<span class="change">${FIELD[ch.field] || esc(ch.field)}: <s>${esc(showValue(ch.field, ch.from))}</s> → <b>${esc(showValue(ch.field, ch.to))}</b></span>`).join(""));
      add("Tickets on sale soon", c.onsale_soon, (i) => `<span class="change">🎟 Sale starts <b>${esc(fmtStamp(i.onsale_date))}</b></span>`);
      add("No longer listed", c.removed);
    }
    app.innerHTML = `
      <h1 class="section-title">What’s new</h1>
      <p class="lead">${c?.generatedAt ? `From the latest update, ${esc(fmtStamp(c.generatedAt))}.` : "Nothing collected yet."}</p>
      ${sections.join("") || emptyState("All quiet", "No new concerts or changes in the latest update.")}`;
  }

  function sourceRow(name, s) {
    const cls = { ok: "ok", partial: "warn", error: "bad", unavailable: "bad", skipped: "off" }[s.status] || "off";
    const label = { ok: "Working", partial: "Partly working", error: "Errors", unavailable: "Unavailable", skipped: "Not used" }[s.status] || s.status;
    const detail = s.message ? ` · ${esc(s.message)}` : s.errors ? ` · ${plural(s.errors, "error")}` : "";
    return `<div class="status"><span class="led ${cls}" aria-hidden="true"></span>
      <span class="main"><span class="name">${esc(SOURCE_LABEL[name] || name)}</span>
      <span class="sub">${label} · ${plural(s.events || 0, "event")} found${detail}</span></span></div>`;
  }

  function viewAbout() {
    const m = state.meta || {};
    const theme = store.get("theme", "system");
    const ambiguous = (m.ambiguousArtists || []).map((a) => esc(a.artist)).join(", ");
    app.innerHTML = `
      <h1 class="section-title">About</h1>
      <div class="card">
        <h3>Last update</h3>
        <p>${m.lastRun ? `Checked <b>${esc(fmtStamp(m.lastRun))}</b>` : "Not run yet"}${m.lastSuccess && m.lastSuccess !== m.lastRun ? ` · last successful ${esc(fmtStamp(m.lastSuccess))}` : ""}.
          The radar updates every day at about 10:15 Dubai time.</p>
        ${Object.entries(m.sources || {}).map(([k, v]) => sourceRow(k, v)).join("") || "<p>No sources checked yet.</p>"}
        ${ambiguous ? `<p>⚠️ Needs your choice (several artists with this name): <b>${ambiguous}</b>.</p>` : ""}
      </div>
      <div class="card">
        <h3>Install on your phone</h3>
        <p><b>iPhone (Safari)</b></p>
        <ol><li>Open this page in Safari.</li><li>Tap <b>Share</b> (square with an arrow).</li>
          <li>Choose <b>Add to Home Screen</b>.</li><li>Tap <b>Add</b>.</li></ol>
        <p><b>Android (Chrome)</b></p>
        <ol><li>Open this page in Chrome.</li><li>Tap the <b>⋮</b> menu.</li>
          <li>Choose <b>Install app</b> (or <b>Add to Home screen</b>).</li><li>Confirm with <b>Install</b>.</li></ol>
      </div>
      <div class="card">
        <h3>Theme</h3>
        <div class="seg" role="group" aria-label="Theme">
          ${["system", "light", "dark"].map((t) => `<button data-theme-set="${t}" aria-pressed="${theme === t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}
        </div>
      </div>
      <div class="card">
        <h3>Where the data comes from</h3>
        <p>Concert facts and ticket links come from the <b>Ticketmaster Discovery API</b>, <b>Platinumlist</b> and
          artists’ <b>official websites</b>. Each card shows its sources; tickets are always bought on the source’s own site.
          Event data belongs to its sources and organisers.</p>
        <p>Shown regions: Schengen area, Dubai, Abu Dhabi, Saudi Arabia and Oman.</p>
        <p><a class="link" href="https://github.com/NadyaOmeroglu/concert-radar" target="_blank" rel="noopener">Project on GitHub ›</a></p>
      </div>`;
    app.querySelectorAll("[data-theme-set]").forEach((b) => b.addEventListener("click", () => {
      store.set("theme", b.dataset.themeSet); applyTheme(); render();
    }));
  }

  /* ---------- filters sheet ---------- */
  function fillSelect(sel, values, current, allLabel, labelFn) {
    sel.innerHTML = `<option value="">${allLabel}</option>` + values.map((v) =>
      `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labelFn ? labelFn(v) : v)}</option>`).join("");
  }
  function openFilters() {
    const dlg = document.getElementById("filters");
    const f = state.filters; const ev = state.events;
    const uniq = (arr) => [...new Set(arr)].sort();
    const refresh = () => {
      fillSelect(document.getElementById("f-country"), uniq(ev.map((e) => e.country)).sort((a, b) => regionName(a).localeCompare(regionName(b))),
        f.country, "All countries", (c) => `${flag(c)} ${regionName(c)}`);
      fillSelect(document.getElementById("f-city"), uniq(ev.filter((e) => !f.country || e.country === f.country).map((e) => e.city)), f.city, "All cities");
      fillSelect(document.getElementById("f-month"), uniq(ev.map((e) => e.date.slice(0, 7))), f.month, "Any month",
        (m) => `${MONTHS_LONG[Number(m.slice(5)) - 1]} ${m.slice(0, 4)}`);
      const artists = uniq([...(state.meta?.artists || []), ...ev.map((e) => e.artist)]);
      document.getElementById("f-artists").innerHTML = artists.map((a) =>
        `<label><input type="checkbox" value="${esc(a)}" ${f.artists.includes(a) ? "checked" : ""}>${esc(a)}</label>`).join("");
    };
    refresh();
    document.getElementById("f-country").onchange = (e) => { f.country = e.target.value; f.city = ""; refresh(); };
    document.getElementById("f-city").onchange = (e) => { f.city = e.target.value; };
    document.getElementById("f-month").onchange = (e) => { f.month = e.target.value; };
    document.getElementById("f-artists").onchange = () => {
      f.artists = [...document.querySelectorAll("#f-artists input:checked")].map((i) => i.value);
    };
    document.getElementById("filters-reset").onclick = () => {
      Object.assign(f, { artists: [], country: "", city: "", month: "", newOnly: false, onSale: false }); refresh();
    };
    dlg.onclose = () => { saveFilters(); render(); };
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
  }

  /* ---------- calendar (.ics) and sharing ---------- */
  function zonedToUtc(date, time, tz) {
    const [y, m, d] = date.split("-").map(Number); const [hh, mm] = time.split(":").map(Number);
    const guess = Date.UTC(y, m - 1, d, hh, mm);
    try {
      const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric",
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(new Date(guess)).map((x) => [x.type, x.value]));
      const asZone = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute);
      return new Date(guess - (asZone - guess));
    } catch (e) { return null; }
  }
  const icsStamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const icsText = (s) => String(s || "").replace(/[\\;,]/g, (c) => "\\" + c).replace(/\n/g, "\\n");
  function makeIcs(e) {
    const url = safeUrl(e.ticket_urls?.[0]?.url) || "";
    let when;
    if (e.time && e.timezone && zonedToUtc(e.date, e.time, e.timezone)) {
      const start = zonedToUtc(e.date, e.time, e.timezone);
      when = [`DTSTART:${icsStamp(start)}`, `DTEND:${icsStamp(new Date(start.getTime() + 3 * 3600e3))}`];
    } else if (e.time) { // local time of the venue, no zone known
      const s = e.date.replace(/-/g, "") + "T" + e.time.replace(":", "") + "00";
      when = [`DTSTART:${s}`];
    } else {
      const next = new Date(Date.UTC(...e.date.split("-").map((n, i) => (i === 1 ? n - 1 : +n))) + 864e5);
      when = [`DTSTART;VALUE=DATE:${e.date.replace(/-/g, "")}`, `DTEND;VALUE=DATE:${next.toISOString().slice(0, 10).replace(/-/g, "")}`];
    }
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Concert Radar//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
      `UID:${e.id}@concert-radar`, `DTSTAMP:${icsStamp(new Date())}`, ...when,
      `SUMMARY:${icsText(e.artist + (e.title && e.title !== e.artist ? " — " + e.title : ""))}`,
      `LOCATION:${icsText([e.venue, e.city, regionName(e.country)].filter(Boolean).join(", "))}`,
      url ? `URL:${url}` : null, `DESCRIPTION:${icsText("Tickets: " + url)}`,
      "END:VEVENT", "END:VCALENDAR"].filter(Boolean).join("\r\n");
  }
  function downloadIcs(e) {
    const blob = new Blob([makeIcs(e)], { type: "text/calendar;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${e.artist}-${e.date}.ics`.replace(/[^\w.-]+/g, "-");
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }
  async function share(e) {
    const url = safeUrl(e.ticket_urls?.[0]?.url) || location.href;
    const text = `${e.artist} — ${fmtDate(e.date)}${e.time ? " " + e.time : ""}, ${e.venue ? e.venue + ", " : ""}${e.city}`;
    if (navigator.share) {
      try { await navigator.share({ title: e.artist, text, url }); } catch (err) { /* cancelled */ }
      return;
    }
    try { await navigator.clipboard.writeText(`${text}\n${url}`); toast("Copied to clipboard"); }
    catch (err) { toast("Sharing is not available here"); }
  }
  app.addEventListener("click", (ev) => {
    const ics = ev.target.closest("[data-ics]"); const sh = ev.target.closest("[data-share]");
    const find = (id) => state.events.find((e) => e.id === id);
    if (ics) { const e = find(ics.dataset.ics); if (e) downloadIcs(e); }
    if (sh) { const e = find(sh.dataset.share); if (e) share(e); }
  });

  /* ---------- routing, theme, startup ---------- */
  const VIEWS = { upcoming: viewUpcoming, artists: viewArtists, new: viewNew, about: viewAbout };
  function route() { const r = location.hash.replace("#", ""); return VIEWS[r] ? r : "upcoming"; }
  function render() {
    const r = route(); VIEWS[r]();
    document.querySelectorAll(".tabbar a").forEach((a) => {
      if (a.dataset.tab === r) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function applyTheme() {
    const t = store.get("theme", "system");
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  }
  window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
  const setOffline = () => { document.getElementById("offline").hidden = navigator.onLine; };
  window.addEventListener("online", setOffline); window.addEventListener("offline", setOffline);

  applyTheme(); initBaseline(); setOffline();
  Object.assign(state.filters, store.get("filters", {}), { q: "" });
  load().then(render);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { /* optional */ });
})();
