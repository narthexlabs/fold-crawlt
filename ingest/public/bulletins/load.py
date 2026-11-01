"""Turn bulletin ministries (extracted.json) into new `ministry` rows, INSERT only, skipping groups we already hold.

    python3 -I ingest/public/bulletins/load.py [--out /tmp/bulletin_groups.sql] [--existing existing.json]

Same row shape as ingest/public/groups/load_sql.py (group_id "<entity_id>:<group key>", fixed taxonomy from
build_groups.classify, privacy screen build_groups.private_name_risk). For each parish the newest bulletin per host
is used, plus the one before it (a group printed in either counts).

Source label: source_kind stays 'bulletin' (the app's sourceText() switches on it) and the row carries the flag
"source:bulletin-<host>-<YYYY-MM-DD>" (host = lpi | discovermass); source_url is the PDF.

Duplicates: a group is skipped when the parish already has a ministry row with the same group_id (ignoring the
language suffix, so 'choir' and 'choir_es' count as one), or with the same
normalised name (lower case, letters only). The check runs inside the SQL (WHERE NOT EXISTS), so the file is safe to
re-run; with --existing (a JSON list of {entity_id, name, group_id} exported from the DB) the same check also runs
here so the counts are known before loading. No UPDATE, no DELETE.

Writes data/real/bulletins/new_groups.json (the candidate rows) and the SQL file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "groups"))
sys.path.insert(0, str(HERE.parent))
import build_groups as bg  # noqa: E402
from load_sql import COLS, lit  # noqa: E402

ROOT = HERE.parents[2]
REAL = ROOT / "data/real"


LANG_SUFFIX = re.compile(r"_(en\+es|es|vi|zh|ko|tl|pt|to|ig)$")


def stem(gid: str) -> str:
    """Group id without its language suffix; all Knights of Columbus at one parish are one council record."""
    g = LANG_SUFFIX.sub("", gid or "")
    return re.sub(r":kofc.*$", ":kofc", g)


def nkey(s: str) -> str:
    return re.sub(r"[^a-z]", "", bg.fold(s or ""))


KEEP_SHORT = {"of", "st", "cy", "yo", "dr"}


def clean_ocr(raw: str) -> str:
    """OCR'd column text cuts names mid-word ("Legion of Mary t", "Cursillo R"): drop trailing junk tokens."""
    toks = re.sub(r"[^\w'’&/ -]", " ", raw).split()
    while toks and ((len(toks[-1]) <= 2 and toks[-1].lower() not in KEEP_SHORT and not toks[-1].isupper())
                    or toks[-1].lower() in ("de", "of", "the", "and", "y", "del", "la", "pa")
                    or (len(toks[-1]) <= 2 and len(toks) > 1)):
        toks.pop()
    return " ".join(toks).strip(" /-")


