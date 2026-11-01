"""Build the diocesan media feed: data/real/media/feed.json (candidates for the /media page).

Sources (all public, no login; Facebook and Instagram are never fetched, see curated_embeds.json):
  * YouTube channels in channels.json: the public /videos tab (and /streams, only for special liturgies), read from
    the same polite cache as stats.py. YouTube's robots.txt disallows /feeds/videos.xml, so the RSS feed is not used;
    if YOUTUBE_API_KEY is set, the YouTube Data API v3 (search.list is avoided; playlistItems on the uploads list)
    is used instead and gives exact publish dates.
  * Vimeo accounts: the public /<user>/videos/rss feed.
  * Podcasts: the libsyn RSS feeds listed in channels.json.
  * WordPress REST API (/wp-json/wp/v2/posts): oakdiocese.org and the parish sites whose CMS is WordPress.
  * Parish calendar events already collected (data/real/calendars/events.json), diocesan calendar events
    (data/real/diocese_events.json) and bulletin event items (data/real/enrichment/parish_profiles.json).
  * Hand-curated embeds (data/real/media/curated_embeds.json): official oEmbed/embed snippets only.

This script only GATHERS candidates. Ranking, the per-organization cap, demotion of routine liturgy and the
auto status (approved / featured / candidate) are done by ingest/public/media/rank.py (docs/design/media-ranking.md).
Routine Mass livestreams of the last ROUTINE_KEEP_DAYS days are kept and flagged `prefilter: routine_liturgy` (rank.py
demotes them; the review page shows them). Titles naming a PRIVATE person (media_privacy.py) are quarantined:
data/real/media/quarantine.json records the item with a redacted title and the reason. Summaries are redacted.
Rerunnable; then run rank.py:

    python3 ingest/public/media/feed_build.py            # uses cached pages up to 72 h old
    python3 ingest/public/media/feed_build.py --offline  # cache only, no network
"""
from __future__ import annotations

import collections
import hashlib
import html
import importlib.util
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import RAW, REAL, ROOT, get, now_iso  # noqa: E402
sys.path.insert(0, str(HERE))
import media_privacy  # noqa: E402  (ingest/public/media/media_privacy.py)
pub_privacy = media_privacy.pub_privacy  # ingest/public/privacy.py (the shared screen)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


cal_privacy = _load("cal_privacy", ROOT / "ingest" / "connectors" / "calendars" / "privacy.py")
stats = _load("media_stats", HERE / "stats.py")

MEDIA = REAL / "media"
CACHE = RAW / "media" / "_stats_cache"
OFFLINE = "--offline" in sys.argv
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
PAST_DAYS = 120        # how far back news items reach
VIDEO_PAST_DAYS = 365  # videos and podcast episodes: teaching content is timeless, rank.py decays by freshness
ROUTINE_KEEP_DAYS = 21 # routine Mass streams older than this are counted, not kept
AHEAD_DAYS = 45        # how far ahead events reach
FETCHED = now_iso()


def cached(url: str, max_age_h: float = 72) -> tuple[int | None, str]:
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")
    if f.exists() and (OFFLINE or (time.time() - f.stat().st_mtime) < max_age_h * 3600):
        st, _, body = f.read_text(errors="ignore").partition("\n")
        return (int(st) if st.isdigit() else None), body
    if OFFLINE:
        return None, ""
    r = get(url, timeout=20)
    time.sleep(1.2)
    st = r.status_code if r is not None else None
    body = r.text if r is not None else ""
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(f"{st}\n{body}")
    return st, body


# ------------------------------------------------------------------------------------------- organizations
def load_orgs() -> dict[str, dict]:
    orgs: dict[str, dict] = {}
    ents = json.loads((ROOT / "data" / "seed" / "entities.json").read_text())
    ents = ents if isinstance(ents, list) else ents.get("entities", [])
    for e in ents:
        a = e.get("attrs") or {}
        orgs[e["id"]] = {"name": e["name"], "type": e["type"], "entity": True, "city": e.get("city"),
                         "deanery_id": a.get("deanery_id") or (e.get("parent_id") if str(e.get("parent_id", "")).startswith("dn-") else None)}
    for o in json.loads((REAL / "ecosystem" / "orgs.json").read_text())["records"]:
        orgs.setdefault(o["id"], {"name": o["name"], "type": o["type"], "entity": False, "city": o.get("city"), "deanery_id": None})
    return orgs


# ------------------------------------------------------------------------------------------- text rules
SPECIAL = re.compile(
    r"\b(ordination|ordained|ordains|confirmation|confirmaci[oó]n|feast|fiesta|solemnity|jubilee|jubileo|anniversary|"
    r"aniversario|dedication|installation|chrism|easter vigil|christmas|navidad|simbang|guadalupe|all saints|all souls|"
    r"d[ií]a de (los )?muertos|first communion|primera comuni[oó]n|graduation|pilgrimage|peregrinaci[oó]n|procession|"
    r"procesi[oó]n|rite of election|blessing of|white mass|red mass|blue mass|chautauqua|religious jubilarians|vows|"
    r"posadas|las posadas|corpus christi procession|eucharistic (congress|revival|procession))\b", re.I)
ROUTINE = re.compile(
    r"\b(mass|misa|messe|missa|th[aá]nh l[eễ]|sunday|domingo|live ?stream(ed|ing)?|livestream|en vivo|en directo|"
    r"daily|diaria|weekday|rosary live|holy hour live)\b|"
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+\d{1,2}(:\d{2})?\s*(am|pm)\b|"
    r"^\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|[A-Z][a-z]+ \d{1,2},? \d{4})\s*$", re.I)
