#!/usr/bin/env python3
"""Coordinates for the parishes the directory geocode could not place (7 of 75 on 2026-10-26).

Order of attempts per parish, first hit wins:
  1. U.S. Census geocoder (onelineaddress, Public_AR_Current) on the street address
  2. OpenStreetMap Nominatim, free-text address search
  3. OpenStreetMap Nominatim, church name + city (must return a place_of_worship)
St. Joseph (Fremont) lists only a P.O. Box in the directory; its church address (43148 Mission Boulevard)
comes from the parish website footer (https://www.saintjosephmsj.org/).

Nominatim is used at <= 1 request per 1.5 s with the project's identifying User-Agent, per the OSM usage
policy. Output: data/real/civic/parish_geocode_fill.json (read by _civic.load_parishes()).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import OUT, REAL, SESSION, load_json, now_iso, save_json  # noqa: E402

# physical addresses where the directory gives only a mailing address
PHYSICAL = {"joseph-fremont": ("43148 Mission Boulevard", "https://www.saintjosephmsj.org/")}
NAME_QUERY = {
    "francis-assisi-concord": "St. Francis of Assisi, Oak Grove Road, Concord",
    "michael-livermore": "St. Michael Catholic Church, Livermore, California",
    "felicitas-san-leandro": "St. Felicitas Catholic Church, San Leandro, California",
    "columba-oakland": "St. Columba Catholic Church, Oakland, California",
    "augustine-oakland": "St. Augustine Catholic Church, Oakland, California",
    "our-lady-guadalupe-fremont": "Our Lady of Guadalupe Catholic Church, Fremont, California",
}


def census(addr: str):
    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address=" + quote_plus(addr)
           + "&benchmark=Public_AR_Current&format=json")
    try:
        m = SESSION.get(url, timeout=30).json()["result"]["addressMatches"]
    except Exception:
        return None
    if m:
        return {"lat": m[0]["coordinates"]["y"], "lng": m[0]["coordinates"]["x"], "geo_source": "census",
                "matched": m[0]["matchedAddress"], "geo_source_url": url}
    return None


def nominatim(q: str, want_church: bool):
    params = {"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "us"}
    url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
    time.sleep(1.5)
    try:
        res = SESSION.get(url, timeout=30).json()
    except Exception:
        return None
    for x in res:
        church = x.get("type") in ("place_of_worship", "church") or x.get("category") == "amenity" and x.get("type") == "place_of_worship"
        is_point = x.get("category") in ("building", "amenity", "place") and x.get("addresstype") != "road"
        if (want_church and church) or (not want_church and is_point and x.get("category") != "highway"):
            return {"lat": float(x["lat"]), "lng": float(x["lon"]), "geo_source": "openstreetmap_nominatim",
                    "matched": x["display_name"], "osm": f"{x.get('osm_type')}/{x.get('osm_id')}",
                    "geo_source_url": url}
    return None


# Parishes in the MAP 2022 appendix, in the diocese's own Detention Ministry parish dropdown (2025-09) and listed
# as active by GCatholic, but absent from the 2026 oakdiocese.org directory scrape (open item for Matthew).
# Included as flagged catchment generators so neighbouring catchments are not overstated. St. Anne (Byron) is in
# the same situation but no sourced street address or map location was found, so it is not modelled.
EXTRA = [
    {"id": "corpus-christi-piedmont", "name": "Corpus Christi Parish", "address": "322 St. James Dr.", "city": "Piedmont",
     "zip": "94611", "source_url": "https://gcatholic.org/churches/usa-11/21212", "map_2022_key": "corpus christi|piedmont"},
    {"id": "john-baptist-el-cerrito", "name": "St. John the Baptist Parish", "address": "11150 San Pablo Ave.",
     "city": "El Cerrito", "zip": "94530", "source_url": "https://gcatholic.org/churches/usa-11/21261",
     "map_2022_key": "john the baptist|el-cerrito"},
    {"id": "joseph-pinole", "name": "St. Joseph Parish", "address": "837 Tennent Ave.", "city": "Pinole", "zip": "94564",
     "source_url": "https://gcatholic.org/churches/usa-11/21266", "map_2022_key": "joseph|pinole"},
]


def main() -> None:
    recs = load_json(REAL / "parishes_directory.json")["records"]
    extra = []
    for e in EXTRA:
        hit = census(f"{e['address']}, {e['city']}, CA {e['zip']}")
        extra.append({**e, **(hit or {"lat": None, "lng": None, "geo_source": None}), "in_2026_directory": False,
                      "status_note": ("In the MAP 2022 appendix, in the parish dropdown of the diocese's Detention Ministry "
                                      "page (https://www.oakdiocese.org/detention-ministry/, modified 2025-09-29) and listed "
                                      "as an active parish by GCatholic (fetched 2026-10-26); absent from the 2026 "
                                      "oakdiocese.org directory scrape. Probably active; not yet confirmed."),
                      "fetched_at": now_iso()})
        print("extra", e["id"], hit and hit["matched"])
    out = []
    for r in recs:
        if r.get("lat") is not None:
            continue
        street, street_src = PHYSICAL.get(r["id"], (r.get("address"), r.get("source_url")))
        addr = f"{street}, {r['city']}, CA"
        hit = census(addr) or nominatim(addr, want_church=False)
        if not hit and r["id"] in NAME_QUERY:
            hit = nominatim(NAME_QUERY[r["id"]], want_church=True)
        rec = {"id": r["id"], "name": r["name"], "address_used": addr, "address_source_url": street_src,
               **(hit or {"lat": None, "lng": None, "geo_source": None}), "fetched_at": now_iso()}
        out.append(rec)
        print(r["id"], rec.get("geo_source"), rec.get("matched"))
    save_json(OUT / "parish_geocode_fill.json", {
        "meta": {"note": "Coordinates for parishes the directory geocode missed. Census geocoder first, then "
                         "OpenStreetMap Nominatim (data (c) OpenStreetMap contributors, ODbL).",
                 "generated_at": now_iso()},
        "records": out, "extra_parishes": extra})


if __name__ == "__main__":
    main()
