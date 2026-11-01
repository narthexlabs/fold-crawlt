"""Knights of Columbus councils in Alameda + Contra Costa counties -> data/real/ecosystem/kofc_councils.json

Source: the public JSON endpoint behind kofc.org's own "Find a Council" page
(https://www.kofc.org/get-involved/find-a-council/), queried by ZIP code:
    https://www.kofc.org/api/FindACouncilSearch/councils/countryZip/USA/<zip>/
It returns councils near the ZIP with number, name, city, ZIP and assembly, but no meeting address. So each
council is matched to a parish only when (a) a parish website names the council number, or (b) the council
name is the parish's name and the city matches. Everything else stays unmatched (no guessing by distance).

    python3 -I ingest/public/ecosystem/kofc_councils.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, get, load_json, now_iso, save_json  # noqa: E402

API = "https://www.kofc.org/api/FindACouncilSearch/councils/countryZip/USA/{}/"
FINDER = "https://www.kofc.org/get-involved/find-a-council/"
OUT = REAL / "ecosystem" / "kofc_councils.json"
CACHE = RAW / "ecosystem" / "kofc"

ALAMEDA = {"alameda", "albany", "berkeley", "castro valley", "dublin", "emeryville", "fremont", "hayward", "livermore",
           "newark", "oakland", "piedmont", "pleasanton", "san leandro", "san lorenzo", "union city", "sunol"}
CONTRA_COSTA = {"antioch", "bay point", "brentwood", "byron", "clayton", "concord", "crockett", "danville", "el cerrito",
                "el sobrante", "hercules", "kensington", "lafayette", "martinez", "moraga", "oakley", "orinda", "pinole",
                "pittsburg", "pleasant hill", "point richmond", "port costa", "richmond", "rodeo", "san pablo",
                "san ramon", "walnut creek", "discovery bay", "pacheco", "alamo", "diablo"}

# Extra ZIPs so outlying areas without a parish ZIP are covered too.
EXTRA_ZIPS = ["94501", "94502", "94536", "94538", "94539", "94541", "94544", "94545", "94546", "94550", "94551",
              "94552", "94555", "94560", "94566", "94568", "94577", "94578", "94579", "94580", "94586", "94587",
              "94601", "94602", "94603", "94605", "94606", "94607", "94608", "94609", "94610", "94611", "94612",
              "94618", "94619", "94621", "94702", "94703", "94704", "94705", "94706", "94707", "94708", "94709",
              "94710", "94505", "94506", "94507", "94509", "94511", "94513", "94514", "94517", "94518", "94519",
              "94520", "94521", "94523", "94525", "94526", "94530", "94531", "94547", "94549", "94553", "94556",
              "94561", "94563", "94564", "94565", "94569", "94572", "94582", "94583", "94595", "94596", "94597",
              "94598", "94801", "94803", "94804", "94805", "94806"]


def county(city: str) -> str | None:
    c = city.lower().strip()
    return "Alameda" if c in ALAMEDA else "Contra Costa" if c in CONTRA_COSTA else None


def main() -> None:
    fetched = now_iso()
    dirx = load_json(REAL / "parishes_directory.json")
    zips = sorted({r.get("zip") for r in dirx["records"] if r.get("zip")} | set(EXTRA_ZIPS))
    CACHE.mkdir(parents=True, exist_ok=True)
    councils: dict[str, dict] = {}
    queried = []
    for z in zips:
        path = CACHE / f"{z}.json"
        if path.exists():
            data = json.loads(path.read_text())
        else:
            r = get(API.format(z), timeout=40)
            if r is None or not r.ok:
                queried.append({"zip": z, "status": getattr(r, "status_code", None)})
                time.sleep(1.5)
                continue
            data = r.json()
            path.write_text(json.dumps(data))
            time.sleep(1.2)
        queried.append({"zip": z, "status": 200, "n": len(data)})
        for c in data:
            num = (c.get("councilNumber") or "").strip()
            if not num:
                continue
            cty = (c.get("city") or c.get("location") or "").title()
            if not county(cty):
                continue
            councils.setdefault(num, {
                "council_number": num,
                "council_name": (c.get("councilName") or "").title(),
                "type_code": c.get("type"),
                "city": cty, "county": county(cty), "state": c.get("state"), "zip": c.get("zipCode"),
                "assembly": c.get("assembly"), "website": c.get("webSite") or None,
                "source_url": API.format(z), "finder_url": FINDER, "fetched_at": fetched,
            })
    rows = sorted(councils.values(), key=lambda x: (x["county"], x["city"], int(re.sub(r"\D", "", x["council_number"]) or 0)))
    save_json(OUT, {
        "source": FINDER, "api": API.format("<zip>"), "fetched_at": fetched,
        "notes": ["Councils whose listed city is in Alameda or Contra Costa county. The API gives no meeting place;"
                  " parish links are made in build_ecosystem.py from parish-site mentions and name+city matches only.",
                  "type_code: R = regular council (KofC code)."],
        "zips_queried": queried, "count": len(rows), "councils": rows})
    print(f"{len(rows)} councils from {len(zips)} ZIPs")


if __name__ == "__main__":
    main()
