"""Calendar feeds and the next 90 days of public events for one parish site (sandbox).

Reuses the Oakland calendar connector's parts (ingest/connectors/calendars/): discover.scan_html (Google Calendar embeds,
.ics links, CCB / MyChurchEvents / CalendarWiz hops, The Events Calendar REST), adapters (Fetcher, fetch_tribe, parse_ics,
window), categorize.guess_category and its standalone privacy screen (screen_event, feed_excluded). The connector's run.py
cannot be imported as a module (its `from privacy import ...` resolves to ingest/public/privacy.py), so its small
fetch/normalize steps are repeated here around the same parts.

Same-site feeds (the site's own The Events Calendar REST pages, .ics files) were already fetched, politely, by
fetch_site.py and cached; those are read from the cache. Only third-party hosts (calendar.google.com, Outlook, CCB,
MyChurchEvents, CalendarWiz) are fetched live here: one request per second per host, no retries on 403.

Privacy: the connector's screen_event (funerals, weddings, Mass intentions, rentals, appointments, titles that name a
private person -> dropped), then the shared ingest/public/privacy.redact on title and location (any name it would
withhold -> the event is dropped, not stored with a gap). Descriptions are never stored. A feed whose calendar name says it
is funerals / intentions / personal is excluded and its address is not stored. A Google Calendar id that is an e-mail
address (not an opaque @group.calendar.google.com id) is not stored.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[3]
CAL = ROOT / "ingest" / "connectors" / "calendars"
sys.path.insert(0, str(ROOT / "ingest" / "public"))
sys.path.append(str(CAL))
import adapters  # noqa: E402
import discover  # noqa: E402
from categorize import guess_category  # noqa: E402
from privacy import redact, strip_contacts  # noqa: E402

_spec = importlib.util.spec_from_file_location("cal_privacy", CAL / "privacy.py")
cal_privacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal_privacy)

discover.RAW_PAGES = ROOT / "data" / "raw" / "california" / "sites" / "_calpages"   # not the Oakland cache

FACILITY = re.compile(r"facilit|venue|room|hall|gym|chapel|church|center|centre", re.I)


def is_facility_feed(row: dict, calname: str | None) -> bool:
    """A room / facility booking calendar: it lists uses of rooms, not only offerings (same test as the Oakland connector)."""
    return bool(row.get("label") and FACILITY.search(row["label"])) or bool(
        re.search(r"facilit|venue", f"{calname or ''} {row.get('page_url') or ''}", re.I))


PLATFORM = [("calendar.google.com", "Google Calendar"), ("outlook.office365", "Microsoft 365 (Outlook)"),
            ("outlook.live.com", "Microsoft 365 (Outlook)"), ("ccbchurch.com", "CCB / Pushpay ChMS"),
            ("calendarwiz", "CalendarWiz"), ("mychurchevents", "MyChurchEvents"), ("/calendar.ics", "CatholicWebsite.com"),
            ("tribe/events", "The Events Calendar (WordPress)"), ("all-in-one-event-calendar", "All-in-One Event Calendar (WordPress)")]
MAX_EVENTS = 250
MAX_LITURGY = 30
MAX_FEEDS = 6


def platform(url: str | None) -> str:
    for k, v in PLATFORM:
        if url and k in url:
            return v
    return "iCal"


def host_of(u: str) -> str:
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


class CachedFetcher(adapters.Fetcher):
    """Serves the same-site feed pages fetch_site.py cached; third-party hosts go to the shared live Fetcher.

    A same-site feed page the crawl did not reach (the crawl is capped per site) is asked for once, only when the site's
    own pages answered, with the plain default User-Agent, robots.txt honoured, and no faster than the crawl (1 request a
    second). The answer is kept in <site raw dir>/feeds_extra/ so a rerun does not ask again."""

    def __init__(self, shared: adapters.Fetcher, site_dir: Path, manifest: dict, site_host: str):
        super().__init__(delay=shared.delay)
        self.shared, self.dir, self.site_host, self.manifest = shared, site_dir, site_host, manifest
        self.by_url = {p["url"]: p for p in manifest.get("pages", [])}
        self.extra_idx = site_dir / "feeds_extra" / "index.json"
        self.extra = json.loads(self.extra_idx.read_text()) if self.extra_idx.exists() else {}
        self.rp = None
        txt = (manifest.get("robots") or {}).get("text")
        if txt:
            import urllib.robotparser
            self.rp = urllib.robotparser.RobotFileParser()
            self.rp.parse(txt.splitlines())

    def _extra_get(self, full: str):
        import fetch_site as fs
        h = hashlib.sha1(full.encode()).hexdigest()[:16]
        rec = self.extra.get(full)
        if rec is None:
            if self.manifest.get("status") != "ok":
                return "error:not_cached", b"", "", full
            if self.rp is not None and not self.rp.can_fetch("*", full):
                rec = {"class": "robots_disallowed", "status": None}
            else:
                import time
                time.sleep(1.0)
                resp = fs.http_get(full, accept="text/calendar,application/json,*/*", timeout=25, max_bytes=3_000_000)
                cls = fs.classify(resp)
                rec = {"class": cls, "status": resp["status"], "ctype": resp.get("ctype", ""), "fetched_at": now_iso()}
                if cls == "ok":
                    (self.dir / "feeds_extra").mkdir(parents=True, exist_ok=True)
                    with gzip.open(self.dir / "feeds_extra" / f"{h}.gz", "wb") as fh:
                        fh.write(resp["body"])
                    rec["file"] = f"{h}.gz"
            self.extra[full] = rec
            self.extra_idx.parent.mkdir(parents=True, exist_ok=True)
            self.extra_idx.write_text(json.dumps(self.extra, indent=1))
        if rec.get("class") == "ok" and rec.get("file"):
            return 200, gzip.open(self.dir / "feeds_extra" / rec["file"], "rb").read(), rec.get("ctype", ""), full
        return rec.get("status") or f"error:{rec.get('class')}", b"", "", full

    def get(self, url, params=None, accept=None):
        full = url + ("?" + urlencode(params) if params else "")
        rec = self.by_url.get(full)
        if rec is not None:
            if rec.get("class") == "ok" and rec.get("file"):
                body = gzip.open(self.dir / rec["file"], "rb").read()
                return 200, body, rec.get("ctype", ""), full
            return rec.get("status") or f"error:{rec.get('class')}", b"", "", full
        if host_of(url) == self.site_host:
            return self._extra_get(full)
        return self.shared.get(url, params=params, accept=accept)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def opaque_id(url: str) -> bool:
    m = re.search(r"/calendar/ical/([^/]+)/public/", url)
    if not m:
        return True
    cid = unquote(m.group(1))
    return "@" not in cid or cid.endswith("group.calendar.google.com")


def feed_rows(pages, manifest: dict, site_dir: Path, key: str, shared: adapters.Fetcher) -> list[dict]:
    """Registry rows for the site's feeds (one hop resolved live for CCB / MyChurchEvents / CalendarWiz pages)."""
    cands: list[dict] = []
    for p in pages:
        cands += discover.scan_html(p.text, p.url)
    ok_tribe = False
    for rec in manifest.get("pages", []):
        if rec["kind"] == "tribe_events" and rec.get("class") == "ok" and rec.get("file"):
            try:
                js = json.loads(gzip.open(site_dir / rec["file"], "rb").read())
                ok_tribe = ok_tribe or "events" in js
            except (ValueError, OSError):
                pass
    base = f"{urlparse(manifest.get('final_home') or manifest['home']).scheme}://{urlparse(manifest.get('final_home') or manifest['home']).netloc}"
    for c in cands:
        if c["type"] == "tribe_rest":
            c["status"], c["note"] = (("active", "The Events Calendar (WordPress) REST API") if ok_tribe else
                                      ("no_public_feed", "The Events Calendar markers on the site, but its REST route is not available"))
    if ok_tribe and not any(c["type"] == "tribe_rest" for c in cands):
        cands.append({"type": "tribe_rest", "url": base + "/wp-json/tribe/events/v1/events", "page_url": base + "/events/",
                      "label": None, "status": "active", "note": "The Events Calendar (WordPress) REST API"})
    resolved = []
    for c in cands:
        if c["type"].endswith("_page"):
            try:
                resolved += discover.resolve_hop(shared, c)
            except Exception as e:  # noqa: BLE001
                resolved.append({**c, "status": "blocked", "note": f"{c['note']}: {type(e).__name__}"})
        else:
            resolved.append(c)
    merged: dict[tuple, dict] = {}
    for c in resolved:
        if c["type"] == "none" and c["status"] == "no_public_feed":
            continue                                   # a site without a feed is the common case; not a registry row
        k = (c["type"], c["url"], c["status"], c.get("note") if c["url"] is None else None)
        merged.setdefault(k, c)
    rows = []
    for c in merged.values():
        url = c.get("url")
        row = {"feed_id": f"{key}:{c['type']}:{hashlib.sha1((url or c.get('note') or '').encode()).hexdigest()[:8]}",
               "type": c["type"], "platform": platform(url), "url": url, "page_url": c.get("page_url"), "label": c.get("label"),
               "status": c["status"], "note": c.get("note")}
        if c.get("display_tz"):
            row["display_tz"] = c["display_tz"]
        if url and not opaque_id(url):
            row.update(url=None, url_withheld="calendar id is an e-mail address; not stored", feed_key=hashlib.sha1(url.encode()).hexdigest()[:12])
            row["_url"] = url                          # in memory only, to read the feed; removed before writing
        rows.append(row)
    return rows[:MAX_FEEDS + 6]


