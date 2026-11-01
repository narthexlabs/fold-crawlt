"""Extract public social / video / newsletter / livestream links from every cached HTML page we hold for
diocesan organizations, and attribute each link to the organization whose site it was found on.

Inputs (all local; nothing fetched here):
  data/raw/parish_sites/<parish_id>/*.html      parish crawl (+ home re-crawl, when present)
  data/raw/charities/<dir>/index.json + html    Catholic Charities, SVdP, St. Mary's Center, ...
  data/raw/ecosystem/web/index.json + html      religious institutes, schools, partners
  data/raw/media/<owner>/index.json + html      fetch_sites.py (schools, orgs, diocese, context bodies)
  data/raw/ecosystem/oakdiocese_posts.json      diocesan WordPress posts (links *mentioned* by the diocese)

Output: data/real/media/_candidates.json  - one row per (owner, channel) with evidence pages.
build_channels.py turns candidates + manual.json + stats.json into channels.json.
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, now_iso  # noqa: E402

ROOT = REAL.parent.parent
OUT = RAW / "media" / "_candidates.json"  # intermediate, gitignored

URL_RX = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>()\\]+""", re.I)

SKIP_PATH = re.compile(
    r"^/(sharer|share|sharer\.php|dialog|plugins|tr|2008|intent|i/|home|login|signup|hashtag|search|"
    r"shareArticle|policies|legal|privacy|terms|about|help|explore|watch\?|embed|feeds|redirect|l\.php|"
    r"photo\.php|events/?$|notes|pages/create|profile\.php$|business|ads|settings|accounts|developers|"
    r"oembed|static|images|js|css|gtag|v\d|api|feed|player|wp-|account)", re.I)

GENERIC_HANDLES = {"", "facebook", "instagram", "youtube", "twitter", "x", "tiktok", "linkedin", "vimeo",
                   "flickr", "ecatholic", "lpiparishesonline", "parishesonline", "wix", "squarespace",
                   "weebly", "wordpress", "share", "home", "intent", "pages", "p", "reel", "watch", "results",
                   "channel", "c", "user", "company", "in", "groups", "events", "photos", "live", "tv",
                   "elementor", "godaddy", "diviengine", "elegantthemes", "onecause", "givelify", "flocknote",
                   "ministrybrands", "faithdirect", "vancopayments", "osvhub", "ourparishnews",
                   "mycatholicdirectory", "thecatholicfoundation", "catholicmediahub"}


