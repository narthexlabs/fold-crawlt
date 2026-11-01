"""Emit batched SQL that loads data/real/media/feed.json and channels.json into Supabase (schema/005_media.sql).

    python3 ingest/public/media/feed_sql.py OUT_DIR [--batch 60]            # full rows (psql / SQL editor)
    python3 ingest/public/media/feed_sql.py OUT_DIR --sync [--known-ids F]   # minimal rows + status (what the app needs)

Writes OUT_DIR/media_item_NN.sql and OUT_DIR/media_channel_NN.sql. Run each file in the SQL editor (or the MCP
execute_sql tool). Statements are INSERT ... ON CONFLICT (id) DO UPDATE of the feed columns only, so a rerun never
overwrites a curator's status or note. Nothing is deleted: items that drop out of a later feed stay in the table
(a curator can hide them).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MEDIA = ROOT / "data" / "real" / "media"

ITEM_COLS = ["id", "org_id", "org_name", "org_type", "deanery_id", "kind", "title", "summary", "url", "embed_url",
             "thumbnail_url", "audio_url", "published_at", "date_precision", "language", "tags", "event_category",
             "source", "source_url", "fetched_at", "score", "auto_status",
             # ranking (rank.py, schema/006_media_ranking.sql)
             "judgment", "judged_by", "content_type", "signals", "rank", "feed_scope", "excluded_reason", "rank_reason",
             "duration_s", "views", "livestream", "prefilter", "clip_start_s", "clip_end_s", "clip_source"]
CLIP = {"clip_start_s", "clip_end_s", "clip_source"}
# A person's override wins: status stays as they set it when the item has a status change in media_item_log
# (clip edits are logged with a note starting "clip "), and a human clip window is never replaced by a suggestion.
HUMAN_STATUS = ("exists (select 1 from public.media_item_log l where l.item_id = media_item.id "
                "and coalesce(l.note, '') not like 'clip %')")
CHANNEL_COLS = ["id", "org_id", "org_name", "org_type", "scope", "platform", "url", "handle", "stats", "last_post_at",
                "livestream", "signal_only", "confidence", "source_url", "fetched_at"]


def lit(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "array[" + ",".join(lit(str(x)) for x in v) + "]::text[]" if v else "'{}'::text[]"
    if isinstance(v, dict):
        return lit(json.dumps(v, ensure_ascii=False)) + "::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


INSERT_ONLY = "--insert-only" in sys.argv  # first load: skip the long DO UPDATE clause


def upsert(table: str, cols: list[str], rows: list[dict]) -> str:
    if table == "media_item":  # status = the algorithm's auto status on insert
        cols = cols + ["status"]
        rows = [{**r, "status": r.get("auto_status") if r.get("auto_status") in ("candidate", "approved", "featured") else "candidate"} for r in rows]
    vals = ",\n".join("(" + ",".join(lit(r.get(c)) for c in cols) + ")" for r in rows)
    def set_one(c: str) -> str:
        if table == "media_item" and c == "status":
            return f"status = case when {HUMAN_STATUS} then media_item.status else excluded.status end"
        if table == "media_item" and c in CLIP:
            return f"{c} = case when media_item.clip_source = 'human' then media_item.{c} else excluded.{c} end"
        return f"{c} = excluded.{c}"
    sets = ", ".join(set_one(c) for c in cols if c != "id")
    tail = ", updated_at = now()" if table == "media_item" else ""
    if INSERT_ONLY:
        return f"insert into public.{table} ({', '.join(cols)}) values\n{vals}\non conflict (id) do nothing;\n"
    return f"insert into public.{table} ({', '.join(cols)}) values\n{vals}\non conflict (id) do update set {sets}{tail};\n"


def channel_rows() -> list[dict]:
    data = json.loads((MEDIA / "channels.json").read_text())
    live = {x["owner_id"] for x in data.get("livestream_map", [])}
    rows = []
    for c in data["channels"]:
        st = c.get("stats") or {}
        compact = {k: st[k] for k in ("channel_title", "account_title", "subscribers", "videos", "activity_status",
                                      "posts_per_month_est", "days_since_last_post", "languages_in_titles", "source")
                   if st.get(k) is not None} or None
        if compact is not None and st.get("fetched_at"):
            compact["fetched_at"] = st["fetched_at"]
        ev = next((e["url"] for e in c.get("evidence") or [] if str(e.get("url", "")).startswith("http")), None)
        rows.append({
            "id": c["id"], "org_id": c["owner_id"], "org_name": c.get("owner_name"), "org_type": c.get("owner_type"),
            "scope": c.get("scope"), "platform": c["platform"], "url": c.get("url"), "handle": c.get("handle"),
            "stats": compact, "last_post_at": st.get("last_post"),
            "livestream": bool((st.get("livestream") or {}).get("present")) or (c["owner_id"] in live and c["platform"] in ("youtube", "livestream_other")),
            "signal_only": bool(c.get("signal_only")), "confidence": c.get("confidence"),
            "source_url": ev or st.get("source") or c.get("url"), "fetched_at": c.get("fetched_at"),
        })
    return rows


SYNC_COLS = ["id", "org_id", "kind", "title", "published_at", "status", "content_type", "judged_by", "feed_scope", "rank", "score",
             "auto_status"]


def sync(out: Path, items: list[dict], batch: int) -> None:
    """--sync [--known-ids FILE]: the small load the app needs. The pages read the ranking itself from feed.json
    (deployed with the app); the database holds one row per item for status, overrides, clip and the change log.
    New items get a minimal row; known ones get status/rank/scope updates (a person's override is kept)."""
    known: set[str] = set()
    if "--known-ids" in sys.argv:
        known = set(Path(sys.argv[sys.argv.index("--known-ids") + 1]).read_text().strip().split(","))
    rows = [{**r, "status": r.get("auto_status") if r.get("auto_status") in ("candidate", "approved", "featured") else "candidate"}
            for r in items]
    new = [r for r in rows if r["id"] not in known]
    old = [r for r in rows if r["id"] in known]
    cols = SYNC_COLS[:-1]
    for i in range(0, len(new), batch):
        vals = ",\n".join("(" + ",".join(lit(r.get(c)) for c in cols) + ")" for r in new[i:i + batch])
        (out / f"sync_insert_{i // batch:02d}.sql").write_text(
            f"insert into public.media_item ({', '.join(cols)}) values\n{vals}\non conflict (id) do nothing;\n")
    upd_cols = ["id", "status", "content_type", "judged_by", "feed_scope", "rank", "score"]
    for i in range(0, len(old), batch * 2):
        vals = ",\n".join("(" + ",".join(lit(r.get(c)) for c in upd_cols) + ")" for r in old[i:i + batch * 2])
        (out / f"sync_update_{i // (batch * 2):02d}.sql").write_text(
            "update public.media_item m set "
            f"status = case when {HUMAN_STATUS.replace('media_item.id', 'm.id')} then m.status else v.status end, "
            "content_type = v.content_type, judged_by = v.judged_by, feed_scope = v.feed_scope, rank = v.rank::int, "
            "score = v.score::numeric, updated_at = now() "
            f"from (values\n{vals}\n) as v({', '.join(upd_cols)}) where m.id = v.id;\n")
    ids = ",".join(lit(x["id"]) for x in items)
    (out / "sync_zz_retire.sql").write_text(
        f"update public.media_item set feed_scope = 'retired', rank = null, excluded_reason = 'retired: not in the latest feed build' "
        f"where id not in ({ids}) and coalesce(feed_scope, '') <> 'retired';\n")
    print(f"sync: {len(new)} new rows, {len(old)} updates -> {out}")


def main() -> None:
    out = Path(sys.argv[1])
    batch = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 60
    out.mkdir(parents=True, exist_ok=True)
    items = json.loads((MEDIA / "feed.json").read_text())["items"]
    if "--sync" in sys.argv:
        sync(out, items, batch)
        return
    for it in items:  # trim what the pages do not read
        if it.get("judgment"):
            it["judgment"] = {k: v for k, v in it["judgment"].items()}
        if it.get("signals"):
            it["signals"] = {k: it["signals"][k] for k in ("duration_band", "view_velocity", "freshness", "half_life_days",
                                                         "rarity_boost", "signals_score") if k in it["signals"]}
    for i in range(0, len(items), batch):
        (out / f"media_item_{i // batch:02d}.sql").write_text(upsert("media_item", ITEM_COLS, items[i:i + batch]))
    chans = channel_rows()
    for i in range(0, len(chans), batch * 2):
        (out / f"media_channel_{i // (batch * 2):02d}.sql").write_text(upsert("media_channel", CHANNEL_COLS, chans[i:i + batch * 2]))
    # items that dropped out of this build stay in the table (nothing is deleted); mark them retired so pages skip them
    ids = ",".join(lit(x["id"]) for x in items)
    (out / "media_item_zz_retire.sql").write_text(
        f"update public.media_item set feed_scope = 'retired', rank = null, excluded_reason = 'retired: not in the latest feed build' "
        f"where id not in ({ids}) and coalesce(feed_scope, '') <> 'retired';\n")
    print(f"{len(items)} items, {len(chans)} channels -> {out}")


if __name__ == "__main__":
    main()
