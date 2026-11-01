"""Assign every parsed DiscoverMass church to a California diocese and write the per-diocese and app files.

    python3 -I ingest/california/discovermass_publish.py
    python3 -I ingest/california/discovermass_publish.py --offline     # no network: Census answers from the cache only

Reads the page cache through discovermass_crawl.py (no network) plus the shared diocese reference written by
scripts/california/build_dioceses.py:
    data/real/california/dioceses.json      12 dioceses as whole-county unions; county_index GEOID -> slug
    public/ca-dioceses.geojson              the same dioceses as shapes (Census 1:500k counties, simplified)

Diocese assignment (diocese_assigned_by on every record), first that applies:
  1. point-in-polygon of the page's lat/lng on public/ca-dioceses.geojson. Cross-checked against the unsimplified
     Census county polygon (cb_2024 1:500k) through county_index; where the two disagree (a point within the
     simplification tolerance of a diocese border) the county polygon wins and the record says so.
  2. county from the lat/lng (the 1:500k county polygon, for a point just off the simplified coastline).
  3. county from the city named in the page's address (or the URL slug's city), through the Census place file.
  4. the "Diocese of X" line on the page.
The page's own diocese line is kept (diocese_on_page) and compared; disagreements are listed in the summary.

Writes
    data/real/california/discovermass/churches.json             every record, with diocese fields
    data/real/california/discovermass/flagged_rows.json         schedule rows flagged as likely typos
    data/real/california/discovermass/by-diocese/<slug>.json    full records per diocese
    public/ca/<slug>/index.json                                  pins (id, name, type, city, lat, lng, website, has_times)
    public/ca/<slug>/schedules.json                              { id: rows[] } in the mass_schedule shape

Stable id: <diocese-slug>--<name-slug>-<city-slug>; when two listings would share one, both get -<DiscoverMass post id>.
Oakland: oakland_entity_id where the name and city match our entity table (export in
data/raw/california/oakland_entities_db_2026-10-31.json) and/or the bulletin pipeline's match_log.json.
Nothing is written to Supabase.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import ca_geo  # noqa: E402
import discovermass_crawl as dc  # noqa: E402

DIOCESES = ROOT / "data" / "real" / "california" / "dioceses.json"
SHAPES = ROOT / "public" / "ca-dioceses.geojson"
OUT = dc.OUT_DIR
PUBLIC = ROOT / "public" / "ca"
OAK_DB = ROOT / "data" / "raw" / "california" / "oakland_entities_db_2026-10-31.json"
OAK_MATCH_LOG = ROOT / "data" / "real" / "bulletins" / "match_log.json"
SLUGS = ["los-angeles", "san-francisco", "fresno", "monterey", "oakland", "orange", "sacramento", "san-bernardino",
         "san-diego", "san-jose", "santa-rosa", "stockton"]


def load_dioceses():
    d = json.loads(DIOCESES.read_text())
    g = json.loads(SHAPES.read_text())
    shapes = [(f["properties"]["slug"], prep(shape(f["geometry"])), shape(f["geometry"])) for f in g["features"]]
    meta = {x["slug"]: x for x in d["dioceses"]}
    assert set(meta) == set(SLUGS), set(meta) ^ set(SLUGS)
    return meta, d["county_index"], shapes


GEO_CACHE = ROOT / "data" / "raw" / "california" / "census_geocode_cache.json"
_geo_last = [0.0]


def census_geocode(address: str) -> dict | None:
    """US Census Geocoder (public domain; geocoding.geo.census.gov), cached, 1 request/s. Only for listings whose
    DiscoverMass coordinates disagree with their own address city."""
    import time
    import urllib.parse
    import requests
    cache = json.loads(GEO_CACHE.read_text()) if GEO_CACHE.exists() else {}
    if address in cache:
        return cache[address]
    if "--offline" in sys.argv:
        return None  # not cached: asked on the next online run
    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address="
           + urllib.parse.quote_plus(address) + "&benchmark=Public_AR_Current&format=json")
    wait = _geo_last[0] + 1.0 - time.time()
    if wait > 0:
        time.sleep(wait)
    out = None
    try:
        r = requests.get(url, timeout=30)
        if r.ok:
            m = r.json()["result"]["addressMatches"]
            if m:
                out = {"lat": m[0]["coordinates"]["y"], "lng": m[0]["coordinates"]["x"],
                       "matched_address": m[0]["matchedAddress"], "source_url": url, "fetched_at": dc.now_iso()}
    except (requests.RequestException, ValueError, KeyError):
        return None  # not cached: try again next run
    finally:
        _geo_last[0] = time.time()
    cache[address] = out
    GEO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    GEO_CACHE.write_text(json.dumps(cache, indent=0))
    return out


def page_line_slug(rec: dict, meta) -> str | None:
    dn = re.sub(r"\s+in california$", "", (rec.get("diocese_on_page_norm") or "").lower()).strip()
    hit = [s for s, m in meta.items() if m["short_name"].lower() == dn]
    return hit[0] if len(hit) == 1 else None


def fix_coordinates(rec: dict, meta, county_index) -> None:
    """DiscoverMass coordinates that land outside the listing's own address city/county are checked: kept when the
    page's own diocese line agrees with the diocese they fall in (two places share a name: Squaw Valley is a Fresno
    County CDP and the old name of Olympic Valley, Placer County); otherwise checked against the Census Geocoder,
    whose point replaces them only when it agrees with the address."""
    city = rec.get("city") or (rec.get("slug_city") or "").replace("-", " ")
    why = ca_geo.check_latlng(rec.get("lat"), rec.get("lng"), city)
    if not why:
        return
    rec["latlng_page"] = [rec["lat"], rec["lng"]]
    rec["latlng_suspect"] = why
    if rec.get("street") and rec.get("city"):
        g = census_geocode(f"{rec['street']}, {rec['city']}, CA {rec.get('zip') or ''}".strip())
        if g and ca_geo.check_latlng(g["lat"], g["lng"], city) is None:
            rec["lat"], rec["lng"] = round(g["lat"], 6), round(g["lng"], 6)
            rec["latlng_source"] = "US Census Geocoder (page address); the page's own coordinates were rejected"
            rec["latlng_geocoder_match"] = g["matched_address"]
            rec["latlng_suspect"] = None
            rec["page_flags"].append(f"page coordinates replaced: {why}")
            rec["county"] = ca_geo.county_at(rec["lat"], rec["lng"])
            rec["county_from"] = "lat/lng in Census county polygon"
            return
    _, g, _ = ca_geo.county_at_full(rec.get("lat"), rec.get("lng"))
    line = page_line_slug(rec, meta)
    if g and line and county_index.get(g) == line:
        rec["latlng_suspect"] = None
        rec.pop("latlng_page", None)
        rec["latlng_check"] = f"kept: {why}; no geocoder match that agrees with the address, and the page's own " \
                              f"diocese line ({rec.get('diocese_on_page')}) agrees with the diocese the point falls in"
        return
    rec["page_flags"].append(f"page coordinates suspect (no geocoder match): {why}")


def content_check(rec: dict) -> str | None:
    """A page whose content belongs to another church (DiscoverMass served the Ohio 'National Shrine of Our Lady of
    Lebanon' at the Irvine St. Elizabeth Ann Seton URL): an address outside California, or neither the name nor the
    city agreeing with the URL slug."""
    if rec.get("state") and rec["state"] != "CA":
        return f"the page's address is in {rec['state']}, not California ({rec.get('address_raw')})"
    sc = rec.get("slug_city") or ""
    stem = rec["dm_slug"][:-3]
    if sc and stem.endswith("-" + sc):
        stem = stem[: -len(sc) - 1]
    a, b = toks(stem.replace("-", " ")), toks(rec.get("name") or "")
    city_ok = not sc or not rec.get("city") or ca_geo.slugify(rec["city"]) == sc or \
        ca_geo.city_county(rec["city"])[0] == ca_geo.city_county(sc)[0]
    if a and b and not (a & b) and not city_ok:
        return f"the page's name ({rec.get('name')}) and city ({rec.get('city')}) do not match its URL"
    return None


def assign(rec: dict, meta, county_index, shapes) -> None:
    if not rec.get("unavailable"):
        why = content_check(rec)
        if why:
            rec["excluded"] = f"page content does not match its URL: {why}"
            rec["page_flags"].append(rec["excluded"])
            rec["has_times"] = False
            rec["diocese"] = None
            rec["diocese_assigned_by"] = "not assigned: " + rec["excluded"]
            return
    if rec.get("unavailable"):
        rec["diocese"] = None
        rec["diocese_assigned_by"] = None
        return
    fix_coordinates(rec, meta, county_index)
    lat, lng = rec.get("lat"), rec.get("lng")
    if rec.get("latlng_suspect"):
        lat = lng = None  # suspect point: let the address city decide the diocese
    pip = None
    if lat is not None:
        p = Point(lng, lat)
        pip = next((s for s, pr, _ in shapes if pr.contains(p)), None)
    cname, cgeoid, chow = ca_geo.county_at_full(lat, lng)
    by_county = county_index.get(cgeoid) if cgeoid else None
    slug, how, note = None, None, None
    if pip:
        slug, how = pip, "point-in-polygon (public/ca-dioceses.geojson)"
        if by_county and by_county != pip:
            slug = by_county
            how = "county polygon from lat/lng (Census cb_2024 1:500k)"
            note = (f"the simplified diocese shape puts this point in {pip}; the unsimplified county ({cname}) "
                    f"belongs to {by_county}: county kept")
    elif by_county:
        slug, how = by_county, f"county polygon from lat/lng (Census cb_2024 1:500k, {chow})"
    if not slug:
        city = rec.get("city") or rec.get("slug_city")
        c, chow2 = ca_geo.city_county(city)
        g = ca_geo.county_geoid(c)
        if g and county_index.get(g):
            slug, how, cname, cgeoid = county_index[g], f"county from city name ({chow2}: {city} -> {c})", c, g
    if not slug and rec.get("diocese_on_page_norm"):
        dn = rec["diocese_on_page_norm"].lower()
        hit = [s for s, m in meta.items() if m["short_name"].lower() == dn]
        if len(hit) == 1:
            slug, how = hit[0], "diocese line on the DiscoverMass page"
    rec["diocese"] = slug
    rec["diocese_assigned_by"] = how
    if note:
        rec["diocese_note"] = note
    if cname and not rec.get("county"):
        rec["county"] = cname
    rec["county_geoid"] = cgeoid or ca_geo.county_geoid(rec.get("county"))
    dn = re.sub(r"\s+in california$", "", (rec.get("diocese_on_page_norm") or "").lower()).strip()
    if dn in ("unknown", "none", "n/a"):
        dn = ""
    latin = {m["short_name"].lower() for m in meta.values()}
    eastern = (rec.get("rite") not in (None, "Roman-Latin")) or \
        re.search(r"eparchy|exarchate|ordinariate", rec.get("diocese_on_page") or "", re.I)
    rec["diocese_on_page_agrees"] = None
    if dn and slug and (dn in latin or not eastern):
        rec["diocese_on_page_agrees"] = dn == meta[slug]["short_name"].lower()
    elif dn:
        # an eparchy or another jurisdiction (Eastern Catholic churches name theirs: "Newton" = the Melkite Eparchy
        # of Newton; "Maronite Eparchy of Our Lady of Lebanon"); the church is placed in the Latin diocese whose
        # territory it stands in, for the map only
        rec["jurisdiction_on_page"] = rec.get("diocese_on_page")
        rec["diocese_note"] = ((rec.get("diocese_note") + "; ") if rec.get("diocese_note") else "") + \
            "the page names another jurisdiction (e.g. an Eastern Catholic eparchy); placed in the territorial " \
            "Latin diocese for the map only"


# ------------------------------------------------------------------------------------------------ ids + Oakland
def mark_duplicates(recs: list[dict]) -> int:
    """DiscoverMass sometimes lists one church twice (two posts). Same folded name and the same street (or a point
    within ~100 m) -> possible_duplicate_of. Both stay; the one with more schedule rows (then the lower post id) is
    primary, and the other's pin carries "dup"."""
    groups = defaultdict(list)
    for r in recs:
        if r.get("unavailable"):
            continue
        k1 = ca_geo.slugify(r.get("name") or "")
        k2 = ca_geo.slugify(r.get("street") or "") or (f"{round(r['lat'], 3)},{round(r['lng'], 3)}"
                                                       if r.get("lat") is not None else r["dm_slug"])
        groups[(k1, ca_geo.slugify(r.get("city") or ""), k2)].append(r)
    n = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda r: (-len(r["schedule"]), int(r.get("post_id") or 0)))
        for r in g[1:]:
            r["possible_duplicate_of"] = g[0]["dm_slug"]
            n += 1
    return n


