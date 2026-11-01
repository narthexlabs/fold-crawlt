"""Add Census Geocoder coordinates to schools_directory.json, schools_nces.json and cemeteries.json.

https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address=...&benchmark=Public_AR_Current&format=json
Results are cached in data/raw/geocode_cache.json (shared with the parish scrapers).
When the Census geocoder misses, NCES's own lat/lng is used (schools only), flagged geo_source.

Run last:  python -I ingest/public/schools/geocode.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schools import REAL, census_geocode, load_json, save_json  # noqa: E402


def one_line(r: dict) -> str | None:
    addr = r.get("address")
    if not addr or addr.upper().startswith("PO BOX"):
        return None
    return f"{addr}, {r.get('city') or ''}, CA {r.get('zip') or ''}".strip()


def apply(records: list[dict], fallback: dict | None = None) -> tuple[int, int]:
    hit = fb = 0
    for r in records:
        q = one_line(r)
        g = census_geocode(q) if q else None
        if g:
            r.update({"lat": g["lat"], "lng": g["lng"], "geo_matched_address": g["matched_address"],
                      "geo_source": "census_geocoder", "geo_source_url": g["source_url"]})
            hit += 1
        elif fallback and fallback.get(r.get("nces_ppin")):
            lat, lng = fallback[r["nces_ppin"]]
            r.update({"lat": lat, "lng": lng, "geo_matched_address": None, "geo_source": "nces_pss",
                      "geo_source_url": "https://nces.ed.gov/surveys/pss/pssdata.asp"})
            fb += 1
        else:
            r.update({"lat": r.get("lat"), "lng": r.get("lng"), "geo_source": r.get("geo_source")})
    return hit, fb


def main() -> None:
    nces_p = REAL / "schools_nces.json"
    nces = load_json(nces_p)
    fb = {}
    for r in sorted(nces["records"], key=lambda r: r["survey_year"]):
        if r.get("lat") and r.get("lng") and r.get("geo_source") != "census_geocoder":
            fb[r["nces_ppin"]] = (r["lat"], r["lng"])
    for name, path, use_fb in [("schools_directory", REAL / "schools_directory.json", True),
                               ("cemeteries", REAL / "cemeteries.json", False)]:
        d = load_json(path)
        hit, f = apply(d["records"], fb if use_fb else None)
        save_json(path, d)
        print(f"{name}: {hit}/{len(d['records'])} Census matches, {f} NCES fallback")


if __name__ == "__main__":
    main()
