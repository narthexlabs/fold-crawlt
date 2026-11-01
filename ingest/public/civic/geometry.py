#!/usr/bin/env python3
"""Boundaries for the civic layer: census tracts, the two counties, and the places (cities / CDPs) that
overlap them, from the Census Bureau's 2024 cartographic boundary files; plus 2020 census-block
population points used to population-weight catchments.

Sources (all U.S. Census Bureau):
  https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_06_tract_500k.zip
  https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_06_place_500k.zip
  https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip
  https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_06_tabblock20.zip  (POP20 + internal points)

Outputs (data/real/civic/):
  tracts_2020.geojson        621 tracts, simplified with mapshaper (Visvalingam, 12% of vertices kept)
  diocese_counties.geojson   Alameda + Contra Costa, simplified
  places_diocese.geojson     cities/CDPs overlapping the two counties, simplified
  places_diocese.json        place list (GEOID, name, kind, share of area inside the diocese)
Raw/intermediate (data/raw/civic/): unzipped shapefiles, full-resolution GeoJSON, blocks_2020_diocese.csv

Usage: python ingest/public/civic/geometry.py
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import shapefile  # pyshp
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import OUT, RAW, download, now_iso, save_json  # noqa: E402

TIGER = RAW / "tiger"
URLS = {
    "tract": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_06_tract_500k.zip",
    "place": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_06_place_500k.zip",
    "county": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip",
    "blocks": "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_06_tabblock20.zip",
}
COUNTYFP = {"001", "013"}
MAPSHAPER = "mapshaper@0.6.102"


def unzip(zpath: Path, members: list[str] | None = None) -> Path:
    d = zpath.with_suffix("")
    d.mkdir(exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if members is None or any(n.endswith(m) for m in members):
                if not (d / n).exists():
                    z.extract(n, d)
    return d


def read_shp(d: Path, stem: str, keep):
    sf = shapefile.Reader(str(d / stem))
    fields = [f[0] for f in sf.fields[1:]]
    feats = []
    for sr in sf.iterShapeRecords():
        props = dict(zip(fields, sr.record))
        if keep(props):
            feats.append({"type": "Feature", "properties": props, "geometry": sr.shape.__geo_interface__})
    return feats


def write_fc(path: Path, feats, meta=None):
    fc = {"type": "FeatureCollection", "features": feats}
    if meta:
        fc["meta"] = meta
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")


def simplify(src: Path, dst: Path, pct: str, fields: str, meta: dict) -> None:
    cmd = ["npx", "-y", MAPSHAPER, "-i", str(src), "-simplify", "visvalingam", pct, "keep-shapes",
           "-filter-fields", fields, "-o", str(dst), "format=geojson", "precision=0.00001"]
    subprocess.run(cmd, check=True, capture_output=True)
    fc = json.loads(dst.read_text())
    fc = {"type": "FeatureCollection", "meta": meta, "features": fc["features"]}
    dst.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")


def blocks_csv() -> Path:
    out = RAW / "blocks_2020_diocese.csv"
    if out.exists():
        return out
    z = download(URLS["blocks"], TIGER / "tl_2020_06_tabblock20.zip")
    d = unzip(z, [".dbf"])
    dbf = next(d.rglob("*.dbf"))
    r = shapefile.Reader(dbf=open(dbf, "rb"))
    fields = [f[0] for f in r.fields[1:]]
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["geoid20", "tract", "pop20", "housing20", "lat", "lng", "aland20"])
        for rec in r.iterRecords():
            p = dict(zip(fields, rec))
            if p["COUNTYFP20"] not in COUNTYFP:
                continue
            w.writerow([p["GEOID20"], p["GEOID20"][:11], p["POP20"], p["HOUSING20"],
                        float(p["INTPTLAT20"]), float(p["INTPTLON20"]), p["ALAND20"]])
            n += 1
    print("blocks:", n)
    return out


def main() -> None:
    fetched = now_iso()
    zips = {k: download(u, TIGER / Path(u).name) for k, u in URLS.items() if k != "blocks"}

    # counties
    cd = unzip(zips["county"])
    counties = read_shp(cd, "cb_2024_us_county_500k", lambda p: p["STATEFP"] == "06" and p["COUNTYFP"] in COUNTYFP)
    raw_c = RAW / "counties_full.geojson"
    write_fc(raw_c, counties)
    diocese = unary_union([shape(f["geometry"]) for f in counties])
    meta = lambda url, extra=None: {"source": "U.S. Census Bureau cartographic boundary files (1:500k), 2024",  # noqa: E731
                                    "source_url": url, "fetched_at": fetched, **(extra or {})}
    simplify(raw_c, OUT / "diocese_counties.geojson", "8%", "GEOID,NAME",
             meta(URLS["county"], {"note": "Diocese of Oakland = Alameda + Contra Costa counties"}))

    # tracts
    td = unzip(zips["tract"])
    tracts = read_shp(td, "cb_2024_06_tract_500k", lambda p: p["COUNTYFP"] in COUNTYFP)
    raw_t = RAW / "tracts_full.geojson"
    for f in tracts:
        f["properties"] = {"GEOID": f["properties"]["GEOID"], "NAME": f["properties"]["NAMELSAD"],
                           "COUNTY": f["properties"]["NAMELSADCO"], "ALAND": f["properties"]["ALAND"],
                           "AWATER": f["properties"]["AWATER"]}
    write_fc(raw_t, tracts)
    simplify(raw_t, OUT / "tracts_2020.geojson", "12%", "GEOID,NAME,COUNTY,ALAND",
             meta(URLS["tract"], {"note": "2020 census tracts (geography used by ACS 2020-2024); simplified with mapshaper "
                                          "Visvalingam 12% keep-shapes, 5-decimal coordinates. Display only."}))
    print("tracts:", len(tracts))

    # places overlapping the diocese
    pd = unzip(zips["place"])
    places = read_shp(pd, "cb_2024_06_place_500k", lambda p: True)
    keep, plist = [], []
    for f in places:
        g = shape(f["geometry"])
        if not g.intersects(diocese):
            continue
        share = g.intersection(diocese).area / g.area if g.area else 0
        if share < 0.01:
            continue
        p = f["properties"]
        kind = {"25": "city", "43": "town", "57": "CDP"}.get(p["LSAD"], p["LSAD"])
        plist.append({"geoid": p["GEOID"], "name": p["NAME"], "namelsad": p["NAMELSAD"], "kind": kind,
                      "incorporated": kind in ("city", "town"), "share_of_area_in_diocese": round(share, 3)})
        keep.append({"type": "Feature", "properties": {"GEOID": p["GEOID"], "NAME": p["NAME"], "KIND": kind},
                     "geometry": f["geometry"]})
    raw_p = RAW / "places_full.geojson"
    write_fc(raw_p, keep)
    simplify(raw_p, OUT / "places_diocese.geojson", "10%", "GEOID,NAME,KIND",
             meta(URLS["place"], {"note": "Census places (incorporated cities/towns and unincorporated CDPs) overlapping "
                                          "Alameda or Contra Costa county. CDPs are statistical areas, not governments."}))
    plist.sort(key=lambda x: x["name"])
    save_json(OUT / "places_diocese.json", {"meta": meta(URLS["place"]), "count": len(plist), "places": plist})
    print("places:", len(plist), "incorporated:", sum(p["incorporated"] for p in plist))

    blocks_csv()


if __name__ == "__main__":
    main()