def platform_of(url: str) -> tuple[str, str] | None:
    """Return (platform, canonical_key) for a channel-like URL or None."""
    u = html.unescape(unquote(url)).replace("\\/", "/").split('"')[0].split("&quot")[0].rstrip(".,;")
    try:
        p = urlparse(u)
    except ValueError:
        return None
    host = p.netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    host = host[2:] if host.startswith("m.") else host
    path = re.sub(r"/+", "/", p.path)
    segs = [s for s in path.split("/") if s]
    q = p.query

    def ok(h):
        return h and h.lower() not in GENERIC_HANDLES and not SKIP_PATH.match("/" + h + "/")

    if host in ("facebook.com", "fb.com", "web.facebook.com", "business.facebook.com", "fb.me"):
        if segs[:1] == ["groups"] and len(segs) > 1:
            return "facebook_group", "facebook.com/groups/" + segs[1].lower()
        if segs[:1] == ["profile.php"] and "id=" in q:
            pid = re.search(r"id=(\d+)", q)
            return ("facebook", "facebook.com/" + pid.group(1)) if pid else None
        if segs[:1] == ["pages"] and len(segs) >= 3:
            return "facebook", "facebook.com/" + segs[-1].lower()
        if segs[:1] == ["events"]:
            return None
        if segs and ok(segs[0]) and not segs[0].endswith(".php") and not re.fullmatch(r"\d+_\d+", segs[0]):
            return "facebook", "facebook.com/" + segs[0].lower()
        return None
    if host in ("instagram.com", "instagr.am"):
        if segs and ok(segs[0]):
            return "instagram", "instagram.com/" + segs[0].lower()
        return None
    if host in ("youtube.com", "youtu.be", "youtube-nocookie.com", "music.youtube.com"):
        if host == "youtu.be" and segs:
            return "youtube_video", "youtube.com/watch?v=" + segs[0]
        if segs[:1] == ["watch"]:
            v = re.search(r"v=([\w-]{11})", q)
            return ("youtube_video", "youtube.com/watch?v=" + v.group(1)) if v else None
        if segs[:1] in (["embed"], ["live"], ["shorts"], ["v"]) and len(segs) > 1:
            if segs[1] == "live_stream":
                ch = re.search(r"channel=(UC[\w-]{22})", q)
                return ("youtube", "youtube.com/channel/" + ch.group(1)) if ch else None
            if segs[1] == "videoseries":
                return None
            return "youtube_video", "youtube.com/watch?v=" + segs[1][:11]
        if segs and segs[0].startswith("@"):
            return "youtube", "youtube.com/" + segs[0].lower()
        if segs[:1] == ["channel"] and len(segs) > 1:
            return "youtube", "youtube.com/channel/" + segs[1]
        if segs[:1] in (["c"], ["user"]) and len(segs) > 1:
            return "youtube", f"youtube.com/{segs[0]}/{segs[1].lower()}"
        if segs and re.fullmatch(r"UC[\w-]{22}", segs[0]):
            return "youtube", "youtube.com/channel/" + segs[0]
        if segs[:1] == ["playlist"]:
            return None
        if segs and ok(segs[0]) and segs[0] not in ("feeds", "iframe_api", "s", "yts", "t", "subscription_center"):
            return "youtube", "youtube.com/c/" + segs[0].lower()
        return None
    if host in ("twitter.com", "x.com", "mobile.twitter.com"):
        if segs and ok(segs[0]) and segs[0].lower() not in ("i", "share", "hashtag", "intent"):
            return "x", "x.com/" + segs[0].lower()
        return None
    if host == "tiktok.com":
        if segs and segs[0].startswith("@"):
            return "tiktok", "tiktok.com/" + segs[0].lower()
        return None
    if host in ("flickr.com",):
        if segs[:1] in (["photos"], ["people"]) and len(segs) > 1:
            return "flickr", "flickr.com/photos/" + segs[1].lower()
        return None
    if host in ("vimeo.com",):
        if segs and segs[0] == "channels" and len(segs) > 1:
            return "vimeo", "vimeo.com/channels/" + segs[1].lower()
        if segs and segs[0] == "event" and len(segs) > 1:
            return "vimeo_live", "vimeo.com/event/" + segs[1]
        if segs and not segs[0].isdigit() and ok(segs[0]) and segs[0] not in ("api", "upgrade", "log_in"):
            return "vimeo", "vimeo.com/" + segs[0].lower()
        if segs and segs[0].isdigit():
            return "vimeo_video", "vimeo.com/" + segs[0]
        return None
    if host == "player.vimeo.com" and len(segs) >= 2 and segs[0] == "video":
        return "vimeo_video", "vimeo.com/" + segs[1]
    if host.endswith("linkedin.com"):
        if segs[:1] in (["company"], ["school"]) and len(segs) > 1:
            return "linkedin", "linkedin.com/company/" + segs[1].lower().split("&")[0]
        return None
    if host in ("whatsapp.com",) and segs[:1] == ["channel"] and len(segs) > 1:
        return "whatsapp_channel", "whatsapp.com/channel/" + segs[1]
    if host in ("t.me", "telegram.me") and segs and ok(segs[0]) and not segs[0].startswith("+"):
        return "telegram", "t.me/" + segs[0].lower()
    if host.endswith("flocknote.com"):
        sub = host.split(".")[0]
        if sub not in ("flocknote", "www", "app", "help", "cdn", "assets", "new", "rss", "api", "s3"):
            return "flocknote", sub + ".flocknote.com"
        if segs and segs[0].lower() not in ("signup", "login", "about", "pricing", "help", "assets", "group", "note"):
            return "flocknote", "flocknote.com/" + segs[0].lower()
        return None
    # podcasts
    if host == "podcasts.apple.com" and segs:
        m = re.search(r"id(\d+)", path)
        return ("podcast", "podcasts.apple.com/id" + m.group(1)) if m else None
    if host == "open.spotify.com" and segs[:1] == ["show"] and len(segs) > 1:
        return "podcast", "open.spotify.com/show/" + segs[1]
    if host in ("anchor.fm", "podcasters.spotify.com") and segs:
        return "podcast", "anchor.fm/" + segs[-1].lower()
    if host.endswith(("buzzsprout.com", "podbean.com", "libsyn.com", "simplecast.com", "captivate.fm",
                      "transistor.fm", "spreaker.com")):
        return "podcast", host + ("/" + segs[0] if segs and host.split(".")[0] in ("buzzsprout", "spreaker") else "")
    if host == "soundcloud.com" and segs and ok(segs[0]):
        return "soundcloud", "soundcloud.com/" + segs[0].lower()
    # newsletters
    if host.endswith("list-manage.com") and "subscribe" in path:
        uid = re.search(r"id=([0-9a-f]+)", q)
        return "newsletter", host + path + ("?id=" + uid.group(1) if uid else "")
    if host == "eepurl.com" and segs:
        return "newsletter", "eepurl.com/" + segs[0]
    if host.endswith(("constantcontact.com", "constantcontactpages.com", "ctctcdn.com")) and (
            "subscribe" in u.lower() or "signup" in u.lower() or "sl.php" in u.lower() or "/cu/" in u.lower()):
        return "newsletter", host + path
    if host.endswith("substack.com") and host != "substack.com":
        return "newsletter", host
    if host in ("myemail.constantcontact.com",):
        return "newsletter", host + path
    # livestream hosts
    if host.endswith(("boxcast.tv", "boxcast.com", "churchstreaming.tv", "streamspot.com", "livestream.com",
                      "resi.io", "dacast.com", "ustream.tv", "churchonline.org", "sermonaudio.com")):
        if host.endswith("resi.io"):
            return "livestream_other", "resi.io"
        return "livestream_other", host + ("/" + "/".join(segs[:2]) if segs else "")
    if host in ("catholicvoiceoakland.org",):
        return "catholic_voice", host
    return None