def make_ids(recs: list[dict]) -> None:
    base = {}
    for r in recs:
        city = r.get("city") or (r.get("slug_city") or "").replace("-", " ")
        base[r["dm_slug"]] = f"{r['diocese'] or 'unassigned'}--{ca_geo.slugify(r.get('name') or r['dm_slug'])}-" \
                             f"{ca_geo.slugify(city)}".rstrip("-")
    counts = Counter(base.values())
    for r in recs:
        b = base[r["dm_slug"]]
        r["id"] = f"{b}-{r.get('post_id') or ca_geo.slugify(r['dm_slug'])}" if counts[b] > 1 else b


STOP = {"st", "saint", "sts", "saints", "parish", "church", "catholic", "of", "the", "and", "community", "roman",
        "chapel", "mission", "shrine", "basilica", "cathedral", "co", "ca", "de", "la", "el", "los", "our"}
CITY_ALIASES = {"point richmond": "richmond", "bay point": "pittsburg", "el sobrante": "richmond",
                "mission san jose": "fremont"}


def toks(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9 ]+", " ", ca_geo.slugify(s).replace("-", " "))
    s = re.sub(r"\bsaint\b", "st", s)
    return {t for t in s.split() if t not in STOP and len(t) > 1}


def oakland_links(recs: list[dict]) -> dict:
    """oakland_entity_id from the bulletin pipeline's match_log (entity -> DiscoverMass URL decisions) and an
    independent name + city check against the entity table. Both are kept; disagreements are reported."""
    db = json.loads(OAK_DB.read_text())["rows"] if OAK_DB.exists() else []
    ml = json.loads(OAK_MATCH_LOG.read_text())["parishes"] if OAK_MATCH_LOG.exists() else {}
    url_to_ent = defaultdict(list)
    for eid, v in ml.items():
        for u in (v.get("discovermass") or {}).get("urls") or []:
            url_to_ent[u.rstrip("/") + "/"].append((eid, v["discovermass"]["decision"]))
    seed = ROOT / "data" / "seed" / "entities.json"
    se = json.loads(seed.read_text()) if seed.exists() else []
    se = se if isinstance(se, list) else se.get("entities", [])
    addr = {e["id"]: e.get("address") for e in se if e.get("type") in ("parish", "mission") and e.get("address")}

    def street_key(a):
        m = re.match(r"\s*(\d+)\s*-?\s*([A-Za-z0-9]+)", a or "")
        return f"{m.group(1)} {m.group(2).lower()}" if m else None

    report = Counter()
    for r in recs:
        if r.get("diocese") != "oakland":
            continue
        city = (r.get("city") or "").lower()
        cand = []
        for eid, name, ecity, status in db:
            ec = (ecity or "").lower()
            if ec != city and CITY_ALIASES.get(ec) != city and CITY_ALIASES.get(city) != ec:
                continue
            a, b = toks(name), toks(r.get("name") or "")
            if a and b and (a == b or (a & b and (a <= b or b <= a))):
                cand.append(eid)
        via_log = url_to_ent.get(r["source_url"].rstrip("/") + "/", [])
        log_ids = [e for e, _ in via_log]
        if len(cand) == 1 and (not log_ids or cand[0] in log_ids):
            r["oakland_entity_id"] = cand[0]
            r["oakland_entity_match"] = "name + city" + (" (agrees with match_log)" if log_ids else "")
            report["name+city"] += 1
        elif len(log_ids) == 1:
            r["oakland_entity_id"] = log_ids[0]
            r["oakland_entity_match"] = f"match_log only ({via_log[0][1]}); name/city candidates: {cand or 'none'}"
            report["match_log only"] += 1
        elif cand or log_ids:
            r["oakland_entity_id"] = None
            r["oakland_entity_candidates"] = sorted(set(cand) | set(log_ids))
            r["oakland_entity_match"] = "ambiguous: left for a person"
            report["ambiguous"] += 1
        else:
            r["oakland_entity_id"] = None
            same_addr = [eid for eid, a in addr.items() if street_key(r.get("street")) and street_key(a) == street_key(r.get("street"))]
            if same_addr:
                # a renamed or merged parish (DiscoverMass still lists "St. Lawrence O'Toole/St. Cyril" at the
                # address of today's Divine Mercy Parish): a candidate for a person, never linked automatically
                r["oakland_entity_candidates"] = same_addr
                r["oakland_entity_match"] = f"same street address as {', '.join(same_addr)}, different name " \
                                            "(renamed or merged?): left for a person"
                report["address only (left for a person)"] += 1
            else:
                r["oakland_entity_match"] = "no entity with this name and city"
                report["none"] += 1
    linked = {r.get("oakland_entity_id") for r in recs if r.get("oakland_entity_id")}
    report["entities_linked"] = len(linked)
    report["entities_in_db"] = len(db)
    report["entities_without_page"] = len({e[0] for e in db} - linked)
    return dict(report)


