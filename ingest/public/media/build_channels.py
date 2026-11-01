"""Build data/real/media/channels.json (one record per public channel) from:
  _candidates.json (extract_links.py), _video_resolution.json + _stats.json (stats.py), manual.json (hand review),
  and livestream mentions in the cached parish HTML.

Run order:  fetch_sites.py -> extract_links.py -> stats.py -> build_channels.py
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, now_iso  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_links import owner_index  # noqa: E402

MEDIA = REAL / "media"
ROOT = REAL.parent.parent

SCOPE = {
    "diocese": "diocesan", "media": "diocesan", "diocesan_ministry": "diocesan", "chancery_office": "diocesan",
    "bishop": "diocesan", "parish": "parish", "mission": "parish", "school": "school", "charity": "charity",
    "svdp_council": "charity", "svdp_conference": "charity", "healthcare": "charity", "seminary": "formation",
    "higher_education": "formation", "religious_institute": "religious_institute", "religious_house": "religious_institute",
    "retreat_center": "formation", "lay_movement": "lay_movement", "community_partner": "partner", "context": "context",
    "ethnic_apostolate": "diocesan",
}

LIVE_TEXT_RX = re.compile(r"(live[\s-]?stream(?:ed|ing)?|streamed live|watch (?:mass )?live|transmisi[oó]n en vivo|"
                          r"misa en vivo|en vivo|facebook live|watch mass online)", re.I)


def url_for(platform: str, key: str) -> str:
    if key.startswith("http"):
        return key
    return "https://" + ("www." + key if platform in ("facebook", "instagram", "youtube", "linkedin") else key)


def handle_for(platform: str, key: str) -> str | None:
    last = key.rstrip("/").split("/")[-1]
    if platform in ("instagram", "x", "tiktok"):
        return "@" + last.lstrip("@")
    if platform == "flocknote":
        return key.split(".")[0]
    return last


def name_tokens(s: str) -> set[str]:
    s = html.unescape(s or "").lower()
    return {t for t in re.findall(r"[a-z]{3,}", s)} - {"the", "and", "church", "catholic", "parish", "saint",
                                                        "school", "of", "california"}


PRIVATE_EVENT_RX = re.compile(r"funeral|memorial|celebration of life|wedding|nuptial|baptism of|quincea|rosary for|in loving memory|vigil for", re.I)


def safe_title(t: str) -> str:
    """Video titles can name private people (funerals, weddings). Withhold those; CLAUDE.md bars private individuals' data."""
    from privacy import private_name_risk  # noqa: PLC0415
    t = html.unescape(t or "")
    if PRIVATE_EVENT_RX.search(t):
        return "[private liturgy - title withheld]"
    if re.search(r"\b(mrs?|ms)\.", t, re.I):
        return "[title withheld - may name a private person]"
    import media_privacy as mp  # noqa: PLC0415  (shared media rules: intentions, birthdays, alumni, parishioners)
    if any(rx.search(t) for rx in (mp.INTENTION, mp.WEDDING, mp.ALUMNI, mp.LAY_ROLE_NAME)) or \
            (mp.LAY_BIRTHDAY.search(t) and not mp.RELIGIOUS.search(t)):
        return "[title withheld - may name a private person]"  # also on "Mass ..." titles, which pass below
    if re.search(r"\b(mass|misa|liturgy|homily|homil[ií]a|rosary|adoration|stations|vespers|novena|lễ|主日|弥撒|"
                 r"livestream|live stream|sunday|daily)\b|主日|弥撒", t, re.I):
        return t
    if private_name_risk(t):
        return "[title withheld - may name a private person]"
    return t


