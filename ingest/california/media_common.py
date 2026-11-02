"""Shared helpers for the California YouTube catalog (ingest/california/media_*.py).

* YouTube link parsing (channel ids, @handles, /c/, /user/, bare vanity names, single videos).
* A polite fetcher for youtube.com: the honest FindTheChurch user agent (2026-11-02: with the HTTP library's plain default
  User-Agent YouTube no longer serves the ytInitialData the parsers read; the honest name gets the normal page), robots.txt
  read and honoured (/feeds/videos.xml, /results, /api/, /youtubei/ are disallowed and never requested), at most one network
  request every 2 seconds across the whole process, answers cached under data/raw/california/youtube/cache/ (gitignored).
  A consent page or a "confirm you're not a bot" page stops the run (never worked around); it is written to
  RAW/blocked.json. MEDIA_DEADLINE (epoch seconds) stops cleanly at a time limit (GitHub runner chunks).
* Parsers for the public channel /about page (ytInitialData: title, handle, description, About links) and for the
  /videos and /streams tabs (same lockupViewModel reading as ingest/public/media/feed_build.py).
* Name / city / domain matching used by the ownership check.

Nothing here is a scraper of private data: channel titles, handles and About links are public; video titles go through
ingest/public/media/media_privacy.py before anything is kept.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import os

import requests

ROOT = Path(__file__).resolve().parents[2]
CA = ROOT / "data" / "real" / "california"
# MEDIA_OUT / MEDIA_RAW let the national run (ingest/us/media/) reuse this module with its own folders
OUT = Path(os.environ["MEDIA_OUT"]) if os.environ.get("MEDIA_OUT") else CA / "media"
RAW = Path(os.environ["MEDIA_RAW"]) if os.environ.get("MEDIA_RAW") else ROOT / "data" / "raw" / "california" / "youtube"
CACHE = RAW / "cache"
for _d in (OUT, RAW, CACHE):
    _d.mkdir(parents=True, exist_ok=True)
USER_AGENT = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"}
DEADLINE = float(os.environ["MEDIA_DEADLINE"]) if os.environ.get("MEDIA_DEADLINE") else None
BOT_PAGE = re.compile(r"confirm you(?:'|’|&#39;)?re not a bot|Sign in to confirm|unusual traffic from your computer", re.I)

NOW = datetime.now(timezone.utc)
MIN_GAP_S = 2.0

# websites of the dioceses (the home site plus older / alternative domains)
EXTRA_DIOCESE_HOSTS = {"los-angeles": ["la-archdiocese.org", "archdiocese.la", "lacatholics.org"],
                       "san-francisco": ["sfarchdiocese.org", "sfarch.org"], "orange": ["rcbo.org"],
                       "san-bernardino": ["sbdiocese.org", "sbdcatholics.org"], "stockton": ["dioceseofstockton.org", "stocktondiocese.org"],
                       "monterey": ["dioceseofmonterey.org", "dioceseofmonterey.com"], "fresno": ["dioceseoffresno.org", "dioceseoffresno.com"],
                       "san-jose": ["dsj.org"], "santa-rosa": ["srdiocese.org"], "sacramento": ["scd.org"], "san-diego": ["sdcatholic.org"],
                       "oakland": ["oakdiocese.org"]}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------------------------------ text helpers
def fold(s: str | None) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


STOP = {"the", "of", "and", "de", "del", "la", "el", "los", "las", "y", "in", "at", "a", "an", "for", "to", "our", "on"}
GENERIC = {"church", "parish", "catholic", "roman", "community", "iglesia", "parroquia", "catolica", "catolico", "cc", "rc",
           "mission", "misión", "misson", "chapel", "shrine", "basilica", "cathedral", "center", "centre", "ministry",
           "ministries", "official", "channel", "youtube", "tv", "live", "online", "stream", "streaming", "video", "videos"}
# Spanish / abbreviated saint names -> one canonical token so "San José" meets "St. Joseph".
ALIAS = {"st": "saint", "sts": "saint", "saints": "saint", "santa": "saint", "san": "saint", "santo": "saint", "ste": "saint",
         "sainte": "saint", "sant": "saint",
         "jose": "joseph", "juan": "john", "pedro": "peter", "pablo": "paul", "francisco": "francis", "antonio": "anthony",
         "miguel": "michael", "marcos": "mark", "mateo": "matthew", "lucas": "luke", "maria": "mary", "ana": "anne", "anne": "anne",
         "ines": "agnes", "isidro": "isidore", "judas": "jude", "tomas": "thomas", "bartolome": "bartholomew",
         "jeronimo": "jerome", "ignacio": "ignatius", "agustin": "augustine", "benito": "benedict", "gregorio": "gregory",
         "lorenzo": "lawrence", "vicente": "vincent", "rafael": "raphael", "gabriel": "gabriel", "diego": "james",
         "mt": "mount", "ft": "fort", "pt": "point", "nuestra": "our", "senora": "lady", "nra": "our", "sra": "lady",
         "stephen": "stephen", "esteban": "stephen", "teresa": "theresa", "therese": "theresa", "cecilia": "cecilia",
         "catherine": "catherine", "katherine": "catherine", "catalina": "catherine", "isabel": "elizabeth", "elizabeth": "elizabeth"}


def toks(s: str | None) -> list[str]:
    """Canonical word tokens of a name or title ('St. Joseph's Catholic Church' -> ['saint', 'joseph'])."""
    t = fold(s).replace("&", " and ").replace("'", "").replace("’", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    out = []
    for w in t.split():
        w = ALIAS.get(w, w)
        if w in STOP or w in GENERIC:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def squash(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold(s))


def reg_domain(url_or_host: str | None) -> str | None:
    """example.org from https://www.stjoe.example.org/x ; the last two labels (three for co.xx style)."""
    if not url_or_host:
        return None
    u = url_or_host.strip()
    if "://" not in u:
        u = "http://" + u
    try:
        h = (urlparse(u).hostname or "").lower()
    except ValueError:
        return None
    h = re.sub(r"^(www\d?|m)\.", "", h)
    parts = h.split(".")
    if len(parts) < 2:
        return None
    if parts[-2] in ("co", "com", "org", "net", "gov", "edu") and len(parts[-1]) == 2 and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_label(url_or_host: str | None) -> str | None:
    d = reg_domain(url_or_host)
    return d.split(".")[0] if d else None


# ------------------------------------------------------------------------------------------------ YouTube links
YT_URL = re.compile(r"""(?:https?:)?//(?:www\.|m\.|music\.)?(?:youtube(?:-nocookie)?\.com|youtu\.be)/[^\s"'<>\\)\]}]+""", re.I)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
RESERVED = {"watch", "embed", "playlist", "results", "feed", "live", "shorts", "channel", "c", "user", "redirect", "oembed",
            "iframe_api", "s", "about", "premium", "hashtag", "tv", "t", "yt", "account", "howyoutubeworks", "gaming", "kids",
            "reporthistory", "post", "podcasts", "audiolibrary", "upload", "signin", "static", "img", "vi", "player_api",
            "get_video_info", "watch_popup", "attribution_link", "api", "subscribe_embed", "subscription_center", "supported_browsers",
            "verify_age", "e", "v", "favicon.ico", "robots.txt", "sw.js", "results?search_query", "playlists", "videos", "streams",
            "featured", "community", "store", "channels", "movies", "storefront", "view_play_list", "profile", "user_watch",
            "feeds", "pwa", "index", "www", "html", "iframe", "player", "channel_switcher", "create_channel", "musicpremium"}
# accounts a site template links by default, plus platform accounts (mirrors ingest/california/sites/parse_site.py)
TEMPLATE_HANDLES = {"wix", "squarespace", "weebly", "godaddy", "wordpress", "wordpresscom", "teepublic", "elementor", "webflow",
                    "pontifex", "masstime.us", "facebook", "instagram", "twitter", "youtube", "youtubecreators", "youtubemusic",
                    "google", "googlechrome", "gsuite", "vimeo", "shopify", "ecatholic", "ecatholicofficial", "wixeditor",
                    "squarespacehelp", "weeblycom", "websitebuilder", "yt", "yt-lawsuit"}


def parse_yt_url(url: str) -> dict | None:
    """-> {'type': channel_id|handle|custom|user|vanity|video|playlist, 'key': str, 'url': canonical, 'live_hint': bool} or None.
    type=video means a single video (its channel is found with oEmbed)."""
    u = url.replace("\\/", "/").replace("&amp;", "&").strip().rstrip(".,;")
    if u.startswith("//"):
        u = "https:" + u
    try:
        p = urlparse(u)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    host = re.sub(r"^(www|m|music)\.", "", host)
    segs = [unquote(s) for s in p.path.split("/") if s]
    q = parse_qs(p.query)
    if host == "youtu.be":
        return {"type": "video", "key": segs[0], "url": f"https://www.youtube.com/watch?v={segs[0]}", "live_hint": False} \
            if segs and VIDEO_ID.match(segs[0]) else None
    if host not in ("youtube.com", "youtube-nocookie.com"):
        return None
    if not segs:
        return None
    s0 = segs[0]
    live = bool(len(segs) > 1 and segs[-1] in ("live", "streams")) or s0 == "live"
    if s0 == "channel" and len(segs) > 1 and re.fullmatch(r"UC[\w-]{22}", segs[1]):
        return {"type": "channel_id", "key": segs[1], "url": f"https://www.youtube.com/channel/{segs[1]}", "live_hint": live}
    if s0.startswith("@") and len(s0) > 1:
        return {"type": "handle", "key": s0.lower(), "url": f"https://www.youtube.com/{s0}", "live_hint": live}
    if s0 in ("c", "user") and len(segs) > 1:
        return {"type": "custom" if s0 == "c" else "user", "key": f"{s0}/{segs[1].lower()}",
                "url": f"https://www.youtube.com/{s0}/{segs[1]}", "live_hint": live}
    if s0 == "embed" and len(segs) > 1:
        if segs[1] == "live_stream" and q.get("channel") and re.fullmatch(r"UC[\w-]{22}", q["channel"][0]):
            return {"type": "channel_id", "key": q["channel"][0], "url": f"https://www.youtube.com/channel/{q['channel'][0]}", "live_hint": True}
        if VIDEO_ID.match(segs[1]):
            return {"type": "video", "key": segs[1], "url": f"https://www.youtube.com/watch?v={segs[1]}", "live_hint": False}
        return None
    if s0 == "watch" and q.get("v") and VIDEO_ID.match(q["v"][0]):
        return {"type": "video", "key": q["v"][0], "url": f"https://www.youtube.com/watch?v={q['v'][0]}", "live_hint": False}
    if s0 in ("live", "shorts", "v", "e") and len(segs) > 1 and VIDEO_ID.match(segs[1]):
        return {"type": "video", "key": segs[1], "url": f"https://www.youtube.com/watch?v={segs[1]}", "live_hint": s0 == "live"}
    if s0 == "playlist" and q.get("list"):
        return {"type": "playlist", "key": q["list"][0], "url": f"https://www.youtube.com/playlist?list={q['list'][0]}", "live_hint": False}
    if s0.lower() not in RESERVED and re.fullmatch(r"[A-Za-z0-9_.-]{3,60}", s0):
        return {"type": "vanity", "key": s0.lower(), "url": f"https://www.youtube.com/{s0}", "live_hint": live}
    return None


def extract_yt(text: str) -> list[dict]:
    """Every YouTube link in a blob of HTML / JSON (escaped slashes handled), parsed and de-duplicated by key."""
    t = text.replace("\\/", "/").replace("\\u002F", "/").replace("&amp;", "&").replace("\\u0026", "&")
    out, seen = [], set()
    for m in YT_URL.finditer(t):
        r = parse_yt_url(m.group(0))
        if not r:
            continue
        k = (r["type"], r["key"])
        if k in seen:
            continue
        seen.add(k)
        r["raw"] = m.group(0)[:200]
        out.append(r)
    return out


def is_template_handle(key: str) -> bool:
    k = re.sub(r"[^a-z0-9.]", "", key.lower().split("/")[-1].lstrip("@"))
    return k in TEMPLATE_HANDLES


# ------------------------------------------------------------------------------------------------ polite fetcher
class YouTube:
    """Cached, throttled, robots-aware GET for youtube.com. One instance per process."""

    def __init__(self) -> None:
        self.last = 0.0
        self.requests_made = 0
        self.cache_hits = 0
        self.errors = 0
        self.stopped: str | None = None
        self.rp = urllib.robotparser.RobotFileParser()
        rf = RAW / "robots.txt"
        if rf.exists() and time.time() - rf.stat().st_mtime < 7 * 86400:
            body = rf.read_text()
        else:
            self._wait()
            body = requests.get("https://www.youtube.com/robots.txt", headers=HEADERS, timeout=20).text
            self.requests_made += 1
            rf.write_text(body)
        self.rp.parse(body.splitlines())

    def _wait(self) -> None:
        gap = time.monotonic() - self.last
        if gap < MIN_GAP_S:
            time.sleep(MIN_GAP_S - gap)
        self.last = time.monotonic()

    def allowed(self, url: str) -> bool:
        return self.rp.can_fetch("*", url)

    def get(self, url: str, kind: str = "page", max_age_h: float = 24 * 3) -> dict:
        """-> {'status', 'final_url', 'data' (ytInitialData dict | oEmbed dict | None), 'fetched_at', 'cached': bool, 'error'}.
        kind='page' extracts ytInitialData; kind='json' parses the body as JSON (oEmbed)."""
        f = CACHE / (hashlib.sha1((kind + url).encode()).hexdigest()[:20] + ".json.gz")
        if f.exists() and (time.time() - f.stat().st_mtime) < max_age_h * 3600:
            self.cache_hits += 1
            d = json.loads(gzip.decompress(f.read_bytes()))
            d["cached"] = True
            return d
        if DEADLINE and time.time() > DEADLINE and not self.stopped:
            self.stopped = "deadline"
        if self.stopped:
            return {"status": None, "final_url": None, "data": None, "fetched_at": None, "cached": False, "error": self.stopped}
        if not self.allowed(url):
            return {"status": None, "final_url": None, "data": None, "fetched_at": None, "cached": False, "error": "robots_disallowed"}
        r = None
        err = None
        for attempt in range(2):
            self._wait()
            try:
                r = requests.get(url, headers=HEADERS, timeout=25)  # honest user agent, never a browser's
                self.requests_made += 1
            except requests.RequestException as e:
                err = type(e).__name__
                self.errors += 1
                r = None
                continue
            if r.status_code == 429 or r.status_code >= 500:
                err = f"http_{r.status_code}"
                self.errors += 1
                time.sleep(30 * (attempt + 1))
                if attempt == 1 and r.status_code == 429:
                    self.stopped = "rate_limited_429"
                continue
            break
        if r is None or r.status_code in (429,) or r.status_code >= 500:
            return {"status": r.status_code if r is not None else None, "final_url": None, "data": None,
                    "fetched_at": now_iso(), "cached": False, "error": err or "failed"}
        fu = (r.url or "").lower()
        if "consent.youtube.com" in fu or "consent.google." in fu or "google.com/sorry" in fu or "accounts.google.com" in fu \
                or (kind == "page" and r.status_code == 200 and "ytInitialData" not in r.text[:3_000_000] and BOT_PAGE.search(r.text[:300_000])):
            self.stopped = "consent_or_bot_page"
            (RAW / "blocked.json").write_text(json.dumps({"at": now_iso(), "url": url, "final_url": r.url, "status": r.status_code,
                                                          "note": "YouTube served a consent / bot-check page: run stopped, not worked around"}) + "\n")
            return {"status": r.status_code, "final_url": r.url, "data": None, "fetched_at": now_iso(), "cached": False, "error": self.stopped}
        data = None
        if r.status_code == 200:
            if kind == "json":
                try:
                    data = r.json()
                except ValueError:
                    data = None
            else:
                m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
                if m:
                    try:
                        data = json.loads(m.group(1))
                    except json.JSONDecodeError:
                        data = None
        out = {"status": r.status_code, "final_url": r.url, "data": data, "fetched_at": now_iso(), "cached": False, "error": None}
        f.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False).encode()))
        return out


