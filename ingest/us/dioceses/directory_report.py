#!/usr/bin/env python3
"""Fold the reader pass (reader_status.json) into directory_status.json and print the per-see README table (lane G4b).

    python3 -I ingest/us/dioceses/directory_report.py [--write-readme]

Each see in directory_status.json gets a `reader` object: result, reader used (rest+page / generic / vendor name),
cms_guess, records, with_website, and the parishes.json counts actually on disk. No network.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dirlib as DL  # noqa: E402

README = DL.OUT / "README.md"
BEGIN, END = "<!-- G4b table begin -->", "<!-- G4b table end -->"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-readme", action="store_true")
    a = ap.parse_args()
    st = json.loads(DL.STATUS.read_text())
    rd = DL.load_reader_status()["sees"]
    rows = []
    tot = Counter()
    for slug, v in sorted(st["sees"].items()):
        f = DL.existing(slug)
        r = rd.get(slug) or {}
        on_disk = {"count": f.get("count"), "with_website": f.get("with_website"), "method": f.get("method") or
                   "ingest/us/dioceses/directory_probe.py", "source_label": f.get("source_label")} if f else None
        if r or f:
            v["reader"] = {"result": r.get("result"), "reader": r.get("reader"), "cms_guess": r.get("cms_guess") or
                           (f or {}).get("cms_guess"), "checked_at": r.get("checked_at"), "parishes_json": on_disk}
        if f:
            tot["sees"] += 1
            tot["records"] += f.get("count", 0)
            tot["with_website"] += f.get("with_website", 0)
        rows.append((slug, v.get("status"), r.get("result") or "-", r.get("reader") or "-",
                     (r.get("cms_guess") or (f or {}).get("cms_guess") or "-"),
                     (f or {}).get("count", 0), (f or {}).get("with_website", 0)))
    st["reader_totals"] = dict(tot)
    st["reader_generated_at"] = DL.now_iso()
    DL.STATUS.write_text(json.dumps(st, indent=1, ensure_ascii=False) + "\n")
    lines = ["| see | probe status | reader result | reader | CMS guess | records | with website |",
             "|---|---|---|---|---|---:|---:|"]
    for row in rows:
        if row[1] in ("challenge", "dead", "blocked", "egress_closed") and row[5] == 0:
            continue
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    table = "\n".join(lines)
    print(table)
    print(dict(tot))
    if a.write_readme and README.exists():
        t = README.read_text()
        if BEGIN in t:
            t = t.split(BEGIN)[0] + BEGIN + "\n" + table + "\n" + END + t.split(END)[1]
            README.write_text(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
