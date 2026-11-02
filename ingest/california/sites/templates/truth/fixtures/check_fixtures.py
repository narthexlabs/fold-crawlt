#!/usr/bin/env python3
"""Frozen schedule-parse regression cases (2026-11-01). Each folder here is a cached site (manifest.json + gzipped pages,
real crawls or Wayback captures; folders ending .invalid are reconstructions of a page from a screenshot, text verbatim).
expected.json lists rows that must be read ("rows": [kind, day, time, language]) and rows that must not ("absent"), plus
optional "schedule_none" / "notes" kinds. Offline: no network.

    python3 -I ingest/california/sites/templates/truth/fixtures/check_fixtures.py [--write]   # --write: print current rows
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))           # ingest/california/sites
import parse_site as P  # noqa: E402


def rows_of(d: Path) -> tuple[list[list], dict]:
    m = json.loads((d / "manifest.json").read_text())
    rec = P.parse_site(d, m, {"key": m["key"], "home": m["home"], "slug": "fixture", "names": m.get("names") or []}, "2026-11-01")
    rows = [[r["kind"], r["day"], r["time"], r.get("language")] for r in rec["schedule_rows"] if not r.get("needs_review")]
    return rows, rec


def main() -> int:
    bad = 0
    for d in sorted(p for p in HERE.iterdir() if (p / "manifest.json").exists()):
        rows, rec = rows_of(d)
        if "--write" in sys.argv:
            print(f"== {d.name}")
            for r in rows:
                print("  ", r)
            print("   none:", rec.get("schedule_none"), " notes:", [n["phrase"] for n in rec.get("schedule_notes") or []])
            continue
        ef = d / "expected.json"
        if not ef.exists():
            continue
        exp = json.loads(ef.read_text())
        have = {tuple(r) for r in rows}
        miss = [r for r in exp.get("rows", []) if tuple(r) not in have]
        extra = [r for r in exp.get("absent", []) if tuple(r) in have]
        none_ok = all(any(n["kind"] == k and n["day"] == dd for n in rec.get("schedule_none") or []) for k, dd in exp.get("schedule_none", []))
        over = "max_rows" in exp and len(rows) > exp["max_rows"]
        ok = not miss and not extra and none_ok and not over
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {d.name}: {len(rows)} rows" + (f"; missing {miss}" if miss else "") +
              (f"; must not read {extra}" if extra else "") + ("" if none_ok else "; schedule_none missing"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
