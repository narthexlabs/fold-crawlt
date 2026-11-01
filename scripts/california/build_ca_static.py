"""Static files the California map (/ca, /ca/<slug>) fetches, for all 12 dioceses, and the search file. Rerunnable:
every input is a file (or Oakland's database, read-only with the anon key) and every output is rewritten.

    python3 -I scripts/california/build_ca_static.py                       # all 12 dioceses + search.json
    python3 -I scripts/california/build_ca_static.py --only los-angeles,san-diego --skip-search
    python3 -I scripts/california/build_ca_static.py --no-geocode          # offline: Census answers from the cache only

Per diocese (scripts/california/merge.py does the merging; see its docstring for matching and schedule precedence):
    <slug>/map.json        pins: id, name, city, lat, lng, region, deanery, kind, has_times, has_groups, has_events; the
                           pastoral layer, deaneries/vicariates, sources and the "what we have" line. No times. First paint.
    <slug>/p/<id>.json     one parish: address, office phone, website, social links, schedules by kind with source + date,
                           groups, next 30 days of events, bulletin link, sources. On demand.
    <slug>/groups.json     the diocese's group index (Groups tab), on demand
    <slug>/events.json     the next 60 days, weekly series folded (This week tab), on demand
    <slug>/places.json     schools, charities, cemeteries, shrines, retreat centers, health, Newman ("Other places"), on demand
    search.json            "City, ZIP or parish" for the state landing: Census places and ZIP codes (ZCTA) with their
                           diocese and an internal point, and the parish names of every diocese.
    data/real/california/coverage.json, data/real/california/merged/<slug>.json (all rows, hidden ones marked)

Not written here: public/ca/<slug>/index.json + schedules.json (ingest/california/discovermass_publish.py owns them),
outline.geojson (scripts/california/build_outlines.py), anything under data/real/california/dioceses/ or discovermass/.

Inputs
    data/real/california/dioceses.json                         diocese -> counties (scripts/california/build_dioceses.py)
    Oakland: Supabase (entity, deanery, mass_schedule, place_notice, ministry, event), read-only with the anon key from
             .env.local; data/real/calendars/events.json (parish calendars); data/real/bulletins/index.json
    Others: see scripts/california/merge.py
    Census: Gazetteer places + ZCTAs 2023 (data/raw/california/census/), counties cb_2024 500k (data/raw/civic/tiger/)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
try:  # only search.json needs these (county shapes); a --skip-search build runs without them
    import shapefile
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union  # noqa: F401
    from shapely.prepared import prep  # noqa: F401
    from shapely.strtree import STRtree
except ImportError:  # pragma: no cover
    shapefile = None

ROOT = Path(__file__).resolve().parents[2]
PUB = ROOT / "public" / "ca"
REAL = ROOT / "data" / "real" / "california"
DIOCESES = json.loads((REAL / "dioceses.json").read_text())
COUNTY_SHP = ROOT / "data" / "raw" / "civic" / "tiger" / "cb_2024_us_county_500k" / "cb_2024_us_county_500k.shp"
CENSUS = ROOT / "data" / "raw" / "california" / "census"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge  # noqa: E402  (scripts/california/merge.py)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(s).replace("'", "")).strip("-")


def r5(x):
    return None if x is None else round(float(x), 5)


def write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(s)
    return len(s.encode())


# ------------------------------------------------------------------------------------------------ Oakland (our DB)
def env() -> tuple[str, str]:
    vals = {}
    for line in (ROOT / ".env.local").read_text().splitlines():
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*)\s*$", line)
        if m:
            vals[m.group(1)] = m.group(2).strip().strip('"')
    url = vals.get("NEXT_PUBLIC_SUPABASE_URL")
    key = vals.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or vals.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    return url, key


def rest(table: str, params: dict) -> list[dict]:
    url, key = env()
    out, start = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{table}", params=params, timeout=60,
                         headers={"apikey": key, "Authorization": f"Bearer {key}", "Range": f"{start}-{start + 999}"})
        r.raise_for_status()
        rows = r.json()
        out += rows
        if len(rows) < 1000:
            return out
        start += 1000


def status_unverified_ids() -> set[str]:
    src = (ROOT / "lib" / "status.ts").read_text()
    block = src[src.index("STATUS_UNVERIFIED"):]
    block = block[: block.index("\n};")]
    return set(re.findall(r"^  '([a-z0-9-]+)': \{", block, re.M))


def build_oakland(geocode: bool = True) -> dict:
    """Oakland from our database (read-only, anon key), in the same shape merge.build_diocese() returns, so the same
    display policy, coverage and files apply. Groups: ministry; events: the parishes' calendar file + the event table."""
    fetched = now_iso()
    ents = rest("entity", {"select": "id,type,name,parent_id,city,address,lat,lng,website,phone,status",
                           "type": "in.(parish,mission)", "order": "id"})
    dean = {d["id"]: d for d in rest("deanery", {"select": "id,number,region,region_name,name", "order": "number"})}
    notices = {n["entity_id"] for n in rest("place_notice", {"select": "entity_id"})}
    sched = defaultdict(list)
    for s in rest("mass_schedule", {"select": "entity_id,day,time,language,kind,raw,source,source_url", "order": "entity_id,id"}):
        sched[s.pop("entity_id")].append(s)
    unverified = status_unverified_ids()
    listed = [e for e in ents if e["status"] != "closed_or_merged" or e["id"] in notices or e["id"] in unverified]
    areas = json.loads((ROOT / "public" / "deanery-areas.geojson").read_text())
    regions = [f for f in areas["features"] if f["properties"].get("kind") == "region"]
    region_of_dean = {d: f"rg-{v['region']}" for d, v in dean.items()}
    cath = next(d for d in DIOCESES["dioceses"] if d["slug"] == "oakland")["cathedral"]["name"]
    groups = defaultdict(list)
    for g in rest("ministry", {"select": "entity_id,name,scope,category,subcategory,audience,language,language_assumed,meets,source_url,last_seen,confidence",
                               "category": "not.is.null", "order": "entity_id,name"}):
        groups[g["entity_id"]].append(g)
    bulletins = {b["entity_id"]: b for b in (merge.load_json(ROOT / "data/real/bulletins/index.json", {}) or {}).get("records", [])}
    cal = merge.load_json(ROOT / "data/real/calendars/events.json", {}) or {}
    feeds = {f["feed_id"]: f for f in (merge.load_json(ROOT / "data/real/calendars/feeds.json", {}) or {}).get("feeds", [])}
    pevents = defaultdict(list)
    for e in cal.get("events", []):
        if e.get("owner_id") == "dio-oakland" or not merge.keep_event(e.get("title") or "", e.get("category"), e.get("tags")):
            continue
        dd, hh = merge.wall(e["start"], bool(e.get("all_day")))
        try:
            dv = merge.date.fromisoformat(dd)
        except ValueError:
            continue
        if dv < merge.TODAY or dv > merge.TODAY + merge.timedelta(days=90):
            continue
        pid = e.get("parish_id") or e.get("owner_id")
        f = feeds.get(e.get("feed_id")) or {}
        pevents[pid].append({"pid": pid, "date": dd, "time": hh, "title": e["title"].strip()[:120], "type": merge.event_type(e.get("category"), e["title"]),
                             "lang": merge.language_of(e["title"]), "url": e.get("source_url") or f.get("page_url"), "fetched_at": cal.get("generated_at")})
    sites = {r["id"]: r for r in (merge.load_json(ROOT / "data/real/parish_sites.json", {}) or {}).get("records", [])}
    recs = []
    for e in sorted(listed, key=lambda x: x["name"]):
        kind = "cathedral" if (e["name"] == cath or "cathedral" in e["name"].lower()) else ("mission" if e["type"] == "mission" else "parish")
        rows = [dict(x, needs_review=False) for x in sched.get(e["id"], [])]
        rows = merge.choose_rows(rows)
        kinds = merge.kinds_of(rows)
        one, ser = merge.fold_events(pevents.get(e["id"], []))
        b = bulletins.get(e["id"])
        st = sites.get(e["id"], {})
        recs.append({
            "id": e["id"], "dir": None, "dm": None, "name": e["name"], "kind": kind, "type": e["type"], "address": e["address"], "city": e["city"],
            "zip": None, "county": None, "lat": e["lat"], "lng": e["lng"], "coords_source": "Find the Church database (Diocese of Oakland pilot)",
            "phone": e["phone"], "website": e["website"], "status": e["status"], "status_note": None, "rite": None, "languages": [],
            "listed_by": "database", "match": {}, "sites": [], "region": region_of_dean.get(e["parent_id"]), "deanery": e["parent_id"],
            "rows": rows, "kinds": kinds, "has_times": "mass" in kinds, "closing": e["id"] in notices,
            "groups": [g for g in groups.get(e["id"], []) if g.get("scope") != "diocesan"],
            "events": [x for x in one if merge.date.fromisoformat(x["date"]) <= merge.TODAY + merge.timedelta(days=30)], "weekly": ser, "series": ser,
            "all_events": one, "has_events": bool(one or ser), "social": {},
            "bulletin": {"url": b["url"], "date": b.get("date"), "kind": b.get("host"), "found_on": b.get("found_via")} if b else None,
            "site_status": st.get("crawl_status") or st.get("status"),
        })
    dio_wide = [g for gs in groups.values() for g in gs if g.get("scope") == "diocesan" or g["entity_id"] == "dio-oakland"]
    all_groups = [(r["id"], g) for r in recs for g in r["groups"]] + [(None, g) for g in dio_wide]
    to = (merge.TODAY + merge.timedelta(days=60)).isoformat()
    devents = rest("event", {"select": "id,entity_id,title,start,end,all_day,venue,venue_city,categories,url,is_bishop_calendar",
                             "start": f"gte.{merge.TODAY.isoformat()}", "order": "start"})
    all_events = [x for r in recs for x in r["all_events"] if x["date"] <= to]
    for e in devents:
        if not e.get("start") or e["start"][:10] > to:
            continue
        dd, hh = merge.wall(e["start"], bool(e.get("all_day")))
        venue = ", ".join(x for x in [e.get("venue"), e.get("venue_city")] if x and not (e.get("venue") or "").endswith(x or "-")) or None
        all_events.append({"pid": e["entity_id"] if e.get("entity_id") in {r["id"] for r in recs} else None, "date": dd, "time": hh,
                           "title": e["title"].strip()[:120], "type": merge.diocese_event_type(e.get("categories") or [], e["title"]),
                           "lang": merge.language_of(e["title"]), "url": e.get("url"), "venue": venue, "dio": True, "bishop": bool(e.get("is_bishop_calendar")),
                           "end": merge.day_of(e.get("end")) if e.get("all_day") and merge.day_of(e.get("end")) != dd else None})
    places = []
    for x in rest("entity", {"select": "id,type,name,city,address,lat,lng,website,phone,status", "type": "in.(school,charity)", "order": "id"}):
        if x.get("status") == "closed_or_merged":
            continue
        places.append({"id": x["id"], "name": x["name"], "type": "school" if x["type"] == "school" else "charity", "subtype": None,
                       "city": x["city"], "address": x["address"], "lat": r5(x["lat"]), "lng": r5(x["lng"]), "phone": x["phone"], "website": x["website"],
                       "source": "Find the Church database (Diocese of Oakland pilot)", "source_url": "https://www.oakdiocese.org/", "fetched_at": fetched[:10]})
    st_of = lambda v: v.get("crawl_status") or v.get("status")  # noqa: E731
    ss = Counter(("read" if st_of(v) == "ok" else "refused" if st_of(v) in ("blocked_403", "js_challenge") else "unreachable")
                 for v in sites.values() if st_of(v) != "no_website")
    ss["listed"] = sum(ss.values())
    layer = {"kind": "region", "label": "Regions", "approximate": True,
             "note": "Regions are a list filter only: the diocese publishes no region boundaries, so none are drawn. A parish belongs to the region of its deanery.",
             "items": [{"id": f["properties"]["id"], "name": f["properties"]["name"], "leader": None, "area": None,
                        "approximate": True} for f in regions]}
    dio = next(d for d in DIOCESES["dioceses"] if d["slug"] == "oakland")
    return {"slug": "oakland", "dio": dio, "has_dir": True, "recs": recs, "structure": {"fetched_at": fetched}, "unit_label": "Deaneries",
            "deaneries": [{"id": d["id"], "name": d["name"], "region": f"rg-{d['region']}"} for d in dean.values()],
            "events": all_events, "groups": all_groups, "places": places, "dm_meta": {}, "dm_count": 0, "dm_unmatched": [],
            "site_stats": ss, "sites_n": ss["listed"], "social_n": 0, "bulletin_n": sum(bool(r["bulletin"]) for r in recs),
            "directory_n": len(recs), "layer": layer,
            "sources": [{"what": "Parishes, regions, deaneries, Mass times, groups", "label": "Find the Church database (Diocese of Oakland pilot)",
                         "url": "https://www.oakdiocese.org/parishes/", "fetched_at": fetched[:10]},
                        {"what": "Parish events", "label": "the parishes' own public calendars", "url": None, "fetched_at": (cal.get("generated_at") or "")[:10]},
                        {"what": "Deaneries and regions", "label": "Diocese of Oakland, MAP commission interim report (Sept 2022)",
                         "url": "https://www.oakdiocese.org/wp-content/uploads/2026/04/MAP-commission-interim-report-092622-1.pdf", "fetched_at": None}],
            "times_note": "Mass times as on the Find page: checked by hand or read by a person from the parish's own pages first; then "
                          "DiscoverMass.com; then the parish website read automatically, only for days nothing better lists.",
            "coverage_extra": {"sites_source": "data/real/parish_sites.json", "parishes_source": "Find the Church database (from oakdiocese.org)"}}


