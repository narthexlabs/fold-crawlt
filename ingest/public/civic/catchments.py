#!/usr/bin/env python3
"""Per-parish neighbourhood profiles from ACS tract data, by two documented methods.

Real parish boundaries for the Diocese of Oakland are not public, so both methods are stand-ins:

(a) radius_2mi  - everyone living within 2 miles (straight line) of the parish church.
(b) voronoi     - everyone for whom this is the nearest parish church (straight line), i.e. a Voronoi /
                  nearest-facility partition of Alameda + Contra Costa counties. Every resident is counted
                  exactly once across parishes.

Both apportion tract-level ACS estimates with 2020 census-block populations: a tract's estimate is split
across catchments in proportion to the 2020 population of its blocks (by block internal point) that fall
inside each catchment. This is population-weighted areal interpolation; it assumes each ACS characteristic
is spread evenly across the people of a tract.

Margins of error for apportioned sums use sqrt(sum((w_t * MOE_t)^2)), an approximation that treats the
weights as fixed and the tracts as independent. Proportion MOEs use the ACS Handbook formula.

Generators: the 75 entries of the 2026 diocesan directory plus 3 parishes absent from it but listed in
MAP 2022 and by GCatholic (flagged in_2026_directory=false). A directory-only Voronoi population is also
reported for every parish so the effect of those 3 is visible.

Outputs (data/real/civic/):
  parish_catchments.json      per-parish profiles for both methods
  parish_tract_weights.json   the tract weights behind each profile (for audit / re-use)
  parish_voronoi.geojson      nearest-parish cells clipped to the two counties (display only)
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely.ops import unary_union
from shapely import voronoi_polygons

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import (MILE_KM, OUT, RAW, haversine_km, load_json, load_parishes, moe_prop, now_iso,  # noqa: E402
                    reliability, save_json, to_lnglat, to_xy)
import geometry  # noqa: E402

RADIUS_MI = 2.0

# shares reported with MOE: key -> (numerator, denominator)
SHARES = {
    "share_under18": ("age_under18", "pop"), "share_65plus": ("age_65plus", "pop"), "share_75plus": ("age_75plus", "pop"),
    "share_hh_children": ("hh_children", "hh"), "poverty_rate": ("pov_below100", "pov_universe"),
    "share_below200pct_poverty": ("pov_below200", "pov_universe"), "share_renter": ("renter_occ", "occ_units"),
    "share_owner": ("owner_occ", "occ_units"), "share_foreign_born": ("foreign_born", "nativity_universe"),
    "share_hispanic": ("hispanic", "hisp_universe"), "share_english_only": ("lang_english_only", "lang_universe"),
    "share_lep_households": ("lehh_total", "hh_lang_universe"),
}
LANG_KEYS = ["spanish", "tagalog", "chinese", "vietnamese", "korean", "other_asian_pacific", "other_indo_european",
             "russian_polish_slavic", "arabic", "french_haitian_cajun", "german_west_germanic", "other_unspecified"]


def load_tracts():
    t = load_json(OUT / "acs_tracts.json")
    cols = t["columns"]
    keys = [c for c in cols[2:] if not c.endswith("_moe")]
    data = {}
    for row in t["rows"]:
        rec = dict(zip(cols, row))
        data[rec["geoid"]] = rec
    return t["meta"], keys, data


def load_blocks():
    path = geometry.blocks_csv()
    g, tr, pop, lat, lng = [], [], [], [], []
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["pop20"]) <= 0:
                continue
            g.append(r["geoid20"]); tr.append(r["tract"]); pop.append(int(r["pop20"]))
            lat.append(float(r["lat"])); lng.append(float(r["lng"]))
    return np.array(g), np.array(tr), np.array(pop, dtype=float), np.array(lat), np.array(lng)


def aggregate(weights: dict[str, float], keys, tracts):
    vals = {}
    for k in keys:
        if k == "median_hh_income":
            continue
        est, sq, ok = 0.0, 0.0, True
        for gid, w in weights.items():
            rec = tracts.get(gid)
            if rec is None:
                continue
            e, m = rec.get(k), rec.get(k + "_moe")
            if e is None:
                ok = False if k in ("pop",) else ok
                continue
            est += w * e
            if m is not None:
                sq += (w * m) ** 2
        vals[k] = [round(est), round(math.sqrt(sq))]
    # median income: household-weighted median of tract medians (approximation, labelled as such)
    pairs = []
    for gid, w in weights.items():
        rec = tracts.get(gid)
        if rec and rec.get("median_hh_income") is not None and rec.get("hh"):
            pairs.append((rec["median_hh_income"], w * rec["hh"]))
    if pairs:
        pairs.sort()
        tot = sum(p[1] for p in pairs)
        acc, med = 0.0, None
        for v, wt in pairs:
            acc += wt
            if acc >= tot / 2:
                med = v
                break
        vals["median_hh_income_tract_weighted"] = {"value": med, "min_tract": pairs[0][0], "max_tract": pairs[-1][0],
                                                   "method": "household-weighted median of tract medians (approximate)"}
    return vals


def shares(vals):
    out = {}
    for k, (n, d) in SHARES.items():
        if n in vals and d in vals and vals[d][0]:
            p = vals[n][0] / vals[d][0]
            m = moe_prop(vals[n][0], vals[n][1], vals[d][0], vals[d][1])
            out[k] = [round(p, 4), None if m is None else round(m, 4)]
    return out


def languages(vals):
    """Non-English languages spoken at home, largest first, with LEP counts and reliability."""
    out = []
    for lk in LANG_KEYS:
        e, m = vals.get("lang_" + lk, [0, 0])
        le, lm = vals.get("lep_" + lk, [0, 0])
        if e <= 0:
            continue
        u = vals["lang_universe"][0] or 1
        out.append({"language_group": lk, "speakers": e, "speakers_moe": m, "share_of_pop5plus": round(e / u, 4),
                    "lep": le, "lep_moe": lm, "reliability": reliability(e, m)})
    return sorted(out, key=lambda x: -x["speakers"])


BIRTH = ["mexico", "philippines", "china", "india", "vietnam", "el_salvador", "guatemala", "korea", "honduras",
         "nicaragua", "peru", "brazil", "colombia", "afghanistan", "iran", "eritrea", "ethiopia", "nigeria", "fiji",
         "ireland", "italy", "portugal", "poland", "ukraine", "burma", "cambodia", "indonesia", "laos", "iraq",
         "lebanon", "cuba"]


def top_birth(vals, n=6):
    rows = [(c, vals.get("fb_" + c, [0, 0])) for c in BIRTH]
    rows = [r for r in rows if r[1][0] > 0]
    rows.sort(key=lambda r: -r[1][0])
    fb = vals.get("foreign_born", [0, 0])[0] or 1
    return [{"country": c, "people": v[0], "moe": v[1], "share_of_foreign_born": round(v[0] / fb, 4),
             "reliability": reliability(v[0], v[1])} for c, v in rows[:n]]


def build_voronoi_cells(parishes):
    pts = [Point(*to_xy(p["lng"], p["lat"])) for p in parishes]
    cf = load_json(RAW / "counties_full.geojson")
    diocese_ll = unary_union([shape(f["geometry"]) for f in cf["features"]])
    # project the diocese outline
    from shapely.ops import transform
    dio = transform(lambda x, y, z=None: to_xy(x, y), diocese_ll)
    cells = voronoi_polygons(MultiPoint(pts), extend_to=dio.buffer(50))
    feats = []
    for cell in cells.geoms:
        idx = next(i for i, pt in enumerate(pts) if cell.contains(pt))
        clipped = cell.intersection(dio)
        if clipped.is_empty:
            continue
        ll = transform(lambda x, y, z=None: to_lnglat(x, y), clipped).simplify(0.0004, preserve_topology=True)
        area_km2 = clipped.area  # projected units are km
        feats.append((idx, ll, area_km2))
    return feats


def main() -> None:
    acs_meta, keys, tracts = load_tracts()
    parishes = [p for p in load_parishes(include_extra=True) if p["lat"] is not None]
    g, tr, pop, lat, lng = load_blocks()
    plat = np.array([p["lat"] for p in parishes]); plng = np.array([p["lng"] for p in parishes])
    D = haversine_km(lat[:, None], lng[:, None], plat[None, :], plng[None, :])  # blocks x parishes
    tract_pop = {}
    for t, pp in zip(tr, pop):
        tract_pop[t] = tract_pop.get(t, 0) + pp

    in_dir = np.array([p["in_2026_directory"] for p in parishes])
    nearest = D.argmin(axis=1)
    nearest_dir = np.where(in_dir)[0][D[:, in_dir].argmin(axis=1)]
    r_km = RADIUS_MI * MILE_KM

    def weights_for(mask):
        w = {}
        for t, pp in zip(tr[mask], pop[mask]):
            w[t] = w.get(t, 0) + pp
        return {t: v / tract_pop[t] for t, v in w.items() if tract_pop[t] > 0}

    cells = {i: (geom, a) for i, geom, a in build_voronoi_cells(parishes)}
    out, wout, vfeats = [], [], []
    for j, p in enumerate(parishes):
        m_r = D[:, j] <= r_km
        m_v = nearest == j
        w_r, w_v = weights_for(m_r), weights_for(m_v)
        v_r, v_v = aggregate(w_r, keys, tracts), aggregate(w_v, keys, tracts)
        others = np.delete(np.arange(len(parishes)), j)
        dd = haversine_km(p["lat"], p["lng"], plat[others], plng[others])
        k = others[int(np.argmin(dd))]
        rec = {
            "id": p["id"], "name": p["name"], "city": p["city"], "kind": p["kind"],
            "in_2026_directory": p["in_2026_directory"], "lat": p["lat"], "lng": p["lng"], "geo_source": p["geo_source"],
            "nearest_other_parish": {"id": parishes[k]["id"], "miles": round(float(dd.min()) / MILE_KM, 2)},
            "parishes_within_2mi": int(((dd / MILE_KM) <= RADIUS_MI).sum()),
            "radius_2mi": {"pop2020_in_blocks": int(pop[m_r].sum()), "tracts_touched": len(w_r),
                           "values": v_r, "shares": shares(v_r), "languages": languages(v_r), "top_birthplaces": top_birth(v_r)},
            "voronoi": {"pop2020_in_blocks": int(pop[m_v].sum()), "tracts_touched": len(w_v),
                        "pop2020_directory_only": int(pop[nearest_dir == j].sum()) if p["in_2026_directory"] else None,
                        "area_sq_mi": round(cells[j][1] / (MILE_KM ** 2), 2) if j in cells else None,
                        "values": v_v, "shares": shares(v_v), "languages": languages(v_v), "top_birthplaces": top_birth(v_v)},
        }
        if not p["in_2026_directory"]:
            rec["status_note"] = p.get("status_note")
        out.append(rec)
        wout.append({"id": p["id"], "radius_2mi": {t: round(w, 4) for t, w in sorted(w_r.items())},
                     "voronoi": {t: round(w, 4) for t, w in sorted(w_v.items())}})
        if j in cells:
            vfeats.append({"type": "Feature", "properties": {"parish_id": p["id"], "name": p["name"],
                                                             "in_2026_directory": p["in_2026_directory"],
                                                             "pop2020": int(pop[m_v].sum()),
                                                             "area_sq_mi": rec["voronoi"]["area_sq_mi"]},
                           "geometry": mapping(cells[j][0])})

    # coverage check: every tract's population is assigned exactly once under voronoi
    tot_acs = sum((tracts[t]["pop"] or 0) for t in tracts)
    tot_v = sum(r["voronoi"]["values"]["pop"][0] for r in out)
    meta = {
        "generated_at": now_iso(),
        "acs": {"vintage": acs_meta["vintage"], "source_url": acs_meta["source_url"], "fetched_at": acs_meta["fetched_at"]},
        "weights_source": {"name": "2020 Census block populations (POP20) and internal points, TIGER/Line 2020 TABBLOCK20",
                           "source_url": geometry.URLS["blocks"]},
        "boundary_note": ("The Diocese of Oakland does not publish parish boundaries. These are statistical stand-ins: "
                          "a 2-mile radius and a nearest-parish (Voronoi) partition. Straight-line distance; real "
                          "parish life follows Mass times, language, family history and freeways, not geometry."),
        "methods": {
            "radius_2mi": "All 2020 census blocks whose internal point is within 2.0 miles of the church; circles overlap, "
                          "so the same residents appear in several parishes' profiles. Do not add across parishes.",
            "voronoi": "Each 2020 census block is assigned to the nearest parish church (straight line) within Alameda + "
                       "Contra Costa; every resident counted once. Cells are clipped to the county outlines.",
            "apportionment": "ACS tract estimate x (2020 pop of the tract's blocks inside the catchment / 2020 tract pop).",
            "moe": "Apportioned-sum MOE = sqrt(sum((w*MOE)^2)) (approximation); share MOE per ACS Handbook ratio formula; "
                   "90% confidence. reliability: CV<=15% high, <=30% medium, else low.",
            "median_income": "median_hh_income_tract_weighted = household-weighted median of tract medians (approximate).",
        },
        "generators": {"count": len(parishes), "in_2026_directory": int(in_dir.sum()),
                       "flagged_not_in_directory": [p["id"] for p in parishes if not p["in_2026_directory"]],
                       "single_point_note": ("Each parish is one point (its directory address). Multi-site parishes (e.g. "
                                             "Divine Mercy Oakland's two churches; Pleasanton's St. Augustine and St. "
                                             "Elizabeth Seton; Christ the King-St. Stephen) are represented by one site.")},
        "coverage_check": {"acs_pop_two_counties": tot_acs, "voronoi_pop_sum": tot_v},
        "share_keys": SHARES,
    }
    save_json(OUT / "parish_catchments.json", {"meta": meta, "parishes": out}, compact=True)
    save_json(OUT / "parish_tract_weights.json", {"meta": {"generated_at": meta["generated_at"],
                                                           "note": "weight = share of the tract's 2020 population inside the catchment"},
                                                  "parishes": wout}, compact=True)
    save_json(OUT / "parish_voronoi.geojson", {"type": "FeatureCollection", "meta": {
        "note": "Nearest-parish (Voronoi) cells, straight-line, clipped to Alameda + Contra Costa. NOT parish boundaries.",
        "generated_at": meta["generated_at"]}, "features": vfeats}, compact=True)
    print("parishes:", len(out), "coverage:", tot_acs, tot_v)


if __name__ == "__main__":
    main()
