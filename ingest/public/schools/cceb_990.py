"""Catholic Charities East Bay (EIN 94-2677202) Form 990s -> data/real/cceb_990.json
and the cceb.org program list -> data/real/cceb_programs.json

Sources
  * ProPublica Nonprofit Explorer API (summary fields, FY2011-FY2023):
    https://projects.propublica.org/nonprofits/api/v2/organizations/942677202.json
  * IRS e-file XML for every year ProPublica lists (FY2014-FY2025, including FY2024 and FY2025, which
    the API has no extracted data for). ProPublica's own XML download sits behind a bot check, so the
    XML comes from the GivingTuesday 990 data lake mirror of the IRS files, keyed by the same object_id:
    https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/<object_id>_public.xml
  * cceb.org program pages.

Fiscal year ends April 30; "fiscal_year" below is the calendar year the FY ends in.

Privacy: Part VII lists officers, directors and key employees. Staff (paid, or working 20+ hours a week) and clergy
are kept by name, in their public roles; unpaid lay board members are a lay committee, so save_json's privacy screen
(ingest/public/privacy.py, rule "board") replaces their names with "[name withheld]" and keeps title, hours and
flags. officer_comp_summary lists paid people only.

Run: python -I ingest/public/schools/cceb_990.py
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schools import RAW, REAL, SESSION, download, now_iso, save_json  # noqa: E402

EIN = "942677202"
API = f"https://projects.propublica.org/nonprofits/api/v2/organizations/{EIN}.json"
ORG_PAGE = f"https://projects.propublica.org/nonprofits/organizations/{EIN}"
GT = "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{}_public.xml"
RAW_CC = RAW / "cceb"
NS = "{http://www.irs.gov/efile}"

PP_FIELDS = {"totrevenue": "total_revenue", "totfuncexpns": "total_expenses", "totassetsend": "total_assets",
             "totliabend": "total_liabilities", "totnetassetend": "net_assets", "totcntrbgfts": "contributions",
             "totprgmrevnue": "program_service_revenue", "invstmntinc": "investment_income",
             "compnsatncurrofcr": "officer_compensation", "othrsalwages": "other_salaries_wages",
             "payrolltx": "payroll_taxes", "profndraising": "professional_fundraising"}

X_FIELDS = {  # IRS990 element -> output key (2013+ schema names)
    "CYTotalRevenueAmt": "total_revenue", "CYTotalExpensesAmt": "total_expenses",
    "CYRevenuesLessExpensesAmt": "revenue_less_expenses",
    "CYContributionsGrantsAmt": "contributions", "CYProgramServiceRevenueAmt": "program_service_revenue",
    "CYInvestmentIncomeAmt": "investment_income", "CYOtherRevenueAmt": "other_revenue",
    "CYGrantsAndSimilarPaidAmt": "grants_paid", "CYSalariesCompEmpBnftPaidAmt": "salaries_benefits",
    "CYTotalFundraisingExpenseAmt": "fundraising_expense",
    "TotalAssetsEOYAmt": "total_assets", "TotalLiabilitiesEOYAmt": "total_liabilities",
    "NetAssetsOrFundBalancesEOYAmt": "net_assets", "TotalEmployeeCnt": "employees",
    "TotalVolunteersCnt": "volunteers", "GoverningBodyVotingMembersCnt": "board_voting_members",
    "IndependentVotingMemberCnt": "board_independent_members",
    "IndivRcvdGreaterThan100KCnt": "people_paid_over_100k",
    "ActivityOrMissionDesc": "mission",
}


def num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v.strip()


def local(tag: str) -> str:
    return tag.replace(NS, "")


def parse_990(path: Path) -> dict:
    root = ET.parse(path).getroot()
    hdr = root.find(f"{NS}ReturnHeader")
    rtype = hdr.findtext(f"{NS}ReturnTypeCd") or hdr.findtext(f"{NS}ReturnType")
    out = {"return_type": rtype, "tax_period_end": hdr.findtext(f"{NS}TaxPeriodEndDt"),
           "return_version": root.get("returnVersion"), "filed_at": hdr.findtext(f"{NS}ReturnTs")}
    irs = root.find(f"{NS}ReturnData/{NS}IRS990")
    if irs is None:
        return out
    out["amended"] = irs.find(f"{NS}AmendedReturnInd") is not None
    for el in irs.iter():
        k = X_FIELDS.get(local(el.tag))
        if k and k not in out:
            out[k] = num(el.text)
    fx = irs.find(f"{NS}TotalFunctionalExpensesGrp")
    if fx is not None:
        out["functional_expenses"] = {local(c.tag).replace("Amt", ""): num(c.text) for c in fx}
    # Part III program lines. Program 1 is unwrapped (ExpenseAmt/GrantAmt/RevenueAmt/Desc directly under
    # IRS990); 2, 3 and "other" sit in ProgSrvcAccomActy*Grp.
    progs = []
    kids = list(irs)
    first = {}
    for c in kids:
        n = local(c.tag)
        if n in ("ExpenseAmt", "GrantAmt", "RevenueAmt", "Desc", "ActivityCd"):
            first[n] = c.text
    if first:
        progs.append(first)
    for c in kids:
        if re.match(r"ProgSrvcAccomActy(\d|Other)Grp", local(c.tag)):
            progs.append({local(g.tag): g.text for g in c})
    out["programs"] = []
    for p in progs:
        desc = (p.get("Desc") or "").strip()
        name = desc.split(":", 1)[0].strip().title() if ":" in desc[:80] else None
        out["programs"].append({"name": name, "expense": num(p.get("ExpenseAmt")),
                                "grants": num(p.get("GrantAmt")), "revenue": num(p.get("RevenueAmt")),
                                "description": desc})
    people = []
    for g in irs.findall(f"{NS}Form990PartVIISectionAGrp"):
        nm = g.findtext(f"{NS}PersonNm") or g.findtext(f"{NS}BusinessName/{NS}BusinessNameLine1Txt")
        people.append({
            "name": (nm or "").title(), "title": (g.findtext(f"{NS}TitleTxt") or "").title(),
            "hours_per_week": num(g.findtext(f"{NS}AverageHoursPerWeekRt")),
            "comp_org": num(g.findtext(f"{NS}ReportableCompFromOrgAmt")) or 0,
            "comp_related": num(g.findtext(f"{NS}ReportableCompFromRltdOrgAmt")) or 0,
            "comp_other": num(g.findtext(f"{NS}OtherCompensationAmt")) or 0,
            "officer": g.find(f"{NS}OfficerInd") is not None,
            "key_employee": g.find(f"{NS}KeyEmployeeInd") is not None,
            "highest_comp": g.find(f"{NS}HighestCompensatedEmployeeInd") is not None,
            "director": g.find(f"{NS}IndividualTrusteeOrDirectorInd") is not None,
        })
    paid = sorted([p for p in people if p["comp_org"] + p["comp_related"] + p["comp_other"] > 0],
                  key=lambda p: -(p["comp_org"] + p["comp_related"] + p["comp_other"]))
    out["officer_comp_summary"] = {
        "people_listed": len(people),
        "people_paid": len(paid),
        "total_reportable_comp": sum(p["comp_org"] + p["comp_related"] for p in people),
        "total_other_comp": sum(p["comp_other"] for p in people),
        "top_paid": [{k: p[k] for k in ("name", "title", "comp_org", "comp_other")} for p in paid[:5]],
        "ceo": next(({k: p[k] for k in ("name", "title", "comp_org", "comp_other")} for p in people
                     if re.search(r"\bCeo\b|Chief Executive|Executive Director", p["title"])), None),
    }
    out["officers_and_directors"] = people
    return out


def programs_from_site(fetched_at: str) -> list[dict]:
    pages = [("Housing Services", "https://www.cceb.org/housing-services/"),
             ("Immigration Legal Services", "https://www.cceb.org/immigration-legal-services/"),
             ("Mental Health and Wellness", "https://www.cceb.org/mental-health-and-wellness/"),
             ("Joy Bells", "https://www.cceb.org/joy-bells/"),
             ("Community Clinics", "https://www.cceb.org/community-clinics/")]
    out = []
    for name, url in pages:
        r = SESSION.get(url, timeout=30)
        if r.status_code != 200:
            out.append({"name": name, "url": url, "status": r.status_code, "source_url": url, "fetched_at": fetched_at})
            continue
        s = re.sub(r"<(script|style|header|footer|nav)[\s\S]*?</\1>", " ", r.text)
        paras = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", x))).strip()
                 for x in re.findall(r"<p[^>]*>([\s\S]*?)</p>", s)]
        paras = [x for x in paras if len(x) > 80 and "casino" not in x.lower() and "scam" not in x.lower()]
        subs = []
        for x in re.findall(r"<h[234][^>]*>([\s\S]*?)</h[234]>", s):
            x = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", x))).strip()
            if 3 < len(x) < 80 and x not in subs and not re.search(r"(?i)alert|scam|find a catholic|additional resources", x):
                subs.append(x)
        out.append({
            "name": name, "url": url,
            "summary": paras[0] if paras else None,
            "sub_programs": subs[:20],
            "source_url": url, "fetched_at": fetched_at,
        })
    return out


def main() -> None:
    fetched_at = now_iso()
    api = SESSION.get(API, timeout=60).json()
    org_html = SESSION.get(ORG_PAGE, timeout=60).text
    obj_ids = sorted(set(re.findall(r"object_id=(\d+)", org_html)))
    years = {}
    for f in api["filings_with_data"]:
        fy = int(str(f["tax_prd"])[:4])
        years.setdefault(fy, {"fiscal_year": fy, "fiscal_year_end": f"{fy}-04-30"})
        years[fy]["propublica"] = {v: f.get(k) for k, v in PP_FIELDS.items()}
        years[fy]["pdf_url"] = f.get("pdf_url")
        years[fy]["source_url"] = [API]
    for f in api["filings_without_data"]:
        fy = int(str(f.get("tax_prd") or f.get("tax_prd_yr"))[:4])
        y = years.setdefault(fy, {"fiscal_year": fy, "fiscal_year_end": None, "source_url": [API]})
        y.setdefault("pdf_url", f.get("pdf_url"))
    for oid in obj_ids:
        url = GT.format(oid)
        path = download(url, RAW_CC / "xml" / f"{oid}.xml")
        x = parse_990(path)
        if x.get("return_type") != "990":
            continue  # 990-T (unrelated business income) returns are skipped
        fy = int(x["tax_period_end"][:4])
        y = years.setdefault(fy, {"fiscal_year": fy, "source_url": []})
        y["fiscal_year_end"] = x["tax_period_end"]
        y["irs_object_id"] = oid
        y["xml"] = x
        y["source_url"] = y.get("source_url", []) + [url, f"{ORG_PAGE}"]
        y["fetched_at"] = fetched_at
    rows = []
    for fy in sorted(years):
        y = years[fy]
        x, pp = y.get("xml", {}), y.get("propublica", {})
        pick = lambda k: x.get(k) if x.get(k) is not None else pp.get(k)  # noqa: E731
        rows.append({
            "fiscal_year": fy, "fiscal_year_end": y.get("fiscal_year_end"),
            "total_revenue": pick("total_revenue"), "total_expenses": pick("total_expenses"),
            "net": (pick("total_revenue") - pick("total_expenses")) if pick("total_revenue") is not None and pick("total_expenses") is not None else None,
            "total_assets": pick("total_assets"), "total_liabilities": pick("total_liabilities"),
            "net_assets": pick("net_assets"), "contributions": pick("contributions"),
            "program_service_revenue": pick("program_service_revenue"), "investment_income": pick("investment_income"),
            "grants_paid": x.get("grants_paid"), "salaries_benefits": x.get("salaries_benefits"),
            "fundraising_expense": x.get("fundraising_expense"),
            "functional_expenses": x.get("functional_expenses"),
            "employees": x.get("employees"), "volunteers": x.get("volunteers"),
            "programs": x.get("programs"),
            "officer_comp_summary": x.get("officer_comp_summary"),
            "officers_and_directors": x.get("officers_and_directors"),
            "mission": x.get("mission"),
            "amended_return": x.get("amended"), "filed_at": x.get("filed_at"),
            "original_return_differs": (bool(x.get("amended")) and pp.get("total_revenue") is not None
                                        and pp.get("total_revenue") != x.get("total_revenue")),
            "data_basis": "irs_xml" if x else ("propublica_api" if pp else "pdf_only"),
            "propublica_summary": pp or None,
            "pdf_url": y.get("pdf_url"),
            "irs_object_id": y.get("irs_object_id"),
            "source_url": y.get("source_url") or [API],
            "fetched_at": fetched_at,
        })
    save_json(REAL / "cceb_990.json", {
        "organization": api["organization"]["name"], "ein": "94-2677202",
        "fiscal_year_note": "Fiscal year ends April 30; fiscal_year = calendar year of FY end.",
        "amendment_note": "FY2022 and FY2023 values come from amended 990s (filed Aug 2024 and Mar 2025). ProPublica's "
                          "summary (propublica_summary) still reflects the original returns, e.g. FY2023 revenue "
                          "$7.04M original vs $8.35M amended.",
        "sources": [API, ORG_PAGE, "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/"],
        "fetched_at": fetched_at,
        "years": rows,
    })
    progs = programs_from_site(fetched_at)
    save_json(REAL / "cceb_programs.json", {
        "organization": "Catholic Charities East Bay", "website": "https://www.cceb.org/",
        "service_centers": [
            {"name": "Oakland Service Center", "address": "433 Jefferson Street", "city": "Oakland", "zip": "94607"},
            {"name": "Richmond Service Center", "address": "217 Harbour Way", "city": "Richmond", "zip": "94801"},
            {"name": "Concord Service Center", "address": "2120 Diamond Blvd #220", "city": "Concord", "zip": "94520"},
        ],
        "service_centers_source_url": "https://www.cceb.org/locations/",
        "main_phone": "510.768.3100",
        "impact_2025": {"calls_for_help": 28637, "food_assistance_usd": 29190, "housing_financial_assistance_usd": 762670,
                        "source_url": "https://www.cceb.org/our-impact/"},
        "site_integrity_warning": "cceb.org/locations/ contains injected online-casino spam text (SEO spam); the "
                                  "addresses were kept, the spam ignored. Worth flagging to CCEB.",
        "programs": progs,
        "fetched_at": fetched_at,
    })
    for r in rows:
        print(r["fiscal_year"], r["data_basis"], r["total_revenue"], r["total_expenses"], r["total_assets"],
              len(r["programs"] or []), (r["officer_comp_summary"] or {}).get("people_paid"))
    print("programs:", [(p["name"], len(p.get("sub_programs") or [])) for p in progs])


if __name__ == "__main__":
    main()
