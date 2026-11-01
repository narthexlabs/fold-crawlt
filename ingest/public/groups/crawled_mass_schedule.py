"""Crawled Mass / confession / adoration times for parishes with no schedule rows in the database yet.

(Lives beside the groups builder because it was added in the same pass; it only touches mass_schedule.)

    python3 -I ingest/public/groups/crawled_mass_schedule.py --have <comma-separated entity ids already in mass_schedule>
        -> data/real/groups/crawled_mass_schedule.json  and  /tmp/crawled_mass_schedule.sql

Source: data/real/parish_sites.json (home re-crawl 2026-10-29), field mass_schedule.parsed. Rows are labelled
source='crawled-2026-10-29' and are shown publicly as "from the parish website, unverified".

Rules (conservative; a dropped row just means "see the parish website"):
  * kind comes from the line itself, else the nearest heading above it in the page text (Mass / Misa,
    Confession / Reconciliation, Adoration / Exposition / Holy Hour). Office hours, open-church hours, holy days,
    holidays, anointing, rosary/novena lines and lines with no recognisable heading are dropped.
  * a time range ("3:30 pm - 4:30 pm", "... until 12 noon") keeps only the start time; lines that only refer to a
    Mass ("after the 9 AM Mass") are not Mass times.
  * day names normalised (Spanish and abbreviations; "Vigil" -> Saturday); language normalised to English/Spanish/Latin.
  * raw line passes privacy.scrub_schedule_line (Mass intentions withheld).
INSERT only, for parishes that have no rows at all; the SQL never deletes or updates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "public"))
from privacy import scrub_schedule_line  # noqa: E402

SOURCE = "crawled-2026-10-29"
DAY = {"sun": "Sunday", "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
       "sat": "Saturday", "domingo": "Sunday", "lunes": "Monday", "martes": "Tuesday", "miércoles": "Wednesday",
       "miercoles": "Wednesday", "jueves": "Thursday", "viernes": "Friday", "sábado": "Saturday", "sabado": "Saturday",
       "vigil": "Saturday", "daily": "Daily"}
KW = [("confession", re.compile(r"confess|reconcil|penance|confesi", re.I)),
      ("adoration", re.compile(r"adorat|adoraci|exposition|holy hour|hora santa", re.I)),
      ("mass", re.compile(r"\bmass(es)?\b|\bmisa|eucharist\b|vigil|liturgy", re.I))]
DROP = re.compile(r"office|private prayer|doors are open|holiday|holy day|anointing|rosary|novena|perseveran|"
                  r"morning prayer|for 15 minutes|live-?stream only", re.I)
RANGE = re.compile(r"(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\s*(?:-|–|to|until)\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|noon))", re.I)


def norm_day(d):
    if not d:
        return None
    return DAY.get(d.strip().lower(), d if d in DAY.values() else DAY.get(d.strip().lower()[:3]))


def hhmm(s):
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|noon)?", s.strip(), re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if ap.startswith("p") and h < 12:
        h += 12
    if ap.startswith("a") and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


REF = re.compile(r"\b(after|following|prior to|before|concluding|until)\b", re.I)


def kind_for(line, raw_lines, parsed_kind):
    for k, rx in KW:
        if k == "mass" and REF.search(line):
            continue  # "after the 9 AM Mass": a reference to a Mass, not a Mass time
        if rx.search(line):
            return k
    try:
        idx = next(i for i, ln in enumerate(raw_lines) if line.strip() and line.strip()[:40] in ln)
    except StopIteration:
        return None
    for j in range(idx - 1, max(-1, idx - 8), -1):
        ln = raw_lines[j]
        if DROP.search(ln):
            return None
        for k, rx in KW:
            if rx.search(ln):
                return k
    return None


def lang_for(p, line):
    l = (p.get("language") or "").lower()
    if l in ("spanish", "español", "espanol") or re.search(r"\(esp\)|español|spanish", line, re.I):
        return "Spanish"
    if l == "latin" or re.search(r"\blatin\b", line, re.I):
        return "Latin"
    if l in ("english", "ingles", "inglés"):
        return "English"
    return None


def build(have: set[str]):
    sites = json.loads((ROOT / "data/real/parish_sites.json").read_text())["records"]
    out = []
    for r in sites:
        ms = r.get("mass_schedule") or {}
        if r["id"] in have or not ms.get("parsed"):
            continue
        out += rows_from_record(r, SOURCE)
    return out


def rows_from_record(r: dict, source: str) -> list[dict]:
    """The filtered rows for one parish_sites-shaped record ({id, website, mass_schedule: {raw, parsed, source_url}});
    also used by ingest/public/recovery.py for pages read through a side door."""
    out = []
    ms = r.get("mass_schedule") or {}
    if ms.get("parsed"):
        raw_lines = [ln.strip() for ln in (ms.get("raw") or "").split("\n") if ln.strip()]
        seen = set()
        for p in ms["parsed"]:
            line = p.get("line") or ""
            if re.search(r"\bfollowing\b|\bafter (the )?\d", line, re.I):
                continue  # start time is relative to a Mass, not stated
            if DROP.search(line) or not p.get("time") or re.fullmatch(r"\s*[\d:]+\s*(?:a\.?m\.?|p\.?m\.?)?\s*[-–]\s*", line, re.I) \
                    or (re.search(r"\bmass", line, re.I) and REF.search(line) and not re.search(
                        r"confess|reconcil|adorat|exposition", line, re.I)):
                continue
            rg = RANGE.search(line)
            if rg and hhmm(rg.group(2)) == p["time"] and hhmm(rg.group(1)) != p["time"]:
                continue  # end of a range
            kind = kind_for(line, raw_lines, p.get("kind"))
            if kind is None:
                continue
            day = norm_day(p.get("day"))
            key = (kind, day, p["time"], lang_for(p, line))
            if key in seen:
                continue
            seen.add(key)
            clean, _ = scrub_schedule_line(line)
            out.append({"entity_id": r["id"], "kind": kind, "language": key[3], "day": day, "time": p["time"],
                        "raw": clean[:200], "source": source, "source_url": ms.get("source_url") or r.get("website")})
    return out


def sql(rows):
    def q(v):
        return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
    vals = ",\n".join("(" + ",".join(q(x[c]) for c in ("entity_id", "day", "time", "language", "kind", "raw",
                                                        "source", "source_url")) + ")" for x in rows)
    return f"""with ins as (
insert into mass_schedule (entity_id, day, time, language, kind, raw, source, source_url)
select v.*
from (values
{vals}) as v(entity_id, day, time, language, kind, raw, source, source_url)
where not exists (select 1 from mass_schedule m where m.entity_id = v.entity_id)
returning entity_id, kind)
select count(*) rows_inserted, count(distinct entity_id) parishes, count(*) filter (where kind = 'mass') mass,
       count(*) filter (where kind = 'confession') confession, count(*) filter (where kind = 'adoration') adoration
from ins;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--have", default="", help="entity ids that already have mass_schedule rows")
    a = ap.parse_args()
    rows = build({x for x in a.have.split(",") if x})
    (ROOT / "data/real/groups/crawled_mass_schedule.json").write_text(
        json.dumps({"source": SOURCE, "note": "unverified; from parish websites", "count": len(rows),
                    "parishes": sorted({x["entity_id"] for x in rows}), "rows": rows}, ensure_ascii=False, indent=1))
    Path("/tmp/crawled_mass_schedule.sql").write_text(sql(rows))
    from collections import Counter
    print(len(rows), "rows,", len({x["entity_id"] for x in rows}), "parishes", Counter(x["kind"] for x in rows))


if __name__ == "__main__":
    main()
