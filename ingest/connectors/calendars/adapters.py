"""Fetch and parse public calendar feeds into raw event dicts.

One polite HTTP client (honest User-Agent, >= `delay` seconds between requests to the same host, one retry
on 429/503, never on 403, no bot-check workarounds) and one parser per feed type:

    ics              any iCalendar feed (Google public basic.ics, Outlook published .ics, CCB, CatholicWebsite.com)
    tribe_rest       The Events Calendar WordPress REST API (/wp-json/tribe/events/v1/events)
    calendarwiz_rss  CalendarWiz RSS (next ~100 items only)
    mce_json         MyChurchEvents published calendar JSON (the file its public calendar page loads)

Every parser returns a list of raw events:
    {title, start, end, all_day, location, uid, recurring, categories, description, class_, url, venue_city}
`start`/`end` are timezone-aware datetimes (America/Los_Angeles) or dates for all-day events.
Descriptions are returned only so the privacy screen can read them; they are never written out.
"""
from __future__ import annotations

import gzip
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("America/Los_Angeles")
UA = "OaklandDioceseDashboard-research/0.1 (public-data collection; contact matthewdcolt@gmail.com)"


class Fetcher:
    def __init__(self, delay: float = 2.0, timeout: int = 40):
        self.delay, self.timeout = delay, timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
        self._last: dict[str, float] = {}
        self.log: list[dict] = []

    def get(self, url: str, params: dict | None = None, accept: str | None = None) -> tuple[int | str, bytes, str, str]:
        """Return (status, body, content_type, final_url). status is an int or 'error:<Type>'."""
        host = urlparse(url).netloc.lower()
        for attempt in range(2):
            wait = self.delay - (time.time() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            try:
                headers = {"Accept": accept} if accept else None
                r = self.s.get(url, params=params, timeout=self.timeout, headers=headers, allow_redirects=True)
                self._last[host] = time.time()
                body = r.content
                if body[:2] == b"\x1f\x8b":                      # stored-gzip blobs
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
                self.log.append({"url": r.url, "status": r.status_code, "bytes": len(body)})
                if r.status_code in (429, 503) and attempt == 0:
                    time.sleep(10)
                    continue
                return r.status_code, body, r.headers.get("content-type", ""), r.url
            except requests.RequestException as e:
                self._last[host] = time.time()
                self.log.append({"url": url, "status": f"error:{type(e).__name__}", "bytes": 0})
                if attempt == 0:
                    time.sleep(3)
                    continue
                return f"error:{type(e).__name__}", b"", "", url
        return "error:retries", b"", "", url


def is_bot_challenge(body: bytes) -> bool:
    head = body[:4000].lower()
    return (b"sucuri_cloudproxy" in head or b"javascript is required" in head or b"cf-chl" in head
            or b"just a moment..." in head or b"attention required" in head)


# ------------------------------------------------------------------------------------------- helpers
def to_local(v):
    if isinstance(v, datetime):
        return (v.replace(tzinfo=TZ) if v.tzinfo is None else v).astimezone(TZ)
    return v


def window(days: int, today: date | None = None) -> tuple[datetime, datetime]:
    d0 = today or datetime.now(TZ).date()
    ws = datetime.combine(d0, dtime.min, TZ)
    return ws, ws + timedelta(days=days)


def _txt(v) -> str:
    if v is None:
        return ""
    return html.unescape(str(v)).strip()


# ------------------------------------------------------------------------------------------- ics
def _relabel(v, display_tz: str | None):
    """Wall-clock time as the parish's own embed shows it (display_tz), relabelled as local Pacific time."""
    if not display_tz or not isinstance(v, datetime):
        return v
    return v.astimezone(ZoneInfo(display_tz)).replace(tzinfo=None).replace(tzinfo=TZ)


def parse_ics(body: bytes, ws: datetime, we: datetime, display_tz: str | None = None) -> tuple[dict, list[dict]]:
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(body)
    meta = {"calname": _txt(cal.get("X-WR-CALNAME")), "caldesc": _txt(cal.get("X-WR-CALDESC")),
            "prodid": _txt(cal.get("PRODID")), "vevents_total": sum(1 for c in cal.walk("VEVENT"))}
    out = []
    for comp in recurring_ical_events.of(cal, skip_bad_series=True).between(ws, we):
        if comp.name != "VEVENT":
            continue
        if _txt(comp.get("STATUS")).upper() == "CANCELLED":
            continue
        start = to_local(comp.decoded("DTSTART"))
        if "DTEND" in comp:
            end = to_local(comp.decoded("DTEND"))
        elif "DURATION" in comp:
            end = start + comp.decoded("DURATION")
        else:
            end = None
        start, end = _relabel(start, display_tz), _relabel(end, display_tz)
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        cats = []
        for c in (comp.get("CATEGORIES") if isinstance(comp.get("CATEGORIES"), list) else [comp.get("CATEGORIES")]):
            if c is None:
                continue
            cats += [_txt(x) for x in getattr(c, "cats", [c])]
        out.append({
            "title": _txt(comp.get("SUMMARY")), "start": start, "end": end, "all_day": all_day,
            "location": _txt(comp.get("LOCATION")) or None, "uid": _txt(comp.get("UID")),
            "recurring": any(k in comp for k in ("RRULE", "RDATE", "RECURRENCE-ID")),
            "categories": [c for c in cats if c], "description": _txt(comp.get("DESCRIPTION")),
            "class_": _txt(comp.get("CLASS")) or None, "url": _txt(comp.get("URL")) or None, "venue_city": None,
        })
    return meta, out


# ------------------------------------------------------------------------------------------- The Events Calendar
def _tribe_dt(s: str | None, tzname: str | None):
    if not s:
        return None
    tz = ZoneInfo(tzname) if tzname and "/" in tzname else TZ
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).astimezone(TZ)


