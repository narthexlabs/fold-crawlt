"""Chapter 11 timeline for The Roman Catholic Bishop of Oakland (Bankr. N.D. Cal. 23-40523).

Sources:
  * https://www.oakdiocese.org/chapter-11/ (diocese's own updates)
  * https://www.kccllc.net/RCBO -> https://www.veritaglobal.net/RCBO (claims agent docket)
  * court calendar, Leagle decisions, trade press (cited per item)

Both primary sites refuse scripted requests from the collection environment (oakdiocese.org: 403
from its Azure gateway; veritaglobal.net: not reachable). The milestone list is therefore curated in
chapter11_curated.json (each item cites its source). This script:
  1. tries to fetch the two primary pages and, if it succeeds, records the PDF/document links found
     there under `live_capture` (so a rerun from an unblocked network adds docket links),
  2. validates and sorts the curated milestones and writes data/real/diocese/chapter11_timeline.json.

Run:  python -I ingest/public/diocese/chapter11.py [--no-download]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CURATED = HERE / "chapter11_curated.json"
OUT = ROOT / "data" / "real" / "diocese" / "chapter11_timeline.json"
UA = "OaklandDioceseDashboard-research/0.1 (public-data collection)"
PRIMARY = {
    "diocese_chapter11_page": "https://www.oakdiocese.org/chapter-11/",
    "claims_agent": "https://www.kccllc.net/RCBO",
}


def try_fetch(url: str) -> dict:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        return {"url": url, "ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    res = {"url": url, "final_url": r.url, "status": r.status_code, "ok": r.status_code == 200}
    if res["ok"]:
        links = sorted(set(re.findall(r'href="([^"]+?(?:\.pdf|/document/[^"]*))"', r.text)))
        res["document_links"] = links[:500]
    return res


def main():
    cur = json.loads(CURATED.read_text(encoding="utf-8"))
    live = {} if "--no-download" in sys.argv else {k: try_fetch(u) for k, u in PRIMARY.items()}

    ms = cur["milestones"]
    for m in ms:
        assert re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", m["date"]), m
        assert m["source_url"].startswith("http"), m
    ms.sort(key=lambda m: (m["date"] + "-00")[:10])

    out = {
        "dataset": "Diocese of Oakland Chapter 11 milestone timeline",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provenance": cur["_about"],
        "case": cur["case"],
        "milestone_count": len(ms),
        "milestones": ms,
        "headline_numbers": cur["headline_numbers"],
        "excluded": cur.get("excluded", []),
        "live_capture": live,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"milestones": len(ms), "with_docket_no": sum(1 for m in ms if m.get("docket_no")),
                      "live": {k: (v.get("status"), v.get("error")) for k, v in live.items()}}, indent=1))


if __name__ == "__main__":
    main()
