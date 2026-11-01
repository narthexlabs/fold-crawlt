"""Rewrite the "Measures" section of data/real/california/bulletins/README.md from the per-diocese files (no network).

    python3 -I ingest/california/bulletins/report.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "real" / "california" / "bulletins"
SLUGS = ["los-angeles", "san-francisco", "fresno", "monterey", "orange", "sacramento", "san-bernardino", "san-diego",
         "san-jose", "santa-rosa", "stockton"]
KINDS = ["mens", "womens", "moms", "young_adults", "youth", "seniors", "bible_study", "small_groups", "prayer_group",
         "charismatic", "rosary_group", "legion_of_mary", "cursillo", "kofc", "columbiettes", "catholic_daughters", "svdp",
         "grief", "divorced_separated", "alpha", "ocia_inquiry", "couples", "crhp", "fraternal_other"]


def main() -> int:
    rows, kinds, tot = [], {}, Counter()
    status = Counter()
    for s in SLUGS:
        p = OUT / f"{s}.json"
        if not p.exists():
            rows.append(f"| {s} | not built yet | | | | | | | | | |")
            continue
        c = json.loads(p.read_text())["counts"]
        L = c["links"]
        rows.append(f"| {s} | {L['attempted']} | {L['pdfs_got']} | {L['challenged']} | {L['blocked_403_429']} | "
                    f"{L['failed']} | {c['issues']} ({c['issues_ocr']} OCR) | {c['parishes_with_groups_before']} -> "
                    f"{c['parishes_with_groups_after']} of {c['parishes_total']} | {c['parishes_with_bulletin_groups']} | "
                    f"{c['groups']} | {c['informal_groups']} |")
        for k in ("attempted", "pdfs_got", "challenged", "blocked_403_429", "failed"):
            tot[k] += L[k]
        for k in ("issues", "issues_ocr", "parishes_with_groups_before", "parishes_with_groups_after", "parishes_total",
                  "parishes_with_bulletin_groups", "groups", "informal_groups", "with_meeting_time"):
            tot[k] += c[k]
        kinds[s] = c["informal_by_kind"]
        status.update(c["site_status"])
    rows.append(f"| **all** | **{tot['attempted']}** | **{tot['pdfs_got']}** | **{tot['challenged']}** | "
                f"**{tot['blocked_403_429']}** | **{tot['failed']}** | **{tot['issues']} ({tot['issues_ocr']} OCR)** | "
                f"**{tot['parishes_with_groups_before']} -> {tot['parishes_with_groups_after']} of {tot['parishes_total']}** | "
                f"**{tot['parishes_with_bulletin_groups']}** | **{tot['groups']}** | **{tot['informal_groups']}** |")
    used = [k for k in KINDS if any(kinds[s].get(k) for s in kinds)]
    krows = ["| diocese | " + " | ".join(used) + " |", "|---|" + "---:|" * len(used)]
    allk = Counter()
    for s in SLUGS:
        if s in kinds:
            krows.append(f"| {s} | " + " | ".join(str(kinds[s].get(k, "")) for k in used) + " |")
            allk.update(kinds[s])
    krows.append("| **all** | " + " | ".join(f"**{allk.get(k, 0)}**" for k in used) + " |")
    md = ["## Measures", "",
          "Links: one per parish website with a bulletin link (a site serving 2-3 parishes counts once). `failed` = "
          "LPi app page without a public code, no PDF on the page, robots.txt disallows the file host (Google Drive, "
          "edl.io, some parish hosts), not a bulletin link (sign-up/give pages), folders needing JavaScript, or not a PDF.",
          "Parishes with >= 1 group: before = the site crawl's groups (merged records' `groups_n`); after = union with "
          "the bulletin groups.", "",
          "| diocese | links attempted | PDFs got | challenged | blocked 403/429 | failed | issues read | parishes with >= 1 group (before -> after) | parishes with bulletin groups | groups | informal groups |",
          "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|", *rows, "",
          f"Groups with a meeting day/time or a next date: {tot['with_meeting_time']}.", "",
          "Site statuses: " + ", ".join(f"{k} {v}" for k, v in status.most_common()) + ".", "",
          "Informal groups by kind:", "", *krows, ""]
    rd = OUT / "README.md"
    txt = rd.read_text()
    head = txt.split("## Measures")[0].rstrip() + "\n\n"
    tail = ""
    if "## Spot check" in txt:
        tail = "\n## Spot check" + txt.split("## Spot check", 1)[1]
    rd.write_text(head + "\n".join(md) + tail)
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
