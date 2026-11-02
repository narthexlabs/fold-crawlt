"""Paradisus Dei program finder -> parishes hosting That Man Is You!, She Shall Be Called Woman and the other Paradisus
Dei programs (2026-11-02).

tmiy.org no longer resolves; the program lives at https://paradisusdei.org/that-man-is-you/, whose "Find a group near
you" button opens https://programs.paradisusdei.org/search. That page asks its own JSON route
    GET https://programs.paradisusdei.org/api/schools?search_type=current&lat=<lat>&lng=<lng>&miles=<miles>
for the participating parishes ("schools") within a radius. robots.txt of programs.paradisusdei.org is empty (all allowed;
checked at run time). Three requests cover the country: the lower 48 (centre of the US, 3,000 miles), Alaska and Hawaii.

Stored per parish (institutional only): the finder's id, parish name, street address, city, state, coordinates, diocese,
language, program tags, number of active classes. The finder's per-course coordinator name / e-mail / phone (a separate
route, /places/courses) is never requested.
    python3 -I ingest/us/movements/paradisus_dei.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import common_fetch as cf  # noqa: E402

PAGE = "https://programs.paradisusdei.org/search"
API = "https://programs.paradisusdei.org/api/schools?search_type=current&lat={lat}&lng={lng}&miles={mi}"
CENTRES = [(39.5, -98.35, 3000), (61.2, -149.9, 1500), (21.3, -157.85, 600)]
OUTF = c.OUT / "paradisus_dei.json"
US = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
         "OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI".split())
PROGRAMS = {"TMIY": "That Man Is You!", "SHE": "She Shall Be Called Woman", "Mysteries": "Mysteries of the Rosary",
            "EHET": "EHET (men's program in Spanish)", "Choice Wine": "The Choice Wine"}


def main() -> None:
    pol = cf.Polite()
    u0 = API.format(lat=39.5, lng=-98.35, mi=3000)
    rb = pol.robots_for(u0)
    robots = {"url": rb["url"], "status": rb["status"], "class": rb["class"], "crawl_delay": rb.get("crawl_delay"),
              "api_allowed": pol.allowed(u0)}
    if not robots["api_allowed"]:
        print("robots.txt disallows the finder route; stopping"); return
    recs: dict = {}
    calls = []
    for lat, lng, mi in CENTRES:
        url = API.format(lat=lat, lng=lng, mi=mi)
        r = pol.get(url, accept="application/json", timeout=60, max_bytes=8_000_000)
        calls.append({"url": url, "status": r["status"], "class": r.get("class")})
        if r["status"] != 200:
            continue
        try:
            rows = json.loads(r["body"].decode("utf-8", "replace"))
        except Exception:
            calls[-1]["class"] = "bad_json"
            continue
        for x in rows:
            st = (x.get("state") or "").strip()
            if st not in US or not x.get("id"):
                continue
            recs[x["id"]] = {"id": x["id"], "name": (x.get("name") or "").strip(), "address": (x.get("address") or "").strip() or None,
                             "city": (x.get("city") or "").strip() or None, "state": st, "zip": (x.get("zip") or "").strip() or None,
                             "lat": x.get("lat"), "lng": x.get("lng"), "diocese": x.get("diocese"), "language": x.get("language"),
                             "programs": sorted(set(x.get("tags") or [])), "active_classes": x.get("active_classes"),
                             "source_url": url, "fetched_at": c.now_iso()}
    if not recs:
        print("no records:", calls); return
    old = {r["id"]: r for r in (c.load(OUTF, {}) or {}).get("records", [])}
    for k, r in recs.items():
        if old.get(k, {}).get("match"):
            r["match"] = old[k]["match"]
    out = sorted(recs.values(), key=lambda r: (r["state"], r.get("city") or "", r["name"]))
    c.save(OUTF, {"source": "Paradisus Dei - Find a group near you (That Man Is You! and other programs)", "source_url": PAGE,
                  "program_page": "https://paradisusdei.org/that-man-is-you/", "endpoint": API.format(lat="<lat>", lng="<lng>", mi="<miles>"),
                  "user_agent": c.UA, "robots": robots, "calls": calls, "fetched_at": c.now_iso(), "count": len(out),
                  "programs": PROGRAMS,
                  "fields": "institutional only: parish name, street address, city, state, coordinates, diocese, language, program tags, active classes",
                  "note": "parishes hosting at least one active Paradisus Dei class; parish match by match.py into the 'match' field",
                  "records": out}, list_key="records")
    print(f"{len(out)} US parishes; calls {calls}")


if __name__ == "__main__":
    main()