def parse_ics_tolerant(body: bytes, ws, we, display_tz):
    """eCatholic / CatholicWebsite calendars label their times with a zone called "LocalTZ" (X-WR-TIMEZONE:LocalTZ), which
    is not an IANA zone and stops the shared reader. Every California diocese is on Pacific time, so an unknown zone name is
    read as America/Los_Angeles (the feed's own VTIMEZONE block, which the zone name points at, uses the US Pacific rules)."""
    try:
        return adapters.parse_ics(body, ws, we, display_tz)
    except Exception as e:  # noqa: BLE001
        if "ZoneInfo" not in type(e).__name__ and "No time zone found" not in str(e):
            raise
        fixed = re.sub(rb"(?im)^(X-WR-TIMEZONE:).*$", rb"\1America/Los_Angeles", body)
        fixed = re.sub(rb"(?i)TZID=LocalTZ", b"TZID=America/Los_Angeles", fixed)
        return adapters.parse_ics(fixed, ws, we, display_tz)


def read_feed(f: CachedFetcher, row: dict, ws, we):
    url = row.get("_url") or row["url"]
    typ = row["type"]
    if typ == "tribe_rest":
        return adapters_fetch_tribe(f, url, ws, we)
    st, body, ctype, final = f.get(url)
    if st != 200:
        return st, {}, []
    if adapters.is_bot_challenge(body):
        return "bot_challenge", {}, []
    try:
        if typ == "ics":
            if b"BEGIN:VCALENDAR" not in body[:2000]:
                return "not_ical", {}, []
            return 200, *parse_ics_tolerant(body, ws, we, row.get("display_tz"))
        if typ == "calendarwiz_rss":
            return 200, *adapters.parse_calendarwiz_rss(body, ws, we)
        if typ == "mce_json":
            return 200, *adapters.parse_mce_json(body, ws, we, row.get("page_url") or url)
    except Exception as e:  # noqa: BLE001 - a malformed feed must not stop the run
        return f"parse_error:{type(e).__name__}", {}, []
    return f"unknown_type:{typ}", {}, []


