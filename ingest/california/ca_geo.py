"""California county and place lookups from the Census cartographic boundary files already on disk.

    data/raw/civic/tiger/cb_2024_us_county_500k/   (US counties, 2024, 1:500k)  -> CA counties only
    data/raw/civic/tiger/cb_2024_06_place_500k/    (CA places: cities, towns and CDPs, 2024)

Both come from https://www2.census.gov/geo/tiger/GENZ2024/shp/ (ingest/public/civic/geometry.py downloads them).
Nothing here touches the network.

    county_at(lat, lng)       -> county name ("Los Angeles") by point-in-polygon, or None
    place_counties()          -> {place-slug: {"name", "geoid", "county", "counties"}}  (largest-area county first)
    city_county(city)         -> county for a city name or slug (Census place, then the neighbourhood list below)

A place that spans two counties gets the county holding most of its land area. City-of-Los-Angeles neighbourhoods and
a few other postal names are not Census places; NEIGHBOURHOODS maps the ones DiscoverMass uses to their county
(used only to order the crawl and as a last-resort fallback; the church's own lat/lng decides when it exists).
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import shapefile  # pyshp
from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
TIGER = ROOT / "data" / "raw" / "civic" / "tiger"
COUNTY_SHP = TIGER / "cb_2024_us_county_500k" / "cb_2024_us_county_500k"
PLACE_SHP = TIGER / "cb_2024_06_place_500k" / "cb_2024_06_place_500k"
CACHE = ROOT / "data" / "raw" / "california" / "census_place_county.json"

# Postal / neighbourhood names that are not Census places (CA). County only; for ordering and fallback.
NEIGHBOURHOODS = {
    # City of Los Angeles communities
    **{k: "Los Angeles" for k in """
    north-hollywood van-nuys sherman-oaks encino tarzana woodland-hills canoga-park reseda northridge granada-hills
    sylmar san-pedro wilmington harbor-city hollywood west-hollywood-la studio-city sun-valley pacoima panorama-city
    chatsworth venice playa-del-rey playa-vista westchester mission-hills arleta tujunga sunland highland-park
    eagle-rock boyle-heights lake-view-terrace west-hills winnetka valley-village toluca-lake pacific-palisades
    brentwood westwood hyde-park north-hills porter-ranch shadow-hills sun-land la-tuna-canyon lincoln-heights
    el-sereno silver-lake echo-park los-feliz mar-vista palms cheviot-hills watts koreatown mid-city mid-wilshire
    hancock-park chinatown little-tokyo cypress-park glassell-park atwater-village mount-washington harbor-gateway
    sunland-tujunga van-nuys-ca north-hollywood-ca canoga west-los-angeles south-los-angeles east-los-angeles
    city-of-industry newhall valencia saugus canyon-country castaic stevenson-ranch wilmington-ca
    marina-del-rey topanga agoura toluca-lake-ca pacoima-ca
    """.split()},
    # Ventura / Santa Barbara postal names
    "montecito": "Santa Barbara", "goleta": "Santa Barbara", "isla-vista": "Santa Barbara", "summerland": "Santa Barbara",
    "newbury-park": "Ventura", "westlake-village": "Los Angeles", "somis": "Ventura", "piru": "Ventura",
    # Orange County postal names
    "corona-del-mar": "Orange", "balboa": "Orange", "capistrano-beach": "Orange", "trabuco-canyon": "Orange",
    "foothill-ranch": "Orange", "laguna-niguel": "Orange", "san-juan-capistrano": "Orange",
    # San Diego postal names
    "la-jolla": "San Diego", "pacific-beach": "San Diego", "ocean-beach": "San Diego", "point-loma": "San Diego",
    "rancho-bernardo": "San Diego", "san-ysidro": "San Diego", "mira-mesa": "San Diego", "clairemont": "San Diego",
    "rancho-penasquitos": "San Diego", "scripps-ranch": "San Diego",  # not carmel-valley: Monterey has one too "paradise-hills": "San Diego",
    "cardiff-by-the-sea": "San Diego", "cardiff": "San Diego", "san-luis-rey": "San Diego", "pala": "San Diego",
    # Inland Empire
    "sun-city": "Riverside", "cathedral-city": "Riverside", "thousand-palms": "Riverside", "mira-loma": "Riverside",
    "rancho-cucamonga": "San Bernardino", "alta-loma": "San Bernardino", "cucamonga": "San Bernardino",
    "lake-arrowhead": "San Bernardino", "crestline": "San Bernardino", "twentynine-palms": "San Bernardino",
    # Northern / central postal names
    "point-richmond": "Contra Costa", "mission-san-jose": "Alameda", "san-francisco": "San Francisco",
    "carmel": "Monterey", "carmel-by-the-sea": "Monterey", "pebble-beach": "Monterey", "aptos": "Santa Cruz",
    "san-juan-bautista": "San Benito", "cambria": "San Luis Obispo",
    # names DiscoverMass slugs use that are not Census place names (seen in the 2026-10-31 sitemap)
    "palos-verdes": "Los Angeles", "la": "Los Angeles", "ventura": "Ventura", "la-canada": "Los Angeles",
    "la-crescenta": "Los Angeles", "montrose": "Los Angeles", "jolon": "Monterey", "paso-robles": "San Luis Obispo",
    "prather": "Fresno", "pinedale": "Fresno", "raymond": "Madera", "big-sur": "Monterey", "tahoe-city": "Placer",
    "guasti": "San Bernardino", "idyllwild": "Riverside", "newberry-springs": "San Bernardino",
    "santa-ysabel": "San Diego", "olema": "Marin", "lagunitas": "Marin", "challenge": "Yuba", "zamora": "Yolo",
    "loch-lomond": "Lake", "asti": "Sonoma", "elk": "Mendocino", "gualala": "Mendocino", "saint-helena": "Napa",
    "alviso": "Santa Clara", "balboa-island": "Orange", "mc-farland": "Kern", "greenbrae": "Marin",
    "caslistoga": "Napa", "five-points": "Fresno", "buhach": "Merced", "angels-camp": "Calaveras", "hilmar": "Merced",
}

DIOCESE_COUNTIES_PROVISIONAL = {
    # Used only to ORDER the crawl before data/real/california/dioceses.json exists; the assignment step reads that file.
    "los-angeles": ["Los Angeles", "Ventura", "Santa Barbara"],
    "orange": ["Orange"],
    "san-bernardino": ["San Bernardino", "Riverside"],
    "san-diego": ["San Diego", "Imperial"],
    "fresno": ["Fresno", "Inyo", "Kern", "Kings", "Madera", "Mariposa", "Merced", "Tulare"],
    "monterey": ["Monterey", "San Benito", "San Luis Obispo", "Santa Cruz"],
    "san-jose": ["Santa Clara"],
    "san-francisco": ["San Francisco", "San Mateo", "Marin"],
    "oakland": ["Alameda", "Contra Costa"],
    "sacramento": ["Amador", "Butte", "Colusa", "El Dorado", "Glenn", "Lassen", "Modoc", "Nevada", "Placer", "Plumas",
                   "Sacramento", "Shasta", "Sierra", "Siskiyou", "Solano", "Sutter", "Tehama", "Trinity", "Yolo", "Yuba"],
    "stockton": ["Alpine", "Calaveras", "Mono", "San Joaquin", "Stanislaus", "Tuolumne"],
    "santa-rosa": ["Del Norte", "Humboldt", "Lake", "Mendocino", "Napa", "Sonoma"],
}


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


@lru_cache(maxsize=1)
def counties() -> list[tuple[str, str, object]]:
    """[(name, geoid, geometry)] for California's 58 counties."""
    r = shapefile.Reader(str(COUNTY_SHP))
    out = []
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict()
        if rec["STATEFP"] == "06":
            out.append((rec["NAME"], rec["GEOID"], shape(sr.shape.__geo_interface__)))
    return out