def display(raw: str, res, ocr: bool = False) -> str:
    key, cat, sub, aud, lang, disp, strength = res
    nm = bg.smart_title(raw).strip(" .,:;/")
    if disp and ocr:
        return disp
    if disp and (strength == "generic" or len(nm.split()) > 6 or re.search(rf"\b({bg.DAYS})s?\b", nm, re.I)):
        nm = disp
    if sub == "knights_of_columbus":
        m = re.search(r"council (\d{3,5})", raw, re.I)
        nm = f"Knights of Columbus Council {m.group(1)}" if m else "Knights of Columbus"
    return nm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/bulletin_groups.sql")
    ap.add_argument("--existing", default="")
    ap.add_argument("--ids", default="", help="only these parishes (comma-separated)")
    ap.add_argument("--json-out", default=str(REAL / "bulletins/new_groups.json"))
    a = ap.parse_args()
    only = set(filter(None, a.ids.split(",")))
    ents = {e["id"]: e for e in json.loads((ROOT / "data/seed/entities.json").read_text())["entities"]}
    ext = json.loads((REAL / "bulletins/extracted.json").read_text())["records"]
    by_pid = defaultdict(list)
    for b in ext:
        if b.get("ministries") is not None and not b.get("error") and (not only or b["entity_id"] in only):
            by_pid[b["entity_id"]].append(b)
    existing = json.loads(Path(a.existing).read_text()) if a.existing else []
    have_gid = {stem(x["group_id"]) for x in existing if x.get("group_id")}
    have_name = {(x["entity_id"], nkey(x["name"])) for x in existing}
    rows, dropped, skipped = {}, Counter(), Counter()
    for pid, bl in sorted(by_pid.items()):
        bl.sort(key=lambda b: b.get("date") or "", reverse=True)
        ent = ents.get(pid) or {}
        for b in bl[:2]:
            for m in b["ministries"]:
                raw = bg.clean_name(bg.fix_mojibake(m["name"]))
                if b.get("ocr"):
                    raw = clean_ocr(raw)
                res, why = bg.classify(raw)
                if res is None:
                    dropped[why] += 1
                    continue
                if bg.private_name_risk(raw):
                    dropped["private_name_risk"] += 1
                    continue
                key = res[0]
                if res[2] == "knights_of_columbus":
                    key = "kofc"
                gid = f"{pid}:{key}"
                name = display(raw, res, bool(b.get("ocr")))
                if gid in rows:
                    continue
                if stem(gid) in have_gid or (pid, nkey(name)) in have_name or (pid, nkey(raw)) in have_name:
                    skipped["already_in_db"] += 1
                    continue
                _, cat, sub, aud, lang, _, strength = res
                rows[gid] = {
                    "id": gid, "parish_id": pid, "name": name, "source_url": b["source_url"], "scope": "parish",
                    "category": cat, "subcategory": sub, "audience": aud, "language": lang or "en",
                    "language_assumed": not lang, "meets": None,
                    "location": ", ".join(x for x in [ent.get("name"), ent.get("city")] if x),
                    "contact_role": "ministry coordinator (see bulletin) or parish office", "description": None,
                    "join_how": ("Contact the parish office to ask when the next session starts and how to register."
                                 if cat == "formation" else
                                 "Ask the parish office or the ministry coordinator how to join and whether training is offered."
                                 if cat == "liturgical" else "Contact the parish office to ask when it meets."),
                    "source_kind": "bulletin", "confidence": "low" if strength != "specific" else "medium",
                    "last_seen": b["date"], "fetched_at": b["fetched_at"],
                    "flags": [f"source:{b['source_label']}"] + (["ocr"] if b.get("ocr") else []), "bulletin_page": m.get("page"), "raw_name": raw}
    recs = list(rows.values())
    Path(a.json_out).write_text(json.dumps({
        "generated_by": "ingest/public/bulletins/load.py", "count": len(recs), "dropped": dict(dropped),
        "skipped": dict(skipped), "by_parish": dict(Counter(r["parish_id"] for r in recs)), "records": recs},
        ensure_ascii=False, indent=1) + "\n")
    if recs:
        cols = ",".join(COLS)

        def row(r, first):
            vals = dict(r, group_id=r["id"], entity_id=r["parish_id"])
            return "(" + ",".join(lit(vals[c], c, first) for c in COLS) + ")"
        vals = ",\n".join(row(r, i == 0) for i, r in enumerate(recs))
        sql = f"""with v({cols}) as (values
{vals})
, ins as (
  insert into ministry ({cols})
  select {cols} from v
  where not exists (select 1 from ministry x where x.entity_id = v.entity_id and
                    regexp_replace(x.group_id, '_(en\\+es|es|vi|zh|ko|tl|pt|to|ig)$', '') =
                    regexp_replace(v.group_id, '_(en\\+es|es|vi|zh|ko|tl|pt|to|ig)$', ''))
    and not exists (select 1 from ministry x where x.entity_id = v.entity_id
                    and regexp_replace(lower(x.name), '[^a-z]', '', 'g') = regexp_replace(lower(v.name), '[^a-z]', '', 'g'))
  returning entity_id, flags)
select count(*) inserted, count(distinct entity_id) parishes,
       count(*) filter (where flags[1] like 'source:bulletin-lpi%') from_lpi,
       count(*) filter (where flags[1] like 'source:bulletin-discovermass%') from_discovermass,
       count(*) filter (where flags[1] like 'source:bulletin-ecatholic%' or flags[1] like 'source:bulletin-parish-site%') from_side_doors
from ins;
"""
        Path(a.out).write_text(sql)
    print(f"{len(recs)} candidate new groups in {len({r['parish_id'] for r in recs})} parishes; dropped {dict(dropped)}; "
          f"skipped {dict(skipped)} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
