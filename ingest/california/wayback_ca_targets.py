#!/usr/bin/env python3
"""targets.json for ingest/california/wayback_ca_run.py: the California parish sites the crawl could not read.

    python3 -I ingest/california/wayback_ca_targets.py <out targets.json>

Every data/real/california/sites/<slug>/<domain>.json whose crawl_status is challenge, blocked_403, http_4xx, dns,
ssl_error, not_found or unreachable_via_proxy (Oakland is not in that tree). Phase "challenge" first, then "other";
inside a phase the dioceses with fewer sites come first (Los Angeles last), so small dioceses finish early.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITES = ROOT / "data" / "real" / "california" / "sites"
STATUSES = {"challenge", "blocked_403", "http_4xx", "dns", "ssl_error", "not_found", "unreachable_via_proxy"}


def main() -> int:
    out = Path(sys.argv[1])
    rows = []
    for f in sorted(SITES.glob("*/*.json")):
        if f.name == "summary.json" or f.parent.name == "oakland":
            continue
        d = json.loads(f.read_text("utf-8"))
        if d.get("crawl_status") not in STATUSES:
            continue
        rows.append({"slug": f.parent.name, "key": d["domain"], "home": d.get("home_url") or d.get("final_url") or f"https://{d['domain']}/",
                     "status": d["crawl_status"], "phase": "challenge" if d["crawl_status"] == "challenge" else "other",
                     "names": d.get("names", [])[:2]})
    size = Counter((r["phase"], r["slug"]) for r in rows)
    rows.sort(key=lambda r: (r["phase"] != "challenge", size[(r["phase"], r["slug"])], r["slug"], r["key"]))
    out.write_text(json.dumps({"note": "Wayback CA targets", "sites": rows}, ensure_ascii=False, indent=0) + "\n", "utf-8")
    c = Counter(r["phase"] for r in rows)
    print(len(rows), "sites", dict(c), "->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
