"""Step 3 of the California YouTube catalog: recent videos for the verified / probable channels, the privacy screen, the
HEURISTIC ranking, and the files in data/real/california/media/.

    python3 -I ingest/california/media_feed.py            # network: 2 tab pages per channel (1 request / 2 s, cached)
    python3 -I ingest/california/media_feed.py --offline  # cache only (reruns the ranking and the files)

For each channel whose ownership is `verified` or `probable` (media_resolve.py verify):
  * the public /videos tab: the last 25 uploads (title, relative date -> approximate date, duration, views);
  * the public /streams tab: live now, upcoming, and past streams of the last 30 days (up to 15), flagged `livestream`.
  /feeds/videos.xml and /results are disallowed by YouTube's robots.txt and are never used. With YOUTUBE_API_KEY set the
  Data API v3 uploads playlist gives exact dates (code path shared with ingest/public/media/feed_build.py; not exercised in
  this build, which had no key).
Titles pass ingest/public/media/media_privacy.py: funerals of lay people, Mass intentions, weddings, baptisms, students and
other private names are dropped to quarantine.json with a redacted title and the reason.
Ranking: ingest/public/media/rank.py, its HeuristicJudge (no ANTHROPIC_API_KEY): once for the whole state and once per
diocese, each in a scratch folder so nothing under data/real/media/ (Oakland) is touched.
"""
from __future__ import annotations

import collections
import copy
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from media_common import CA, NOW, OUT, RAW, YouTube, now_iso, parse_tab  # noqa: E402

PUB = ROOT / "ingest" / "public"
sys.path.insert(0, str(PUB))
sys.path.insert(0, str(PUB / "media"))
import feed_build as fb  # noqa: E402  (is_routine, lang_guess, SPECIAL, item_id, iso; also loads stats.py)
import media_privacy  # noqa: E402
import rank  # noqa: E402

OFFLINE = "--offline" in sys.argv
API_KEY = os.environ.get("YOUTUBE_API_KEY")
VERIFIED = RAW / "verified.json"
DIOCESES = {d["slug"]: d for d in json.loads((CA / "dioceses.json").read_text())["dioceses"]}
UPLOADS = 25
STREAMS_MAX = 15
STREAM_DAYS = 30
ROUTINE_KEEP_DAYS = fb.ROUTINE_KEEP_DAYS


class OfflineYT:
    """Cache-only stand-in for YouTube() when --offline."""
    requests_made = cache_hits = errors = 0
    stopped = None

    def __init__(self):
        from media_common import CACHE  # noqa: PLC0415
        self.CACHE = CACHE

    def get(self, url, kind="page", max_age_h=0):
        import gzip, hashlib  # noqa: E401, PLC0415
        f = self.CACHE / (hashlib.sha1((kind + url).encode()).hexdigest()[:20] + ".json.gz")
        if f.exists():
            self.cache_hits += 1
            d = json.loads(gzip.decompress(f.read_bytes()))
            d["cached"] = True
            return d
        return {"status": None, "final_url": None, "data": None, "fetched_at": None, "cached": False, "error": "offline_not_cached"}


