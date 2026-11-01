#!/usr/bin/env python3
"""Language of the neighbourhood vs. language of the liturgy.

Compares the languages people speak at home (ACS 2020-2024, C16001, by tract and by parish catchment)
with the languages in which Masses are known to be offered, to show where sizeable language communities
live farther from a Mass or ministry in their language. Framed as pastoral opportunity, not deficit.

Worship-language evidence, merged per parish and language:
  1. oakdiocese.org directory Mass times (data/real/parishes_directory.json, mass_times_text)
  2. parsed parish-website schedules (data/seed/mass_schedule.json, language field)
  3. hand-checked parish-website text (ingest/public/civic/manual/worship_languages_evidence.json)
  4. MAP 2022 appendix: count of Masses "in a language other than English or based on culture"
     (data/real/map_2022_attendance.json) - language not named, used only as a signal.

Outputs: data/real/civic/parish_worship_languages.json, data/real/civic/language_liturgy.json
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import (HERE, MILE_KM, OUT, REAL, ROOT, haversine_km, load_json, load_parishes, now_iso,  # noqa: E402
                    reliability, save_json)
import geometry  # noqa: E402

# worship language -> ACS C16001 language group (None = not a household language, e.g. Latin)
LANG2GROUP = {
    "Spanish": "spanish", "Tagalog": "tagalog", "Vietnamese": "vietnamese", "Mandarin": "chinese",
    "Cantonese": "chinese", "Chinese": "chinese", "Korean": "korean", "Tongan": "other_asian_pacific",
    "Samoan": "other_asian_pacific", "Kmhmu": "other_asian_pacific", "Indonesian": "other_asian_pacific",
    "Portuguese": "other_indo_european", "Polish": "russian_polish_slavic", "Swahili": "other_unspecified",
    "Indian communities": None, "Latin": None,
}
GROUP_LABEL = {
    "spanish": "Spanish", "tagalog": "Tagalog (incl. Filipino)", "chinese": "Chinese (incl. Mandarin, Cantonese)",
    "vietnamese": "Vietnamese", "korean": "Korean",
    "other_asian_pacific": "Other Asian & Pacific Island languages (mostly Telugu, Tamil, Malayalam in the Tri-Valley/Fremont; also Japanese, Khmer, Thai/Lao, Kmhmu, Ilocano, Tongan, Samoan)",
    "other_indo_european": "Other Indo-European (Hindi, Punjabi, Gujarati, Urdu, Persian, Portuguese, Italian, etc.)",
    "russian_polish_slavic": "Russian, Polish or other Slavic", "arabic": "Arabic",
    "french_haitian_cajun": "French, Haitian or Cajun", "german_west_germanic": "German or other West Germanic",
    "other_unspecified": "Other and unspecified (incl. Amharic, Tigrinya, Swahili, West African languages)",
}
FREQ_RANK = {"weekly": 3, "monthly": 2, "occasional": 1, "ministry": 0}
DIR_LANGS = ["Spanish", "Vietnamese", "Mandarin", "Cantonese", "Tagalog", "Tongan", "Samoan", "Korean", "Portuguese",
             "Polish", "Latin", "Chinese", "Indonesian", "Bilingual"]
OPP_MIN_SPEAKERS = 1000      # catchment speakers of the language (people 5+)
OPP_MIN_LEP = 300            # or this many who speak English less than "very well"
OPP_MIN_MILES = 3.0          # and no known Mass in the language within this distance of the church
# ACS groups that correspond to one worship language; the rest bundle many languages
SPECIFIC = {"spanish", "tagalog", "chinese", "vietnamese", "korean"}


def directory_entries(parishes):
    out = []
    for p in parishes:
        txt = p.get("mass_times_text") or ""
        for line in txt.splitlines():
            for lang in DIR_LANGS:
                if re.search(r"\b" + lang + r"\b", line, re.I):
                    if lang == "Bilingual":
                        lang = "Spanish" if re.search("spanish", line, re.I) else None
                        if not lang:
                            continue
                    freq = "monthly" if re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th|month)", line, re.I) else "weekly"
                    out.append({"parish_id": p["id"], "language": lang, "frequency": freq,
                                "evidence": line.strip()[:90], "source_url": p.get("source_url"),
                                "source": "oakdiocese.org parish directory"})
    return out


def seed_entries():
    d = load_json(ROOT / "data" / "seed" / "mass_schedule.json") or {}
    out = []
    for r in d.get("mass_schedule", []):
        lang = r.get("language")
        if lang and lang != "English":
            out.append({"parish_id": r["entity_id"], "language": lang, "frequency": "weekly",
                        "evidence": (r.get("raw") or "")[:90], "source_url": r.get("source_url"),
                        "source": r.get("source") or "parsed schedule"})
    return out


def merge(entries):
    by = defaultdict(lambda: {"frequency": None, "mass": False, "ministry": False, "evidence": []})
    for e in entries:
        k = (e["parish_id"], e["language"])
        b = by[k]
        f = e["frequency"]
        if f != "ministry":
            b["mass"] = True
            if b["frequency"] is None or FREQ_RANK[f] > FREQ_RANK.get(b["frequency"], -1):
                b["frequency"] = f
        else:
            b["ministry"] = True
        ev = {"evidence": e["evidence"], "source_url": e["source_url"],
              "source": e.get("source") or "parish website (hand-checked)", "frequency": f}
        if e.get("note"):
            ev["note"] = e["note"]
        if not any(x["evidence"] == ev["evidence"] for x in b["evidence"]):
            b["evidence"].append(ev)
    return by


def tract_centroids():
    """Population-weighted tract centroids from 2020 block internal points."""
    acc = defaultdict(lambda: [0.0, 0.0, 0.0])
    with open(geometry.blocks_csv()) as f:
        for r in csv.DictReader(f):
            p = int(r["pop20"])
            if p > 0:
                a = acc[r["tract"]]
                a[0] += p * float(r["lat"]); a[1] += p * float(r["lng"]); a[2] += p
    return {t: (a[0] / a[2], a[1] / a[2]) for t, a in acc.items() if a[2] > 0}


def main() -> None:
    parishes = load_parishes(include_extra=True)
    pid = {p["id"]: p for p in parishes}
    sites = {r["id"]: r for r in load_json(REAL / "parish_sites.json")["records"]}
    cur = load_json(HERE / "manual" / "worship_languages_evidence.json")
    entries = directory_entries(parishes) + seed_entries() + [
        {**e, "source": "parish website (crawled 2026-10-25, hand-checked)"} for e in cur["entries"]]
    merged = merge(entries)

    # MAP 2022 non-English Mass counts
    ents = load_json(ROOT / "data" / "seed" / "entities.json")["entities"]
    key2id = {e["attrs"].get("map_canon_key"): e["id"] for e in ents if e["attrs"].get("map_canon_key")}
    mapj = load_json(REAL / "map_2022_attendance.json")
    map_by = {key2id[r["canon_key"]]: r for r in mapj["rows"] if r["canon_key"] in key2id}

    # per-parish worship language records
    pw = []
    for p in parishes:
        s = sites.get(p["id"], {})
        crawl = s.get("crawl_status") or ("no_record" if p["in_2026_directory"] else "not_crawled")
        has_dir_times = bool(p.get("mass_times_text"))
        sched = ("parish_website" if crawl == "ok" else "diocesan_directory" if has_dir_times else "unknown")
        langs = []
        for (ppid, lang), b in merged.items():
            if ppid == p["id"]:
                langs.append({"language": lang, "acs_group": LANG2GROUP.get(lang), "mass": b["mass"],
                              "frequency": b["frequency"], "ministry": b["ministry"], "evidence": b["evidence"]})
        langs.sort(key=lambda x: (-FREQ_RANK.get(x["frequency"] or "ministry", 0), x["language"]))
        m = map_by.get(p["id"])
        pw.append({"id": p["id"], "name": p["name"], "city": p["city"], "in_2026_directory": p["in_2026_directory"],
                   "schedule_source": sched, "crawl_status": crawl, "languages": langs,
                   "map_2022": None if not m else {"masses": m.get("masses"), "lang_masses": m.get("lang_masses"),
                                                   "note": "Lng = Masses in a language other than English or based on culture (MAP 2022, p.19); language not named"}})
    save_json(OUT / "parish_worship_languages.json", {
        "meta": {"generated_at": now_iso(),
                 "sources": {"directory": "https://www.oakdiocese.org/parishes/", "parish_sites": "data/real/parish_sites.json",
                             "curated": "ingest/public/civic/manual/worship_languages_evidence.json",
                             "map_2022": mapj.get("source_url")},
                 "diocesan_statement": {"text": "Masses ... in more than a dozen languages, including Spanish, Portuguese, Polish, Kmhmu",
                                        "source_url": "https://www.oakdiocese.org/ethnic-pastoral-and-cultural-centers/",
                                        "also_named_there": ["Vietnamese", "Indonesian", "Cantonese", "Mandarin", "Korean", "Tagalog"]},
                 "schedule_source_values": {"parish_website": "crawled schedule (37 sites)",
                                            "diocesan_directory": "directory lists Mass times", "unknown": "no schedule available to this project"},
                 "caveat": "A language missing here is unknown, not absent, for parishes whose schedule_source is unknown."},
        "parishes": pw})

    # ---------------- sites per ACS group (Mass weekly or monthly)
    mass_sites = defaultdict(list)     # group -> [(parish_id, language, frequency)]
    ministry_sites = defaultdict(list)
    for r in pw:
        for l in r["languages"]:
            g = l["acs_group"]
            if not g:
                continue
            if l["mass"] and l["frequency"] in ("weekly", "monthly"):
                mass_sites[g].append((r["id"], l["language"], l["frequency"]))
            if l["ministry"]:
                ministry_sites[g].append((r["id"], l["language"]))

    # ---------------- tract-level distances to nearest known Mass in the group
    t = load_json(OUT / "acs_tracts.json")
    cols = t["columns"]
    tracts = {row[0]: dict(zip(cols, row)) for row in t["rows"]}
    cent = tract_centroids()
    groups = ["spanish", "tagalog", "chinese", "vietnamese", "korean", "other_asian_pacific", "other_indo_european",
              "russian_polish_slavic", "arabic", "other_unspecified", "french_haitian_cajun"]
    bands = [(0, 2), (2, 5), (5, 10), (10, 1e9)]
    tract_dist = {}
    summary = {}
    for g in groups:
        sites_g = sorted(set(s[0] for s in mass_sites.get(g, [])))
        slat = np.array([pid[s]["lat"] for s in sites_g]); slng = np.array([pid[s]["lng"] for s in sites_g])
        tot = lep = 0.0
        band_sp = [0.0] * len(bands); band_lep = [0.0] * len(bands)
        for gid, rec in tracts.items():
            sp = rec.get("lang_" + g) or 0
            le = rec.get("lep_" + g) or 0
            tot += sp; lep += le
            if gid not in cent:
                continue
            if len(sites_g) and g in SPECIFIC:
                d = haversine_km(cent[gid][0], cent[gid][1], slat, slng) / MILE_KM
                dm = float(d.min())
                tract_dist.setdefault(gid, {})[g] = round(dm, 2)
                for i, (lo, hi) in enumerate(bands):
                    if lo <= dm < hi:
                        band_sp[i] += sp; band_lep[i] += le
        summary[g] = {
            "label": GROUP_LABEL[g], "speakers_5plus": round(tot), "lep": round(lep),
            "known_mass_sites": [{"parish_id": s[0], "language": s[1], "frequency": s[2]} for s in mass_sites.get(g, [])],
            "known_ministry_sites": [{"parish_id": s[0], "language": s[1]} for s in ministry_sites.get(g, [])],
            "speakers_by_miles_to_nearest_known_mass": None if (not sites_g or g not in SPECIFIC) else {
                f"{lo}-{hi if hi < 1e8 else 'plus'}": {"speakers": round(band_sp[i]), "share": round(band_sp[i] / tot, 3) if tot else None,
                                                     "lep": round(band_lep[i])} for i, (lo, hi) in enumerate(bands)},
        }

    # ---------------- per-parish catchment comparison (Voronoi catchments)
    cat = {r["id"]: r for r in load_json(OUT / "parish_catchments.json")["parishes"]}
    rows, opps = [], []
    for r in pw:
        c = cat.get(r["id"])
        if not c:
            continue
        p = pid[r["id"]]
        v = c["voronoi"]["values"]
        u = v["lang_universe"][0] or 1
        here = {l["acs_group"]: l for l in r["languages"] if l["acs_group"]}
        items = []
        for g in groups:
            sp, spm = v.get("lang_" + g, [0, 0])
            le, lem = v.get("lep_" + g, [0, 0])
            if sp < 300 and sp / u < 0.03:
                continue
            sites_g = sorted(set(s[0] for s in mass_sites.get(g, [])))
            near = None
            if sites_g:
                dd = [(float(haversine_km(p["lat"], p["lng"], pid[s]["lat"], pid[s]["lng"])) / MILE_KM, s) for s in sites_g]
                dm, sid = min(dd)
                near = {"parish_id": sid, "miles": round(dm, 2)}
            h = here.get(g)
            it = {"group": g, "speakers": sp, "speakers_moe": spm, "share_of_pop5plus": round(sp / u, 3), "lep": le,
                  "lep_moe": lem, "reliability": reliability(sp, spm),
                  "mass_here": bool(h and h["mass"]), "mass_frequency_here": h["frequency"] if h else None,
                  "ministry_here": bool(h and h["ministry"]), "nearest_known_mass": near}
            items.append(it)
            if g not in SPECIFIC:
                continue  # mixed-language ACS group: no meaningful 'nearest Mass in the language'
            far = near is None or near["miles"] > OPP_MIN_MILES
            big = sp >= OPP_MIN_SPEAKERS or le >= OPP_MIN_LEP
            if big and far and not it["mass_here"]:
                lm = (r.get("map_2022") or {}).get("lang_masses")
                known = r["schedule_source"] != "unknown"
                strong = sp >= 2000 or le >= 750
                tier = ("A" if known and strong else "B" if not known and strong else "C")
                opps.append({"parish_id": r["id"], "name": r["name"], "city": r["city"], "group": g, "tier": tier,
                             "speakers": sp, "speakers_moe": spm, "lep": le, "lep_moe": lem,
                             "reliability": reliability(sp, spm), "nearest_known_mass": near,
                             "ministry_here": it["ministry_here"], "schedule_known": known,
                             "map_2022_lang_masses": lm,
                             "confidence": ("parish website schedule lists no Mass in this language" if r["schedule_source"] == "parish_website"
                                            else "diocesan directory lists Mass times, none in this language (listing may be incomplete)"
                                            if r["schedule_source"] == "diocesan_directory"
                                            else ("schedule unknown; MAP 2022 lists non-English Masses here - may already exist"
                                                  if lm else "schedule unknown"))})
        rows.append({"parish_id": r["id"], "schedule_source": r["schedule_source"], "languages_vs_liturgy": items})

    opps.sort(key=lambda x: (x["group"], x["tier"], -x["speakers"]))
    # mixed groups: where they are largest, plus the worship in member languages that is known
    mixed = {}
    for g in groups:
        if g in SPECIFIC:
            continue
        top = sorted(((rr["parish_id"], it) for rr in rows for it in rr["languages_vs_liturgy"] if it["group"] == g),
                     key=lambda x: -x[1]["speakers"])[:6]
        mixed[g] = {"label": GROUP_LABEL[g], "largest_catchments": [{"parish_id": a, "speakers": it["speakers"],
                                                                      "speakers_moe": it["speakers_moe"], "lep": it["lep"]} for a, it in top],
                    "known_worship_in_member_languages": summary[g]["known_mass_sites"] + [
                        {**m, "ministry": True} for m in summary[g]["known_ministry_sites"]]}
    save_json(OUT / "language_liturgy.json", {
        "meta": {
            "generated_at": now_iso(),
            "framing": ("Pastoral opportunity, not deficit: where a sizeable language community lives and no Mass in that "
                        "language is known nearby. Speaking a language at home says nothing about religion or about the "
                        "language a family prefers for worship (many Filipino and Indian Catholics worship in English)."),
            "acs": "ACS 2020-2024 5-year, C16001 (population 5+), apportioned to Voronoi catchments with 2020 block populations",
            "known_mass": "weekly or monthly Mass in a language mapped to the ACS group (ministries listed separately)",
            "opportunity_rule": {"groups": sorted(SPECIFIC), "min_speakers": OPP_MIN_SPEAKERS, "or_min_lep": OPP_MIN_LEP,
                                 "min_miles_to_known_mass": OPP_MIN_MILES,
                                 "and": "no known Mass in the language at the parish itself",
                                 "tiers": {"A": "schedule known and >=2,000 speakers or >=750 LEP",
                                           "B": "same size but the parish schedule is unknown (check first; MAP 2022 count shown)",
                                           "C": "smaller (>=1,000 speakers or >=300 LEP)"}},
            "mixed_groups_note": ("other_asian_pacific, other_indo_european, russian_polish_slavic, arabic, french_haitian_cajun "
                                  "and other_unspecified bundle many languages, so no distance-based opportunity is computed; "
                                  "see mixed_groups. County detail (ACS 2024 1-year B16001) is in acs_areas.json."),
            "distance": "straight-line miles from tract population-weighted centroid (2020 blocks) or from the parish church",
            "caveats": [
                "Schedules are known for only part of the diocese (parish websites crawled 37/70; directory times for a few more).",
                "ACS language groups are broad: 'other Asian & Pacific Island' is mostly South Indian languages here; Tongan and Samoan are a small part.",
                "Catchment and tract figures carry margins of error (90%); 'reliability' = CV-based (high <=15%, medium <=30%, low).",
                "A language community may be well served by a parish just over the threshold distance, by a non-territorial community, or by Eastern Catholic churches not in this dataset.",
            ]},
        "language_groups": summary,
        "tract_miles_to_nearest_known_mass": tract_dist,
        "parishes": rows,
        "opportunities": opps,
        "mixed_groups": mixed,
    }, compact=True)
    print("opportunities:", len(opps))
    for g in groups:
        s = summary[g]
        print(g, s["speakers_5plus"], "sites:", len(s["known_mass_sites"]), s["speakers_by_miles_to_nearest_known_mass"])


if __name__ == "__main__":
    main()
