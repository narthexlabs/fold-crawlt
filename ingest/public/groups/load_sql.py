"""Emit batched upsert SQL for data/real/groups/groups.json into the ministry table (see schema/008_groups.sql).

    python3 -I ingest/public/groups/load_sql.py [--batch 120] [--out /tmp/groups_sql]

Writes <out>/groups_NN.sql. Each file is one self-contained statement block that
  1. updates rows already carrying the group_id (re-runs),
  2. claims a legacy ministry row with the same (entity_id, name) and no group_id (fills all group columns),
  3. inserts groups whose group_id is not present yet.
No DELETE anywhere. Re-running is idempotent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "data" / "real" / "groups" / "groups.json"

COLS = ["group_id", "entity_id", "name", "source_url", "scope", "category", "subcategory", "audience", "language",
        "language_assumed", "meets", "location", "contact_role", "description", "join_how", "source_kind",
        "confidence", "last_seen", "fetched_at", "flags"]
TYPES = {"language_assumed": "boolean", "last_seen": "date", "fetched_at": "timestamptz", "flags": "text[]"}


def lit(v, col, first=True):
    """Typed casts only on the first row of a VALUES list; later rows' literals take the column's resolved type."""
    if v is None or v == []:
        return ("NULL::" + TYPES[col]) if (col in TYPES and first) else "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "ARRAY[" + ",".join("'" + str(x).replace("'", "''") + "'" for x in v) + "]::text[]"
    s = "'" + str(v).replace("'", "''") + "'"
    return s + ("::" + TYPES[col] if (col in TYPES and first) else "")


def row(r, first=True):
    vals = {"group_id": r["id"], "entity_id": r["parish_id"], "name": r["name"], "source_url": r["source_url"],
            "scope": r["scope"], "category": r["category"], "subcategory": r["subcategory"],
            "audience": r["audience"], "language": r["language"], "language_assumed": r["language_assumed"],
            "meets": r["meets"], "location": r["location"], "contact_role": r["contact_role"],
            "description": r["description"], "join_how": r["join_how"], "source_kind": r["source_kind"],
            "confidence": r["confidence"], "last_seen": r["last_seen"], "fetched_at": r["fetched_at"],
            "flags": r["flags"]}
    return "(" + ",".join(lit(vals[c], c, first) for c in COLS) + ")"


def batch_sql(rows):
    vals = ",\n".join(row(r, i == 0) for i, r in enumerate(rows))
    cols = ",".join(COLS)
    sets = ",".join(f"{c}=v.{c}" for c in COLS if c not in ("entity_id",))
    return f"""with v({cols}) as (values
{vals})
, upd as (
  update ministry m set {sets} from v where m.group_id = v.group_id returning m.group_id)
, claim as (
  update ministry m set {sets} from v
  where m.group_id is null and m.entity_id = v.entity_id and m.name = v.name
    and not exists (select 1 from ministry x where x.group_id = v.group_id)
  returning m.group_id)
, ins as (
  insert into ministry ({cols})
  select {cols} from v
  where not exists (select 1 from ministry x where x.group_id = v.group_id)
    and v.group_id not in (select group_id from claim)
  returning group_id)
select (select count(*) from upd) updated, (select count(*) from claim) claimed, (select count(*) from ins) inserted;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=120)
    ap.add_argument("--out", default="/tmp/groups_sql")
    ap.add_argument("--compact", action="store_true", help="JSON + string-table form (about half the size)")
    a = ap.parse_args()
    recs = json.loads(SRC.read_text())["records"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(recs), a.batch):
        fn = compact_sql if a.compact else batch_sql
        (out / f"groups_{i // a.batch:02d}.sql").write_text(fn(recs[i:i + a.batch]))
    print(f"{len(recs)} groups -> {out} ({(len(recs) + a.batch - 1) // a.batch} files)")



# --------------------------------------------------------------------------- compact form (used for the MCP load)
# Same three-step upsert, but the batch travels as JSON with a string table: any value used twice in the batch is
# sent once and referenced by index. Roughly halves the statement size; decoded entirely in SQL.

def compact_sql(rows):
    vals = []
    for r in rows:
        vals.append({"group_id": r["id"], "entity_id": r["parish_id"], "name": r["name"],
                     "source_url": r["source_url"], "scope": r["scope"], "category": r["category"],
                     "subcategory": r["subcategory"], "audience": r["audience"], "language": r["language"],
                     "language_assumed": "t" if r["language_assumed"] else "f", "meets": r["meets"],
                     "location": r["location"], "contact_role": r["contact_role"], "description": r["description"],
                     "join_how": r["join_how"], "source_kind": r["source_kind"], "confidence": r["confidence"],
                     "last_seen": r["last_seen"], "fetched_at": r["fetched_at"],
                     "flags": ",".join(r["flags"]) if r["flags"] else None})
    from collections import Counter
    cnt = Counter(v for row in vals for v in row.values() if isinstance(v, str))
    table = [s for s, n in cnt.items() if n >= 2 and len(s) > 3]
    idx = {s: i for i, s in enumerate(table)}
    packed = [[(idx[row[c]] if isinstance(row[c], str) and row[c] in idx else row[c]) for c in COLS] for row in vals]
    dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    js = ('{"s":[\n' + ",\n".join(dump(x) for x in table) + '],\n"r":[\n' + ",\n".join(dump(x) for x in packed)
          + "]}").replace("'", "''")
    def f(i, c):
        e = f"(case jsonb_typeof(r->{i}) when 'number' then d.j->'s'->>((r->>{i})::int) else r->>{i} end)"
        if c == "language_assumed":
            return f"({e} = 't')"
        if c == "flags":
            return f"string_to_array({e}, ',')"
        if c in ("last_seen", "fetched_at"):
            return f"{e}::{TYPES[c]}"
        return e
    sel = ",\n  ".join(f"{f(i, c)} as {c}" for i, c in enumerate(COLS))
    cols = ",".join(COLS)
    sets = ",".join(f"{c}=v.{c}" for c in COLS if c not in ("entity_id",))
    return f"""with d as (select '{js}'::jsonb j)
, v as (select
  {sel}
  from d, jsonb_array_elements(d.j->'r') r)
, upd as (
  update ministry m set {sets} from v where m.group_id = v.group_id returning m.group_id)
, claim as (
  update ministry m set {sets} from v
  where m.group_id is null and m.entity_id = v.entity_id and m.name = v.name
    and not exists (select 1 from ministry x where x.group_id = v.group_id)
  returning m.group_id)
, ins as (
  insert into ministry ({cols})
  select {cols} from v
  where not exists (select 1 from ministry x where x.group_id = v.group_id)
    and v.group_id not in (select group_id from claim)
  returning group_id)
select (select count(*) from upd) updated, (select count(*) from claim) claimed, (select count(*) from ins) inserted;
"""



if __name__ == "__main__":
    main()