def api_uploads(cid: str) -> list[dict]:
    """Data API v3 path (only with YOUTUBE_API_KEY): uploads playlist + videos.list for duration, views, live state."""
    import requests  # noqa: PLC0415
    base = "https://www.googleapis.com/youtube/v3"
    r = requests.get(f"{base}/playlistItems", params={"part": "snippet", "maxResults": UPLOADS, "playlistId": "UU" + cid[2:], "key": API_KEY}, timeout=25)
    if r.status_code != 200:
        return []
    items = [(it["snippet"]["resourceId"]["videoId"], it["snippet"]) for it in r.json().get("items", []) if (it.get("snippet") or {}).get("resourceId")]
    ids = [i for i, _ in items]
    det = {}
    if ids:
        rv = requests.get(f"{base}/videos", params={"part": "contentDetails,statistics,snippet,liveStreamingDetails", "id": ",".join(ids), "key": API_KEY}, timeout=25)
        det = {v["id"]: v for v in rv.json().get("items", [])} if rv.status_code == 200 else {}
    out = []
    for vid, sn in items:
        v = det.get(vid, {})
        dur = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", (v.get("contentDetails") or {}).get("duration", "") or "")
        dsec = (int(dur.group(1) or 0) * 3600 + int(dur.group(2) or 0) * 60 + int(dur.group(3) or 0)) if dur else None
        lb = (v.get("snippet") or {}).get("liveBroadcastContent")
        out.append({"vid": vid, "title": sn.get("title") or "", "tab": "streams" if (v.get("liveStreamingDetails") or lb in ("live", "upcoming")) else "videos",
                    "when": __import__("datetime").datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")) if sn.get("publishedAt") else None,
                    "duration_s": dsec, "views": int((v.get("statistics") or {}).get("viewCount", 0)) if (v.get("statistics") or {}).get("viewCount") else None,
                    "upcoming": lb == "upcoming", "live_now": lb == "live", "relative": None, "exact": True})
    return out


