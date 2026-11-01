"""Wikidata -> US Catholic church/parish/cathedral/basilica items with an official website (P856).

SPARQL against https://query.wikidata.org/sparql, one query at a time, paged with LIMIT/OFFSET (items are first
selected in a cheap sub-query ordered by item id, then labels/state are attached). Output:
data/real/us/url_sources/wikidata.json. Wikidata data is CC0. Raw pages are cached in data/raw/us/url_sources/wikidata/.

Resume: python3 ingest/us/url_sources/wikidata.py   (cached pages are reused; --refresh to refetch)
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
RAW = ROOT / "data" / "raw" / "us" / "url_sources" / "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"
PAGE = 1500

# Q1088552 Catholic church building, Q102496 parish, Q2977 cathedral, Q120560 minor basilica (ids checked 2026-10-31).
# Q35657 = state of the United States; Q1352230 = US territory is not used.
CLASSES = "wd:Q1088552 wd:Q102496 wd:Q2977 wd:Q120560"

SUBCLASS_Q = "SELECT DISTINCT ?c WHERE { VALUES ?r { %s } ?c wdt:P279* ?r }" % CLASSES

# Step B: items whose P31 is one of the (already expanded) classes, plain triples, no label service and no ORDER BY
# sub-select (both made the public endpoint time out). Labels and the state come from small batched follow-up queries.
QUERY = """
SELECT ?item ?website ?coord ?admin ?diocese WHERE {
  VALUES ?cls { %s }
  ?item wdt:P31 ?cls ; wdt:P17 wd:Q30 ; wdt:P856 ?website .
  OPTIONAL { ?item wdt:P625 ?coord }
  OPTIONAL { ?item wdt:P131 ?admin }
  OPTIONAL { ?item wdt:P708 ?diocese }
}
"""
LABEL_Q = 'SELECT ?x ?l WHERE { VALUES ?x { %s } ?x rdfs:label ?l FILTER(lang(?l)="en") }'
STATE_Q = "SELECT ?item ?st WHERE { VALUES ?item { %s } ?item wdt:P131+ ?st . ?st wdt:P31 wd:Q35657 }"
BATCH = 200

ABBR = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
        "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
        "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
        "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
        "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
        "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
        "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
        "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
        "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR", "Guam": "GU",
        "U.S. Virgin Islands": "VI", "American Samoa": "AS", "Northern Mariana Islands": "MP"}


def classes(refresh: bool) -> str:
    cache = RAW / "classes.json"
    if cache.exists() and not refresh:
        d = json.loads(cache.read_text())
    else:
        d = sparql(SUBCLASS_Q, cache)
    return " ".join("wd:" + b["c"]["value"].rsplit("/", 1)[-1] for b in d["results"]["bindings"])


def sparql(q: str, cache: Path) -> dict | None:
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
    for attempt in range(3):
        r = cf.fs.http_get(url, accept="application/sparql-results+json", timeout=120, max_bytes=60_000_000)
        if r["status"] == 200:
            d = json.loads(r["body"])
            RAW.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(d))
            return d
        print(f"{cache.name}: status {r['status']} err {r['err']} (attempt {attempt + 1})", file=sys.stderr)
        time.sleep(30 * (attempt + 1))
    return None


def parse_point(s: str | None):
    if not s or not s.startswith("Point("):
        return None, None
    try:
        lng, lat = s[6:-1].split()
        return round(float(lat), 6), round(float(lng), 6)
    except ValueError:
        return None, None


def qid_of(v: str | None) -> str | None:
    return v.rsplit("/", 1)[-1] if v and "/entity/Q" in v else None


def batched(tmpl: str, ids: list[str], tag: str, refresh: bool) -> list[dict]:
    rows = []
    for i in range(0, len(ids), BATCH):
        cache = RAW / f"{tag}-{i:05d}.json"
        if cache.exists() and not refresh:
            d = json.loads(cache.read_text())
        else:
            d = sparql(tmpl % " ".join("wd:" + q for q in ids[i:i + BATCH]), cache)
            time.sleep(2)
        if d is None:
            raise SystemExit(f"{tag} batch {i} failed")
        rows += d["results"]["bindings"]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cls = classes(a.refresh)
    cache = RAW / "items.json"
    d = json.loads(cache.read_text()) if cache.exists() and not a.refresh else sparql(QUERY % cls, cache)
    if d is None:
        return 1
    v = lambda b, k: b.get(k, {}).get("value")  # noqa: E731
    items: dict[str, dict] = {}
    for b in d["results"]["bindings"]:
        qid = qid_of(v(b, "item"))
        r = items.setdefault(qid, {"websites": set(), "lat": None, "lng": None, "admin": None, "diocese": None})
        r["websites"].add(v(b, "website"))
        if r["lat"] is None:
            r["lat"], r["lng"] = parse_point(v(b, "coord"))
        r["admin"] = r["admin"] or qid_of(v(b, "admin"))
        r["diocese"] = r["diocese"] or qid_of(v(b, "diocese"))
    ids = sorted(items, key=lambda q: int(q[1:]))
    lab_ids = sorted(set(ids) | {r[k] for r in items.values() for k in ("admin", "diocese") if r[k]}, key=lambda q: int(q[1:]))
    labels = {qid_of(v(b, "x")): v(b, "l") for b in batched(LABEL_Q, lab_ids, "labels", a.refresh)}
    state = {}
    for b in batched(STATE_Q, ids, "state", a.refresh):
        state.setdefault(qid_of(v(b, "item")), qid_of(v(b, "st")))
    slab = {qid_of(v(b, "x")): v(b, "l") for b in batched(LABEL_Q, sorted(set(state.values())), "slabels", a.refresh)}
    state = {k: slab.get(q) for k, q in state.items()}
    recs = []
    for qid in ids:
        r = items[qid]
        name = labels.get(qid)
        st = ABBR.get(state.get(qid) or "")
        for w in sorted(w for w in r["websites"] if w):
            recs.append({"qid": qid, "name": name, "website": w, "lat": r["lat"], "lng": r["lng"], "state": st,
                         "city": labels.get(r["admin"]) if r["admin"] else None, "admin_label": labels.get(r["admin"]) if r["admin"] else None,
                         "diocese": labels.get(r["diocese"]) if r["diocese"] else None,
                         "source_url": f"https://www.wikidata.org/wiki/{qid}", "fetched_at": fetched_at, "license": "CC0"})
    by = Counter(r["state"] or "??" for r in recs)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wikidata.json").write_text(json.dumps({
        "source": "Wikidata SPARQL (query.wikidata.org)", "license": "CC0", "fetched_at": fetched_at,
        "query_classes": "P31 in P279* of Q1088552 Catholic church building, Q102496 parish, Q2977 cathedral, Q120560 minor basilica; P17=Q30; P856 present",
        "counts": {"items": len(items), "records": len(recs), "distinct_websites": len({r["website"].lower().rstrip("/") for r in recs}),
                   "by_state": dict(sorted(by.items(), key=lambda kv: -kv[1]))},
        "records": recs}, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(items)} items, {len(recs)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