def adapters_fetch_tribe(f, url, ws, we):
    return adapters.fetch_tribe(f, url, ws, we)


LITURGY_CAL = re.compile(r"\b(?:mass|misa|confessions?|reconciliation|adoration|rosary|liturgy|eucharist|benediction|novena)\b", re.I)
HONORIFIC_ONLY = re.compile(r"^(?:fr|father|rev|msgr|deacon|dcn|bishop)\b\.?\s*[\w.'-]{0,24}(?:\s*\([^)]{1,20}\))?$", re.I)


def liturgy_title(title: str, calname: str | None) -> str:
    """A calendar called "Mass" whose entries read just "Fr." or "Fr.Luke" lists the celebrant of each Mass. Say so in the
    title ("Mass: Fr.Luke") so the entry reads as a Mass; a bare "Fr." becomes "Mass". Nothing else is inferred."""
    if not calname or not LITURGY_CAL.search(calname) or len(calname) > 40 or not HONORIFIC_ONLY.match(title.strip()):
        return title
    t = title.strip()
    return calname.strip() if re.fullmatch(r"(?:fr|father|rev|msgr|deacon|dcn|bishop)\.?", t, re.I) else f"{calname.strip()}: {t}"


def safe_calname(name: str | None) -> str | None:
    """Calendar names are sometimes the owner's e-mail address; never stored."""
    if not name:
        return None
    return None if (strip_contacts(name) != name or "@" in name) else name