def fetch_tribe(f: Fetcher, api: str, ws: datetime, we: datetime, per_page: int = 50,
                max_pages: int = 40) -> tuple[int | str, dict, list[dict]]:
    out, page, meta = [], 1, {}
    while True:
        st, body, ctype, _ = f.get(api, params={"start_date": ws.strftime("%Y-%m-%d"),
                                                "end_date": (we - timedelta(seconds=1)).strftime("%Y-%m-%d 23:59:59"),
                                                "per_page": per_page, "page": page},
                                   accept="application/json")
        if st != 200:
            return (st if page == 1 else 200), meta, out
        try:
            js = json.loads(body)
        except ValueError:
            return ("not_json" if page == 1 else 200), meta, out
        meta = {"total": js.get("total"), "total_pages": js.get("total_pages")}
        for ev in js.get("events", []):
            v = ev.get("venue") or {}
            if isinstance(v, list):
                v = v[0] if v else {}
            all_day = bool(ev.get("all_day"))
            start = _tribe_dt(ev.get("start_date"), ev.get("timezone"))
            end = _tribe_dt(ev.get("end_date"), ev.get("timezone"))
            if all_day:
                start, end = start.date() if start else None, end.date() if end else None
            out.append({
                "title": _txt(ev.get("title")), "start": start, "end": end, "all_day": all_day,
                "location": (_txt(v.get("venue")) if _txt(v.get("city")) and _txt(v.get("city")) in _txt(v.get("venue"))
                             else ", ".join(x for x in [_txt(v.get("venue")), _txt(v.get("city"))] if x)) or None,
                "uid": str(ev.get("id")), "recurring": bool(ev.get("id") and int(ev["id"]) >= 10_000_000),
                "categories": [_txt(c.get("name")) for c in (ev.get("categories") or [])],
                "description": re.sub(r"<[^>]+>", " ", ev.get("description") or ""), "class_": None,
                "url": ev.get("url"), "venue_name": _txt(v.get("venue")), "venue_city": _txt(v.get("city")) or None,
            })
        if page >= (js.get("total_pages") or 1) or page >= max_pages:
            return 200, meta, out
        page += 1


# ------------------------------------------------------------------------------------------- CalendarWiz RSS
_CW_TITLE = re.compile(
    r"^(?P<cat>[A-Z0-9 ,&'/().-]+?) - (?P<title>.+?) - (?P<when>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), "
    r"[A-Z][a-z]{2} \d{1,2}, \d{4}[^@]*?)(?: @ (?P<loc>.+))?$")


