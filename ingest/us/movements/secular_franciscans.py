"""Secular Franciscan Order - USA (OFS-USA) "Map of Local Fraternities" -> fraternities and where they meet (2026-11-02).

The page https://www.secularfranciscansusa.org/map-of-local-fraternities/ ("Find a Fraternity near you" on the national
site) embeds its map data (WP Google Map plugin) as one base64 JSON block in the HTML: ~530 fraternities, each with the
fraternity name, number, region, the meeting place's name and street address and coordinates. One request.
robots.txt (checked at run time) disallows only /wp-admin/.

Stored (institutional only): fraternity name and number, region, meeting place name, street address, ZIP, state,
coordinates. The plugin's contact e-mail / phone fields are dropped (people's contacts; the page itself says to ask the
regional minister).
    python3 -I ingest/us/movements/secular_franciscans.py
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import common_fetch as cf  # noqa: E402
sys.path.insert(0, str(HERE.parent.parent / "public"))
import privacy as pv  # noqa: E402  (the shared privacy screen: a private name in a free-text field -> "[name withheld]")

PAGE = "https://www.secularfranciscansusa.org/map-of-local-fraternities/"
OUTF = c.OUT / "secular_franciscans.json"
BLOCK = re.compile(r'class="wpgmp-map-data-container"[^>]*>([A-Za-z0-9+/=\s]+)<')
ST_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?(?:,?\s*USA)?\s*$")


def main() -> None:
    pol = cf.Polite()
    rb = pol.robots_for(PAGE)
    robots = {"url": rb["url"], "status": rb["status"], "class": rb["class"], "crawl_delay": rb.get("crawl_delay"),
              "page_allowed": pol.allowed(PAGE)}
    if not robots["page_allowed"]:
        print("robots.txt disallows the page; stopping"); return
    r = pol.get(PAGE, timeout=60, max_bytes=8_000_000)
    if r["status"] != 200:
        print("fetch:", r["status"], r.get("class")); return
    html = r["body"].decode("utf-8", "replace")
    places = []
    for m in BLOCK.finditer(html):
        s = re.sub(r"\s+", "", m.group(1))
        try:
            d = json.loads(base64.b64decode(s + "=" * (-len(s) % 4)))
        except Exception:
            continue
        places += d.get("places") or []
    recs = []
    for p in places:
        loc = p.get("location") or {}
        ex = loc.get("extra_fields") or {}
        addr = re.sub(r"\s+", " ", p.get("address") or "").strip()
        if (loc.get("country") or "USA") not in ("USA", "US", "United States"):
            continue
        m = ST_ZIP.search(addr)
        state = (m.group(1) if m else (loc.get("state") or "")).upper()[:2]
        z = (m.group(2) if m else (loc.get("postal_code") or "")[:5]) or None
        parts = [x.strip() for x in addr.split(",")]
        street = parts[0] if addr else None
        tparts = [x.strip() for x in (p.get("title") or "").split("-")]
        city = parts[1] if len(parts) >= 3 else (tparts[-2] if len(tparts) >= 3 else None)
        recs.append({"id": p.get("id"), "fraternity": (p.get("title") or "").strip(), "number": ex.get("fraternitynumber") or None,
                     "region": ex.get("region") or None, "meeting_place": (ex.get("locationname") or "").strip() or None,
                     "address": addr or None, "street": street, "city": city, "state": state or None, "zip": z,
                     "lat": float(loc["lat"]) if loc.get("lat") else None, "lng": float(loc["lng"]) if loc.get("lng") else None,
                     "language": ex.get("primarylanguage") or ex.get("primarylanuage") or None,
                     "source_url": PAGE, "fetched_at": c.now_iso()})
    withheld = 0
    for x in recs:
        for k in ('meeting_place',):
            if x.get(k):
                x[k], n = pv.redact(x[k])
                withheld += n
    if not recs:
        print("no map data found"); return
    old = {r["id"]: r for r in (c.load(OUTF, {}) or {}).get("records", [])}
    for rr in recs:
        if old.get(rr["id"], {}).get("match"):
            rr["match"] = old[rr["id"]]["match"]
    recs.sort(key=lambda x: (x.get("state") or "", x.get("fraternity") or ""))
    c.save(OUTF, {"source": "Secular Franciscan Order - USA: Map of Local Fraternities", "source_url": PAGE,
                  "user_agent": c.UA, "robots": robots, "fetched_at": c.now_iso(), "count": len(recs),
                  "fields": "institutional only: fraternity name/number, region, meeting place, street address, ZIP, state, coordinates",
                  "note": "contact e-mail/phone fields of the map plugin are not stored; parish match by match.py into the 'match' field",
                  "privacy": f"ingest/public/privacy.py redact on {('meeting_place',)}: {withheld} name(s) withheld",
                  "records": recs}, list_key="records")
    print(len(recs), "fraternities")


if __name__ == "__main__":
    main()