def domain(u: str | None) -> str | None:
    if not u:
        return None
    try:
        h = urlparse(u if "//" in u else "https://" + u).netloc.lower()
    except ValueError:
        return None
    return h[4:] if h.startswith("www.") else h


def owner_index() -> tuple[dict, dict]:
    """domain -> owner_id, owner_id -> {name,type}"""
    dom, meta = {}, {}
    ents = json.loads((ROOT / "data" / "seed" / "entities.json").read_text())["entities"]
    for e in ents:
        meta[e["id"]] = {"name": e["name"], "type": e["type"]}
        d = domain(e.get("website"))
        if d and e["type"] in ("parish", "school", "diocese", "charity", "mission") and d not in dom:
            dom[d] = e["id"]
    for o in json.loads((REAL / "ecosystem" / "orgs.json").read_text())["records"]:
        meta.setdefault(o["id"], {"name": o["name"], "type": o["type"]})
        d = domain(o.get("website"))
        if d and d not in dom:
            dom[d] = o["id"]
    for p in json.loads((REAL / "parishes_directory.json").read_text())["records"]:
        meta.setdefault(p["id"], {"name": p["name"], "type": "parish"})
        d = domain(p.get("website"))
        if d:
            dom.setdefault(d, p["id"])
    dom["oakdiocese.org"] = "dio-oakland"
    dom["cceb.org"] = "cceb"
    meta.update({
        "dio-oakland": {"name": "Diocese of Oakland", "type": "diocese"},
        "ctx-usccb": {"name": "United States Conference of Catholic Bishops", "type": "context"},
        "ctx-cacatholic": {"name": "California Catholic Conference", "type": "context"},
        "dm-cem": {"name": "Catholic Funeral & Cemetery Services, Diocese of Oakland", "type": "diocesan_ministry"},
        "med-catholic-voice": {"name": "The Catholic Voice", "type": "media"},
    })
    dom.update({"usccb.org": "ctx-usccb", "cacatholic.org": "ctx-cacatholic", "cfcsoakland.org": "dm-cem"})
    return dom, meta


