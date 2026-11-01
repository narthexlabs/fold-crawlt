"""Copy the parish universe (ids the Find build uses) into data/real/us/movements/parish_index.json.

    python3 -I ingest/us/movements/build_parish_index.py --app /home/claude/fold-app
Reads fold-app public/us/<see>/p/*.json and public/ca/<diocese>/p/*.json: id, name, city, state, zip, see, and whether
the page already lists groups (has_groups) so the report can count parishes that gain their first group.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

ST_ZIP = re.compile(r",\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=str(c.ROOT.parent / "fold-app"))
    a = ap.parse_args()
    app = Path(a.app)
    see_state = {}
    for s in (c.load(c.ROOT / "data/real/us/dioceses.json", {}) or {}).get("dioceses") or []:
        short = re.sub(r"^(?:arch)?(?:diocese|eparchy|archeparchy)-of-", "", s["slug"])
        see_state[short] = s.get("see_state")
    out = []
    for area, default_state in (("us", None), ("ca", "CA")):
        for f in sorted((app / "public" / area).glob("*/p/*.json")):
            d = json.loads(f.read_text())
            see = f.parent.parent.name
            addr = d.get("address") or ""
            m = ST_ZIP.search(addr)
            state = m.group(1) if m else (default_state or see_state.get(see))
            z = m.group(2) if m else ((ZIP.findall(addr) or [None])[-1])
            out.append({"id": d["id"], "name": d.get("name"), "city": d.get("city"), "state": state, "zip": z,
                        "see": see, "area": area, "kind": d.get("kind"), "has_groups": bool(d.get("groups"))})
    c.save(c.OUT / "parish_index.json", {"source": f"fold-app public/us + public/ca page files ({app})",
                                          "built_at": c.now_iso(), "count": len(out),
                                          "with_zip": sum(1 for p in out if p["zip"]),
                                          "note": "ids are the Find build's page ids; has_groups = page already lists groups",
                                          "parishes": out}, list_key="parishes")
    print(len(out), "parishes;", sum(1 for p in out if p["zip"]), "with zip;", len({p['zip'] for p in out if p['zip']}), "zips")


if __name__ == "__main__":
    main()