def main() -> None:
    ver = json.loads(VERIFIED.read_text())
    chans = [c for c in ver["channels"] if c["status"] in ("verified", "probable")]
    dio = [c for c in ver["diocese_channels"] if c["status"] in ("verified", "probable")]
    yt = OfflineYT() if OFFLINE else YouTube()
    items: list[dict] = []
    quarantine: list[dict] = []
    counts = collections.Counter()
    stat_by_channel: dict[str, dict] = {}
    all_ch = [("parish", c) for c in chans] + [("diocese", c) for c in dio]
    print(f"{len(all_ch)} channels to read ({len(chans)} parish/school, {len(dio)} diocese-level)", flush=True)
    for n, (kind, c) in enumerate(all_ch, 1):
        cid = c["channel_id"]
        if kind == "diocese":
            owner_id, owner_name, org_type, slug = c["diocese"], DIOCESES[c["diocese"]]["name"], "diocese", c["diocese"]
            org_type = "diocese"
            parish_id = None
        else:
            owner_id, owner_name, org_type, slug, parish_id = c["owner_id"], c["owner_name"], c["owner_type"], c["diocese"], c["owner_id"]
        vids: list[dict] = []
        tab_status = {}
        if API_KEY and not OFFLINE:
            vids = api_uploads(cid)
            tab_status["api"] = bool(vids)
        if not vids:
            rv = yt.get(f"https://www.youtube.com/channel/{cid}/videos")
            rs = yt.get(f"https://www.youtube.com/channel/{cid}/streams")
            tab_status = {"videos": rv["status"], "streams": rs["status"]}
            ups = parse_tab(rv["data"], "videos")[:UPLOADS]
            strs = parse_tab(rs["data"], "streams")
            vids = ups + strs
        # livestream statistics from the /streams tab sample
        s_items = [v for v in vids if v["tab"] == "streams" or v.get("live_now")]
        ls30 = [v for v in s_items if v["when"] and not v["upcoming"] and (NOW - v["when"]).days < STREAM_DAYS]
        ls30_live = sum(1 for v in s_items if v["live_now"])
        seen_vid = set()
        kept_ups = kept_streams = 0
        titles_safe = []
        dates_for_activity = []
        n_seen = len(vids)
        for v in sorted(vids, key=lambda v: (v["when"] is None, -(v["when"].timestamp()) if v["when"] else 0)):
            if v["vid"] in seen_vid:
                continue
            seen_vid.add(v["vid"])
            is_stream = v["tab"] == "streams" or v["live_now"] or v["upcoming"]
            age_d = (NOW - v["when"]).days if v["when"] else None
            if is_stream:
                if not (v["live_now"] or v["upcoming"]):
                    if age_d is None or age_d >= STREAM_DAYS or kept_streams >= STREAMS_MAX:
                        counts["stream_older_than_30_days_or_over_cap"] += 1
                        continue
            else:
                if kept_ups >= UPLOADS:
                    continue
            title = v["title"]
            keep, why = media_privacy.screen(title, "video", org_type if org_type in ("school", "parish") else "other")
            iid = fb.item_id("yt", v["vid"])
            if not keep:
                counts[f"privacy_{why}"] += 1
                quarantine.append({"id": iid, "org_id": owner_id, "kind": "video", "source": "YouTube",
                                   "published_at": fb.iso(v["when"]), "reason": why, "title_redacted": media_privacy.redacted(title),
                                   "url": f"https://www.youtube.com/watch?v={v['vid']}"})
                continue
            routine = fb.is_routine(title) or (is_stream and not fb.SPECIAL.search(title))
            if is_stream:
                kept_streams += 1
            else:
                kept_ups += 1
            if v["when"] and not v["upcoming"]:
                dates_for_activity.append(fb.iso(v["when"]))
            if len(titles_safe) < 5:
                titles_safe.append(title)
            items.append({
                "id": iid, "org_id": owner_id, "org_name": owner_name, "org_type": org_type, "deanery_id": None, "diocese": slug,
                "parish_id": parish_id, "channel_id": cid, "channel_title": c["title"], "kind": "video", "title": title, "summary": None,
                "url": f"https://www.youtube.com/watch?v={v['vid']}", "embed_url": f"https://www.youtube-nocookie.com/embed/{v['vid']}",
                "thumbnail_url": f"https://i.ytimg.com/vi/{v['vid']}/hqdefault.jpg", "published_at": fb.iso(v["when"]),
                "date_precision": "exact" if v.get("exact") else "approx", "language": fb.lang_guess(title),
                "source": "YouTube: " + ("Data API v3 (uploads playlist)" if v.get("exact") else "public channel /videos and /streams tabs"),
                "source_url": c["url"], "fetched_at": now_iso(), "duration_s": v["duration_s"], "views": v["views"],
                "livestream": bool(is_stream), "is_live": bool(v["live_now"]), "is_upcoming": bool(v["upcoming"]),
                "prefilter": "routine_liturgy" if routine else None, "tags": []})
            counts["routine_liturgy_flagged"] += 1 if routine else 0
        acts = fb.stats.activity(dates_for_activity)
        last_stream = max((fb.iso(v["when"]) for v in s_items if v["when"] and not v["upcoming"]), default=None)
        stat_by_channel[cid] = {
            "channel_title": c["title"], "channel_id": cid, "subscribers": None, "videos": None,
            "last_post": acts.get("last_post"), "days_since_last_post": acts.get("days_since_last_post"),
            "posts_per_month_est": acts.get("posts_per_month_est"), "activity_status": acts.get("status"),
            "languages_in_titles": fb.stats.langs_of(titles_safe) if titles_safe else [],
            "livestream": {"present": bool(s_items), "streams_in_sample": len(s_items), "last_stream": (last_stream or "")[:10] or None,
                           "mass_titles_in_sample": sum(1 for v in s_items if fb.stats.LIVE_RX.search(v["title"])),
                           "streams_last_30d": len(ls30), "live_now": ls30_live,
                           "upcoming": sum(1 for v in s_items if v["upcoming"])},
            "recent_titles": titles_safe, "videos_in_feed": kept_ups, "streams_in_feed": kept_streams, "tab_status": tab_status,
            "source": f"https://www.youtube.com/channel/{cid} (+ /videos, /streams tabs)" if not API_KEY else "YouTube Data API v3",
            "fetched_at": now_iso(),
        }
        counts["items_seen_in_tabs"] += n_seen
        if n % 20 == 0:
            print(f"  {n}/{len(all_ch)} channels; {len(items)} items; requests {yt.requests_made}, cache hits {yt.cache_hits}", flush=True)
    print(f"read {len(all_ch)} channels; {len(items)} items; {len(quarantine)} quarantined", flush=True)
    (RAW / "feed_items.json").write_text(json.dumps({"items": items, "quarantine": quarantine, "stats": stat_by_channel,
                                                      "counts": dict(counts)}, ensure_ascii=False) + "\n")
    build_outputs(items, quarantine, stat_by_channel, counts, ver, yt)


