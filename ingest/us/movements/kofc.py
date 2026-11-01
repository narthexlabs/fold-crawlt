"""Knights of Columbus "Find a Council" -> councils near each parish ZIP (lane D, source 1).

The public page https://www.kofc.org/get-involved/find-a-council/ calls a JSON endpoint (read from its main.js):
    GET https://www.kofc.org/api/FindACouncilSearch/councils/countryZip/USA/<zip>/
which returns the ~5 nearest councils (type R), nearby 4th-degree assemblies (type A) and the state council. robots.txt
disallows only /ui/, /utils/ and /search/?query= (checked at run time). One request at a time, >= 1.05 s apart.

We query the ZIP codes of our own parishes (parishes whose page lists no groups first), keep councils only (type R),
and store institutional fields only: council number and name, city, state, ZIP, language, the council's public
website. The endpoint's contact/email/phone fields (empty in every reply seen) are never stored.

    python3 -I ingest/us/movements/kofc.py --minutes 60          # resumable: kofc_progress.json holds done ZIPs
    python3 -I ingest/us/movements/match.py                      # councils -> parishes -> by_parish.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import common_fetch as cf  # noqa: E402

API = "https://www.kofc.org/api/FindACouncilSearch/councils/countryZip/USA/{z}/"
PAGE = "https://www.kofc.org/get-involved/find-a-council/"
OUTF = c.OUT / "kofc.json"
PROG = c.OUT / "kofc_progress.json"
KEEP = ("councilNumber", "councilName", "type", "language", "location", "city", "state", "zipCode", "webSite")


def zip_order(parishes: list[dict]) -> list[str]:
    first, rest = [], []
    for p in parishes:
        z = p.get("zip")
        if not z:
            continue
        (rest if p.get("has_groups") else first).append(z)
    seen, out = set(), []
    for z in first + rest:
        if z not in seen:
            seen.add(z)
            out.append(z)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    stop = time.time() + a.minutes * 60
    parishes = c.load_parish_index()
    zips = zip_order(parishes)
    prog = c.load(PROG, {}) or {}
    done = prog.get("done", {})
    data = c.load(OUTF, {}) or {}
    councils = {r["councilNumber"]: r for r in data.get("records", [])}
    pol = cf.Polite()
    rb = pol.robots_for(API.format(z="94612"))
    robots = {"url": rb["url"], "status": rb["status"], "class": rb["class"], "crawl_delay": rb.get("crawl_delay"),
              "api_allowed": pol.allowed(API.format(z="94612"))}
    if not robots["api_allowed"]:
        print("robots.txt disallows the API; stopping"); return
    n = errs = 0
    todo = [z for z in zips if z not in done]
    print(f"{len(zips)} zips, {len(todo)} to do, {len(councils)} councils so far", flush=True)

    def flush():
        recs = sorted(councils.values(), key=lambda r: (r.get("state") or "", r.get("city") or "", int(r["councilNumber"]) if r["councilNumber"].isdigit() else 0))
        c.save(OUTF, {"source": "Knights of Columbus - Find a Council", "source_url": PAGE,
                      "endpoint": API.format(z="<zip>"), "user_agent": c.UA, "robots": robots,
                      "fetched_at": c.now_iso(), "count": len(recs),
                      "fields": "institutional only: council number/name, city, state, ZIP, language, public website",
                      "note": "councils (type R) near the ZIP codes of our parishes; parish match is done by match.py into the 'match' field",
                      "records": recs}, list_key="records")
        c.save(PROG, {"zips_total": len(zips), "zips_done": len(done), "updated_at": c.now_iso(), "done": done})

    for z in todo:
        if time.time() > stop or (a.limit and n >= a.limit):
            break
        url = API.format(z=z)
        r = pol.get(url, accept="application/json", timeout=30, max_bytes=500000)
        n += 1
        if r["status"] != 200:
            done[z] = r.get("class") or r.get("err") or f"http_{r['status']}"
            errs += 1
            if r.get("class") in ("challenge", "blocked") or errs > 30 and errs > n / 2:
                print("stopping:", z, r.get("class"), r.get("status"), flush=True)
                break
            continue
        try:
            rows = json.loads(r["body"].decode("utf-8", "replace"))
        except Exception:
            done[z] = "bad_json"
            continue
        k = 0
        for x in rows:
            if (x.get("type") or "") != "R" or not x.get("councilNumber"):
                continue
            rec = {f: (x.get(f) or None) for f in KEEP}
            rec["source_url"] = url
            rec["fetched_at"] = c.now_iso()
            prev = councils.get(rec["councilNumber"])
            if prev:
                rec["source_url"], rec["fetched_at"] = prev["source_url"], prev["fetched_at"]
                if prev.get("match"):
                    rec["match"] = prev["match"]
            councils[rec["councilNumber"]] = rec
            k += 1
        done[z] = k
        if n % 50 == 0:
            flush()
            print(f"{n} zips this run, {len(done)}/{len(zips)} done, {len(councils)} councils", flush=True)
    flush()
    print(f"run end: {n} requests, {len(done)}/{len(zips)} zips done, {len(councils)} councils, errors {errs}", flush=True)


if __name__ == "__main__":
    main()
