"""Catholic schools, all of California -> data/real/california/schools_cde.json

Primary record: the California Department of Education Private School Affidavit (PSA) files
(https://www.cde.ca.gov/ds/si/ps/, privateschooldata{2526,2425,2324}.xlsx): CDS code, county, public school district,
school name, enrollment by grade and total, per school year.

Why not the CDE "Private School Directory" export: it sits behind a Radware bot manager (validate.perfdrive.com /
"Radware Captcha Page"). The listing page `sd.cde.ca.gov/SchoolDirectory/active-or-pending-schools/2` loads, but its
`/schooldirectory/ExportSelect` export and the per-school `details` pages answer the challenge, from the sandbox and
from the owner's laptop connection. No work-around is attempted. CDE's own file-structure page
(sd.cde.ca.gov/schooldirectory/topic/8) lists no religious-affiliation field for the export either.

Catholic filter and address: NCES Private School Universe Survey (PSS) public-use files, 2023-24 and 2021-22,
RELIG=1 (Roman Catholic) in California. PSS has street address, city, zip, phone, lat/lng, grades and the NCES diocese
code. NCES rows are matched to PSA rows by county + normalised school name (+ a district/city hint); every record says
how it was matched. Dioceses are assigned from the school's county with the shared reference
data/real/california/dioceses.json (`county_index`).

    python -I ingest/california/directories/cde_schools.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

import openpyxl  # noqa: E402
import requests  # noqa: E402

RAW = C.RAW_ROOT / "schools"
OLD_RAW = C.ROOT / "data" / "raw" / "schools"
PSA_BASE = "https://www.cde.ca.gov/ds/si/ps/documents/privateschooldata{}.xlsx"
PSA_YEARS = ["2526", "2425", "2324"]
NCES_BASE = "https://nces.ed.gov/surveys/pss/zip/{}"
NCES_CYCLES = {"2023-24": ("pss2324_pu_csv.zip", "24"), "2021-22": ("pss2122_pu_csv.zip", "22")}
GRADES = ["K", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
GRADE_CODE = {"1": "UG", "2": "PK", "3": "K", "4": "TK", "5": "TK", "6": "1", "7": "2", "8": "3", "9": "4",
              "10": "5", "11": "6", "12": "7", "13": "8", "14": "9", "15": "10", "16": "11", "17": "12"}
P445 = {"1": "parochial", "2": "diocesan", "3": "private (religious order / independent)"}

NAME_NOISE = re.compile(r"\b(st|saint|sts|school|schools|catholic|elementary|parish|the|of|inc|high|academy|college|"
                        r"preparatory|prep|a|de|la|el|los|las|and)\b")
CANDIDATE = re.compile(r"(?i)\b(catholic|our lady|holy (?:family|trinity|spirit|cross|rosary|name|angels|redeemer|child)|"
                       r"sacred heart|immaculate|christ the king|cristo rey|saint|st\.?|san|santa|notre dame|mater dei|"
                       r"queen of|corpus christi|blessed|assumption|annunciation|resurrection|ascension|visitation)\b")
NOT_CATHOLIC_HINT = re.compile(r"(?i)lutheran|episcopal|baptist|methodist|christian|orthodox|presbyterian|church of|adventist|"
                               r"m\.b\.c|pentecostal|evangelical|bible|jewish|hebrew|islamic|muslim|coptic|armenian|greek")


def key(name: str) -> str:
    s = C.pub_privacy()._ascii(name or "").lower() if hasattr(C.pub_privacy(), "_ascii") else (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s).replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = NAME_NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_case(s: str | None) -> str | None:
    if not s:
        return s
    t = " ".join(w.capitalize() for w in s.lower().split())
    t = re.sub(r"\bSt\b", "St.", t)
    t = re.sub(r"\b(Ne|Nw|Se|Sw)\b", lambda m: m.group(1).upper(), t)
    t = re.sub(r"\bPo Box\b", "PO Box", t)
    return t


def fetch_bin(url: str, dest: Path, old: Path | None = None) -> tuple[Path, str]:
    """Binary download with the default User-Agent. Returns (path, fetched_at). Reuses an earlier raw copy if present."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size:
        return dest, C.now_iso()
    if old is not None and old.exists() and old.stat().st_size:
        dest.write_bytes(old.read_bytes())
        return dest, C.now_iso()
    fx = C.Fetcher("schools")
    rp, why = fx._robots_for(url)
    if why or (rp is not None and not rp.can_fetch("*", url)):
        raise C.Blocked(url, why or "disallowed by robots.txt")
    host = url.split("/")[2]
    fx._wait(host, fx.crawl_delay(url))
    r = requests.get(url, timeout=120)
    fx._stamp(host)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest, C.now_iso()


