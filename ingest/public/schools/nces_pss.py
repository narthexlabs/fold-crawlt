"""NCES Private School Universe Survey (PSS), two latest cycles -> data/real/schools_nces.json,
plus a CDE <-> NCES crosswalk -> data/real/schools_crosswalk.json.

Source: https://nces.ed.gov/surveys/pss/pssdata.asp (public-use CSV, 2023-24 and 2021-22).
Filter: PSTABB=CA, county Alameda/Contra Costa, RELIG=1 (Roman Catholic). Every row in that
filter carries DIOCESE=0605 (Oakland).

Run after cde_affidavit.py:  python -I ingest/public/schools/nces_pss.py
"""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schools import (RAW_SCHOOLS, REAL, download, load_json, now_iso, operator_for,  # noqa: E402
                      save_json, school_key, title_case)

CYCLES = {"2023-24": ("pss2324_pu_csv.zip", "24"), "2021-22": ("pss2122_pu_csv.zip", "22")}
BASE = "https://nces.ed.gov/surveys/pss/zip/{}"

P445 = {"1": "Parochial", "2": "Diocesan", "3": "Private (religious order / independent)"}
TYPOLOGY = {"1": "Catholic, parochial", "2": "Catholic, diocesan", "3": "Catholic, private"}
LEVEL = {"1": "Elementary", "2": "Secondary", "3": "Combined"}
ORIENT = {"1": "Roman Catholic"}
GRADE_CODE = {"1": "UG", "2": "PK", "3": "K", "4": "TK", "5": "TK", "6": "1", "7": "2", "8": "3", "9": "4",
              "10": "5", "11": "6", "12": "7", "13": "8", "14": "9", "15": "10", "16": "11", "17": "12"}
RACE = {"P320": "hispanic", "P330": "white", "P325": "black", "P316": "asian", "P318": "pacific_islander",
        "P310": "american_indian", "P332": "two_or_more"}


def num(v):
    if v in (None, "", ".", "-1"):
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else round(f, 1)
    except ValueError:
        return None


def load_cycle(label: str, fetched_at: str) -> list[dict]:
    fname, yy = CYCLES[label]
    url = BASE.format(fname)
    zpath = download(url, RAW_SCHOOLS / "nces" / fname)
    with zipfile.ZipFile(zpath) as z:
        member = next(n for n in z.namelist() if n.endswith(".csv"))
        text = z.read(member).decode("latin-1")
    out = []
    for d in csv.DictReader(io.StringIO(text)):
        if d["PSTABB"] != "CA" or d["RELIG"] != "1":
            continue
        if d["PCNTNM"].strip().upper() not in ("ALAMEDA", "CONTRA COSTA"):
            continue
        tot = num(d["P305"]) or 0
        race = {k: num(d[c]) for c, k in RACE.items()}
        name = title_case(d["PINST"])
        out.append({
            "nces_ppin": d["PPIN"],
            "survey_year": label,
            "name": name,
            "address": title_case(d["PL_ADD"] or d["PADDRS"]),
            "mailing_address": title_case(d["PADDRS"]),
            "city": title_case(d["PL_CIT"] or d["PCITY"]),
            "zip": (d["PL_ZIP"] or d["PZIP"]) or None,
            "county": title_case(d["PCNTNM"]),
            "phone": d["PPHONE"] or None,
            "lat": num(d.get(f"LATITUDE{yy}")), "lng": num(d.get(f"LONGITUDE{yy}")),
            "grade_low": GRADE_CODE.get(d.get(f"LOGR20{yy}"), d.get(f"LOGR20{yy}")),
            "grade_high": GRADE_CODE.get(d.get(f"HIGR20{yy}"), d.get(f"HIGR20{yy}")),
            "level": LEVEL.get(d["LEVEL"], d["LEVEL"]),
            "orientation": ORIENT.get(d["ORIENT"], d["ORIENT"]),
            "catholic_type": P445.get(d["P445"], d["P445"]),
            "typology": TYPOLOGY.get(d["TYPOLOGY"], d["TYPOLOGY"]),
            "diocese_code": d["DIOCESE"],
            "total_enrollment": num(d["P305"]),
            "k12_students": num(d["NUMSTUDS"]),
            "pk_enrollment": num(d["P150"]),
            "male_students": num(d["MALES"]),
            "race_ethnicity_counts": race,
            "race_ethnicity_pct": {k: (round(100 * v / tot, 1) if (v is not None and tot) else None)
                                   for k, v in race.items()},
            "fte_teachers": num(d["NUMTEACH"]),
            "student_teacher_ratio": num(d["STTCH_RT"]),
            "operator": operator_for(name) or "Diocese of Oakland / parish",
            "diocesan": operator_for(name) is None,
            "source_url": url,
            "fetched_at": fetched_at,
        })
    return out


