"""MAP Interim Report (Diocese of Oakland, Sept 10 2022): Appendix 2 Mass attendance + deanery map.

Outputs
  data/real/map_2022_attendance.json  one row per parish (81) with 5-yr avg, 2019, 2021, Masses,
                                      "Lng" (non-English Masses), seats, region, deanery, PDF page, canon key
  data/real/deaneries.json            5 regions x 14 deaneries with member parishes and printed totals
  data/real/map_2022_summary.json     diocese-wide figures quoted from the narrative, with page refs

Source PDF: https://www.oakdiocese.org/wp-content/uploads/2026/04/MAP-commission-interim-report-092622-1.pdf
  -> downloaded to data/raw/map/ (gitignored) and parsed with pdfplumber (pp. 8-11).
  If the download is refused (the site's Azure WAF answers 403 to scripted clients from some networks),
  falls back to ingest/public/manual/map_appendix2_webfetch.txt, a line-for-line transcription of the
  same pages. Every number is copied, never inferred; rows the parser can't read are listed in
  `unparsed` and in data/real/README-map-events.md.

Usage: python ingest/public/map_appendix.py [--offline]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MANUAL, RAW, REAL, SESSION, canon_key, city_key, name_key, now_iso, save_json  # noqa: E402

PDF_URL = "https://www.oakdiocese.org/wp-content/uploads/2026/04/MAP-commission-interim-report-092622-1.pdf"
PDF_PATH = RAW / "map" / "MAP-commission-interim-report-092622.pdf"
MANUAL_TXT = MANUAL / "map_appendix2_webfetch.txt"
APPENDIX_PAGES = range(8, 12)  # 1-based PDF pages holding Appendix 2

# Longest first so "Walnut Creek" beats "Walnut", "Crockt/Pt Costa" is matched whole, etc.
CITIES = sorted([
    "Oakland", "Piedmont", "Alameda", "Berkeley", "Pt Richmond", "Richmond", "El Cerrito", "El Sobrante",
    "Pinole", "Rodeo", "San Pablo", "Crockt/Pt Costa", "Orinda", "Walnut Creek", "Moraga", "Lafayette",
    "Pleasant Hill", "Concord", "Martinez", "Pittsburg", "Brentwood", "Antioch", "Bay Point", "Byron",
    "Oakley", "Pleasanton", "Livermore", "Danville", "San Ramon", "Dublin", "Fremont", "Newark", "Hayward",
    "Union City", "San Lorenzo", "San Leandro", "Castro Valley", "Kensington", "Albany", "Emeryville",
], key=len, reverse=True)
REGION_NAMES = {1: "Central", 2: "Northwest", 3: "Northeast", 4: "Southeast", 5: "Southwest"}

NUM = r"\d{1,3}(?:,\d{3})*"
ROW_RE = re.compile(rf"^(?P<label>.*?\D)\s+(?P<nums>(?:{NUM}\s+){{4,5}}{NUM})\s*$")
REGION_RE = re.compile(r"^REGION\s+(\d)\b", re.I)
DEANERY_RE = re.compile(r"^Deanery\s+(\d{1,2})\s*$", re.I)
DTOT_RE = re.compile(r"^Deanery\s+(\d{1,2})\s+TTLS?\b", re.I)
RTOT_RE = re.compile(r"^REGION\s+(\d)\s+TTLS?\b", re.I)


def to_int(s: str) -> int:
    return int(s.replace(",", ""))


def split_nums(nums: list[int]) -> dict:
    """5-yr, 2019, 2021, Mass, [Lng], Seats. Lng is blank on the page when a parish has none."""
    if len(nums) == 6:
        a, b, c, m, l, s = nums
    else:
        a, b, c, m, s = nums
        l = None
    return {"avg_2015_2019": a, "attendance_2019": b, "attendance_2021": c, "masses": m,
            "lang_masses": l, "seats": s}


def download() -> tuple[str | None, str]:
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PDF_PATH.exists() and PDF_PATH.read_bytes()[:5] == b"%PDF-":
        return str(PDF_PATH), "cached"
    try:
        r = SESSION.get(PDF_URL, timeout=60)
    except Exception as e:  # noqa: BLE001
        return None, f"download error: {e}"
    if r.ok and r.content[:5] == b"%PDF-":
        PDF_PATH.write_bytes(r.content)
        return str(PDF_PATH), "downloaded"
    return None, f"download refused: HTTP {r.status_code}"


def pdf_lines(path: str) -> list[tuple[int, str]]:
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for p in APPENDIX_PAGES:
            if p - 1 < len(pdf.pages):
                for ln in (pdf.pages[p - 1].extract_text() or "").splitlines():
                    out.append((p, ln.strip()))
    return out


def manual_lines() -> list[tuple[int, str]]:
    out, page = [], None
    for ln in MANUAL_TXT.read_text(encoding="utf-8").splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        if ln.startswith("@page"):
            page = int(ln.split()[1])
            continue
        out.append((page, ln.strip()))
    return out


def parse(lines: list[tuple[int, str]]):
    """The PDF text layer has no deanery/region headings: parish rows are followed by a
    'Deanery N TTLS' row and deaneries by a 'REGION N TTLS' row. So rows are buffered and
    assigned to a deanery when its total row appears, and deaneries to a region likewise."""
    rows, dtot, rtot, unparsed, diocese = [], {}, {}, [], None
    pending: list[dict] = []          # parish rows awaiting their deanery TTLS
    open_deaneries: list[int] = []    # deaneries awaiting their REGION TTLS
    for i, (page, ln) in enumerate(lines):
        if m := RTOT_RE.match(ln):
            g = int(m.group(1))
            nums = [to_int(x) for x in re.findall(NUM, ln[m.end():])]
            rtot[g] = {**split_nums(nums), "page": page} if len(nums) in (5, 6) else None
            for r in rows:
                if r["deanery"] in open_deaneries:
                    r["region"], r["region_name"] = g, REGION_NAMES.get(g)
            open_deaneries = []
            continue
        if m := DTOT_RE.match(ln):
            d = int(m.group(1))
            nums = [to_int(x) for x in re.findall(NUM, ln[m.end():])]
            dtot[d] = {**split_nums(nums), "page": page} if len(nums) in (5, 6) else None
            for r in pending:
                r["deanery"] = d
            rows += pending
            pending = []
            open_deaneries.append(d)
            continue
        if REGION_RE.match(ln) or DEANERY_RE.match(ln) or re.fullmatch(r"\d{1,2}", ln):
            continue  # headings in the manual transcription; bare page numbers in the PDF
        if ln.lower().startswith("diocesan totals"):
            nxt = [l for _, l in lines[i + 1:i + 3] if re.fullmatch(rf"(?:{NUM}\s+){{5}}{NUM}", l)]
            if nxt:
                diocese = {**split_nums([to_int(x) for x in nxt[0].split()]), "page": page}
            continue
        if re.fullmatch(rf"(?:{NUM}\s+){{5}}{NUM}", ln) and diocese is not None:
            continue  # the diocesan totals value line, already consumed
        m = ROW_RE.match(ln)
        if not m:
            if re.search(r"\d", ln) and not re.search(r"5-yr|TTLS|^Appendix \d+$", ln, re.I):
                unparsed.append({"page": page, "line": ln, "reason": "no numeric row match"})
            continue
        label = m.group("label").strip()
        nums = [to_int(x) for x in m.group("nums").split()]
        city = next((c for c in CITIES if label.startswith(c + " ")), None)
        if city is None:
            unparsed.append({"page": page, "line": ln, "reason": "city not recognised"})
            continue
        parish = label[len(city):].strip()
        pending.append({"region": None, "region_name": None, "deanery": None,
                        "city_as_printed": city, "parish_as_printed": parish, **split_nums(nums), "page": page})
    for r in pending:
        unparsed.append({"page": r["page"], "line": f"{r['city_as_printed']} {r['parish_as_printed']}",
                         "reason": "parish row not followed by a Deanery TTLS row"})
    return rows, dtot, rtot, diocese, unparsed


def checks(rows, dtot, rtot, diocese) -> list[str]:
    """Compare parish sums with the printed TTLS rows. Differences are reported, not corrected."""
    F = ["avg_2015_2019", "attendance_2019", "attendance_2021", "masses", "lang_masses", "seats"]
    notes = []

    def s(rs, f):
        return sum(r[f] or 0 for r in rs)

    for d, t in sorted(dtot.items()):
        rs = [r for r in rows if r["deanery"] == d]
        for f in F:
            if t and s(rs, f) != (t[f] or 0):
                notes.append(f"Deanery {d} {f}: parish rows sum {s(rs, f)}, printed total {t[f]} (p.{t['page']})")
    for g, t in sorted(rtot.items()):
        rs = [r for r in rows if r["region"] == g]
        for f in F:
            if t and s(rs, f) != (t[f] or 0):
                notes.append(f"Region {g} {f}: parish rows sum {s(rs, f)}, printed total {t[f]} (p.{t['page']})")
    if diocese:
        for f in F:
            if s(rows, f) != (diocese[f] or 0):
                notes.append(f"Diocese {f}: parish rows sum {s(rows, f)}, printed total {diocese[f]} (p.{diocese['page']})")
        for f in F:
            rs_sum = sum((t[f] or 0) for t in rtot.values() if t)
            if rs_sum != (diocese[f] or 0):
                notes.append(f"Diocese {f}: sum of printed region totals {rs_sum}, printed diocesan total {diocese[f]}")
    return notes


# ---------------------------------------------------------------- diocese-wide figures (map_2022_summary.json)
# Narrative figures: `quote` must appear verbatim in the PDF text layer (checked when the PDF is
# available). Chart figures (pp. 4-5) are images with no text layer; values were read off the
# rendered charts at 250-300 dpi and are flagged source="chart image".
NARRATIVE = [
    ("mass_attendance_drop_2019_2021_pct", 42, "%", 3, "show a drop of 42% from 2019 to"),
    ("priests_fewer_than_1985_pct", 25, "%", 3, "25% fewer priests than in 1985"),
    ("parishes", 81, "count", 19, "The diocese has 81 parishes in"),
    ("deaneries", 14, "count", 19, "14 deaneries in five regions"),
    ("regions", 5, "count", 19, "14 deaneries in five regions"),
    ("clergy_at_mar_2022_gathering", 98, "count", 12, "98 Clergy from the Diocese of Oakland attended"),
    ("parishes_with_prepandemic_attendance_decline_pct", 86, "%", 32, "86% of parishes had decline in attendance pre-COVID"),
    ("parishes_yellow_or_red_financial_score_share", "2/3", "fraction", 33, "2/3 of parishes have yellow or red blended financial scores"),
    ("parish_financial_scores_clergy_view", "evenly split green/yellow/red", "text", 13, "Evenly split between green, yellow and red parishes."),
    ("schools_financially_healthy_pct", 68, "%", 16, "Schools are effective, 68% financially healthy"),
    ("schools_wcea_highly_effective_pct", 80, "%", 31, "Schools are generally strong with 80% rated highly effective by WCEA"),
    ("schools_wcea_highly_effective_pct_alt", 82, "%", 37, "82% of schools are highly effective"),
    ("regional_meeting_observations", 368, "count", 32, "368 observations were made by participants"),
]
SCHOOL_PRINCIPALS_BY_REGION = [  # "There are N school principals in Region R" (elementary schools)
    (1, 9, 24, "There are nine school principals in Region 1"),
    (2, 7, 26, "There are seven school principals in Region 2"),
    (3, 7, 27, "There are seven school principals in Region 3"),
    (4, 7, 28, "There are seven school principals in Region 4"),
    (5, 9, 30, "There are nine school principals in Region 5"),
]
PARISHES_BY_REGION = [
    (1, 24, 5, 24, "This region has 24 parishes in five deaneries"),
    (2, 17, 3, 25, "This region has 17 parishes in three deaneries"),
    (3, 15, 2, 27, "This region has 15 parishes in two deaneries"),
    (4, 12, 2, 28, "This region has 12 parishes in two deaneries"),
    (5, 13, 2, 29, "This region has 13 parishes in two deaneries"),
]
CHART_MASS_COUNTS = {  # p.4 "Total Diocesan October Mass Counts" (x-axis 2010-2019, 2021; no 2020 count)
    2010: 125346, 2011: 131193, 2012: 127920, 2013: 127057, 2014: 126469, 2015: 122427,
    2016: 117452, 2017: 116725, 2018: 111062, 2019: 106182, 2021: 61701}
CHART_PRIESTS = {  # p.5 "Total Pastors, Parochial Administrators & Parochial Vicars by Year"
    1985: 155, 1990: 154, 1994: 160, 1995: 151, 2000: 147, 2005: 137, 2008: 133, 2011: 141,
    2015: 135, 2018: 130, 2022: 117}
CHART_PRIEST_PROJECTION = {  # p.5 "Net Priest Retirement plus Priests Ordained" data table, priests in ministry by age band
    "years": list(range(2022, 2035)),
    "age_76_plus": [6, 6, 7, 8, 10, 11, 13, 15, 18, 24, 27, 29, 33],
    "age_71_75": [5, 7, 8, 10, 14, 16, 16, 18, 16, 17, 22, 25, 24],
    "age_70_under": [119, 117, 115, 112, 106, 103, 101, 97, 96, 89, 81, 76, 73],
}


def build_summary(pdf_path: str | None, diocese: dict | None) -> dict:
    page_text: dict[int, str] = {}
    if pdf_path:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for i, p in enumerate(pdf.pages, 1):
                page_text[i] = re.sub(r"\s+", " ", p.extract_text() or "")

    def ok(page: int, quote: str):
        if not page_text:
            return None
        return re.sub(r"\s+", " ", quote) in page_text.get(page, "")

    figs = [{"metric": k, "value": v, "unit": u, "page": p, "quote": q, "source": "narrative text",
             "verified_in_pdf_text": ok(p, q)} for k, v, u, p, q in NARRATIVE]
    out = {
        "source_url": PDF_URL, "report": "Diocese of Oakland MAP Interim Report", "report_date": "2022-09-10",
        "retrieved": now_iso(),
        "note": "verified_in_pdf_text=true means the quote was found verbatim in that page's PDF text layer; "
                "null means the PDF was unavailable at run time. Chart series were read from images.",
        "figures": figs,
        "mass_attendance_october_counts": {
            "page": 4, "source": "chart image ('Total Diocesan October Mass Counts 2000 - 2019, 2021'; x-axis shows 2010-2021, no 2020)",
            "by_year": {str(k): v for k, v in CHART_MASS_COUNTS.items()},
            "cross_check": "2019 and 2021 match Appendix 2 diocesan totals (p.11)",
            "appendix2_diocesan_totals_p11": diocese},
        "priests_pastors_admins_vicars": {
            "page": 5, "source": "chart image ('Total Pastors, Parochial Administrators & Parochial Vicars by Year')",
            "by_year": {str(k): v for k, v in CHART_PRIESTS.items()}},
        "priest_availability_projection": {
            "page": 5, "source": "chart image data table ('Net Priest Retirement plus Priests Ordained', y-axis 'Priests in Ministry')",
            **CHART_PRIEST_PROJECTION},
        "parishes_by_region": [{"region": r, "parishes": n, "deaneries": d, "page": p, "quote": q,
                                "verified_in_pdf_text": ok(p, q)} for r, n, d, p, q in PARISHES_BY_REGION],
        "elementary_school_principals_by_region": [{"region": r, "principals": n, "page": p, "quote": q,
                                                    "verified_in_pdf_text": ok(p, q)} for r, n, p, q in SCHOOL_PRINCIPALS_BY_REGION],
        "not_in_report": ["school count/enrollment totals (only principals per region)", "Catholic population",
                          "diocese-wide parish/school dollar figures", "per-school financial tiers"],
    }
    return out


def city_for_key(printed: str) -> str:
    return {"Pt Richmond": "Richmond", "Crockt/Pt Costa": "Crockett"}.get(printed, printed)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip the download, use the manual transcription")
    a = ap.parse_args()

    path, status = (None, "offline") if a.offline else download()
    if path:
        lines, source = pdf_lines(path), f"pdfplumber:{PDF_PATH.relative_to(REAL.parents[1])}"
    else:
        lines, source = manual_lines(), f"manual transcription:{MANUAL_TXT.relative_to(REAL.parents[1])} ({status})"
    rows, dtot, rtot, diocese, unparsed = parse(lines)

    for r in rows:
        city = city_for_key(r["city_as_printed"])
        r["city"] = city
        r["name_key"] = name_key(r["parish_as_printed"])
        r["city_key"] = city_key(city)
        r["canon_key"] = canon_key(r["parish_as_printed"], city)
        r["pct_2021_of_2019"] = round(100 * r["attendance_2021"] / r["attendance_2019"], 1) if r["attendance_2019"] else None
        r["seat_utilization_2019"] = round(r["attendance_2019"] / r["seats"] / r["masses"], 3) if r["seats"] and r["masses"] else None

    notes = checks(rows, dtot, rtot, diocese)
    meta = {"source_url": PDF_URL, "report": "Diocese of Oakland Mission Alignment Process (MAP) Interim Report, Sept 10 2022",
            "appendix": "Appendix 2: Mass Attendance (October counts)", "pages": "8-11", "parsed_from": source,
            "retrieved": now_iso(),
            "columns": {"avg_2015_2019": "5-yr Av (2015-2019)", "attendance_2019": "2019", "attendance_2021": "2021",
                        "masses": "Mass (weekend Masses)",
                        "lang_masses": "Lng (blank on page -> null; appears to count non-English Masses, header not defined in report)",
                        "seats": "Seats"},
            "derived": {"pct_2021_of_2019": "attendance_2021 / attendance_2019",
                        "seat_utilization_2019": "attendance_2019 / (seats * masses), rough avg fill per Mass"},
            "validation_notes": notes, "unparsed": unparsed}
    save_json(REAL / "map_2022_attendance.json", {**meta, "count": len(rows), "rows": rows})

    regions = []
    for g in sorted({r["region"] for r in rows}):
        ds = []
        for d in sorted({r["deanery"] for r in rows if r["region"] == g}):
            ps = [r for r in rows if r["deanery"] == d]
            ds.append({"deanery": d, "name": f"Deanery {d}", "parish_count": len(ps),
                       "parishes": [{"parish": p["parish_as_printed"], "city": p["city"], "canon_key": p["canon_key"]} for p in ps],
                       "printed_totals": dtot.get(d)})
        regions.append({"region": g, "name": REGION_NAMES.get(g), "deanery_count": len(ds), "deaneries": ds,
                        "printed_totals": rtot.get(g)})
    save_json(REAL / "deaneries.json", {
        "source_url": PDF_URL, "pages": "8-11 (Appendix 2)", "as_of": "2022-09-10", "retrieved": now_iso(),
        "note": "Structure as of the 2022 MAP report. p.62 says St. Stephen (Walnut Creek, listed here in Deanery 8) "
                "was later counted with Deanery 9; the 2026 events calendar shows a combined 'Christ the King - St. Stephen'.",
        "region_count": len(regions), "deanery_count": sum(r["deanery_count"] for r in regions),
        "parish_count": len(rows), "diocesan_totals": diocese, "regions": regions})

    summary = build_summary(path, diocese)
    save_json(REAL / "map_2022_summary.json", summary)
    unverified = [f["metric"] for f in summary["figures"] if f["verified_in_pdf_text"] is False]
    unverified += [f"region {x['region']}" for k in ("parishes_by_region", "elementary_school_principals_by_region")
                   for x in summary[k] if x["verified_in_pdf_text"] is False]
    if unverified:
        print("   SUMMARY quotes NOT found in PDF text:", unverified)

    print(f"{source}: {len(rows)} parish rows, {len(dtot)} deanery totals, {len(rtot)} region totals, "
          f"{len(unparsed)} unparsed, {len(notes)} total mismatches")
    for n in notes:
        print("  ", n)
    for u in unparsed:
        print("   UNPARSED p.%s: %s" % (u["page"], u["line"]))


if __name__ == "__main__":
    main()
