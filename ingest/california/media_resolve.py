"""Step 2 of the California YouTube catalog: resolve every candidate link to a channel and check who owns it.

    python3 -I ingest/california/media_resolve.py fetch     # network: resolve links (1 request / 2 s, cached)
    python3 -I ingest/california/media_resolve.py verify    # offline: ownership check -> channels, unverified, borrowed

fetch
  * channel links (/channel/UC.., /@handle, /c/, /user/, a bare vanity name): the public channel /about page
    (ytInitialData: channel id, title, @handle, description, the links listed under About);
  * single videos: YouTube oEmbed -> the author's channel -> its /about page;
  * playlists: the playlist page's owner -> its /about page.
  /feeds/videos.xml and /results are disallowed by YouTube's robots.txt and never requested; every request waits 2 s;
  the HTTP library's plain default User-Agent. With YOUTUBE_API_KEY set, the Data API v3 (channels.list) is used instead.

verify (ownership; every verdict carries its reasons)
  verified    the channel About links to the parish website, or its description names the website, or its @handle is the
              website's name; or the parish name and the city are in the title/handle/description; or the parish name is in
              the title and the parish's own website (or its directory / DiscoverMass listing) links the channel.
  probable    the parish name matches but nothing else corroborates it; or part of the name plus the city or the parish's
              own link.
  unverified  nothing ties the channel to the parish (kept in the audit list, no videos are collected).
  borrowed    site-builder default, a national or Vatican body (EWTN, Vatican News, ...), another diocese, or a channel whose
              name and city clearly belong to another parish.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from media_common import (CA, EXTRA_DIOCESE_HOSTS, RAW, YouTube, domain_label, extract_yt, fold, is_template_handle, now_iso, parse_about,  # noqa: E402
                          parse_yt_url, reg_domain, squash, toks, walk)

CANDS = RAW / "candidates.json"
INDEX = RAW / "parish_index.json"
RESOLVED = RAW / "resolved.json"
API_KEY = os.environ.get("YOUTUBE_API_KEY")
DIOCESES = {d["slug"]: d for d in json.loads((CA / "dioceses.json").read_text())["dioceses"]}


# ------------------------------------------------------------------------------------------------ fetch
def about_url(link_type: str, key: str, url: str) -> str | None:
    base = url.split("?")[0].rstrip("/")
    if link_type in ("channel_id", "handle", "custom", "user", "vanity"):
        return base + "/about"
    return None


def api_channel(key_type: str, key: str) -> dict | None:
    """YouTube Data API v3 channels.list (used only when YOUTUBE_API_KEY is set). Returns the parse_about() shape.
    Not exercised in the 2026-10-31 build (no key in the environment)."""
    import requests  # noqa: PLC0415
    params = {"part": "snippet,statistics,brandingSettings", "key": API_KEY}
    if key_type == "channel_id":
        params["id"] = key
    elif key_type == "handle":
        params["forHandle"] = key
    elif key_type == "user":
        params["forUsername"] = key.split("/")[-1]
    else:
        return None
    r = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=25)
    if r.status_code != 200 or not r.json().get("items"):
        return None
    it = r.json()["items"][0]
    sn = it.get("snippet") or {}
    return {"channel_id": it["id"], "title": sn.get("title"), "handle": sn.get("customUrl"), "description": sn.get("description") or "",
            "links": [], "subscribers": (it.get("statistics") or {}).get("subscriberCount"),
            "videos": (it.get("statistics") or {}).get("videoCount"), "views": (it.get("statistics") or {}).get("viewCount"),
            "joined": sn.get("publishedAt"), "country": sn.get("country"), "family_safe": None}


def playlist_owner(d: dict | None) -> str | None:
    """The channel id that owns a playlist page (ytInitialData)."""
    if not d:
        return None
    for key in ("ownerText", "videoOwnerRenderer"):
        for o in walk(d, key):
            s = json.dumps(o)
            m = re.search(r'"browseId": "(UC[\w-]{22})"', s)
            if m:
                return m.group(1)
    m = re.search(r'"browseId": "(UC[\w-]{22})"', json.dumps(d))
    return m.group(1) if m else None


def fetch() -> None:
    cands = json.loads(CANDS.read_text())["candidates"]
    yt = YouTube()
    res = json.loads(RESOLVED.read_text()) if RESOLVED.exists() else {"keys": {}, "channels": {}, "videos": {}, "playlists": {}}

    def save() -> None:
        RESOLVED.write_text(json.dumps(res, ensure_ascii=False) + "\n")

    def fetch_about(url: str) -> dict:
        r = yt.get(url)
        about = parse_about(r.get("data"))
        return {"status": r["status"], "final_url": r["final_url"], "error": r["error"], "about": about, "fetched_at": r["fetched_at"]}

    def store_channel(about: dict, fetched_at: str | None) -> None:
        cid = about["channel_id"]
        if cid not in res["channels"]:
            res["channels"][cid] = {**about, "fetched_at": fetched_at, "source": "youtube /about page" if not API_KEY else "YouTube Data API v3"}

    order = {"channel_id": 0, "handle": 1, "custom": 2, "user": 3, "vanity": 4, "video": 5, "playlist": 6}
    seen = set()
    todo = []
    # parishes first (dropped by the clean-up first), then diocese pages and unattributed links
    pri = lambda c: (0 if c.get("cleanup") == "dropped" else 1 if c["owner_type"] == "parish" else 2, order[c["link_type"]])  # noqa: E731
    for c in sorted(cands, key=pri):
        k = f"{c['link_type']}:{c['key']}"
        if k in seen:
            continue
        seen.add(k)
        todo.append(c)
    print(f"{len(todo)} unique links to resolve; {yt.requests_made} requests so far", flush=True)
    n = 0
    for c in todo:
        k = f"{c['link_type']}:{c['key']}"
        if k in res["keys"] and (res["keys"][k].get("channel_id") or res["keys"][k].get("status") in (404, 410)):
            continue
        t, key = c["link_type"], c["key"]
        if yt.stopped:
            print("stopped:", yt.stopped, flush=True)
            break
        if t in ("channel_id", "handle", "custom", "user", "vanity"):
            if API_KEY and t in ("channel_id", "handle", "user"):
                about = api_channel(t, key if t != "handle" else c["url"].rsplit("/", 1)[-1])
                out = {"status": 200 if about else 404, "about": about, "error": None, "fetched_at": now_iso()}
            else:
                out = fetch_about(about_url(t, key, c["url"]))
            about = out.get("about")
            res["keys"][k] = {"channel_id": about["channel_id"] if about else None, "status": out["status"], "error": out["error"],
                              "url": c["url"], "fetched_at": out["fetched_at"]}
            if about:
                store_channel(about, out["fetched_at"])
        elif t == "video":
            r = yt.get(f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={key}", kind="json", max_age_h=24 * 30)
            j = r.get("data") or {}
            v = {"status": r["status"], "error": r["error"], "title": j.get("title"), "author_name": j.get("author_name"),
                 "author_url": j.get("author_url"), "fetched_at": r["fetched_at"], "channel_id": None}
            if j.get("author_url"):
                pl = parse_yt_url(j["author_url"])
                if pl:
                    if pl["type"] == "channel_id":
                        v["channel_id"] = pl["key"]
                    out = fetch_about(pl["url"] + "/about")
                    if out.get("about"):
                        v["channel_id"] = out["about"]["channel_id"]
                        store_channel(out["about"], out["fetched_at"])
            res["videos"][key] = v
            res["keys"][k] = {"channel_id": v["channel_id"], "status": r["status"], "error": r["error"], "url": c["url"], "fetched_at": r["fetched_at"]}
        elif t == "playlist":
            r = yt.get(c["url"])
            cid = playlist_owner(r.get("data"))
            if cid:
                out = fetch_about(f"https://www.youtube.com/channel/{cid}/about")
                if out.get("about"):
                    store_channel(out["about"], out["fetched_at"])
            res["playlists"][key] = {"status": r["status"], "channel_id": cid, "fetched_at": r["fetched_at"]}
            res["keys"][k] = {"channel_id": cid, "status": r["status"], "error": r["error"], "url": c["url"], "fetched_at": r["fetched_at"]}
        n += 1
        if n % 25 == 0:
            save()
            print(f"  {n}/{len(todo)} resolved; {len(res['channels'])} channels; requests {yt.requests_made}, cache hits {yt.cache_hits}", flush=True)
    save()
    print(f"done: {len(res['channels'])} channels; requests {yt.requests_made}, cache hits {yt.cache_hits}, errors {yt.errors}", flush=True)


# ------------------------------------------------------------------------------------------------ verify
SHARED_HOSTS = {"wixsite.com", "weebly.com", "squarespace.com", "godaddysites.com", "google.com", "blogspot.com", "wordpress.com",
                "facebook.com", "parishesonline.com", "ecatholic.com", "yolasite.com", "webs.com", "square.site", "mailchimp.com",
                "flocknote.com", "parishsoft.com", "carrd.co", "wixstudio.com", "myportfolio.com", "business.site",
                "scd.org", "sdcatholic.org", "dsj.org", "lacatholics.org", "sfarch.org", "sfarchdiocese.org", "rcbo.org",
                "dioceseofmonterey.org", "dioceseoffresno.org", "srdiocese.org", "stocktondiocese.org", "sbdcatholics.org",
                "oakdiocese.org", "catholicharities.org", "formed.org", "linktr.ee", "bit.ly", "youtube.com", "instagram.com",
                "twitter.com", "x.com", "tiktok.com", "eventbrite.com", "givecentral.org", "onrealm.org", "subsplash.com"}

NATIONAL = re.compile(
    r"\b(vatican|pontifex|pope\s+(francis|leo)|ewtn|usccb|word\s+on\s+fire|barron|formed\b|catholic\s+answers|hallow\b|"
    r"ascension\s+(press|presents)|augustine\s+institute|catholictv|catholic\s+tv|salt\s*(\+|and|&)?\s*light|relevant\s+radio|"
    r"franciscan\s+media|dynamic\s+catholic|mike\s+schmitz|catholic\s+news\s+agency|our\s+sunday\s+visitor|knights\s+of\s+columbus|"
    r"opus\s+dei|catholic\s+relief\s+services|fulton\s+sheen|national\s+catholic|catholic\s+herald|youtube\s+(movies|music|kids)|"
    r"google|wix\b|squarespace|godaddy|weebly|wordpress|vimeo|jesuit\s+(conference|west)|march\s+for\s+life|world\s+youth\s+day|"
    r"\bmagis\b|\bcbs\b|\bnbc\b|\babc\s+news\b|\bcnn\b|catholic\s+online|aleteia|pillar|\bcna\b|catholic\s+culture|"
    r"sensus\s+fidei|focus\s+(catholic|missionaries)|steubenville|\bnfcym\b|\bncyc\b)", re.I)
DIOCESE_WORD = re.compile(r"\b(arch)?dioceses?\b|\beparchy\b|di[oó]cesis|arquidi[oó]cesis", re.I)
SCHOOL_WORD = re.compile(r"\b(school|academy|escuela|high school|elementary|prep|preparatory|montessori|college|university)\b", re.I)
CHURCH_WORD = re.compile(r"\b(church|parish|catholic|iglesia|parroquia|mass|misa|shrine|mission|cathedral|basilica|chapel)\b", re.I)
PERSON_TITLE = re.compile(r"^(fr\.?|father|padre|bishop|msgr\.?|monsignor|deacon|dcn\.?|sister|sr\.?|brother|br\.?|dr\.?)\s+\w", re.I)
SAINT_WORDS = {"saint", "st", "sts", "saints", "san", "santa", "santo", "sainte", "ste"}


def site_key(u: str | None) -> str | None:
    """Registered domain, or host + first path segment for hosts many parishes share (wixsite.com/stjoe)."""
    if not u:
        return None
    s = u.strip()
    if "://" not in s:
        s = "http://" + s
    from urllib.parse import urlparse  # noqa: PLC0415
    rd = reg_domain(s)
    if not rd:
        return None
    if rd in SHARED_HOSTS:
        p = urlparse(s)
        seg = [x for x in p.path.split("/") if x]
        return (p.hostname or rd).lower().removeprefix("www.") + ("/" + seg[0].lower() if seg else "")
    return rd


def raw_words(name: str, keep_stop: bool = False) -> list[str]:
    from media_common import GENERIC, STOP  # noqa: PLC0415
    t = fold(re.sub(r"\(.*?\)", " ", name or "")).replace("&", " and ").replace("'", "").replace("’", "")
    return [w for w in re.sub(r"[^a-z0-9]+", " ", t).split() if w not in GENERIC and (keep_stop or w not in STOP)]


def name_forms(name: str) -> set[str]:
    forms = set()
    for ks in (False, True):
        rw = raw_words(name, ks)
        if not rw:
            continue
        forms.add("".join(rw))
        for sub in ("st", "saint", "san", "santa"):
            forms.add("".join(sub if w in SAINT_WORDS else w for w in rw))
        forms.add("".join(w for w in rw if w not in SAINT_WORDS))
    return {f for f in forms if len(f) >= 7}


class Parishes:
    """Index of the parishes of the 11 dioceses, for matching."""

    def __init__(self, idx: dict):
        self.by_id = {p["id"]: p for p in idx["parishes"]}
        self.by_dio: dict[str, list[dict]] = collections.defaultdict(list)
        city_ct = collections.Counter()
        for p in idx["parishes"]:
            p["_toks"] = toks(re.sub(r"\(.*?\)", " ", p["name"] or ""))
            p["_forms"] = name_forms(p["name"])
            p["_city"] = squash(p.get("city"))
            p["_domains"] = {site_key(p.get("website"))} | {site_key(s.get("domain")) for s in p["sites"]
                                                            if not s.get("school") and not s.get("diocesan_host")}
            p["_domains"].discard(None)
            self.by_dio[p["diocese"]].append(p)
            if p.get("city"):
                city_ct[fold(p["city"]).strip()] += 1
        self.cities = {c for c in city_ct if len(c) >= 5}


def pre(ch: dict, P: Parishes) -> dict:
    """Per-channel features computed once."""
    title = ch.get("title") or ""
    handle = (ch.get("handle") or "").lstrip("@")
    link_texts = [(l.get("text") or "") for l in ch.get("links") or []]
    ft = " " + fold(re.sub(r"[^A-Za-zÀ-ɏ ]+", " ", title)) + " "
    about_dom = {site_key(t) for t in link_texts if "." in t}
    about_dom.discard(None)
    return {"title": title, "handle": handle, "tt": set(toks(title)), "ts": squash(title), "ht": squash(handle),
            "ds": squash(fold(ch.get("description") or "")[:1200]), "desc": fold(ch.get("description") or "")[:1500],
            "ls": squash(" ".join(link_texts)), "about_dom": about_dom,
            "cities_in_title": {c for c in P.cities if f" {c} " in ft}}


def match_pair(f: dict, p: dict, links: list[dict], P: Parishes, longer_name_hit: str | None = None) -> dict:
    """Evidence that the channel with features `f` is parish p's. -> {parish_id, verdict, score, reasons, checks}."""
    tt, ts, ht, ds = f["tt"], f["ts"], f["ht"], f["ds"]
    ptoks = p["_toks"]
    checks: dict = {}
    reasons: list[str] = []

    hit = f["about_dom"] & p["_domains"]
    if hit:
        checks["about_link_to_site"] = sorted(hit)[0]
        reasons.append(f"channel About links to the parish website ({sorted(hit)[0]})")
    desc_hit = None
    if not hit:
        desc_hit = next((d for d in p["_domains"] if d and "." in d and d.split("/")[0] not in SHARED_HOSTS and d.split("/")[0] in f["desc"]), None)
        if desc_hit:
            checks["description_names_site"] = desc_hit
            reasons.append(f"channel description names the parish website ({desc_hit})")
    labels = {d.split(".")[0].split("/")[0] for d in p["_domains"] if d and d.split("/")[0] not in SHARED_HOSTS}
    labels |= {d.split("/")[1] for d in p["_domains"] if d and "/" in d}
    h_dom = next((lab for lab in labels if len(lab) >= 7 and ht and (lab == ht or (len(ht) >= 7 and (lab in ht or ht in lab)))), None)
    if h_dom:
        checks["handle_is_site_name"] = h_dom
        reasons.append(f"@handle is the website's name ({h_dom})")

    full, how = False, "title"
    if ptoks and set(ptoks) <= tt:
        full = True
    if not full and p["_forms"]:
        fm = next((x for x in p["_forms"] if x in ts or x in ht), None)
        if fm:
            full, how = True, ("title" if fm in ts else "@handle")
    if not full and ptoks:
        ini = "".join(w[0] for w in raw_words(p["name"]))
        if len(ini) >= 2 and len(p["_city"]) >= 4 and (ini + p["_city"] in ht or p["_city"] + ini in ht):
            full, how = True, "initials + city in @handle"
    if full and longer_name_hit:
        full = False
        checks["longer_name"] = longer_name_hit
    rare = [t for t in ptoks if t not in ("saint", "lady", "holy", "mary", "family", "christ", "heart", "joseph", "john", "paul", "peter")]
    part = (not full) and bool(ptoks) and not longer_name_hit and (
        bool(len(set(ptoks) & tt) / len(ptoks) >= 0.5 and ((set(ptoks) & tt) - {"saint"})) or
        any(len(t) >= 5 and (t in ht or t in ts) for t in rare))
    if full:
        checks["name"] = f"full match in {how}"
        reasons.append(f"parish name '{p['name']}' matches the channel {how} '{f['title'] if how == 'title' else f['handle']}'")
    elif part:
        checks["name"] = "partial"
        reasons.append(f"part of the parish name matches the channel title '{f['title']}'")

    csq = p["_city"]
    city_hit = None
    if len(csq) >= 4:
        for where, txt in (("title", ts), ("@handle", ht), ("description", ds), ("About links", f["ls"])):
            if csq in txt:
                city_hit = where
                break
    if city_hit:
        checks["city"] = f"{p['city']} in {city_hit}"
        reasons.append(f"city '{p['city']}' appears in the channel {city_hit}")

    own_site = any(l["owner_id"] == p["id"] and ({s["source"] for s in l["sources"]} & {"site_json", "site_html"}) and not l.get("site_is_school")
                   for l in links)
    listing = any(l["owner_id"] == p["id"] and ({s["source"] for s in l["sources"]} & {"directory", "discovermass"}) for l in links)
    if own_site:
        checks["linked_from"] = "the parish's own website"
        reasons.append("linked from the parish's own website")
    elif listing:
        checks["linked_from"] = "the parish's directory / DiscoverMass listing"
        reasons.append("linked from the parish's directory or DiscoverMass listing")

    other_city = next((c for c in f["cities_in_title"] if c != fold(p.get("city") or "") and c not in fold(p["name"] or "")), None)
    strong = bool(hit or desc_hit or h_dom)
    church_ish = bool(CHURCH_WORD.search(f["title"]) or CHURCH_WORD.search(f["desc"][:300]))
    if strong:
        verdict = "verified"
    elif other_city and not city_hit:
        verdict = "unverified"
        checks["other_city"] = other_city
        reasons.append(f"the title names another city ({other_city})")
    elif full and (city_hit or own_site or listing):
        verdict = "verified"
    elif full:
        verdict = "probable"
    elif part and (city_hit or own_site or listing):
        verdict = "probable"
    elif city_hit and (own_site or listing) and church_ish:
        verdict = "probable"
    else:
        verdict = "unverified"
    score = (100 if strong else 0) + (40 if full else 15 if part else 0) + (25 if city_hit else 0) + (30 if own_site else 15 if listing else 0)
    return {"parish_id": p["id"], "verdict": verdict, "score": score, "reasons": reasons, "checks": checks}


