#!/usr/bin/env python3
"""Catholic hospitals and skilled-nursing facilities in California -> data/real/california/health.json (+ README-health.md).

Method (every record joins an official facility record to evidence that the facility is Catholic-sponsored):

  1. Facility record (address, county, coordinates, licensed beds, license): the California Dept. of Public Health
     "Licensed and Certified Healthcare Facility Listing" (CHHS open data, data.chhs.ca.gov), DATA_DATE in the file.
  2. Dignity Health (CommonSpirit Health) hospitals: the 28 hospital pages on commonspirit.org/find-a-location (the Dignity
     location sitemaps for California; each page's schema.org Hospital block gives name, phone, address, coordinates).
  3. Other Catholic systems, by an explicit curated list (CURATED below). Each entry says which source supports it:
     the Archdiocese of Los Angeles directory (data/real/california/dioceses/los-angeles/entities.json), the Diocese of
     San Bernardino's Facilities page (…/san-bernardino/entities.json), Saint Agnes Medical Center's own "History and Ministry"
     page (Trinity Health, Sisters of the Holy Cross), or only the facility's licensed name (Providence; providence.org
     answers 403 to the sandbox and to Matthew's laptop, so no Providence page was read).
  4. Diocese: `diocese_stated` only where a diocesan directory lists the facility; `diocese_by_county` is derived from the
     facility's county through the shared county index (data/real/california/dioceses.json) and is labelled as derived.

Run: python3 -I ingest/california/health.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "directories"))
import common as C  # noqa: E402

ROOT = C.ROOT
OUT = ROOT / "data" / "real" / "california" / "health.json"
README = ROOT / "data" / "real" / "california" / "README-health.md"
DIOCESES = ROOT / "data" / "real" / "california" / "dioceses.json"
DSET = "https://data.chhs.ca.gov/dataset/healthcare-facility-locations"
CS_SITEMAP = "https://www.commonspirit.org/content/dam/commonspiritorg/devops/seo/%s-locations-sitemap.xml"
CS_REGIONS = ["dhbay", "dhcco", "dhcva", "dhnst", "dhsac", "dhsoc", "dhven"]
CS_SERVICE = re.compile(r"imaging|cancer|therap|family-medical|laborator|emergency|outpatient|cardiac|infusion|hyperbaric|teleneuro|surg|urgent|clinic|"
                        r"institute|rehab|sleep|wound|medical-group|pavilion|physician|primary-care|dialysis|pharmac|hospice|home-health|diagnostic|women|"
                        r"breast|orthop|pediatric|neuro|spine|behavioral|senior|skilled|foundation|radiolog|vascular|stroke|trauma|perinatal|subacute|"
                        r"childrens|learning|outreach|plaza|bone|terrace|spirituality|lobby|connected-living", re.I)

PROV = "Providence (Providence St. Joseph Health)"
PROV_NOTE = ("The facility's licensed name carries the Providence name and its licensee is a Providence or St. Joseph Health entity "
             "(CDPH listing). providence.org answered 403 to the sandbox and to Matthew's laptop, so no Providence page was read.")
LA_SRC = "https://lacatholics.org/wp-content/uploads/2025/07/2025_ArchdioceseOfLosAngelesDirectory.pdf"

# (state FACNAME, city) -> sponsor, how we know it is Catholic
CURATED_HOSPITALS = [
    ("PROVIDENCE HOLY CROSS MEDICAL CENTER", "MISSION HILLS", PROV),
    ("PROVIDENCE LITTLE COMPANY OF MARY MEDICAL CENTER SAN PEDRO", "SAN PEDRO", PROV),
    ("PROVIDENCE LITTLE COMPANY OF MARY MEDICAL CENTER TORRANCE", "TORRANCE", PROV),
    ("PROVIDENCE SAINT JOSEPH MEDICAL CENTER", "BURBANK", PROV),
    ("SAINT JOHN'S HEALTH CENTER", "SANTA MONICA", PROV),
    ("PROVIDENCE CEDARS-SINAI TARZANA MEDICAL CENTER", "TARZANA", PROV),
    ("PROVIDENCE MISSION HOSPITAL", "MISSION VIEJO", PROV),
    ("PROVIDENCE MISSION HOSPITAL - LAGUNA BEACH", "LAGUNA BEACH", PROV),
    ("PROVIDENCE ST. JOSEPH HOSPITAL", "ORANGE", PROV),
    ("PROVIDENCE ST. JUDE MEDICAL CENTER", "FULLERTON", PROV),
    ("PROVIDENCE ST. MARY MEDICAL CENTER", "APPLE VALLEY", PROV),
    ("PROVIDENCE QUEEN OF THE VALLEY MEDICAL CENTER", "NAPA", PROV),
    ("PROVIDENCE REDWOOD MEMORIAL HOSPITAL", "FORTUNA", PROV),
    ("PROVIDENCE ST. JOSEPH HOSPITAL", "EUREKA", PROV),
    ("PROVIDENCE SANTA ROSA MEMORIAL HOSPITAL", "SANTA ROSA", PROV),
    ("SAINT AGNES MEDICAL CENTER", "FRESNO", "Trinity Health"),
    ("EMANATE HEALTH QUEEN OF THE VALLEY HOSPITAL", "WEST COVINA", "Emanate Health"),
]
# freestanding and distinct-part skilled nursing facilities of the same Catholic licensees, plus religious-order homes
CURATED_SNF = [
    ("MERCY RETIREMENT & CARE CENTER", "OAKLAND", "Sisters of Mercy (licensee: Mercy Retirement and Care Center)", None),
    ("BRUCEVILLE TERRACE - D/P SNF OF METHODIST HOSPITAL", "SACRAMENTO", "Dignity Health (CommonSpirit Health)", "Methodist Hospital of Sacramento"),
    ("NEW BETHANY", "LOS BANOS", "Franciscan Hospitaller Sisters of the Immaculate Conception (licensee)", None),
    ("MARIAN REGIONAL MEDICAL CENTER DP/SNF", "SANTA MARIA", "Dignity Health (CommonSpirit Health)", "Marian Regional Medical Center"),
    ("ST. JOHN'S HOSPITAL CAMARILLO D/P SNF", "CAMARILLO", "Dignity Health (CommonSpirit Health)", "St. John's Hospital Camarillo"),
    ("PROVIDENCE SANTA ROSA MEMORIAL HOSPITAL D/P SNF", "SANTA ROSA", PROV, "Providence Santa Rosa Memorial Hospital"),
    ("PROVIDENCE ST. ELIZABETH CARE CENTER", "NORTH HOLLYWOOD", PROV, None),
    ("PROVIDENCE LITTLE COMPANY OF MARY SUBACUTE CARE CENTER", "SAN PEDRO", PROV, None),
    ("PROVIDENCE HOLY CROSS MEDICAL CENTER D/P SNF", "MISSION HILLS", PROV, "Providence Holy Cross Medical Center"),
    ("GLENDALE MEMORIAL HOSPITAL AND HEALTH CTR. D/P SNF", "GLENDALE", "Dignity Health (CommonSpirit Health)", "Glendale Memorial Hospital and Health Center"),
    ("PROVIDENCE LITTLE COMPANY OF MARY TRANSITIONAL CARE CENTER", "TORRANCE", PROV, None),
    ("ST. JOHN OF GOD RETIREMENT AND CARE CENTER", "LOS ANGELES", "Hospitaller Brothers of St. John of God (licensee)", None),
]
# looked at and left out (reason recorded in the README)
NOT_INCLUDED = [
    ("AHMC SETON MEDICAL CENTER", "DALY CITY", "licensee AHMC (not a Catholic system); listed among hospitals in the Archdiocese of San Francisco's facility pages, which also list county and VA hospitals"),
    ("ST. FRANCIS MEDICAL CENTER", "LYNWOOD", "licensee Prime Healthcare; no source consulted calls it Catholic-sponsored today"),
    ("O'CONNOR HOSPITAL", "SAN JOSE", "licensee County of Santa Clara"),
    ("ST. LOUISE REGIONAL HOSPITAL", "GILROY", "licensee County of Santa Clara"),
    ("ST ROSE HOSPITAL", "HAYWARD", "licensee Hayward Sisters Hospital; its site (read) shows a pending sale and no sponsor statement"),
    ("SCRIPPS MERCY HOSPITAL", "SAN DIEGO", "Scripps Health (not a Catholic system)"),
    ("SCRIPPS MERCY HOSPITAL CHULA VISTA", "CHULA VISTA", "Scripps Health (not a Catholic system)"),
]


SMALL = {"of", "and", "the", "de", "las", "la", "del", "at", "in"}
KEEP_UP = {"d/p": "D/P", "dp/snf": "DP/SNF", "snf": "SNF", "ii": "II", "iii": "III", "us": "US", "ctr.": "Ctr."}


def tc(s):
    """Title-case the all-caps CDPH strings: 'PROVIDENCE LITTLE COMPANY OF MARY' -> '... Company of Mary', '34TH ST' -> '34th St'."""
    out = []
    for i, w in enumerate((s or "").lower().split(" ")):
        if w in KEEP_UP:
            out.append(KEEP_UP[w])
        elif w in SMALL and i > 0:
            out.append(w)
        elif re.fullmatch(r"\d+(st|nd|rd|th)", w):
            out.append(w)
        else:
            w = "-".join(p[:1].upper() + p[1:] for p in w.split("-"))
            out.append(re.sub(r"^(\d+)([a-z])$", lambda m: m.group(1) + m.group(2).upper(), w))
    return " ".join(out)


DISPLAY = {"AHMC SETON MEDICAL CENTER": "Seton Medical Center (AHMC)", "ST ROSE HOSPITAL": "St. Rose Hospital", "O'CONNOR HOSPITAL": "O'Connor Hospital"}


def up(s):
    return re.sub(r"[^A-Z0-9 ]", "", (s or "").upper().replace("’", "'").replace("'", ""))


def toks(s):
    stop = {"OF", "THE", "AND", "HOSPITAL", "MEDICAL", "CENTER", "HEALTH", "ST", "SAINT", "DIGNITY", "PROVIDENCE", "REGIONAL"}
    return {t for t in up(s).split() if t not in stop}


def house(addr):
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else None


def load_state(fx):
    r = fx.get(DSET)
    href = re.search(r'href="(https://data\.chhs\.ca\.gov/[^"]+/health_facility_locations\.csv)"', r.text)
    assert href, "CHHS dataset page no longer lists health_facility_locations.csv"
    csv_url = href.group(1)
    r = fx.get(csv_url)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return rows, csv_url, r.fetched_at


def commonspirit(fx):
    urls = []
    for k in CS_REGIONS:
        for x in re.findall(r"<loc>([^<]+)</loc>", fx.get(CS_SITEMAP % k).text):
            s = x.rsplit("/", 1)[1]
            if re.search(r"hospital|medical-center|memorial", s) and not CS_SERVICE.search(s):
                urls.append((k, x))
    out = []
    for k, x in urls:
        r = fx.get(x)
        for m in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', r.text, flags=re.S):
            try:
                j = json.loads(m)
            except ValueError:
                continue
            if j.get("@type") == "Hospital":
                a = j["address"]
                g = j.get("geo") or {}
                out.append({"region": k, "name": re.sub(r"\s*[-–]\s*", " - ", j["name"], count=1).replace("Dignity Health - ", "", 1), "page": x,
                            "phone": C.norm_phone(j.get("telephone")), "street": a.get("streetAddress"), "city": a.get("addressLocality"), "zip": a.get("postalCode"),
                            "lat": (g.get("latitude") or [None])[0], "lng": (g.get("longitude") or [None])[0], "fetched_at": r.fetched_at})
    return out


def find_state(rows, fac_types, name=None, city=None, street=None, zip5=None):
    cand = [r for r in rows if r["FAC_TYPE_CODE"] in fac_types]
    if name:
        cand = [r for r in cand if r["FACNAME"] == name and (not city or r["CITY"] == city)]
        return cand
    num = house(street)
    cand = [r for r in cand if r["ZIP"] == zip5 and house(r["ADDRESS"]) == num]
    return cand


def la_sb_listing():
    out = []
    for slug in ("los-angeles", "san-bernardino"):
        for e in json.loads((ROOT / "data/real/california/dioceses" / slug / "entities.json").read_text()):
            if e.get("subtype") == "hospital":
                out.append({"slug": slug, "name": e["name"], "city": e.get("city"), "sponsor": e.get("conducted_by") or e.get("sponsor"), "source_url": e.get("source_url"),
                            "page_url": e.get("page_url")})
    return out


def diocese_stated(rec, listing):
    best, score = None, 0
    for l in listing:
        if (l["city"] or "").upper() != rec["city"].upper():
            continue
        a, b = toks(rec["name"]) | toks(rec["state_name"]), toks(l["name"])
        s = len(a & b)
        if s > score:
            best, score = l, s
    if best and score >= 1:
        return {"slug": best["slug"], "listed_as": best["name"], "conducted_by": best["sponsor"], "source_url": best["source_url"]}
    return None


def base_record(r, name, typ, subtype, sponsor, county_index, csv_url, state_fetched):
    fips = r["FIPS_COUNTY_CODE"]
    rec = {
        "name": name, "state_name": r["FACNAME"], "type": typ, "subtype": subtype,
        "address": tc(r["ADDRESS"]), "city": tc(r["CITY"]), "zip": r["ZIP"], "county": tc(r["COUNTY_NAME"]), "county_fips": fips,
        "lat": float(r["LATITUDE"]) if r["LATITUDE"] else None, "lng": float(r["LONGITUDE"]) if r["LONGITUDE"] else None,
        "phone": C.norm_phone(r["CONTACT_PHONE_NUMBER"]), "phone_source": "CDPH facility contact number (may be an administrative line)",
        "website": None, "website_source": None,
        "sponsoring_system": sponsor,
        "licensed_beds": int(r["CAPACITY"]) if (r["CAPACITY"] or "").isdigit() and int(r["CAPACITY"]) > 0 else None,
        "licensed_beds_note": "Licensed capacity in the CDPH listing (not staffed or occupied beds).",
        "license": {"facid": r["FACID"], "license_number": r["LICENSE_NUMBER"] or None, "licensee": r["BUSINESS_NAME"] or None, "status": r["LICENSE_STATUS_DESCRIPTION"] or None,
                    "hcai_id": r["HCAI_ID"] or None, "ccn": r["CCN"] or None},
        "diocese_stated": None,
        "diocese_by_county": county_index.get(fips), "diocese_by_county_note": "Derived: the facility's county mapped through data/real/california/dioceses.json (each county lies in one diocese).",
        "sponsor_evidence": [], "state_source_url": csv_url, "state_fetched_at": state_fetched,
    }
    return rec


def main() -> None:
    fx = C.Fetcher("health")
    state, csv_url, state_fetched = load_state(fx)
    data_date = state[0]["DATA_DATE"]
    county_index = json.loads(DIOCESES.read_text())["county_index"]
    listing = la_sb_listing()
    recs: list[dict] = []
    problems: list[str] = []

    # ---- Dignity Health (CommonSpirit)
    cs = commonspirit(fx)
    cs_basis = {"kind": "system_page", "says": "CommonSpirit mission page: 'draws on the deep faith of our founding congregations'",
                "source_url": "https://www.commonspirit.org/about-us/mission-vision-and-values"}
    for h in cs:
        m = find_state(state, {"GACH"}, street=h["street"], zip5=h["zip"])
        if len(m) != 1:
            problems.append(f"Dignity page without a unique CDPH match: {h['name']} {h['street']} {h['zip']} -> {len(m)}")
            continue
        rec = base_record(m[0], h["name"], "hospital", "general_acute_care", "Dignity Health (CommonSpirit Health)", county_index, csv_url, state_fetched)
        if h["phone"]:
            rec["phone"], rec["phone_source"] = h["phone"], "CommonSpirit Health location page"
        rec["website"], rec["website_source"] = h["page"], "CommonSpirit Health location page for this hospital"
        if h["lat"]:
            rec["lat"], rec["lng"] = float(h["lat"]), float(h["lng"])
        rec["sponsor_evidence"] = [{"kind": "system_location_page", "says": "Hospital listed in CommonSpirit Health's California Dignity Health locations", "source_url": h["page"], "fetched_at": h["fetched_at"]}, cs_basis]
        recs.append(rec)

    # ---- other Catholic systems
    for name, city, sponsor in CURATED_HOSPITALS:
        for r in find_state(state, {"GACH"}, name=name, city=city):
            nm = tc(r["FACNAME"])
            rec = base_record(r, nm, "hospital", "general_acute_care", sponsor, county_index, csv_url, state_fetched)
            if sponsor == PROV:
                rec["sponsor_evidence"].append({"kind": "licensed_name", "says": PROV_NOTE, "source_url": csv_url})
            if sponsor == "Trinity Health":
                rec["website"], rec["website_source"] = "https://www.samc.com/", "Saint Agnes Medical Center"
                rec["sponsor_evidence"].append({"kind": "own_site", "says": "History and Ministry page: Sisters of the Holy Cross began work in Fresno in 1894; site links Trinity Health System Office",
                                                "source_url": "https://www.samc.com/about-us/history-and-ministry"})
            if sponsor == "Emanate Health":
                rec["sponsor_evidence"].append({"kind": "diocese_directory", "says": "Listed as a hospital in the Archdiocese of Los Angeles directory; Emanate Health is an independent nonprofit system",
                                                "source_url": LA_SRC})
            if r["FACNAME"].startswith("PROVIDENCE SANTA ROSA") and r["ADDRESS"].startswith("151 "):
                rec["note"] = "Second licensed campus of Providence Santa Rosa Memorial Hospital (15 licensed beds), listed separately by CDPH."
            recs.append(rec)

    # skilled nursing
    for name, city, sponsor, part_of in CURATED_SNF:
        m = find_state(state, {"SNF"}, name=name, city=city)
        if len(m) != 1:
            problems.append(f"SNF not found uniquely: {name} {city} -> {len(m)}")
            continue
        r = m[0]
        rec = base_record(r, tc(r["FACNAME"]), "skilled_nursing", "distinct_part_snf" if part_of else "freestanding_snf", sponsor, county_index, csv_url, state_fetched)
        rec["part_of"] = part_of
        rec["sponsor_evidence"].append({"kind": "licensee", "says": f"CDPH licensee: {r['BUSINESS_NAME']}", "source_url": csv_url})
        if name.startswith("MERCY RETIREMENT"):
            rec["sponsor_evidence"].append({"kind": "diocese_site_listing", "says": "Listed among the Diocese of Oakland's health-care ministries on oakdiocese.org (docs/DATA-SOURCES.md, id hc-mercy-retirement)",
                                            "source_url": "docs/DATA-SOURCES.md"})
        recs.append(rec)

    # diocese stated and ids
    for rec in recs:
        if rec["type"] == "hospital":
            rec["diocese_stated"] = diocese_stated(rec, listing)
    for rec in recs:
        kinds = {e["kind"] for e in rec["sponsor_evidence"]}
        rec["diocese_stated"] and kinds.add("diocese_directory")
        order = [("system_location_page", "system_page"), ("own_site", "own_site"), ("diocese_directory", "diocese_directory"),
                 ("diocese_site_listing", "diocese_site_listing"), ("licensed_name", "licensed_name"), ("licensee", "licensee_name")]
        rec["evidence_level"] = next((lab for k, lab in order if k in kinds), "none")
        if rec["diocese_stated"]:
            rec["sponsor_evidence"].append({"kind": "diocese_directory", "says": f"Listed by the diocese as '{rec['diocese_stated']['listed_as']}' (conducted by {rec['diocese_stated']['conducted_by']})",
                                            "source_url": rec["diocese_stated"]["source_url"]})
    for rec in recs:
        if rec.get("part_of"):
            host = next((h for h in recs if h["type"] == "hospital" and h["address"] == rec["address"] and h["zip"] == rec["zip"]), None)
            if host:
                rec["evidence_level"] = host["evidence_level"]
                rec["evidence_via_parent_hospital"] = host["name"]
    C.dedupe_ids(recs, "name")
    # an SNF that sits in a hospital's grounds inherits that hospital's evidence level, nothing more
    fetched = C.now_iso()

    # exclusions: verify they exist in the state file
    excl = []
    for name, city, why in NOT_INCLUDED:
        hit = find_state(state, {"GACH"}, name=name, city=city)
        excl.append({"state_name": name, "display_name": DISPLAY.get(name, tc(name)), "city": tc(city), "licensee": hit[0]["BUSINESS_NAME"] if hit else None, "reason": why, "in_state_listing": bool(hit)})

    hosp = [r for r in recs if r["type"] == "hospital"]
    snf = [r for r in recs if r["type"] == "skilled_nursing"]
    out = {
        "built_at": fetched,
        "about": "Catholic hospitals and skilled-nursing facilities in California: an official facility record (California Dept. of Public Health listing) joined to "
                 "evidence of Catholic sponsorship. Clinics, home health, hospice, adult day and the other 'additional sponsored services' are not included.",
        "scope": "hospital = general acute care hospital (a licence per campus); skilled_nursing = freestanding homes of Catholic licensees and the skilled-nursing units distinct-part to Catholic-system hospitals.",
        "facility_source": {"name": "CDPH Licensed and Certified Healthcare Facility Listing (CHHS open data)", "dataset_url": DSET, "file_url": csv_url, "data_date": data_date, "fetched_at": state_fetched},
        "cha_cross_check": {"source": "Catholic Health Association, 'Catholic Health Care Serving California' state fact sheet (March 2026; 2023 AHA Annual Survey)",
                            "source_url": "https://www.chausa.org/docs/default-source/advocacy/state-fact-sheets/california.pdf",
                            "acute_care_hospitals": 49, "skilled_nursing_facilities": 12, "clinics": 52, "additional_sponsored_services": 40,
                            "this_file": {"hospitals": len(hosp), "skilled_nursing": len(snf)}},
        "not_included": excl,
        "counts": {"by_type": dict(Counter(r["type"] for r in recs)), "by_system": dict(Counter(r["sponsoring_system"] for r in recs)),
                   "by_evidence_level": dict(Counter(r["evidence_level"] for r in recs)),
                   "by_diocese_by_county": dict(Counter(r["diocese_by_county"] for r in recs)), "diocese_stated": sum(1 for r in recs if r["diocese_stated"])},
        "facilities": sorted(recs, key=lambda r: (r["diocese_by_county"] or "", r["city"], r["name"])),
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    README.write_text(make_readme(out, hosp, snf, problems, cs), encoding="utf-8")
    print("hospitals", len(hosp), "snf", len(snf), "problems", problems)
    print(json.dumps(out["counts"], indent=1))
    print("not in state:", [e for e in excl if not e["in_state_listing"]])


def make_readme(out, hosp, snf, problems, cs) -> str:
    c = out["counts"]
    fmt = lambda d: ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], str(kv[0]))))  # noqa: E731
    stated = [r for r in out["facilities"] if r["diocese_stated"]]
    nostate = [r["name"] for r in hosp if not r["diocese_stated"]]
    ex = "\n".join(f"- {e['display_name']} ({e['city']}): {e['reason']}." for e in out["not_included"])
    return f"""# Catholic hospitals and skilled-nursing facilities in California

