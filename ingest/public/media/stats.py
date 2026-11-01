"""Public, no-login activity metadata for video channels.

* YouTube channel: the public channel page (title, externalId, "N subscribers", "N videos") and its public
  /videos and /streams tabs (titles + relative publish times, e.g. "Streamed 3 days ago"). YouTube's
  robots.txt disallows /feeds/videos.xml and /results, so neither the RSS feed nor search is used. Embedded/linked single videos are resolved to their channel with YouTube oEmbed.
* Vimeo: oEmbed for single videos (-> author account); the public RSS /<user>/videos/rss for accounts.
* Facebook / Instagram / X / TikTok / LinkedIn: NOT fetched. Their public pages sit behind login walls or
  terms that bar automated collection, so follower counts stay null ("not collected").

Polite: common.UA, 1.2 s between requests, cache in data/raw/media/_stats_cache/ (gitignored).
Input:  data/real/media/_candidates.json + manual.json (extra channel keys)
Output: data/real/media/_stats.json  {key: {...}}  and  _video_resolution.json {video_key: {...}}
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, get, now_iso  # noqa: E402

MEDIA = REAL / "media"
CACHE = RAW / "media" / "_stats_cache"
CACHE.mkdir(parents=True, exist_ok=True)


def cached(url: str, max_age_h: float = 24) -> tuple[int | None, str]:
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")
    if f.exists() and (time.time() - f.stat().st_mtime) < max_age_h * 3600:
        st, _, body = f.read_text(errors="ignore").partition("\n")
        return (int(st) if st.isdigit() else None), body
    r = get(url, timeout=20)
    time.sleep(1.2)
    st = r.status_code if r is not None else None
    body = r.text if r is not None else ""
    f.write_text(f"{st}\n{body}")
    return st, body


# --------------------------------------------------------------- language & livestream heuristics
LANG_HINTS = {
    "es": r"\b(misa|domingo|semana|homil[ií]a|señor|virgen|guadalupe|en español|parroquia|adoraci[oó]n|bautismo|cuaresma)\b",
    "vi": r"[ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđ]|\b(thánh lễ|chúa nhật)\b",
    "tl": r"\b(simbang gabi|tagalog|misa de gallo|pinoy|filipino)\b",
    "pt": r"\b(missa|português|portugues|brasil)\b",
    "ko": r"[가-힣]",
    "zh": r"[一-鿿]",
    "la": r"\b(latin mass|missa cantata|tridentine|extraordinary form|usus antiquior)\b",
    "ig": r"\b(igbo)\b",
    "to": r"\b(tongan|lotu)\b",
}
LIVE_RX = re.compile(r"\b(live|livestream|live stream|en vivo|mass|misa|thánh lễ|liturgy|sunday \d|vigil)\b", re.I)


def langs_of(titles: list[str]) -> list[str]:
    found = []
    blob = " \n ".join(titles)
    for code, rx in LANG_HINTS.items():
        if re.search(rx, blob, re.I):
            found.append(code)
    # English if any title looks English
    if any(re.search(r"\b(the|of|and|sunday|mass|homily|week)\b", t, re.I) for t in titles):
        found.insert(0, "en")
    return found


def activity(dates: list[str]) -> dict:
    ds = sorted(datetime.fromisoformat(d.replace("Z", "+00:00")) for d in dates if d)
    if not ds:
        return {"last_post": None, "posts_per_month_est": None, "status": "no_public_posts"}
    now = datetime.now(timezone.utc)
    span_days = max((ds[-1] - ds[0]).days, 1)
    age = (now - ds[-1]).days
    last90 = sum(1 for d in ds if (now - d).days <= 90)
    # the sample is capped (15 per tab), so a full sample understates frequency; say so
    per_month = round(last90 / 3, 1)
    status = "active" if age <= 45 else "occasional" if age <= 180 else "dormant"
    return {"last_post": ds[-1].date().isoformat(), "first_in_sample": ds[0].date().isoformat(),
            "sample_size": len(ds), "posts_last_90d_in_sample": last90, "posts_per_month_est": per_month, "days_since_last_post": age,
            "status": status}


# --------------------------------------------------------------- YouTube
def yt_channel(key: str) -> dict:
    url = "https://www." + key
    st, body = cached(url)
    out = {"page_status": st, "page_url": url}
    m = re.search(r'"externalId":"(UC[\w-]{22})"', body) or re.search(r'"channelId":"(UC[\w-]{22})"', body)
    if not m:
        return out
    cid = m.group(1)
    out["channel_id"] = cid
    t = re.search(r'<meta property="og:title" content="([^"]*)"', body)
    out["title"] = t.group(1) if t else None
    h = re.search(r'"canonicalBaseUrl":"/(@[^"]+)"', body) or re.search(r'"vanityChannelUrl":"http://www.youtube.com/(@[^"]+)"', body)
    out["handle"] = h.group(1) if h else None
    s = re.search(r'"([\d.,]+[KM]?) subscribers?"', body) or re.search(r'([\d.,]+[KM]?) subscribers?', body)
    out["subscribers"] = s.group(1) if s else None
    v = re.search(r'(\d[\d,.]*[KM]?) videos\b', body) or re.search(r'(1) video\b', body)
    out["videos"] = v.group(1) if v else None
    d = re.search(r'<meta property="og:description" content="([^"]*)"', body)
    out["description"] = (d.group(1)[:300] if d else None)
    out["has_live_tab_hint"] = '"/streams"' in body or "/streams\"" in body
    entries = []
    for tab in ("videos", "streams"):
        st2, tb = cached(f"https://www.youtube.com/channel/{cid}/{tab}")
        out[f"{tab}_tab_status"] = st2
        items = tab_items(tb, tab)
        out[f"recent_{tab}"] = items[:15]
        entries += items
    titles = [e["title"] for e in entries]
    out["activity"] = activity([e["published_approx"] for e in entries if e.get("published_approx")])
    out["languages_in_titles"] = langs_of(titles)
    streams = out.get("recent_streams") or []
    out["livestreams"] = {"count_in_sample": len(streams),
                          "last_stream": max((e["published_approx"] for e in streams if e.get("published_approx")), default=None),
                          "mass_titles_in_sample": sum(1 for e in streams if LIVE_RX.search(e["title"]))}
    return out


REL_RX = re.compile(r"(?:Streamed )?(\d+) (second|minute|hour|day|week|month|year)s? ago", re.I)
UNIT_DAYS = {"second": 0, "minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30.4, "year": 365}


def tab_items(body: str, tab: str) -> list[dict]:
    """Parse the public /videos or /streams tab: title + relative publish time ('Streamed 3 days ago')."""
    items = []
    for m in re.finditer(r'"lockupMetadataViewModel":\{"title":\{"content":"((?:[^"\\]|\\.)*)"\}(.{0,900}?)(?:"menuButton"|"contentId")', body):
        title = json.loads('"' + m.group(1) + '"')
        rel = REL_RX.search(m.group(2))
        when = None
        if rel:
            days = int(rel.group(1)) * UNIT_DAYS[rel.group(2).lower()]
            when = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).isoformat()
        views = re.search(r'"accessibilityLabel":"([\d,.KM]+) views?"', m.group(2))
        items.append({"title": title, "relative": rel.group(0) if rel else None, "published_approx": when,
                      "views": views.group(1) if views else None, "tab": tab})
    return items


def yt_oembed(video_key: str) -> dict:
    st, body = cached("https://www.youtube.com/oembed?format=json&url=https://www." + video_key, max_age_h=24 * 30)
    if st != 200:
        return {"status": st}
    j = json.loads(body)
    return {"status": 200, "title": j.get("title"), "author_name": j.get("author_name"),
            "author_url": j.get("author_url")}


# --------------------------------------------------------------- Vimeo
def vimeo_oembed(video_key: str) -> dict:
    st, body = cached("https://vimeo.com/api/oembed.json?url=https://" + video_key, max_age_h=24 * 30)
    if st != 200:
        return {"status": st}
    j = json.loads(body)
    return {"status": 200, "title": j.get("title"), "author_name": j.get("author_name"),
            "author_url": j.get("author_url")}


def vimeo_account(key: str) -> dict:
    st, body = cached("https://" + key + "/videos/rss")
    out = {"page_status": st}
    if st != 200 or "<rss" not in body[:300]:
        return out
    root = ET.fromstring(body)
    ch = root.find("channel")
    out["title"] = (ch.findtext("title") or "").replace("Vimeo / ", "").replace("’s videos", "")
    items = [{"title": i.findtext("title"), "published": i.findtext("pubDate")} for i in ch.findall("item")]
    dates = []
    for i in items:
        try:
            dates.append(datetime.strptime(i["published"], "%a, %d %b %Y %H:%M:%S %z").isoformat())
        except (TypeError, ValueError):
            pass
    out["recent"] = items[:15]
    out["activity"] = activity(dates)
    out["languages_in_titles"] = langs_of([i["title"] or "" for i in items])
    out["rss_url"] = "https://" + key + "/videos/rss"
    return out


def main():
    cands = json.loads((RAW / "media" / "_candidates.json").read_text())["candidates"]
    manual = json.loads((MEDIA / "manual.json").read_text()) if (MEDIA / "manual.json").exists() else {}
    keys = {(c["platform"], c["key"]) for c in cands}
    for m in manual.get("add", []):
        keys.add((m["platform"], m["key"]))
    vids = {}
    for plat, key in sorted(keys):
        if plat == "youtube_video":
            vids[key] = yt_oembed(key)
        elif plat == "vimeo_video":
            vids[key] = vimeo_oembed(key)
    (RAW / "media" / "_video_resolution.json").write_text(json.dumps(vids, indent=1, ensure_ascii=False) + "\n")
    # channels: candidates + channels that embedded videos resolve to
    for v in vids.values():
        au = (v.get("author_url") or "").replace("https://www.", "").replace("https://", "")
        if au.startswith("youtube.com/"):
            keys.add(("youtube", au.lower() if "/@" in au else au))
        elif au.startswith("vimeo.com/") and not au.split("/")[-1].isdigit():
            keys.add(("vimeo", au.lower()))
    stats = {}
    for plat, key in sorted(keys):
        if plat == "youtube":
            stats[key] = yt_channel(key)
        elif plat == "vimeo" and not key.startswith("vimeo.com/channels/"):
            stats[key] = vimeo_account(key)
        else:
            continue
        stats[key]["fetched_at"] = now_iso()
        print(plat, key, (stats[key].get("activity") or {}).get("status"), stats[key].get("subscribers"), flush=True)
    (RAW / "media" / "_stats.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
