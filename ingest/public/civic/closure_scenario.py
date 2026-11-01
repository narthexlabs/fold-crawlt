#!/usr/bin/env python3
"""Scenario: nearest-parish neighbourhoods after the worship-site closures announced on April 28, 2026.

Takes the closure list in data/real/oakland/closures_2026.json and removes the church points in this model that are
closing sites. Those are whole-site closures where the parish's mapped church is the one closing. It then reassigns
their 2020 census blocks to the nearest remaining church and reports, for each closing site:
  - which parishes receive its neighbourhood, and what share of the residents goes to each
  - the languages spoken at home among those residents (ACS 2020-2024, apportioned by 2020 block population)
  - straight-line distance from the closing church to each receiving church
  - the residents' average straight-line distance to their nearest church, before and after

Multi-site parishes whose mapped point is the church that stays are unaffected in this model: St. Philip Neri
(St. Albert closes), St. Lawrence O'Toole (St. Paschal Baylon closes), Christ the King (St. Stephen closes) and
St. Anthony (Mary Help of Christians closes). Our Lady of Guadalupe's Blacow Road site "relocates to the parish's
Fremont Boulevard site", which has no address in our data, so it is left in place. St. Patrick (Oakland) and the
St. Andrew Kim Korean Pastoral Center are not points in this model.

This is a geometry exercise, not a forecast. People will choose parishes by language, Mass time, school and
friendship, and the bishop asks receiving parishes to welcome them. Output: data/real/civic/closure_scenario_2026.json
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import MILE_KM, OUT, REAL, haversine_km, load_json, load_parishes, now_iso, save_json  # noqa: E402
import catchments as C  # noqa: E402

CLOSING_POINTS = {   # closure-list site -> parish point in this model that is that site
    "Our Lady of Lourdes": "our-lady-lourdes-oakland",
    "Sacred Heart": "sacred-heart-oakland",
    "St. Augustine": "augustine-oakland",
    "St. Barnabas": "barnabas-alameda",
    "Transfiguration": "transfiguration-castro-valley",
    "St. Rose of Lima": "rose-lima-patrick-crockett",
}
LANGS = ["spanish", "chinese", "tagalog", "vietnamese", "korean", "other_indo_european", "other_asian_pacific",
         "other_unspecified", "arabic", "russian_polish_slavic", "french_haitian_cajun"]


def main() -> None:
    clos = load_json(REAL / "oakland" / "closures_2026.json")
    parishes = load_parishes(include_extra=True)
    ids = [p["id"] for p in parishes]
    acs_meta, keys, tracts = C.load_tracts()
    g, tr, pop, lat, lng = C.load_blocks()
    plat = np.array([p["lat"] for p in parishes]); plng = np.array([p["lng"] for p in parishes])
    D = haversine_km(lat[:, None], lng[:, None], plat[None, :], plng[None, :])
    before = D.argmin(axis=1)
    closing_idx = [ids.index(pid) for pid in CLOSING_POINTS.values()]
    keep = np.ones(len(parishes), bool); keep[closing_idx] = False
    keep_idx = np.where(keep)[0]
    after = keep_idx[D[:, keep].argmin(axis=1)]
    tract_pop = defaultdict(float)
    for t, pp in zip(tr, pop):
        tract_pop[t] += pp

    def apportion(mask):
        w = defaultdict(float)
        for t, pp in zip(tr[mask], pop[mask]):
            w[t] += pp
        w = {t: v / tract_pop[t] for t, v in w.items() if tract_pop[t] > 0}
        v = C.aggregate(w, keys, tracts)
        return v

    sites = []
    for site, pid in CLOSING_POINTS.items():
        j = ids.index(pid)
        m = before == j
        tot = float(pop[m].sum())
        recv = []
        for k in np.unique(after[m]):
            mk = m & (after == k)
            v = apportion(mk)
            langs = sorted(({"group": lg, "speakers": v["lang_" + lg][0], "moe": v["lang_" + lg][1]} for lg in LANGS
                            if v.get("lang_" + lg, [0])[0] >= 100), key=lambda x: -x["speakers"])[:4]
            recv.append({"parish_id": ids[k], "name": parishes[k]["name"],
                         "share_of_residents": round(float(pop[mk].sum()) / tot, 3) if tot else None,
                         "pop2020": int(pop[mk].sum()), "acs_pop": v["pop"][0],
                         "miles_from_closing_church": round(float(haversine_km(plat[j], plng[j], plat[k], plng[k])) / MILE_KM, 2),
                         "top_languages_other_than_english": langs})
        recv.sort(key=lambda r: -r["pop2020"])
        d0 = float((D[m, j] * pop[m]).sum() / tot) / MILE_KM if tot else None
        d1 = float((D[m][np.arange(m.sum()), after[m]] * pop[m]).sum() / tot) / MILE_KM if tot else None
        meta_site = next((s for s in clos["sites"] if s["site"].startswith(site)), {})
        sites.append({"closing_site": site, "parish_point": pid, "city": parishes[j]["city"],
                      "closure_context": meta_site.get("context"),
                      "neighbourhood_pop2020": int(tot), "neighbourhood_acs_pop": apportion(m)["pop"][0],
                      "avg_miles_to_nearest_church_before": round(d0, 2) if d0 is not None else None,
                      "avg_miles_to_nearest_church_after": round(d1, 2) if d1 is not None else None,
                      "receiving_parishes": recv})
    save_json(OUT / "closure_scenario_2026.json", {
        "meta": {"generated_at": now_iso(), "closures_source": "data/real/oakland/closures_2026.json",
                 "announcement": "https://www.oakdiocese.org/aligning-our-reality-with-our-mission/",
                 "method": ("Nearest-parish (straight-line) assignment of 2020 census blocks, before and after removing the "
                            "closing church points; ACS 2020-2024 apportioned by 2020 block population."),
                 "not_modelled": ["St. Albert the Great, St. Paschal Baylon, St. Stephen, Mary Help of Christians (the parish's "
                                  "other church is the mapped point)", "Our Lady of Guadalupe relocation (no address for the "
                                  "Fremont Boulevard site)", "St. Patrick (Oakland) and the St. Andrew Kim Korean Pastoral Center "
                                  "(not in the 2026 directory points)"],
                 "caution": ("Geometry, not a forecast: people choose parishes by language, Mass time, school and friendship. "
                             "Closure timelines are not public (no decrees found as of 2026-10-26).")},
        "sites": sites}, compact=False)
    for s in sites:
        print(s["closing_site"], s["neighbourhood_pop2020"], s["avg_miles_to_nearest_church_before"], "->",
              s["avg_miles_to_nearest_church_after"], [(r["parish_id"], r["share_of_residents"], r["miles_from_closing_church"]) for r in s["receiving_parishes"][:4]])


if __name__ == "__main__":
    main()
