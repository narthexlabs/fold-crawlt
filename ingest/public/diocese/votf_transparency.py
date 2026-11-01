"""Voice of the Faithful (VOTF) diocesan online financial transparency scores.

Parses Appendix B ("Transparency Scores, Alphabetical Listing") of each available annual report.
Each diocese has two rows: current-year total + per-question scores, then prior-year total + scores.
Writes data/real/diocese/votf_scores.json with Oakland's full series and all California dioceses.

Run:  python -I ingest/public/diocese/votf_transparency.py [--no-download]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import requests

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "diocese"
OUT = ROOT / "data" / "real" / "diocese" / "votf_scores.json"
UA = "OaklandDioceseDashboard-research/0.1 (public-data collection)"

REPORTS = {
    # report year: (url, local file)
    2025: ("https://www.votf.org/wp-content/uploads/2026/04/VOTF-2025-Diocesan-Financial-Transparency-Report.pdf", "VOTF-2025.pdf"),
    2023: ("https://www.votf.org/wp-content/uploads/2023/12/2023-VOTF-Fiancial-Transparency-Report-122123.pdf", "VOTF-2023.pdf"),
    # 2022 report is mirrored by a law firm; VOTF's own copy was not found
    2022: ("https://www.andersonadvocates.com/wp-content/uploads/2022/12/VOTF-2022-Diocesan-Financial-Transparency-Report.pdf", "VOTF-2022.pdf"),
}
# The 2024 report PDF was not found online; its scores appear as the prior-year row in the 2025 report.

QUESTIONS = {
    "q1": {"max": 5, "label": "Central finance page"},
    "q2": {"max": 10, "label": "Workable internal search"},
    "q3_4": {"max": 25, "label": "Audited financial statements posted (current/unqualified/linked) or unaudited alternative"},
    "q5": {"max": 10, "label": "Annual appeal programs + appeal income in audit"},
    "q6": {"max": 10, "label": "Parish assessment (cathedraticum) explained"},
    "q7": {"max": 10, "label": "Finance staff + Diocesan Finance Council contact/membership (redefined 2025)"},
    "q8": {"max": 10, "label": "DFC functions/operations (redefined 2025: agendas, Canon 1287, Canon 1277)"},
    "q9": {"max": 10, "label": "Parish financial policies & procedures posted"},
    "q10": {"max": 10, "label": "Parish collection & counting procedures"},
}
QKEYS = list(QUESTIONS)

ROW1 = re.compile(r"^(?P<name>[A-Z][A-Za-z.\-’' ,]+?)\s+(?P<st>[A-Z]{2}),?\s+(?P<nums>(?:\d{1,3}\s+){9}\d{1,3})$")
ROW2 = re.compile(r"^(?P<nums>(?:\d{1,3}\s+){9}\d{1,3})$")


def download():
    status = {}
    RAW.mkdir(parents=True, exist_ok=True)
    for yr, (url, fn) in REPORTS.items():
        dest = RAW / fn
        if dest.exists() and dest.read_bytes()[:5] == b"%PDF-":
            status[yr] = "cached"; continue
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                dest.write_bytes(r.content); status[yr] = "downloaded"
            else:
                status[yr] = f"http {r.status_code}"
        except requests.RequestException as e:
            status[yr] = f"error: {e}"
    return status


def to_scores(nums: str) -> dict:
    v = [int(x) for x in nums.split()]
    total, parts = v[0], dict(zip(QKEYS, v[1:]))
    return {"total": total, **parts, "sum_check": sum(v[1:]) == total}


def parse_appendix_b(pdf_path: Path, report_year: int) -> dict[str, dict]:
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            t = page.extract_text() or ""
            if "Alphabetical listing" not in t:
                continue
            lines = [l.strip() for l in t.split("\n")]
            for i, l in enumerate(lines):
                m = ROW1.match(l)
                if not m:
                    continue
                name = f"{m.group('name').strip().rstrip(',')} {m.group('st')}"
                rec = {"page": pno, str(report_year): to_scores(m.group("nums"))}
                if i + 1 < len(lines) and ROW2.match(lines[i + 1]):
                    rec[str(report_year - 1)] = to_scores(lines[i + 1])
                out[name] = rec
    return out


def oakland_context_2023(pdf_path: Path) -> dict | None:
    """Table D-1 in the 2023 report lists the 2017 baseline score for bankrupt dioceses."""
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            for l in (page.extract_text() or "").split("\n"):
                m = re.match(r"^Oakland CA (\d{4}) (Yes|No) (\d{4}|None) (\d+) (\d+)$", l)
                if m:
                    return {"page": pno, "raw": l, "filed": int(m.group(1)), "current_dfc_posted": m.group(2),
                            "latest_published_finance_statement": m.group(3),
                            "score_2023": int(m.group(4)), "score_2017": int(m.group(5))}
    return None


def main():
    dl = {} if "--no-download" in sys.argv else download()
    parsed = {}
    for yr, (url, fn) in REPORTS.items():
        p = RAW / fn
        if p.exists():
            parsed[yr] = parse_appendix_b(p, yr)

    # Merge per diocese: report-year rows take precedence over prior-year rows from later reports
    series: dict[str, dict] = {}
    for yr in sorted(parsed):  # older reports first; newer report-year rows overwrite prior-year rows
        for name, rec in parsed[yr].items():
            d = series.setdefault(name, {})
            for k, v in rec.items():
                if k == "page":
                    continue
                src = {"report": yr, "url": REPORTS[yr][0], "page": rec["page"],
                       "row": "current-year" if int(k) == yr else "prior-year"}
                if k not in d or (d[k]["source"]["row"] == "prior-year" and src["row"] == "current-year") or int(k) == yr:
                    d[k] = {**v, "source": src}

    ca = {n: dict(sorted(s.items())) for n, s in sorted(series.items()) if n.endswith(" CA")}
    oak = ca.get("Oakland CA", {})
    base = oakland_context_2023(RAW / REPORTS[2023][1]) if 2023 in parsed else None

    out = {
        "dataset": "Voice of the Faithful - Measuring and Ranking Diocesan Online Financial Transparency",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "reports_parsed": {yr: {"url": REPORTS[yr][0], "dioceses_parsed": len(parsed[yr])} for yr in parsed},
        "download_status": dl,
        "max_score": 100,
        "questions": QUESTIONS,
        "methodology_notes": [
            "Scores are VOTF reviewers' assessment of what is posted on the diocesan website during the review window (2025: Jun 1-Aug 31, 2025).",
            "2025 changed Q7/Q8 (Diocesan Finance Council); 2025 Q7/Q8 are not comparable with earlier years.",
            "2024 scores come from the prior-year rows of the 2025 report (2024 report PDF not located).",
            "2017 Oakland score comes from Table D-1 of the 2023 report; 2018-2020 not located.",
        ],
        "oakland": {
            "scores_by_year": oak,
            "score_2017_baseline": base,
            "report_commentary_2025": [
                {"page": 8, "text": "Oakland CA 39 60 -21 (Table 4: eight dioceses losing most points 2024->2025)"},
                {"page": 8, "text": "Oakland received only five points this year on Questions 3 and 4 because the latest financial statement posted on their website is from 2021, more than two years out of date. Oakland also redesigned their website this year, and the finance page disappeared along with links to important financial information that had been previously found there."},
                {"page": 11, "text": "Dioceses lacking a finance page in 2025 were ... Oakland ... Green Bay and Oakland's pages disappeared in 2025 due to website redesigns"},
            ],
            "report_commentary_2023": [
                {"page": 14, "text": "Oakland among 10 dioceses whose financial reports are 1 year out of date; 'Both Albany and Oakland filed for bankruptcy in 2023.'"},
            ],
        },
        "california": ca,
        "california_2025_ranked": sorted(
            [{"diocese": n, "score_2025": s["2025"]["total"], "score_2024": s.get("2024", {}).get("total")}
             for n, s in ca.items() if "2025" in s], key=lambda r: -r["score_2025"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    bad = [(n, y) for n, s in series.items() for y, v in s.items() if not v["sum_check"]]
    print(json.dumps({"parsed": {y: len(v) for y, v in parsed.items()}, "ca": len(ca),
                      "oakland": {y: v["total"] for y, v in oak.items()}, "baseline": base,
                      "sum_check_failures": bad[:20], "ca_2025": out["california_2025_ranked"]}, indent=1))


if __name__ == "__main__":
    main()