@lru_cache(maxsize=1)
def _county_index():
    cs = counties()
    geoms = [g for _, _, g in cs]
    return STRtree(geoms), cs, [prep(g) for g in geoms]


def county_at_full(lat: float | None, lng: float | None) -> tuple[str | None, str | None, str | None]:
    """(county name, county GEOID, how) by point-in-polygon on the 1:500k counties."""
    if lat is None or lng is None:
        return None, None, None
    tree, cs, prepped = _county_index()
    p = Point(lng, lat)
    for i in tree.query(p):
        if prepped[i].contains(p):
            return cs[i][0], cs[i][1], "inside"
    # a coastal point just outside the 1:500k shoreline: nearest county within ~2 km
    i = tree.nearest(p)
    if cs[i][2].distance(p) < 0.02:
        return cs[i][0], cs[i][1], "nearest (within ~2 km of the shoreline)"
    return None, None, None


def county_at(lat: float | None, lng: float | None) -> str | None:
    return county_at_full(lat, lng)[0]


def county_geoid(name: str | None) -> str | None:
    for n, g, _ in counties():
        if n == name:
            return g
    return None


def place_counties() -> dict[str, dict]:
    """{place-slug: {"name", "places": [{"geoid", "lsad", "county", "counties"}], "county", "counties"}}.
    A name used by two places (El Sobrante is a CDP in Contra Costa and in Riverside) keeps both; its "county" is
    then None unless both lie in one county, and "counties" is the union."""
    if CACHE.exists():
        d = json.loads(CACHE.read_text())
        if d and "places" in next(iter(d.values())):
            return d
    cs = counties()
    tree = STRtree([g for _, _, g in cs])
    r = shapefile.Reader(str(PLACE_SHP))
    out: dict[str, dict] = {}
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict()
        g = shape(sr.shape.__geo_interface__)
        shares = []
        for i in tree.query(g):
            a = cs[i][2].intersection(g).area
            if a > 0:
                shares.append((a, cs[i][0]))
        shares.sort(reverse=True)
        if not shares:
            continue
        key = slugify(rec["NAME"])
        e = out.setdefault(key, {"name": rec["NAME"], "places": []})
        e["places"].append({"geoid": rec["GEOID"], "lsad": rec["NAMELSAD"], "county": shares[0][1],
                            "counties": [c for _, c in shares]})
    for e in out.values():
        prim = {p["county"] for p in e["places"]}
        e["county"] = prim.pop() if len(prim) == 1 else None
        e["counties"] = sorted({c for p in e["places"] for c in p["counties"]})
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, indent=0, sort_keys=True))
    return out


