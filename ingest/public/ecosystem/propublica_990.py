"""Form 990 headline figures for registry orgs -> data/real/ecosystem/form990.json

For each org in registry.py that has an `ein`, or a `pp_query` (name) + optional `pp_city`:
  1. EIN known: ProPublica Nonprofit Explorer API  /api/v2/organizations/<EIN>.json
     Name only:  /api/v2/search.json?q=<name>&state[id]=CA, accepted only when the city matches (if given) and
                 the name matches closely; otherwise the org is reported as unmatched (never guessed).
  2. Latest filing with extracted data -> revenue, expenses, assets, liabilities (ProPublica summary fields).
  3. Employees: Part I line 5 (TotalEmployeeCnt) from the IRS e-file XML of the latest e-filed return,
     downloaded from the GivingTuesday data-lake mirror (same object_id ProPublica lists), as cceb_990.py does.

Church bodies (religious institutes, parishes) are exempt from filing, so most of them have no 990.

    python3 -I ingest/public/ecosystem/propublica_990.py
"""
from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, REAL, get, now_iso, save_json  # noqa: E402
import registry  # noqa: E402

API = "https://projects.propublica.org/nonprofits/api/v2"
ORG_PAGE = "https://projects.propublica.org/nonprofits/organizations/{}"
GT = "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{}_public.xml"
NS = "{http://www.irs.gov/efile}"
OUT = REAL / "ecosystem" / "form990.json"
CACHE = RAW / "ecosystem" / "propublica"
DELAY = 1.2


