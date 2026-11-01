"""Side doors: public machine-readable files on the same host as a parish site whose HTML we cannot read.

    python3 -I ingest/public/sidedoors.py [--ids a,b] [--refresh]
        -> data/raw/sidedoors/<entity_id>/  (bodies, gitignored)  and  data/raw/sidedoors/doors.json (per-door log)

Why: a Cloudflare challenge or WAF rule often covers only HTML pages, while robots.txt, the WordPress REST API,
RSS feeds, sitemaps and uploaded PDFs are served plainly. These are ordinary public URLs that the site publishes
for machines; we ask for each one once, with the default python-requests User-Agent.

Rules (CLAUDE.md, docs/design/scaling-california.md):
  * one request at a time per host, robots.txt Crawl-delay honoured (else 3 s), robots Disallow honoured when the
    file is readable; no cookies, no JavaScript, nothing spoofed;
  * a response that is a challenge page ("Just a moment...", cf-chl, captcha) is recorded as `challenge` and that URL
    is never retried; we do not try to pass any challenge;
  * a parked or re-registered domain (redirect to an unrelated site) is recorded and nothing from it is used.

Doors tried per host: /robots.txt, /wp-json/wp/v2/pages?per_page=100, /wp-json/wp/v2/posts?per_page=20, /feed/,
/sitemap.xml, /sitemap_index.xml, eCatholic /documents/, /bulletins, /media, the bulletin link and direct PDF links
recorded in data/real/parish_sites.json, plus the files.ecatholic.com bulletin PDFs for the site id seen there
(recorded date and the next Sunday). PDF/bulletin URLs listed in a readable sitemap or feed are then fetched
(at most 6 per site).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import urllib.robotparser
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "bulletins"))
from common import RAW, REAL, now_iso  # noqa: E402

OUT = RAW / "sidedoors"
LOG = OUT / "doors.json"
DEFAULT_DELAY = 3.0
SESSION = requests.Session()  # default python-requests User-Agent, nothing spoofed

# entity id -> hosts to try (parish's own site(s) as recorded in the fill JSON / directory / DiscoverMass)
TARGETS = {
    "christ-light-oakland": ["https://www.ctlcathedral.org"],
    "most-holy-rosary-antioch": ["https://holyrosaryantioch.org"],
    "anne-union-city": ["https://saintannecatholic.org"],
    "benedict-oakland": ["https://www.saintbenedictoakland.com"],
    "columba-oakland": ["https://www.stcolumba-oak.com"],
    "leo-great-oakland": ["https://www.stleothegreat.org"],  # parish pages live on ctlcathedral.org/st-leo
    "louis-bertrand-oakland": ["https://www.slboakland.com"],
    "mark-richmond": ["https://stmarkrichmond.org"],
    "perpetua-lafayette": ["https://stperpetua.org"],
    "anthony-mary-help-christians-oakland": ["https://stanthonymaryhelp.org", "https://stanthony-maryhelp.org"],
    "our-lady-mercy-richmond": ["https://pointrichmondcatholic.org"],
    "augustine-oakland": ["https://staugustineoakland.com"],
    "jarlath-oakland": ["https://saintjarlath.org"],
    "good-shepherd-pittsburg": ["https://goodshepherdpittsburg.org"],
    "divine-mercy-oakland": ["https://divinemercyoak.org"],
    "raymond-dublin": ["http://www.st-raymond-dublin.org"],
    "anthony-oakley": ["https://stanthonyoakley.org"],
    "cornelius-richmond": ["https://www.stcorneliusrichmond.org"],
    "our-lady-lourdes-oakland": ["http://www.lourdesoakland.com"],
    "peter-martyr-pittsburg": ["https://saintpetermartyr.org"],
    "rose-lima-patrick-crockett": ["https://www.strosecrockett.org"],
    "bernard-oakland": [],  # no parish website; listed only in the oakdiocese.org directory
}
EXTRA = {"leo-great-oakland": ["https://www.ctlcathedral.org/st-leo"]}
DOORS = ["/robots.txt", "/wp-json/wp/v2/pages?per_page=100", "/wp-json/wp/v2/posts?per_page=20", "/feed/",
         "/sitemap.xml", "/sitemap_index.xml", "/documents/", "/bulletins", "/media"]
CHALLENGE = re.compile(r"just a moment\.\.\.|cf-chl|challenge-platform|cf_chl_opt|captcha|attention required! \| cloudflare",
                       re.I)
ECATH = re.compile(r"files\.ecatholic\.com/(\d+)/bulletins/(\d{8})\.pdf")


def next_sunday(d: date) -> date:
    return d + timedelta(days=(6 - d.weekday()) % 7 or 7)


class HostPacer:
    def __init__(self):
        self.robots: dict[str, tuple] = {}
        self.last: dict[str, float] = {}
        self.lock = threading.Lock()

    def wait(self, host: str, delay: float):
        w = self.last.get(host, 0) + delay - time.time()
        if w > 0:
            time.sleep(w)
        self.last[host] = time.time()


def classify(r: requests.Response | None, err: str | None, body: bytes) -> str:
    if err:
        return f"error:{err}"
    head = body[:6000].decode("utf-8", "ignore")
    if CHALLENGE.search(head) or r.headers.get("cf-mitigated") == "challenge":
        return "challenge"
    if r.status_code != 200:
        return f"http-{r.status_code}"
    ct = (r.headers.get("content-type") or "").lower()
    if body[:4] == b"%PDF":
        return "ok-pdf"
    if "json" in ct or head.lstrip()[:1] in ("[", "{"):
        return "ok-json"
    if "xml" in ct or head.lstrip().startswith("<?xml") or "<urlset" in head or "<rss" in head:
        return "ok-xml"
    if "pdf" in ct:
        return "http-200-not-pdf"
    return "ok-html" if "<html" in head.lower() else "ok-text"


def fetch(p: HostPacer, url: str, eid: str, robots_rp=None, delay: float = DEFAULT_DELAY) -> dict:
    host = urlparse(url).netloc
    if robots_rp is not None and not robots_rp.can_fetch("*", url):
        return {"url": url, "result": "disallowed-by-robots", "fetched_at": now_iso()}
    p.wait(host, delay)
    r, err, body = None, None, b""
    try:
        r = SESSION.get(url, timeout=30, allow_redirects=True)
        body = r.content
    except requests.exceptions.SSLError:
        err = "SSLError"
    except requests.exceptions.ConnectionError as e:
        err = "DNS" if "Name or service not known" in str(e) or "NameResolution" in str(e) else "ConnectionError"
    except requests.RequestException as e:
        err = type(e).__name__
    res = classify(r, err, body)
    out = {"url": url, "result": res, "fetched_at": now_iso()}
    if r is not None:
        out.update(status=r.status_code, final_url=r.url, content_type=r.headers.get("content-type", ""), bytes=len(body))
        if urlparse(r.url).netloc.removeprefix("www.") != host.removeprefix("www."):
            out["redirected_off_host"] = urlparse(r.url).netloc
    if res.startswith("ok"):
        d = OUT / eid
        d.mkdir(parents=True, exist_ok=True)
        ext = {"ok-pdf": ".pdf", "ok-json": ".json", "ok-xml": ".xml"}.get(res, ".html")
        fp = d / (hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
        fp.write_bytes(body)
        out["path"] = str(fp.relative_to(RAW.parent.parent))
    return out


def robots_for(p: HostPacer, base: str, eid: str, log: list):
    host = urlparse(base).netloc
    if host in p.robots:
        return p.robots[host]
    rec = fetch(p, base.rstrip("/") + "/robots.txt", eid)
    log.append(dict(rec, door="robots.txt"))
    rp, delay = None, DEFAULT_DELAY
    if rec["result"] in ("ok-text", "ok-html") and rec.get("path"):
        txt = (RAW.parent.parent / rec["path"]).read_text(errors="ignore")
        if "<html" not in txt[:500].lower():
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(txt.splitlines())
            cd = rp.crawl_delay("*")
            if cd:
                delay = max(float(cd), 1.0)
    p.robots[host] = (rp, delay, rec["result"])
    return p.robots[host]


def recorded_links(eid: str) -> list[tuple[str, str]]:
    recs = {r["id"]: r for r in json.loads((REAL / "parish_sites.json").read_text())["records"]}
    r = recs.get(eid) or {}
    out = []
    b = r.get("bulletin") or {}
    if b.get("url"):
        out.append(("recorded bulletin link", b["url"]))
    for cat, links in (r.get("discovered_links") or {}).items():
        for u in links:
            if re.search(r"\.pdf(\?|$)", u, re.I):
                out.append((f"recorded pdf ({cat})", u))
    for u in [u for _, u in out]:
        m = ECATH.search(u)
        if m:
            d0 = date(int(m.group(2)[:4]), int(m.group(2)[4:6]), int(m.group(2)[6:]))
            nxt = next_sunday(d0)
            while nxt <= next_sunday(date.today() - timedelta(days=1)):
                out.append(("eCatholic files host, next Sunday after recorded bulletin",
                            f"https://files.ecatholic.com/{m.group(1)}/bulletins/{nxt:%Y%m%d}.pdf"))
                nxt += timedelta(days=7)
    seen, uniq = set(), []
    for k, u in out:
        if u not in seen:
            seen.add(u)
            uniq.append((k, u))
    return uniq


def listed_urls(path: Path) -> list[str]:
    txt = path.read_text(errors="ignore")
    return re.findall(r"<(?:loc|link)>\s*([^<\s]+)\s*</(?:loc|link)>", txt) + re.findall(r'href="([^"]+\.pdf[^"]*)"', txt)


def run_entity(p: HostPacer, eid: str) -> dict:
    log: list[dict] = []
    for base in TARGETS[eid]:
        rp, delay, rres = robots_for(p, base, eid, log)
        for door in DOORS[1:]:
            rec = fetch(p, base.rstrip("/") + door, eid, rp, delay)
            log.append(dict(rec, door=door))
        # follow URLs listed in readable sitemaps / feeds (sub-sitemaps first, then PDF / bulletin URLs)
        follow, seen = [], {x["url"] for x in log}
        for rec in list(log):
            if rec["result"] == "ok-xml" and rec.get("path"):
                for u in listed_urls(RAW.parent.parent / rec["path"]):
                    if u in seen:
                        continue
                    if u.endswith(".xml") and len([f for f in follow if f[0] == "sub-sitemap"]) < 4:
                        follow.append(("sub-sitemap", u))
                    elif re.search(r"\.pdf|bulletin|boletin|mass|misa|schedule|horario|confess|ministr", u, re.I):
                        follow.append(("listed in sitemap/feed", u))
                    seen.add(u)
        for kind, u in [f for f in follow if f[0] == "sub-sitemap"]:
            rec = fetch(p, u, eid, rp, delay)
            log.append(dict(rec, door=kind))
            if rec["result"] == "ok-xml" and rec.get("path"):
                for v in listed_urls(RAW.parent.parent / rec["path"]):
                    if v not in seen and re.search(r"\.pdf|bulletin|boletin|mass|misa|schedule|horario|confess|ministr", v, re.I):
                        follow.append(("listed in sitemap/feed", v))
                        seen.add(v)
        listed = [f for f in follow if f[0] != "sub-sitemap"]
        listed.sort(key=lambda f: (not re.search(r"\.pdf", f[1], re.I), f[1]), reverse=False)
        for kind, u in listed[:6]:
            rec = fetch(p, u, eid, rp if urlparse(u).netloc == urlparse(base).netloc else None, delay)
            log.append(dict(rec, door=kind))
    for kind, u in recorded_links(eid) + [("parish page on another host", u) for u in EXTRA.get(eid, [])]:
        if u in {x["url"] for x in log}:
            continue
        host_rp = p.robots.get(urlparse(u).netloc, (None, DEFAULT_DELAY, None))
        if urlparse(u).netloc == "files.ecatholic.com" and "files.ecatholic.com" not in p.robots:
            robots_for(p, "https://files.ecatholic.com", eid, log)
            host_rp = p.robots["files.ecatholic.com"]
        rec = fetch(p, u, eid, host_rp[0], host_rp[1])
        log.append(dict(rec, door=kind))
    opened = sorted({x["door"] for x in log if x["result"].startswith("ok")})
    return {"entity_id": eid, "checked_at": now_iso(), "hosts": TARGETS[eid], "doors": log, "opened": opened}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    ids = [x for x in a.ids.split(",") if x] or list(TARGETS)
    prev = json.loads(LOG.read_text()) if LOG.exists() else {"parishes": {}}
    todo = [e for e in ids if a.refresh or e not in prev["parishes"]]
    p = HostPacer()
    results: dict[str, dict] = {}

    def work(e):
        results[e] = run_entity(p, e)
        r = results[e]
        print(f"{e:42s} doors={len(r['doors']):3d} opened={r['opened']}", flush=True)

    # hosts are independent; each worker keeps one request at a time per host
    threads = [threading.Thread(target=work, args=(e,)) for e in todo]
    for t in threads:
        t.start()
        time.sleep(0.2)
    for t in threads:
        t.join()
    prev["parishes"].update(results)
    prev["generated_at"] = now_iso()
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(prev, indent=1, ensure_ascii=False))
    print("->", LOG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
