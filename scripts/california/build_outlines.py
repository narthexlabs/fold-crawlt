"""Each California diocese's outline for its own map view: whole counties, cut back to the real shoreline.

    python3 -I scripts/california/build_outlines.py

Writes public/ca/<slug>/outline.geojson (12 files).

Why not the 1:500,000 cartographic boundary counties (cb_2024_us_county_500k)? They are clipped to the shoreline, but so
generalised that they cross the mouths of bays and estuaries (San Leandro Bay, Alameda's south shore, Bolinas Lagoon):
at street zoom the line sits in the water, 100 to 800 m off the basemap's coast. So the land is built from full-resolution
Census TIGER/Line instead (all public domain, https://www2.census.gov/geo/tiger/TIGER2024/):
    COUNTY/tl_2024_us_county.zip          legal county boundaries; they run out over the water (to the county line)
    AREAWATER/tl_2024_<GEOID>_areawater.zip   water polygons per county; here only bays, estuaries and ocean
                                          (MTFCC H2051, H2053), so rivers and inland lakes stay inside the county
land of a diocese = union of its counties, minus that coastal water. The result matches the 500k files' land area to
within about 1 percent but follows the shore. Then Douglas-Peucker at 0.0002 degrees (about 20 m, topology kept),
coordinates rounded to 5 decimals (about 1 m), islands under about 0.2 km2 and holes under about 0.5 km2 dropped.
Files are downloaded once into data/raw/civic/tiger/tl_2024/ (gitignored, about 105 MB).
The coarse state shapes (public/ca-dioceses.geojson, public/ca-mask.geojson) are still made by build_dioceses.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests
import shapefile  # pyshp
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
TL = ROOT / "data" / "raw" / "civic" / "tiger" / "tl_2024"
BASE = "https://www2.census.gov/geo/tiger/TIGER2024"
DIOCESES = ROOT / "data" / "real" / "california" / "dioceses.json"
PUB = ROOT / "public" / "ca"
COASTAL = {"H2051", "H2053"}  # bay/estuary/gulf/sound, ocean/sea
TOLERANCE = 0.0002  # degrees
MIN_PART = 2e-5  # square degrees, about 0.2 km2 at California's latitudes
MIN_HOLE = 5e-5  # about 0.5 km2


def fetch(name: str, url: str) -> Path:
    p = TL / name
    if not p.exists():
        TL.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


def rnd(c):
    return [round(c[0], 5), round(c[1], 5)] if isinstance(c[0], float) else [rnd(x) for x in c]


def main() -> None:
    meta = json.loads(DIOCESES.read_text())
    counties = {}
    for sr in shapefile.Reader(str(fetch("tl_2024_us_county.zip", f"{BASE}/COUNTY/tl_2024_us_county.zip"))).iterShapeRecords():
        r = sr.record.as_dict()
        if r["STATEFP"] == "06":
            counties[r["GEOID"]] = make_valid(shape(sr.shape.__geo_interface__))
    assert len(counties) == 58
    water: dict[str, list] = {}
    for d in meta["dioceses"]:
        for c in d["counties"]:
            name = f"tl_2024_{c['geoid']}_areawater.zip"
            rd = shapefile.Reader(str(fetch(name, f"{BASE}/AREAWATER/{name}")))
            water[c["geoid"]] = [make_valid(shape(sr.shape.__geo_interface__)) for sr in rd.iterShapeRecords()
                                 if sr.record.as_dict()["MTFCC"] in COASTAL]
    for d in meta["dioceses"]:
        ids = [c["geoid"] for c in d["counties"]]
        land = unary_union([counties[i] for i in ids]).difference(unary_union([w for i in ids for w in water[i]]))
        g = land.simplify(TOLERANCE, preserve_topology=True)
        parts = [p for p in (g.geoms if isinstance(g, MultiPolygon) else [g]) if isinstance(p, Polygon) and p.area >= MIN_PART]
        parts = [orient(Polygon(p.exterior, [i for i in p.interiors if Polygon(i).area >= MIN_HOLE])) for p in parts]
        g = parts[0] if len(parts) == 1 else MultiPolygon(parts)
        m = mapping(g)
        short = d.get("short_name") or d["name"]
        coords = rnd(m["coordinates"])
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {
            "slug": d["slug"], "name": d["name"], "short": short, "rank": d["rank"],
            "source": "US Census Bureau TIGER/Line 2024 counties minus coastal water (AREAWATER H2051/H2053), dissolved"},
            "geometry": {"type": m["type"], "coordinates": coords}}]}
        out = PUB / d["slug"] / "outline.geojson"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fc, separators=(",", ":")))
        n = sum(len(r) for poly in (coords if m["type"] == "MultiPolygon" else [coords]) for r in poly)
        print(f"{d['slug']}: {len(parts)} part(s), {n} vertices, {out.stat().st_size} B")


if __name__ == "__main__":
    main()