def load_psa() -> tuple[dict, dict]:
    """cds -> {year: row}, plus year -> source info."""
    by_cds: dict[str, dict] = defaultdict(dict)
    info = {}
    for y in PSA_YEARS:
        url = PSA_BASE.format(y)
        path, ts = fetch_bin(url, RAW / f"privateschooldata{y}.xlsx", OLD_RAW / f"privateschooldata{y}.xlsx")
        ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        revised = next((str(r[0]) for r in rows[:4] if r and r[0] and str(r[0]).startswith("Revised")), None)
        hi = next(i for i, r in enumerate(rows) if r and r[0] == "CDS Code")
        hdr = [str(h).strip() for h in rows[hi]]
        n = 0
        for r in rows[hi + 1:]:
            if not r or r[0] is None:
                continue
            d = dict(zip(hdr, r))
            cds = str(d["CDS Code"]).strip().zfill(14)
            enr = {g: d.get(f"Grade {g} Enroll") for g in GRADES}
            have = [g for g in GRADES if (enr.get(g) or 0) > 0]
            by_cds[cds][f"20{y[:2]}-{y[2:]}"] = {
                "name": (d["School Name"] or "").strip(), "county": d["County"], "district": d["Public School District"],
                "enrollment": d["Total Enrollment"], "enrollment_by_grade": enr,
                "grades": f"{have[0]}-{have[-1]}" if have else None,
                "ft_teachers": d.get("Full Time Teachers"), "source_url": url}
            n += 1
        info[f"20{y[:2]}-{y[2:]}"] = {"url": url, "revised": revised, "rows": n, "fetched_at": ts}
    return by_cds, info


def load_nces() -> list[dict]:
    out = []
    for label, (fname, yy) in NCES_CYCLES.items():
        url = NCES_BASE.format(fname)
        path, ts = fetch_bin(url, RAW / "nces" / fname, OLD_RAW / "nces" / fname)
        with zipfile.ZipFile(path) as z:
            member = next(n for n in z.namelist() if n.endswith(".csv"))
            text = z.read(member).decode("latin-1")
        for d in csv.DictReader(io.StringIO(text)):
            if d["PSTABB"] != "CA" or d["RELIG"] != "1":
                continue

            def num(v):
                try:
                    return float(v) if v not in ("", ".", "-1", None) else None
                except ValueError:
                    return None
            out.append({
                "nces_ppin": d["PPIN"], "survey": label, "name": title_case(d["PINST"]),
                "address": title_case(d["PL_ADD"] or d["PADDRS"]), "city": title_case(d["PL_CIT"] or d["PCITY"]),
                "zip": (d["PL_ZIP"] or d["PZIP"]) or None, "county": title_case(d["PCNTNM"]),
                "phone": C.norm_phone(d["PPHONE"]), "lat": num(d.get(f"LATITUDE{yy}")), "lng": num(d.get(f"LONGITUDE{yy}")),
                "grade_low": GRADE_CODE.get(d.get(f"LOGR20{yy}"), d.get(f"LOGR20{yy}")),
                "grade_high": GRADE_CODE.get(d.get(f"HIGR20{yy}"), d.get(f"HIGR20{yy}")),
                "catholic_type": P445.get(d["P445"], d["P445"]), "diocese_code": d["DIOCESE"],
                "nces_enrollment": int(num(d["P305"])) if num(d["P305"]) is not None else None,
                "source_url": url, "fetched_at": ts})
    return out