def livestream_mentions() -> dict:
    """owner -> list of {page, snippet} where a parish page text mentions livestreaming."""
    out = defaultdict(list)
    ps = json.loads((REAL / "parish_sites.json").read_text())["records"]
    for r in ps:
        texts = [((r.get("mass_schedule") or {}).get("source_url") or r.get("website"),
                  (r.get("mass_schedule") or {}).get("raw") or "")]
        for page, txt in texts:
            for m in LIVE_TEXT_RX.finditer(re.sub(r"\s+", " ", txt)):
                snip = re.sub(r"\s+", " ", txt)[max(0, m.start() - 70): m.end() + 70].strip()
                if len(out[r["id"]]) < 3 and snip not in [x["snippet"] for x in out[r["id"]]]:
                    out[r["id"]].append({"page": page, "snippet": snip[:200]})
        for pg in r.get("pages") or []:
            f = Path(pg.get("raw_file") or "")
            f = f if f.is_absolute() else (ROOT / f if (ROOT / f).exists() else ROOT / "data" / f)
            if not f.exists():
                continue
            txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", f.read_text(errors="ignore"), flags=re.S | re.I)
            txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
            txt = re.sub(r"\s+", " ", txt)
            for m in LIVE_TEXT_RX.finditer(txt):
                snip = txt[max(0, m.start() - 70): m.end() + 70].strip()
                if len(out[r["id"]]) < 3 and snip not in [x["snippet"] for x in out[r["id"]]]:
                    out[r["id"]].append({"page": pg["url"], "snippet": snip[:200]})
    return out