# ----------------------------------------------------------------------------------------------------- ranking
def run_rank(items: list[dict]) -> dict[str, dict]:
    """rank.py's heuristic path over `items` in a scratch folder; returns id -> ranking fields."""
    with tempfile.TemporaryDirectory() as td:
        rank.MEDIA = Path(td)
        (Path(td) / "feed.json").write_text(json.dumps({"items": copy.deepcopy(items)}, ensure_ascii=False))
        keep_argv = sys.argv
        sys.argv = [sys.argv[0]]
        os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with redirect_stdout(io.StringIO()):
                rank.main()
        finally:
            sys.argv = keep_argv
        ranked = json.loads((Path(td) / "feed.json").read_text())
    out = {}
    for it in ranked["items"]:
        out[it["id"]] = {k: it.get(k) for k in ("rank", "score", "auto_status", "content_type", "excluded_reason", "rank_reason", "judgment",
                                                "feed_scope")}
    return out, ranked.get("ranked_by"), ranked.get("rank_counts")


def slim(it: dict, rk: dict, extra: dict | None = None) -> dict:
    j = rk.get("judgment") or {}
    d = {"rank": rk["rank"], "score": rk["score"], "id": it["id"], "title": it["title"], "channel": it.get("channel_title"),
         "org_id": it["org_id"], "org_name": it["org_name"], "org_type": it["org_type"], "diocese": it["diocese"],
         "published_at": it["published_at"], "date_precision": it["date_precision"], "duration_s": it["duration_s"], "views": it["views"],
         "livestream": it["livestream"], "is_live": it["is_live"], "is_upcoming": it["is_upcoming"], "url": it["url"],
         "thumbnail_url": it["thumbnail_url"], "language": it["language"], "content_type": rk["content_type"],
         "breadth": j.get("breadth"), "auto_status": rk["auto_status"], "reason": (j.get("reason") or "")[:200]}
    if extra:
        d.update(extra)
    return d