def iso(v) -> str | None:
    return None if v is None else v.isoformat()


def read_events(rows: list[dict], key: str, f: CachedFetcher, today: date) -> tuple[list[dict], dict]:
    ws, we = adapters.window(90, today)
    events: list[dict] = []
    excluded: Counter = Counter()
    n_raw = 0
    for row in rows:
        if row["status"] != "active" or not (row.get("url") or row.get("_url")):
            continue
        st, meta, raws = read_feed(f, row, ws, we)
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        fx = cal_privacy.feed_excluded(meta.get("calname"), meta.get("caldesc")) if meta else None
        if fx:
            row.update(status="excluded", url=None, note=f"calendar is titled as {fx.replace('_', ' ')}; it names private people, "
                                                          f"so it is not ingested and its address is not stored")
            row.pop("_url", None)
            row["last_fetch"] = {"status": st, "fetched_at": fetched_at}
            continue
        reasons: Counter = Counter()
        kept = 0
        facility = is_facility_feed(row, meta.get("calname") if meta else None)
        for raw in raws:
            keep, why, title, loc = cal_privacy.screen_event(raw["title"], raw.get("location"), raw.get("categories"),
                                                             raw.get("description"), raw.get("class_"))
            if not keep:
                reasons[why] += 1
                continue
            # second screen: the shared detector; any private name it would withhold drops the event
            if redact(title or "")[1] or (loc and redact(loc)[1]):
                reasons["names_person_shared_screen"] += 1
                continue
            title = liturgy_title(title, meta.get("calname") if meta else None)
            uid = f"{row['feed_id']}|{raw.get('uid')}|{iso(raw['start'])}"
            events.append({
                "id": hashlib.sha1(uid.encode()).hexdigest()[:16], "title": title, "start": iso(raw["start"]), "end": iso(raw.get("end")),
                "all_day": bool(raw.get("all_day")), "location": loc, "category": guess_category(title, raw.get("categories")),
                "recurring": bool(raw.get("recurring")), "source_url": (raw.get("url") if row["type"] in ("tribe_rest", "calendarwiz_rss")
                                                                         and raw.get("url") else row.get("page_url")),
                "feed_id": row["feed_id"], "fetched_at": fetched_at, **({"tags": ["facility_calendar"]} if facility else {})})
            kept += 1
        n_raw += len(raws)
        excluded.update(reasons)
        row["last_fetch"] = {"status": st, "fetched_at": fetched_at, "events_in_window": len(raws), "kept": kept,
                             **({"excluded": dict(reasons.most_common())} if reasons else {}),
                             **({"calendar_name": safe_calname(meta.get("calname"))} if safe_calname(meta.get("calname")) else {})}
    for r in rows:
        r.pop("_url", None)
    # de-duplicate (same event on two calendars), order, cap
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: (e["start"] or "", e["title"])):
        k = (re.sub(r"\W+", " ", e["title"].lower()).strip(), e["start"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    lit = [e for e in uniq if e["category"] in ("liturgy", "reconciliation")]
    rest = [e for e in uniq if e["category"] not in ("liturgy", "reconciliation")]
    stored = sorted(rest[:MAX_EVENTS] + lit[:MAX_LITURGY], key=lambda e: (e["start"] or "", e["title"]))
    counts = {"window_start": ws.date().isoformat(), "window_end_exclusive": we.date().isoformat(), "days": 90,
              "in_window": n_raw, "kept_after_privacy": len(uniq), "stored": len(stored),
              "liturgy_and_confession_instances_total": len(lit), "excluded_by_reason": dict(excluded.most_common())}
    return stored, counts
