"""Merge parish-site crawl outputs into data/real/parish_sites.json (one record per directory parish).

Inputs: data/real/parish_sites/full.json (current parser, all parishes) plus any shard_*.json /
extra crawl files given on the command line. Per parish the richest successful record wins, preferring
records produced by the current parser (full.json, shard_1_of_4.json, and any extra files passed with
--new); older-parser shard records are used only when no current-parser crawl succeeded.

Adds crawl_status: ok | blocked_403 | rate_limited_429 | ssl_error | proxy_error | connection_error | js_challenge |
no_website | other_error, plus crawl_source (file the record came from) and crawl_parser (current|previous).

Usage:
  python ingest/public/merge_parish_sites.py
  python ingest/public/merge_parish_sites.py --new data/real/parish_sites/residential.json
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REAL, load_json, now_iso, save_json  # noqa: E402

DIR = REAL / "parish_sites"
OUT = REAL / "parish_sites.json"
DIRECTORY = REAL / "parishes_directory.json"
CURRENT = ["full.json", "shard_1_of_4.json"]          # produced by the current parser
PREVIOUS = ["shard_2_of_4.json", "shard_3_of_4.json", "shard_4_of_4.json"]
CHALLENGE_RE = re.compile(r"challenge-platform|cf-chl|Just a moment\.\.\.|Checking your browser|"
                          r"enable JavaScript and cookies to continue|sgcaptcha|captcha-delivery|sucuri_cloudproxy|"
                          r"window\.location\.href\s*=\s*[\"']/lander", re.I)  # /lander = JS-only redirect (parked-domain pattern)


def is_challenge(rec: dict) -> bool:
    """Home page fetched with 200 but it is a bot-check interstitial, not the site."""
    pages = rec.get("pages") or []
    if not pages:
        return False
    if "home_challenge" in rec:  # set when the raw HTML stayed on another machine (residential re-crawl)
        return bool(rec["home_challenge"])
    try:
        html = Path(pages[0]["raw_file"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(CHALLENGE_RE.search(html[:20000])) and len(html) < 40000


def status(rec: dict | None) -> str:
    if rec is None or not rec.get("website"):
        return "no_website"
    if rec.get("pages"):
        return "js_challenge" if is_challenge(rec) else "ok"
    errs = [e for e in rec.get("errors", []) if isinstance(e, dict)]
    home = errs[0] if errs else {}
    if home.get("status") == 403:
        return "blocked_403"
    if home.get("status") == 429:
        return "rate_limited_429"
    if home.get("error") == "SSLError":
        return "ssl_error"
    if home.get("error") == "ProxyError":
        return "proxy_error"
    if home.get("error") == "ConnectionError":
        return "connection_error"  # e.g. the domain no longer resolves
    return "other_error"


def richness(rec: dict) -> int:
    return (len(rec.get("pages", [])) + 3 * len((rec.get("mass_schedule") or {}).get("parsed", []))
            + 3 * len((rec.get("staff") or {}).get("items", [])) + len((rec.get("ministries") or {}).get("items", []))
            + 5 * bool(rec.get("bulletin")) + 5 * bool(rec.get("giving_provider")) + len(rec.get("tech_signals") or {}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", nargs="*", default=[], help="extra current-parser crawl files (e.g. residential re-crawl)")
    a = ap.parse_args()
    cands: dict[str, list[tuple[str, str, dict]]] = {}
    for files, parser in ((CURRENT, "current"), (PREVIOUS, "previous"), (a.new, "current")):
        for f in files:
            p = Path(f) if "/" in f else DIR / f
            for r in (load_json(p, {}) or {}).get("records", []):
                cands.setdefault(r["id"], []).append((parser, p.name, r))
    out = []
    for d in load_json(DIRECTORY, {}).get("records", []):
        pid = d["id"]
        cs = cands.get(pid, [])
        ok = [(pa, src, r) for pa, src, r in cs if status(r) == "ok"]
        pick = None
        for want in ("current", "previous"):
            pool = [c for c in ok if c[0] == want]
            if pool:
                pick = max(pool, key=lambda c: richness(c[2]))
                break
        if pick is None and cs:
            # no success anywhere: report the newest attempt (a --new re-crawl, else the full run)
            newest = [c for c in cs if c[1] in {Path(f).name for f in a.new}]
            pick = newest[-1] if newest else next((c for c in cs if c[1] == "full.json"), cs[0])
        if pick is None:
            rec = {"id": pid, "name": d.get("name"), "website": d.get("website"), "pages": [], "errors": []}
            pa, src = None, None
        else:
            pa, src, rec = pick
            rec = dict(rec)
        rec["crawl_status"] = status(rec) if d.get("website") else "no_website"
        rec["crawl_source"] = src
        rec["crawl_parser"] = pa
        rec["attempt_statuses"] = {s: status(r) for _, s, r in cs}
        out.append(rec)
    save_json(OUT, {"generated_at": now_iso(), "count": len(out), "records": out})
    from collections import Counter
    print(Counter(r["crawl_status"] for r in out), "->", OUT)


if __name__ == "__main__":
    main()