def iter_pages(dom):
    """yield (owner_id, page_url, html_path, fetched_at)"""
    ps = json.loads((REAL / "parish_sites.json").read_text())["records"]
    for r in ps:
        for pg in r.get("pages") or []:
            f = Path(pg.get("raw_file") or "")
            if not f.is_absolute():
                f = ROOT / f if (ROOT / f).exists() else ROOT / "data" / f  # residential crawl stores 'raw/...'
            if f.exists():
                yield r["id"], pg["url"], f, r.get("crawled_at")
    # any other html in parish dirs (e.g. re-crawl files not yet listed)
    seen = {str(Path(pg.get("raw_file") or "")) for r in ps for pg in r.get("pages") or []}
    for d in (RAW / "parish_sites").glob("*/"):
        for f in d.glob("*.html"):
            if str(f) not in seen:
                yield d.name, None, f, None
    for idx in (RAW / "charities").glob("*/index.json"):
        j = json.loads(idx.read_text())
        owner = dom.get(domain(j["home"]))
        for pg in j.get("pages", []):
            tf = pg.get("text_file")
            if tf:
                f = ROOT / tf.replace(".txt", ".html")
                if f.exists():
                    yield owner, pg["url"], f, pg.get("fetched_at")
    web = RAW / "ecosystem" / "web"
    if (web / "index.json").exists():
        for key, v in json.loads((web / "index.json").read_text()).items():
            f = web / (key + ".html")
            if f.exists():
                yield dom.get(domain(v["url"])) or dom.get(domain(v.get("final_url"))), v["url"], f, v.get("fetched_at")
    for idx in (RAW / "media").glob("*/index.json"):
        j = json.loads(idx.read_text())
        for pg in j["pages"]:
            if pg.get("raw_file"):
                owner = dom.get(domain(pg["url"])) or dom.get(domain(pg.get("final_url"))) or j["owner_id"]
                if j["owner_id"] in ("med-catholic-voice", "christ-light-oakland", "dm-cyo"):
                    owner = j["owner_id"]
                yield owner, pg["url"], ROOT / pg["raw_file"], pg["fetched_at"]


def main():
    dom, meta = owner_index()
    cands: dict[tuple, dict] = {}
    seen_on = defaultdict(set)
    for owner, page_url, f, fetched in iter_pages(dom):
        if not owner:
            continue
        text = f.read_text(errors="ignore")
        for m in URL_RX.findall(text):
            r = platform_of(m)
            if not r:
                continue
            plat, key = r
            seen_on[key].add(owner)
            c = cands.setdefault((owner, key), {"owner_id": owner, "owner_name": meta.get(owner, {}).get("name"),
                                                "owner_type": meta.get(owner, {}).get("type"), "platform": plat,
                                                "key": key, "raw_urls": set(), "evidence_pages": set(),
                                                "fetched_at": fetched})
            c["raw_urls"].add(html.unescape(m.replace("\\/", "/"))[:300])
            c["evidence_pages"].add(page_url or str(f.relative_to(ROOT)))
    # diocesan WordPress posts: links the diocese *mentions* (not attributed; used for discovery/manual review)
    mentions = defaultdict(set)
    posts = RAW / "ecosystem" / "oakdiocese_posts.json"
    if posts.exists():
        for it in json.loads(posts.read_text()).get("items", []):
            for m in URL_RX.findall(it.get("content", {}).get("rendered", "")):
                r = platform_of(m)
                if r:
                    mentions[r[1]].add(it.get("link"))
    rows = []
    for c in cands.values():
        c["raw_urls"] = sorted(c["raw_urls"])[:5]
        c["evidence_pages"] = sorted(c["evidence_pages"])[:5]
        c["seen_on_owners"] = sorted(seen_on[c["key"]])
        rows.append(c)
    rows.sort(key=lambda r: (r["owner_id"], r["platform"], r["key"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": now_iso(), "count": len(rows), "candidates": rows,
                               "diocesan_post_mentions": {k: sorted(v)[:5] for k, v in sorted(mentions.items())}},
                              indent=1) + "\n")
    print(len(rows), "candidates;", len({r["owner_id"] for r in rows}), "owners;", len(mentions), "diocesan mentions")


if __name__ == "__main__":
    main()
