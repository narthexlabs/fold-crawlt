#!/usr/bin/env python3
"""List the sites a sandbox pass could not read, for the home-connection (laptop) pass.

    python3 -I ingest/california/sites/laptop_list.py <slug> [--out FILE]

Reads data/raw/california/sites/_inputs/<slug>.json and the sandbox manifests; writes the fetch list (key + home) of every
site whose home page was refused or unreachable from the sandbox. A site that answered ok, or is definitely gone
(404/410), a parked domain, or one whose robots.txt disallows us is not retried.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "california" / "sites"
NO_RETRY = {"ok", "not_found", "robots_disallowed", "parked", "social_only"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--out")
    a = ap.parse_args()
    sites = json.loads((RAW / "_inputs" / f"{a.slug}.json").read_text("utf-8"))["sites"]
    out = []
    tried = json.loads((RAW / "_laptop" / f"{a.slug}.json").read_text()) if (RAW / "_laptop" / f"{a.slug}.json").exists() else {}
    for s in sites:
        if s["key"] in tried:
            continue                      # already asked once from the home connection
        if s.get("social_only"):
            continue
        mp = RAW / s["key"].replace("/", "__") / "manifest.json"
        if not mp.exists():
            out.append({"key": s["key"], "home": s["home"], "why": "not fetched"})
            continue
        m = json.loads(mp.read_text("utf-8"))
        if m.get("status") in NO_RETRY or m.get("env") == "laptop":
            continue
        out.append({"key": s["key"], "home": s["home"], "why": m.get("status")})
    p = Path(a.out) if a.out else RAW / "_inputs" / f"{a.slug}.laptop.json"
    p.write_text(json.dumps(out, indent=0) + "\n", "utf-8")
    from collections import Counter
    print(len(out), "sites for the laptop:", dict(Counter(x["why"] for x in out)), "->", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