LECTIONARY = re.compile(r"\blectionary:?\s*\d+|\bweek in ordinary time\b|\bweek of (advent|lent|easter)\b", re.I)
GENERIC = re.compile(r"\b(homily|homil[ií]a|reflection|reflexi[oó]n|gospel|evangelio|readings?|lecturas?|weekly|"
                     r"announcements?|anuncios|bulletin)\b", re.I)
GOOD = re.compile(r"\b(school|escuela|youth|j[oó]venes|young adults?|vocation|seminar|retreat|retiro|charity|caridad|"
                  r"food|pantry|meals?|shelter|homeless|immigra|refugee|service|volunteer|voluntari|mission|misi[oó]n|"
                  r"family|familia|marriage|matrimonio|ocia|rcia|catechesis|catequesis|formation|formaci[oó]n|bible|"
                  r"music|choir|coro|concert|festival|appeal|campaign|celebrat|welcome|bienvenid|history|historia)\b", re.I)
PRIVATE_EVENTS = re.compile(r"\b(funeral|memorial|obituar|rest in peace|\brip\b|wedding|boda|quincea|baptism of|"
                            r"bautizo de|celebration of life|in loving memory|requiem for)\b", re.I)


DROPS: list[tuple[str, str]] = []  # (reason, title) for the local audit file only (data/raw is gitignored)
QUARANTINE: list[dict] = []        # privacy drops with a REDACTED title, committed as data/real/media/quarantine.json


def quarantine(reason: str, title: str, ctx: dict | None) -> None:
    ctx = ctx or {}
    if str(ctx.get("org_id") or "").startswith(("ctx-", "ptn-")) or ctx.get("org_type") in ("context", "community_partner"):
        return  # not a Church body: left out of the feed anyway, so nothing to audit
    url = ctx.get("url") or ""
    qid = ctx.get("id") or item_id("q", ctx.get("org_id") or "", title or "")
    QUARANTINE.append({
        "id": qid, "org_id": ctx.get("org_id"), "kind": ctx.get("kind"), "source": ctx.get("source"),
        "published_at": ctx.get("published_at"), "reason": reason,
        "title_redacted": media_privacy.redacted(title or "") if title else None,
        # a YouTube/Vimeo link is an opaque id; other links can carry the name in their slug, so only the host is kept
        "url": url if re.search(r"youtube\.com/watch|vimeo\.com/\d", url) else (re.match(r"https?://[^/]+", url).group(0) if url.startswith("http") else None),
    })


def title_ok(title: str, cats: list[str] | None = None, ctx: dict | None = None) -> tuple[bool, str | None, str | None]:
    """(keep, reason, clean title). Calendar-style items (events, bulletins) also pass the calendar screen; every
    item passes the media screen (media_privacy.screen). A drop is quarantined with a redacted title."""
    kind = (ctx or {}).get("kind", "video")
    clean = (title or "").strip()
    if kind in ("event", "bulletin_item"):
        keep, reason, clean2, _ = cal_privacy.screen_event(title, None, cats or [], None, None)
        if not keep or not clean2:
            DROPS.append((reason or "screen", title or ""))
            quarantine(f"calendar_screen:{reason or 'screen'}", title, ctx)
            return False, reason or "screen", None
        clean = clean2
    keep, why = media_privacy.screen(clean, kind, (ctx or {}).get("org_type"))
    if not keep:
        DROPS.append((why or "", title or ""))
        quarantine(why or "screen", title, ctx)
        return False, why, None
    return True, None, clean


def slug_names_person(it: dict) -> bool:
    """A post about a private person ('Thank you to <name> ... Eagle Scout project') can carry the name in its link or
    image file name ('eagle-scout-evan', 'Project-Evan-Trang-2.jpg') even after the summary is redacted. When the
    summary needed redaction, any slug word that is a known given name and is not in the title drops the item."""
    if "[name withheld]" not in (it.get("summary") or ""):
        return False
    given, _ = pub_privacy._given_names()
    title = (it.get("title") or "").lower()
    for u in (it.get("url"), it.get("thumbnail_url")):
        if not u or "youtube" in u or "vimeo" in u:
            continue
        last = re.sub(r"\.(jpe?g|png|gif|webp)$", "", u.rstrip("/").rsplit("/", 1)[-1].split("?")[0], flags=re.I)
        for w in re.split(r"[-_+%\s.]+", last.lower()):
            if w.isalpha() and len(w) > 2 and w not in title and (w in given or w.capitalize() in given):
                return True
    return False


def clean_summary(text: str | None, words: int = 40) -> str | None:
    if not text:
        return None
    t = html.unescape(re.sub(r"<[^>]+>", " ", text))
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    t, _ = pub_privacy.redact(t, "raw")
    t = pub_privacy.clip(t, words)
    return t or None


WEAK_SPECIAL = re.compile(r"^(feast|fiesta|solemnity)$", re.I)
JUNK = re.compile(r"^\s*(capture|img[_ ]|dsc|mov|video|untitled|stream|live)\b|^[\d\s:._-]+(am|pm)?$", re.I)


def is_routine(title: str) -> bool:
    if LECTIONARY.search(title) or JUNK.search(title):  # daily-readings uploads, raw camera captures
        return True
    if not ROUTINE.search(title):
        return False
    strong = [m.group(0) for m in SPECIAL.finditer(title) if not WEAK_SPECIAL.match(m.group(0))]
    return not strong  # a weekday feast Mass is still a routine livestream


