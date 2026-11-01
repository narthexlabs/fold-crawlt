"""Find each parish's public calendar feed(s).

Sources, in order:
1. Raw HTML already crawled by ingest/public/parish_sites.py (data/raw/parish_sites/<id>/*.html).
2. Online (unless --offline): each parish's own calendar/events pages (at most 3 per parish), then one hop
   into known calendar hosts (CCB public events page -> its iCal link; MyChurchEvents page -> its published
   JSON; CalendarWiz page -> its RSS link), and a one-request probe of The Events Calendar REST route on
   WordPress sites.
3. ingest/connectors/calendars/feeds_manual.json: feeds registered by hand (a parish office, or a re-crawl
   from a residential connection).

Each registry row: {feed_id, parish_id, type, url, page_url, label, status, note}
    type    ics | tribe_rest | calendarwiz_rss | mce_json
    status  active | excluded | not_ingested | blocked | no_public_feed
Feeds that cannot be ingested are still listed (with page_url and a reason), so coverage is honest.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

from adapters import Fetcher, is_bot_challenge

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SITES = ROOT / "data" / "real" / "parish_sites.json"
RAW_SITES = ROOT / "data" / "raw" / "parish_sites"
RAW_PAGES = ROOT / "data" / "raw" / "calendars" / "pages"
MANUAL = HERE / "feeds_manual.json"
DIOCESE_API = "https://www.oakdiocese.org/wp-json/tribe/events/v1/events"


CACHE_DAYS = 7


def cached_get(f: Fetcher, url: str) -> tuple[int | str, bytes, str]:
    """GET with a 7-day page cache in data/raw/calendars/pages/ (git-ignored), so rediscovery is cheap."""
    import time as _t
    RAW_PAGES.mkdir(parents=True, exist_ok=True)
    key = RAW_PAGES / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    meta = key.with_suffix(".json")
    if key.exists() and meta.exists() and _t.time() - key.stat().st_mtime < CACHE_DAYS * 86400:
        m = json.loads(meta.read_text("utf-8"))
        return m["status"], key.read_bytes(), m["final"]
    st, body, _, final = f.get(url)
    if st == 200:
        key.write_bytes(body)
        meta.write_text(json.dumps({"url": url, "status": st, "final": final}), "utf-8")
    return st, body, final


def feed_id(parish_id: str, typ: str, url: str) -> str:
    return f"{parish_id}:{typ}:{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def _host(u: str) -> str:
    h = urlparse(u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


# ------------------------------------------------------------------------------------------- page scan
def _google_ids(fragment: str) -> list[str]:
    ids = []
    for raw in re.findall(r"[?&;](?:src|cid)=([^&\"'\s<>…]+)", fragment):
        v = unquote(unquote(raw))
        if "@" not in v and re.fullmatch(r"[A-Za-z0-9+/=_-]{16,}", v):
            try:
                dec = base64.b64decode(v + "=" * (-len(v) % 4)).decode("utf-8")
                if "@" in dec:
                    v = dec
            except (ValueError, UnicodeDecodeError):
                pass
        if "@" in v:
            ids.append(v)
    m = re.search(r"/calendar/ical/([^/]+)/public/", fragment)
    if m:
        ids.append(unquote(m.group(1)))
    return ids


def google_ics(cal_id: str) -> str:
    return f"https://calendar.google.com/calendar/ical/{quote(cal_id, safe='')}/public/basic.ics"


def scan_html(text: str, page_url: str) -> list[dict]:
    """Return feed candidates referenced by one page (no network)."""
    s = html.unescape(text)
    s = s.replace("\\/", "/")
    out: list[dict] = []

    page = re.sub(r"/action~[^?#]*$", "/", page_url)     # All-in-One Event Calendar day/week view URLs

    def add(typ, url, label=None, status="active", note=None):
        out.append({"type": typ, "url": url, "page_url": page, "label": label, "status": status, "note": note})

    # Google Calendar embeds and links (src may repeat and may be base64-encoded)
    for m in re.finditer(r"calendar\.google\.com/calendar/[^\"'\s<>]*", s):
        ctz = re.search(r"[?&;]ctz=([^&\"'\s<>…]+)", m.group(0))
        ctz = unquote(ctz.group(1)) if ctz else None
        for cid in _google_ids(m.group(0)):
            if cid.endswith("group.v.calendar.google.com") or "#contacts@" in cid or "#weather" in cid:
                continue                                    # public holiday / contacts overlays, not parish
            add("ics", google_ics(cid), note="Google Calendar public feed (embedded on page)")
            if ctz and ctz != "America/Los_Angeles":
                # The parish's own embed shows the calendar in another zone, so the times it publishes are
                # wall-clock times in that zone. Render the same way (see adapters.parse_ics).
                out[-1]["display_tz"] = ctz
    # Duda (static.cdn-website.com) Google Calendar widget keeps the id base64-encoded in a data attribute
    for v in re.findall(r'data-public-calendar-id="([^"]+)"', s):
        for cid in _google_ids(f"?src={v}"):
            add("ics", google_ics(cid), note="Google Calendar public feed (website-builder widget)")
    # Outlook / Microsoft 365 published calendars: the .html view has a sibling .ics
    tabs = re.findall(r'class="sprocket-tabs-text">\s*(.*?)\s*<', s, flags=re.S)
    outlook = list(dict.fromkeys(re.findall(
        r"https://outlook\.(?:office365|office|live)\.com/owa/calendar/[^\"'\s<>]+?/(?:reach)?calendar\.html", s)))
    for i, u in enumerate(outlook):
        label = tabs[i].strip() if len(tabs) == len(outlook) else None
        add("ics", re.sub(r"\.html$", ".ics", u), label=label, note="Microsoft 365 published calendar")
    # CCB / Pushpay ChMS public events page -> has a public iCal subscription link (needs one hop)
    for sub in sorted(set(re.findall(r"([a-z0-9-]+)\.ccbchurch\.com/goto/events/public", s))):
        add("ccb_page", f"https://{sub}.ccbchurch.com/goto/events/public", note="CCB public events page")
    for u in sorted(set(re.findall(r"(?:webcal|https)://[a-z0-9-]+\.ccbchurch\.com/w_calendar_sub\.ics", s))):
        add("ics", "https://" + u.split("://", 1)[1], note="CCB public calendar iCal")
    # MyChurchEvents public calendar (one hop to its published JSON)
    for cid in sorted(set(re.findall(r"mychurchevents\.com/calendar/(\d+)", s))):
        add("mce_page", f"https://www.mychurchevents.com/calendar/{cid}", note="MyChurchEvents public calendar")
    # CalendarWiz (one hop to its RSS feed)
    for crd in sorted(set(re.findall(r"calendarwiz\.com/calendars/calendar\.php\?crd=([a-z0-9_]+)", s, flags=re.I))):
        add("calendarwiz_page", f"https://www.calendarwiz.com/calendars/calendar.php?crd={crd}&jsenabled=0",
            note="CalendarWiz calendar")
    for u in sorted(set(re.findall(r"https?://www\.calendarwiz\.com/calendars/rssfeeder\.xml\?crd=[a-z0-9_]+&(?:amp;)?cat=all", s))):
        add("calendarwiz_rss", u.replace("http://", "https://").replace("&amp;", "&"), note="CalendarWiz RSS")
    # Plain .ics / webcal links on the page
    for u in sorted(set(re.findall(r"""(?:href|src)=["']((?:webcal|https?)://[^"']+?\.ics(?:\?[^"']*)?|/[^"']*?\.ics(?:\?[^"']*)?)["']""", s))):
        full = urljoin(page_url, u.replace("webcal://", "https://"))
        if "ccbchurch.com" in full or "calendar.google.com" in full or "outlook." in full:
            continue
        add("ics", full, note="iCal link on page")
    # CatholicWebsite.com (Drupal) sites publish their calendar at /calendar.ics
    if 'content="CatholicWebsite.com' in s:
        add("ics", urljoin(page_url, "/calendar.ics"), note="CatholicWebsite.com calendar iCal")
    # The Events Calendar (WordPress)
    if re.search(r"tribe-events|tribe_events|/wp-json/tribe/events/v1", s):
        base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
        add("tribe_rest", base + "/wp-json/tribe/events/v1/events", note="The Events Calendar (WordPress) REST API")
    # All-in-One Event Calendar (WordPress) documented export
    if re.search(r"ai1ec[-_](?:calendar|event|subscribe)|all-in-one-event-calendar", s):
        base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
        add("ics", base + "/?plugin=all-in-one-event-calendar&controller=ai1ec_exporter_controller&action=export_events",
            note="All-in-One Event Calendar export (WordPress)")
    # Known embeds that are not ingested
    if "massintentions.com" in s:
        add("none", None, status="not_ingested",
            note="calendar page embeds a third-party Mass-intentions service; intentions name private people")
    if re.search(r"events\.wixapps|wix-events|wixEvents", s):
        add("none", None, status="no_public_feed", note="Wix Events widget (no public feed)")
    return out


def calendar_links(text: str, page_url: str, limit: int = 3) -> list[str]:
    """Same-site links that look like the parish calendar/events page."""
    s = html.unescape(text)
    host = _host(page_url)
    cands = []
    for m in re.finditer(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', s, flags=re.S | re.I):
        href, anchor = m.group(1).strip(), re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip().lower()
        full = urljoin(page_url, href)
        if _host(full) != host or re.search(r"\.(pdf|jpg|png|docx?)$|liturgical-calendar|readings|bulletin|/feed/?$|"
                                            r"/category/|/tag/|/author/|wp-login|/\d{4}/\d{2}/", full, re.I):
            continue
        path = urlparse(full).path.lower()
        score = 0
        if re.search(r"calendar|calendario", path):
            score += 3
        if re.search(r"/events?/?$|/eventos/?$|/events/calendar|/news/calendar", path):
            score += 2
        if re.fullmatch(r"(parish\s+)?(calendar|events|calendario|eventos|parish\s+life\s+calendar|event\s+calendar|"
                        r"calendar\s+of\s+events|upcoming\s+events)", anchor):
            score += 2
        if score:
            cands.append((score, full.split("?")[0].rstrip("/") + ("/" if full.endswith("/") else "")))
    seen, out = set(), []
    for _, u in sorted(cands, key=lambda x: -x[0]):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:limit]


# ------------------------------------------------------------------------------------------- hops
def resolve_hop(f: Fetcher, c: dict) -> list[dict]:
    """Turn a page-type candidate into a feed (one request)."""
    st, body, final = cached_get(f, c["url"])
    txt = body.decode("utf-8", "replace") if isinstance(body, bytes) else ""
    if st != 200 or is_bot_challenge(body):
        return [{**c, "type": c["type"], "status": "blocked", "note": f"{c['note']}: HTTP {st}"}]
    t = txt.replace("\\/", "/")
    if c["type"] == "ccb_page":
        m = re.search(r'"public_calendar_ical":"(webcal|https)://([^"]+?\.ics)"', t)
        if m:
            return [{**c, "type": "ics", "url": "https://" + m.group(2), "note": "CCB public calendar iCal"}]
    elif c["type"] == "mce_page":
        m = re.search(r'MCE_PUBLISHED_BASE\s*=\s*"([^"]+)"', t)
        cid = re.search(r"/calendar/(\d+)", c["url"]).group(1)
        if m:
            return [{**c, "type": "mce_json", "url": f"{m.group(1).rstrip('/')}/calendar-json/{cid}.json",
                     "note": "MyChurchEvents published calendar JSON (what its public page loads)"}]
    elif c["type"] == "calendarwiz_page":
        m = re.search(r'href="(https?://www\.calendarwiz\.com/calendars/rssfeeder\.xml\?crd=[^"]+)"', t)
        if m:
            return [{**c, "type": "calendarwiz_rss",
                     "url": html.unescape(html.unescape(m.group(1))).replace("&amp;", "&").replace("http://", "https://"),
                     "note": "CalendarWiz RSS (next ~100 items)"}]
    return [{**c, "status": "no_public_feed", "note": f"{c['note']}: no feed link found"}]


def probe_tribe(f: Fetcher, base: str) -> bool:
    st, body, ctype, _ = f.get(base.rstrip("/") + "/wp-json/tribe/events/v1/events", params={"per_page": 1},
                               accept="application/json")
    if st != 200:
        return False
    try:
        return "events" in json.loads(body)
    except ValueError:
        return False


# ------------------------------------------------------------------------------------------- main
def _raw_pages(pid: str) -> list[tuple[str, str]]:
    d = RAW_SITES / pid
    if not d.exists():
        return []
    return [(p.name, p.read_text("utf-8", "replace")) for p in sorted(d.glob("*.html"))]


def discover(online: bool = True, delay: float = 2.0, only: set[str] | None = None, log=print) -> list[dict]:
    sites = json.loads(SITES.read_text("utf-8"))["records"]
    f = Fetcher(delay=delay)
    rows: list[dict] = []
    RAW_PAGES.mkdir(parents=True, exist_ok=True)
    for rec in sites:
        pid = rec["id"]
        if only and pid not in only:
            continue
        home = rec.get("final_home_url") or rec.get("website")
        pages = {p["url"]: p.get("raw_file") for p in rec.get("pages") or []}
        cands: list[dict] = []
        raw = _raw_pages(pid)
        url_by_file = {Path(v).name: k for k, v in pages.items() if v}
        for name, txt in raw:
            cands += scan_html(txt, url_by_file.get(name, home or ""))
        status = rec.get("crawl_status")
        if not home:
            rows.append({"parish_id": pid, "type": "none", "url": None, "page_url": None, "label": None,
                         "status": "no_website", "note": "no website listed in the diocesan directory"})
            continue
        if status != "ok":
            rows.append({"parish_id": pid, "type": "none", "url": None, "page_url": home, "label": None,
                         "status": "blocked", "note": f"parish site not reachable from this environment ({status})"})
            continue
        if online:
            links: list[str] = []
            for name, txt in raw:
                links += [u for u in calendar_links(txt, url_by_file.get(name, home)) if u not in links]
            for u in links[:3]:
                if u in pages:
                    continue
                st, body, final = cached_get(f, u)
                if st == 200 and not is_bot_challenge(body):
                    cands += scan_html(body.decode("utf-8", "replace"), final)
                elif st == 200:
                    cands.append({"type": "none", "url": None, "page_url": u, "label": None, "status": "blocked",
                                  "note": "calendar page behind a JavaScript bot check (not bypassed)"})
            resolved = []
            for c in cands:
                resolved += resolve_hop(f, c) if c["type"].endswith("_page") else [c]
            cands = resolved
            for c in cands:                                   # theme markers alone do not prove the API is on
                if c["type"] == "tribe_rest" and c["status"] == "active":
                    base = c["url"].split("/wp-json/")[0]
                    if not probe_tribe(f, base):
                        c.update(status="no_public_feed",
                                 note="The Events Calendar markers on the site, but its REST route is not available")
            is_wp = bool(rec.get("tech_signals", {}).get("wordpress")) or any("wp-content" in t for _, t in raw[:2])
            if is_wp and not any(c["type"] == "tribe_rest" for c in cands):
                base = f"{urlparse(home).scheme}://{urlparse(home).netloc}"
                if probe_tribe(f, base):
                    cands.append({"type": "tribe_rest", "url": base + "/wp-json/tribe/events/v1/events",
                                  "page_url": base + "/events/", "label": None, "status": "active",
                                  "note": "The Events Calendar (WordPress) REST API"})
        else:
            cands = [c if not c["type"].endswith("_page") else {**c, "status": "unresolved",
                                                                 "note": c["note"] + " (needs online discovery)"}
                     for c in cands]
        merged: dict[tuple, dict] = {}
        for c in cands:
            key = (c["type"], c["url"], c["status"], c.get("note") if c["url"] is None else None)
            if key not in merged:
                merged[key] = {"parish_id": pid, **c}
                continue
            m = merged[key]
            m["label"] = m.get("label") or c.get("label")
            if re.search(r"calendar|event", c.get("page_url") or "", re.I) and not re.search(
                    r"calendar|event", m.get("page_url") or "", re.I):
                m["page_url"] = c["page_url"]
        kept = list(merged.values())
        if not any(c["status"] == "active" for c in kept):
            kept.append({"parish_id": pid, "type": "none", "url": None, "page_url": home, "label": None,
                         "status": "no_public_feed", "note": "no public calendar feed found on the crawled pages"})
        rows += kept
        log(f"  {pid}: " + ", ".join(f"{c['type']}:{c['status']}" for c in kept))
    # The diocesan calendar (The Events Calendar REST API on oakdiocese.org)
    rows.append({"parish_id": None, "owner_id": "dio-oakland", "type": "tribe_rest", "url": DIOCESE_API,
                 "page_url": "https://www.oakdiocese.org/events/", "label": "Diocese of Oakland events",
                 "status": "active", "note": "diocesan events calendar; venues matched to parishes"})
    # Manual registry
    if MANUAL.exists():
        for m in json.loads(MANUAL.read_text("utf-8")).get("feeds", []):
            rows.append({"label": None, "note": "registered by hand", "status": "active", **m})
    for r in rows:
        r.setdefault("owner_id", r.get("parish_id"))
        r["feed_id"] = feed_id(r["owner_id"] or "none", r["type"], r["url"] or r.get("page_url") or r["note"])
    return rows