def jget(url: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (re.sub(r"[^a-z0-9]+", "_", url.lower())[-150:] + ".json")
    if path.exists():
        import json
        return json.loads(path.read_text())
    r = get(url, timeout=40)
    time.sleep(DELAY)
    if r is None or not r.ok:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    path.write_text(r.text)
    return data


def norm(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"\b(inc|incorporated|corp|corporation|the|of|ca|california|co)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def employees_for(ein: str, tax_prd: int | None) -> dict | None:
    """Latest e-filed 990 XML -> TotalEmployeeCnt (and volunteers)."""
    r = get(ORG_PAGE.format(ein), timeout=40)
    time.sleep(DELAY)
    if r is None or not r.ok:
        return None
    oids = sorted(set(re.findall(r"object_id=(\d+)", r.text)), reverse=True)
    for oid in oids[:4]:
        path = CACHE / "xml" / f"{oid}.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            x = get(GT.format(oid), timeout=60)
            time.sleep(DELAY)
            if x is None or not x.ok:
                continue
            path.write_bytes(x.content)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        hdr = root.find(f"{NS}ReturnHeader")
        rtype = hdr.findtext(f"{NS}ReturnTypeCd") if hdr is not None else None
        if rtype not in ("990", "990EZ"):
            continue
        irs = root.find(f"{NS}ReturnData/{NS}IRS990")
        if irs is None:
            irs = root.find(f"{NS}ReturnData/{NS}IRS990EZ")

        def val(*tags):
            for t in tags:
                v = irs.findtext(f".//{NS}{t}") if irs is not None else None
                if v is not None:
                    try:
                        return int(float(v))
                    except ValueError:
                        return None
            return None
        return {"employees": val("TotalEmployeeCnt"), "volunteers": val("TotalVolunteersCnt"),
                "total_revenue": val("CYTotalRevenueAmt", "TotalRevenueAmt"),
                "total_expenses": val("CYTotalExpensesAmt", "TotalExpensesAmt"),
                "total_assets": val("TotalAssetsEOYAmt", "Form990TotalAssetsGrp/EOYAmt"),
                "total_liabilities": val("TotalLiabilitiesEOYAmt", "SumOfTotalLiabilitiesGrp/EOYAmt"),
                "prior_year_revenue": val("PYTotalRevenueAmt"), "prior_year_expenses": val("PYTotalExpensesAmt"),
                "tax_period_begin": hdr.findtext(f"{NS}TaxPeriodBeginDt"),
                "tax_period_end": hdr.findtext(f"{NS}TaxPeriodEndDt"), "return_type": rtype,
                "xml_url": GT.format(oid), "irs_object_id": oid}
    return None


def summarize(ein_digits: str, fetched: str) -> dict | None:
    url = f"{API}/organizations/{ein_digits}.json"
    data = jget(url)
    if not data or "organization" not in data:
        return None
    org = data["organization"]
    fw = sorted(data.get("filings_with_data") or [], key=lambda f: f.get("tax_prd") or 0, reverse=True)
    out = {"ein": f"{ein_digits[:2]}-{ein_digits[2:]}", "irs_name": org.get("name"), "irs_city": org.get("city"),
           "irs_state": org.get("state"), "ntee_code": org.get("ntee_code"), "subsection_code": org.get("subsection_code"),
           "ruling_date": org.get("ruling_date"), "source_url": url, "propublica_page": ORG_PAGE.format(ein_digits),
           "fetched_at": fetched, "latest": None, "filings_with_data": len(fw),
           "filings_without_data": len(data.get("filings_without_data") or [])}
    api_latest = None
    if fw:
        f = fw[0]
        api_latest = {"tax_period": f.get("tax_prd"), "total_revenue": f.get("totrevenue"),
                      "total_expenses": f.get("totfuncexpns"), "total_assets": f.get("totassetsend"),
                      "total_liabilities": f.get("totliabend"), "pdf_url": f.get("pdf_url"), "source": "propublica_api"}
    x = employees_for(ein_digits, None)
    x_period = int(x["tax_period_end"][:7].replace("-", "")) if x and x.get("tax_period_end") else None
    if x and x_period and (api_latest is None or x_period >= (api_latest["tax_period"] or 0)):
        # The e-file XML is as new or newer than ProPublica's extracted summary: take every figure from it.
        months = None
        if x.get("tax_period_begin") and x.get("tax_period_end"):
            b, e = x["tax_period_begin"], x["tax_period_end"]
            months = (int(e[:4]) - int(b[:4])) * 12 + int(e[5:7]) - int(b[5:7]) + 1
        out["latest"] = {"tax_period": x_period, "period_begin": x.get("tax_period_begin"), "period_end": x.get("tax_period_end"),
                         "period_months": months, "total_revenue": x["total_revenue"], "total_expenses": x["total_expenses"],
                         "total_assets": x["total_assets"], "total_liabilities": x["total_liabilities"],
                         "employees": x["employees"], "volunteers": x["volunteers"],
                         "prior_year_revenue": x.get("prior_year_revenue"), "prior_year_expenses": x.get("prior_year_expenses"),
                         "return_type": x["return_type"],
                         "source": "irs_efile_xml", "xml_url": x["xml_url"], "irs_object_id": x["irs_object_id"]}
        if months and months < 12:
            out["latest"]["note"] = f"short-period return ({months} months, fiscal-year change); see prior_year_revenue for a full year"
        out["propublica_summary_latest"] = api_latest
    elif api_latest:
        out["latest"] = {**api_latest, "employees": None, "volunteers": None}
        if x:
            out["latest"]["employees_note"] = f"newest e-file XML found is for {x_period}; employees not taken from a different year"
    return out


def search(q: str, city: str | None) -> tuple[str | None, list[dict]]:
    url = f"{API}/search.json?q={quote_plus(q)}&state%5Bid%5D=CA"
    data = jget(url) or {}
    cands = []
    for o in data.get("organizations") or []:
        score = SequenceMatcher(None, norm(q), norm(o.get("name"))).ratio()
        city_ok = city is None or (o.get("city") or "").lower() == city.lower()
        cands.append({"ein": str(o.get("ein")), "name": o.get("name"), "city": o.get("city"), "score": round(score, 2),
                      "city_ok": city_ok})
    cands.sort(key=lambda c: (-c["city_ok"], -c["score"]))
    best = next((c for c in cands if c["city_ok"] and (c["score"] >= 0.6 or norm(q) in norm(c["name"]))), None)
    return (best["ein"].zfill(9) if best else None), cands[:5]


def main() -> None:
    fetched = now_iso()
    out, unmatched = {}, []
    for o in registry.ORGS:
        ein = (o.get("ein") or "").replace("-", "")
        basis = "ein_from_source"
        cands = None
        if not ein and o.get("pp_query"):
            ein, cands = search(o["pp_query"], o.get("pp_city"))
            basis = "propublica_search_name_city"
            if not ein:
                unmatched.append({"org_id": o["id"], "query": o["pp_query"], "city": o.get("pp_city"), "candidates": cands})
                continue
        if not ein:
            continue
        s = summarize(ein, fetched)
        if s is None:
            unmatched.append({"org_id": o["id"], "ein": ein, "note": "no ProPublica record"})
            continue
        s["match_basis"] = basis
        if cands:
            s["search_candidates"] = cands
        out[o["id"]] = s
        lt = s.get("latest") or {}
        print(f"{o['id']}: {s['irs_name']} ({s['ein']}) period={lt.get('tax_period')} rev={lt.get('total_revenue')} emp={lt.get('employees')} via {lt.get('source')}")
    save_json(OUT, {"source": f"{API} (ProPublica Nonprofit Explorer) + IRS e-file XML via {GT.format('<object_id>')}",
                    "fetched_at": fetched, "count": len(out), "records": out, "unmatched": unmatched})
    print(f"{len(out)} orgs with 990 data; {len(unmatched)} unmatched")


if __name__ == "__main__":
    main()