`health.json`, built {out['built_at'][:10]} by `ingest/california/health.py` (re-run: `python3 -I ingest/california/health.py`; responses cached in `data/raw/california/dioceses/health/`, gitignored).

## What is in it

{len(hosp)} hospitals and {len(snf)} skilled-nursing facilities, each joined from two things: **an official facility record** and **evidence of Catholic sponsorship**.
Every record carries `sponsor_evidence` (kind, what it says, URL) so the basis is visible; nothing is classified by guesswork. Counts by system: {fmt(c['by_system'])}. Strongest evidence per record: {fmt(c['by_evidence_level'])} (`system_page` = the system's own location page; `own_site` = the hospital's site; `diocese_directory` / `diocese_site_listing` = a diocese lists it; `licensed_name` / `licensee_name` = only the licensed name or licensee in the state file). By diocese (derived from county): {fmt(c['by_diocese_by_county'])}.

- **Facility record:** the California Dept. of Public Health "Licensed and Certified Healthcare Facility Listing" (CHHS open data portal, `{out['facility_source']['file_url']}`), DATA_DATE {out['facility_source']['data_date']}: address, county, coordinates, licensed beds, license number, licensee. `robots.txt` on data.chhs.ca.gov disallows `/api/`; the downloadable file is not disallowed and was read once. Facility administrators' names and emails in that file are not copied.
- **Dignity Health (CommonSpirit Health), {sum(1 for r in hosp if r['sponsoring_system'].startswith('Dignity'))} hospitals:** the hospital pages on `commonspirit.org/find-a-location`, found through CommonSpirit's seven California location sitemaps (`robots.txt` allows it; no Crawl-delay; 1 s between requests). Each page's `Hospital` block gives the name, main phone, address and coordinates; each page was matched to exactly one CDPH record by house number and ZIP. The mission page says "Inspired by faith" draws on "the deep faith of our founding congregations".
- **Providence, {sum(1 for r in hosp if r['sponsoring_system'].startswith('Providence'))} hospitals:** chosen by their licensed names (all begin "Providence", or are Providence Saint John's Health Center) and Providence / St. Joseph Health licensees in the CDPH file. **providence.org answers 403 to the sandbox and also to Matthew's laptop (one attempt, robots.txt and sitemap), so no Providence page was read and their `website` is empty.** Seven are also listed as hospitals in diocesan directories (Archdiocese of Los Angeles directory PDF, "conducted by Providence St. Joseph Health"; Diocese of San Bernardino's Facilities page, which calls St. Mary "operated by the Sisters of St. Joseph of Orange").
- **Saint Agnes Medical Center (Fresno), Trinity Health:** its own "History and Ministry" page (Sisters of the Holy Cross, Fresno since 1894) and a link to the Trinity Health system office.
- **Emanate Health Queen of the Valley Hospital (West Covina):** listed as a hospital in the Archdiocese of Los Angeles directory; Emanate Health is an independent nonprofit system, so its sponsor field is the system name only.
- **Skilled nursing, {len(snf)} facilities:** the licensees are Catholic systems or religious orders (Dignity, Providence, Sisters of Mercy, Franciscan Hospitaller Sisters of the Immaculate Conception, Hospitaller Brothers of St. John of God). Six are the skilled-nursing units distinct-part to a hospital (`part_of`).
- **Diocese:** `diocese_stated` is set only where a diocesan directory lists the facility ({len(stated)} records: {', '.join(sorted({r['diocese_stated']['slug'] for r in stated}))}). `diocese_by_county` is derived from the county through the shared county index and says so.

## Cross-check against the Catholic Health Association

CHA's California fact sheet (March 2026, from the 2023 AHA Annual Survey) counts **49 acute-care hospitals, 12 skilled-nursing facilities, 52 clinics and 40 additional sponsored services**. This file has **{len(hosp)} hospitals** and **{len(snf)} skilled-nursing facilities**
(a different year and definition: the survey may count campuses and facilities that have since changed hands; the sheet does not list them). Clinics and other sponsored services are out of scope here.
CHA's Catholic Health Care Directory (`site.chausa.org/catholic-health-care-directory`) requires a login and was not used.

## Looked at and left out

{ex}

## Cautions

- "Catholic" here means the sponsor is a Catholic system, a religious order, or a hospital a diocesan directory lists; it is not a canonical judgment. Ownership changes often: re-run to refresh against the CDPH file.
- Hospitals outside the Los Angeles and San Bernardino directories have no `diocese_stated`: {len(nostate)} of {len(hosp)} hospitals. Other dioceses' directories (some were still being collected) may add statements.
- `licensed_beds` is licensed capacity, not staffed beds; the file's own DATA_DATE applies.
- No numbers here are rankings; they describe where Catholic health ministries operate.
{('- Unmatched during the build: ' + '; '.join(problems)) if problems else ''}
"""


if __name__ == "__main__":
    main()