# ------------------------------------------------------------------------------------------------ page parsers
def walk(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v, key)


def parse_about(d: dict | None) -> dict | None:
    """ytInitialData of a channel /about page -> {channel_id, title, handle, description, links[{label, text}], ...}."""
    if not d:
        return None
    meta = (d.get("metadata") or {}).get("channelMetadataRenderer") or {}
    about = next(walk(d, "aboutChannelViewModel"), None) or {}
    cid = meta.get("externalId") or about.get("channelId")
    if not cid:
        return None
    van = meta.get("vanityChannelUrl") or about.get("canonicalChannelUrl") or ""
    m = re.search(r"/(@[^/?#]+)", van)
    links = []
    for l in about.get("links") or []:
        x = l.get("channelExternalLinkViewModel") or {}
        links.append({"label": (x.get("title") or {}).get("content"), "text": (x.get("link") or {}).get("content")})
    return {"channel_id": cid, "title": meta.get("title"), "handle": unquote(m.group(1)) if m else None,
            "description": meta.get("description") or about.get("description") or "", "links": links,
            "subscribers": about.get("subscriberCountText"), "videos": about.get("videoCountText"),
            "views": about.get("viewCountText"), "joined": (about.get("joinedDateText") or {}).get("content"),
            "country": about.get("country"), "family_safe": meta.get("isFamilySafe")}