def parse_calendarwiz_rss(body: bytes, ws: datetime, we: datetime) -> tuple[dict, list[dict]]:
    root = ET.fromstring(body)
    items = list(root.iter("item"))
    out = []
    for it in items:
        raw_title = _txt(it.findtext("title"))
        guid = _txt(it.findtext("guid"))
        m = _CW_TITLE.match(raw_title)
        if not m:
            continue
        g = re.search(r"guid=(\d{10})(\d{10})", guid)
        when = m.group("when")
        has_time = bool(re.search(r"\d{1,2}:\d{2}\s*[ap]m", when))
        if g:
            s_utc, e_utc = (datetime.fromtimestamp(int(x), ZoneInfo("UTC")) for x in g.groups())
            start, end = s_utc.astimezone(TZ), e_utc.astimezone(TZ)
        else:
            start = datetime.strptime(re.search(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", when).group(0), "%b %d, %Y").replace(tzinfo=TZ)
            end = None
        if not has_time:
            start, end = start.date(), (end.date() if end else None)
        s_cmp = start if isinstance(start, datetime) else datetime.combine(start, dtime.min, TZ)
        if not (ws <= s_cmp < we):
            continue
        link = _txt(it.findtext("link"))
        out.append({"title": m.group("title"), "start": start, "end": end, "all_day": not has_time,
                    "location": m.group("loc"), "uid": link, "recurring": False,
                    "categories": [m.group("cat").title()], "description": "", "class_": None, "url": link,
                    "venue_city": None})
    return {"items": len(items)}, out


# ------------------------------------------------------------------------------------------- MyChurchEvents JSON
_MCE_BIT = {6: 1, 0: 2, 1: 4, 2: 8, 3: 16, 4: 32, 5: 64}   # python weekday -> MCE weekday bit (Sun=1 ... Sat=64)


def parse_mce_json(body: bytes, ws: datetime, we: datetime, page_url: str) -> tuple[dict, list[dict]]:
    js = json.loads(body)
    groups: dict[int, str] = {}

    def walk(gs):
        for g in gs or []:
            groups[g["id"]] = g.get("name") or ""
            walk(g.get("children"))
    walk(js.get("interestGroups"))
    rooms = {r.get("resourceId"): r.get("name") for r in js.get("resources") or []}
    tz = ZoneInfo(js.get("timeZoneId") or "America/Los_Angeles")
    d0, d1 = ws.date(), we.date()
    stats = {"events_total": len(js.get("events") or []), "hidden_from_public_calendar": 0,
             "unsupported_recurrence": 0}
    out = []
    for e in js.get("events") or []:
        if e.get("hideOnMainCalendar"):
            stats["hidden_from_public_calendar"] += 1
            continue
        rec = e.get("recurrence") or {}
        dates: list[date] = []
        if e.get("date"):
            dates = [date.fromisoformat(e["date"][:10])]
        elif rec:
            ft, iv = rec.get("frequencyType"), rec.get("interval")
            if not (isinstance(ft, int) and 1 <= ft <= 127 and iv == 1):
                stats["unsupported_recurrence"] += 1          # codes >= 128 are not documented; skip, never guess
                continue
            a = max(date.fromisoformat(rec["startDate"][:10]), d0)
            b = min(date.fromisoformat(rec["endDate"][:10]) if rec.get("endDate") else d1, d1)
            skip = {x[:10] for x in rec.get("excludedDates") or []}
            d = a
            while d <= b:
                if ft & _MCE_BIT[d.weekday()] and d.isoformat() not in skip:
                    dates.append(d)
                d += timedelta(days=1)
        cats = [groups.get(i, "") for i in e.get("interestGroupIds") or []]
        loc = e.get("location") or ", ".join(filter(None, (rooms.get(r) for r in e.get("resourceIds") or [])))
        for d in dates:
            if not (d0 <= d < d1):
                continue
            st, et = e.get("startTime"), e.get("endTime")
            if st and not e.get("isAllDayEvent"):
                start = datetime.combine(d, dtime.fromisoformat(st), tz).astimezone(TZ)
                end = datetime.combine(d, dtime.fromisoformat(et), tz).astimezone(TZ) if et else None
                all_day = False
            else:
                start, end, all_day = d, None, True
            out.append({"title": _txt(e.get("name")), "start": start, "end": end, "all_day": all_day,
                        "location": loc or None, "uid": str(e.get("eventId")), "recurring": bool(rec),
                        "categories": [c for c in cats if c], "description": e.get("details") or "",
                        "class_": None, "url": page_url, "venue_city": None})
    return stats, out
