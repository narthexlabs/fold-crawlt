"""California Dept of Education Private School Affidavit (PSA) data -> data/real/schools_cde.json

Source: https://www.cde.ca.gov/ds/si/ps/ (annual privateschooldataYYYY.xlsx, schools with 6+ students).
Keeps Catholic schools in Alameda + Contra Costa counties (the Diocese of Oakland's territory),
including schools run by religious orders (diocesan: false).

The PSA spreadsheet has no address, phone, website, administrator name or religious
affiliation. Those live in the CDE School Directory (sd.cde.ca.gov), whose export sits behind a
bot-protection wall (302 to validate.perfdrive.com), so this script does not fetch them. Address
and phone come from NCES PSS through schools_crosswalk.json; principal and website come from
schools_directory.json.

Run: python -I ingest/public/schools/cde_affidavit.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schools import (COUNTIES, DISTRICT_CITY, RAW_SCHOOLS, REAL, download, now_iso,  # noqa: E402
                      operator_for, save_json)

import openpyxl  # noqa: E402

YEARS = ["2526", "2425", "2324"]  # latest three school years posted (rev. 9/25/2026 for 2025-26)
BASE = "https://www.cde.ca.gov/ds/si/ps/documents/privateschooldata{}.xlsx"
GRADES = ["K", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]

CANDIDATE = re.compile(
    r"(?i)\b(st\.?|saint|holy|our lady|christ the king|catholic|cristo rey|de la salle|salesian|carondelet|"
    r"moreau|madeleine|corpus christi|assumption|queen of all saints|all saints|wood ?rose|bishop o.dowd|"
    r"notre dame|chesterton|immaculate)\b")
# Candidates that match the name pattern but are not Catholic (checked by hand against NCES
# affiliation codes and the schools' own sites).
NOT_CATHOLIC = {
    "01612596960694": "St Paul's Episcopal School (Episcopal)",
    "01612596904452": "St. Andrew M.B.C. (Missionary Baptist)",
    "01611506133441": "OneSchool Global (Plymouth Brethren)",
    "07618046906689": "San Ramon Valley Christian Academy (Protestant)",
    "07618046131270": "Montessori School of San Ramon (nonsectarian)",
    "01612596994107": "St. Vincent's Day Home (nonsectarian child-care nonprofit; NCES RELIG=3)",
}


def year_label(y: str) -> str:
    return f"20{y[:2]}-{y[2:]}"


def grade_span(enr: dict) -> str | None:
    have = [g for g in GRADES if (enr.get(g) or 0) > 0]
    return f"{have[0]}-{have[-1]}" if have else None


def parse_year(y: str, fetched_at: str) -> tuple[list[dict], list[dict]]:
    url = BASE.format(y)
    path = download(url, RAW_SCHOOLS / f"privateschooldata{y}.xlsx")
    ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "CDS Code")
    hdr = [str(h).strip() for h in rows[hi]]
    keep, dropped = [], []
    for r in rows[hi + 1:]:
        if not r or r[0] is None:
            continue
        d = dict(zip(hdr, r))
        if d["County"] not in COUNTIES or not CANDIDATE.search(d["School Name"] or ""):
            continue
        cds = str(d["CDS Code"]).strip().zfill(14)
        if cds in NOT_CATHOLIC:
            dropped.append({"cds_code": cds, "name": d["School Name"], "reason": NOT_CATHOLIC[cds]})
            continue
        enr = {g: d.get(f"Grade {g} Enroll") for g in GRADES}
        op = operator_for(d["School Name"])
        keep.append({
            "cds_code": cds,
            "school_year": year_label(y),
            "name": d["School Name"].strip(),
            "county": d["County"],
            "public_district": d["Public School District"],
            "city_hint": DISTRICT_CITY.get(d["Public School District"]),
            "grades": grade_span(enr),
            "enrollment_by_grade": enr,
            "total_enrollment": d["Total Enrollment"],
            "previous_year_grads": d["Previous Year Grads"],
            "ft_teachers": d["Full Time Teachers"],
            "pt_teachers": d["Part Time Teachers"],
            "administrators_count": d["Administrators"],
            "other_staff": d["Other Staff"],
            "tax_exempt_501c3": d.get("Tax Exempt 501(c)(3) - Fed"),
            "affiliation": "Roman Catholic",
            "operator": op or "Diocese of Oakland / parish",
            "diocesan": op is None,
            # Not in the PSA file; see module docstring.
            "address": None, "city": None, "zip": None, "phone": None, "website": None, "administrator": None,
            "source_url": url,
            "fetched_at": fetched_at,
        })
    return keep, dropped


def main() -> None:
    fetched_at = now_iso()
    records, dropped = [], {}
    for y in YEARS:
        k, d = parse_year(y, fetched_at)
        records += k
        for x in d:
            dropped[x["cds_code"]] = x
    records.sort(key=lambda r: (r["name"].lower(), r["school_year"]))
    out = {
        "source": "California Department of Education, Private School Affidavit data",
        "source_page": "https://www.cde.ca.gov/ds/si/ps/",
        "school_years": [year_label(y) for y in YEARS],
        "filter": "County in Alameda/Contra Costa AND Catholic (name pattern + manual exclusions)",
        "notes": [
            "PSA files cover schools with 6+ students only; enrollment is K-12 (no TK/PK column).",
            "address/city/zip/phone/website/administrator are null here: the CDE School Directory export is "
            "bot-protected. See schools_crosswalk.json (NCES address/phone) and schools_directory.json (principal, website).",
            "diocesan=false marks religious-order or independent Catholic schools (curated list in _schools.py).",
        ],
        "excluded_name_matches": sorted(dropped.values(), key=lambda x: x["name"]),
        "count_records": len(records),
        "count_schools": len({r["cds_code"] for r in records}),
        "fetched_at": fetched_at,
        "records": records,
    }
    save_json(REAL / "schools_cde.json", out)
    by_year = {}
    for r in records:
        by_year.setdefault(r["school_year"], []).append(r["total_enrollment"] or 0)
    print(f"schools_cde.json: {len(records)} school-year rows, {out['count_schools']} schools")
    for yl, v in sorted(by_year.items()):
        print(f"  {yl}: {len(v)} schools, {sum(v):,} students")


if __name__ == "__main__":
    main()
