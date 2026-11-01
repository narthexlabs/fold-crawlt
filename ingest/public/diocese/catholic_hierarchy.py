"""catholic-hierarchy.org statistics table for the Diocese of Oakland.

Source: https://www.catholic-hierarchy.org/diocese/doakl.html (compiled by David M. Cheney from the
Annuario Pontificio; the 'Source' column, e.g. ap2024, is the Annuario edition, and 'Year' is the
data year). The site's HTML leaves <td> unclosed, so rows are split on '<td>' directly; empty
cells are kept as null (not reported), never zero.

Writes data/real/diocese/hierarchy_stats.json.
Run:  python -I ingest/public/diocese/catholic_hierarchy.py [--no-download]
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "diocese" / "ch-oakland.html"
OUT = ROOT / "data" / "real" / "diocese" / "hierarchy_stats.json"
URL = "https://www.catholic-hierarchy.org/diocese/doakl.html"
UA = "OaklandDioceseDashboard-research/0.1 (public-data collection)"

COLS = ["year", "catholics", "total_population", "percent_catholic", "diocesan_priests", "religious_priests",
        "total_priests", "catholics_per_priest", "permanent_deacons", "male_religious", "female_religious",
        "parishes", "source"]


def fetch() -> str:
    if "--no-download" not in sys.argv:
        r = requests.get(URL, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_bytes(r.content)
    return RAW.read_bytes().decode("latin-1")


def num(s: str):
    s = s.strip()
    if not s:
        return None
    if s.endswith("%"):
        return float(s[:-1])
    return int(s.replace(",", ""))


def main():
    h = fetch()
    hdr = h.find("<th>Year</th>")
    if hdr < 0:
        raise SystemExit("statistics table header not found")
    end = h.find("</table>", hdr)
    body = h[hdr:end]
    header = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<th>(.*?)</th>", body)]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.S):
        if "<th" in tr:
            continue
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in tr.split("<td>")[1:]]
        if len(cells) != len(COLS):
            rows.append({"_unparsed": tr[:300]}); continue
        rec = {}
        for k, v in zip(COLS, cells):
            rec[k] = v if k == "source" else num(v)
        rec["source_note"] = "Annuario Pontificio " + rec["source"].lstrip("ap").rstrip("*") + (
            " (* = figure flagged by catholic-hierarchy.org)" if rec["source"].endswith("*") else "")
        rows.append(rec)
    m = re.search(r"Data: ([0-9]{1,2} \w+ [0-9]{4})", h)
    info = {}
    gi = h.find("General Information")
    gtext = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[gi:gi + 4000]))) if gi >= 0 else ""
    labels = ["Type of Jurisdiction", "Erected", "Metropolitan", "Depends on", "Rite", "State", "Country",
              "Square Kilometers", "Description", "Conference Region", "Catholic Directory Abbreviation",
              "Official Web Site", "Mailing Address", "Telephone", "Fax"]
    for i, label in enumerate(labels):
        nxt = "|".join(re.escape(l) + ":" for l in labels[i + 1:]) or "$"
        mm = re.search(re.escape(label) + r":\s*(.*?)\s*(?=" + nxt + r"|Past and Present Ordinaries|Date Event)", gtext)
        if mm:
            info[label] = mm.group(1).strip()
    ordinaries = []
    po = gtext.find("Past and Present Ordinaries")
    if po >= 0:
        seg = gtext[po + len("Past and Present Ordinaries"):].split("Historical Summary")[0]
        for nm, span in re.findall(r"([A-Z][^()]+?)\s*\(\s*([^)]*?(?:\([^)]*\)[^)]*?)*)\)", seg):
            ordinaries.append({"name": nm.replace("†", "").strip(" ,"), "deceased": "†" in nm, "tenure": span.strip()})
    info["past_and_present_ordinaries"] = ordinaries
    out = {
        "dataset": "catholic-hierarchy.org - Diocese of Oakland statistics by year",
        "source_url": URL,
        "site_data_date": m.group(1) if m else None,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "columns_on_site": header,
        "notes": [
            "'year' is the data year; 'source' is the Annuario Pontificio edition (apYYYY).",
            "Blank cells on the site are null here (not reported), not zero.",
            "'Total Priests' = diocesan + religious priests incardinated/resident per the Annuario.",
            "Catholic population is a diocesan self-report to the Annuario; compare: diocese's own Chapter 11 page says ~500,000-550,000.",
        ],
        "general_info": info,
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    bad = [r for r in rows if "_unparsed" in r or (r.get("diocesan_priests") is not None and r.get("religious_priests") is not None and r["diocesan_priests"] + r["religious_priests"] != r["total_priests"])]
    print(json.dumps({"rows": len(rows), "years": [r.get("year") for r in rows], "checks_failed": bad, "info": info}, indent=1))


if __name__ == "__main__":
    main()