# ------------------------------------------------------------------------------------------------ outputs
def pin(r: dict) -> dict:
    p = {"id": r["id"], "name": r["name"], "type": r.get("type"), "city": r.get("city"), "lat": r.get("lat"),
         "lng": r.get("lng"), "website": r.get("website"), "has_times": r.get("has_times", False), "dm": r["dm_slug"]}
    if r.get("possible_duplicate_of_id"):
        p["dup"] = r["possible_duplicate_of_id"]
    return p


def sched_rows(r: dict) -> list[dict]:
    out = []
    for s in r.get("schedule") or []:
        row = {"kind": s["kind"], "day": s["day"], "time": s["time"]}
        if s.get("end_time"):
            row["end_time"] = s["end_time"]
        if s.get("language"):
            row["language"] = s["language"]
        row["raw"] = s["raw"]
        if s.get("all_day"):
            row["all_day"] = True
        if s.get("overnight"):
            row["overnight"] = True
        if s["flags"]:
            row["flag"] = "; ".join(s["flags"])
        out.append(row)
    return out


def per_diocese_summary(rs: list[dict]) -> dict:
    ok = [r for r in rs if not r.get("unavailable") and not r.get("excluded")]
    n = len(ok) or 1
    soc = {k: sum(bool(r["social"].get(k)) for r in ok) for k in dc.SOCIAL}
    return {
        "churches": len(ok), "unavailable_pages": len(rs) - len(ok),
        "with_times": sum(r["has_times"] for r in ok), "with_website": sum(bool(r["website"]) for r in ok),
        "with_any_social": sum(bool(r["social"]) for r in ok), "with_social": soc,
        "with_latlng": sum(r.get("lat") is not None for r in ok),
        "pct_with_times": round(100 * sum(r["has_times"] for r in ok) / n, 1),
        "pct_with_website": round(100 * sum(bool(r["website"]) for r in ok) / n, 1),
        "schedule_rows": sum(len(r["schedule"]) for r in ok),
        "flagged_rows": sum(1 for r in ok for s in r["schedule"] if s["flags"]),
        "flagged_likely_typo": sum(1 for r in ok for s in r["schedule"]
                                   if any(t in f for f in s["flags"] for t in dc.TYPO_FLAGS)),
        "other_jurisdiction_on_page": sum(bool(r.get("jurisdiction_on_page")) for r in ok),
        "possible_duplicate_listings": sum(bool(r.get("possible_duplicate_of")) for r in ok),
        "types_from_name": dict(Counter(r.get("type") or "not stated" for r in ok)),
        "diocese_on_page_disagrees": sum(r.get("diocese_on_page_agrees") is False for r in ok),
        "fetched_from": min((r["fetched_at"] for r in ok if r.get("fetched_at")), default=None),
        "fetched_to": max((r["fetched_at"] for r in ok if r.get("fetched_at")), default=None),
    }


