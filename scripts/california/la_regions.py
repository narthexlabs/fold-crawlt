"""Los Angeles: parishes by pastoral region and deanery, from the archdiocese's own region map; approximate region shapes.

    python3 -I scripts/california/la_regions.py [--refetch]

Sources (all the archdiocese's own publications, read 2026-10-31):
  - lacatholics.org/<region>-pastoral-region/ (5 pages): region area, auxiliary bishop, deaneries, parish counts.
  - archla.org/maps -> Google My Maps "Los Angeles Archdiocese regional maps" (linked from each region page as the
    "Interactive map"). Its KML export lists every parish by region folder with street address, city, ZIP and deanery,
    but no coordinates and no region boundaries. google.com/robots.txt allows /maps/d/.
Coordinates: the US Census Geocoder batch service (public domain) on those addresses.
Region shapes: the archdiocese publishes no boundaries. Santa Barbara region = Santa Barbara + Ventura counties
(its page says so). The other four regions share Los Angeles County; their shapes here are APPROXIMATE: Voronoi
cells around each parish, merged by region and clipped to the county. They are labelled approximate everywhere.

Writes data/real/california/dioceses/los-angeles/map/{parishes.json, regions.json, regions-approx.geojson, README.md}.
The region shapes are not served or drawn: regions are list-only on the map (owner decision, 2026-10-31). Raw files stay in data/raw/california/la-map/ (gitignored).
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
import shapefile
from scipy.spatial import Voronoi
from shapely.geometry import MultiPoint, Point, Polygon, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "california" / "la-map"
OUT = ROOT / "data" / "real" / "california" / "dioceses" / "los-angeles" / "map"
PUB = ROOT / "public" / "ca" / "los-angeles"
COUNTY_SHP = ROOT / "data" / "raw" / "civic" / "tiger" / "cb_2024_us_county_500k" / "cb_2024_us_county_500k.shp"
KML_URL = "https://www.google.com/maps/d/kml?mid=1rQQIqx1wTWUKCpZpJ_QwPnBQAZafRAzC&forcekml=1"
MAP_URL = "https://archla.org/maps"
REGIONS = [  # slug, name, page slug
    ("our-lady-of-the-angels", "Our Lady of the Angels"), ("san-fernando", "San Fernando"), ("san-gabriel", "San Gabriel"),
    ("san-pedro", "San Pedro"), ("santa-barbara", "Santa Barbara"),
]
PAGE = "https://lacatholics.org/{}-pastoral-region/"
BATCH = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
FETCHED = "2026-10-31"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def text(h: str) -> str:
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def slugify(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def region_pages(refetch: bool) -> dict:
    out = {}
    for slug, name in REGIONS:
        f = RAW / f"{slug}.html"
        if refetch or not f.exists():
            f.write_text(requests.get(PAGE.format(slug), timeout=30).text)
            time.sleep(2)
        t = text(f.read_text(errors="ignore"))
        area = re.search(r"Pastoral Region Area (.+?) \d+ (?:Hospitals?|Seminaries|Cemeteries)", t)
        bishop = re.search(r"The Most Reverend (.+?) is the regional auxiliary bishop", t)
        count = re.search(r"There are (\d+) parishes", t) or re.search(r"Parishes (\d+) parishes", t, re.I)
        deans = re.findall(r"Deanery (\d+): ((?:Fr|Msgr|Rev|Very Rev)\.?[^:]+?)(?= Deanery \d+:| Parishes in this Region)", t)
        out[slug] = {"name": name, "page": PAGE.format(slug), "area": area.group(1).strip() if area else None,
                     "auxiliary_bishop": ("Most Rev. " + bishop.group(1).strip()) if bishop else None,
                     "parish_count_stated": int(count.group(1)) if count else None,
                     "deaneries": sorted({int(n) for n, _ in deans}), "fetched_at": FETCHED}
    return out


def cdata(s: str) -> str:
    return re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", s.strip(), flags=re.S).strip()


def kml_parishes(refetch: bool) -> list[dict]:
    f = RAW / "archla.kml"
    if refetch or not f.exists():
        f.write_text(requests.get(KML_URL, timeout=60).text)
    k = f.read_text()
    rows = []
    for folder in re.findall(r"<Folder>(.*?)</Folder>", k, re.S):
        fname = re.search(r"<name>([^<]*)</name>", folder).group(1).strip()
        if fname == "HOSPITALS":
            continue
        region = next(s for s, n in REGIONS if fname.startswith(n))
        for pm in re.findall(r"<Placemark>(.*?)</Placemark>", folder, re.S):
            data = {m.group(1).replace("\ufeff", "").strip().lower(): html.unescape(cdata(m.group(2) or ""))
                    for m in re.finditer(r'<Data name="([^"]+)">\s*(?:<value>(.*?)</value>|<value/>)', pm, re.S)}
            name = html.unescape(cdata(re.search(r"<name>(.*?)</name>", pm, re.S).group(1)))
            order = (data.get("order") or data.get("number") or "").upper()
            if order.startswith("C") or re.search(r"\bcemetery\b|mausoleum", name, re.I):
                continue  # cemeteries are listed with Order C1, C2... (kept out of the parish pins)
            label = re.sub(r"\s*\(([^)]*)\)\s*$", "", name).strip()
            dn = re.search(r"(\d+)", data.get("deanery", "") or "")
            rows.append({"name": label, "kml_name": name, "eastern_rite": data.get("easternrite") or None,
                         "address": data.get("address") or None, "city": data.get("city") or None,
                         "zip": (data.get("zip") or "")[:5] or None, "deanery": int(dn.group(1)) if dn else None,
                         "region": region})
    return rows


def geocode(rows: list[dict], refetch: bool) -> None:
    cache_f = RAW / "geocode_batch.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() and not refetch else {}
    todo = [(i, r) for i, r in enumerate(rows) if r["address"] and f"{r['address']}|{r['city']}|{r['zip']}" not in cache]
    for bench in ("Public_AR_Current", "Public_AR_Census2020"):
        if not todo:
            break
        buf = io.StringIO()
        w = csv.writer(buf)
        for i, r in todo:
            w.writerow([i, r["address"], r["city"], "CA", r["zip"] or ""])
        resp = requests.post(BATCH, files={"addressFile": ("a.csv", buf.getvalue())}, data={"benchmark": bench}, timeout=600)
        got = {}
        for line in csv.reader(io.StringIO(resp.text)):
            if len(line) >= 6 and line[2] == "Match":
                x, y = map(float, line[5].split(","))
                got[int(line[0])] = {"lat": round(y, 6), "lng": round(x, 6), "matched": line[4], "quality": line[3], "benchmark": bench}
        for i, r in todo:
            if i in got:
                cache[f"{r['address']}|{r['city']}|{r['zip']}"] = got[i]
        todo = [(i, r) for i, r in todo if i not in got]
        time.sleep(2)
    # Leftovers: the one-line form (it parses "Saint ... Pl" and suite-style addresses the batch form misses), without ZIP+city
    # disagreement (city as printed, then the ZIP alone).
    for i, r in todo:
        for q in (f"{r['address']}, {r['city']}, CA {r['zip'] or ''}", f"{r['address']}, CA {r['zip'] or ''}"):
            hit = None
            for bench in ("Public_AR_Current", "Public_AR_Census2020"):
                j = requests.get(BATCH.replace("addressbatch", "onelineaddress"), params={"address": q, "benchmark": bench, "format": "json"}, timeout=60).json()
                m = (j.get("result") or {}).get("addressMatches") or []
                time.sleep(1)
                if m:
                    hit = {"lat": round(m[0]["coordinates"]["y"], 6), "lng": round(m[0]["coordinates"]["x"], 6),
                           "matched": m[0]["matchedAddress"], "quality": "oneline", "benchmark": bench}
                    break
            if hit:
                cache[f"{r['address']}|{r['city']}|{r['zip']}"] = hit
                break
    cache_f.write_text(json.dumps(cache, indent=1))
    for r in rows:
        g = cache.get(f"{r['address']}|{r['city']}|{r['zip']}")
        r["lat"], r["lng"] = (g["lat"], g["lng"]) if g else (None, None)
        r["coords_source"] = f"US Census Geocoder ({g['benchmark']}, {g['quality']})" if g else None


def county_shapes() -> dict:
    sf = shapefile.Reader(str(COUNTY_SHP))
    return {rec["NAME"]: shape(s.__geo_interface__) for s, rec in zip(sf.shapes(), sf.records())
            if rec["STATEFP"] == "06" and rec["NAME"] in ("Los Angeles", "Ventura", "Santa Barbara")}


def region_shapes(rows: list[dict], counties: dict) -> dict:
    la = counties["Los Angeles"]
    pts = [(r["lng"], r["lat"], r["region"]) for r in rows if r["lat"] is not None and r["region"] != "santa-barbara"
           and la.buffer(0.01).contains(Point(r["lng"], r["lat"]))]
    xy = np.array([(x, y) for x, y, _ in pts])
    minx, miny, maxx, maxy = la.bounds
    far = [(minx - 3, miny - 3), (minx - 3, maxy + 3), (maxx + 3, miny - 3), (maxx + 3, maxy + 3)]
    vor = Voronoi(np.vstack([xy, far]))
    cells = defaultdict(list)
    for i, (_, _, reg) in enumerate(pts):
        reg_idx = vor.point_region[i]
        verts = vor.regions[reg_idx]
        if -1 in verts or not verts:
            continue
        cells[reg].append(Polygon(vor.vertices[verts]))
    out = {}
    for reg, polys in cells.items():
        g = unary_union(polys).intersection(la)
        out[reg] = g.simplify(0.004, preserve_topology=True)
    out["santa-barbara"] = unary_union([counties["Ventura"], counties["Santa Barbara"]]).simplify(0.004, preserve_topology=True)
    return out


def rnd(geom, nd=4):
    def r(c):
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], float) else [r(x) for x in c]
    m = mapping(geom)
    return {"type": m["type"], "coordinates": r(m["coordinates"])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch", action="store_true")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    PUB.mkdir(parents=True, exist_ok=True)
    regions = region_pages(a.refetch)
    rows = kml_parishes(a.refetch)
    geocode(rows, a.refetch)
    # ids: los-angeles--<name>-<city> (the statewide pattern <diocese-slug>--<name-slug>-<city-slug>), unique
    seen = Counter()
    for r in rows:
        base = "los-angeles--" + slugify(f"{r['name']} {r['city'] or ''}")
        seen[base] += 1
        r["id"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
        # The archdiocese lists every row as a parish of its region. `kind` only picks the map glyph, from the name:
        # the mother church, a church named "Mission ..." (the historic missions and mission churches), a chapel.
        r["type"] = "parish"
        r["kind"] = ("cathedral" if r["name"] == "Cathedral of Our Lady of the Angels"
                     else "chapel" if re.search(r"\bchapel\b", r["name"], re.I)
                     else "mission" if re.search(r"\bmission\b", r["name"], re.I) else "parish")
        r["source_url"] = MAP_URL
        r["fetched_at"] = FETCHED
    counties = county_shapes()
    shapes = region_shapes(rows, counties)
    by = Counter(r["region"] for r in rows)
    for s, info in regions.items():
        info["parish_count_in_map"] = by.get(s, 0)
        info["deaneries"] = sorted({r["deanery"] for r in rows if r["region"] == s and r["deanery"]})  # from the map rows
        info["shape"] = "exact: Santa Barbara + Ventura counties" if s == "santa-barbara" else "approximate"
    (OUT / "parishes.json").write_text(json.dumps({"source": MAP_URL, "kml": KML_URL, "fetched_at": FETCHED, "built_at": now_iso(),
                                                    "count": len(rows), "geocoded": sum(r["lat"] is not None for r in rows),
                                                    "parishes": rows}, indent=1, ensure_ascii=False) + "\n")
    (OUT / "regions.json").write_text(json.dumps({"source": [PAGE.format(s) for s, _ in REGIONS], "map": MAP_URL, "fetched_at": FETCHED,
                                                   "regions": regions}, indent=1, ensure_ascii=False) + "\n")
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": s, "name": regions[s]["name"], "approximate": s != "santa-barbara",
                                           "bishop": regions[s]["auxiliary_bishop"], "area": regions[s]["area"]},
         "geometry": rnd(g)} for s, g in shapes.items()]}
    (OUT / "regions-approx.geojson").write_text(json.dumps(fc, separators=(",", ":")))
    print(len(rows), "parishes;", sum(r["lat"] is not None for r in rows), "geocoded;", dict(by))
    print({s: (v["parish_count_stated"], v["parish_count_in_map"], v["auxiliary_bishop"], v["deaneries"]) for s, v in regions.items()})
    print("regions-approx.geojson", (OUT / "regions-approx.geojson").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
