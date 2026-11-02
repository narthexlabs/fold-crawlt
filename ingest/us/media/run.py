"""National YouTube media catalog: every US diocese outside California (California: data/real/california/media/, built by
ingest/california/media_*.py; Oakland: data/real/media/). Reuses the California modules; nothing is rebuilt.

    python3 -I ingest/us/media/run.py collect   # offline: YouTube links in data/real/us/sites/** (+ gemini url_context)
    python3 -I ingest/us/media/run.py fetch     # network: each link -> its channel's public /about page (1 request / 2 s)
    python3 -I ingest/us/media/run.py verify    # offline: ownership check (verified / probable / unverified / borrowed)
    python3 -I ingest/us/media/run.py feed      # network: /videos + /streams tabs, privacy screen, ranking, per-diocese files
    python3 -I ingest/us/media/run.py all       # the four in order (what the GitHub runner job `media` runs)

The rules are the California ones (ingest/california/media_common.py): youtube.com only, one request every 2 s across the
process, robots.txt honoured (/feeds/videos.xml and /results are disallowed and never requested), the honest FindTheChurch
user agent, no API key; a consent or "confirm you're not a bot" page stops the run (data/raw/us/youtube/blocked.json) and
is never worked around. MEDIA_DEADLINE (epoch seconds) stops cleanly; the page cache makes the next run continue.

Ownership (media_resolve.match_pair / match_diocese, unchanged): verified = the channel's About links to the parish website,
its description names it, or its @handle is the site's name; or the parish name plus the city or a link from the parish's
own site. probable = the name matches with nothing else. Only verified and probable channels are read.
National differences: a parish is one crawled website (data/real/us/sites/<diocese>/<domain>.json; one site may serve
several parishes, so every name/city pair on the site is tried); the "another city" check uses cities of the same state;
a link found only on parishes of `unassigned-<st>` counts as compatible with the dioceses whose see is in that state.

Feed (per diocese, incremental): a diocese's file is written once all its channels' tabs are read, so a run that stops at
the deadline still commits the dioceses it finished. Videos: the last 25 uploads; streams: live, upcoming and the last
30 days (up to 15). Titles pass ingest/public/media/media_privacy.py (quarantined with a redacted title). Ranking:
ingest/public/media/rank.py HeuristicJudge, once per diocese, then once nationally when every diocese is done.
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("MEDIA_RAW", str(ROOT / "data" / "raw" / "us" / "youtube"))
os.environ.setdefault("MEDIA_OUT", str(ROOT / "data" / "real" / "us" / "media"))
sys.path.insert(0, str(ROOT / "ingest" / "california"))
import media_common as mc  # noqa: E402
import media_resolve as mr  # noqa: E402

RAW, OUT = mc.RAW, mc.OUT
US = ROOT / "data" / "real" / "us"
SITES = US / "sites"
GEMINI = US / "gemini" / "url_context"
CANDS, INDEX, RESOLVED, VERIFIED = RAW / "candidates.json", RAW / "parish_index.json", RAW / "resolved.json", RAW / "verified.json"
ITEMS = RAW / "items"
GEN = "ingest/us/media/run.py"

UPLOADS, STREAMS_MAX, STREAM_DAYS = 25, 15, 30
CANDIDATES_PER_DIOCESE, RECENT_PER_CHANNEL, NATIONAL_TOP = 100, 6, 200


def load_dioceses() -> dict[str, dict]:
    out = {}
    for d in json.loads((US / "dioceses.json").read_text())["dioceses"]:
        nm = d["name"]
        short = re.split(r"\bof\s+(?:the\s+)?", nm)[-1].strip() if " of " in nm else nm
        out[d["slug"]] = {**d, "short_name": short}
    return out


DIO = load_dioceses()
CA_SEES = {s for s, d in DIO.items() if d.get("see_state") == "CA"}


def skip_dir(name: str) -> bool:
    """California is catalogued by the California run; its copies here (ca-*, unassigned-ca, the CA sees) are skipped."""
    return name.startswith("ca-") or name == "unassigned-ca" or name in CA_SEES


def dir_state(name: str) -> str | None:
    if name.startswith("unassigned-"):
        return name.rsplit("-", 1)[-1].upper()
    return (DIO.get(name) or {}).get("see_state")


# ------------------------------------------------------------------------------------------------ collect
def collect() -> None:
    rows: dict[str, dict] = {}
    links: dict[str, list[dict]] = collections.defaultdict(list)
    files = 0
    for ddir in sorted(p for p in SITES.iterdir() if p.is_dir()):
        if skip_dir(ddir.name):
            continue
        for f in sorted(ddir.glob("*.json")):
            if f.name in ("summary.json", "progress.json"):
                continue
            try:
                d = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(d, dict) or not d.get("domain"):
                continue
            files += 1
            dom = d["domain"]
            prev = rows.get(dom)
            if prev and not prev["diocese"].startswith("unassigned-") and ddir.name.startswith("unassigned-"):
                continue  # keep the assigned copy of a site listed twice
            names = [n for n in (d.get("names") or []) if n] or [dom]
            cities = [c for c in (d.get("cities") or []) if c]
            rows[dom] = {"id": dom, "diocese": ddir.name, "state": dir_state(ddir.name), "name": names[0], "names": names[:6],
                         "city": cities[0] if cities else None, "cities": cities[:6], "website": d.get("home_url") or f"https://{dom}/",
                         "crawl_status": d.get("crawl_status"), "kind": None,
                         "sites": [{"domain": dom, "school": False, "diocesan_host": False}],
                         "ref": f"data/real/us/sites/{ddir.name}/{f.name}"}
            soc = d.get("social") or {}
            found = []
            for u in soc.get("youtube") or []:
                if isinstance(u, str):
                    found.append(u)
            for l in soc.get("livestream") or []:
                u = l.get("url") if isinstance(l, dict) else l
                if isinstance(u, str):
                    found.append(u)
            links[dom] = [x for x in (mc.parse_yt_url(u) for u in found) if x] + \
                [x for x in links.get(dom, []) if x.get("_src") == "gemini"]
    gem = 0
    if GEMINI.is_dir():
        for f in sorted(GEMINI.glob("*/*.json")):
            try:
                d = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            dom = f.stem
            if dom in rows:
                for x in mc.extract_yt(json.dumps(d, ensure_ascii=False)):
                    x["_src"] = "gemini"
                    links[dom].append(x)
                    gem += 1
    cands: dict[tuple, dict] = {}
    for dom, ls in links.items():
        r = rows.get(dom)
        if not r:
            continue
        for l in ls:
            k = (dom, l["type"], l["key"])
            c = cands.setdefault(k, {"owner_type": "parish", "owner_id": dom, "diocese": r["diocese"], "state": r["state"],
                                     "link_type": l["type"], "key": l["key"], "url": l["url"], "live_hint": False, "sources": [],
                                     "site_is_school": False, "cleanup": None})
            c["live_hint"] = c["live_hint"] or bool(l.get("live_hint"))
            src = {"source": "gemini_url_context" if l.get("_src") == "gemini" else "site_json", "ref": r["ref"], "domain": dom}
            if src not in c["sources"]:
                c["sources"].append(src)
    out = sorted(cands.values(), key=lambda c: (c["diocese"], c["owner_id"], c["link_type"], c["key"]))
    summary = {"built_at": mc.now_iso(), "site_files_read": files, "parishes_indexed": len(rows), "candidates": len(out),
               "unique_links": len({(c["link_type"], c["key"]) for c in out}),
               "parishes_with_a_link": len({c["owner_id"] for c in out}), "gemini_links": gem,
               "by_link_type": dict(collections.Counter(c["link_type"] for c in out)),
               "skipped": "California (ca-*, unassigned-ca and the 12 California sees): see data/real/california/media/"}
    CANDS.write_text(json.dumps({"summary": summary, "candidates": out}, ensure_ascii=False) + "\n")
    INDEX.write_text(json.dumps({"built_at": summary["built_at"], "parishes": list(rows.values())}, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


# ------------------------------------------------------------------------------------------------ fetch
def fetch() -> None:
    mr.CANDS, mr.RESOLVED = CANDS, RESOLVED
    mr.fetch()


# ------------------------------------------------------------------------------------------------ verify
def _ft(title: str) -> str:
    return " " + mc.fold(re.sub(r"[^A-Za-zÀ-ɏ ]+", " ", title or "")) + " "


def verify() -> None:
    mr.DIOCESES = DIO           # match_diocese reads website / short_name / see_city from here
    cands = json.loads(CANDS.read_text())["candidates"]
    idx = json.loads(INDEX.read_text())
    res = json.loads(RESOLVED.read_text())
    P = mr.Parishes(idx)
    city_by_state: dict[str, set] = collections.defaultdict(set)
    for p in idx["parishes"]:
        for c in [p.get("city")] + (p.get("cities") or []):
            if c and len(mc.fold(c).strip()) >= 5:
                city_by_state[p.get("state") or "?"].add(mc.fold(c).strip())
    P.cities = set()            # pre() then skips the national city scan; the state's cities are added per parish

    def variants(p: dict) -> list[dict]:
        names, cities = p.get("names") or [p["name"]], p.get("cities") or [p.get("city")]
        out = []
        for i, nm in enumerate(names[:6]):
            cy = cities[i] if len(cities) == len(names) else (cities[0] if cities else None)
            v = dict(p, name=nm, city=cy)
            v["_toks"] = mc.toks(re.sub(r"\(.*?\)", " ", nm or ""))
            v["_forms"] = mr.name_forms(nm)
            v["_city"] = mc.squash(cy)
            out.append(v)
        return out or [p]

    by_ch: dict[str, list[dict]] = collections.defaultdict(list)
    unresolved = []
    for c in cands:
        k = res["keys"].get(f"{c['link_type']}:{c['key']}") or {}
        cid = k.get("channel_id")
        if c["link_type"] == "channel_id" and not cid:
            cid = c["key"] if c["key"] in res["channels"] else None
        if cid and cid in res["channels"]:
            by_ch[cid].append(c)
        else:
            unresolved.append({"owner_id": c["owner_id"], "diocese": c["diocese"], "link": c["url"], "type": c["link_type"],
                               "resolution": {"status": k.get("status"), "error": k.get("error"), "fetched": bool(k)}})

    channels, unverified, borrowed, diocese_rows = [], [], [], []
    stats = collections.Counter()
    for cid, links in sorted(by_ch.items()):
        ch = res["channels"][cid]
        f = mr.pre(ch, P)
        ft = _ft(ch.get("title") or "")
        link_dioceses = sorted({l["diocese"] for l in links if l["diocese"]})
        linking = sorted({l["owner_id"] for l in links})
        base = {"channel_id": cid, "title": ch.get("title"), "handle": ch.get("handle"),
                "url": f"https://www.youtube.com/{ch['handle']}" if ch.get("handle") else f"https://www.youtube.com/channel/{cid}",
                "linked_as": sorted({l["url"] for l in links})[:6], "linked_by": linking[:20], "dioceses_linking": link_dioceses,
                "evidence": [{"type": "org_website_link" if s["source"] == "site_json" else s["source"], "found_on_owner": l["owner_id"],
                              "url": s.get("ref"), "link": l["url"]} for l in links for s in l["sources"][:1]][:6],
                "fetched_at": ch.get("fetched_at")}
        if any(mc.is_template_handle(l["key"]) for l in links) or mc.is_template_handle(ch.get("handle") or "x"):
            borrowed.append({**base, "status": "borrowed", "reason": "site-builder or platform default account"})
            stats["borrowed_template"] += 1
            continue
        # ---- a diocese's own channel?
        dio_hits = {s: m for s in DIO if not skip_dir(s) and (m := mr.match_diocese(ch, s))}
        if dio_hits:
            # "<Place> Catholic" alone (no diocese word, no link to the diocesan site) yields to a parish whose full name is in the title
            weak = {s for s, m in dio_hits.items() if set(m["checks"]) == {"name"} and "diocese's name for itself" in m["checks"]["name"]}
            if weak:
                pool_names = [v for pid in linking if pid in P.by_id for v in variants(P.by_id[pid])]
                if any(v["_toks"] and set(v["_toks"]) <= f["tt"] for v in pool_names):
                    for s in weak:
                        dio_hits.pop(s)
        if len(dio_hits) == 1:
            slug, m = next(iter(dio_hits.items()))
            st = DIO[slug].get("see_state")
            compatible = (not link_dioceses or slug in link_dioceses or
                          any(ld.startswith("unassigned-") and ld.rsplit("-", 1)[-1].upper() == st for ld in link_dioceses))
            if compatible:
                diocese_rows.append({**base, "diocese": slug, "diocese_match": m, "status": m["verdict"]})
                stats[f"diocese_{m['verdict']}"] += 1
            else:
                borrowed.append({**base, "status": "borrowed", "reason": f"another diocese's channel ({DIO[slug]['name']})",
                                 "diocese_of_channel": slug})
                stats["borrowed_other_diocese"] += 1
            continue
        if mr.NATIONAL.search(f["title"]) or mr.NATIONAL.search(f["handle"]):
            borrowed.append({**base, "status": "borrowed", "reason": f"national, Vatican or publisher channel ('{ch.get('title')}')"})
            stats["borrowed_national"] += 1
            continue
        if mr.PERSON_TITLE.match(f["title"].strip()):
            borrowed.append({**base, "status": "borrowed", "reason": f"a person's own channel ('{ch.get('title')}'): personal accounts are excluded"})
            stats["borrowed_person"] += 1
            continue
        # ---- which parish? the linking sites plus same-diocese sites whose name fits the title
        pool = {pid: P.by_id[pid] for pid in linking if pid in P.by_id}
        for dio in link_dioceses:
            for p in P.by_dio.get(dio, []):
                if p["id"] in pool:
                    continue
                for v in variants(p):
                    if ((set(v["_toks"]) - {"saint"}) & f["tt"] and set(v["_toks"]) <= f["tt"]) or any(x in f["ts"] or x in f["ht"] for x in v["_forms"]) \
                            or (f["about_dom"] & p["_domains"]):
                        pool[p["id"]] = p
                        break
        results = []
        for pid, p in pool.items():
            fs = dict(f)
            fs["cities_in_title"] = {c for c in city_by_state.get(p.get("state") or "?", ()) if c in ft and f" {c} " in ft}
            best = None
            for v in variants(p):
                longer = None
                for q in P.by_dio[p["diocese"]]:
                    if q["id"] != p["id"] and len(q["_toks"]) > len(v["_toks"]) and set(v["_toks"]) < set(q["_toks"]) and set(q["_toks"]) <= f["tt"]:
                        longer = q["name"]
                        break
                r = mr.match_pair(fs, v, links, P, longer)
                rank_v = {"verified": 2, "probable": 1}.get(r["verdict"], 0)
                if best is None or (rank_v, r["score"]) > best[0]:
                    best = ((rank_v, r["score"]), r, v)
            results.append(best[1] | {"_name": best[2]["name"], "_city": best[2].get("city")})
        results.sort(key=lambda r: -r["score"])
        good = [r for r in results if r["verdict"] in ("verified", "probable")]
        if good:
            top = good[0]
            tied = [r for r in good if r["score"] == top["score"] and r["parish_id"] != top["parish_id"]]
            if tied and top["parish_id"] not in linking:
                unverified.append({**base, "status": "unverified", "reason": f"the name fits {len(tied) + 1} parishes equally ({top['_name']} ...)",
                                   "candidates": [r["parish_id"] for r in [top] + tied][:6]})
                stats["unverified_ambiguous"] += 1
                continue
            p = P.by_id[top["parish_id"]]
            is_school = bool(mr.SCHOOL_WORD.search(f["title"])) and not mr.SCHOOL_WORD.search(top["_name"] or "")
            channels.append({**base, "status": top["verdict"], "reasons": top["reasons"], "checks": top["checks"],
                             "owner_id": p["id"], "owner_name": top["_name"], "owner_names": p.get("names"), "city": top["_city"],
                             "state": p.get("state"), "diocese": p["diocese"], "website": p.get("website"),
                             "owner_type": "school" if is_school else "parish", "also_linked_by": [x for x in linking if x != p["id"]][:20]})
            stats[f"parish_{top['verdict']}"] += 1
            for other in linking:
                if other != p["id"] and not any(r["parish_id"] == other and r["verdict"] in ("verified", "probable") for r in results):
                    borrowed.append({**base, "status": "borrowed", "reason": f"belongs to {top['_name']} ({top['_city']}), not to the parish that links it",
                                     "linked_by": [other], "belongs_to": p["id"]})
                    stats["borrowed_other_parish"] += 1
            continue
        top = results[0] if results else None
        unverified.append({**base, "status": "unverified",
                           "reason": "; ".join(top["reasons"]) if top and top["reasons"] else
                           f"the channel name '{ch.get('title')}' does not match the parish name, city or website",
                           "diocese": link_dioceses[0] if link_dioceses else None})
        stats["unverified"] += 1
    out = {"built_at": mc.now_iso(), "counts": dict(stats), "channels": channels, "diocese_channels": diocese_rows,
           "unverified": unverified, "borrowed": borrowed, "unresolved": unresolved}
    VERIFIED.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(out["counts"], indent=1))
    print("channels:", len(channels), "diocese rows:", len(diocese_rows), "unverified:", len(unverified), "borrowed:", len(borrowed),
          "unresolved:", len(unresolved), flush=True)


# ------------------------------------------------------------------------------------------------ feed
def _import_feed_modules():
    pub = ROOT / "ingest" / "public"
    for p in (str(pub), str(pub / "media")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import feed_build as fb  # noqa: PLC0415
    import media_privacy  # noqa: PLC0415
    import media_feed  # noqa: PLC0415  (run_rank, slim)
    return fb, media_privacy, media_feed


def read_channel(yt, fb, media_privacy, c: dict, owner: tuple, counts: collections.Counter) -> dict | None:
    """Both tabs of one channel -> {'items', 'quarantine', 'stats'}; None when the run stopped before reading it."""
    owner_id, owner_name, org_type, slug, parish_id = owner
    cid = c["channel_id"]
    rv = yt.get(f"https://www.youtube.com/channel/{cid}/videos")
    rs = yt.get(f"https://www.youtube.com/channel/{cid}/streams")
    if yt.stopped and yt.stopped in (rv.get("error"), rs.get("error")):
        return None
    items, quarantine = [], []
    vids = []
    for r, tab in ((rv, "videos"), (rs, "streams")):
        try:
            mc.NOW = datetime.fromisoformat(r["fetched_at"]) if r.get("fetched_at") else datetime.now(timezone.utc)
        except ValueError:
            mc.NOW = datetime.now(timezone.utc)
        got = mc.parse_tab(r.get("data"), tab)
        vids += got[:UPLOADS] if tab == "videos" else got
    now = datetime.now(timezone.utc)
    s_items = [v for v in vids if v["tab"] == "streams" or v.get("live_now")]
    seen, kept_ups, kept_streams, titles_safe, dates = set(), 0, 0, [], []
    for v in sorted(vids, key=lambda v: (v["when"] is None, -(v["when"].timestamp()) if v["when"] else 0)):
        if v["vid"] in seen:
            continue
        seen.add(v["vid"])
        is_stream = v["tab"] == "streams" or v["live_now"] or v["upcoming"]
        age_d = (now - v["when"]).days if v["when"] else None
        if is_stream and not (v["live_now"] or v["upcoming"]):
            if age_d is None or age_d >= STREAM_DAYS or kept_streams >= STREAMS_MAX:
                counts["stream_older_than_30_days_or_over_cap"] += 1
                continue
        elif not is_stream and kept_ups >= UPLOADS:
            continue
        title = v["title"]
        keep, why = media_privacy.screen(title, "video", org_type if org_type in ("school", "parish") else "other")
        iid = fb.item_id("yt", v["vid"])
        if not keep:
            counts[f"privacy_{why}"] += 1
            quarantine.append({"id": iid, "org_id": owner_id, "kind": "video", "published_at": fb.iso(v["when"]), "reason": why,
                               "title_redacted": media_privacy.redacted(title), "url": f"https://www.youtube.com/watch?v={v['vid']}"})
            continue
        routine = fb.is_routine(title) or (is_stream and not fb.SPECIAL.search(title))
        kept_streams += 1 if is_stream else 0
        kept_ups += 0 if is_stream else 1
        if v["when"] and not v["upcoming"]:
            dates.append(fb.iso(v["when"]))
        if len(titles_safe) < 5:
            titles_safe.append(title)
        items.append({
            "id": iid, "org_id": owner_id, "org_name": owner_name, "org_type": org_type, "deanery_id": None, "diocese": slug,
            "parish_id": parish_id, "channel_id": cid, "channel_title": c["title"], "kind": "video", "title": title, "summary": None,
            "url": f"https://www.youtube.com/watch?v={v['vid']}", "embed_url": f"https://www.youtube-nocookie.com/embed/{v['vid']}",
            "thumbnail_url": f"https://i.ytimg.com/vi/{v['vid']}/hqdefault.jpg", "published_at": fb.iso(v["when"]),
            "date_precision": "approx", "language": fb.lang_guess(title),
            "source": "YouTube: public channel /videos and /streams tabs", "source_url": c["url"],
            "fetched_at": rv.get("fetched_at") or mc.now_iso(), "duration_s": v["duration_s"], "views": v["views"],
            "livestream": bool(is_stream), "is_live": bool(v["live_now"]), "is_upcoming": bool(v["upcoming"]),
            "prefilter": "routine_liturgy" if routine else None, "tags": []})
        counts["routine_liturgy_flagged"] += 1 if routine else 0
    acts = fb.stats.activity(dates)
    last_stream = max((fb.iso(v["when"]) for v in s_items if v["when"] and not v["upcoming"]), default=None)
    ls30 = [v for v in s_items if v["when"] and not v["upcoming"] and (now - v["when"]).days < STREAM_DAYS]
    stats = {"last_post": acts.get("last_post"), "days_since_last_post": acts.get("days_since_last_post"),
             "posts_per_month_est": acts.get("posts_per_month_est"), "activity_status": acts.get("status"),
             "languages_in_titles": fb.stats.langs_of(titles_safe) if titles_safe else [],
             "livestream": {"present": bool(s_items), "streams_in_sample": len(s_items), "last_stream": (last_stream or "")[:10] or None,
                            "streams_last_30d": len(ls30), "live_now": sum(1 for v in s_items if v["live_now"]),
                            "upcoming": sum(1 for v in s_items if v["upcoming"])},
             "videos_in_feed": kept_ups, "streams_in_feed": kept_streams, "tab_status": {"videos": rv.get("status"), "streams": rs.get("status")},
             "tabs_fetched_at": rv.get("fetched_at")}
    counts["items_seen_in_tabs"] += len(vids)
    return {"items": items, "quarantine": quarantine, "stats": stats}


def _slim_item(it: dict) -> dict:
    return {k: it.get(k) for k in ("id", "title", "url", "thumbnail_url", "published_at", "duration_s", "livestream", "is_live",
                                   "is_upcoming", "prefilter", "language")}


def feed() -> None:
    fb, media_privacy, media_feed = _import_feed_modules()
    ver = json.loads(VERIFIED.read_text())
    chans = [c for c in ver["channels"] if c["status"] in ("verified", "probable")]
    dioch = [c for c in ver["diocese_channels"] if c["status"] in ("verified", "probable")]
    by_dio: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)
    for c in dioch:
        by_dio[c["diocese"]].append(("diocese", c))
    for c in chans:
        by_dio[c["diocese"]].append(("parish", c))
    ITEMS.mkdir(parents=True, exist_ok=True)
    (OUT / "dioceses").mkdir(parents=True, exist_ok=True)
    yt = mc.YouTube()
    order = sorted(by_dio, key=lambda s: (s.startswith("unassigned-"), s))
    done = 0
    for slug in order:
        counts = collections.Counter()
        items, quarantine, ch_out = [], [], []
        stopped = False
        for kind, c in by_dio[slug]:
            if kind == "diocese":
                owner = (slug, DIO[slug]["name"], "diocese", slug, None)
            else:
                owner = (c["owner_id"], c["owner_name"], c["owner_type"], slug, c["owner_id"])
            got = read_channel(yt, fb, media_privacy, c, owner, counts)
            if got is None:
                stopped = True
                break
            items += got["items"]
            quarantine += got["quarantine"]
            recent = sorted((i for i in got["items"] if i["published_at"]), key=lambda i: i["published_at"], reverse=True)[:RECENT_PER_CHANNEL]
            ch_out.append({"channel_id": c["channel_id"], "title": c["title"], "handle": c.get("handle"), "url": c["url"],
                           "status": c["status"], "owner_type": owner[2], "owner_id": owner[0], "owner_name": owner[1],
                           "city": c.get("city"), "website": c.get("website"),
                           "reasons": c.get("reasons") or (c.get("diocese_match") or {}).get("reasons"),
                           **got["stats"], "recent": [_slim_item(i) for i in recent]})
        if stopped:
            print(f"stopped ({yt.stopped}) in {slug}; {done} dioceses written this run", flush=True)
            break
        rk = {}
        if items:
            rk, _rb, _rc = media_feed.run_rank(items)
        on = sorted((i for i in items if not rk[i["id"]]["excluded_reason"] and rk[i["id"]]["rank"]), key=lambda i: rk[i["id"]]["rank"])
        (ITEMS / f"{slug}.json").write_text(json.dumps({"items": items}, ensure_ascii=False) + "\n")
        doc = {"generated_at": mc.now_iso(), "generated_by": GEN, "diocese": slug, "name": (DIO.get(slug) or {}).get("name") or slug,
               "label": "HEURISTIC RANKING - candidates for a person to review; judged by keyword and metadata rules on titles "
                        "(nobody watched the videos). Dates are approximate ('3 weeks ago').",
               "counts": {"channels": len(ch_out), "videos_kept": len(items), "on_feed": len(on), "quarantined": len(quarantine),
                          "routine_liturgy_flagged": counts.get("routine_liturgy_flagged", 0),
                          "excluded_by_reason": dict(collections.Counter((rk[i["id"]]["excluded_reason"] or "on feed").split(":")[0] for i in items)),
                          "by_content_type": dict(collections.Counter(rk[i["id"]]["content_type"] for i in items)),
                          "pipeline": dict(counts)},
               "candidates": [media_feed.slim(i, rk[i["id"]]) for i in on[:CANDIDATES_PER_DIOCESE]],
               "channels": ch_out,
               "quarantine": [{k: q[k] for k in ("id", "org_id", "published_at", "reason", "title_redacted")} for q in quarantine]}
        (OUT / "dioceses" / f"{slug}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
        done += 1
        print(f"  {slug}: {len(ch_out)} channels, {len(items)} videos, {len(on)} on its list, {len(quarantine)} quarantined; "
              f"requests {yt.requests_made}, cache hits {yt.cache_hits}", flush=True)
    complete = not yt.stopped
    summarize(ver, by_dio, complete, yt, media_feed)


def summarize(ver: dict, by_dio: dict, complete: bool, yt, media_feed) -> None:
    idx = json.loads(INDEX.read_text())
    cands = json.loads(CANDS.read_text())
    per = collections.defaultdict(collections.Counter)
    for p in idx["parishes"]:
        per[p["diocese"]]["parishes_indexed"] += 1
    linked = collections.defaultdict(set)
    for c in cands["candidates"]:
        linked[c["diocese"]].add(c["owner_id"])
    for d, s in linked.items():
        per[d]["parishes_with_a_youtube_link"] = len(s)
    own = collections.defaultdict(lambda: collections.defaultdict(set))
    for c in ver["channels"]:
        if c["status"] in ("verified", "probable"):
            own[c["diocese"]][c["status"]].add(c["owner_id"])
            per[c["diocese"]]["channels_catalogued"] += 1
    for d, m in own.items():
        per[d]["parishes_with_verified_channel"] = len(m.get("verified", set()))
        per[d]["parishes_with_probable_only"] = len(m.get("probable", set()) - m.get("verified", set()))
    for c in ver["diocese_channels"]:
        if c["status"] in ("verified", "probable"):
            per[c["diocese"]]["diocese_channels"] += 1
    written = 0
    for f in sorted((OUT / "dioceses").glob("*.json")):
        d = json.loads(f.read_text())
        written += 1
        s = d["diocese"]
        per[s]["file_written"] = 1
        per[s]["videos_kept"] = d["counts"]["videos_kept"]
        per[s]["on_feed_candidates"] = d["counts"]["on_feed"]
        per[s]["quarantined"] = d["counts"]["quarantined"]
        per[s]["channels_active_45d"] = sum(1 for c in d["channels"] if c.get("activity_status") == "active")
        per[s]["channels_streamed_last_30d"] = sum(1 for c in d["channels"] if (c.get("livestream") or {}).get("streams_last_30d"))
    cov = {"generated_at": mc.now_iso(), "generated_by": GEN, "complete": complete,
           "note": "Per diocese (USCCB slug, or unassigned-<st> for parish sites not yet tied to a diocese). parishes_indexed = crawled "
                   "parish websites outside California; a parish counts once however many channels it has.",
           "collect": cands["summary"], "verify_counts": ver["counts"],
           "totals": dict(sum(per.values(), collections.Counter())),
           "dioceses_with_channels": len(by_dio), "diocese_files_written": written,
           "youtube_requests_this_run": {"made": yt.requests_made, "cache_hits": yt.cache_hits, "errors": yt.errors, "stopped": yt.stopped},
           "dioceses": {k: dict(v) for k, v in sorted(per.items())}}
    (OUT / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1) + "\n")
    audit = {"generated_at": mc.now_iso(), "note": "Channels linked from parish sites that were not catalogued, and why.",
             "unverified": [{k: c.get(k) for k in ("channel_id", "title", "url", "reason", "linked_by", "dioceses_linking", "candidates") if c.get(k)}
                            for c in ver["unverified"]],
             "borrowed": [{k: c.get(k) for k in ("channel_id", "title", "url", "reason", "linked_by", "belongs_to", "diocese_of_channel") if c.get(k)}
                          for c in ver["borrowed"]],
             "unresolved": ver["unresolved"]}
    (OUT / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, separators=(",", ":")) + "\n")
    if not complete:
        print("national ranking waits until every diocese is read", flush=True)
        return
    items = []
    for f in sorted(ITEMS.glob("*.json")):
        items += json.loads(f.read_text())["items"]
    if not items:
        return
    rk, ranked_by, rank_counts = media_feed.run_rank(items)
    on = sorted((i for i in items if not rk[i["id"]]["excluded_reason"] and rk[i["id"]]["rank"]), key=lambda i: rk[i["id"]]["rank"])
    nat = {"generated_at": mc.now_iso(), "generated_by": GEN + " (ingest/public/media/rank.py, HeuristicJudge)",
           "label": "HEURISTIC RANKING - NOT A HUMAN OR LLM JUDGMENT. Candidates only: a person reviews them.",
           "scope": "every US diocese outside California (California: data/real/california/media/; Oakland: data/real/media/)",
           "counts": {"items_considered": len(items), "on_feed_nationally": len(on), "rank_counts": rank_counts},
           "national_top": [media_feed.slim(i, rk[i["id"]]) for i in on[:NATIONAL_TOP]]}
    (OUT / "national.json").write_text(json.dumps(nat, ensure_ascii=False, indent=1) + "\n")
    print(f"national: {len(items)} items, {len(on)} on the national list", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("collect", "all"):
        collect()
    if cmd in ("fetch", "all"):
        fetch()
        if (RAW / "blocked.json").exists():
            sys.exit(0)
        if mc.DEADLINE and datetime.now(timezone.utc).timestamp() > mc.DEADLINE:
            print("deadline reached during fetch; the next run continues", flush=True)
            sys.exit(0)
    if cmd in ("verify", "all"):
        verify()
    if cmd in ("feed", "all"):
        feed()
