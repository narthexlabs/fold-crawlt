#!/usr/bin/env python3
"""Neighbours and potential partners near each parish: hospitals, nursing homes, detention facilities,
colleges/universities, public schools and food-distribution sites.

Sources (each record keeps source + source_url; fetched_at is in the file meta):
  hospitals       CDPH Licensed and Certified Healthcare Facility Listing (data.chhs.ca.gov), types GACH
                  (general acute care), APH (acute psychiatric), CDRH (chemical dependency recovery); active licences
  nursing homes   CMS Care Compare, Nursing Homes "Provider Information" (data.cms.gov provider-data 4pq5-n9py)
  detention       The diocesan Detention Ministry's list of the 10 facilities it serves (oakdiocese.org/detention-ministry,
                  population/capacity as printed), geocoded with the Census geocoder (OpenStreetMap as fallback)
  colleges        NCES IPEDS Institutional Characteristics HD2024 (active institutions, counties 06001/06013)
  public schools  California Department of Education public schools directory (pubschls, active schools)
  food            Food Bank of Contra Costa & Solano "Food by City" map (all sites, with coordinates);
                  Alameda County Community Food Bank's FoodNow locator: location names from its public sitemap
                  (addresses are rendered client-side and were not collected)

Outputs (data/real/civic/): neighbors.json (all facilities, compact) and parish_neighbors.json
Usage: python ingest/public/civic/neighbors.py [--refresh]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from bs4 import BeautifulSoup
from shapely.geometry import Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import (MILE_KM, OUT, RAW, SESSION, download, haversine_km, load_json, load_parishes,  # noqa: E402
                    now_iso, save_json)

NB = RAW / "neighbors"
CDPH_PKG = "https://data.chhs.ca.gov/api/3/action/package_show?id=healthcare-facility-locations"
CDPH_PAGE = "https://data.chhs.ca.gov/dataset/healthcare-facility-locations"
CMS_NH_META = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/4pq5-n9py"
CMS_NH_PAGE = "https://data.cms.gov/provider-data/dataset/4pq5-n9py"
IPEDS_HD = "https://nces.ed.gov/ipeds/datacenter/data/HD2024.zip"
IPEDS_IC = "https://nces.ed.gov/ipeds/datacenter/data/IC2024.zip"   # RELAFFIL (30 = Roman Catholic)
CDE_SCHOOLS = "https://www.cde.ca.gov/schooldirectory/report?rid=dl1&tp=txt"
CDE_PAGE = "https://www.cde.ca.gov/ds/si/ds/pubschls.asp"
FBCCS_PAGE = "https://www.foodbankccs.org/find-food/foodbycity/"
FOODNOW_SITEMAP = "https://cms.foodnow.net/location-sitemap.xml"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

# Detention facilities served by the diocesan Detention Ministry, as listed on its page (modified 2025-09-29):
# name, address, population/capacity as printed, county, kind, and the OSM object used if the Census geocoder fails.
DETENTION_PAGE = "https://www.oakdiocese.org/detention-ministry/"
DETENTION = [
    ("Santa Rita Jail", "5325 Broder Blvd., Dublin, CA", "2800/3800", "Alameda", "county jail", ("way", 475559875)),
    ("Glenn E. Dyer Detention Facility", "550 6th St., Oakland, CA", "400/830", "Alameda", "county jail", ("way", 582034702)),
    ("Federal Correctional Institution (FCI) Dublin", "5701 8th St., Dublin, CA", "400/1200", "Alameda", "federal prison", ("way", 475559870)),
    ("Alameda County Juvenile Hall", "2500 Fairmont Dr., San Leandro, CA", "300/360", "Alameda", "juvenile hall", ("way", 317849011)),
    ("Camp Sweeney", "2600 Fairmont Dr., San Leandro, CA", "?/50", "Alameda", "juvenile camp", None),
    ("John A. Davis Juvenile Hall", "202 Glacier Dr., Martinez, CA", "180/290", "Contra Costa", "juvenile hall", ("node", 4959226314)),
    ("Martinez Detention Facility", "1000 Ward St., Martinez, CA", "750/1500", "Contra Costa", "county jail", ("way", 506147376)),
    ("Marsh Creek Detention Facility", "12000 Marsh Creek Rd., Clayton, CA", "150/250", "Contra Costa", "county jail", ("node", 6319964959)),
    ("West County Detention Facility", "5555 Giant Highway, Richmond, CA", "890/1100", "Contra Costa", "county jail", ("way", 943603599)),
    ("Orin Allen Youth Rehabilitation Facility", "4491 Bixler Rd., Byron, CA", "40/100", "Contra Costa", "juvenile facility", None),
]
DETENTION_STATUS = {
    "Federal Correctional Institution (FCI) Dublin": "Listed by the diocese (page dated 2025-09-29), but its population was moved out in 2024; verify before treating as active.",
}


def diocese_shape():
    cf = load_json(RAW / "counties_full.geojson")
    return unary_union([shape(f["geometry"]) for f in cf["features"]])


def inside(dio, lat, lng):
    try:
        return dio.contains(Point(float(lng), float(lat)))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- sources
def hospitals(refresh):
    import pandas as pd
    pkg = SESSION.get(CDPH_PKG, timeout=60).json()["result"]
    url = next(r["url"] for r in pkg["resources"] if r["format"] == "CSV" and "Locations" in r["name"])
    p = download(url, NB / "health_facility_locations.csv", refresh=refresh)
    d = pd.read_csv(p, dtype=str)
    d = d[d["COUNTY_NAME"].str.upper().isin(["ALAMEDA", "CONTRA COSTA"])]
    out = []
    for _, r in d.iterrows():
        if r["FAC_TYPE_CODE"] not in ("GACH", "APH", "CDRH") or r.get("LICENSE_STATUS_DESCRIPTION") != "ACTIVE":
            continue
        out.append({"id": f"cdph-{r['FACID']}", "type": "hospital", "subtype": r["FAC_FDR"].title(), "name": r["FACNAME"].title(),
                    "address": f"{r['ADDRESS']}, {r['CITY']}".title(), "beds": int(float(r["CAPACITY"])) if r["CAPACITY"] == r["CAPACITY"] else None,
                    "ccn": r.get("CCN") if r.get("CCN") == r.get("CCN") else None, "lat": float(r["LATITUDE"]), "lng": float(r["LONGITUDE"]),
                    "source": "CDPH Licensed and Certified Healthcare Facility Listing", "source_url": CDPH_PAGE})
    # hospice facilities and congregate living also matter for pastoral care; keep counts only in meta
    return out, {"cdph_csv_url": url, "cdph_modified": pkg.get("metadata_modified")}


def nursing_homes(refresh):
    import pandas as pd
    meta = SESSION.get(CMS_NH_META, timeout=60).json()
    url = meta["distribution"][0]["downloadURL"]
    p = download(url, NB / "NH_ProviderInfo.csv", refresh=refresh)
    d = pd.read_csv(p, dtype=str)
    d = d[(d["State"] == "CA") & (d["County/Parish"].str.upper().isin(["ALAMEDA", "CONTRA COSTA"]))]
    out = []
    for _, r in d.iterrows():
        def num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        out.append({"id": f"cms-{r['CMS Certification Number (CCN)']}", "type": "nursing_home", "name": r["Provider Name"].title(),
                    "address": f"{r['Provider Address']}, {r['City/Town']}".title(), "ccn": r["CMS Certification Number (CCN)"],
                    "certified_beds": num(r["Number of Certified Beds"]), "avg_residents_per_day": num(r["Average Number of Residents per Day"]),
                    "ownership": r["Ownership Type"], "provider_type": r["Provider Type"],
                    "lat": num(r["Latitude"]), "lng": num(r["Longitude"]),
                    "source": "CMS Care Compare - Nursing Homes Provider Information", "source_url": CMS_NH_PAGE})
    return out, {"cms_nh_csv_url": url, "cms_nh_modified": meta.get("modified")}


def detention():
    """The diocesan Detention Ministry's facility list, geocoded (Census geocoder; OSM object as fallback)."""
    from urllib.parse import quote_plus
    out = []
    for name, addr, popcap, county, kind, osm in DETENTION:
        url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address=" + quote_plus(addr)
               + "&benchmark=Public_AR_Current&format=json")
        lat = lng = None
        geo = None
        try:
            m = SESSION.get(url, timeout=30).json()["result"]["addressMatches"]
            if m:
                lat, lng, geo = m[0]["coordinates"]["y"], m[0]["coordinates"]["x"], "census_geocoder"
        except Exception:
            pass
        if lat is None and osm:
            time.sleep(1.5)
            r = SESSION.get("https://nominatim.openstreetmap.org/lookup",
                            params={"osm_ids": f"{osm[0][0].upper()}{osm[1]}", "format": "jsonv2"}, timeout=30).json()
            if r:
                lat, lng, geo = float(r[0]["lat"]), float(r[0]["lon"]), f"openstreetmap {osm[0]}/{osm[1]}"
        pop, cap = (popcap.split("/") + [None])[:2]
        out.append({"id": "det-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "type": "detention", "subtype": kind,
                    "name": name, "county": county, "address": addr,
                    "population_as_listed": None if pop in ("?", "") else int(pop), "capacity_as_listed": int(cap) if cap else None,
                    "lat": lat, "lng": lng, "geo_source": geo,
                    "status_note": DETENTION_STATUS.get(name, "Listed by the diocesan Detention Ministry (page dated 2025-09-29)."),
                    "source": "Diocese of Oakland, Detention Ministry page", "source_url": DETENTION_PAGE})
    return out