def match_diocese(ch: dict, slug: str) -> dict | None:
    """Is this the diocese's own channel? Title / handle names the diocese, or About / description point at its website."""
    d = DIOCESES[slug]
    title = ch.get("title") or ""
    handle = (ch.get("handle") or "").lstrip("@")
    desc = fold(ch.get("description") or "")[:1500]
    hosts = {site_key(d["website"])} | {site_key(h) for h in EXTRA_DIOCESE_HOSTS.get(slug, [])}
    hosts.discard(None)
    link_dom = {site_key(l.get("text")) for l in ch.get("links") or [] if "." in (l.get("text") or "")}
    reasons, checks = [], {}
    about_hit = link_dom & hosts
    if about_hit:
        checks["about_link_to_diocese_site"] = sorted(about_hit)[0]
        reasons.append(f"channel About links to the diocesan website ({sorted(about_hit)[0]})")
    desc_host = next((h for h in hosts if h and h.split("/")[0] in desc and h.split("/")[0] not in SHARED_HOSTS - set(EXTRA_DIOCESE_HOSTS_FLAT)), None)
    if desc_host and not about_hit:
        checks["description_names_diocese_site"] = desc_host
        reasons.append(f"description names the diocesan website ({desc_host})")
    place = {squash(d["short_name"]), squash(d["see_city"])}
    place.discard("")
    names = squash(title) + "|" + squash(handle)
    word = bool(DIOCESE_WORD.search(title)) or bool(re.search(r"diocese|archdiocese", fold(handle)))
    place_hit = next((x for x in place if x in names), None)
    # "Diocese of San Diego" / "San Diego Catholic" etc.: the diocese word plus its place
    if word and place_hit:
        checks["name"] = f"title/handle names the diocese ({place_hit})"
        reasons.append(f"channel title '{title}' names the diocese of {d['short_name']}")
    elif place_hit and re.search(r"\bcatholics?\b", fold(title)) and not CHURCH_WORD.search(re.sub(r"\bcatholics?\b", "", title)) \
            and not SCHOOL_WORD.search(title):
        checks["name"] = f"title '{title}' is the diocese's name for itself ({place_hit} Catholic)"
        reasons.append(checks["name"])
    if not checks:
        return None
    exact = bool(re.match(r"^\s*(the\s+)?(roman\s+catholic\s+)?(arch)?diocese\s+of\s+", fold(title))) or \
        bool(re.match(rf"^\s*(roman\s+catholic\s+)?(arch)?diocese\s+of\s+", fold(title)))
    verdict = "verified" if (about_hit or desc_host or exact) else "probable"
    return {"verdict": verdict, "reasons": reasons, "checks": checks, "exact_title": exact}