def build_outputs(items, quarantine, stat_by_channel, counts, ver, yt) -> None:
    ts = now_iso()
    # ---- ranking: statewide, then per diocese
    all_rk, ranked_by, rank_counts = run_rank(items)
    by_id = {it["id"]: it for it in items}
    by_dio = collections.defaultdict(list)
    for it in items:
        by_dio[it["diocese"]].append(it)
    dio_out = {}
    for slug in DIOCESES:
        its = by_dio.get(slug, [])
        if not its:
            dio_out[slug] = {"diocese": slug, "name": DIOCESES[slug]["name"], "items_considered": 0, "on_feed": 0, "candidates": []}
            continue
        rk, rb, rc = run_rank(its)
        on = sorted((i for i in its if not rk[i["id"]]["excluded_reason"] and rk[i["id"]]["rank"]), key=lambda i: rk[i["id"]]["rank"])
        dio_out[slug] = {
            "diocese": slug, "name": DIOCESES[slug]["name"], "items_considered": len(its), "on_feed": len(on),
            "excluded_by_reason": dict(collections.Counter((rk[i["id"]]["excluded_reason"] or "on feed").split(":")[0] for i in its)),
            "by_content_type": dict(collections.Counter(rk[i["id"]]["content_type"] for i in its)),
            "candidates": [slim(i, rk[i["id"]], {"statewide_rank": all_rk[i["id"]]["rank"]}) for i in on[:100]]}
    state_on = sorted((i for i in items if not all_rk[i["id"]]["excluded_reason"] and all_rk[i["id"]]["rank"]), key=lambda i: all_rk[i["id"]]["rank"])
    ranked = {
        "generated_at": ts, "generated_by": "ingest/california/media_feed.py (ingest/public/media/rank.py, HeuristicJudge)",
        "label": "HEURISTIC RANKING - NOT A HUMAN OR LLM JUDGMENT. Candidates only: a person reviews them. Every item was judged by "
                 "keyword and metadata rules on its title, channel type, duration and age (judged_by: heuristic); nobody watched the videos.",
        "judge": "heuristic", "ranker_config_version": rank.CFG["version"], "heuristic_version": rank.HEURISTIC_VERSION,
        "ranked_by": ranked_by, "how": "docs/design/media-ranking.md. Run once for the whole state (statewide_top, rank) and once per diocese "
                                     "(candidates; the cap of 2 items per channel per week and the rotation apply inside that diocese).",
        "caveats": ["Dates are approximate ('3 weeks ago'), so the freshness and per-week cap are approximate.",
                    "The heuristic reads titles only; a substantive video with a plain title can land as 'other'.",
                    "Routine Mass streams and school-internal videos are kept off the list by rule (excluded_by_reason).",
                    "Channels are catalogued after an ownership check (verified or probable); see channels.json."],
        "counts": {"items_considered": len(items), "on_feed_statewide": len(state_on), "rank_counts": rank_counts},
        "statewide_top": [slim(i, all_rk[i["id"]]) for i in state_on[:100]],
        "dioceses": dio_out}
    (OUT / "ranked_heuristic.json").write_text(json.dumps(ranked, indent=1, ensure_ascii=False) + "\n")

    # ---- feed.json (gathered candidates, unranked here; Oakland's item fields plus diocese / parish / channel / is_live)
    items_sorted = sorted(items, key=lambda i: (i["diocese"] or "", i["org_id"], i["published_at"] or ""), reverse=False)
    feed = {
        "generated_at": ts, "generated_by": "ingest/california/media_feed.py",
        "window": {"uploads_per_channel": UPLOADS, "past_streams_days": STREAM_DAYS, "past_streams_max": STREAMS_MAX,
                   "routine_keep_days_note": "unlike Oakland's feed, routine Mass items are kept and flagged prefilter=routine_liturgy"},
        "ranked_by": None, "ranking_file": "ranked_heuristic.json",
        "count": len(items),
        "counts_by_kind": {"video": len(items)},
        "counts_by_source": dict(collections.Counter(i["source"] for i in items)),
        "orgs_with_items": len({i["org_id"] for i in items}),
        "counts_by_diocese": dict(collections.Counter(i["diocese"] for i in items)),
        "livestream_items": sum(1 for i in items if i["livestream"]), "live_now": sum(1 for i in items if i["is_live"]),
        "upcoming": sum(1 for i in items if i["is_upcoming"]),
        "pipeline_counts": dict(counts),
        "notes": [
            "Each item is a public YouTube video of a channel whose ownership was checked (channels.json: verified or probable).",
            "published_at is approximate: YouTube shows '3 days ago', so date_precision is 'approx'. Exact dates need YOUTUBE_API_KEY.",
            "is_live: streaming now; is_upcoming: a scheduled stream (published_at is the scheduled day); livestream: from the /streams tab.",
            "Titles passed ingest/public/media/media_privacy.py; the ones dropped are in quarantine.json with a redacted title and the reason.",
            "Not ranked here. ranked_heuristic.json holds the HEURISTIC candidate lists.",
        ],
        "items": items_sorted}
    (OUT / "feed.json").write_text(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
    (OUT / "quarantine.json").write_text(json.dumps({
        "generated_at": ts, "note": "Titles dropped by ingest/public/media/media_privacy.py (private people, funerals of lay people, Mass intentions...). "
                                    "Titles are redacted; links are opaque video ids.", "count": len(quarantine),
        "by_reason": dict(collections.Counter(q["reason"] for q in quarantine)), "items": quarantine}, indent=1, ensure_ascii=False) + "\n")
    print("wrote feed.json, ranked_heuristic.json, quarantine.json", flush=True)
    from media_report import write_channels_and_readme  # noqa: PLC0415
    write_channels_and_readme(items, quarantine, stat_by_channel, counts, ver, ranked, ts, yt)


if __name__ == "__main__":
    main()