def write_lines(path: Path, head: dict | None, key: str | None, items):
    """Compact JSON, one item per line (small files, readable diffs). items: list (under head[key]) or dict."""
    c = dict(separators=(",", ":"), ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(items, dict):
        body = ",\n".join(f"{json.dumps(k, **c)}:{json.dumps(v, **c)}" for k, v in items.items())
        path.write_text("{\n" + body + "\n}\n")
        return
    h = json.dumps(head, **c)[:-1]
    body = ",\n".join(json.dumps(x, **c) for x in items)
    path.write_text(f"{h},{json.dumps(key)}:[\n{body}\n]}}\n")


def write_json(path: Path, obj, indent=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=indent) + ("\n" if indent else ""))


def main():
    meta, county_index, shapes = load_dioceses()
    out = dc.build(write=False)
    recs = out["records"]
    for r in recs:
        assign(r, meta, county_index, shapes)
    make_ids(recs)
    dups = mark_duplicates(recs)
    by_slug = {r["dm_slug"]: r["id"] for r in recs}
    for r in recs:
        if r.get("possible_duplicate_of"):
            r["possible_duplicate_of_id"] = by_slug.get(r["possible_duplicate_of"])
    oak = oakland_links(recs)
    for r in recs:  # rows: drop empty optional fields (raw keeps the page's wording); day/time/flags always stay
        r["schedule"] = [{k: v for k, v in x.items() if k in ("kind", "day", "time", "raw", "flags") or
                          (v is not None and k != "section")} for x in r.get("schedule") or []]
    recs.sort(key=lambda r: (SLUGS.index(r["diocese"]) if r.get("diocese") in SLUGS else 99, r["id"]))
    by = defaultdict(list)
    for r in recs:
        by[r.get("diocese") or "unassigned"].append(r)
    per = {s: per_diocese_summary(by.get(s, [])) for s in SLUGS}
    if by.get("unassigned"):
        per["unassigned"] = per_diocese_summary(by["unassigned"])
    disagree = [{"id": r["id"], "name": r["name"], "city": r.get("city"), "diocese": r["diocese"],
                 "diocese_on_page": r.get("diocese_on_page"), "assigned_by": r["diocese_assigned_by"]}
                for r in recs if r.get("diocese_on_page_agrees") is False]
    out["summary"]["excluded_pages"] = [{"dm_slug": r["dm_slug"], "source_url": r["source_url"], "why": r["excluded"]}
                                        for r in recs if r.get("excluded")]
    out["summary"].update(by_diocese=per, assigned_by=dict(Counter(r.get("diocese_assigned_by") for r in recs
                                                                     if not r.get("unavailable"))),
                          diocese_on_page_disagreements=disagree, oakland_entity_links=oak)
    write_lines(OUT / "churches.json", {k: v for k, v in out.items() if k != "records"}, "records", recs)
    flagged = [{"id": r["id"], "name": r["name"], "city": r.get("city"), "diocese": r.get("diocese"), "raw": s["raw"],
                "kind": s["kind"], "day": s["day"], "time": s["time"], "flags": s["flags"], "source_url": r["source_url"]}
               for r in recs if not r.get("unavailable") for s in r["schedule"] if s["flags"]]
    write_lines(OUT / "flagged_rows.json", {"built_at": out["built_at"], "count": len(flagged),
                                           "by_flag": dict(Counter(re.sub(r"\s*\(.*?\)|\s*\d{1,2}:\d{2}", "", fl)
                                                                   .split(":")[0].strip()
                                                                   for f in flagged for fl in f["flags"]))},
                "rows", flagged)
    for s in SLUGS:
        rs = [r for r in by.get(s, [])]
        ok = [r for r in rs if not r.get("unavailable") and not r.get("excluded")]
        m = meta[s]
        write_lines(OUT / "by-diocese" / f"{s}.json", {
            "diocese": s, "name": m["name"], "source": out["source"], "source_label": out["source_label"],
            "caveat": out["caveat"], "built_at": out["built_at"], "summary": per[s]}, "records", rs)
        head = {"diocese": s, "name": m["name"], "built_at": out["built_at"],
                "source": "discovermass.com church pages (secondary source; schedules 'Powered by MassTimes.org')",
                "source_url": "https://discovermass.com/church/{dm}/",
                "fetched": {"from": per[s]["fetched_from"], "to": per[s]["fetched_to"]},
                "note": "Secondary source, age unknown. Rows with 'flag' are suspected typos; do not show them as-is.",
                "count": len(ok)}
        write_lines(PUBLIC / s / "index.json", head, "pins", [pin(r) for r in ok])
        write_lines(PUBLIC / s / "schedules.json", None, None, {r["id"]: sched_rows(r) for r in ok if r["schedule"]})
    log_txt = dc.LOG.read_text() if dc.LOG.exists() else ""
    from datetime import datetime
    metas = [json.loads(m.read_text()) for m in dc.PAGES.glob("*.json")]
    own = sorted(m["fetched_at"] for m in metas if not m.get("copied_from") and m.get("fetched_at"))
    copied = sum(1 for m in metas if m.get("copied_from"))
    span = ((datetime.fromisoformat(own[-1]) - datetime.fromisoformat(own[0])).total_seconds() / 3600) if own else 0
    runs = len(re.findall(r"RUN start", log_txt))
    blocked = len(re.findall(r" BLOCKED ", log_txt))
    errors = len(re.findall(r" ERROR ", log_txt))
    note = (f"Crawl so far: {out['summary']['pages_cached']} of {out['summary']['pages_in_sitemap']} sitemap pages "
            f"cached: {len(own)} fetched by this crawl ({own[0][:16] if own else '-'} to {own[-1][:16] if own else '-'} "
            f"UTC, {span:.1f} h over {runs} runs, one request at a time), {copied} copied from the Oakland pilot cache. "
            f"{blocked} blocked responses, {errors} errors, {out['summary']['unavailable']} pages unavailable "
            "(404/410/disallowed). Percentages are of parsed pages.")
    flagged_by = Counter(re.sub(r"\s*\(.*?\)|\s*\d{1,2}:\d{2}", "", fl).split(":")[0].strip()
                         for f in flagged for fl in f["flags"])
    write_readme(out["summary"], dict(flagged_by), out["built_at"], note)
    tot = per_diocese_summary([r for r in recs])
    print(json.dumps({"total": tot, "by_diocese": {s: {k: per[s][k] for k in ("churches", "with_times", "with_website",
                                                                          "with_any_social")} for s in per},
                      "assigned_by": out["summary"]["assigned_by"], "page_line_disagreements": len(disagree),
                      "oakland": oak}, indent=1))