def oakland_detail(r: dict) -> dict:
    d = merge.detail(r, {"dio": {"name": "Diocese of Oakland"}})
    d["closing"] = r["closing"]
    d["parish_page"] = f"/parishes/{r['id']}"
    d["sources"] = [{"what": "Parish, address, Mass times and groups", "label": "Find the Church database (Diocese of Oakland pilot)",
                     "url": "https://www.oakdiocese.org/parishes/", "fetched_at": None}]
    d["match"] = {}
    return d


# ------------------------------------------------------------------------------------------------ Los Angeles layer
def la_layer() -> dict:
    lr = json.loads((REAL / "dioceses" / "los-angeles" / "map" / "regions.json").read_text())
    return {"kind": "region", "label": "Pastoral regions", "approximate": True,
            "note": "The archdiocese publishes each region's parish list, not its boundary. Regions are a list filter only; "
                    "no region areas are drawn on the map.",
            "items": [{"id": s, "name": v["name"], "leader": v["auxiliary_bishop"], "leader_title": "Auxiliary bishop",
                       "area": v["area"], "approximate": s != "santa-barbara", "source_url": v["page"]}
                      for s, v in lr["regions"].items()]}


# ------------------------------------------------------------------------------------------------ search
def county_index():
    sf = shapefile.Reader(str(COUNTY_SHP))
    geoms, slugs = [], []
    ci = DIOCESES["county_index"]
    for s, rec in zip(sf.shapes(), sf.records()):
        if rec["STATEFP"] == "06":
            geoms.append(shape(s.__geo_interface__))
            slugs.append(ci[rec["GEOID"]])
    return STRtree(geoms), geoms, slugs


