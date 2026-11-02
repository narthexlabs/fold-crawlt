#!/usr/bin/env python3
"""Build targets.json for the laptop Wayback pass over US parish sites (see data/real/us/wayback/README.md, "Laptop run").

    python3 -I ingest/us/wayback/laptop_targets.py [--ledger ../fold-app/data/real/coverage/ledger.json] OUT.json

Targets: data/real/us/sites/<diocese>/<domain>.json with crawl_status challenge / blocked_403 / blocked_429 that
  * carry no wayback-* status yet (a written Wayback record has crawl_status "wayback-<date>"),
  * are not in any runner/laptop state file (data/real/us/wayback/sites_state*.json),
  * were not reopened by the laptop live pass (crawl_env "laptop").
Order: (1) non-California challenge/blocked sites in the sees of the coverage ledger's `blocked` table, sees ranked by
parishes known minus verified (most known, fewest verified first); (2) California ca-* folders; (3) everything else.
Each target carries what the laptop runner needs to reproduce sites.py exactly: the site id (<diocese>/<stem>), the key
(domain or domain/path) and the home URL computed the same way sites.one() does. The page plan itself is fetch_site.Site's
(home, then the mass-times / ministries / bulletin / calendar / contact links it picks; eCatholic guesses below), answered
from the archive by sites.WaybackSite, so `guess_paths` is informational.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SITES = ROOT / "data" / "real" / "us" / "sites"
OUTW = ROOT / "data" / "real" / "us" / "wayback"
FIRST = ("challenge", "blocked_403", "blocked_429")
GUESS = ["/mass-times", "/mass-schedule", "/ministries", "/bulletins", "/bulletin"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--ledger", default=str(ROOT.parent / "fold-app" / "data" / "real" / "coverage" / "ledger.json"))
    a = ap.parse_args()
    done: set[str] = set()
    for sf in OUTW.glob("sites_state*.json"):
        done |= set(json.loads(sf.read_text()))
    rank: dict[str, int] = {}
    try:
        led = json.loads(Path(a.ledger).read_text())
        rows = led["blocked"]["dioceses"]
        rows = sorted(rows, key=lambda r: -((r.get("parishes_known") or 0) - (r.get("status") or {}).get("verified", 0)))
        rank = {r["slug"]: i for i, r in enumerate(rows)}
    except (OSError, ValueError, KeyError) as e:
        print("ledger not read:", e, file=sys.stderr)
    out = []
    skipped = {"state": 0, "laptop": 0}
    for f in sorted(SITES.glob("*/*.json")):
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("crawl_status") not in FIRST:
            continue
        sid = f"{f.parent.name}/{f.stem}"
        if sid in done:
            skipped["state"] += 1
            continue
        if d.get("crawl_env") == "laptop":
            skipped["laptop"] += 1
            continue
        key = d.get("domain") or f.stem.replace("__", "/")
        home = d.get("home_url") or ("https://" + key.split("/")[0] + "/")
        dio = f.parent.name
        if dio.startswith("ca-"):
            phase, order = 2, 0
        elif dio in rank:
            phase, order = 1, rank[dio]
        else:
            phase, order = 3, 0
        out.append({"sid": sid, "diocese": dio, "key": key, "home_url": home, "prior_status": d["crawl_status"],
                    "phase": phase, "_o": (phase, order, 0 if d["crawl_status"] == "challenge" else 1, sid),
                    "guess_paths": GUESS})
    out.sort(key=lambda t: t.pop("_o"))
    meta = {"built_from": "data/real/us/sites/**/*.json (fold-crawlt)", "n": len(out),
            "phases": {p: sum(1 for t in out if t["phase"] == p) for p in (1, 2, 3)}, "skipped": skipped,
            "plan": "CDX (newest 200/html capture per URL since 2023), then fetch_site.Site's page plan answered from "
                    "the archive as raw id_ captures (sites.WaybackSite)"}
    Path(a.out).write_text(json.dumps({"meta": meta, "targets": out}, indent=0, ensure_ascii=False))
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