# ------------------------------------------------------------------------------------------------ README
README_HEAD = """# DiscoverMass: every California church page (`data/real/california/discovermass/`)

Built by `ingest/california/discovermass_crawl.py` (fetch + parse) and `ingest/california/discovermass_publish.py`
(dioceses, ids, app files). Files only: nothing here is loaded into Supabase (the live site reads the `entity`
table, and California rows there would flood the Oakland pages).

```bash
python3 -I ingest/california/discovermass_crawl.py index                    # sitemap -> ordered list (cached)
python3 -I ingest/california/discovermass_crawl.py crawl --max-seconds 7000  # resumable; one request per >=10.5 s
python3 -I ingest/california/discovermass_publish.py                         # cache -> the files below (offline,
                                                                             # except Census Geocoder for suspect points)
```

## Source, and why it is secondary

discovermass.com is run by Diocesan (Diocesan Publications). Its church pages say "Powered by MassTimes.org": the
schedules are MassTimes.org data shown through DiscoverMass. **Treat every time here as a secondary source.**

- No page carries a "last updated" date. `sitemap_lastmod` (the WordPress post's modified time, from the sitemap)
  is kept on each record, but most California posts share bulk dates (e.g. 2016-05-17 for Los Angeles), so it says
  when the listing was imported or touched, not when a parish confirmed its times.
- Every page still contains a hidden COVID-19 notice (`covid_notice_hidden`).
- Oakland test (`docs/research/13-mass-times-accuracy.md`): about three in four weekend times agree with the
  parish's own website. Prefer the parish site, the diocese directory or the bulletin; use these where nothing else
  exists, labelled `secondary-discovermass-<date>`.

## Method

1. **Index.** The WordPress sitemaps (`wp-sitemap.xml` and 11 `wp-sitemap-posts-item-N.xml`) list every church page
   as `/church/<name>-<city>-<state>/`; 1,327 end in `-ca`. The sitemaps and the 82 Oakland pages fetched for the
   Oakland pilot earlier on 2026-10-31 (`data/raw/hosts/discovermass.com/`) were copied into this cache, not refetched.
2. **Order.** By the provisional diocese of the slug's city (Census place -> county -> diocese, `ca_geo.py`; City of
   Los Angeles neighbourhoods and other postal names from a short list), Los Angeles first, so the map prototype got
   Los Angeles pins first.
3. **Fetch.** One request at a time, then at least 10.5 s before the next (robots `Crawl-delay: 10` plus a margin,
   carried across runs in `state.json`; a lock file stops a second crawler). Raw HTML is cached in
   `data/raw/california/discovermass/pages/` (gitignored); a cached page is never fetched again. Log:
   `data/raw/california/discovermass/crawl.log`.
4. **Parse** (per page): name; street, city, ZIP; lat/lng (the page's "Directions" link, `maps.google.com/?daddr=`;
   the pages have no JSON-LD); phone; website (directory links such as catholicindex.org are kept in `website_listed`
   but not counted as a website); social links in the church's own blocks only (DiscoverMass's own footer and share
   buttons excluded); the "Diocese of X" line; rite; `type` only when the listing's name states it (cathedral,
   basilica, historic mission, shrine, campus ministry, monastery, chapel, parish, mission), else null; the Bulletins
   tab; and the schedule boxes, normalised to the `mass_schedule` shape: `day`, `time` "HH:MM" (start), `end_time`,
   `language`, `kind` = mass / confession / adoration / other, `raw`. "Liturgy of the Word" and "Communion Service"
   rows under Mass Times are `other`, not `mass`. "2nd Sundays only" and the like become
   "Second Sunday of the month" only when the comment opens with that ordinal and names no other time.
5. **Typos are flagged, never dropped.** A Mass before 05:00 or after 22:00 (no "midnight" in the row), any other
   service before 05:00 without "night/overnight/24 h/perpetual", a row with no day, an end not after its start, and
   exact duplicates carry `flags`. Not flagged: all-day adoration ("12:00am-11:55pm", `all_day`) and adoration from
   an afternoon or evening start to the next morning ("8:00pm-5:00am", `overnight`). `has_times` counts only unflagged Mass rows. In the app files a flagged row keeps a
   `flag` string so the app can hide it.
6. **Privacy.** Schedule comments and `raw` text pass `ingest/public/privacy.py` (`scrub_schedule_line`, `redact`):
   Mass-intention text is withheld and private names become "[name withheld]". The church's own public phone number is
   kept as a field. No individuals beyond public clergy roles are stored (the pages name none).
7. **Coordinates check.** A point that lands outside its own address city's county (or > 15 km from the city) is
   checked against the US Census Geocoder (public domain, 1 request/s, `data/raw/california/census_geocode_cache.json`);
   the geocoded point replaces it only when it agrees with the address (`latlng_page` keeps the original).
8. **Diocese.** Point-in-polygon on `public/ca-dioceses.geojson`, cross-checked with the unsimplified Census county
   (cb_2024 1:500k) through `data/real/california/dioceses.json` `county_index` (the county wins near a border);
   else the county of the address city; else the page's own diocese line. `diocese_assigned_by` says which.
   The page's diocese line is kept and compared (`diocese_on_page_agrees`).
9. **Ids.** `<diocese-slug>--<name-slug>-<city-slug>`; if two listings would share one, both get `-<post id>`.
10. **Oakland.** `oakland_entity_id` where the name and city match the `entity` table (read-only export,
    `data/raw/california/oakland_entities_db_2026-10-31.json`) and agree with `data/real/bulletins/match_log.json`.

## robots.txt and rules kept

- `https://discovermass.com/robots.txt` (read at the start of every run, cached 12 h): `User-agent: *` /
  `Crawl-delay: 10`; no Disallow. Honoured as above; robots Disallow would be honoured if added.
- Plain default User-Agent (python-requests). No cookies, logins, JavaScript or bot-check work-arounds. A 403, 429,
  challenge page, or three errors in a row stops the run (`blocked.json`), and nothing is retried harder.
- This crawler was the only client of discovermass.com in this project during the run (other jobs were told to
  leave the host alone; none of the other California scripts request it).

## Files

| file | what it is |
|---|---|
| `churches.json` | every record (all fields above), `summary` with per-diocese counts, assignment methods, disagreements |
| `flagged_rows.json` | every schedule row flagged as a likely typo, with its page |
| `by-diocese/<slug>.json` | the records of one diocese, with its summary |
| `public/ca/<slug>/index.json` | app pins: id, name, type, city, lat, lng, website, has_times, dm (page slug); one per line |
| `public/ca/<slug>/schedules.json` | `{ id: rows[] }` (kind, day, time, end_time, language, raw, flag), loaded on demand |

"""


