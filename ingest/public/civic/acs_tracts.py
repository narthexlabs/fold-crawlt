#!/usr/bin/env python3
"""ACS 2020-2024 5-year estimates for every census tract in Alameda (06001) and Contra Costa (06013)
counties, plus the two counties, California, and California places (cities/CDPs).

Source: the Census Bureau's ACS *table-based Summary File* (bulk files, one per table, all geographies):
  https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/
The api.census.gov data endpoint now answers keyless requests with a redirect to missing_key.html, and no
key is available to this project yet; the Summary File carries the identical published estimates and MOEs.

Each table file is streamed and filtered to the geographies above; the filtered rows are kept in
data/raw/civic/acs/<table>.dat (gitignored). Outputs:
  data/real/civic/acs_tracts.json   per-tract estimates + MOEs (columnar)
  data/real/civic/acs_areas.json    California, the two counties, and CA places (same variables) +
                                    county-level detailed languages (B16001, not published for tracts)
  data/real/civic/acs_variables.json variable dictionary (label, ACS cells, universe)

Usage: python ingest/public/civic/acs_tracts.py [--refresh]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _civic import (OUT, RAW, SESSION, clean_est, clean_moe, download, load_json, moe_sum,  # noqa: E402
                    now_iso, save_json)

YEAR = 2024
VINTAGE = "ACS 2020-2024 5-year"
BASE = f"https://www2.census.gov/programs-surveys/acs/summary_file/{YEAR}/table-based-SF"
DATA_URL = BASE + "/data/5YRData/acsdt5y{y}-{t}.dat"
DATA_URL_1Y = BASE + "/data/1YRData/acsdt1y{y}-{t}.dat"
SHELLS_URL = BASE + f"/documentation/ACS{YEAR}5YR_Table_Shells.txt"
GEOS_URL = BASE + f"/documentation/Geos{YEAR}5YR.txt"
ACS_DIR = RAW / "acs"

KEEP = re.compile(r"^(0400000US06|0500000US06001|0500000US06013|1400000US06001|1400000US06013|1600000US06)")

TABLES = ["B01001", "B01003", "B11005", "B19013", "C17002", "B25003", "B05012", "B05006", "C16001", "C16002",
          "B03001", "B03002", "B02015", "B02016"]


def r(t, a, b=None):
    """Cells t_a..t_b (inclusive) as SF column stems."""
    b = b or a
    return [f"{t}_{i:03d}" for i in range(a, b + 1)]


# key: (label, cells summed, universe key or None)
VARS: dict[str, tuple[str, list[str], str | None]] = {
    # population and age (B01001 / B01003)
    "pop": ("Total population", r("B01003", 1), None),
    "age_under5": ("Under 5 years", r("B01001", 3) + r("B01001", 27), "pop"),
    "age_5_17": ("5 to 17 years", r("B01001", 4, 6) + r("B01001", 28, 30), "pop"),
    "age_under18": ("Under 18 years", r("B01001", 3, 6) + r("B01001", 27, 30), "pop"),
    "age_65plus": ("65 years and over", r("B01001", 20, 25) + r("B01001", 44, 49), "pop"),
    "age_75plus": ("75 years and over", r("B01001", 23, 25) + r("B01001", 47, 49), "pop"),
    # households (B11005)
    "hh": ("Households", r("B11005", 1), None),
    "hh_children": ("Households with one or more people under 18", r("B11005", 2), "hh"),
    "hh_children_single_parent": ("Family households with children, householder with no spouse present",
                                  r("B11005", 5), "hh"),
    # income and poverty
    "median_hh_income": ("Median household income (2024 dollars; 250,001 = 250,000+)", r("B19013", 1), None),
    "pov_universe": ("Population for whom poverty status is determined", r("C17002", 1), None),
    "pov_below100": ("Income below 100% of the poverty level", r("C17002", 2, 3), "pov_universe"),
    "pov_below200": ("Income below 200% of the poverty level", r("C17002", 2, 7), "pov_universe"),
    # tenure
    "occ_units": ("Occupied housing units", r("B25003", 1), None),
    "owner_occ": ("Owner-occupied housing units", r("B25003", 2), "occ_units"),
    "renter_occ": ("Renter-occupied housing units", r("B25003", 3), "occ_units"),
    # nativity and region / country of birth
    "nativity_universe": ("Total population (nativity universe)", r("B05012", 1), None),
    "foreign_born": ("Foreign-born population", r("B05012", 3), "nativity_universe"),
    "fb_europe": ("Born in Europe", r("B05006", 2), "foreign_born"),
    "fb_asia": ("Born in Asia", r("B05006", 47), "foreign_born"),
    "fb_africa": ("Born in Africa", r("B05006", 95), "foreign_born"),
    "fb_oceania": ("Born in Oceania", r("B05006", 130), "foreign_born"),
    "fb_latin_america": ("Born in Latin America", r("B05006", 139), "foreign_born"),
    "fb_northern_america": ("Born in Northern America (Canada etc.)", r("B05006", 176), "foreign_born"),
    "fb_ireland": ("Born in Ireland", r("B05006", 5), "foreign_born"),
    "fb_italy": ("Born in Italy", r("B05006", 23), "foreign_born"),
    "fb_portugal": ("Born in Portugal (incl. Azores)", r("B05006", 24), "foreign_born"),
    "fb_poland": ("Born in Poland", r("B05006", 40), "foreign_born"),
    "fb_ukraine": ("Born in Ukraine", r("B05006", 44), "foreign_born"),
    "fb_china": ("Born in China (incl. Hong Kong, Taiwan)", r("B05006", 49), "foreign_born"),
    "fb_korea": ("Born in Korea", r("B05006", 54), "foreign_born"),
    "fb_afghanistan": ("Born in Afghanistan", r("B05006", 57), "foreign_born"),
    "fb_india": ("Born in India", r("B05006", 60), "foreign_born"),
    "fb_iran": ("Born in Iran", r("B05006", 61), "foreign_born"),
    "fb_burma": ("Born in Burma (Myanmar)", r("B05006", 69), "foreign_born"),
    "fb_cambodia": ("Born in Cambodia", r("B05006", 70), "foreign_born"),
    "fb_indonesia": ("Born in Indonesia", r("B05006", 71), "foreign_born"),
    "fb_laos": ("Born in Laos", r("B05006", 72), "foreign_born"),
    "fb_philippines": ("Born in the Philippines", r("B05006", 74), "foreign_born"),
    "fb_vietnam": ("Born in Vietnam", r("B05006", 77), "foreign_born"),
    "fb_iraq": ("Born in Iraq", r("B05006", 83), "foreign_born"),
    "fb_lebanon": ("Born in Lebanon", r("B05006", 87), "foreign_born"),
    "fb_eritrea": ("Born in Eritrea", r("B05006", 97), "foreign_born"),
    "fb_ethiopia": ("Born in Ethiopia", r("B05006", 98), "foreign_born"),
    "fb_nigeria": ("Born in Nigeria", r("B05006", 124), "foreign_born"),
    "fb_fiji": ("Born in Fiji", r("B05006", 134), "foreign_born"),
    "fb_cuba": ("Born in Cuba", r("B05006", 143), "foreign_born"),
    "fb_el_salvador": ("Born in El Salvador", r("B05006", 157), "foreign_born"),
    "fb_guatemala": ("Born in Guatemala", r("B05006", 158), "foreign_born"),
    "fb_honduras": ("Born in Honduras", r("B05006", 159), "foreign_born"),
    "fb_mexico": ("Born in Mexico", r("B05006", 160), "foreign_born"),
    "fb_nicaragua": ("Born in Nicaragua", r("B05006", 161), "foreign_born"),
    "fb_brazil": ("Born in Brazil", r("B05006", 167), "foreign_born"),
    "fb_colombia": ("Born in Colombia", r("B05006", 169), "foreign_born"),
    "fb_peru": ("Born in Peru", r("B05006", 172), "foreign_born"),
    # language spoken at home, population 5+ (C16001) - 'lep' = speaks English less than "very well"
    "lang_universe": ("Population 5 years and over", r("C16001", 1), None),
    "lang_english_only": ("Speak only English at home", r("C16001", 2), "lang_universe"),
    "lang_spanish": ("Speak Spanish at home", r("C16001", 3), "lang_universe"),
    "lep_spanish": ("Speak Spanish; English less than 'very well'", r("C16001", 5), "lang_universe"),
    "lang_french_haitian_cajun": ("Speak French, Haitian or Cajun", r("C16001", 6), "lang_universe"),
    "lep_french_haitian_cajun": ("French/Haitian/Cajun; English less than 'very well'", r("C16001", 8), "lang_universe"),
    "lang_german_west_germanic": ("Speak German or other West Germanic languages", r("C16001", 9), "lang_universe"),
    "lep_german_west_germanic": ("German/W. Germanic; English less than 'very well'", r("C16001", 11), "lang_universe"),
    "lang_russian_polish_slavic": ("Speak Russian, Polish or other Slavic languages", r("C16001", 12), "lang_universe"),
    "lep_russian_polish_slavic": ("Russian/Polish/Slavic; English less than 'very well'", r("C16001", 14), "lang_universe"),
    "lang_other_indo_european": ("Speak other Indo-European languages (incl. Portuguese, Italian, Hindi, Punjabi, Persian)",
                                 r("C16001", 15), "lang_universe"),
    "lep_other_indo_european": ("Other Indo-European; English less than 'very well'", r("C16001", 17), "lang_universe"),
    "lang_korean": ("Speak Korean", r("C16001", 18), "lang_universe"),
    "lep_korean": ("Korean; English less than 'very well'", r("C16001", 20), "lang_universe"),
    "lang_chinese": ("Speak Chinese (incl. Mandarin, Cantonese)", r("C16001", 21), "lang_universe"),
    "lep_chinese": ("Chinese; English less than 'very well'", r("C16001", 23), "lang_universe"),
    "lang_vietnamese": ("Speak Vietnamese", r("C16001", 24), "lang_universe"),
    "lep_vietnamese": ("Vietnamese; English less than 'very well'", r("C16001", 26), "lang_universe"),
    "lang_tagalog": ("Speak Tagalog (incl. Filipino)", r("C16001", 27), "lang_universe"),
    "lep_tagalog": ("Tagalog; English less than 'very well'", r("C16001", 29), "lang_universe"),
    "lang_other_asian_pacific": ("Speak other Asian and Pacific Island languages (incl. Tongan, Samoan, Ilocano, Khmer, Hmong, Japanese, Tamil)",
                                 r("C16001", 30), "lang_universe"),
    "lep_other_asian_pacific": ("Other Asian & Pacific Island; English less than 'very well'", r("C16001", 32), "lang_universe"),
    "lang_arabic": ("Speak Arabic", r("C16001", 33), "lang_universe"),
    "lep_arabic": ("Arabic; English less than 'very well'", r("C16001", 35), "lang_universe"),
    "lang_other_unspecified": ("Speak other and unspecified languages (incl. Amharic, Tigrinya, African languages)",
                               r("C16001", 36), "lang_universe"),
    "lep_other_unspecified": ("Other/unspecified; English less than 'very well'", r("C16001", 38), "lang_universe"),
    # limited-English-speaking households (C16002)
    "hh_lang_universe": ("Households (language universe)", r("C16002", 1), None),
    "lehh_spanish": ("Limited-English-speaking households, Spanish", r("C16002", 4), "hh_lang_universe"),
    "lehh_other_indo_european": ("Limited-English-speaking households, other Indo-European", r("C16002", 7), "hh_lang_universe"),
    "lehh_asian_pacific": ("Limited-English-speaking households, Asian & Pacific Island languages", r("C16002", 10), "hh_lang_universe"),
    "lehh_other": ("Limited-English-speaking households, other languages", r("C16002", 13), "hh_lang_universe"),
    "lehh_total": ("Limited-English-speaking households, all languages", r("C16002", 4) + r("C16002", 7) + r("C16002", 10) + r("C16002", 13), "hh_lang_universe"),
    # Hispanic origin (B03001)
    "hisp_universe": ("Total population (Hispanic origin universe)", r("B03001", 1), None),
    "hispanic": ("Hispanic or Latino", r("B03001", 3), "hisp_universe"),
    "hisp_mexican": ("Mexican", r("B03001", 4), "hisp_universe"),
    "hisp_puerto_rican": ("Puerto Rican", r("B03001", 5), "hisp_universe"),
    "hisp_cuban": ("Cuban", r("B03001", 6), "hisp_universe"),
    "hisp_central_american": ("Central American", r("B03001", 8), "hisp_universe"),
    "hisp_guatemalan": ("Guatemalan", r("B03001", 10), "hisp_universe"),
    "hisp_honduran": ("Honduran", r("B03001", 11), "hisp_universe"),
    "hisp_nicaraguan": ("Nicaraguan", r("B03001", 12), "hisp_universe"),
    "hisp_salvadoran": ("Salvadoran", r("B03001", 14), "hisp_universe"),
    "hisp_south_american": ("South American", r("B03001", 16), "hisp_universe"),
    "hisp_colombian": ("Colombian", r("B03001", 20), "hisp_universe"),
    "hisp_peruvian": ("Peruvian", r("B03001", 23), "hisp_universe"),
    "hisp_other": ("Other Hispanic or Latino", r("B03001", 27), "hisp_universe"),
    # race and Hispanic origin (B03002)
    "nh_white": ("Not Hispanic, White alone", r("B03002", 3), "hisp_universe"),
    "nh_black": ("Not Hispanic, Black or African American alone", r("B03002", 4), "hisp_universe"),
    "nh_aian": ("Not Hispanic, American Indian and Alaska Native alone", r("B03002", 5), "hisp_universe"),
    "nh_asian": ("Not Hispanic, Asian alone", r("B03002", 6), "hisp_universe"),
    "nh_nhpi": ("Not Hispanic, Native Hawaiian and Other Pacific Islander alone", r("B03002", 7), "hisp_universe"),
    "nh_other": ("Not Hispanic, some other race alone", r("B03002", 8), "hisp_universe"),
    "nh_two_plus": ("Not Hispanic, two or more races", r("B03002", 9), "hisp_universe"),
    # detailed Asian groups, Asian alone (B02015)
    "asian_alone": ("Asian alone (detailed-group universe)", r("B02015", 1), None),
    "asian_chinese": ("Chinese, except Taiwanese (Asian alone)", r("B02015", 2), "asian_alone"),
    "asian_hmong": ("Hmong (Asian alone)", r("B02015", 3), "asian_alone"),
    "asian_japanese": ("Japanese (Asian alone)", r("B02015", 4), "asian_alone"),
    "asian_korean": ("Korean (Asian alone)", r("B02015", 5), "asian_alone"),
    "asian_taiwanese": ("Taiwanese (Asian alone)", r("B02015", 8), "asian_alone"),
    "asian_burmese": ("Burmese (Asian alone)", r("B02015", 10), "asian_alone"),
    "asian_cambodian": ("Cambodian (Asian alone)", r("B02015", 11), "asian_alone"),
    "asian_filipino": ("Filipino (Asian alone)", r("B02015", 12), "asian_alone"),
    "asian_indonesian": ("Indonesian (Asian alone)", r("B02015", 13), "asian_alone"),
    "asian_laotian": ("Laotian (Asian alone)", r("B02015", 14), "asian_alone"),
    "asian_mien": ("Mien (Asian alone)", r("B02015", 16), "asian_alone"),
    "asian_thai": ("Thai (Asian alone)", r("B02015", 18), "asian_alone"),
    "asian_vietnamese": ("Vietnamese (Asian alone)", r("B02015", 19), "asian_alone"),
    "asian_indian": ("Asian Indian (Asian alone)", r("B02015", 21), "asian_alone"),
    "asian_nepalese": ("Nepalese (Asian alone)", r("B02015", 24), "asian_alone"),
    "asian_pakistani": ("Pakistani (Asian alone)", r("B02015", 25), "asian_alone"),
    "asian_sikh": ("Sikh (Asian alone)", r("B02015", 26), "asian_alone"),
    "asian_sri_lankan": ("Sri Lankan (Asian alone)", r("B02015", 27), "asian_alone"),
    "asian_afghan": ("Afghan (Asian alone)", r("B02015", 29), "asian_alone"),
    # detailed Pacific Islander groups, NHPI alone (B02016)
    "nhpi_alone": ("Native Hawaiian and Other Pacific Islander alone (detailed-group universe)", r("B02016", 1), None),
    "nhpi_native_hawaiian": ("Native Hawaiian (NHPI alone)", r("B02016", 2), "nhpi_alone"),
    "nhpi_samoan": ("Samoan (NHPI alone)", r("B02016", 3), "nhpi_alone"),
    "nhpi_tongan": ("Tongan (NHPI alone)", r("B02016", 4), "nhpi_alone"),
    "nhpi_chamorro": ("Chamorro (NHPI alone)", r("B02016", 6), "nhpi_alone"),
    "nhpi_marshallese": ("Marshallese (NHPI alone)", r("B02016", 9), "nhpi_alone"),
    "nhpi_fijian": ("Fijian (NHPI alone)", r("B02016", 11), "nhpi_alone"),
}

# B16001 (detailed languages): 5-year file covers only nation/states, so counties and large cities use ACS 2024 1-year.
B16001_LANGS = {
    "Spanish": 3, "French (incl. Cajun)": 6, "Haitian": 9, "Italian": 12, "Portuguese": 15, "German": 18,
    "Greek": 24, "Russian": 27, "Polish": 30, "Serbo-Croatian": 33, "Ukrainian or other Slavic": 36,
    "Armenian": 39, "Persian (incl. Farsi, Dari)": 42, "Gujarati": 45, "Hindi": 48, "Urdu": 51, "Punjabi": 54,
    "Bengali": 57, "Nepali, Marathi, or other Indic": 60, "Other Indo-European": 63, "Telugu": 66, "Tamil": 69,
    "Malayalam, Kannada, or other Dravidian": 72, "Chinese (incl. Mandarin, Cantonese)": 75, "Japanese": 78,
    "Korean": 81, "Hmong": 84, "Vietnamese": 87, "Khmer": 90, "Thai, Lao, or other Tai-Kadai": 93,
    "Other languages of Asia": 96, "Tagalog (incl. Filipino)": 99,
    "Ilocano, Samoan, Hawaiian, or other Austronesian": 102, "Arabic": 105, "Hebrew": 108,
    "Amharic, Somali, or other Afro-Asiatic": 111, "Yoruba, Twi, Igbo, or other W. African": 114,
    "Swahili or other Central/E./S. African": 117, "Other and unspecified": 126,
}


def fetch_table(t: str, refresh: bool, one_year: bool = False) -> Path:
    dest = ACS_DIR / (f"{t.lower()}_1y.dat" if one_year else f"{t.lower()}.dat")
    if dest.exists() and not refresh:
        return dest
    url = (DATA_URL_1Y if one_year else DATA_URL).format(y=YEAR, t=t.lower())
    ACS_DIR.mkdir(parents=True, exist_ok=True)
    kept = 0
    with SESSION.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(dest.with_suffix(".part"), "w", encoding="utf-8") as f:
            first = True
            for raw in resp.iter_lines(chunk_size=1 << 20):
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                if first:
                    f.write(line + "\n")
                    first = False
                    continue
                if line and KEEP.match(line):
                    f.write(line + "\n")
                    kept += 1
    dest.with_suffix(".part").replace(dest)
    print(f"  {t}: kept {kept} rows", flush=True)
    return dest


def fetch_geos(refresh: bool) -> dict[str, dict]:
    dest = ACS_DIR / "geos_ca.txt"
    if not dest.exists() or refresh:
        full = download(GEOS_URL, ACS_DIR / "geos_full.txt", refresh=refresh)
        with open(full, encoding="utf-8", errors="replace") as fi, open(dest, "w", encoding="utf-8") as fo:
            header = fi.readline()
            fo.write(header)
            for line in fi:
                parts = line.split("|")
                if len(parts) > 41 and KEEP.match(parts[40]):
                    fo.write(line)
    geos = {}
    with open(dest, encoding="utf-8") as f:
        cols = f.readline().rstrip("\n").split("|")
        for line in f:
            p = dict(zip(cols, line.rstrip("\n").split("|")))
            geos[p["GEO_ID"]] = {"name": p["NAME"], "sumlevel": p["SUMLEVEL"], "county": p["COUNTY"],
                                 "place": p["PLACE"], "tract": p["TRACT"]}
    return geos


def read_table(path: Path) -> dict[str, dict[str, tuple]]:
    """GEO_ID -> {stem 'B01001_003': (est, moe)}"""
    out: dict[str, dict[str, tuple]] = {}
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\n").split("|")
        for line in f:
            vals = line.rstrip("\n").split("|")
            gid = vals[0]
            row = out.setdefault(gid, {})
            for c, v in zip(cols[1:], vals[1:]):
                m = re.match(r"^([A-Z0-9]+)_([EM])(\d{3})$", c)
                if not m:
                    continue
                stem = f"{m.group(1)}_{m.group(3)}"
                num = float(v) if v not in ("", ".") else None
                e, mo = row.get(stem, (None, None))
                if m.group(2) == "E":
                    row[stem] = (num, mo)
                else:
                    row[stem] = (e, num)
    return out


def compute(cells: dict[str, tuple], stems: list[str], is_median: bool = False):
    parts = []
    for s in stems:
        if s not in cells:
            return None, None
        e, m = cells[s]
        e, m = clean_est(e), clean_moe(m)
        if e is None:
            return None, None
        parts.append((e, m))
    if is_median:
        return parts[0]
    est = sum(e for e, _ in parts)
    return est, moe_sum(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    fetched_at = now_iso()
    print("tables:", flush=True)
    data: dict[str, dict] = {}
    for t in TABLES:
        p = fetch_table(t, args.refresh)
        for gid, cells in read_table(p).items():
            data.setdefault(gid, {}).update(cells)
    # B16001 (detailed languages) is published in the 5-year file only for the nation and states, so the
    # county / large-city detail comes from the 2024 1-year file (areas of 65,000+ people).
    b16_1y = read_table(fetch_table("B16001", args.refresh, one_year=True))
    download(SHELLS_URL, ACS_DIR / f"ACS{YEAR}5YR_Table_Shells.txt", refresh=args.refresh)
    geos = fetch_geos(args.refresh)

    # Places: only those that overlap the two counties (list written by geometry.py).
    pl = load_json(OUT / "places_diocese.json", {}) or {}
    place_ids = {p["geoid"] for p in pl.get("places", [])}
    if not place_ids:
        print("note: places_diocese.json missing - run geometry.py first; places skipped")

    keys = list(VARS)
    rows_t, rows_a = [], []
    for gid, cells in data.items():
        if gid.startswith("1600000US") and gid.split("US")[1] not in place_ids:
            continue
        g = geos.get(gid, {})
        vals = []
        for k in keys:
            label, stems, _u = VARS[k]
            est, moe = compute(cells, stems, is_median=(k == "median_hh_income"))
            vals += [None if est is None else round(est), None if moe is None else round(moe)]
        rec = [gid.split("US")[1], g.get("name")] + vals
        if gid.startswith("1400000US"):
            rows_t.append(rec)
        else:
            lang = {}
            c1 = b16_1y.get(gid)
            if c1:
                for lname, line in B16001_LANGS.items():
                    e, m = compute(c1, [f"B16001_{line:03d}"])
                    e2, m2 = compute(c1, [f"B16001_{line + 2:03d}"])
                    if e is not None:
                        lang[lname] = {"speakers": round(e), "speakers_moe": None if m is None else round(m),
                                       "lep": None if e2 is None else round(e2), "lep_moe": None if m2 is None else round(m2)}
            rows_a.append({"geoid": gid.split("US")[1], "geo_id": gid, "name": g.get("name"),
                           "sumlevel": g.get("sumlevel"),
                           "values": {k: [rec[2 + 2 * i], rec[3 + 2 * i]] for i, k in enumerate(keys)},
                           **({"b16001_detailed_languages_acs1y2024": lang} if lang else {})})
    rows_t.sort(key=lambda x: x[0])
    rows_a.sort(key=lambda x: (x["sumlevel"], x["geoid"]))

    columns = ["geoid", "name"] + [c for k in keys for c in (k, k + "_moe")]
    meta = {
        "vintage": VINTAGE,
        "source": "U.S. Census Bureau, American Community Survey 5-year estimates, table-based Summary File",
        "source_url": BASE + "/data/5YRData/",
        "table_urls": {t: DATA_URL.format(y=YEAR, t=t.lower()) for t in TABLES},
        "b16001_1year_url": DATA_URL_1Y.format(y=YEAR, t="b16001"),
        "geography_file_url": GEOS_URL,
        "table_shells_url": SHELLS_URL,
        "fetched_at": fetched_at,
        "geography": "2020 census tracts (ACS 2024 uses 2020 tract geography)",
        "moe_note": ("Margins of error are at the 90% confidence level as published. Derived sums use the "
                     "root-sum-of-squares approximation (ACS General Handbook, ch. 8). median_hh_income is the "
                     "published tract median and cannot be summed."),
        "special_values": "null = not available (e.g. median suppressed for too few households); 250001 = 250,000+",
        "why_summary_file": ("api.census.gov now redirects keyless data requests to missing_key.html; the Summary File "
                             "is the Bureau's bulk release of the same estimates."),
    }
    save_json(OUT / "acs_tracts.json", {"meta": {**meta, "count": len(rows_t)}, "columns": columns, "rows": rows_t},
              compact=True)
    save_json(OUT / "acs_areas.json", {"meta": {**meta, "count": len(rows_a),
                                                "note": "California (040), Alameda and Contra Costa counties (050), and the places (160: cities and CDPs) that overlap the two counties. b16001_detailed_languages_acs1y2024 = ACS 2024 1-year table B16001 (detailed languages; published only for areas of 65,000+), a different vintage from the 5-year values."},
                                       "areas": rows_a}, compact=True)
    save_json(OUT / "acs_variables.json", {"meta": meta, "variables": [
        {"key": k, "label": VARS[k][0], "acs_cells": VARS[k][1], "universe": VARS[k][2]} for k in keys]})
    print(f"tracts: {len(rows_t)}  areas: {len(rows_a)}")


if __name__ == "__main__":
    main()