def build_search(indexes: dict[str, dict]) -> int:
    tree, geoms, slugs = county_index()
    order = [d["slug"] for d in DIOCESES["dioceses"]]

    def diocese_at(lat, lng):
        pt = Point(lng, lat)
        for i in tree.query(pt):
            if geoms[i].contains(pt):
                return slugs[i]
        i = tree.nearest(pt)
        return slugs[i] if geoms[i].distance(pt) < 0.02 else None

    places = []
    for line in (CENSUS / "2023_Gaz_place_national.txt").read_text(encoding="latin-1").splitlines()[1:]:
        f = [x.strip() for x in line.split("\t")]
        if f[0] != "CA":
            continue
        name = re.sub(r"\s+(city|town|CDP)$", "", f[3])
        lat, lng = float(f[10]), float(f[11])
        d = diocese_at(lat, lng)
        if d:
            places.append([name, order.index(d), round(lat, 3), round(lng, 3)])
    zips = []
    for line in (CENSUS / "2023_Gaz_zcta_national.txt").read_text().splitlines()[1:]:
        f = [x.strip() for x in line.split("\t")]
        if not f[0].startswith("9"):
            continue
        lat, lng = float(f[5]), float(f[6])
        if not (32 < lat < 42.1 and -124.6 < lng < -114):
            continue
        d = diocese_at(lat, lng)
        if d:
            zips.append([f[0], order.index(d), round(lat, 3), round(lng, 3)])
    parishes = []
    for s, ix in indexes.items():
        for r in ix["rows"]:
            parishes.append([r[1], r[2], order.index(s), r[0]])
    obj = {"v": 1, "built_at": now_iso(), "dioceses": order,
           "sources": [{"what": "Cities, towns and ZIP codes", "label": "US Census Bureau Gazetteer 2023 (places, ZCTAs)",
                        "url": "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html"},
                       {"what": "Diocese of each", "label": "data/real/california/dioceses.json (whole counties)"}],
           "cols": {"places": ["name", "diocese", "lat", "lng"], "zips": ["zip", "diocese", "lat", "lng"],
                    "parishes": ["name", "city", "diocese", "id"]},
           "places": places, "zips": zips, "parishes": parishes}
    return write(PUB / "search.json", obj)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(d["slug"] for d in DIOCESES["dioceses"]))
    ap.add_argument("--skip-search", action="store_true")
    ap.add_argument("--no-geocode", action="store_true", help="use cached Census answers only")
    a = ap.parse_args()
    cov_path = REAL / "coverage.json"
    cov = (merge.load_json(cov_path, {}) or {}).get("dioceses", {})
    built = {}
    for s in [x for x in a.only.split(",") if x]:
        dio = next(d for d in DIOCESES["dioceses"] if d["slug"] == s)
        b = build_oakland() if s == "oakland" else merge.build_diocese(s, geocode=not a.no_geocode)
        out = merge.outputs(b)
        if s == "oakland":
            out["details"] = {r["id"]: oakland_detail(r) for r in b["recs"]}
        layer = b.get("layer")
        if s == "los-angeles":
            layer = la_layer()
        pdir = PUB / s / "p"
        if pdir.exists():
            shutil.rmtree(pdir)
        sizes = [write(pdir / f"{pid}.json", det) for pid, det in out["details"].items()]
        c = merge.coverage_of(b)
        if b.get("coverage_extra"):
            c["parishes"]["source"] = b["coverage_extra"]["parishes_source"]
            c["sites"]["source"] = b["coverage_extra"]["sites_source"]
        cov[s] = c
        g = write(PUB / s / "groups.json", out["groups"])
        e = write(PUB / s / "events.json", out["events"])
        pl = write(PUB / s / "places.json", out["places"])
        srcs = b.get("sources") or merge.sources_for(b)
        notes = {"secondary": "DiscoverMass.com, a secondary listing with no last-updated date"}
        ix = {"v": 2, "diocese": s, "name": dio["name"], "built_at": now_iso(), "sources": srcs,
              "times_note": b.get("times_note") or ("Times read by a person (checked by hand, or from the parish bulletin or an archived copy of its "
                                                    "website) come first; then the parish's own website where a checked template read it; then "
                                                    + notes["secondary"] + "; then the diocese's directory and the parish's own website, read "
                                                    "automatically, only for days nothing better lists. Check with the parish."),
              "layer": layer, "deaneries": b["deaneries"], "unit_label": b["unit_label"],
              "cols": ["id", "name", "city", "lat", "lng", "region", "deanery", "kind", "times", "groups", "events"], "rows": out["rows"],
              "counts": {"places": len(out["rows"]), "on_map": sum(r[3] is not None for r in out["rows"]),
                         "with_times": sum(r[8] for r in out["rows"]), "with_groups": sum(r[9] for r in out["rows"]),
                         "with_events": sum(r[10] for r in out["rows"]), "other_places": len(out["places"]["rows"]),
                         "groups": len(out["groups"]["rows"]), "events_60d": len(out["events"]["rows"]) + len(out["events"]["series"])},
              "have": merge.what_we_have(c), "listed_by": "directory" if b["has_dir"] else ("discovermass" if out["rows"] else None),
              "moved": (merge.load_json(REAL / "id_redirects.json", {}) or {}).get("dioceses", {}).get(s, {})}
        n = write(PUB / s / "map.json", ix)
        merge.write(merge.MERGED / f"{s}.json", merge.merged_dump(b) if s != "oakland" else {"diocese": s, "built_at": now_iso(),
              "note": "Oakland's rows live in the database (mass_schedule); this build only reads them.",
              "hidden_rows": dict(Counter(x["hidden"] for r in b["recs"] for x in r["rows"] if x["hidden"]))}, indent=1)
        built[s] = ix
        print(f"{s}: {ix['counts']} map.json {n} B; p/ {len(sizes)} files avg {sum(sizes) // max(len(sizes), 1)} B; "
              f"groups {g} B, events {e} B, places {pl} B")
    merge.write(cov_path, {"built_at": now_iso(), "built_by": "scripts/california/build_ca_static.py (scripts/california/merge.py)",
                     "note": "Counts of what is collected, per diocese, with where each came from. Not a ranking.",
                     "dioceses": {k: cov[k] for k in [d["slug"] for d in DIOCESES["dioceses"]] if k in cov}}, indent=1)
    for p in PUB.glob("*/map.json"):
        if p.parent.name not in built:
            built[p.parent.name] = json.loads(p.read_text())
    if merge.HOLY_STATS:
        print("holy-day check on parish-site rows:", dict(merge.HOLY_STATS))
    if merge.TRUST_STATS:
        print("template parses (trusted / Sunday disagrees with DiscoverMass):", dict(merge.TRUST_STATS))
    if merge.SOCIAL_STATS:
        print("social links left out (not the parish's own):", dict(merge.SOCIAL_STATS))
    if not a.skip_search:
        print("search.json", build_search(built), "bytes")


if __name__ == "__main__":
    main()