EXTRA_DIOCESE_HOSTS_FLAT = [h for hs in EXTRA_DIOCESE_HOSTS.values() for h in hs]


def verify() -> None:
    cands = json.loads(CANDS.read_text())["candidates"]
    idx = json.loads(INDEX.read_text())
    res = json.loads(RESOLVED.read_text())
    P = Parishes(idx)
    fetched_at = max([v.get("fetched_at") or "" for v in res["channels"].values()] or [""])

    # ---- candidate links -> channel ids
    by_ch: dict[str, list[dict]] = collections.defaultdict(list)
    unresolved = []
    for c in cands:
        k = res["keys"].get(f"{c['link_type']}:{c['key']}") or {}
        cid = k.get("channel_id")
        if c["link_type"] in ("channel_id",) and not cid:
            cid = c["key"] if c["key"] in res["channels"] else None
        if cid and cid in res["channels"]:
            by_ch[cid].append(c)
        else:
            unresolved.append({**c, "resolution": {"status": k.get("status"), "error": k.get("error")}})

    channels, unverified, borrowed, diocese_rows = [], [], [], []
    stats = collections.Counter()
    for cid, links in sorted(by_ch.items()):
        ch = res["channels"][cid]
        f = pre(ch, P)
        link_dioceses = sorted({l["diocese"] for l in links if l["diocese"]})
        linking = sorted({l["owner_id"] for l in links if l["owner_type"] == "parish"})
        link_keys = sorted({l["url"] for l in links})
        ev = [{"type": {"site_json": "org_website_link", "site_html": "org_website_link", "directory": "directory_link",
                        "discovermass": "discovermass_listing", "diocese_page": "diocese_website_link",
                        "diocese_site_html": "diocese_website_link", "diocese_cache": "diocese_cached_page",
                        "diocese_wayback": "wayback_capture", "wayback": "wayback_capture"}.get(s["source"], s["source"]),
               "found_on_owner": l["owner_id"], "url": s.get("ref"), "link": l["url"]}
              for l in links for s in l["sources"][:1]][:6]
        base = {"channel_id": cid, "title": ch.get("title"), "handle": ch.get("handle"),
                "url": f"https://www.youtube.com/{ch['handle']}" if ch.get("handle") else f"https://www.youtube.com/channel/{cid}",
                "linked_as": link_keys[:6], "linked_by": linking, "dioceses_linking": link_dioceses, "evidence": ev,
                "cleanup_139": any(l.get("cleanup") == "dropped" for l in links), "fetched_at": ch.get("fetched_at")}

        # ---- 1. site-builder default / national body / person
        tmpl = any(is_template_handle(l["key"]) for l in links) or is_template_handle(ch.get("handle") or "x")
        if tmpl:
            borrowed.append({**base, "status": "borrowed", "reason": "site-builder or platform default account"})
            stats["borrowed_template"] += 1
            continue
        # ---- 2. a diocese's own channel (or a diocesan office)
        dio_hits = {s: m for s in DIOCESES if (m := match_diocese(ch, s))}
        if dio_hits and not (len(dio_hits) > 1):
            slug, m = next(iter(dio_hits.items()))
            if slug in link_dioceses or not link_dioceses or slug == "oakland":
                diocese_rows.append({**base, "diocese": slug, "diocese_match": m, "status": m["verdict"]})
                stats[f"diocese_{m['verdict']}"] += 1
                continue
            borrowed.append({**base, "status": "borrowed", "reason": f"another diocese's channel ({DIOCESES[slug]['name']})",
                             "diocese_of_channel": slug})
            stats["borrowed_other_diocese"] += 1
            continue
        if NATIONAL.search(f["title"]) or NATIONAL.search(f["handle"]):
            borrowed.append({**base, "status": "borrowed", "reason": f"national, Vatican or publisher channel ('{ch.get('title')}')"})
            stats["borrowed_national"] += 1
            continue
        if PERSON_TITLE.match(f["title"].strip()):
            borrowed.append({**base, "status": "borrowed", "reason": f"a person's own channel ('{ch.get('title')}'): personal accounts are excluded"})
            stats["borrowed_person"] += 1
            continue

        # ---- 3. which parish is it? the linking parishes, plus any parish of the same diocese whose name fits
        pool = {pid: P.by_id[pid] for pid in linking if pid in P.by_id}
        for dio in (link_dioceses or list(P.by_dio)):
            for p in P.by_dio.get(dio, []):
                if p["id"] in pool:
                    continue
                if (set(p["_toks"]) - {"saint"}) & f["tt"] and set(p["_toks"]) <= f["tt"] or any(x in f["ts"] or x in f["ht"] for x in p["_forms"]) \
                        or (f["about_dom"] & p["_domains"]):
                    pool[p["id"]] = p
        results = []
        for pid, p in pool.items():
            longer = None
            for q in P.by_dio[p["diocese"]]:
                if q["id"] != p["id"] and len(q["_toks"]) > len(p["_toks"]) and set(p["_toks"]) < set(q["_toks"]) and set(q["_toks"]) <= f["tt"]:
                    longer = q["name"]
                    break
            results.append(match_pair(f, p, links, P, longer))
        results.sort(key=lambda r: -r["score"])
        good = [r for r in results if r["verdict"] in ("verified", "probable")]
        if good:
            top = good[0]
            # ties between parishes with the same strength and no link to either -> ambiguous
            tied = [r for r in good if r["score"] == top["score"] and r["parish_id"] != top["parish_id"]]
            if tied and not (top["parish_id"] in linking):
                unverified.append({**base, "status": "unverified", "reason": f"the name fits {len(tied) + 1} parishes equally ({P.by_id[top['parish_id']]['name']} ...)",
                                   "candidates": [r["parish_id"] for r in [top] + tied][:6]})
                stats["unverified_ambiguous"] += 1
                continue
            p = P.by_id[top["parish_id"]]
            is_school = bool(SCHOOL_WORD.search(f["title"])) and not SCHOOL_WORD.search(p["name"] or "")
            rec = {**base, "status": top["verdict"], "reasons": top["reasons"], "checks": top["checks"],
                   "owner_id": p["id"], "owner_name": p["name"], "city": p.get("city"), "diocese": p["diocese"],
                   "owner_type": "school" if is_school else "parish", "also_linked_by": [x for x in linking if x != p["id"]],
                   "parish_kind": p.get("kind")}
            channels.append(rec)
            stats[f"parish_{top['verdict']}"] += 1
            # parishes that linked it but are not the owner: borrowed
            for other in linking:
                if other != p["id"] and not any(r["parish_id"] == other and r["verdict"] in ("verified", "probable") for r in results):
                    borrowed.append({**base, "status": "borrowed", "reason": f"belongs to {p['name']} ({p.get('city')}), not to the parish that links it",
                                     "linked_by": [other], "belongs_to": p["id"]})
                    stats["borrowed_other_parish"] += 1
            continue
        # nothing ties it to a parish
        top = results[0] if results else None
        if linking:
            unverified.append({**base, "status": "unverified",
                               "reason": "; ".join(top["reasons"]) if top and top["reasons"] else
                               f"the channel name '{ch.get('title')}' does not match the parish name, city or website",
                               "diocese": link_dioceses[0] if link_dioceses else None})
            stats["unverified"] += 1
        else:
            unverified.append({**base, "status": "unverified", "reason": "found on a diocese page or cache; no parish of the diocese matches it",
                               "diocese": link_dioceses[0] if link_dioceses else None})
            stats["unverified_unattributed"] += 1

    # ---- the channels linked by 4+ parishes and not matched to any parish are shared (borrowed) rather than unverified
    out = {"built_at": now_iso(), "counts": dict(stats), "channels": channels, "diocese_channels": diocese_rows,
           "unverified": unverified, "borrowed": borrowed, "unresolved": [
               {"owner_id": u["owner_id"], "diocese": u["diocese"], "link": u["url"], "type": u["link_type"], "resolution": u["resolution"]}
               for u in unresolved]}
    (RAW / "verified.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(out["counts"], indent=1))
    print("channels:", len(channels), "diocese rows:", len(diocese_rows), "unverified:", len(unverified), "borrowed:", len(borrowed),
          "unresolved:", len(unresolved))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        fetch()
    elif cmd == "verify":
        verify()
