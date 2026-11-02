"""Step 4 of the YouTube catalog (called by media_feed.py build_outputs): channels.json, audit.json, coverage.json and the
README in the output folder (data/real/california/media/, or the national folder when MEDIA_OUT is set).

Nothing here makes a network request. Every number comes from the files the earlier steps wrote:
  RAW/parish_index.json  (media_collect.py; the parishes we know, per diocese)
  RAW/verified.json      (media_resolve.py verify; ownership verdict and reasons for every channel)
  the items / quarantine / per-channel stats that media_feed.py gathered from the public /videos and /streams tabs.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

from media_common import OUT, RAW


def _screen():
    root = Path(__file__).resolve().parents[2] / "ingest" / "public"
    import sys  # noqa: PLC0415
    for x in (str(root), str(root / "media")):
        if x not in sys.path:
            sys.path.insert(0, x)
    import media_privacy  # noqa: PLC0415
    return media_privacy


def audit_row(c: dict, keep: tuple) -> dict:
    """An audit row; a channel whose title names a private person (someone's personal channel linked from a parish site)
    keeps only its opaque channel id and a redacted title: no @handle, no linked URL that could carry the name."""
    row = {k: c.get(k) for k in keep if c.get(k) not in (None, [], "")}
    mp = _screen()
    ok, why = mp.screen(c.get("title") or "", "video", "other")
    if not ok:
        row["title"] = mp.redacted(c.get("title") or "")
        row["title_withheld"] = why
        row["url"] = f"https://www.youtube.com/channel/{c['channel_id']}"
        for k in ("handle", "linked_as"):
            row.pop(k, None)
        if row.get("reason"):
            row["reason"] = mp.redacted(row["reason"])
    return row


def _compact(c: dict) -> dict:
    keep = ("channel_id", "title", "handle", "url", "status", "reason", "linked_by", "dioceses_linking", "diocese", "belongs_to",
            "candidates", "diocese_of_channel", "linked_as", "fetched_at")
    return audit_row(c, keep)


def write_channels_and_readme(items, quarantine, stat_by_channel, counts, ver, ranked, ts, yt, label: str = "California",
                              generated_by: str = "ingest/california/media_feed.py", dioceses: dict | None = None) -> None:
    out_dir: Path = OUT
    idx_f = RAW / "parish_index.json"
    parishes = json.loads(idx_f.read_text())["parishes"] if idx_f.exists() else []
    dio_names = {k: v.get("name") for k, v in (dioceses or {}).items()}

    # ---- channels.json: the catalogued channels (verified / probable) with what their tabs showed
    def with_stats(c: dict, level: str) -> dict:
        st = stat_by_channel.get(c["channel_id"])
        rec = {k: c.get(k) for k in ("channel_id", "title", "handle", "url", "status", "owner_id", "owner_name", "owner_type", "city",
                                     "diocese", "reasons", "checks", "also_linked_by", "linked_by", "evidence", "fetched_at")
               if c.get(k) not in (None, [], "")}
        if level == "diocese":
            rec["owner_type"] = "diocese"
            rec["owner_id"] = c["diocese"]
            rec["owner_name"] = dio_names.get(c["diocese"]) or c["diocese"]
            rec["reasons"] = (c.get("diocese_match") or {}).get("reasons")
        rec["tabs_read"] = bool(st)
        if st:
            rec["activity"] = {k: st.get(k) for k in ("last_post", "days_since_last_post", "posts_per_month_est", "activity_status",
                                                      "languages_in_titles")}
            rec["livestream"] = st.get("livestream")
            rec["videos_in_feed"] = st.get("videos_in_feed")
            rec["streams_in_feed"] = st.get("streams_in_feed")
            rec["tabs_source"] = st.get("source")
            rec["tabs_fetched_at"] = st.get("fetched_at")
        return rec

    parish_ch = [with_stats(c, "parish") for c in ver["channels"] if c["status"] in ("verified", "probable")]
    dio_ch = [with_stats(c, "diocese") for c in ver["diocese_channels"] if c["status"] in ("verified", "probable")]
    status_ct = collections.Counter(c["status"] for c in ver["channels"]) + collections.Counter(
        "diocese_" + c["status"] for c in ver["diocese_channels"])
    ch_doc = {
        "generated_at": ts, "generated_by": generated_by,
        "what": f"{label} YouTube channels of parishes, schools and dioceses whose ownership was checked (verified or probable). "
                "Each record says why (reasons/checks) and where the link was found (evidence).",
        "verdicts": {"verified": "the channel's About links to the parish website, or its description names it, or its @handle is "
                                 "the site's name; or the parish name and the city are on the channel; or the parish name plus a "
                                 "link from the parish's own website or listing",
                     "probable": "the parish name matches but nothing else corroborates it; or part of the name plus the city or "
                                 "the parish's own link"},
        "counts": {"parish_or_school_channels": len(parish_ch), "diocese_channels": len(dio_ch), "by_status": dict(status_ct),
                   "unverified": len(ver["unverified"]), "borrowed": len(ver["borrowed"]), "unresolved_links": len(ver["unresolved"]),
                   "tabs_read": sum(1 for c in parish_ch + dio_ch if c["tabs_read"])},
        "channels": sorted(parish_ch, key=lambda c: (c.get("diocese") or "", c.get("owner_name") or "")),
        "diocese_channels": dio_ch,
    }
    (out_dir / "channels.json").write_text(json.dumps(ch_doc, indent=1, ensure_ascii=False) + "\n")

    # ---- audit.json: what was NOT catalogued, and why (so a person can promote a channel by hand)
    audit = {"generated_at": ts, "note": "Channels found on parish / diocese pages that were not catalogued. unverified: nothing ties "
                                          "the channel to the parish; borrowed: a site-builder default, a national body, another diocese "
                                          "or another parish's channel; unresolved: the link did not lead to a channel.",
             "unverified": [_compact(c) for c in ver["unverified"]], "borrowed": [_compact(c) for c in ver["borrowed"]],
             "unresolved": ver["unresolved"]}
    (out_dir / "audit.json").write_text(json.dumps(audit, indent=1, ensure_ascii=False) + "\n")

    # ---- coverage.json: per diocese
    by_dio = collections.defaultdict(lambda: collections.Counter())
    for p in parishes:
        by_dio[p["diocese"]]["parishes_indexed"] += 1
    cand_f = RAW / "candidates.json"
    cands = json.loads(cand_f.read_text())["candidates"] if cand_f.exists() else []
    linked = collections.defaultdict(set)
    for c in cands:
        if c.get("owner_type") == "parish" and c.get("owner_id"):
            linked[c["diocese"]].add(c["owner_id"])
    for d, s in linked.items():
        by_dio[d]["parishes_with_a_youtube_link"] = len(s)
    owners = collections.defaultdict(lambda: collections.defaultdict(set))
    for c in parish_ch:
        owners[c.get("diocese")][c["status"]].add(c.get("owner_id"))
        by_dio[c.get("diocese")]["channels_catalogued"] += 1
        if c.get("tabs_read"):
            by_dio[c.get("diocese")]["channels_tabs_read"] += 1
        if (c.get("activity") or {}).get("activity_status") == "active":
            by_dio[c.get("diocese")]["channels_active_45d"] += 1
        if (c.get("livestream") or {}).get("streams_last_30d"):
            by_dio[c.get("diocese")]["channels_streamed_last_30d"] += 1
    for d, m in owners.items():
        by_dio[d]["parishes_with_verified_channel"] = len(m.get("verified", set()))
        by_dio[d]["parishes_with_probable_only"] = len(m.get("probable", set()) - m.get("verified", set()))
    for c in dio_ch:
        by_dio[c["diocese"]]["diocese_channels"] += 1
    for it in items:
        by_dio[it["diocese"]]["videos_kept"] += 1
        if it.get("prefilter") == "routine_liturgy":
            by_dio[it["diocese"]]["routine_liturgy_flagged"] += 1
    org_dio = {it["org_id"]: it["diocese"] for it in items}
    for q in quarantine:
        by_dio[org_dio.get(q["org_id"]) or (q["org_id"] if q["org_id"] in dio_names else "unknown")]["quarantined"] += 1
    for slug, d in (ranked.get("dioceses") or {}).items():
        by_dio[slug]["on_feed_candidates"] = d.get("on_feed", 0)
    cov = {"generated_at": ts, "label": label,
           "note": "Per diocese. parishes_indexed = parish records we hold; a parish counts once however many channels it has. "
                   "on_feed_candidates = items the HEURISTIC ranking would put on that diocese's list (a person reviews them).",
           "totals": dict(sum((c for c in by_dio.values()), collections.Counter())),
           "pipeline_counts": dict(counts),
           "youtube_requests": {"made_this_run": getattr(yt, "requests_made", 0), "cache_hits": getattr(yt, "cache_hits", 0),
                                "stopped": getattr(yt, "stopped", None)},
           "dioceses": {k: dict(v) for k, v in sorted(by_dio.items())}}
    (out_dir / "coverage.json").write_text(json.dumps(cov, indent=1, ensure_ascii=False) + "\n")
    print("wrote channels.json, audit.json, coverage.json", flush=True)