REL = re.compile(r"(?:Streamed )?(\d+) (second|minute|hour|day|week|month|year)s? ago", re.I)
UNIT_DAYS = {"second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30.4, "year": 365}


def _hms(t: str) -> int:
    s = 0
    for part in t.split(":"):
        s = s * 60 + int(part)
    return s


def _count(t: str) -> int:
    t = t.replace(",", "")
    mult = 1_000 if t.endswith("K") else 1_000_000 if t.endswith("M") else 1
    return int(float(t.rstrip("KM")) * mult)


def parse_tab(d: dict | None, tab: str) -> list[dict]:
    """The public /videos or /streams tab -> items {vid, title, when (datetime|None), relative, duration_s, views, upcoming,
    live_now}. Same lockupViewModel reading as ingest/public/media/feed_build.py yt_tab()."""
    if not d:
        return []
    out = []
    for lv in walk(d, "lockupViewModel"):
        vid = lv.get("contentId")
        if not vid or "VIDEO" not in str(lv.get("contentType", "VIDEO")):
            continue
        md = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
        title = ((md.get("title") or {}).get("content") or "").strip()
        labels = json.dumps(md.get("metadata") or {})
        rel = REL.search(labels)
        when = None
        if rel:
            when = NOW - timedelta(days=int(rel.group(1)) * UNIT_DAYS[rel.group(2).lower()])
        img = json.dumps(lv.get("contentImage") or {})
        dur = re.search(r'"thumbnailBadgeViewModel": \{"text": "(\d+(?::\d{2}){1,2})"', img)
        views = re.search(r'"accessibilityLabel": "([\d,.]+[KM]?) (?:views|watching)"', labels)
        live_now = bool(re.search(r"\bwatching\b", labels)) or '"LIVE"' in img or "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE" in img
        upcoming = bool(re.search(r"UPCOMING|Scheduled for|waiting", labels + img, re.I)) and not live_now
        sch = re.search(r"Scheduled for (\d{1,2})/(\d{1,2})/(\d{2}), (\d{1,2}):(\d{2}) (AM|PM)", labels)
        if sch and upcoming:
            mo, dd, yy, hh, mi, ap = int(sch.group(1)), int(sch.group(2)), int(sch.group(3)), int(sch.group(4)), int(sch.group(5)), sch.group(6)
            hh = hh % 12 + (12 if ap == "PM" else 0)
            try:
                when = datetime(2000 + yy, mo, dd, hh, mi, tzinfo=timezone.utc)  # the viewer's local clock: only the day is reliable
            except ValueError:
                pass
        out.append({"vid": vid, "title": title, "when": when, "relative": rel.group(0) if rel else None, "tab": tab,
                    "duration_s": _hms(dur.group(1)) if dur else None, "views": _count(views.group(1)) if views else None,
                    "upcoming": upcoming, "live_now": live_now})
    return out
