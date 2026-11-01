"""Record facts about a parish taken from an alternate public source (for sites the crawler could not read).

Used during the enrichment session: blocked parish sites were read with the WebFetch tool (after a
domain-restricted web search surfaced the page), and the facts were entered here as structured JSON.
WebFetch returns a model-written summary of the page, not raw HTML, so every entry is tagged
method="webfetch_summary" and should be treated as lower-confidence than a parsed PDF or HTML page.

Usage:
  python ingest/public/enrichment/alt_sources_add.py < entry.json
    entry.json = {"id": "<parish id>", "source_url": "...", "method": "webfetch_summary", <fields>}
Fields (all optional): mass_schedule [{day,time,language,kind,note}], confession, adoration, office_hours,
  staff [{name,title}], ministries [str], volunteer_needs [str], giving_links [str], bulletin {publisher,
  index_url, pdf_urls}, flocknote_url, notes [str], events [{date,title}].
Writes data/real/enrichment/parish_alt_sources.json (entries keyed by parish id, newest last).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import REAL, load_json, now_iso, save_json  # noqa: E402

OUT = REAL / "enrichment" / "parish_alt_sources.json"


def add(entry: dict) -> None:
    data = load_json(OUT, {"description": "Facts from alternate public sources for parishes whose sites blocked "
                                          "the crawler. method=webfetch_summary means a model-summarized page.",
                           "records": {}})
    pid = entry.pop("id")
    entry.setdefault("fetched_at", now_iso())
    entry.setdefault("method", "webfetch_summary")
    lst = data["records"].setdefault(pid, [])
    lst = [e for e in lst if e.get("source_url") != entry.get("source_url")]
    lst.append(entry)
    data["records"][pid] = lst
    data["updated_at"] = now_iso()
    save_json(OUT, data)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    for e in payload if isinstance(payload, list) else [payload]:
        add(e)
    print("ok")
