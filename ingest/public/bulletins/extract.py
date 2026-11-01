"""Extract groups, schedules and events from the bulletins fetch.py downloaded, with the existing parser.

    python3 -I ingest/public/bulletins/extract.py [--ids a,b]

Reuses ingest/public/enrichment/parse_bulletins.parse_one: the same rule-based extractor that parsed the 134 earlier
bulletins (pdftotext pages + pdfplumber word positions; ministries, schedules, events, volunteer needs, offertory,
Appeal figures). It is rule-based: no LLM is called (none was used for the earlier bulletins either), so
ANTHROPIC_API_KEY is not needed. Pages with almost no text layer are OCR'd with tesseract first
(enrichment/fetch_bulletins.ocr_pdf), as before.

Privacy: parse_one already runs privacy.screen_bulletin_record on every record (prayer lists, the sick, the deceased,
Mass intentions, banns, donors are skipped; private names withheld; contacts cut). This script screens the record a
second time and drops the per-bulletin staff_withheld list, so nothing but public roles is written.

Reads data/real/bulletins/index.json. Writes data/real/bulletins/extracted.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "enrichment"))
from common import ROOT, now_iso  # noqa: E402
from parse_bulletins import parse_one  # noqa: E402
from fetch_bulletins import ocr_pdf  # noqa: E402
from privacy import screen_bulletin_record  # noqa: E402

OUT = ROOT / "data/real/bulletins"


def ensure_text(rec: dict) -> dict:
    """OCR near-empty pages; returns the entry parse_one expects."""
    tp = ROOT / rec["text_path"]
    text = tp.read_text(encoding="utf-8", errors="ignore")
    pages = text.split("\f")
    thin = [i for i, p in enumerate(pages, 1) if len(p.strip()) < 80][:12]
    ocr = False
    if thin and len(thin) >= max(1, len(pages) // 2):
        o = tp.with_suffix(".ocr.txt")
        if not o.exists():
            got = ocr_pdf(ROOT / rec["pdf_path"], thin, tp.parent / "_ocr")
            for i, t in got.items():
                if i - 1 < len(pages):
                    pages[i - 1] = t
            o.write_text("\f".join(pages))
        tp, ocr = o, True
    return {"text_path": str(tp.relative_to(ROOT)), "pdf_path": rec["pdf_path"], "date": rec["date"],
            "url": rec["url"], "found_on": rec.get("found_on"), "fetched_at": rec["fetched_at"], "ocr": ocr}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    ids = set(filter(None, a.ids.split(",")))
    idx = json.loads((OUT / "index.json").read_text())["records"]
    out_p = OUT / "extracted.json"
    prev = json.loads(out_p.read_text())["records"] if out_p.exists() and ids else []
    recs = [r for r in prev if r["entity_id"] not in ids]
    for r in idx:
        if ids and r["entity_id"] not in ids:
            continue
        if not r.get("text_path"):
            continue
        try:
            e = ensure_text(r)
            b = parse_one(e, r["entity_id"])
        except Exception as ex:  # noqa: BLE001
            recs.append({"entity_id": r["entity_id"], "host": r["host"], "date": r["date"], "source_url": r["url"],
                         "error": f"{type(ex).__name__}: {str(ex)[:120]}"})
            continue
        b.pop("staff_withheld", None)
        screen_bulletin_record(b)
        b.update(entity_id=r["entity_id"], host=r["host"], source_label=f"bulletin-{r['host']}-{r['date']}")
        recs.append(b)
        print(f"{r['entity_id']:46s} {r['host']:12s} {r['date']} pages={b['pages']} ocr={b['ocr']} "
              f"ministries={len(b['ministries'])} mass={len(b['schedules'].get('mass', []))} events={len(b['events'])}",
              flush=True)
    recs.sort(key=lambda x: (x["entity_id"], x.get("date") or ""), reverse=False)
    out_p.write_text(json.dumps({
        "generated_at": now_iso(), "generated_by": "ingest/public/bulletins/extract.py",
        "method": "rule-based (enrichment/parse_bulletins.parse_one); no LLM; privacy-screened twice",
        "count": len(recs), "records": recs}, ensure_ascii=False, indent=1) + "\n")
    print(len(recs), "bulletins ->", out_p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
