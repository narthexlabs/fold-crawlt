"""Side doors for a site whose HTML pages answer with a challenge / 403 (sandbox).

Reuses ingest/public/sidedoors.py (HostPacer, fetch, classify): public machine-readable files a site publishes for machines
(robots.txt, WordPress REST pages, feed, sitemaps). Every door is asked once, with the default User-Agent; a challenge on a
door is a stop for that door, and three refusals in a row mean the whole site is behind the challenge, so we stop there
(we do not keep knocking). Nothing is solved or bypassed.

Bodies go to data/raw/california/sites/<key>/sidedoors/ and a log to <key>/sidedoors.json.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "public"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sidedoors as sd  # noqa: E402

RAW = ROOT / "data" / "raw" / "california" / "sites"
DOORS = ["/wp-json/wp/v2/pages?per_page=100", "/feed/", "/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml",
         "/wp-json/wp/v2/posts?per_page=20"]
REFUSED = ("challenge", "http-403", "http-429", "http-503", "http-401")
BULLETIN_URL = re.compile(r"\.pdf|bulletin|boletin", re.I)


def fetch_door(pacer, url: str, d: Path, rp, delay: float) -> dict:
    """One plain GET of a public machine-readable file (urllib, the plain default User-Agent, same client as the crawl)."""
    import hashlib
    import fetch_site as fs
    host = urlparse(url).netloc
    if rp is not None and not rp.can_fetch("*", url):
        return {"url": url, "result": "disallowed-by-robots", "fetched_at": sd.now_iso()}
    pacer.wait(host, delay)
    resp = fs.http_get(url, timeout=20, max_bytes=1_500_000)
    body, cls = resp["body"], fs.classify(resp)
    head = body[:600].decode("utf-8", "ignore").lstrip().lower()
    if resp["err"]:
        res = f"error:{resp['err']}"
    elif cls == "challenge":
        res = "challenge"
    elif resp["status"] != 200:
        res = f"http-{resp['status']}"
    elif body[:4] == b"%PDF":
        res = "ok-pdf"
    elif head[:1] in ("[", "{"):
        res = "ok-json"
    elif head.startswith("<?xml") or "<urlset" in head or "<rss" in head or "<feed" in head or "<sitemapindex" in head:
        res = "ok-xml"
    else:
        res = "ok-html" if "<html" in head else "ok-text"
    out = {"url": url, "result": res, "fetched_at": sd.now_iso(), "status": resp["status"], "bytes": len(body)}
    if urlparse(resp["final_url"]).netloc.removeprefix("www.") != host.removeprefix("www."):
        out["redirected_off_host"] = urlparse(resp["final_url"]).netloc
        return out
    if res.startswith("ok"):
        d.mkdir(parents=True, exist_ok=True)
        ext = {"ok-pdf": ".pdf", "ok-json": ".json", "ok-xml": ".xml"}.get(res, ".html")
        fp = d / (hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
        fp.write_bytes(body)
        out["path"] = str(fp.relative_to(ROOT))
    return out


def run(key: str, home: str, pacer: "sd.HostPacer") -> dict:
    base = f"{urlparse(home).scheme}://{urlparse(home).netloc}"
    d = RAW / key.replace("/", "__")
    bodies = d / "sidedoors"
    log: list[dict] = []
    rp, delay = None, 2.0
    mp = d / "manifest.json"
    if mp.exists():
        txt = (json.loads(mp.read_text()).get("robots") or {}).get("text")
        if txt:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(txt.splitlines())
            cd = rp.crawl_delay("*")
            delay = max(2.0, min(float(cd or 0), 30.0))
    refused = 0
    for door in DOORS:
        rec = fetch_door(pacer, base + door, bodies, rp, delay)
        log.append(dict(rec, door=door))
        if rec["result"] in REFUSED:
            refused += 1
            if refused >= 3:
                log.append({"door": "stop", "result": "three refusals in a row: site is behind a challenge; stopped"})
                break
        elif rec["result"].startswith("ok"):
            refused = 0
    follow, seen = [], {x.get("url") for x in log}
    for rec in list(log):
        if rec.get("result") == "ok-xml" and rec.get("path"):
            for u in sd.listed_urls(ROOT / rec["path"]):
                if u not in seen and re.search(r"bulletin|boletin", u, re.I):
                    follow.append(u)
                seen.add(u)
    out = {"key": key, "checked_at": sd.now_iso(), "doors": log,
           "opened": sorted({x["door"] for x in log if str(x.get("result", "")).startswith("ok")}),
           "bulletin_urls_listed": follow[:5]}
    d.mkdir(parents=True, exist_ok=True)
    (d / "sidedoors.json").write_text(json.dumps(out, indent=1))
    return out


def run_many(items: list[dict], workers: int = 8, log=print) -> dict:
    import concurrent.futures as cf
    pacer = sd.HostPacer()
    res = {}

    def one(it):
        try:
            r = run(it["key"], it["home"], pacer)
        except Exception as e:  # noqa: BLE001
            r = {"key": it["key"], "error": repr(e)[:120], "opened": []}
        res[it["key"]] = r
        log(f"  sidedoor {it['key']:40s} opened={r.get('opened')}")
        return r

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, items))
    return res


def pseudo_pages(key: str, home: str):
    """Pages rebuilt from WordPress REST page content: [(kind, url, title, html)]."""
    d = RAW / key.replace("/", "__")
    log = d / "sidedoors.json"
    if not log.exists():
        return []
    out = []
    for rec in json.loads(log.read_text()).get("doors", []):
        if rec.get("door") == "/wp-json/wp/v2/pages?per_page=100" and rec.get("result") == "ok-json" and rec.get("path"):
            try:
                arr = json.loads((ROOT / rec["path"]).read_text(errors="ignore"))
            except ValueError:
                continue
            for p in arr if isinstance(arr, list) else []:
                title = re.sub(r"<[^>]+>", " ", (p.get("title") or {}).get("rendered", ""))
                html = (p.get("content") or {}).get("rendered", "")
                hay = f"{title} {p.get('slug', '')}".lower()
                kind = ("mass_times" if re.search(r"mass|schedule|confession|reconcil|adoration|sacrament|horario|misa|liturg", hay) else
                        "ministries" if re.search(r"ministr|group|societ|organi[sz]ation|involve|apostolate|grupo|serve|formation", hay) else
                        "bulletin" if re.search(r"bulletin|boletin", hay) else
                        "calendar" if re.search(r"calendar|event", hay) else "contact" if "contact" in hay else "other")
                if html and kind != "other":
                    out.append((kind, p.get("link") or home, title.strip(), html, rec.get("fetched_at")))
    return out