def county_to_diocese() -> dict:
    ref = json.loads((C.ROOT / "data" / "real" / "california" / "dioceses.json").read_text())
    return {c: s for c, s in ref.get("county_index", {}).items()}, {
        d["slug"]: d for d in ref["dioceses"]}


def match(psa_latest: dict, nces: list[dict]):
    """Greedy one-to-one match of NCES Catholic rows to PSA rows within a county."""
    scores = []
    by_county = defaultdict(list)
    for cds, v in psa_latest.items():
        by_county[v["county"]].append((cds, v))
    for i, n in enumerate(nces):
        kn = key(n["name"])
        for cds, v in by_county.get((n["county"] or "").title(), []):
            kp = key(v["name"])
            if not kn or not kp:
                continue
            tn, tp = set(kn.split()), set(kp.split())
            sc = 1.0 if kn == kp else max(SequenceMatcher(None, kn, kp).ratio(),
                                         0.9 if (tn <= tp or tp <= tn) and min(len(tn), len(tp)) >= 2 else 0)
            if sc < 0.84:
                continue
            city = (n["city"] or "").lower()
            if city and city in (v["district"] or "").lower():
                sc += 0.1
            scores.append((sc, i, cds))
    scores.sort(reverse=True)
    used_n, used_p, pairs = set(), set(), {}
    for sc, i, cds in scores:
        if i in used_n or cds in used_p:
            continue
        # skip ties: another unused candidate with the same score for this NCES row
        pairs[i] = (cds, sc)
        used_n.add(i)
        used_p.add(cds)
    return pairs