def write_readme(summary: dict, flagged_by: dict, built_at: str, crawl_note: str) -> None:
    per = summary["by_diocese"]
    lines = [README_HEAD, f"## Counts (built {built_at})\n", crawl_note + "\n",
             "| diocese | churches | with Mass times | with website | with any social link | Facebook | Instagram | "
             "YouTube | X | flagged rows (likely typos) | other jurisdiction on page | possible duplicate listings |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot = Counter()
    for s, v in per.items():
        n = v["churches"]
        if not n and s == "unassigned":
            continue
        pc = (lambda k: f"{v[k]} ({round(100 * v[k] / n)}%)" if n else "0")
        lines.append(f"| {s} | {n} | {pc('with_times')} | {pc('with_website')} | {v['with_any_social']} | "
                     f"{v['with_social']['facebook']} | {v['with_social']['instagram']} | {v['with_social']['youtube']} | "
                     f"{v['with_social']['x']} | {v['flagged_rows']} ({v['flagged_likely_typo']}) | "
                     f"{v['other_jurisdiction_on_page']} | {v['possible_duplicate_listings']} |")
        for k in ("churches", "with_times", "with_website", "with_any_social", "flagged_rows", "flagged_likely_typo",
                  "diocese_on_page_disagrees", "other_jurisdiction_on_page", "possible_duplicate_listings"):
            tot[k] += v[k]
        for k in dc.SOCIAL:
            tot[k] += v["with_social"][k]
    n = tot["churches"] or 1
    lines.append(f"| **all** | **{tot['churches']}** | **{tot['with_times']} ({round(100 * tot['with_times'] / n)}%)** | "
                 f"**{tot['with_website']} ({round(100 * tot['with_website'] / n)}%)** | **{tot['with_any_social']}** | "
                 f"**{tot['facebook']}** | **{tot['instagram']}** | **{tot['youtube']}** | **{tot['x']}** | "
                 f"**{tot['flagged_rows']} ({tot['flagged_likely_typo']})** | **{tot['other_jurisdiction_on_page']}** | "
                 f"**{tot['possible_duplicate_listings']}** |")
    lines.append(f"\nThe page's own Latin diocese line disagrees with the assigned diocese for "
                 f"{tot['diocese_on_page_disagrees']} church{'' if tot['diocese_on_page_disagrees'] == 1 else 'es'} (listed in churches.json summary; the page's line looks wrong). 'Other jurisdiction' = "
                 "the page names an eparchy or similar (Eastern Catholic churches); they are placed in the Latin "
                 "diocese whose territory they stand in, for the map only. 'Possible duplicate listings' = a second "
                 "DiscoverMass post for the same name and street; both are kept, the second pin carries `dup`.")
    lines.append("\nDiocese assignment: " + "; ".join(f"{k}: {v}" for k, v in summary["assigned_by"].items()) + ".\n")
    lines.append("Flagged schedule rows by reason: " + "; ".join(f"{k}: {v}" for k, v in
                                                               sorted(flagged_by.items(), key=lambda x: -x[1])) + ".\n")
    exc = summary.get("excluded_pages") or []
    lines.append(f"Excluded from the per-diocese and app files ({len(exc)}): pages whose content belongs to another "
                 "church (kept in churches.json with `excluded`). " +
                 " ".join(f"`{e['dm_slug']}`: {e['why']}." for e in exc) + "\n")
    oak = summary.get("oakland_entity_links") or {}
    if oak:
        lines.append(f"Oakland: {oak.get('entities_linked', 0)} of {oak.get('entities_in_db', 0)} parishes/missions in "
                     f"the entity table linked to a page ({oak.get('name+city', 0)} pages by name + city, "
                     f"{oak.get('match_log only', 0)} by match_log only); {oak.get('entities_without_page', 0)} have no "
                     "page under their own name (Divine Mercy, Oakland; St. Anthony, Oakley). "
                     f"{oak.get('address only (left for a person)', 0)} page(s) share a street address with an entity "
                     "under another name (\"St. Lawrence O'Toole/St. Cyril\" at Divine Mercy's 3725 High St.): recorded "
                     "as candidates, not linked. Pages in Oakland territory with no entity: St. Albert Priory (Dominican "
                     "house), St. Elias Melkite (Eastern Catholic), St. Paschal Baylon (3700 Dorisa Ave; not in the "
                     "diocesan directory we hold, status to check).\n")
    (OUT / "README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
