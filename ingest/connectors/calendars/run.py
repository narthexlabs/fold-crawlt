"""Public parish calendar connector: discover -> fetch -> expand -> screen -> normalize -> write.

    python3 ingest/connectors/calendars/run.py                 # reuse feeds.json, refetch all feeds
    python3 ingest/connectors/calendars/run.py --rediscover    # also re-scan parish pages for feeds
    python3 ingest/connectors/calendars/run.py --rediscover --offline-discovery   # scan raw HTML only
    options: --days 90  --delay 2.0  --only joseph-fremont,francis-assisi-concord  --keep-raw

Needs: requests, icalendar, recurring-ical-events (pip install icalendar recurring-ical-events).

Writes data/real/calendars/events.json, feeds.json and README.md. Reruns are idempotent: event ids are a
hash of (feed, source uid, start), so a loader can upsert on id. Raw feed bodies are not kept unless
--keep-raw (then under data/raw/calendars/feeds/, git-ignored; charter R22: at most 30 days).

Privacy: see privacy.py. Only public parish events are kept; anything that names a private person
(funerals, weddings, quinceañeras, individual baptisms, Mass intentions, rentals, appointments) is dropped
and only counted. Descriptions are never stored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ingest" / "public"))

from adapters import (TZ, Fetcher, is_bot_challenge, fetch_tribe, parse_calendarwiz_rss,  # noqa: E402
                      parse_ics, parse_mce_json, window)
from categorize import guess_category  # noqa: E402
from discover import discover  # noqa: E402
from privacy import feed_excluded, screen_event  # noqa: E402

OUT = ROOT / "data" / "real" / "calendars"
RAW_FEEDS = ROOT / "data" / "raw" / "calendars" / "feeds"
ENTITIES = ROOT / "data" / "seed" / "entities.json"
SITES = ROOT / "data" / "real" / "parish_sites.json"
DIOCESE_SNAPSHOT = ROOT / "data" / "real" / "diocese_events.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else v.isoformat()


def parish_index() -> tuple[dict[str, str], dict[str, str]]:
    from common import canon_key  # ingest/public/common.py
    ents = json.loads(ENTITIES.read_text("utf-8"))
    rows = ents if isinstance(ents, list) else next(v for v in ents.values() if isinstance(v, list))
    keys, names = {}, {}
    for e in rows:
        if e.get("type") in ("parish", "mission"):
            keys[canon_key(e["name"], e.get("city"))] = e["id"]
            names[e["id"]] = e["name"] + (f", {e['city']}" if e.get("city") else "")
    return keys, names


def match_parish(venue: str | None, city: str | None, keys: dict[str, str]) -> str | None:
    from common import canon_key
    if not venue:
        return None
    parts = [p.strip() for p in venue.split(",") if p.strip()]
    city = city or (parts[-1] if len(parts) > 1 else None)
    for part in parts + [venue]:                      # "Holy Family Care Center, St. Francis of Assisi, Concord"
        k = canon_key(part, city)
        if k in keys:
            return keys[k]
    return None


# ------------------------------------------------------------------------------------------- fetch one feed
def fetch_feed(f: Fetcher, row: dict, ws, we, keep_raw: bool) -> tuple[str | int, dict, list[dict]]:
    typ, url = row["type"], row["url"]
    if typ == "tribe_rest":
        st, meta, evs = fetch_tribe(f, url, ws, we)
        return st, meta, evs
    st, body, ctype, final = f.get(url)
    if st != 200:
        return st, {}, []
    if is_bot_challenge(body):
        return "bot_challenge", {}, []
    if keep_raw:
        RAW_FEEDS.mkdir(parents=True, exist_ok=True)
        (RAW_FEEDS / (row["feed_id"].replace(":", "_") + ".raw")).write_bytes(body)
    try:
        if typ == "ics":
            if b"BEGIN:VCALENDAR" not in body[:2000]:
                return "not_ical", {}, []
            return 200, *parse_ics(body, ws, we, row.get("display_tz"))
        if typ == "calendarwiz_rss":
            return 200, *parse_calendarwiz_rss(body, ws, we)
        if typ == "mce_json":
            return 200, *parse_mce_json(body, ws, we, row.get("page_url") or url)
    except Exception as e:  # a malformed feed must not stop the run
        return f"parse_error:{type(e).__name__}", {}, []
    return f"unknown_type:{typ}", {}, []


def diocese_snapshot(ws, we) -> list[dict]:
    """Fallback when oakdiocese.org's WAF refuses the live API: the 2026-10-26 capture, windowed."""
    if not DIOCESE_SNAPSHOT.exists():
        return []
    js = json.loads(DIOCESE_SNAPSHOT.read_text("utf-8"))
    out = []
    for e in js.get("events", []):
        if not e.get("start"):
            continue
        s = datetime.strptime(e["start"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        if not (ws <= s < we):
            continue
        en = datetime.strptime(e["end"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ) if e.get("end") else None
        allday = bool(e.get("all_day"))
        out.append({"title": e.get("title") or "", "start": s.date() if allday else s,
                    "end": (en.date() if en else None) if allday else en, "all_day": allday,
                    "location": (e.get("venue") if e.get("venue_city") and e["venue_city"] in (e.get("venue") or "")
                                 else ", ".join(x for x in [e.get("venue"), e.get("venue_city")] if x)) or None,
                    "uid": str(e.get("id")), "recurring": bool(e.get("is_recurring_instance")),
                    "categories": e.get("categories") or [], "description": "", "class_": None,
                    "url": e.get("url"), "venue_name": e.get("venue"), "venue_city": e.get("venue_city")})
    return out


# ------------------------------------------------------------------------------------------- normalize
FACILITY = re.compile(r"facilit|venue|room|hall|gym|chapel|church|center|centre", re.I)


def is_facility_feed(row: dict) -> bool:
    """A room/facility booking calendar (the parish publishes it, but it lists uses of rooms, not offerings)."""
    lf = row.get("last_fetch") or {}
    return bool(row.get("label") and FACILITY.search(row["label"])) or bool(
        re.search(r"facilit|venue", f"{lf.get('calendar_name') or ''} {row.get('page_url') or ''}", re.I))


def normalize(raw: dict, row: dict, keys: dict, fetched_at: str, facility: bool) -> tuple[dict | None, str | None]:
    keep, reason, title, loc = screen_event(raw["title"], raw.get("location"), raw.get("categories"),
                                            raw.get("description"), raw.get("class_"))
    if not keep:
        return None, reason
    owner = row.get("owner_id")
    pid = row.get("parish_id")
    if owner == "dio-oakland":
        pid = match_parish(raw.get("venue_name") or raw.get("location"), raw.get("venue_city"), keys)
    else:
        loc = loc or row.get("label")                 # e.g. the room a Microsoft 365 room calendar belongs to
    tags = []
    if any("bishop" in (c or "").lower() for c in raw.get("categories") or []):
        tags.append("bishops_calendar")
    if facility:
        tags.append("facility_calendar")
    src = raw.get("url") if row["type"] in ("tribe_rest", "calendarwiz_rss") and raw.get("url") else row.get("page_url")
    uid_basis = f"{row['feed_id']}|{raw.get('uid')}|{iso(raw['start'])}"
    return {
        "id": hashlib.sha1(uid_basis.encode()).hexdigest()[:16],
        "parish_id": pid,
        "owner_id": owner,
        "title": title,
        "start": iso(raw["start"]),
        "end": iso(raw.get("end")),
        "all_day": bool(raw.get("all_day")),
        "location": loc,
        "category": guess_category(title, raw.get("categories")),
        "recurring": bool(raw.get("recurring")),
        "tags": tags,
        "source_url": src,
        "feed_id": row["feed_id"],
        "fetched_at": fetched_at,
    }, None


# ------------------------------------------------------------------------------------------- output
def write_events(path: Path, head: dict, events: list[dict]) -> None:
    lines = json.dumps(head, indent=2, ensure_ascii=False)[:-2]  # drop closing brace
    body = ",\n".join("    " + json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in events)
    path.write_text(lines + ',\n  "events": [\n' + body + "\n  ]\n}\n", "utf-8")


PLATFORM = [("calendar.google.com", "Google Calendar"), ("outlook.office365", "Microsoft 365 (Outlook)"),
            ("ccbchurch.com", "CCB / Pushpay ChMS"), ("calendarwiz", "CalendarWiz"),
            ("mceproduction", "MyChurchEvents"), ("/calendar.ics", "CatholicWebsite.com"),
            ("tribe/events", "The Events Calendar (WordPress)"),
            ("all-in-one-event-calendar", "All-in-One Event Calendar (WordPress)")]


def platform(url: str | None) -> str:
    for k, v in PLATFORM:
        if url and k in url:
            return v
    return "iCal"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--rediscover", action="store_true")
    ap.add_argument("--offline-discovery", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--readme-only", action="store_true", help="rebuild README.md from events.json and feeds.json")
    a = ap.parse_args()
    if a.readme_only:
        from readme import build_readme
        txt = (OUT / "events.json").read_text("utf-8")
        head = json.loads(txt[:txt.index(',\n  "events": [')] + "\n}")
        rows = json.loads((OUT / "feeds.json").read_text("utf-8"))["feeds"]
        (OUT / "README.md").write_text(build_readme(head, rows, parish_index()[1], platform), "utf-8")
        print("README.md rebuilt")
        return
    only = {x.strip() for x in a.only.split(",") if x.strip()} or None
    OUT.mkdir(parents=True, exist_ok=True)
    feeds_path = OUT / "feeds.json"

    prev = json.loads(feeds_path.read_text("utf-8")) if feeds_path.exists() else None
    if a.rediscover or prev is None:
        print("discovering feeds ...")
        rows = discover(online=not a.offline_discovery, delay=a.delay, only=only)
        discovered_at = now_iso()
    else:
        rows, discovered_at = prev["feeds"], prev.get("discovered_at")
        manual = HERE / "feeds_manual.json"
        known = {r["feed_id"] for r in rows}
        if manual.exists():
            from discover import feed_id
            for m in json.loads(manual.read_text("utf-8")).get("feeds", []):
                r = {"label": None, "note": "registered by hand", "status": "active", **m}
                r.setdefault("owner_id", r.get("parish_id"))
                r["feed_id"] = feed_id(r["owner_id"] or "none", r["type"], r["url"])
                if r["feed_id"] not in known:
                    rows.append(r)

    keys, names = parish_index()
    ws, we = window(a.days)
    f = Fetcher(delay=a.delay)
    events: list[dict] = []
    excluded = Counter()
    for row in rows:
        if row.get("status") != "active" or not row.get("url"):
            continue
        if only and row.get("owner_id") not in only and row.get("owner_id") != "dio-oakland":
            continue
        fetched_at = now_iso()
        st, meta, raws = fetch_feed(f, row, ws, we, a.keep_raw)
        source_note = None
        if st != 200 and row.get("owner_id") == "dio-oakland":
            raws, source_note = diocese_snapshot(ws, we), "live API refused; used data/real/diocese_events.json"
            fetched_at = json.loads(DIOCESE_SNAPSHOT.read_text("utf-8")).get("retrieved", fetched_at)
        fx = feed_excluded(meta.get("calname"), meta.get("caldesc")) if meta else None
        if fx:
            row.update(status="excluded", url=None,
                       note=f"calendar is titled as {fx.replace('_', ' ')}; it names private people, so it is "
                            f"not ingested and its feed address is not stored")
            row["last_fetch"] = {"status": st, "fetched_at": fetched_at}
            print(f"  EXCLUDED {row['feed_id']}: {fx}")
            continue
        reasons = Counter()
        kept = 0
        facility = is_facility_feed({**row, "last_fetch": {"calendar_name": meta.get("calname")}})
        for raw in raws:
            ev, why = normalize(raw, row, keys, fetched_at, facility)
            if ev is None:
                reasons[why] += 1
                continue
            events.append(ev)
            kept += 1
        excluded.update(reasons)
        row["last_fetch"] = {"status": st, "fetched_at": fetched_at, "events_in_window": len(raws), "kept": kept,
                             "excluded": dict(reasons.most_common()), **({"note": source_note} if source_note else {}),
                             **({"calendar_name": meta.get("calname")} if meta.get("calname") else {}),
                             **({k: v for k, v in meta.items() if k in ("hidden_from_public_calendar",
                                                                         "unsupported_recurrence", "items")})}
        print(f"  {row['feed_id']:55s} {str(st):>5} raw={len(raws):5d} kept={kept:5d} excl={sum(reasons.values())}")

    # de-duplicate within an owner (same event on two room calendars, or repeated in one feed)
    merged: dict[tuple, dict] = {}
    for e in events:
        k = (e["owner_id"], e["parish_id"], re.sub(r"\W+", " ", e["title"].lower()).strip(), e["start"])
        if k in merged:
            m = merged[k]
            if e["location"] and m["location"] and e["location"] not in m["location"]:
                m["location"] = f"{m['location']}; {e['location']}"
            m["location"] = m["location"] or e["location"]
            continue
        merged[k] = e
    events = sorted(merged.values(), key=lambda e: (e["start"], e["parish_id"] or "", e["title"]))

    per = defaultdict(lambda: {"events": 0, "own_feed": 0, "diocesan": 0, "categories": Counter()})
    for e in events:
        p = per[e["parish_id"] or f"({e['owner_id']}, not at a parish)"]
        p["events"] += 1
        p["diocesan" if e["owner_id"] == "dio-oakland" else "own_feed"] += 1
        p["categories"][e["category"]] += 1
    parish_feeds = [r for r in rows if r.get("owner_id") not in (None, "dio-oakland")]
    with_feed = sorted({r["parish_id"] for r in parish_feeds if r["status"] == "active"})
    head = {
        "generated_at": now_iso(),
        "window": {"start": ws.date().isoformat(), "end_exclusive": we.date().isoformat(), "days": a.days,
                   "timezone": "America/Los_Angeles"},
        "source": "Public parish calendar feeds (see feeds.json) and the diocesan events API; built by "
                  "ingest/connectors/calendars/run.py",
        "fields": {
            "id": "stable hash of feed + source uid + start (upsert key)",
            "parish_id": "entity id of the parish (null for diocesan events not held at a parish)",
            "owner_id": "who publishes the calendar: a parish id or dio-oakland",
            "title": "event title after privacy cleaning", "start": "ISO 8601 with offset, or a date if all_day",
            "end": "same format; for all-day events the day after the last day (iCalendar convention)",
            "location": "room or venue as published (private homes removed)",
            "category": "keyword guess (categorize.py); not a judgment", "recurring": "an instance of a series",
            "tags": "bishops_calendar = on the bishop's official calendar; facility_calendar = from a room or "
                    "facility booking calendar the parish publishes (uses of rooms, not only offerings)",
            "source_url": "public page or event page", "feed_id": "row in feeds.json",
            "fetched_at": "when the feed was read (UTC)"},
        "privacy": "Only public parish events. Funerals, memorials, weddings, quinceañeras, individual baptisms, "
                   "Mass intentions, rentals, appointments and anything naming a private person are dropped and "
                   "only counted; descriptions are never stored.",
        "counts": {"events": len(events), "parishes_with_feed": len(with_feed),
                   "diocesan_events": sum(1 for e in events if e["owner_id"] == "dio-oakland"),
                   "diocesan_events_at_parishes": sum(1 for e in events if e["owner_id"] == "dio-oakland" and e["parish_id"]),
                   "parishes_with_events": len([p for p in per if not p.startswith("(")]),
                   "feeds_active": sum(1 for r in rows if r["status"] == "active"),
                   "excluded_events_by_reason": dict(excluded.most_common())},
        "by_parish": {k: {"name": names.get(k, k), "events": v["events"], "from_own_feed": v["own_feed"],
                          "from_diocesan_calendar": v["diocesan"], "categories": dict(v["categories"].most_common())}
                      for k, v in sorted(per.items())},
    }
    if only:
        print(f"--only run: not writing outputs ({len(events)} events)")
        return
    write_events(OUT / "events.json", head, events)
    feeds_doc = {"generated_at": now_iso(), "discovered_at": discovered_at,
                 "statuses": dict(Counter(r["status"] for r in rows)), "feeds": rows}
    (OUT / "feeds.json").write_text(json.dumps(feeds_doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    from readme import build_readme
    (OUT / "README.md").write_text(build_readme(head, rows, names, platform), "utf-8")
    print(f"events: {len(events)}; parishes with a feed: {len(with_feed)}; excluded: {dict(excluded)}")


if __name__ == "__main__":
    main()