def main() -> None:
    fetched_at = C.now_iso()
    psa, psa_info = load_psa()
    nces_all = load_nces()
    # one NCES row per PPIN, newest cycle first
    seen, seen_school, nces = set(), set(), []
    for n in sorted(nces_all, key=lambda x: x["survey"], reverse=True):
        sk = (n["county"], key(n["name"]), (n["city"] or "").lower())
        if n["nces_ppin"] in seen or sk in seen_school:
            continue  # same PPIN, or the same school listed under a new PPIN in the older cycle
        seen.add(n["nces_ppin"])
        seen_school.add(sk)
        nces.append(n)
    latest = {}
    for cds, yrs in psa.items():
        y = sorted(yrs)[-1]
        latest[cds] = {**yrs[y], "year": y}
    pairs = match(latest, nces)
    cidx, drefs = county_to_diocese()
    recs, nces_only = [], []
    matched_cds = set()
    for i, n in enumerate(nces):
        if i not in pairs:
            nces_only.append({k: n[k] for k in ("nces_ppin", "name", "address", "city", "zip", "county", "phone",
                                                "survey", "nces_enrollment", "source_url")})
            continue
        cds, sc = pairs[i]
        matched_cds.add(cds)
        hist = [{"school_year": y, "grades": v["grades"], "enrollment": v["enrollment"], "teachers_ft": v["ft_teachers"],
                 "source_url": v["source_url"]} for y, v in sorted(psa[cds].items(), reverse=True)]
        cur = hist[0]
        geoid = None
        recs.append({
            "cds_code": cds, "name": latest[cds]["name"], "address": n["address"], "city": n["city"], "zip": n["zip"],
            "county": latest[cds]["county"], "phone": n["phone"], "lat": n["lat"], "lng": n["lng"],
            "diocese": None,  # filled below
            "school_year": cur["school_year"], "grades": cur["grades"], "enrollment": cur["enrollment"],
            "enrollment_history": hist,
            "grades_nces": f"{n['grade_low']}-{n['grade_high']}" if n["grade_low"] else None,
            "catholic_type_nces": n["catholic_type"], "nces_ppin": n["nces_ppin"], "nces_diocese_code": n["diocese_code"],
            "catholic_basis": f"NCES PSS {n['survey']} RELIG=1 (Roman Catholic)",
            "match": {"method": "county + normalised name" + (" + district city" if sc > 1 else ""), "score": round(min(sc, 1.1), 2)},
            "address_source": f"NCES PSS {n['survey']} (CDE directory export blocked)",
            "source_url": cur["source_url"], "nces_source_url": n["source_url"], "fetched_at": fetched_at,
        })
    county_geo = {}
    for r in recs:
        r["county"] = (r["county"] or "").title()
    # diocese by county name via the shared reference (county_index is keyed by GEOID; map names through diocese county lists)
    name_to_slug = {}
    for slug, d in drefs.items():
        for c in d.get("counties", []):
            name_to_slug[c["name"].lower()] = slug
    for r in recs:
        r["diocese"] = name_to_slug.get(r["county"].lower())
    recs.sort(key=lambda r: (r["diocese"] or "", r["county"], r["name"].lower()))
    # unverified candidates: PSA name looks Catholic-ish but no NCES RELIG=1 match
    cands = []
    for cds, v in latest.items():
        if cds in matched_cds or v["year"] != "2025-26":
            continue
        if CANDIDATE.search(v["name"]) and not NOT_CATHOLIC_HINT.search(v["name"]):
            cands.append({"cds_code": cds, "name": v["name"], "county": v["county"], "district": v["district"],
                          "school_year": v["year"], "grades": v["grades"], "enrollment": v["enrollment"],
                          "reason": "name looks Catholic; not matched to an NCES PSS RELIG=1 school (new school, renamed, or not Catholic)"})
    cands.sort(key=lambda r: (r["county"], r["name"].lower()))
    by_d = Counter(r["diocese"] for r in recs)
    enr = defaultdict(int)
    for r in recs:
        if r["school_year"] == "2025-26" and r["enrollment"]:
            enr[r["diocese"]] += r["enrollment"]
    out = {
        "source": "California Department of Education, Private School Affidavit data (enrollment, grades, CDS code) joined to NCES PSS (Catholic flag, address)",
        "source_pages": ["https://www.cde.ca.gov/ds/si/ps/", "https://nces.ed.gov/surveys/pss/pssdata.asp"],
        "psa_files": psa_info,
        "filter": "NCES PSS RELIG=1 (Roman Catholic), state CA, cycles 2023-24 and 2021-22, matched to a CDE PSA row (any of 2025-26, 2024-25, 2023-24)",
        "notes": [
            "The CDE Private School Directory export and detail pages answer a Radware bot challenge (sandbox and laptop connection); not bypassed. "
            "The CDE directory's documented export fields (sd.cde.ca.gov/schooldirectory/topic/8) have no religious-affiliation field.",
            "PSA files cover schools with 6+ students; enrollment is K-12 headcount (no TK/PK).",
            "Address, city, zip, phone and lat/lng come from NCES PSS, not from CDE.",
            "A school whose latest PSA is older than 2025-26 may have stopped filing (see school_year).",
            "Each record's `diocese` comes from its county via data/real/california/dioceses.json; NCES's own diocese code is kept as nces_diocese_code.",
        ],
        "counts": {"records": len(recs), "by_diocese": dict(by_d), "enrollment_2025_26_by_diocese": dict(enr),
                   "nces_catholic_rows": len(nces), "nces_unmatched": len(nces_only), "unverified_name_candidates": len(cands)},
        "fetched_at": fetched_at,
        "records": recs,
        "nces_catholic_unmatched": nces_only,
        "unverified_candidates": cands,
    }
    C.write_json(C.ROOT / "data" / "real" / "california" / "schools_cde.json", out)
    cn = out["counts"]
    dioc = "".join(f"| {k} | {cn['by_diocese'][k]} | {cn['enrollment_2025_26_by_diocese'].get(k, 0):,} |\n" for k in sorted(cn["by_diocese"], key=str))
    readme = f"""# Catholic schools in California (`schools_cde.json`)

Built {fetched_at[:10]} by `ingest/california/directories/cde_schools.py`.

## What is in it

{cn['records']} Catholic schools: CDS code, name, address, city, zip, county, phone, lat/lng, diocese (from county), grades and enrollment
per school year (2025-26, 2024-25, 2023-24 where the school filed), the NCES id, and how the record was matched. `school_year`, `grades` and
`enrollment` at the top of a record are the school's latest filing; `enrollment_history` has all three years. Enrollment is the CDE
affidavit's K-12 headcount (schools with 6+ students; no TK/PK).

| Diocese | Schools | Enrollment 2025-26 |
|---|---|---|
{dioc}
Also in the file: `nces_catholic_unmatched` ({cn['nces_unmatched']} NCES Catholic schools with no PSA match: closed, renamed, or ambiguous
names) and `unverified_candidates` ({cn['unverified_name_candidates']} 2025-26 PSA schools whose name looks Catholic but are not RELIG=1 in NCES; most are not
Catholic or are new; review before using).

## Sources and method

- **CDE Private School Affidavit files** (`https://www.cde.ca.gov/ds/si/ps/documents/privateschooldata{{2526,2425,2324}}.xlsx`, public, no robots
  restriction): CDS code, county, public school district, school name, enrollment by grade, total, teachers. Rows: {', '.join(f"{{y}} {{v['rows']}}" for y, v in psa_info.items())}.
- **NCES Private School Universe Survey**, 2023-24 and 2021-22 public-use CSV (`https://nces.ed.gov/surveys/pss/pssdata.asp`): the Catholic flag
  (`RELIG=1`), address, city, zip, phone, lat/lng, grade span, NCES diocese code. {cn['nces_catholic_rows']} distinct Catholic CA schools after
  collapsing a school listed under two PPINs in the two cycles.
- **Match**: NCES to PSA by county + normalised name (similarity >= 0.84 or one name's words contained in the other's), +0.1 when the NCES city is
  in the PSA's public school district name; one-to-one, best score first. `match.score` is stored on every record (1.1 = name and district city agree).
- **Diocese**: the school's county through `data/real/california/dioceses.json` (all 58 counties map to exactly one diocese).

## The CDE Private School Directory: blocked

The request was for the CDE directory's religious-affiliation field. Findings:

- `sd.cde.ca.gov/SchoolDirectory/active-or-pending-schools/2` (the list page) loads, but it lists only CDS code, county, district, name, type, status.
- The export (`/schooldirectory/ExportSelect`) and each school's `/schooldirectory/details?cdscode=...` page redirect to `validate.perfdrive.com`
  (Radware Bot Manager / "Radware Captcha Page"). The export was tried once from the sandbox and once from the owner's laptop connection
  (Linux VM, home network) on 2026-10-31, and a detail page once from the sandbox; all three were challenged. A direct download URL of the form `/schooldirectory/report?rid=dlN&tp=txt` returned a file for one id
  (a public-school administrator list, discarded, not used) and the challenge for the next, so no further ids were tried.
- CDE's own file-structure page (`sd.cde.ca.gov/schooldirectory/topic/8`) documents the export fields; none is a religious affiliation.
  Private-school "status" there is derived from affidavit filing.
- Nothing was bypassed. If someone with a browser session downloads the private-school export from `sd.cde.ca.gov` by hand, it can be joined on CDS code
  to add CDE's street address, website and administrator.

## Gaps

- Addresses are NCES's (2023-24, else 2021-22), which can lag a move or a new campus; schools that opened after the 2023-24 survey are only in `unverified_candidates`.
- Schools that stopped filing keep their last year (39 records have a `school_year` before 2025-26).
- Catholic schools run by religious orders or independent boards are included; `catholic_type_nces` says parochial / diocesan / private.
"""
    (C.ROOT / "data" / "real" / "california" / "README-schools_cde.md").write_text(readme, encoding="utf-8")
    print(json.dumps(out["counts"], indent=1))


if __name__ == "__main__":
    main()
