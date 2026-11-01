"""Attach movement records to our parishes and write data/real/us/movements/by_parish.json.

    python3 -I ingest/us/movements/match.py
Reads kofc.json (and any future <source>.json with parish-level records), adds a `match` field to every record
({parish_id, parish_name, parish_city, state, confidence exact|fuzzy|unmatched, method}) and writes by_parish.json:
parish id -> groups [{name, category, subcategory, source, label, meeting_time, url, source_url, fetched_at, confidence}].
Only exact and fuzzy matches are attached; unmatched records stay in the source file for a human or a later pass.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

LABEL = {"kofc": "From the Knights of Columbus's directory"}


def title(s: str) -> str:
    import re
    s = re.sub(r"\b(knights of columbus|k\s*of\s*c|kofc)\b", " ", s or "", flags=re.I).strip(" -,")
    small = {"of", "the", "and", "de", "la", "del", "y"}
    w = (s or "").lower().split()
    return " ".join(x if (i and x in small) else (x[:1].upper() + x[1:]) for i, x in enumerate(w))


def main() -> None:
    parishes = c.load_parish_index()
    ix = c.by_state_city(parishes)
    by_id = {p["id"]: p for p in parishes}
    by_parish: dict = {}
    stats = {}
    kf = c.OUT / "kofc.json"
    d = c.load(kf)
    if d:
        cnt = Counter()
        for r in d["records"]:
            p, conf, how = c.match(r.get("councilName"), r.get("city"), r.get("state"), ix)
            r["match"] = {"parish_id": p["id"] if p else None, "parish_name": p["name"] if p else None,
                          "parish_city": p.get("city") if p else None, "state": r.get("state"),
                          "confidence": conf, "method": how}
            cnt[conf] += 1
            if p:
                web = r.get("webSite")
                if web and not web.lower().startswith("http"):
                    web = "https://" + web
                by_parish.setdefault(p["id"], []).append({
                    "name": f"Knights of Columbus Council {r['councilNumber']} ({title(r['councilName'])})",
                    "category": "fraternal_orders", "subcategory": "knights_of_columbus", "source": "kofc",
                    "label": LABEL["kofc"], "meeting_time": None, "url": web,
                    "source_url": "https://www.kofc.org/get-involved/find-a-council/", "api_url": r["source_url"],
                    "fetched_at": r["fetched_at"], "confidence": conf})
        c.save(kf, d, list_key="records")
        stats["kofc"] = dict(cnt)
    first = [pid for pid in by_parish if not by_id.get(pid, {}).get("has_groups")]
    c.save(c.OUT / "by_parish.json", {
        "built_at": c.now_iso(), "built_by": "ingest/us/movements/match.py",
        "note": "parish id (Find build page id) -> groups read from national movement directories; exact/fuzzy matches only",
        "stats": {"by_source": stats, "parishes_with_groups": len(by_parish), "parishes_gaining_first_group": len(first)},
        "parishes": dict(sorted(by_parish.items()))})
    print(stats, len(by_parish), "parishes;", len(first), "gain their first group")


if __name__ == "__main__":
    main()