def city_county(city: str | None) -> tuple[str | None, str]:
    """(county, how) for a city name or slug. A name two places share in different counties gives (None, ...)."""
    if not city:
        return None, "none"
    k = slugify(city)
    pc = place_counties()
    if k in NEIGHBOURHOODS and k in pc and pc[k]["county"] != NEIGHBOURHOODS[k]:
        return None, f"ambiguous: Census place in {'/'.join(pc[k]['counties'])}, neighbourhood in {NEIGHBOURHOODS[k]}"
    for key in (k, k.replace("saint-", "st-", 1), k.replace("st-", "saint-", 1)):
        if key in pc:
            if pc[key]["county"] is None:
                return None, f"ambiguous Census place name ({' / '.join(pc[key]['counties'])})"
            return pc[key]["county"], "census-place"
    if k in NEIGHBOURHOODS:
        return NEIGHBOURHOODS[k], "neighbourhood-list"
    return None, "unknown"


def provisional_diocese(county: str | None) -> str | None:
    for d, cs in DIOCESE_COUNTIES_PROVISIONAL.items():
        if county in cs:
            return d
    return None


@lru_cache(maxsize=1)
def place_geoms() -> dict[str, list]:
    """{place-slug: [geometry, ...]} for CA Census places (every place that uses the name)."""
    r = shapefile.Reader(str(PLACE_SHP))
    out: dict[str, list] = {}
    for sr in r.iterShapeRecords():
        out.setdefault(slugify(sr.record.as_dict()["NAME"]), []).append(shape(sr.shape.__geo_interface__))
    return out


def km_from_place(lat: float, lng: float, city: str | None) -> float | None:
    """Rough distance in km from a point to the nearest Census place of that name (0 inside); None if not a place."""
    import math
    gs = place_geoms().get(slugify(city or ""))
    if not gs:
        return None
    p = Point(lng, lat)
    best = None
    for g in gs:
        if g.contains(p):
            return 0.0
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        for gg in polys:
            q = gg.exterior.interpolate(gg.exterior.project(p))
            dy = (q.y - lat) * 111.0
            dx = (q.x - lng) * 111.0 * math.cos(math.radians(lat))
            d = math.hypot(dx, dy)
            best = d if best is None or d < best else best
    return round(best, 1)


def check_latlng(lat: float | None, lng: float | None, city: str | None) -> str | None:
    """Why the page's coordinates look wrong for its own address city, or None when they look fine."""
    if lat is None or city is None:
        return None
    c_ll = county_at(lat, lng)
    k = slugify(city)
    pc = place_counties().get(k)
    if c_ll and NEIGHBOURHOODS.get(k) == c_ll:
        return None  # "Mission Hills": a CDP in Santa Barbara County and a Los Angeles neighbourhood
    if pc:
        d = km_from_place(lat, lng, city)
        if c_ll not in pc["counties"] and (d is None or d > 2):
            return f"coordinates fall in {c_ll or 'no'} County, {d} km from {city} ({'/'.join(pc['counties'])} County)"
        if d is not None and d > 15:
            return f"coordinates are {d} km outside {city}"
        return None
    nb = NEIGHBOURHOODS.get(k)
    if nb and c_ll and c_ll != nb:
        return f"coordinates fall in {c_ll} County; {city} is in {nb} County"
    return None
