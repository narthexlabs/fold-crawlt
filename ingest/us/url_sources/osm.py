"""OpenStreetMap (Overpass) -> Catholic places of worship with a website tag, per US state.

One Overpass query at a time, 25 s pause between queries, one retry after 60 s on 429/504, stop after 3 failures in
a row (a connection the sandbox cannot make is recorded as `unreachable_via_proxy`, not worked around). Raw responses
are cached in data/raw/us/url_sources/osm/<ST>.json (gitignored). Output: data/real/us/url_sources/osm.json
(source "OpenStreetMap contributors, ODbL"). Committing every 10 states is done by the caller (see README).

Resume: python3 ingest/us/url_sources/osm.py            (states with a cache file are skipped)
        python3 ingest/us/url_sources/osm.py --states TX,NY
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common_fetch as cf  # noqa: E402

ROOT = cf.ROOT
OUT = ROOT / "data" / "real" / "us" / "url_sources"
RAW = ROOT / "data" / "raw" / "us" / "url_sources" / "osm"
ENDPOINT = "https://overpass.kumi.systems/api/interpreter"   # overpass-api.de drops the tunnel from the sandbox (2026-10-31)
STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
          "RI SC SD TN TX UT VT VA WA WV WI WY").split()
PAUSE, RETRY_PAUSE, MAX_FAILS = 25, 60, 3

Q = """[out:json][timeout:180];
area["ISO3166-2"="US-%s"]->.a;
nwr(area.a)["amenity"="place_of_worship"]["religion"="christian"]["denomination"~"^(catholic|roman_catholic)$"]["website"];
out center tags;
nwr(area.a)["amenity"="place_of_worship"]["religion"="christian"]["denomination"~"^(catholic|roman_catholic)$"]["contact:website"][!"website"];
out center tags;
"""


def fetch_state(st: str) -> tuple[dict | None, str]:
    url = ENDPOINT + "?data=" + urllib.parse.quote(Q % st)
    for attempt in range(2):
        r = cf.fs.http_get(url, timeout=200, max_bytes=80_000_000)
        if r["status"] == 200:
            try:
                return json.loads(r["body"]), "ok"
            except ValueError:
                return None, "bad_json"
        why = cf.fs.classify(r) if r["status"] is None else f"http_{r['status']}"
        if r["status"] in (429, 504) and attempt == 0:
            print(f"{st}: {why}, retry in {RETRY_PAUSE}s", file=sys.stderr)
            time.sleep(RETRY_PAUSE)
            continue
        return None, why
    return None, "retry_failed"


def record(el: dict, st: str, fetched_at: str) -> dict | None:
    t = el.get("tags", {})
    web = t.get("website") or t.get("contact:website")
    if not web:
        return None
    c = el.get("center") or el
    return {"osm_type": el["type"], "osm_id": el["id"], "name": t.get("name"), "website": web.strip(),
            "lat": c.get("lat"), "lng": c.get("lon"), "state": st, "city": t.get("addr:city"),
            "source": "OpenStreetMap contributors, ODbL", "source_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "fetched_at": fetched_at}


def build(states: list[str]) -> dict:
    recs, status = [], {}
    for st in states:
        p = RAW / f"{st}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        fa = d.get("_fetched_at")
        for el in d.get("elements", []):
            r = record(el, st, fa)
            if r:
                recs.append(r)
    by = Counter(r["state"] for r in recs)
    return {"source": "OpenStreetMap contributors, ODbL (Overpass API)", "license": "ODbL 1.0, https://www.openstreetmap.org/copyright",
            "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "counts": {"records": len(recs), "distinct_websites": len({r["website"].lower().rstrip('/') for r in recs}),
                       "by_state": dict(sorted(by.items(), key=lambda kv: -kv[1]))},
            "records": recs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states")
    a = ap.parse_args()
    states = a.states.upper().split(",") if a.states else STATES
    RAW.mkdir(parents=True, exist_ok=True)
    fails, first = 0, True
    for st in states:
        if (RAW / f"{st}.json").exists():
            continue
        if not first:
            time.sleep(PAUSE)
        first = False
        d, why = fetch_state(st)
        if d is None:
            fails += 1
            print(f"{st}: FAILED {why} ({fails}/{MAX_FAILS})", file=sys.stderr)
            (RAW / f"{st}.failed.json").write_text(json.dumps({"state": st, "why": why, "at": datetime.now(timezone.utc).isoformat()}))
            if fails >= MAX_FAILS:
                print("stopping after 3 failures", file=sys.stderr)
                break
            continue
        fails = 0
        d["_fetched_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        (RAW / f"{st}.json").write_text(json.dumps(d))
        print(f"{st}: {len(d.get('elements', []))} elements", file=sys.stderr)
    out = build(STATES)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "osm.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(out["counts"]["records"]))
    return 0 if fails < MAX_FAILS else 2


if __name__ == "__main__":
    sys.exit(main())
