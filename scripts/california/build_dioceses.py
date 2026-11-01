"""California's 12 Catholic dioceses as whole-county unions: the shared reference file and the map shapes.

    python3 -I scripts/california/build_dioceses.py [--mapshaper /path/to/mapshaper]

Writes
    data/real/california/dioceses.json      slug, name, rank, province, see city, cathedral (+ lat/lng), counties, sources
    public/ca-dioceses.geojson              the state map: 12 diocese polygons, simplified together (shared edges stay shared)

Boundaries: US Census Bureau cartographic boundary counties, 2024, 1:500,000 (cb_2024_us_county_500k, public domain),
already on disk under data/raw/civic/tiger/ (ingest/public/civic/geometry.py downloads it). Cathedral coordinates: the
US Census Geocoder (public domain) on the cathedral's street address; the Oakland cathedral from our own entity row.
Union + simplification run in mapshaper (topology-aware, so neighbouring dioceses never gap or overlap).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import shapefile  # pyshp

ROOT = Path(__file__).resolve().parents[2]
COUNTY_SHP = ROOT / "data" / "raw" / "civic" / "tiger" / "cb_2024_us_county_500k" / "cb_2024_us_county_500k.shp"
OUT_JSON = ROOT / "data" / "real" / "california" / "dioceses.json"
OUT_STATE = ROOT / "public" / "ca-dioceses.geojson"
OUT_DIR = ROOT / "public" / "ca"
GEOCODE_CACHE = ROOT / "data" / "raw" / "california" / "cathedral_geocode.json"
BOUNDARY_SOURCE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

WIKI = "https://en.wikipedia.org/wiki/"
# Each diocese: counties and the sources that state them. "read" = we read that page on 2026-10-31 and it says so;
# "reference" = a reliable reference we could not re-read from this network (diocese site 403, Wikipedia 429).
D = [
    dict(slug="los-angeles", name="Archdiocese of Los Angeles", short="Los Angeles", rank="archdiocese", province="los-angeles",
         see="Los Angeles", website="https://lacatholics.org",
         cathedral=("Cathedral of Our Lady of the Angels", "555 W Temple St, Los Angeles, CA 90012"),
         counties=["Los Angeles", "Ventura", "Santa Barbara"],
         sources=[("read", "https://lacatholics.org/santa-barbara-pastoral-region/", "Pastoral Region Area: Santa Barbara and Ventura Counties"),
                  ("read", "https://lacatholics.org/san-pedro-pastoral-region/", "Long Beach and Southern Los Angeles County"),
                  ("reference", WIKI + "Roman_Catholic_Archdiocese_of_Los_Angeles", None)]),
    dict(slug="san-francisco", name="Archdiocese of San Francisco", short="San Francisco", rank="archdiocese", province="san-francisco",
         see="San Francisco", website="https://sfarch.org",
         cathedral=("Cathedral of Saint Mary of the Assumption", "1111 Gough St, San Francisco, CA 94109"),
         counties=["San Francisco", "San Mateo", "Marin"],
         sources=[("read", "https://sfarch.org/wp-json/wp/v2/pages?search=Marin", "Restorative Justice Ministry page: within Marin, San Francisco, and San Mateo counties"),
                  ("reference", WIKI + "Roman_Catholic_Archdiocese_of_San_Francisco", None)]),
    dict(slug="fresno", name="Diocese of Fresno", short="Fresno", rank="diocese", province="los-angeles", see="Fresno",
         website="https://dioceseoffresno.org", cathedral=("St. John's Cathedral", "2814 Mariposa St, Fresno, CA 93721"),
         counties=["Fresno", "Inyo", "Kern", "Kings", "Madera", "Mariposa", "Merced", "Tulare"],
         sources=[("reference", WIKI + "Roman_Catholic_Diocese_of_Fresno", None)]),
    dict(slug="monterey", name="Diocese of Monterey", short="Monterey", rank="diocese", province="los-angeles", see="Monterey",
         website="https://www.dioceseofmonterey.org", cathedral=("Cathedral of San Carlos Borromeo", "500 Church St, Monterey, CA 93940"),
         counties=["Monterey", "San Benito", "San Luis Obispo", "Santa Cruz"],
         sources=[("reference", WIKI + "Roman_Catholic_Diocese_of_Monterey_in_California", None)]),
    dict(slug="oakland", name="Diocese of Oakland", short="Oakland", rank="diocese", province="san-francisco", see="Oakland",
         website="https://oakdiocese.org", cathedral=("Cathedral of Christ the Light", "2121 Harrison St, Oakland, CA 94612"),
         counties=["Alameda", "Contra Costa"],
         sources=[("read", "https://www.oakdiocese.org/history-of-our-diocese/", "covers Alameda and Contra Costa counties (read for docs/research/09-diocese-of-oakland.md)"),
                  ("reference", WIKI + "Roman_Catholic_Diocese_of_Oakland", None)]),
    dict(slug="orange", name="Diocese of Orange", short="Orange", rank="diocese", province="los-angeles", see="Orange",
         website="https://www.rcbo.org", cathedral=("Christ Cathedral", "13280 Chapman Ave, Garden Grove, CA 92840"),
         counties=["Orange"],
         sources=[("read", "https://www.rcbo.org/", "In the heart of Orange County"),
                  ("reference", WIKI + "Roman_Catholic_Diocese_of_Orange", None)]),
    dict(slug="sacramento", name="Diocese of Sacramento", short="Sacramento", rank="diocese", province="san-francisco", see="Sacramento",
         website="https://www.scd.org", cathedral=("Cathedral of the Blessed Sacrament", "1017 11th St, Sacramento, CA 95814"),
         counties=["Amador", "Butte", "Colusa", "El Dorado", "Glenn", "Lassen", "Modoc", "Nevada", "Placer", "Plumas", "Sacramento",
                   "Shasta", "Sierra", "Siskiyou", "Solano", "Sutter", "Tehama", "Trinity", "Yolo", "Yuba"],
         sources=[("read", "https://www.scd.org/", "serves 20 counties of northern California (the count; the list is from the reference)"),
                  ("reference", WIKI + "Roman_Catholic_Diocese_of_Sacramento", None)]),
    dict(slug="san-bernardino", name="Diocese of San Bernardino", short="San Bernardino", rank="diocese", province="los-angeles",
         see="San Bernardino", website="https://www.sbdcatholics.org",
         cathedral=("Cathedral of Our Lady of the Rosary", "2525 N Arrowhead Ave, San Bernardino, CA 92405"),
         counties=["San Bernardino", "Riverside"],
         sources=[("reference", WIKI + "Roman_Catholic_Diocese_of_San_Bernardino", None)]),
    dict(slug="san-diego", name="Diocese of San Diego", short="San Diego", rank="diocese", province="los-angeles", see="San Diego",
         website="https://sdcatholic.org", cathedral=("St. Joseph Cathedral", "1535 3rd Ave, San Diego, CA 92101"),
         counties=["San Diego", "Imperial"],
         sources=[("read", "https://www.sdcatholic.org/wp-json/wp/v2/pages?search=Imperial", "96 parishes and 13 missions across 9,000 square miles of San Diego and Imperial Counties"),
                  ("reference", WIKI + "Roman_Catholic_Diocese_of_San_Diego", None)]),
    dict(slug="san-jose", name="Diocese of San Jose", short="San Jose", rank="diocese", province="san-francisco", see="San Jose",
         website="https://dsj.org", cathedral=("Cathedral Basilica of St. Joseph", "80 S Market St, San Jose, CA 95113"),
         counties=["Santa Clara"],
         sources=[("reference", WIKI + "Roman_Catholic_Diocese_of_San_Jose_in_California", None)]),
    dict(slug="santa-rosa", name="Diocese of Santa Rosa", short="Santa Rosa", rank="diocese", province="san-francisco", see="Santa Rosa",
         website="https://srdiocese.org", cathedral=("Cathedral of St. Eugene", "2323 Montgomery Dr, Santa Rosa, CA 95405"),
         counties=["Del Norte", "Humboldt", "Lake", "Mendocino", "Napa", "Sonoma"],
         sources=[("reference", WIKI + "Roman_Catholic_Diocese_of_Santa_Rosa_in_California", None)]),
    dict(slug="stockton", name="Diocese of Stockton", short="Stockton", rank="diocese", province="san-francisco", see="Stockton",
         website="https://stocktondiocese.org", cathedral=("Cathedral of the Annunciation", "425 W Magnolia St, Stockton, CA 95203"),
         counties=["Alpine", "Calaveras", "Mono", "San Joaquin", "Stanislaus", "Tuolumne"],
         sources=[("reference", WIKI + "Roman_Catholic_Diocese_of_Stockton", None)]),
]
FETCHED = "2026-10-31"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def geocode(addr: str, cache: dict) -> dict | None:
    """Census Geocoder, current address ranges first, then the 2020 ranges (Christ Cathedral matches only there)."""
    if cache.get(addr):
        return cache[addr]
    out = None
    for bench in ("Public_AR_Current", "Public_AR_Census2020"):
        r = requests.get(GEOCODER, params={"address": addr, "benchmark": bench, "format": "json"}, timeout=30)
        m = (r.json().get("result") or {}).get("addressMatches") or []
        time.sleep(1)
        if m:
            out = {"lat": round(m[0]["coordinates"]["y"], 6), "lng": round(m[0]["coordinates"]["x"], 6),
                   "matched": m[0]["matchedAddress"], "benchmark": bench, "fetched_at": now_iso()}
            break
    cache[addr] = out
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapshaper", default="mapshaper")
    a = ap.parse_args()

    sf = shapefile.Reader(str(COUNTY_SHP))
    ca = {r["NAME"]: r["GEOID"] for r in sf.records() if r["STATEFP"] == "06"}
    assert len(ca) == 58, len(ca)
    seen: dict[str, str] = {}
    for d in D:
        for c in d["counties"]:
            assert c in ca, c
            assert c not in seen, (c, seen.get(c), d["slug"])
            seen[c] = d["slug"]
    assert len(seen) == 58, sorted(set(ca) - set(seen))

    GEOCODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(GEOCODE_CACHE.read_text()) if GEOCODE_CACHE.exists() else {}
    out = []
    for d in D:
        cname, caddr = d["cathedral"]
        g = geocode(caddr, cache)
        out.append({
            "slug": d["slug"], "name": d["name"], "short_name": d["short"], "rank": d["rank"],
            "province": d["province"], "see_city": d["see"], "website": d["website"],
            "cathedral": {"name": cname, "address": caddr, "lat": g["lat"] if g else None, "lng": g["lng"] if g else None,
                          "coords_source": f"US Census Geocoder ({g.get('benchmark', 'Public_AR_Current')})" if g else None,
                          "geocoder_matched": g["matched"] if g else None},
            "counties": [{"name": c, "geoid": ca[c]} for c in sorted(d["counties"])],
            "source_url": next((u for k, u, _ in d["sources"] if k == "read"), d["sources"][0][1]),
            "sources": [{"kind": k, "url": u, "says": q, "fetched_at": FETCHED if k == "read" else None} for k, u, q in d["sources"]],
            "counties_verified_from_diocese_site": any(k == "read" for k, _, _ in d["sources"]),
            "fetched_at": FETCHED,
        })
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=1))
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({
        "built_at": now_iso(),
        "about": "California's 12 Latin-rite Catholic dioceses. Each is a set of whole counties; the 58 counties are each in exactly one. "
                 "Use county_index (Census county GEOID -> diocese slug) or public/ca-dioceses.geojson (point in polygon) to assign a church.",
        "boundary_source": BOUNDARY_SOURCE, "boundary_note": "US Census Bureau cartographic boundary counties 2024, 1:500k, public domain",
        "dioceses": out,
        "county_index": {ca[c]: s for c, s in sorted(seen.items(), key=lambda x: ca[x[0]])},
        "county_names": {ca[c]: c for c in sorted(ca)},
    }, indent=1, ensure_ascii=False) + "\n")

    # Shapes: tag each CA county with its diocese, dissolve, simplify together.
    with tempfile.TemporaryDirectory() as tmp:
        tag = Path(tmp) / "tag.csv"
        tag.write_text("GEOID,slug\n" + "".join(f"{ca[c]},{s}\n" for c, s in seen.items()))
        meta = Path(tmp) / "meta.csv"
        meta.write_text("slug,name,short,rank\n" + "".join(f"{d['slug']},{d['name']},{d['short']},{d['rank']}\n" for d in D))
        base = [a.mapshaper, "-i", str(COUNTY_SHP), "-filter", "STATEFP == '06'", "-join", str(tag), "keys=GEOID,GEOID",
                "field-types=GEOID:str", "-dissolve", "slug", "-join", str(meta), "keys=slug,slug"]
        # State map: ~1 km detail is plenty at zoom 5-7.
        subprocess.run(base + ["-simplify", "interval=900", "keep-shapes", "-filter-slivers", "min-area=4km2",
                               "-o", str(OUT_STATE), "format=geojson", "precision=0.001", "force"], check=True)
        # Each diocese's own outline (public/ca/<slug>/outline.geojson) is made by scripts/california/build_outlines.py.
    # A label point inside each diocese (pole of inaccessibility of its largest part), and the cathedral, for the map.
    from shapely.geometry import shape
    from shapely.ops import polylabel
    fc = json.loads(OUT_STATE.read_text())
    cat = {d["slug"]: d["cathedral"] for d in out}
    for f in fc["features"]:
        g = shape(f["geometry"])
        big = max(getattr(g, "geoms", [g]), key=lambda x: x.area)
        pt = polylabel(big, tolerance=0.01)
        c = cat[f["properties"]["slug"]]
        f["properties"].update(label=[round(pt.x, 3), round(pt.y, 3)], cathedral=c["name"], cathedral_lnglat=[c["lng"], c["lat"]])
    OUT_STATE.write_text(json.dumps(fc, separators=(",", ":")))
    # Everything outside California, to soften the neighbours on the map (one polygon with the state as holes).
    from shapely.geometry import box, mapping
    from shapely.ops import unary_union
    ca_union = unary_union([shape(f["geometry"]) for f in fc["features"]]).buffer(0)
    # buffered ~5 km outward first, so the simplified edge never crosses California's own shore or towns
    mask = box(-140, 20, -100, 55).difference(ca_union.buffer(0.05)).simplify(0.01)
    def rnd(c):
        return [round(c[0], 3), round(c[1], 3)] if isinstance(c[0], float) else [rnd(x) for x in c]
    m = mapping(mask)
    (ROOT / "public" / "ca-mask.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": m["type"], "coordinates": rnd(m["coordinates"])}}]}, separators=(",", ":")))
    print(OUT_JSON, OUT_STATE, OUT_STATE.stat().st_size, "bytes; mask", (ROOT / "public" / "ca-mask.geojson").stat().st_size)


if __name__ == "__main__":
    main()