def _ipeds_csv(path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(name)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1").lstrip("\ufeff\xef\xbb\xbf")


def colleges(refresh):
    txt = _ipeds_csv(download(IPEDS_HD, NB / "HD2024.zip", refresh=refresh))
    rel = {r["UNITID"]: r.get("RELAFFIL") for r in csv.DictReader(io.StringIO(_ipeds_csv(download(IPEDS_IC, NB / "IC2024.zip", refresh=refresh))))}
    out = []
    sector = {"1": "Public, 4-year+", "2": "Private nonprofit, 4-year+", "3": "Private for-profit, 4-year+", "4": "Public, 2-year",
              "5": "Private nonprofit, 2-year", "6": "Private for-profit, 2-year", "7": "Public, <2-year",
              "8": "Private nonprofit, <2-year", "9": "Private for-profit, <2-year", "0": "Administrative unit"}
    size = {"1": "Under 1,000", "2": "1,000-4,999", "3": "5,000-9,999", "4": "10,000-19,999", "5": "20,000 and above",
            "-1": "Not reported", "-2": "Not applicable"}
    for r in csv.DictReader(io.StringIO(txt)):
        if r.get("COUNTYCD") not in ("6001", "6013") or r.get("CYACTIVE") != "1" or r.get("SECTOR") == "0":
            continue
        out.append({"id": f"ipeds-{r['UNITID']}", "type": "college", "name": r["INSTNM"], "address": f"{r['ADDR']}, {r['CITY']}",
                    "sector": sector.get(r["SECTOR"], r["SECTOR"]), "size": size.get(r.get("INSTSIZE"), r.get("INSTSIZE")),
                    "catholic": rel.get(r["UNITID"]) == "30", "website": r.get("WEBADDR"),
                    "lat": float(r["LATITUDE"]), "lng": float(r["LONGITUD"]),
                    "source": "NCES IPEDS HD2024 (+ IC2024 religious affiliation)", "source_url": IPEDS_HD})
    return out


def public_schools(refresh):
    p = NB / "pubschls.txt"
    if refresh or not p.exists():
        r = SESSION.get(CDE_SCHOOLS, timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
    out = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["County"] not in ("Alameda", "Contra Costa") or r["StatusType"] != "Active" or r["School"] in ("No Data", ""):
                continue
            try:
                lat, lng = float(r["Latitude"]), float(r["Longitude"])
            except ValueError:
                continue
            out.append({"id": f"cde-{r['CDSCode']}", "type": "public_school", "name": r["School"], "district": r["District"],
                        "address": f"{r['Street']}, {r['City']}", "school_type": r["SOCType"], "grades": r["GSserved"],
                        "charter": r["Charter"] == "Y", "virtual": r.get("Virtual"), "lat": lat, "lng": lng,
                        "source": "CDE public schools directory", "source_url": CDE_PAGE})
    return out


def fbccs(refresh, dio):
    p = NB / "fbccs_foodbycity.html"
    if refresh or not p.exists():
        r = SESSION.get(FBCCS_PAGE, timeout=120)
        r.raise_for_status()
        p.write_text(r.text, encoding="utf-8")
    soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
    out = []
    for loc in soup.select("div.mpfy-mll-location"):
        card = loc.select_one("div.mpfy-card")
        if not card:
            continue
        name = card.find("h2").get_text(" ", strip=True)
        prog = card.select_one("h3.mpfy-card-program-name")
        prog = prog.get_text(" ", strip=True) if prog else None
        link = card.select_one("a.mpfy-card-address-link")
        addr_el = link.find_previous("span") if link else None
        addr = addr_el.get_text(", ", strip=True) if addr_el else None
        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", link["href"]) if link else None
        if not m:
            continue
        lat, lng = float(m.group(1)), float(m.group(2))
        if not inside(dio, lat, lng):
            continue  # Solano County sites
        hours = None
        for h in card.find_all("h3"):
            if h.get_text(strip=True) == "Open Hours":
                hours = h.find_next("span").get_text(" ", strip=True)
        out.append({"id": f"fbccs-{loc.get('data-id')}", "type": "food_site", "name": name, "program": prog, "address": addr,
                    "hours": hours, "lat": lat, "lng": lng, "network": "Food Bank of Contra Costa & Solano",
                    "source": "Food Bank of Contra Costa & Solano, Food by City map", "source_url": FBCCS_PAGE})
    return out


def foodnow_names(refresh):
    p = NB / "foodnow_location_sitemap.xml"
    if refresh or not p.exists():
        r = SESSION.get(FOODNOW_SITEMAP, timeout=60)
        r.raise_for_status()
        p.write_text(r.text, encoding="utf-8")
    root = ET.fromstring(p.read_text(encoding="utf-8"))
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    out = []
    for u in root.findall("s:url", ns):
        loc = u.find("s:loc", ns).text
        slug = loc.rstrip("/").rsplit("/", 1)[-1]
        out.append({"slug": slug, "url": loc.replace("cms.foodnow.net", "foodnow.net")})
    return out


# ---------------------------------------------------------------- matching helpers
# FoodNow (ACCFB) location slugs that are parish sites, matched by hand from the sitemap (2026-10-26).
FOODNOW_PARISH = {
    "st-bernard-catholic-church": "bernard-oakland",
    "svdp-st-columba": "columba-oakland",
    "svdp-mission-san-jose-fremont": "joseph-fremont",
    "st-michael-catholic-church": "michael-livermore",
    "st-lawrence-otoole": "divine-mercy-oakland",   # St. Lawrence O'Toole is one of Divine Mercy Parish's two churches
}
FOODNOW_CATHOLIC_OTHER = {
    "svdp-dining-room": "Society of St. Vincent de Paul of Alameda County dining room (not a parish)",
    "oakland-catholic-worker": "Oakland Catholic Worker (lay movement; not a parish)",
    "st-vincent-de-paul-livermore": "St. Vincent de Paul, Livermore (conference; parish not stated)",
}
STOPW = {"st", "saint", "saints", "sts", "parish", "church", "catholic", "of", "the", "and", "community", "cathedral",
         "our", "lady", "s", "christ", "mission"}
SAINT = re.compile(r"\b(st|saint|sts|santa|san)\b\.?", re.I)


def norm(s):
    s = (s or "").lower().replace("&", " and ")
    s = SAINT.sub(" st ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_words(name):
    """Distinctive words of a name (saint/church/of etc. removed)."""
    return {w for w in norm(name).split() if w not in STOPW and len(w) > 1}


def parish_tokens(p):
    """Distinctive name phrases for a parish, e.g. 'st columba', 'queen of all saints'."""
    n = norm(p["name"])
    n = re.sub(r"\b(parish|catholic|church|community|the|cathedral|of)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    NB.mkdir(parents=True, exist_ok=True)
    fetched = now_iso()
    dio = diocese_shape()

    hosp, hmeta = hospitals(args.refresh)
    nh, nmeta = nursing_homes(args.refresh)
    det = detention()
    col = colleges(args.refresh)
    sch = public_schools(args.refresh)
    food = fbccs(args.refresh, dio)
    fn = foodnow_names(args.refresh)
    print("hospitals", len(hosp), "nursing", len(nh), "detention", len(det), "colleges", len(col), "schools", len(sch),
          "fbccs", len(food), "foodnow", len(fn))

    parishes = load_parishes(include_extra=True)
    plat = np.array([p["lat"] for p in parishes]); plng = np.array([p["lng"] for p in parishes])
    facilities = hosp + nh + det + col + sch + food
    for f in facilities:
        if f.get("lat") is None:
            continue
        d = haversine_km(f["lat"], f["lng"], plat, plng) / MILE_KM
        j = int(np.argmin(d))
        f["nearest_parish"] = {"id": parishes[j]["id"], "miles": round(float(d[j]), 2)}

    # parish partner pantries.
    # FBCCS: a site counts as the parish's own when (a) every distinctive word of the parish name appears in the
    # site name and the site is in the parish's city, or (b) it is within 150 m of the church and its name marks it
    # as Catholic (St. Vincent de Paul / SVdP / Catholic / the parish name). Other sites within 150 m are listed
    # separately as next-door neighbours, not partners.
    partners, adjacent = [], []
    for p in parishes:
        pw = name_words(p["name"])
        for f in food:
            dkm = float(haversine_km(p["lat"], p["lng"], f["lat"], f["lng"]))
            fw = name_words(f["name"])
            in_city = p["city"].lower() in (f.get("address") or "").lower()
            named = bool(pw) and pw <= fw and in_city and dkm <= 1.0
            catholic_marked = bool(re.search(r"svdp|vincent de paul|catholic", f["name"], re.I)) or (bool(pw) and pw <= fw)
            if named or (dkm <= 0.15 and catholic_marked):
                partners.append({"parish_id": p["id"], "network": f["network"], "site": f["name"], "program": f.get("program"),
                                 "hours": f.get("hours"), "address": f.get("address"),
                                 "match": "parish name + city" if named else "same location (<150 m), Catholic-marked name",
                                 "meters_from_church": round(dkm * 1000), "source_url": f["source_url"]})
            elif dkm <= 0.15:
                adjacent.append({"parish_id": p["id"], "site": f["name"], "program": f.get("program"),
                                 "meters_from_church": round(dkm * 1000), "source_url": f["source_url"]})
    # ACCFB FoodNow: location slugs (no addresses), matched by hand; -2/-3/-4 suffixes are extra schedule entries
    fn_bases = sorted({re.sub(r"-\d+$", "", x["slug"]) for x in fn})
    for base in fn_bases:
        if base in FOODNOW_PARISH:
            partners.append({"parish_id": FOODNOW_PARISH[base], "network": "Alameda County Community Food Bank (FoodNow)",
                             "site": base.replace("-", " ").title(), "match": "FoodNow location name (hand-matched; no address published)",
                             "source_url": f"https://www.foodnow.net/location/{base}/"})
    # generic Catholic-affiliated pantry names (St. Vincent de Paul etc.) for the record
    catholic_like = [f for f in food if re.search(r"vincent de paul|svdp|catholic", f["name"], re.I)]
    fn_catholic = [b for b in fn_bases if b in FOODNOW_PARISH or b in FOODNOW_CATHOLIC_OTHER]

    # per-parish summaries
    by_type = {}
    for f in facilities:
        by_type.setdefault(f["type"], []).append(f)
    pn = []
    for j, p in enumerate(parishes):
        rec = {"id": p["id"], "name": p["name"], "city": p["city"], "in_2026_directory": p["in_2026_directory"]}
        for t, items in by_type.items():
            lat = np.array([f["lat"] for f in items]); lng = np.array([f["lng"] for f in items])
            d = haversine_km(p["lat"], p["lng"], lat, lng) / MILE_KM
            order = np.argsort(d)
            within1 = [items[i] for i in order if d[i] <= 1.0]
            within2 = [items[i] for i in order if d[i] <= 2.0]
            nearest = items[int(order[0])]
            entry = {"within_1mi": len(within1), "within_2mi": len(within2),
                     "nearest": {"id": nearest["id"], "name": nearest["name"], "miles": round(float(d[order[0]]), 2)}}
            if t in ("hospital", "nursing_home", "detention", "college", "food_site"):
                lst = []
                for i in order:
                    if d[i] > 2.0 or len(lst) >= 15:
                        break
                    ff = items[i]
                    row = {"id": ff["id"], "name": ff["name"], "miles": round(float(d[i]), 2)}
                    if t == "nursing_home":
                        row["avg_residents_per_day"] = ff.get("avg_residents_per_day")
                    if t == "hospital":
                        row["beds"] = ff.get("beds")
                    if t == "food_site":
                        row["program"] = ff.get("program")
                    lst.append(row)
                entry["list_2mi"] = lst
            if t == "nursing_home":
                entry["residents_per_day_2mi"] = round(sum((f["avg_residents_per_day"] or 0) for f in within2))
            if t == "public_school":
                entry["by_type_2mi"] = {}
                for f in within2:
                    entry["by_type_2mi"][f["school_type"]] = entry["by_type_2mi"].get(f["school_type"], 0) + 1
            rec[t] = entry
        rec["partner_food_sites"] = [x for x in partners if x["parish_id"] == p["id"]]
        rec["food_sites_next_door"] = [x for x in adjacent if x["parish_id"] == p["id"]]
        pn.append(rec)

    meta = {"fetched_at": fetched, **hmeta, **nmeta,
            "sources": {"hospitals": CDPH_PAGE, "nursing_homes": CMS_NH_PAGE, "detention": DETENTION_PAGE,
                        "colleges": IPEDS_HD, "public_schools": CDE_PAGE, "food_contra_costa": FBCCS_PAGE,
                        "food_alameda_names": FOODNOW_SITEMAP},
            "counts": {k: len(v) for k, v in by_type.items()},
            "gaps": ["Alameda County Community Food Bank's FoodNow map loads addresses in the browser, so only location names "
                     "(from its public sitemap) are used; pantries near Alameda County parishes are not mapped. Ask ACCFB for a "
                     "partner-agency list to close this gap.",
                     "Detention facilities are the diocese's own list (Sept 2025); FCI Dublin's status needs checking."],
            "distance": "straight-line miles from the parish church"}
    save_json(OUT / "neighbors.json", {"meta": meta, "facilities": facilities,
                                       "foodnow_location_names": [x["slug"] for x in fn]}, compact=True)
    save_json(OUT / "parish_neighbors.json", {"meta": meta, "parishes": pn,
                                              "partner_food_sites": partners,
                                              "catholic_named_food_sites_contra_costa": [{"name": f["name"], "program": f.get("program"), "address": f.get("address"),
                                                                                         "nearest_parish": f.get("nearest_parish")} for f in catholic_like],
                                              "catholic_foodnow_locations": [{"slug": b, "parish_id": FOODNOW_PARISH.get(b),
                                                                              "note": FOODNOW_CATHOLIC_OTHER.get(b)} for b in fn_catholic]}, compact=True)
    print("partner matches:", len(partners))


if __name__ == "__main__":
    main()
