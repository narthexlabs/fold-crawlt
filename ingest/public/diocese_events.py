"""Diocese of Oakland events calendar (The Events Calendar REST API) -> data/real/diocese_events.json.

Paginates https://www.oakdiocese.org/wp-json/tribe/events/v1/events with start_date = today - 2 years
(the API honours start_date/end_date; in practice the archive only reaches back to 2026-04-24, and
recurring series are published out to 2028-10). Keeps title, start, end, all_day, venue, organizer,
categories, url, plus a parish canon_key guess from the venue for later matching.

The site's Azure WAF intermittently answers 403 to scripted clients. Each page is retried with
backoff; if pages still fail, previously captured pages in ingest/public/manual/events_webfetch/
(*.jsonl, captured with Claude's WebFetch tool on 2026-10-25) are merged in and the gap is reported.

Usage: python ingest/public/diocese_events.py [--years 2] [--per-page 50] [--offline]
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MANUAL, REAL, canon_key, get, now_iso, save_json  # noqa: E402

API = "https://www.oakdiocese.org/wp-json/tribe/events/v1/events"
CAPTURE_DIR = MANUAL / "events_webfetch"
OUT = REAL / "diocese_events.json"


def clean(s):
    if s is None:
        return None
    s = html.unescape(str(s)).replace(" ", " ").strip()
    return s or None


def norm(ev: dict) -> dict:
    """Normalise a raw API event (or an already-normalised captured row)."""
    if "start_date" in ev:  # raw API shape
        v = ev.get("venue") or {}
        if isinstance(v, list):
            v = v[0] if v else {}
        orgs = ev.get("organizer") or []
        row = {"id": ev.get("id"), "title": clean(ev.get("title")), "start": ev.get("start_date"),
               "end": ev.get("end_date"), "all_day": ev.get("all_day"),
               "venue": clean(v.get("venue")), "venue_address": clean(v.get("address")), "venue_city": clean(v.get("city")),
               "organizer": [clean(o.get("organizer")) for o in orgs if isinstance(o, dict)],
               "categories": [clean(c.get("name")) for c in (ev.get("categories") or [])],
               "url": ev.get("url"), "timezone": ev.get("timezone")}
    else:
        row = {k: ev.get(k) for k in ("id", "title", "start", "end", "all_day", "venue", "venue_city", "organizer", "categories", "url")}
        row["title"] = clean(row["title"])
        row["venue"] = clean(row["venue"])
        row["categories"] = [clean(c) for c in row.get("categories") or []]
        row["venue_address"] = None
        row["timezone"] = None
    row["venue_canon_key"] = canon_key(row["venue"].split(",")[0], row["venue_city"]) if row.get("venue") and row.get("venue_city") else None
    row["is_bishop_calendar"] = any("Bishop" in (c or "") for c in row["categories"])
    row["is_recurring_instance"] = bool(row["id"] and int(row["id"]) >= 10_000_000)
    return row


def fetch_page(params: dict, tries: int = 4) -> dict | None:
    for attempt in range(tries):
        r = get(API, timeout=40, params=params)
        if r is not None and r.ok:
            try:
                return r.json()
            except ValueError:
                pass
        time.sleep(3 * (attempt + 1))
    return None


def load_captures() -> dict[int, dict]:
    out = {}
    for f in sorted(CAPTURE_DIR.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                ev = json.loads(ln)
                out[ev["id"]] = norm(ev)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2)
    ap.add_argument("--per-page", type=int, default=50)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()

    start = (date.today() - timedelta(days=int(365 * a.years))).isoformat()
    params = {"start_date": start, "per_page": a.per_page, "page": 1}
    events: dict[int, dict] = {}
    failed_pages, total, total_pages = [], None, None
    if not a.offline:
        first = fetch_page(params)
        if first:
            total, total_pages = first.get("total"), first.get("total_pages")
            for ev in first.get("events", []):
                events[ev["id"]] = norm(ev)
            for p in range(2, (total_pages or 1) + 1):
                time.sleep(0.8)
                js = fetch_page({**params, "page": p})
                if js is None:
                    failed_pages.append(p)
                    continue
                for ev in js.get("events", []):
                    events[ev["id"]] = norm(ev)
        else:
            failed_pages.append(1)
    live = len(events)

    captured = load_captures()
    from_capture = 0
    for k, v in captured.items():
        if k not in events:
            events[k] = v
            from_capture += 1
    if total is None:
        total = 369  # reported by the API on 2026-10-25 for start_date=2024-10-25
        total_note = "API unreachable this run; total from the 2026-10-25 capture"
    else:
        total_note = "reported by API this run"

    rows = sorted(events.values(), key=lambda r: (r["start"] or "", r["id"]))
    cats: dict[str, int] = {}
    for r in rows:
        for c in r["categories"] or ["(none)"]:
            cats[c] = cats.get(c, 0) + 1
    save_json(OUT, {
        "source": API, "query": {"start_date": start, "per_page": a.per_page}, "retrieved": now_iso(),
        "api_total": total, "api_total_note": total_note, "count": len(rows),
        "missing": max(0, (total or 0) - len(rows)),
        "from_live_api": live, "from_webfetch_capture": from_capture, "failed_live_pages": failed_pages,
        "date_range": [rows[0]["start"] if rows else None, rows[-1]["start"] if rows else None],
        "notes": ["start_date two years back is accepted, but the archive's earliest event is 2026-04-24 "
                  "(older events appear to have been purged in the 2025-26 site rebuild).",
                  "organizer is almost always empty on this site; categories carry the owning office "
                  "('Bishop's official calendar', 'SPRED', 'Immigration', ...).",
                  "ids >= 10,000,000 are generated instances of recurring series (Chess Academy, That Man is You!, ...).",
                  "venue_canon_key is a best-effort parish key from the venue name + city; not every venue is a parish."],
        "category_counts": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "events": rows})
    print(f"events: {len(rows)} of {total} ({live} live, {from_capture} from capture); failed live pages: {failed_pages}")


if __name__ == "__main__":
    main()
