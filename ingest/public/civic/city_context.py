#!/usr/bin/env python3
"""City context for each parish: the city or unincorporated community it sits in, the council district
(Oakland and Berkeley, whose district maps are on their open-data portals), city-level ACS figures, and
homelessness Point-in-Time (PIT) counts for its jurisdiction.

Sources:
  places          Census 2024 TIGER/Line places (full resolution) - point-in-polygon on the church point
  council (OAK)   City of Oakland open data, "Council District" (Socrata ynuh-geyh), 2022 district lines
  council (BRK)   City of Berkeley open data, "CouncilDistricts" (Socrata c8zs-8y7x)
  PIT Alameda     Alameda County Health, Housing & Homelessness Services:
                    2024 PIT Count Report (Table 4 / Figure 5) and "2026 Point-in-Time Count: Initial Results"
                    (by-city table for 2022, 2024, 2026); both linked from
                    https://health.alamedacountyca.gov/data-and-reports/point-in-time-count/
  PIT Contra Costa  Contra Costa Health H3, "2025 Homeless Point-in-Time Count" infographic (June 2025), attached
                    to Board of Supervisors item 25-2790 (Legistar). It gives the countywide total and raw counts of
                    unsheltered people observed by city for 2024 and 2025. cchealth.org itself refuses this
                    environment (HTTP 403), so the full report was not read.
Outputs: data/real/civic/pit_counts.json, data/real/civic/parish_city_context.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import KX, KY, OUT, RAW, SESSION, download, load_json, load_parishes, now_iso, save_json  # noqa: E402

DIST = RAW / "districts"
COUNCIL = {
    "Oakland": {"url": "https://data.oaklandca.gov/resource/ynuh-geyh.geojson", "page": "https://data.oaklandca.gov/d/ynuh-geyh",
                "field": "fullname"},
    "Berkeley": {"url": "https://data.cityofberkeley.info/resource/c8zs-8y7x.geojson", "page": "https://data.cityofberkeley.info/d/c8zs-8y7x",
                 "field": "district"},
}
AC_PIT_PAGE = "https://health.alamedacountyca.gov/data-and-reports/point-in-time-count/"
AC_2024_REPORT = "https://weblink.alamedacountyca.gov/ACHHS/DocView.aspx?id=1008&dbid=0&repo=ACH-DocumentLibrary"
AC_2026_INITIAL = "https://weblink.alamedacountyca.gov/ACHHS/DocView.aspx?id=484&dbid=0&repo=ACH-DocumentLibrary"
CC_2025_INFO = "https://contra-costa.legistar1.com/contra-costa/attachments/3a5431e7-5a78-459a-b2a0-e72a84b6afa3.pdf"
CC_MATTER = "https://contra-costa.legistar.com/ (Board of Supervisors file 25-2790, 'Update: 2025 Point-In-Time Count')"

# Alameda County PIT, people experiencing homelessness (sheltered + unsheltered) by jurisdiction.
# 2022 and 2024 and 2026 from the 2026 Initial Results by-city table; 2024 also matches the 2024 report's Table 4
# (eight largest jurisdictions) and Figure 5 (the rest). Countywide: 9,747 (2022), 9,450 (2024), 8,201 (2026).
AC_PIT = {
    "Albany": (23, 90, 61), "Berkeley": (1057, 844, 880), "Alameda": (264, 455, 242), "Dublin": (29, 25, 40),
    "Emeryville": (91, 38, 34), "Fremont": (1026, 807, 852), "Hayward": (381, 512, 404), "Livermore": (242, 277, 343),
    "Newark": (58, 76, 71), "Oakland": (5055, 5485, 4410), "Piedmont": (42, 3, 6), "Pleasanton": (72, 70, 96),
    "San Leandro": (409, 284, 296), "Unincorporated Alameda County": (509, 294, 325), "Union City": (489, 191, 141),
}
AC_TOTAL = {"2022": 9747, "2024": 9450, "2026": 8201}
AC_UNSHELTERED = {"2024": 6343, "2026": 5202}
# Contra Costa: raw counts of unsheltered people observed, by community, 2024 and 2025 (infographic tables).
CC_UNSHELTERED_OBS = {
    "West County": {"Crockett": (21, 3), "El Cerrito": (11, 21), "North Richmond": (5, 16), "Pinole": (7, 12),
                    "Richmond": (388, 208), "Rodeo": (29, 17), "San Pablo": (35, 13), "_total": (502, 297)},
    "Central County": {"Concord": (173, 131), "Martinez": (158, 63), "Pacheco": (28, 10), "Pleasant Hill": (42, 32),
                       "San Ramon": (10, 8), "Walnut Creek": (64, 50), "_total": (485, 321)},
    "East County": {"Antioch": (413, 246), "Bay Point": (51, 31), "Brentwood": (30, 5), "Oakley": (35, 25),
                    "Pittsburg": (110, 73), "_total": (646, 388)},
}
CC_TOTAL_2025 = 2118   # sheltered + unsheltered, "26% decrease from 2024"


def council_layers():
    layers = {}
    DIST.mkdir(parents=True, exist_ok=True)
    for city, c in COUNCIL.items():
        p = DIST / f"{city.lower()}_council.geojson"
        if not p.exists():
            r = SESSION.get(c["url"], timeout=60)
            r.raise_for_status()
            p.write_text(r.text, encoding="utf-8")
        fc = json.loads(p.read_text(encoding="utf-8"))
        layers[city] = [(f["properties"].get(c["field"]), shape(f["geometry"])) for f in fc["features"]]
    return layers


TIGER_PLACE = "https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_06_place.zip"


def tiger_places():
    """Full-resolution 2024 TIGER/Line places (the cartographic files are generalised and can misplace border points)."""
    import shapefile
    import geometry
    z = download(TIGER_PLACE, RAW / "tiger" / "tl_2024_06_place.zip")
    d = geometry.unzip(z)
    sf = shapefile.Reader(str(next(d.rglob("*.shp")).with_suffix("")))
    fields = [f[0] for f in sf.fields[1:]]
    out = []
    for sr in sf.iterShapeRecords():
        r = dict(zip(fields, sr.record))
        kind = {"25": "city", "43": "town", "57": "CDP"}.get(r["LSAD"], r["LSAD"])
        out.append(({"GEOID": r["GEOID"], "NAME": r["NAME"], "KIND": kind}, shape(sr.shape.__geo_interface__)))
    return out


def main() -> None:
    pl = tiger_places()
    counties = [(f["properties"]["NAME"], shape(f["geometry"])) for f in json.loads((RAW / "counties_full.geojson").read_text())["features"]]
    areas = {a["geoid"]: a for a in load_json(OUT / "acs_areas.json")["areas"]}
    council = council_layers()

    def acs_city(geoid):
        a = areas.get(geoid)
        if not a:
            return None
        v = a["values"]
        def sh(n, d):
            return round(v[n][0] / v[d][0], 4) if v.get(n) and v.get(d) and v[d][0] else None
        return {"pop": v["pop"][0], "median_hh_income": v["median_hh_income"], "poverty_rate": sh("pov_below100", "pov_universe"),
                "share_foreign_born": sh("foreign_born", "nativity_universe"), "share_english_only": sh("lang_english_only", "lang_universe"),
                "share_lep_households": sh("lehh_total", "hh_lang_universe"), "share_renter": sh("renter_occ", "occ_units"),
                "share_65plus": sh("age_65plus", "pop"), "share_under18": sh("age_under18", "pop")}

    out = []
    for p in load_parishes(include_extra=True):
        pt = Point(p["lng"], p["lat"])
        county = next((n for n, g in counties if g.contains(pt)), None)
        place = next(((pr, g) for pr, g in pl if g.contains(pt)), None)
        pr = place[0] if place else None
        edge_m = None
        if place:
            # metres from the church point to the place boundary (local projection), to flag border cases
            b = place[1].boundary
            edge_m = round(1000 * min(((pt.x - q[0]) * KX) ** 2 + ((pt.y - q[1]) * KY) ** 2 for q in
                                      (b.interpolate(b.project(pt)).coords[0],)) ** 0.5)
        rec = {"id": p["id"], "name": p["name"], "mailing_city": p["city"], "county": county,
               "place": None if not pr else {"geoid": pr["GEOID"], "name": pr["NAME"], "kind": pr["KIND"],
                                             "incorporated": pr["KIND"] in ("city", "town"),
                                             "meters_to_place_boundary": edge_m,
                                             **({"border_note": "Church point is within 150 m of a city/CDP line; check jurisdiction."}
                                                if edge_m is not None and edge_m < 150 else {})},
               "government": None, "council_district": None, "pit": None, "city_acs": None}
        if pr and pr["KIND"] in ("city", "town"):
            rec["government"] = f"City of {pr['NAME']}" if pr["KIND"] == "city" else f"Town of {pr['NAME']}"
        else:
            rec["government"] = f"Unincorporated {county} County" + (f" ({pr['NAME']} CDP)" if pr else "")
        if pr and pr["NAME"] in council:
            d = next((n for n, g in council[pr["NAME"]] if g.contains(pt)), None)
            rec["council_district"] = {"district": d, "source_url": COUNCIL[pr["NAME"]]["page"]}
        if pr:
            rec["city_acs"] = acs_city(pr["GEOID"])
        # PIT jurisdiction
        if county == "Alameda":
            j = pr["NAME"] if pr and pr["KIND"] == "city" and pr["NAME"] in AC_PIT else "Unincorporated Alameda County"
            c22, c24, c26 = AC_PIT[j]
            rec["pit"] = {"jurisdiction": j, "people_2022": c22, "people_2024": c24, "people_2026_initial": c26,
                          "measure": "people experiencing homelessness, sheltered + unsheltered", "source_url": AC_PIT_PAGE}
        elif county == "Contra Costa":
            nm = pr["NAME"] if pr else None
            hit = None
            for region, rows in CC_UNSHELTERED_OBS.items():
                if nm in rows:
                    hit = (region, nm, rows[nm])
            if hit:
                rec["pit"] = {"jurisdiction": hit[1], "region": hit[0], "unsheltered_observed_2024": hit[2][0],
                              "unsheltered_observed_2025": hit[2][1],
                              "measure": "raw count of unsheltered people observed (Contra Costa infographic)", "source_url": CC_2025_INFO}
            else:
                region = None
                rec["pit"] = {"jurisdiction": nm, "unsheltered_observed_2025": None,
                              "note": "Not listed in the 2025 infographic (fewer than five unsheltered people observed, or not broken out).",
                              "source_url": CC_2025_INFO}
        out.append(rec)

    pit = {
        "meta": {"generated_at": now_iso(),
                 "caution": ("PIT counts are one-night snapshots (late January) with method changes between years; they "
                             "undercount people doubled-up or hidden. Use for scale and trend, not precision.")},
        "alameda_county": {
            "source_page": AC_PIT_PAGE, "report_2024": AC_2024_REPORT, "initial_results_2026": AC_2026_INITIAL,
            "count_dates": {"2024": "2024-01-25", "2026": "2026-01-22"},
            "total_people": AC_TOTAL, "unsheltered_people": AC_UNSHELTERED,
            "by_jurisdiction": {j: {"2022": v[0], "2024": v[1], "2026_initial": v[2]} for j, v in AC_PIT.items()},
            "check": {"sum_2024": sum(v[1] for v in AC_PIT.values()), "sum_2026": sum(v[2] for v in AC_PIT.values())},
        },
        "contra_costa_county": {
            "source": CC_2025_INFO, "board_item": CC_MATTER, "total_people_2025": CC_TOTAL_2025,
            "change_vs_2024": "-26% (as printed)", "share_unsheltered_2025": 0.60,
            "unsheltered_observed_by_community": {r: {k: {"2024": v[0], "2025": v[1]} for k, v in rows.items()}
                                                  for r, rows in CC_UNSHELTERED_OBS.items()},
            "note": "Contra Costa counts every year; city figures are raw observations of unsheltered people, not totals.",
            "gap": "cchealth.org returns HTTP 403 to this environment, so the 2025/2026 full reports were not read.",
        },
    }
    save_json(OUT / "pit_counts.json", pit)
    save_json(OUT / "parish_city_context.json", {
        "meta": {"generated_at": now_iso(),
                 "sources": {"places": TIGER_PLACE, "council": {k: v["page"] for k, v in COUNCIL.items()},
                             "pit": "data/real/civic/pit_counts.json", "city_acs": "data/real/civic/acs_areas.json"},
                 "council_gap": ("Council districts are attached for Oakland and Berkeley only. Most other cities in the "
                                 "diocese now elect by district too, but their maps were not collected here."),
                 "note": "Place = the Census place containing the church point; mailing city can differ (e.g. Castro Valley CDP)."},
        "parishes": out})
    print("parishes:", len(out), "with council district:", sum(1 for r in out if r["council_district"]))
    print("AC check:", pit["alameda_county"]["check"])


if __name__ == "__main__":
    main()