def main():
    _, meta = owner_index()
    meta["per-bishop"] = {"name": "Bishop Michael C. Barber, SJ (office of the bishop)", "type": "bishop"}
    meta["ctx-sangabriel"] = {"name": "San Gabriel Media", "type": "context"}
    meta["christ-light-oakland"] = meta.get("christ-light-oakland") or {"name": "Cathedral of Christ the Light", "type": "parish"}
    cand = json.loads((RAW / "media" / "_candidates.json").read_text())
    vids = json.loads((RAW / "media" / "_video_resolution.json").read_text())
    stats = json.loads((RAW / "media" / "_stats.json").read_text())
    manual = json.loads((MEDIA / "manual.json").read_text())
    excl = {(e["key"], e.get("owner_id")) for e in manual["exclude"]}
    embed_ok = set(manual["embed_owner_ok"])

    # ---- gather (owner, platform, key, evidence)
    rows: dict[tuple, dict] = {}

    def add(owner, platform, key, ev, **extra):
        if (key, None) in excl or (key, owner) in excl:
            return
        owner = manual["reassign"].get(key, owner)
        r = rows.setdefault((platform, key), {"platform": platform, "key": key, "owners": [], "evidence": [], **extra})
        if owner not in r["owners"]:
            r["owners"].append(owner)
        r["evidence"].extend(ev)
        for k, v in extra.items():
            r.setdefault(k, v)

    embeds = defaultdict(list)  # owner -> third-party embedded channels
    for c in cand["candidates"]:
        ev = [{"type": "org_website_link", "found_on_owner": c["owner_id"], "url": u, "fetched_at": c["fetched_at"]}
              for u in c["evidence_pages"][:2]]
        if c["platform"] in ("youtube_video", "vimeo_video"):
            v = vids.get(c["key"], {})
            au = (v.get("author_url") or "").replace("https://www.", "").replace("https://", "")
            if not au:
                continue
            au_key = au.lower() if ("/@" in au or au.startswith("vimeo.com/")) else au
            plat = "youtube" if au.startswith("youtube") else "vimeo"
            if au_key in embed_ok:
                add(c["owner_id"], plat, au_key, [{"type": "embedded_video", "found_on_owner": c["owner_id"],
                                                    "url": c["evidence_pages"][0], "video": c["key"],
                                                    "video_title": v.get("title"), "fetched_at": c["fetched_at"]}])
            else:
                embeds[c["owner_id"]].append({"channel": au_key, "author_name": v.get("author_name"),
                                              "video": c["key"], "title": v.get("title")})
            continue
        add(c["owner_id"], c["platform"], c["key"], ev)
    for a in manual["add"]:
        extra = {k: a[k] for k in ("cadence", "audience", "handle", "url", "confidence", "notes") if k in a}
        add(a["owner_id"], a["platform"], a["key"], a["evidence"], **extra)

    # ---- merge YouTube duplicates (@handle, /c/, /channel/ that resolve to the same channel id)
    by_cid = {}
    for (plat, key), r in list(rows.items()):
        if plat != "youtube":
            continue
        cid = (stats.get(key) or {}).get("channel_id")
        if not cid:
            continue
        if cid in by_cid:
            keep = rows[by_cid[cid]]
            for o in r["owners"]:
                if o not in keep["owners"]:
                    keep["owners"].append(o)
            keep["evidence"].extend(r["evidence"])
            keep.setdefault("aliases", []).append(key)
            del rows[(plat, key)]
        else:
            by_cid[cid] = (plat, key)

    # ---- crawl signals without HTML here (residential re-crawl ran on Matthew's machine; raw HTML stays there)
    SIG = {"facebook": "facebook", "youtube_livestream": "youtube", "facebook_live": "facebook",
           "flocknote": "flocknote", "instagram": "instagram"}
    have = defaultdict(set)
    for (plat, key), r in rows.items():
        for o in r["owners"]:
            have[o].add(plat)
    for pr in json.loads((REAL / "parish_sites.json").read_text())["records"]:
        if pr.get("crawl_source") != "residential.json":
            continue
        for sig, pages in (pr.get("tech_signals") or {}).items():
            plat = SIG.get(sig)
            if not plat or plat in have[pr["id"]]:
                continue
            have[pr["id"]].add(plat)
            rows[(plat, f"signal:{pr['id']}:{plat}")] = {
                "platform": plat, "key": f"signal:{pr['id']}:{plat}", "owners": [pr["id"]], "url": None,
                "handle": None, "confidence": "medium", "signal_only": True,
                "notes": (f"the parish website links a {plat} account" + (" (livestream link)" if "live" in sig else "") +
                          "; detected by the residential re-crawl (2026-10-29); exact URL not captured in this "
                          "environment - rerun extract_links.py where data/raw/parish_sites/ holds that crawl's HTML"),
                "evidence": [{"type": "crawl_signal", "signal": sig, "url": u, "fetched_at": pr.get("crawled_at")}
                             for u in pages[:2]]}

    live_txt = livestream_mentions()
    out = []
    fetched = now_iso()
    for (plat, key), r in sorted(rows.items(), key=lambda kv: (kv[1]["owners"][0], kv[0])):
        st = stats.get(key) or {}
        for al in r.get("aliases", []):
            st = st or stats.get(al) or {}
        # primary owner = best name overlap with channel title/handle
        title = html.unescape(st.get("title") or "")
        owners = r["owners"]
        if len(owners) > 1:
            owners = sorted(owners, key=lambda o: -len(name_tokens(meta.get(o, {}).get("name", "")) &
                                                        (name_tokens(title) | name_tokens(key))))
        o = owners[0]
        om = meta.get(o, {"name": o, "type": None})
        notes = [x for x in [r.get("notes"), manual["notes"].get(key)] if x]
        if plat == "livestream_other" and "resi" in key:
            notes.append(manual["notes"]["resi.io"])
        act = st.get("activity") or {}
        rec = {
            "id": f"{o}:{plat}:{re.sub(r'[^a-z0-9]+', '-', key.lower()).strip('-')}"[:120],
            "owner_id": o, "owner_name": om["name"], "owner_type": om["type"],
            "scope": SCOPE.get(om["type"] or "", "other"),
            "also_linked_by": owners[1:],
            "platform": plat, "url": None if r.get("signal_only") else (r.get("url") or url_for(plat, key)), "handle": None if r.get("signal_only") else (r.get("handle") or st.get("handle") or handle_for(plat, key)),
            "signal_only": bool(r.get("signal_only")),
            "key": key, "aliases": r.get("aliases", []),
            "evidence": r["evidence"][:4],
            "confidence": r.get("confidence") or ("high" if any(e["type"] in ("org_website_link", "embedded_video")
                                                                  for e in r["evidence"]) else "medium"),
            "fetched_at": fetched,
            "stats": None, "stats_note": None, "notes": "; ".join(notes) or None,
        }
        for k in ("cadence", "audience"):
            if r.get(k):
                rec[k] = r[k]
        if plat == "youtube" and st.get("channel_id"):
            ls = st.get("livestreams") or {}
            rec["stats"] = {
                "channel_title": title or None, "channel_id": st["channel_id"],
                "subscribers": st.get("subscribers"), "videos": st.get("videos"),
                "last_post": act.get("last_post"), "days_since_last_post": act.get("days_since_last_post"),
                "posts_per_month_est": act.get("posts_per_month_est"), "activity_status": act.get("status"),
                "languages_in_titles": st.get("languages_in_titles"),
                "livestream": {"present": bool(ls.get("count_in_sample")), "streams_in_sample": ls.get("count_in_sample"),
                               "last_stream": (ls.get("last_stream") or "")[:10] or None,
                               "mass_titles_in_sample": ls.get("mass_titles_in_sample")},
                "recent_titles": [safe_title(x["title"]) for x in (st.get("recent_streams") or st.get("recent_videos") or [])[:5]],
                "source": f"https://www.youtube.com/channel/{st['channel_id']} (+ /videos, /streams tabs)",
                "fetched_at": st.get("fetched_at"),
            }
            rec["stats_note"] = "dates are approximate (YouTube shows relative times such as '3 days ago'); samples cap at ~15 items per tab"
        elif plat == "vimeo" and st.get("page_status") == 200:
            rec["stats"] = {"account_title": st.get("title"), "last_post": act.get("last_post"),
                            "days_since_last_post": act.get("days_since_last_post"),
                            "posts_per_month_est": act.get("posts_per_month_est"), "activity_status": act.get("status"),
                            "languages_in_titles": st.get("languages_in_titles"),
                            "recent_titles": [safe_title(x["title"]) for x in (st.get("recent") or [])[:5]],
                            "source": st.get("rss_url"), "fetched_at": st.get("fetched_at")}
        elif plat in ("facebook", "facebook_group", "instagram", "x", "tiktok", "linkedin"):
            rec["stats_note"] = ("not collected: follower counts and post dates on this platform need a login or "
                                 "automated collection its terms do not allow")
        out.append(rec)

    # ---- per-owner summary: livestream signals and third-party embeds
    owners = defaultdict(lambda: {"channels": 0, "platforms": set()})
    for r in out:
        for o in [r["owner_id"]] + r["also_linked_by"]:
            owners[o]["channels"] += 1
            owners[o]["platforms"].add(r["platform"])
    livemap = []
    for o in sorted(set(owners) | set(live_txt)):
        yt = [r for r in out if (r["owner_id"] == o or o in r["also_linked_by"]) and r["platform"] == "youtube"
              and r["stats"] and r["stats"]["livestream"]["present"]]
        other = [r for r in out if (r["owner_id"] == o or o in r["also_linked_by"]) and r["platform"] in ("livestream_other",)]
        if yt or other or live_txt.get(o):
            livemap.append({"owner_id": o, "owner_name": meta.get(o, {}).get("name"),
                            "owner_type": meta.get(o, {}).get("type"),
                            "youtube_live": [{"url": r["url"], "last_stream": r["stats"]["livestream"]["last_stream"],
                                              "mass_titles_in_sample": r["stats"]["livestream"]["mass_titles_in_sample"],
                                              "languages_in_titles": r["stats"]["languages_in_titles"]} for r in yt],
                            "other_players": [r["key"] for r in other],
                            "site_mentions": live_txt.get(o, [])})
    doc = {
        "generated_at": fetched,
        "generated_by": "ingest/public/media/build_channels.py",
        "count": len(out),
        "notes": [
            "One record per public channel. owner_id uses data/seed/entities.json ids (parishes, schools, dio-oakland, cceb) "
            "or data/real/ecosystem/orgs.json ids; ctx-* are context bodies (USCCB, California Catholic Conference).",
            "Channels are attributed to the org whose own website links them, unless manual.json reassigns them.",
            "Stats exist only for YouTube (public channel page and /videos and /streams tabs) and Vimeo (public RSS). "
            "Facebook, Instagram, X, TikTok and LinkedIn stats are not collected.",
            "Individuals' personal accounts are excluded on purpose.",
        ],
        "channels": out,
        "livestream_map": livemap,
        "third_party_embeds": {k: v[:6] for k, v in sorted(embeds.items())},
    }
    (MEDIA / "channels.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(len(out), "channels;", len({r["owner_id"] for r in out}), "primary owners;", len(livemap), "livestream rows")


if __name__ == "__main__":
    main()
