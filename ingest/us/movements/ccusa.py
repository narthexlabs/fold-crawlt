"""Catholic Charities USA member-agency locator -> data/real/us/movements/catholic_charities.json (lane D, source 5).

https://www.catholiccharitiesusa.org/about-us/find-a-local-agency/ embeds every member agency as JSON
(<script type="application/json">{"data":[...]}) for its map. One request. These are ENTITIES (agencies with offices),
not parish groups, so nothing goes into by_parish.json. Stored: agency name, street address, city, state, ZIP, lat/lng,
website, the number of ZIP codes it lists as served. Not stored: email, phone, fax, CRM id.

    python3 -I ingest/us/movements/ccusa.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import common_fetch as cf  # noqa: E402

URL = "https://www.catholiccharitiesusa.org/about-us/find-a-local-agency/"


def main() -> None:
    pol = cf.Polite()
    rb = pol.robots_for(URL)
    r = pol.get(URL)
    out = {"source": "Catholic Charities USA - Find a local agency", "source_url": URL, "user_agent": c.UA,
           "robots": {"url": rb["url"], "status": rb["status"], "allowed": pol.allowed(URL)},
           "fetched_at": c.now_iso(), "http_status": r["status"], "class": r.get("class"),
           "kind": "entities (agencies), not parish groups; not merged into by_parish.json"}
    recs = []
    if r["status"] == 200:
        h = r["body"].decode("utf-8", "replace")
        dec = json.JSONDecoder()
        for m in re.finditer(r'<script type="application/json">\s*(?=\{"data":\[)', h):
            try:
                rows = dec.raw_decode(h, m.end())[0]["data"]
            except Exception:
                continue
            for x in rows:
                if not isinstance(x, dict) or not x.get("title"):
                    continue
                recs.append({"name": x.get("title"), "address": x.get("address") or None, "city": x.get("city") or None,
                             "state": x.get("state") or None, "zip": x.get("zip_code") or None,
                             "lat": float(x["latitude"]) if x.get("latitude") else None,
                             "lng": float(x["longitude"]) if x.get("longitude") else None,
                             "website": x.get("website_url") or None,
                             "served_zip_count": len(x.get("serviced_zip") or []),
                             "served_zips": x.get("serviced_zip") or [],
                             "source_url": URL, "fetched_at": out["fetched_at"]})
    seen, uniq = set(), []
    for x in recs:
        k = (x["name"], x["city"], x["state"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    out["count"] = len(uniq)
    out["records"] = sorted(uniq, key=lambda x: (x["state"] or "", x["name"]))
    c.save(c.OUT / "catholic_charities.json", out, list_key="records")
    print(out["http_status"], out["count"], "agencies")


if __name__ == "__main__":
    main()
