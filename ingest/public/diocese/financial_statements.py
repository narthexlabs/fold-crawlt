"""Audited Central Services Administration (CSA) statements, Diocese of Oakland, FY2010-FY2021.

Source: https://www.oakdiocese.org/financial-reports/ (one PDF per two-year comparative report).
Downloads PDFs to data/raw/diocese/ (gitignored) and writes data/real/diocese/financials.json.

Rules:
  * Each FY's primary value comes from that FY's own report (current-year column).
  * FY2010 and any FY whose own report is unavailable fall back to the prior-year
    (comparative) column of the following year's report; that is flagged in `basis`.
  * Comparative values that differ from the primary are recorded under `restatements`.
  * Anything that fails to parse is null with the page number searched. Nothing is estimated.

Run:  python -I ingest/public/diocese/financial_statements.py [--no-download]
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import requests

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "diocese"
OUT = ROOT / "data" / "real" / "diocese" / "financials.json"
INDEX_URL = "https://www.oakdiocese.org/financial-reports/"
UA = "OaklandDioceseDashboard-research/0.1 (public-data collection)"

# ------------------------------------------------------------------ download

def discover_pdfs() -> list[str]:
    r = requests.get(INDEX_URL, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    urls = sorted(set(re.findall(r'href="([^"]+Central-Services-Administration-Financial-Statements-(\d{4})-(\d{4})\.pdf)"', r.text)))
    return [u[0] for u in urls]


def download(urls: list[str]) -> dict[str, dict]:
    status = {}
    RAW.mkdir(parents=True, exist_ok=True)
    for u in urls:
        m = re.search(r"(\d{4})-(\d{4})\.pdf$", u)
        name = f"CSA-{m.group(1)}-{m.group(2)}.pdf"
        dest = RAW / name
        if dest.exists() and dest.read_bytes()[:5] == b"%PDF-":
            status[name] = {"url": u, "status": "cached"}
            continue
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=60)
        except requests.RequestException as e:
            status[name] = {"url": u, "status": f"error: {e}"}
            continue
        if r.status_code == 200 and r.content[:5] == b"%PDF-":
            dest.write_bytes(r.content)
            status[name] = {"url": u, "status": "downloaded"}
        else:
            status[name] = {"url": u, "status": f"http {r.status_code} (not a PDF)"}
            if dest.exists():
                dest.unlink()
        time.sleep(1)
    return status

# ------------------------------------------------------------------ parsing

DASHES = {"-", "‐", "–", "—"}
NUM_RE = re.compile(r"^\(?\d{1,3}(,\d{3})*\)?$")


def tokens_to_numbers(rest: str) -> list[int] | None:
    """Turn the numeric tail of a statement line into ints.

    pdfplumber splits some digits ("6 57,598", "1 0,457,831", "( 7,292,542)"). A short
    token is merged with the following token only when the result is a validly
    comma-grouped number (first group <= 3 digits).
    """
    toks = [t for t in rest.replace("$", " ").split() if t]
    # join "(" with following token
    joined = []
    for t in toks:
        if joined and joined[-1] == "(":
            joined[-1] = "(" + t
        else:
            joined.append(t)
    toks = joined
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in DASHES:
            out.append("0"); i += 1; continue
        if i + 1 < len(toks) and re.fullmatch(r"\(?\d{1,2}", t):
            nxt = toks[i + 1]
            m = re.fullmatch(r"(\d*)((?:,\d{3})+)(\)?)", nxt)
            if m and len(t.lstrip("(")) + len(m.group(1)) <= 3:
                out.append(t + nxt); i += 2; continue
        out.append(t); i += 1
    nums = []
    for t in out:
        if t == "0":
            nums.append(0); continue
        if not NUM_RE.match(t):
            return None
        neg = t.startswith("(")
        v = int(t.strip("()").replace(",", ""))
        nums.append(-v if neg else v)
    return nums


def split_two_years(nums: list[int]) -> tuple[int, int] | None:
    """Return (current_total, prior_total) for a row of 2 or 2xN column values.

    Balance-sheet rows have one column per year. Activity rows have N restriction
    columns + total per year; the total is identified by sum check, which also
    tolerates a dropped dash in one year.
    """
    if len(nums) == 2:
        return nums[0], nums[1]
    for k in range(2, len(nums) - 1):
        a, b = nums[:k], nums[k:]
        if len(a) >= 2 and len(b) >= 2 and sum(a[:-1]) == a[-1] and sum(b[:-1]) == b[-1]:
            return a[-1], b[-1]
    return None


LINE_RE = re.compile(r"^(?P<label>[A-Za-z].*?)\s+(?P<rest>[$(\d‐–—-][^A-Za-z]*)$")


def statement_rows(pdf_path: Path) -> tuple[list[dict], list[int]]:
    """Ordered rows {key, label, cur, prior, page, raw} from statement-of-position/activities pages."""
    rows: list[dict] = []
    pages_used = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages[:14], start=1):
            # y_tolerance=1: the default (3) interleaves adjacent lines on some FY2014/15 pages
            text = page.extract_text(y_tolerance=1) or ""
            head = text[:400].lower()
            if "statements of cash flows" in head or "statement of cash flows" in head:
                break
            if not re.search(r"statements? of (financial position|activities)", head) or "functional" in head:
                continue
            if "....." in text:  # table of contents
                continue
            pages_used.append(pno)
            pending = ""
            for line in text.split("\n"):
                # drop parens around words, e.g. "Investment income (loss)", "(losses) gains"
                line = re.sub(r"\(([A-Za-z][A-Za-z ]*)\)", r"\1", line.strip())
                m = LINE_RE.match(line)
                if m and re.search(r"[A-Za-z]", m.group("rest")):
                    m = None  # label-only line such as "Clergy retirement - actuarial change in valuation"
                if not m:
                    # label-only line that continues onto the next line
                    pending = (pending + " " + line).strip() if re.fullmatch(r"[A-Za-z][A-Za-z ,'’\-‐()&/]*", line) else ""
                    continue
                label = m.group("label").strip()
                if pending and label[:1].islower():
                    label = pending + " " + label
                pending = ""
                nums = tokens_to_numbers(m.group("rest"))
                key = norm(label)
                pair = split_two_years(nums) if nums else None
                rows.append({"key": key, "cur": pair[0] if pair else None, "prior": pair[1] if pair else None,
                             "page": pno, "raw": line, "label": label})
    return rows, pages_used


def norm(s: str) -> str:
    s = s.lower().replace("‐", "-").replace("’", "'")
    s = re.sub(r"[^a-z' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# field -> list of label regexes (matched against normalized labels)
FIELDS = {
    "total_revenue": [r"^total revenues"],
    "parish_assessments": [r"^parish assessments$"],
    "bishops_appeal": [r"^bishop's annual appeal$"],
    "gifts_grants_bequests": [r"^gifts grants and bequests$"],
    "cemetery_rent": [r"^cemetery rent$"],
    "insurance_revenue_net": [r"^insurance revenue net$"],
    "dividends_and_interest": [r"^dividends and interest$"],
    "interest_income_deposit_loan_fund": [r"^interest income on deposit and loan fund$"],
    "ministerial_department_revenue": [r"^ministerial department revenue$"],
    "catholic_voice_revenue": [r"^catholic voice$"],  # first occurrence is the revenue line
    "other_income": [r"^other income$"],
    "investment_gains_losses": [r"^net realized and unrealized", r"^investment income"],
    "interest_income_related_party_notes": [r"^interest income on notes (to|from) related parties"],
    "bond_financing_interest_expense": [r"^(bonds|bank loan) and other financing interest expense"],
    "gain_on_sale_of_property": [r"^gain loss on sale of land|^gain on sale of land"],
    "total_expenses": [r"^total expenses$"],
    "change_in_net_assets": [r"^change in net assets$"],
    "total_assets": [r"^total assets$"],
    "total_liabilities": [r"^total liabilities$"],
    "total_net_assets": [r"^total net assets$"],
    "loans_to_parishes_schools": [r"^loans to parishes"],
    "deposits_from_parishes": [r"^deposits payable$"],
    "investments": [r"^investments$", r"^marketable securities"],
    "bonds_payable": [r"^bonds payable"],
    "notes_payable": [r"^notes payable$"],
    "pension_liability": [r"^liability for pension benefits$"],
    "postretirement_benefits_liability": [r"^post-? ?retirement employment benefits liability$", r"^post retirement"],
}

REVENUE_ITEMS = ["parish_assessments", "bishops_appeal", "gifts_grants_bequests", "dividends_and_interest",
                 "ministerial_department_revenue", "catholic_voice_revenue", "cemetery_rent",
                 "interest_income_deposit_loan_fund", "insurance_revenue_net", "other_income"]


def expense_lines(rows: list[dict]) -> list[dict]:
    """Rows between the first expense line and 'Total expenses' on the statement of activities."""
    keys = [r["key"] for r in rows]
    try:
        start = next(i for i, k in enumerate(keys) if k.startswith(("pastoral life", "services and administration")))
        end = keys.index("total expenses")
    except (StopIteration, ValueError):
        return []
    return rows[start:end]


def pick(rows, field):
    for pat in FIELDS[field]:
        for r in rows:
            if re.search(pat, r["key"]):
                return r
    return None


def parse_report(pdf_path: Path) -> dict:
    rows, pages = statement_rows(pdf_path)
    cur, prior = {}, {}
    for f in FIELDS:
        r = pick(rows, f)
        for target, col in ((cur, "cur"), (prior, "prior")):
            if r is None:
                target[f] = {"value": None, "page": pages, "note": "label not found"}
            elif r[col] is None:
                target[f] = {"value": None, "page": r["page"], "note": f"could not parse: {r['raw']}"}
            else:
                target[f] = {"value": r[col], "page": r["page"]}
    # expense categories (catholic voice appears twice: revenue then expense; the expense one
    # is stored under a second key only if labels repeat, so handle by scanning raw order)
    cats = expense_lines(rows)
    for target, col in ((cur, "cur"), (prior, "prior")):
        target["_expenses_by_category"] = {r["label"]: r[col] for r in cats}
    return {"current": cur, "prior": prior, "pages": pages}


def checks(rec: dict) -> dict:
    out = {}
    v = lambda f: rec.get(f, {}).get("value") if isinstance(rec.get(f), dict) else None
    items = [v(f) for f in REVENUE_ITEMS]
    if all(x is not None for x in items) and v("total_revenue") is not None:
        out["revenue_items_sum_to_total"] = sum(items) == v("total_revenue")
    cats = rec.get("expenses_by_category") or {}
    if cats and all(x is not None for x in cats.values()) and v("total_expenses") is not None:
        out["expense_categories_sum_to_total"] = sum(cats.values()) == v("total_expenses")
    if None not in (v("total_assets"), v("total_liabilities"), v("total_net_assets")):
        out["assets_eq_liabilities_plus_net_assets"] = v("total_assets") == v("total_liabilities") + v("total_net_assets")
    return out


def main():
    dl_status = {}
    if "--no-download" not in sys.argv:
        try:
            dl_status = download(discover_pdfs())
        except requests.RequestException as e:
            dl_status = {"_index": f"error: {e}"}
    reports = {}
    for pdf in sorted(RAW.glob("CSA-*.pdf")):
        m = re.match(r"CSA-(\d{4})-(\d{4})", pdf.stem)
        cur_fy, prior_fy = int(m.group(1)), int(m.group(2))
        parsed = parse_report(pdf)
        reports[cur_fy] = {"file": pdf.name, "prior_fy": prior_fy, **parsed}

    src_url = lambda f: f"https://www.oakdiocese.org/wp-content/uploads/2025/09/Central-Services-Administration-Financial-Statements-{f[4:13]}.pdf"
    years = {}
    for fy in range(2010, 2022):
        own = reports.get(fy)
        nxt = reports.get(fy + 1)
        if own:
            rec, basis, file = own["current"], "own report, current-year column", own["file"]
        elif nxt:
            rec, basis, file = nxt["prior"], f"comparative column of FY{fy+1} report (own report unavailable or not published)", nxt["file"]
        else:
            years[str(fy)] = {"basis": "no report available", "fields": None}
            continue
        fields = {k: val for k, val in rec.items() if not k.startswith("_")}
        # fill nulls from the comparative column of the next report, flagged
        if own and nxt:
            for k, val in fields.items():
                alt = nxt["prior"].get(k, {})
                if val["value"] is None and alt.get("value") is not None:
                    fields[k] = {"value": alt["value"], "page": alt["page"], "source_file": nxt["file"],
                                 "note": f"own report unparsed ({val.get('note')}); taken from FY{fy+1} report comparative column"}
        entry = {
            "basis": basis,
            "source_file": file,
            "source_url": src_url(file),
            "fields": fields,
            "expenses_by_category": rec["_expenses_by_category"],
        }
        flat = dict(fields); flat["expenses_by_category"] = rec["_expenses_by_category"]
        entry["checks"] = checks(flat)
        # restatements: compare own-report value with next report's comparative
        if own and nxt:
            rs = {}
            for k, val in own["current"].items():
                if k.startswith("_"):
                    continue
                a, b = val.get("value"), nxt["prior"].get(k, {}).get("value")
                if a is not None and b is not None and a != b:
                    rs[k] = {"as_originally_reported": a, f"as_shown_in_FY{fy+1}_report": b}
            if rs:
                entry["restatements"] = rs
        years[str(fy)] = entry

    out = {
        "dataset": "Diocese of Oakland - Central Services Administration audited consolidated statements",
        "entity_scope": "Central Services Administration only (chancery/central entities); NOT parishes, schools, Catholic Charities or cemeteries corporation",
        "fiscal_year": "calendar year (Dec 31)",
        "units": "USD",
        "index_url": INDEX_URL,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "download_status": dl_status,
        "field_notes": {
            "deposits_from_parishes": "'Deposits payable' - parish/institution deposits held in the diocesan deposit & loan fund",
            "loans_to_parishes_schools": "'Loans to parishes, schools, institutions[, and seminarians], net'",
            "investment_gains_losses": "Other income: 'Net realized and unrealized gains (losses)' (FY2010-16) / 'Investment income' (FY2017+); presentation changed",
            "dividends_and_interest": "Operating revenue line, separate from investment gains",
            "bonds_payable": "Bonds were refinanced into bank notes; no 'Bonds payable' line exists in FY2019+ statements (null = line absent, not a parse failure)",
            "change_in_net_assets": "FY2014/FY2015 PDFs have overlapping text on the 'CHANGE IN NET ASSETS' row; left null where unreadable (derive from net assets roll-forward if needed)",
            "restatements": "Where a later report's comparative column differs from the original, both are kept; primary value is the original",
            "expenses_by_category": "Lines between EXPENSES and Total expenses on the statement of activities, as labeled in that report (labels vary by year)",
        },
        "years": years,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    nulls = {fy: [k for k, v in e["fields"].items() if v["value"] is None] for fy, e in years.items() if e.get("fields")}
    print(json.dumps({"years": len(years), "nulls": nulls,
                      "checks": {fy: e.get("checks") for fy, e in years.items()}}, indent=1))


if __name__ == "__main__":
    main()