def crosswalk(cde: list[dict], nces: list[dict]) -> list[dict]:
    """Match each CDE school (CDS code) to its NCES PPIN in each PSS cycle, on name key + city.

    PPINs can change between cycles (Salesian), so matching runs per cycle. The CDE file has a
    public district, not a city; when the district's city fails (Pleasant Hill is in Mt. Diablo
    USD), matching falls back to the best name within the two counties.
    """
    cde_schools = {}
    for r in sorted(cde, key=lambda r: r["school_year"]):
        cde_schools[r["cds_code"]] = r  # latest year's name wins
    manual = {  # names diverge too far; checked by address
        "01612346972921": "St Edward",         # "St. Edward Parish" = St Edward Elementary, Newark
        "07618126967707": "St Mary Elementary",  # St. Mary of the Immaculate Conception, Walnut Creek
        "07617546967624": "Christ The King",   # Pleasant Hill
        "07617966933493": "Salesian",
        "07617546158992": "De La Salle Academy",
        "07617546933394": "De La Salle High School",
    }
    rows = []
    matched = {y: set() for y in CYCLES}
    for cds, c in sorted(cde_schools.items(), key=lambda kv: kv[1]["name"]):
        ck, city = school_key(c["name"]), c.get("city_hint")
        row = {"cds_code": cds, "cde_name": c["name"], "cde_city_hint": city, "nces": {}}
        for y in CYCLES:
            pool = [n for n in nces if n["survey_year"] == y and n["nces_ppin"] not in matched[y]]
            best, score, method = None, 0.0, None
            if cds in manual:
                cand = [n for n in pool if school_key(n["name"]).startswith(school_key(manual[cds]))]
                if cand:
                    best, score, method = cand[0], 1.0, "manual"
            if best is None:
                for restrict in ([True, False] if city else [False]):
                    for n in pool:
                        if restrict and (n["city"] or "").lower() != city.lower():
                            continue
                        nk = school_key(n["name"])
                        sc = 1.0 if ck and ck == nk else SequenceMatcher(None, ck, nk).ratio()
                        if sc > score:
                            best, score = n, sc
                            method = "name+city" if restrict else "name (county-wide)"
                    if score >= (0.8 if restrict else 0.95):
                        break
                if method == "name (county-wide)" and score < 0.95:
                    best = None
            if best is not None and score >= 0.8:
                matched[y].add(best["nces_ppin"])
                row["nces"][y] = {"ppin": best["nces_ppin"], "name": best["name"], "score": round(score, 3),
                                  "method": method}
                row.update({k: row.get(k) or best[k] for k in ("address", "city", "zip", "phone")})
        row["match_method"] = "matched" if row["nces"] else "unmatched"
        for k in ("address", "city", "zip", "phone"):
            row.setdefault(k, None)
        rows.append(row)
    for y in CYCLES:
        for n in nces:
            if n["survey_year"] == y and n["nces_ppin"] not in matched[y]:
                rows.append({"cds_code": None, "cde_name": None, "cde_city_hint": None,
                             "nces": {y: {"ppin": n["nces_ppin"], "name": n["name"], "score": None, "method": None}},
                             "address": n["address"], "city": n["city"], "zip": n["zip"], "phone": n["phone"],
                             "match_method": "nces_only"})
    return rows


def main() -> None:
    fetched_at = now_iso()
    recs = []
    for label in CYCLES:
        recs += load_cycle(label, fetched_at)
    recs.sort(key=lambda r: (r["name"], r["survey_year"]))
    save_json(REAL / "schools_nces.json", {
        "source": "NCES Private School Universe Survey (PSS), public-use files",
        "source_page": "https://nces.ed.gov/surveys/pss/pssdata.asp",
        "survey_years": list(CYCLES),
        "filter": "PSTABB=CA, PCNTNM in (ALAMEDA, CONTRA COSTA), RELIG=1 (Roman Catholic); all have DIOCESE=0605",
        "notes": ["grade_low/grade_high decode LOGR/HIGR recodes (2=PK, 3=K, 6=grade 1 ... 17=grade 12); mapping inferred from the data, the layout gives no value labels.",
                  "catholic_type is the school's own PSS answer (Q13C) and shifts between cycles; use diocesan/operator instead.",
                  "total_enrollment = P305 (includes PK/TK where reported); k12_students = NUMSTUDS."],
        "count_records": len(recs),
        "count_schools": len({r["nces_ppin"] for r in recs}),
        "fetched_at": fetched_at,
        "records": recs,
    })
    cde = load_json(REAL / "schools_cde.json", {}).get("records", [])
    xw = crosswalk(cde, recs)
    save_json(REAL / "schools_crosswalk.json", {
        "description": "CDE CDS code <-> NCES PPIN, matched on normalized name + city (CDE district -> city).",
        "fetched_at": fetched_at,
        "source_url": ["https://www.cde.ca.gov/ds/si/ps/", "https://nces.ed.gov/surveys/pss/pssdata.asp"],
        "counts": {m: sum(1 for r in xw if r["match_method"] == m) for m in {r["match_method"] for r in xw}},
        "rows": xw,
    })
    for y in CYCLES:
        rs = [r for r in recs if r["survey_year"] == y]
        print(f"NCES {y}: {len(rs)} schools, {sum(r['total_enrollment'] or 0 for r in rs):,} students")
    print("crosswalk:", {m: sum(1 for r in xw if r["match_method"] == m) for m in {r["match_method"] for r in xw}})
    for r in xw:
        weak = [v for v in r["nces"].values() if v["score"] is not None and v["score"] < 0.95]
        if r["match_method"] != "matched" or weak:
            print("  ", r["match_method"], r["cde_name"], r["city"], {y: (v["name"], v["score"]) for y, v in r["nces"].items()})


if __name__ == "__main__":
    main()