def lang_guess(text: str) -> str:
    if re.search(r"\b(spanish|espa[nñ]ol)\b", text or "", re.I):
        return "es"
    ls = stats.langs_of([text])
    non_en = [x for x in ls if x != "en"]
    if non_en:
        return non_en[0]
    return "en" if re.search(r"[A-Za-z]{3,}", text or "") else "und"


def iso(dt: datetime | date | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return datetime(dt.year, dt.month, dt.day, 12, tzinfo=timezone.utc).isoformat()


def item_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def make(org_id: str, orgs: dict, **kw) -> dict:
    o = orgs.get(org_id, {"name": kw.pop("org_name_fallback", org_id), "type": "other", "deanery_id": None})
    kw.pop("org_name_fallback", None)
    return {"id": kw.pop("id"), "org_id": org_id, "org_name": o["name"], "org_type": o["type"],
            "deanery_id": o.get("deanery_id"), "kind": kw.pop("kind"), "title": kw.pop("title"),
            "summary": kw.pop("summary", None), "url": kw.pop("url"), "embed_url": kw.pop("embed_url", None),
            "thumbnail_url": kw.pop("thumbnail_url", None), "published_at": kw.pop("published_at"),
            "date_precision": kw.pop("date_precision", "exact"), "language": kw.pop("language", "und"),
            "source": kw.pop("source"), "source_url": kw.pop("source_url"), "fetched_at": kw.pop("fetched_at", FETCHED),
            **kw}


# ------------------------------------------------------------------------------------------- YouTube
REL = re.compile(r"(?:Streamed )?(\d+) (second|minute|hour|day|week|month|year)s? ago", re.I)


def _walk_lockups(o):
    if isinstance(o, dict):
        if "lockupViewModel" in o:
            yield o["lockupViewModel"]
        for v in o.values():
            yield from _walk_lockups(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_lockups(v)


def _hms(t: str) -> int:
    s = 0
    for part in t.split(":"):
        s = s * 60 + int(part)
    return s


def _count(t: str) -> int:
    t = t.replace(",", "")
    mult = 1_000 if t.endswith("K") else 1_000_000 if t.endswith("M") else 1
    return int(float(t.rstrip("KM")) * mult)


def yt_tab(cid: str, tab: str) -> list[dict]:
    st, body = cached(f"https://www.youtube.com/channel/{cid}/{tab}")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", body or "", re.S)
    if st != 200 or not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out = []
    for lv in _walk_lockups(data):
        vid = lv.get("contentId")
        if not vid or "VIDEO" not in str(lv.get("contentType", "VIDEO")):
            continue
        md = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
        title = ((md.get("title") or {}).get("content") or "").strip()
        labels = json.dumps(md.get("metadata") or {})
        rel = REL.search(labels)
        when = None
        if rel:
            days = int(rel.group(1)) * stats.UNIT_DAYS[rel.group(2).lower()]
            when = NOW - timedelta(days=days)
        img = json.dumps(lv.get("contentImage") or {})
        dur = re.search(r'"thumbnailBadgeViewModel": \{"text": "(\d+(?::\d{2}){1,2})"', img)
        views = re.search(r'"accessibilityLabel": "([\d,.]+[KM]?) (?:views|watching)"', labels)
        out.append({"vid": vid, "title": title, "when": when, "tab": tab,
                    "upcoming": "UPCOMING" in labels.upper() or "Scheduled for" in labels,
                    "duration_s": _hms(dur.group(1)) if dur else None,
                    "views": _count(views.group(1)) if views else None,
                    "live_badge": '"LIVE"' in img or "badgeStyle\": \"THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE" in img})
    return out


def yt_api_uploads(cid: str, key: str) -> list[dict]:
    """YouTube Data API v3: the channel's uploads playlist (UU...), exact publish dates."""
    url = ("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=25"
           f"&playlistId=UU{cid[2:]}&key={key}")
    r = get(url, timeout=20)
    if r is None or r.status_code != 200:
        return []
    out = []
    for it in r.json().get("items", []):
        sn = it.get("snippet") or {}
        vid = (sn.get("resourceId") or {}).get("videoId")
        if not vid:
            continue
        out.append({"vid": vid, "title": sn.get("title") or "", "tab": "videos", "upcoming": False,
                    "when": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")) if sn.get("publishedAt") else None,
                    "desc": sn.get("description")})
    return out


def youtube_items(channels: list[dict], orgs: dict, counts: collections.Counter) -> list[dict]:
    key = os.environ.get("YOUTUBE_API_KEY")
    out, seen = [], set()
    for c in channels:
        if c["platform"] != "youtube" or not (c.get("stats") or {}).get("channel_id"):
            continue
        cid = c["stats"]["channel_id"]
        if cid in seen:
            continue
        seen.add(cid)
        vids = yt_api_uploads(cid, key) if key else []
        method = "YouTube Data API v3 (uploads playlist)" if vids else "public channel /videos and /streams tabs"
        if not vids:
            vids = yt_tab(cid, "videos") + yt_tab(cid, "streams")
        for v in vids:
            counts["youtube_seen"] += 1
            if not v["when"] or (NOW - v["when"]).days > VIDEO_PAST_DAYS or v.get("upcoming"):
                counts["youtube_out_of_window"] += 1
                continue
            routine = is_routine(v["title"]) or (v["tab"] == "streams" and not SPECIAL.search(v["title"]))
            if routine and (NOW - v["when"]).days > ROUTINE_KEEP_DAYS:
                counts["youtube_routine_liturgy_older_than_keep_window"] += 1
                continue
            ctx = {"id": item_id("yt", v["vid"]), "org_id": c["owner_id"], "kind": "video", "source": "YouTube",
                   "published_at": iso(v["when"]), "url": f"https://www.youtube.com/watch?v={v['vid']}",
                   "org_type": (orgs.get(c["owner_id"]) or {}).get("type")}
            ok, why, t = title_ok(v["title"], ctx=ctx)
            if not ok:
                counts[f"youtube_privacy_{why}"] += 1
                continue
            out.append(make(c["owner_id"], orgs, id=item_id("yt", v["vid"]), kind="video", title=t,
                            summary=clean_summary(v.get("desc")),
                            url=f"https://www.youtube.com/watch?v={v['vid']}",
                            embed_url=f"https://www.youtube-nocookie.com/embed/{v['vid']}",
                            thumbnail_url=f"https://i.ytimg.com/vi/{v['vid']}/hqdefault.jpg",
                            published_at=iso(v["when"]), date_precision="exact" if key and vids and "desc" in v else "approx",
                            language=lang_guess(t), source=f"YouTube: {method}", source_url=c["url"],
                            org_name_fallback=c.get("owner_name"), duration_s=v.get("duration_s"),
                            views=v.get("views"), livestream=v["tab"] == "streams",
                            prefilter="routine_liturgy" if routine else None))
            if routine:
                counts["youtube_routine_liturgy_kept_flagged"] += 1
    return out


# ------------------------------------------------------------------------------------------- RSS (Vimeo, podcasts)
NS = {"media": "http://search.yahoo.com/mrss/", "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


def rss_items(url: str) -> list[dict]:
    st, body = cached(url)
    if st != 200 or "<rss" not in body[:500]:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for i in root.iter("item"):
        try:
            when = parsedate_to_datetime(i.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        thumb = None
        for tag in ("media:thumbnail", "media:content/media:thumbnail", "itunes:image"):
            el = i.find(tag, NS)
            if el is not None:
                thumb = el.get("url") or el.get("href")
                if thumb:
                    break
        enc = i.find("enclosure")
        dur = None
        mc = i.find("media:content", NS)
        if mc is not None and (mc.get("duration") or "").isdigit():
            dur = int(mc.get("duration"))
        it_d = i.findtext("itunes:duration", default="", namespaces=NS).strip()
        if dur is None and it_d:
            dur = int(it_d) if it_d.isdigit() else (_hms(it_d) if re.fullmatch(r"\d+(:\d{2}){1,2}", it_d) else None)
        out.append({"duration_s": dur, "title": (i.findtext("title") or "").strip(), "link": i.findtext("link"), "when": when,
                    "desc": i.findtext("description"), "thumb": thumb,
                    "audio": enc.get("url") if enc is not None else None})
    return out


def vimeo_items(channels: list[dict], orgs: dict, counts: collections.Counter) -> list[dict]:
    out = []
    for c in channels:
        if c["platform"] != "vimeo":
            continue
        key = re.sub(r"^https?://(www\.)?", "", c["url"]).rstrip("/")
        if key.startswith("vimeo.com/channels/") or key.split("/")[-1].isdigit():
            continue
        for it in rss_items(f"https://{key}/videos/rss"):
            counts["vimeo_seen"] += 1
            if not it["when"] or (NOW - it["when"]).days > VIDEO_PAST_DAYS:
                counts["vimeo_out_of_window"] += 1
                continue
            routine = is_routine(it["title"])
            if routine and (NOW - it["when"]).days > ROUTINE_KEEP_DAYS:
                counts["vimeo_routine_liturgy_older_than_keep_window"] += 1
                continue
            ctx = {"id": item_id("vimeo", it["link"] or it["title"]), "org_id": c["owner_id"], "kind": "video",
                   "source": "Vimeo", "published_at": iso(it["when"]), "url": it["link"],
                   "org_type": (orgs.get(c["owner_id"]) or {}).get("type")}
            ok, why, t = title_ok(it["title"], ctx=ctx)
            if not ok:
                counts[f"vimeo_privacy_{why}"] += 1
                continue
            vid = (re.search(r"vimeo\.com/(?:.*/)?(\d+)", it["link"] or "") or [None, None])[1]
            out.append(make(c["owner_id"], orgs, id=item_id("vimeo", it["link"] or t), kind="video", title=t,
                            summary=clean_summary(it["desc"]), url=it["link"],
                            embed_url=f"https://player.vimeo.com/video/{vid}" if vid else None,
                            thumbnail_url=it["thumb"], published_at=iso(it["when"]), language=lang_guess(t),
                            source="Vimeo public RSS", source_url=f"https://{key}/videos/rss",
                            org_name_fallback=c.get("owner_name"), duration_s=it.get("duration_s"),
                            prefilter="routine_liturgy" if routine else None))
    return out


def podcast_items(channels: list[dict], orgs: dict, counts: collections.Counter) -> list[dict]:
    out = []
    for c in channels:
        if c["platform"] != "podcast" or "libsyn.com" not in c["url"]:
            continue
        feed = c["url"].rstrip("/") + "/rss"
        for it in rss_items(feed)[:20]:
            counts["podcast_seen"] += 1
            if not it["when"] or (NOW - it["when"]).days > VIDEO_PAST_DAYS:
                counts["podcast_out_of_window"] += 1
                continue
            ctx = {"id": item_id("pod", it["link"] or it["audio"] or it["title"]), "org_id": c["owner_id"],
                   "kind": "episode", "source": "Podcast RSS", "published_at": iso(it["when"]), "url": it["link"],
                   "org_type": (orgs.get(c["owner_id"]) or {}).get("type")}
            ok, why, t = title_ok(it["title"], ctx=ctx)
            if not ok:
                counts[f"podcast_privacy_{why}"] += 1
                continue
            out.append(make(c["owner_id"], orgs, id=item_id("pod", it["link"] or it["audio"] or t), kind="episode",
                            title=t, summary=clean_summary(it["desc"]), url=it["link"] or c["url"],
                            thumbnail_url=it["thumb"], published_at=iso(it["when"]), language=lang_guess(t),
                            source="Podcast RSS", source_url=feed, audio_url=it["audio"],
                            org_name_fallback=c.get("owner_name"), duration_s=it.get("duration_s")))
    return out


# ------------------------------------------------------------------------------------------- WordPress REST
def wp_items(org_id: str, base: str, orgs: dict, counts: collections.Counter, pages: int = 2) -> list[dict]:
    base = base.rstrip("/")
    after = (NOW - timedelta(days=PAST_DAYS)).strftime("%Y-%m-%dT00:00:00")
    posts = []
    for p in range(1, pages + 1):
        url = (f"{base}/wp-json/wp/v2/posts?per_page=50&page={p}&after={after}"
               "&_fields=id,date_gmt,link,title,excerpt,featured_media")
        st, body = cached(url, max_age_h=24)
        if st != 200:
            counts[f"wp_status_{st}"] += 1
            break
        try:
            batch = json.loads(body)
        except json.JSONDecodeError:
            counts["wp_not_json"] += 1
            break
        if not isinstance(batch, list) or not batch:
            break
        posts += batch
        if len(batch) < 50:
            break
    media_ids = sorted({x["featured_media"] for x in posts if x.get("featured_media")})
    thumbs: dict[int, str] = {}
    for i in range(0, len(media_ids), 50):
        ids = ",".join(map(str, media_ids[i:i + 50]))
        st, body = cached(f"{base}/wp-json/wp/v2/media?per_page=50&include={ids}&_fields=id,source_url,media_details", max_age_h=24 * 7)
        if st == 200:
            try:
                for m in json.loads(body):
                    sizes = ((m.get("media_details") or {}).get("sizes") or {})
                    pick = sizes.get("medium_large") or sizes.get("medium") or {}
                    thumbs[m["id"]] = pick.get("source_url") or m.get("source_url")
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
    out = []
    for x in posts:
        counts["wp_seen"] += 1
        raw_title = html.unescape(re.sub(r"<[^>]+>", "", (x.get("title") or {}).get("rendered") or "")).strip()
        if re.match(r"^the weekly\b", raw_title, re.I):  # the leaders' newsletter: one news item per issue is enough
            pass
        ctx = {"id": item_id("wp", base, str(x["id"])), "org_id": org_id, "kind": "news", "source": "WordPress REST API",
               "published_at": x.get("date_gmt"), "url": x.get("link"), "org_type": (orgs.get(org_id) or {}).get("type")}
        ok, why, t = title_ok(raw_title, ctx=ctx)
        if not ok:
            counts[f"wp_privacy_{why}"] += 1
            continue
        when = datetime.fromisoformat(x["date_gmt"] + "+00:00") if x.get("date_gmt") else None
        out.append(make(org_id, orgs, id=item_id("wp", base, str(x["id"])), kind="news", title=t,
                        summary=clean_summary((x.get("excerpt") or {}).get("rendered")), url=x.get("link"),
                        thumbnail_url=thumbs.get(x.get("featured_media")), published_at=iso(when),
                        language=lang_guess(t), source="WordPress REST API", source_url=f"{base}/wp-json/wp/v2/posts"))
    return out


# ------------------------------------------------------------------------------------------- events, bulletins
EVENT_CATS = {"formation", "youth_young_adults", "community", "charity_service", "school", "sacramental_preparation",
              "devotion_prayer"}


def calendar_items(orgs: dict, counts: collections.Counter) -> list[dict]:
    data = json.loads((REAL / "calendars" / "events.json").read_text())
    out = []
    for ev in data["events"]:
        try:
            d = date.fromisoformat(ev["start"][:10])
        except (TypeError, ValueError):
            continue
        if d < TODAY or (d - TODAY).days > AHEAD_DAYS:
            continue
        counts["calendar_seen"] += 1
        if ev.get("recurring") or "facility_calendar" in (ev.get("tags") or []) or ev.get("category") not in EVENT_CATS:
            counts["calendar_routine_or_recurring"] += 1
            continue
        if ev["category"] == "devotion_prayer" and not SPECIAL.search(ev["title"]):
            counts["calendar_routine_or_recurring"] += 1
            continue
        org = ev.get("owner_id") or ev.get("parish_id")
        ok, why, t = title_ok(ev["title"], ctx={"id": item_id("cal", ev["id"]), "org_id": org, "kind": "event",
                                                "source": "Parish public calendar", "published_at": iso(d),
                                                "url": ev["source_url"], "org_type": (orgs.get(org) or {}).get("type")})
        if not ok:
            counts[f"calendar_privacy_{why}"] += 1
            continue
        out.append(make(org, orgs, id=item_id("cal", ev["id"]), kind="event", title=t.strip(),
                        summary=None, url=ev["source_url"], published_at=iso(d), date_precision="day",
                        language=lang_guess(t), source="Parish public calendar", source_url=ev["source_url"],
                        fetched_at=ev.get("fetched_at") or FETCHED, event_category=ev["category"]))
    return out


def diocese_event_items(orgs: dict, venue_to_org: dict, counts: collections.Counter) -> list[dict]:
    data = json.loads((REAL / "diocese_events.json").read_text())
    out = []
    for ev in data["events"]:
        try:
            d = date.fromisoformat(ev["start"][:10])
        except (TypeError, ValueError):
            continue
        if d < TODAY or (d - TODAY).days > AHEAD_DAYS * 2:
            continue
        counts["diocese_events_seen"] += 1
        cats = ev.get("categories") or []
        if "The Weekly" in cats or ev.get("is_recurring_instance"):
            counts["diocese_events_skipped"] += 1
            continue
        title = html.unescape(ev["title"])
        if is_routine(title) and not ev.get("is_bishop_calendar"):
            counts["diocese_events_routine"] += 1
            continue
        ok, why, t = title_ok(title, cats, ctx={"id": item_id("dioev", str(ev["id"])), "org_id": "dio-oakland", "kind": "event",
                                                "source": "oakdiocese.org events calendar", "published_at": iso(d),
                                                "url": ev["url"], "org_type": "diocese"})
        if not ok:
            counts[f"diocese_events_privacy_{why}"] += 1
            continue
        org = venue_to_org.get(ev.get("venue_canon_key") or "", "dio-oakland")
        out.append(make(org, orgs, id=item_id("dioev", str(ev["id"])), kind="event", title=t,
                        summary=", ".join(x for x in [ev.get("venue"), ev.get("venue_city")] if x) or None,
                        url=ev["url"], published_at=iso(d), date_precision="day", language=lang_guess(t),
                        source="oakdiocese.org events calendar (WordPress REST)", source_url=data["source"],
                        fetched_at=data.get("retrieved") or FETCHED,
                        event_category="bishop_calendar" if ev.get("is_bishop_calendar") else (cats[0] if cats else None)))
    return out


def bulletin_items(orgs: dict, counts: collections.Counter) -> list[dict]:
    prof = json.loads((REAL / "enrichment" / "parish_profiles.json").read_text())["records"]
    out = []
    for r in prof:
        for ev in r.get("upcoming_events") or []:
            counts["bulletin_seen"] += 1
            try:
                d = date.fromisoformat(ev["date"])
            except (TypeError, ValueError, KeyError):
                continue
            if d < TODAY or (d - TODAY).days > AHEAD_DAYS:
                counts["bulletin_out_of_window"] += 1
                continue
            raw = re.sub(r"\s+", " ", ev.get("title") or "").strip()
            # bulletin lines are cut out of running text; keep only those that read like an announcement
            if len(raw) < 12 or re.match(r"^[a-z]", raw) or len(raw.split()) < 4 or is_routine(raw):
                counts["bulletin_fragment"] += 1
                continue
            ok, why, t = title_ok(raw, ctx={"id": item_id("bul", r["id"], ev["date"], raw), "org_id": r["id"],
                                            "kind": "bulletin_item", "source": "Parish bulletin (parsed)",
                                            "published_at": iso(d), "url": ev["source_url"], "org_type": "parish"})
            if not ok:
                counts[f"bulletin_privacy_{why}"] += 1
                continue
            t, n = pub_privacy.redact(t, "raw")
            if n:  # a bulletin line that names someone is dropped, not shown with a gap
                counts["bulletin_privacy_redacted"] += 1
                continue
            out.append(make(r["id"], orgs, id=item_id("bul", r["id"], ev["date"], raw), kind="bulletin_item",
                            title=t, summary=f"From the parish bulletin of {ev.get('bulletin_date')}, p.{ev.get('page')}",
                            url=ev["source_url"], published_at=iso(d), date_precision="day", language=lang_guess(t),
                            source="Parish bulletin (parsed)", source_url=ev["source_url"],
                            fetched_at=ev.get("fetched_at") or FETCHED))
    return out


def curated_items(orgs: dict, counts: collections.Counter) -> list[dict]:
    f = MEDIA / "curated_embeds.json"
    if not f.exists():
        return []
    out = []
    for c in json.loads(f.read_text()).get("embeds", []):
        ok, why, t = title_ok(c.get("title") or "", ctx={"id": item_id("cur", c["url"]), "org_id": c["org_id"],
                                                          "kind": c.get("kind", "photo_post"), "source": "Hand-curated embed",
                                                          "published_at": c.get("published_at"), "url": c["url"],
                                                          "org_type": (orgs.get(c["org_id"]) or {}).get("type")})
        if not ok:
            counts[f"curated_privacy_{why}"] += 1
            continue
        out.append(make(c["org_id"], orgs, id=item_id("cur", c["url"]), kind=c.get("kind", "photo_post"), title=t,
                        summary=clean_summary(c.get("summary")), url=c["url"], embed_url=c.get("embed_url"),
                        thumbnail_url=c.get("thumbnail_url"), published_at=c.get("published_at"),
                        language=lang_guess(t), source=f"Hand-curated {c.get('platform', 'embed')} (official embed)",
                        source_url=c["url"], fetched_at=c.get("added_at") or FETCHED, curated=True))
    return out


# ------------------------------------------------------------------------------------------- pinned (the Appeal videos)
# The diocese's own 2026 Bishop's Ministries Appeal videos, embedded on https://www.oakdiocese.org/bishops-ministries-appeal/
# (English and Spanish). Kept regardless of the date window; read with Vimeo's public oEmbed.
PINNED = [("dio-oakland", "https://vimeo.com/1166663803", "https://www.oakdiocese.org/bishops-ministries-appeal/"),
          ("dio-oakland", "https://vimeo.com/1170015410", "https://www.oakdiocese.org/bishops-ministries-appeal/")]


def pinned_items(orgs: dict, counts: collections.Counter) -> list[dict]:
    out = []
    for org, url, found_on in PINNED:
        st, body = cached("https://vimeo.com/api/oembed.json?url=" + url, max_age_h=24 * 30)
        if st != 200:
            counts[f"pinned_status_{st}"] += 1
            continue
        j = json.loads(body)
        ok, why, t = title_ok(j.get("title") or "", ctx={"id": item_id("vimeo", url), "org_id": org, "kind": "video",
                                                          "source": "Vimeo oEmbed", "url": url, "org_type": "diocese"})
        if not ok:
            counts[f"pinned_privacy_{why}"] += 1
            continue
        when = None
        if j.get("upload_date"):
            when = datetime.fromisoformat(j["upload_date"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        out.append(make(org, orgs, id=item_id("vimeo", url), kind="video", title=t,
                        summary=clean_summary(j.get("description")), url=url,
                        embed_url=f"https://player.vimeo.com/video/{j.get('video_id') or url.rsplit('/', 1)[-1]}",
                        thumbnail_url=j.get("thumbnail_url"), published_at=iso(when) or FETCHED,
                        language=lang_guess(t), source="Vimeo oEmbed (video on the diocese's Appeal page)",
                        source_url=found_on, pinned=True, duration_s=j.get("duration")))
    return out


# ------------------------------------------------------------------------------------------- Appeal tags
# Which of the four 2026 Appeal ministries an item illustrates (keyword or organization based; a person can correct it).
TAGS = [
    ("appeal", r"\b(bishop'?s?[’']? ministries appeal|bishop'?s?[’']? appeal|ministries appeal|\bbma\b)"),
    ("sustain", r"\b(food|meals?|pantry|pantries|shelter|homeless|hunger|hungry|poverty|poor|rent|housing|clothing|"
                r"thanksgiving drive|food drive|joy bells|vincent de paul|svdp|charit(y|ies)|refugee|immigra)"),
    ("build", r"\b(vocations?|seminar(y|ian|ians)|ordination|ordained|ordains|priesthood|diaconate|deacons?|"
              r"discern(ing|ment)?|religious life|final vows|first vows)\b"),
    ("heal", r"\b(grief|griev|healing|heal|counsel(ing|or)?|mental health|hospital|chaplain|sick|homebound|"
             r"anointing|hospice|detention|jail|prison|incarcerat|white mass|depression|addiction|recovery)"),
    ("empower", r"\b(youth|young adults?|j[oó]venes|teens?|students?|school|escuela|catechesis|catequesis|"
                r"faith formation|formaci[oó]n|confirmation|confirmaci[oó]n|first communion|ocia|rcia|bible study|"
                r"retreat|campus ministry|newman)"),
]
TAG_BY_ORGTYPE = {"svdp_conference": "sustain", "svdp_council": "sustain", "charity": "sustain", "seminary": "build",
                  "school": "empower", "chaplaincy": "heal", "healthcare": "heal"}


def tags_for(it: dict) -> list[str]:
    text = f"{it['title']} {it.get('summary') or ''}"
    tags = [k for k, rx in TAGS if re.search(rx, text, re.I)]
    t = TAG_BY_ORGTYPE.get(it["org_type"])
    if t and t not in tags:
        tags.append(t)
    if it.get("pinned") and "appeal" not in tags:
        tags.insert(0, "appeal")
    return tags


# ------------------------------------------------------------------------------------------- scoring, caps
KIND_W = {"video": 1.0, "news": 1.0, "episode": 0.85, "photo_post": 0.9, "event": 0.8, "bulletin_item": 0.5}
LOCAL = {"parish", "mission", "school", "diocese", "charity", "chancery_office", "svdp_conference", "svdp_council",
         "ethnic_apostolate", "diocesan_ministry", "seminary", "media", "healthcare", "cemetery"}


def score(it: dict) -> float:
    try:
        when = datetime.fromisoformat(it["published_at"])
    except (TypeError, ValueError):
        return 0.0
    days = (when - NOW).total_seconds() / 86400
    recency = 0.5 ** (abs(days) / 21) * (0.9 if days > 0 else 1.0)
    t = it["title"] or ""
    dist = 1.0 + (0.25 if it.get("thumbnail_url") else 0) + (0.35 if SPECIAL.search(t) else 0) + (0.15 if GOOD.search(t) else 0)
    dist -= 0.3 if GENERIC.search(t) else 0
    dist *= KIND_W.get(it["kind"], 0.7) * (1.0 if it["org_type"] in LOCAL else 0.6)
    return round(recency * max(dist, 0.1), 4)


def cap_per_week(items: list[dict], counts: collections.Counter, n: int = 2) -> list[dict]:
    by = collections.defaultdict(list)
    for it in items:
        d = datetime.fromisoformat(it["published_at"])
        by[(it["org_id"], d.isocalendar()[0], d.isocalendar()[1])].append(it)
    keep = []
    for group in by.values():
        pinned = [x for x in group if x.get("pinned") or x.get("curated")]
        keep += pinned
        group = [x for x in group if x not in pinned]
        group.sort(key=lambda x: (-x["score"], x["id"]))
        keep += group[:n]
        counts["capped_2_per_org_week"] += max(len(group) - n, 0)
    return keep


def main() -> None:
    orgs = load_orgs()
    channels = json.loads((MEDIA / "channels.json").read_text())["channels"]
    counts: collections.Counter = collections.Counter()
    venue_to_org = {}
    for e in json.loads((ROOT / "data" / "seed" / "entities.json").read_text())["entities"]:
        k =(e.get("attrs") or {}).get("map_canon_key")
        if k:
            venue_to_org[k] = e["id"]

    items: list[dict] = []
    items += youtube_items(channels, orgs, counts)
    items += vimeo_items(channels, orgs, counts)
    items += podcast_items(channels, orgs, counts)
    items += wp_items("dio-oakland", "https://www.oakdiocese.org", orgs, counts, pages=3)
    wp_sites = []
    for r in json.loads((REAL / "parish_sites.json").read_text())["records"]:
        if (r.get("cms_guess") or {}).get("cms") == "wordpress":
            base = re.match(r"https?://[^/]+", r.get("final_home_url") or r.get("website") or "")
            if base:
                n0 = len(items)
                items += wp_items(r["id"], base.group(0), orgs, counts, pages=1)
                wp_sites.append({"org_id": r["id"], "base": base.group(0), "posts_in_window": len(items) - n0})
    items += calendar_items(orgs, counts)
    items += diocese_event_items(orgs, venue_to_org, counts)
    items += bulletin_items(orgs, counts)
    items += curated_items(orgs, counts)
    items += pinned_items(orgs, counts)

    # de-duplicate (a video embedded by several orgs), drop context bodies and community partners (not Church bodies)
    seen, uniq = set(), []
    for it in items:
        tkey = (it["org_id"], it["kind"], re.sub(r"\W+", " ", (it["title"] or "").lower()).strip())
        if it["id"] in seen or tkey in seen or not it["published_at"]:
            counts["duplicate"] += 1
            continue
        seen.add(tkey)
        if slug_names_person(it):
            counts["privacy_name_in_link"] += 1
            DROPS.append(("name_in_link", it["title"]))
            quarantine("name_in_link", it["title"], it)
            continue
        # A summary that needed a name withheld is dropped whole (the rest of it often names the person again by
        # first name); podcast show notes name guests and callers, so episodes carry their title only.
        sm = it.get("summary") or ""
        if "[name withheld]" in sm or it["kind"] == "episode" or re.search(r"\blistener named\b", sm, re.I):
            if sm:
                counts["summary_withheld"] += 1
            it["summary"] = None
        if it["org_type"] in ("context", "community_partner") or str(it["org_id"]).startswith(("ctx-", "ptn-")):
            counts["skipped_context_or_partner"] += 1
            continue
        seen.add(it["id"])
        it["tags"] = tags_for(it)
        it.setdefault("prefilter", None)
        uniq.append(it)
    # ranking, the per-organization cap and the auto status are rank.py's job (docs/design/media-ranking.md)
    feed = uniq
    feed.sort(key=lambda x: (x["published_at"], x["id"]), reverse=True)
    out = {
        "generated_at": FETCHED,
        "generated_by": "ingest/public/media/feed_build.py",
        "window": {"news_past_days": PAST_DAYS, "video_past_days": VIDEO_PAST_DAYS, "routine_keep_days": ROUTINE_KEEP_DAYS,
                   "ahead_days": AHEAD_DAYS, "as_of": TODAY.isoformat()},
        "ranked_by": None,
        "count": len(feed),
        "counts_by_kind": dict(collections.Counter(x["kind"] for x in feed)),
        "counts_by_source": dict(collections.Counter(x["source"] for x in feed)),
        "orgs_with_items": len({x["org_id"] for x in feed}),
        "pipeline_counts": dict(sorted(counts.items())),
        "wordpress_sites": wp_sites,
        "notes": [
            "Facebook and Instagram are never fetched; they appear only as links and as hand-curated official embeds.",
            "YouTube dates are approximate (the public tab shows '3 days ago') unless YOUTUBE_API_KEY was set.",
            "Event items carry the event date in published_at (future dates = upcoming).",
            "Titles that name a private person are quarantined (quarantine.json, redacted); summaries are redacted.",
            "Routine Mass streams of the last ROUTINE_KEEP_DAYS days are kept with prefilter=routine_liturgy; rank.py demotes them.",
        ],
        "items": feed,
    }
    (RAW / "media").mkdir(parents=True, exist_ok=True)
    (RAW / "media" / "_feed_drops.json").write_text(json.dumps(DROPS, indent=0, ensure_ascii=False))
    qids, q = set(), []
    for r in QUARANTINE:
        if r["id"] not in qids:
            qids.add(r["id"])
            q.append(r)
    (MEDIA / "quarantine.json").write_text(json.dumps({
        "generated_at": FETCHED, "generated_by": "ingest/public/media/feed_build.py (media_privacy.py)",
        "note": "Items kept out of the feed because the title names a private person (or a private occasion). Titles are "
                "redacted; non-YouTube/Vimeo links are cut to the host because a slug can carry the name.",
        "count": len(q), "by_reason": dict(collections.Counter(r["reason"] for r in q)), "items": q,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (MEDIA / "feed.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("count", "counts_by_kind", "counts_by_source", "orgs_with_items")}, indent=1))
    print(json.dumps(out["pipeline_counts"], indent=1))


if __name__ == "__main__":
    main()
