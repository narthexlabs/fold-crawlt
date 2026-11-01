#!/usr/bin/env python3
"""Wayback Machine fetcher for the California parish sites the crawl could not read. Python 3 standard library only.

Runs on Matthew's laptop (web.archive.org is unreachable from the project sandbox). Folder layout (all beside this file):

    wayback_ca_run.py   this runner
    targets.json        built by ingest/california/wayback_ca_targets.py (sites, phase = challenge first)
    progress.json       cooldown / pace state          raw/<slug>/<site>/site.json   one resumable record per site
    raw/<slug>/<site>/  cdx.json.gz (newest capture per URL) + captures (<role>_<ts>_<hash>.html.gz)
    out/batch_NN.zip    finished sites, zipped for the trip back (python3 wayback_ca_run.py zip --min 100)

    python3 wayback_ca_run.py run    [--budget 150] [--pace 4] [--limit N] [--phase challenge|other]
    python3 wayback_ca_run.py status
    python3 wayback_ca_run.py zip    [--min 100] [--all]

Per site (1 CDX request + up to 6 capture requests):
  1. CDX: url=<domain> matchType=domain (a key with a path, e.g. sldm.org/st-anthonys, is matchType=prefix),
     filter=statuscode:200 + mimetype:text/html, from=2024, collapse=urlkey + sort=reverse = the NEWEST capture of every
     URL since 2024-01-01 in one listing (a page without a 2025+ capture falls back to its 2024 capture).
  2. the home page capture; its links + the listing choose: the Mass-times page (1), ministries/groups pages
     (up to 3: ministr, group, organization, get-involved, faith-formation, men, women, young-adult, bible), the bulletin
     page (1). All fetched as raw `id_` content at the exact CDX timestamp.
Links to bulletin PDFs on third-party hosts are read from the stored pages later (wayback_ca_build.py); no PDF is fetched.

Rules: one request at a time, >= 4 s between request starts (redirect hops count); default urllib User-Agent; no
cookies; nothing spoofed; no challenge is ever answered. HTTP 429/503 or a refused/reset connection stops the run and
sets a cool-down (exponential, 60 s .. 600 s); the next run sleeps it out. Every finished request is written at once,
so the run can be stopped anywhere and re-run.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = os.path.join(HERE, "targets.json")
PROGRESS = os.path.join(HERE, "progress.json")
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "out")
MAX_BYTES = 12 * 1024 * 1024
PACE = 4.0
CDX_FROM = "2024"
MAX_MIN_PAGES = 3
_last = [0.0]
T0 = time.time()
BUDGET = [150.0]


class Stop(Exception):
    pass


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def elapsed():
    return time.time() - T0


def jload(p, default):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def jsave(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def pace():
    w = _last[0] + PACE - time.time()
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()


class PacedRedirect(urllib.request.HTTPRedirectHandler):
    """Every redirect hop is a request too: wait the same pace before following it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        pace()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(PacedRedirect())  # default "Python-urllib/3.x" User-Agent


def http_get(url, timeout):
    pace()
    out = {"status": None, "error": None, "body": b"", "final_url": url, "headers": {}}
    try:
        with OPENER.open(urllib.request.Request(url), timeout=timeout) as r:
            out.update(status=r.status, final_url=r.geturl(), headers=dict(r.headers.items()))
            out["body"] = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        out.update(status=e.code, final_url=e.geturl() or url, headers=dict(e.headers.items()) if e.headers else {})
        try:
            out["body"] = e.read(100000)
        except Exception:  # noqa: BLE001
            pass
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        out["error"] = f"{type(e).__name__}: {str(getattr(e, 'reason', e))[:140]}"
        if re.search(r"refused|reset|Errno 10[46]|Errno 111|Connection aborted", out["error"]):
            raise Stop(out["error"], "conn")
    return out


CONSEC = [0]


def pushback(r, prog):
    """Call after every request. A 429/503 backs off (30 s, 60 s, 90 s, 120 s) inside the run; a fourth in a row, or
    no budget left to wait, stops the run with a persisted cool-down. Any other answer resets the count."""
    if r["status"] in (429, 503):
        CONSEC[0] += 1
        wait = min(30 * CONSEC[0], 120)
        if CONSEC[0] >= 4 or elapsed() + wait + 30 > BUDGET[0] + 20:
            raise Stop(f"HTTP {r['status']} x{CONSEC[0]}", "http")
        print(f"  HTTP {r['status']}: backing off {wait} s", flush=True)
        time.sleep(wait)
        return True
    CONSEC[0] = 0
    return False


# ------------------------------------------------------------------------------------------------ url helpers
INDEX_TAIL = re.compile(r"/(?:index|default|home)\.(?:html?|php|aspx?)$", re.I)


def norm(u):
    """Canonical key: host without www, path without trailing slash / index file, query kept, no fragment."""
    p = urlparse(u.strip())
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = INDEX_TAIL.sub("", p.path or "/").rstrip("/") or "/"
    return host + path.lower() + (("?" + p.query) if p.query else "")


def depth(u):
    return len([s for s in urlparse(u).path.split("/") if s])


class LinkScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._a, self.title, self._in_title = [], None, "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._a = {"href": a["href"].strip(), "text": "", "label": (a.get("aria-label") or a.get("title") or "")[:120]}
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self._a["text"] = re.sub(r"\s+", " ", self._a["text"]).strip()[:120]
            self.links.append(self._a)
            self._a = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._a is not None and len(self._a["text"]) < 160:
            self._a["text"] += " " + data
        if self._in_title and len(self.title) < 200:
            self.title += data


# ------------------------------------------------------------------------------------------------ role choice
SKIP_PATH = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|svg|docx?|xlsx?|pptx?|zip|mp3|mp4|ics|css|js)(?:\?|$)|/wp-(?:json|admin|login|content|includes)|"
                       r"/feed/?$|/tag/|/category/|/author/|/\d{4}/\d{2}/|/event/|/events?/(?:category|tag|list|month|day)/|"
                       r"[?&](?:month|date|tribe|eventdate|ical|outlook|replytocom|share|print|s)=|/cart|/checkout|/search|"
                       r"/privacy|/terms|/login|/account|/donate|/giving|/give/|/shop|/store|/blogs?/|/news/[^/]+|/posts?/|/blog-post/|/articles?/[^/]+", re.I)
RX = {
    "mass": re.compile(r"mass(?:es)?[\s_/-]*(?:times?|schedule|hours)|mass[\s_-]*(?:and|&)[\s_-]*confession|horarios?|schedule|"
                       r"(?<![a-z])mass(?:es)?(?![a-z])|(?<![a-z])misas?(?![a-z])|worship|confession|reconcil", re.I),
    "ministries": re.compile(r"ministr|ministerios|(?<![a-z])groups?(?![a-z])|(?<![a-z])grupos(?![a-z])|organi[sz]ation|get[\s_-]*involved|"
                             r"(?<![a-z])involve|faith[\s_-]*formation|(?<![a-z])(?:men|mens|men's|women|womens|women's)(?![a-z])|"
                             r"young[\s_-]*adult|(?<![a-z])bible|apostolate|(?<![a-z])societ(?:y|ies)(?![a-z])|"
                             r"parish[\s_-]*life|(?<![a-z])clubs?(?![a-z])", re.I),
    "bulletin": re.compile(r"bulletin|boletin|bulletn", re.I),
}
PREF = {
    "mass": re.compile(r"mass(?:es)?[\s_-]*(?:times?|schedule|hours)|horarios?[\s_-]*(?:de[\s_-]*)?misas?|mass-?and|"
                       r"(?:worship|liturgy|service|weekend)[\s_-]*(?:times?|schedule)|(?:^|/|\s)(?:schedules?|horarios?)/?$|worship/?$|/masses?/?$|/mass/?$|/misas?/?$", re.I),
    "ministries": re.compile(r"/ministr[a-z]*/?$|ministerios|/groups?/?$|get-involved|/involved|organi[sz]ations|parish-life|faith-formation/?$|"
                             r"/(?:men|women|mens|womens)(?:-[a-z]+)?/?$|young-adult", re.I),
    "bulletin": re.compile(r"bulletins?/?$|boletin", re.I),
}
THRESH = {"mass": 2.0, "ministries": 2.0, "bulletin": 2.0}


NOT_MASS = re.compile(r"baptis|wedding|marriage|funeral|first[\s_-]*communion|confirmation|rcia|quincea|anointing|vocation|school|religious[\s_-]*ed", re.I)


def score(role, path_q, text):
    path = path_q.lower()
    t = (text or "").lower()
    s = 0.0
    if RX[role].search(t):
        s += 2
    if RX[role].search(path):
        s += 1
    if not s:
        return 0.0
    if PREF[role].search((path + " " + t).strip()) or PREF[role].search(path) or (t and PREF[role].search(t)):
        s += 3
    s -= 0.4 * max(0, len([x for x in path.split("?")[0].split("/") if x]) - 1)
    if role == "mass" and NOT_MASS.search(path + " " + t):
        s -= 8
    return s


def choose_pages(index, home_entry, home_links, home_url):
    """index: {norm: (ts, original)}. Returns [{role(s), ts, original}] for mass, ministries (<=3), bulletin (1)."""
    cand = {}
    for n, (ts, orig) in index.items():
        if SKIP_PATH.search(orig):
            continue
        cand[n] = {"ts": ts, "original": orig, "texts": []}
    base = home_entry[1] if home_entry else home_url
    for a in home_links:
        href = urljoin(base, a["href"])
        if not href.startswith("http"):
            continue
        n = norm(href.split("#")[0])
        if n in cand:
            cand[n]["texts"].append(a["text"] or a["label"])
    home_n = norm(home_entry[1]) if home_entry else None
    picks = {}
    for role, quota in (("mass", 1), ("ministries", MAX_MIN_PAGES), ("bulletin", 1)):
        scored = []
        for n, c in cand.items():
            if n == home_n:
                continue
            sc = max([score(role, urlparse(c["original"]).path + (("?" + urlparse(c["original"]).query) if urlparse(c["original"]).query else ""), tx)
                      for tx in (c["texts"] or [""])])
            if sc >= THRESH[role]:
                scored.append((sc, ts_key(c["ts"]), n))
        scored.sort(reverse=True)
        picks[role] = [n for _, _, n in scored[:quota]]
    plan, seen = [], {}
    for role in ("mass", "ministries", "bulletin"):
        for n in picks[role]:
            if n in seen:
                seen[n]["roles"].append(role)
                continue
            item = {"roles": [role], "ts": cand[n]["ts"], "original": cand[n]["original"]}
            seen[n] = item
            plan.append(item)
    return plan


def ts_key(ts):
    return int(ts) if str(ts).isdigit() else 0


# ------------------------------------------------------------------------------------------------ one site
def site_dir(t):
    return os.path.join(RAW, t["slug"], t["key"].replace("/", "__"))


def cdx_url(t, attempt=1):
    key = t["key"]
    mt = "prefix" if "/" in key else "domain"
    frm = {1: CDX_FROM, 2: "2025"}.get(attempt, "202506")   # a listing that timed out is asked again for a shorter window
    return (f"https://web.archive.org/cdx/search/cdx?url={quote(key, safe='/')}&matchType={mt}&output=json"
            f"&filter=statuscode:200&from={frm}&fl=timestamp,original,mimetype"
            f"&collapse=urlkey&sort=reverse&limit=3000")


def save_site(d, st):
    jsave(os.path.join(d, "site.json"), st)


def can_start(kind):
    need = 75 if kind == "cdx" else 22
    return BUDGET[0] - elapsed() >= need


def timeout_for(cap):
    return max(15, min(cap, 172 - elapsed()))


def work_site(t, prog, cdx_only=False):
    """Do as much of one site as the budget allows. True = finished, False = out of budget (stop the call),
    None = not finished (CDX failed this time, or cdx_only), go on to the next site; "nocdx" = too little budget left
    to start a CDX listing (sites whose listing is already in hand can still be worked)."""
    d = site_dir(t)
    os.makedirs(d, exist_ok=True)
    st = jload(os.path.join(d, "site.json"), None) or {
        "slug": t["slug"], "key": t["key"], "home": t["home"], "crawl_status": t.get("status"), "phase": t.get("phase"),
        "cdx": None, "plan": None, "captures": [], "done": False}
    if st.get("done"):
        return True
    # 1. CDX listing
    if st["cdx"] is None or st["cdx"].get("status") != 200:
        if not can_start("cdx"):
            return "nocdx"
        att = (st["cdx"] or {}).get("attempts", 0) + 1
        u = cdx_url(t, att)
        r = http_get(u, timeout_for(90))
        reg_ok(prog)
        pushback(r, prog)          # sleeps after a 429/503; the attempt still counts
        rec = {"query": u, "status": r["status"], "error": r["error"], "at": now_iso(), "attempts": att, "rows": 0}
        rows = []
        if r["status"] == 200:
            body = r["body"].decode("utf-8", "ignore").strip() or "[]"
            try:
                rows = [[x[0], x[1]] for x in json.loads(body)[1:] if len(x) < 3 or "html" in (x[2] or "html").lower()]
            except (ValueError, IndexError, TypeError):
                rec.update(status=None, error="unparseable listing")
        if rec["status"] == 200:
            rec["rows"] = len(rows)
            rec["rows_2025plus"] = sum(1 for ts, _ in rows if ts >= "2025")
            with gzip.open(os.path.join(d, "cdx.json.gz"), "wt", encoding="utf-8") as fh:
                json.dump(rows, fh)
            print(f"  cdx {t['slug']}/{t['key'][:38]:38s} {len(rows):4d} urls, {rec['rows_2025plus']:4d} in 2025+ {elapsed():.0f}s", flush=True)
        else:
            print(f"  cdx {t['slug']}/{t['key'][:38]:38s} FAIL {r['status']} {r['error'] or ''} (attempt {att})", flush=True)
            if att >= 3:
                st["done"], st["note"] = True, f"cdx failed {att}x: {r['status']} {r['error'] or ''}"
        st["cdx"] = rec
        save_site(d, st)
        if st["done"]:
            return True
        if rec["status"] != 200:
            return None            # not finished; the next site is tried, this one again on the next call
        if not rows:
            st["done"], st["note"] = True, "no 200 text/html capture since " + rec["query"].split("&from=")[1].split("&")[0]
            save_site(d, st)
            return True
        if cdx_only:
            return None
    if cdx_only:
        return None
    rows = json.load(gzip.open(os.path.join(d, "cdx.json.gz"), "rt", encoding="utf-8"))
    index = {}
    for ts, orig in rows:
        n = norm(orig)
        if n not in index or ts_key(ts) > ts_key(index[n][0]):
            index[n] = (ts, orig)
    # 2. home page, then the plan
    home_n = norm(t["home"])
    home_entry = index.get(home_n)
    if home_entry is None:
        root = [v for n, v in index.items() if urlparse(v[1]).path in ("", "/") and not urlparse(v[1]).query]
        if "/" not in t["key"] and root:
            home_entry = max(root, key=lambda v: ts_key(v[0]))
        elif "/" in t["key"]:
            cands = sorted(index.values(), key=lambda v: (depth(v[1]), -ts_key(v[0])))
            home_entry = cands[0] if cands else None
    if st["plan"] is None:
        if home_entry is None:
            st["plan"] = choose_pages(index, None, [], t["home"])
            st["home_missing"] = True
        else:
            st["plan"] = [{"roles": ["home"], "ts": home_entry[0], "original": home_entry[1]}]
        save_site(d, st)
    # fetch loop: home first, then plan the rest from its links
    while True:
        todo = [it for it in st["plan"] if not item_done(st, it)]
        if not todo:
            if st["plan"] and st["plan"][0]["roles"] == ["home"] and not st.get("planned_from_home"):
                links = []
                for c in st["captures"]:
                    if "home" in c["roles"] and c.get("status") == 200 and c.get("path"):
                        body = gzip.open(os.path.join(d, c["path"]), "rb").read()
                        sc = LinkScan()
                        try:
                            sc.feed(body.decode("utf-8", "replace"))
                            sc.close()
                        except Exception:  # noqa: BLE001
                            pass
                        links = sc.links
                more = choose_pages(index, home_entry, links, t["home"])
                for it in more:
                    if all(norm(it["original"]) != norm(p["original"]) for p in st["plan"]):
                        st["plan"].append(it)
                st["planned_from_home"] = True
                save_site(d, st)
                continue
            st["done"], st["finished_at"] = True, now_iso()
            save_site(d, st)
            return True
        it = todo[0]
        if not can_start("cap"):
            return False
        fetch_item(d, st, it, prog)


def item_done(st, it):
    for c in st["captures"]:
        if c["original"] == it["original"] and c["requested_timestamp"] == it["ts"]:
            st_ = c.get("status")
            return (isinstance(st_, int) and st_ < 500) or c.get("attempts", 0) >= 3
    return False


def fetch_item(d, st, it, prog):
    ru = f"https://web.archive.org/web/{it['ts']}id_/{it['original']}"
    prev = next((c for c in st["captures"] if c["original"] == it["original"] and c["requested_timestamp"] == it["ts"]), None)
    r = http_get(ru, timeout_for(40))
    reg_ok(prog)
    if pushback(r, prog):
        return
    m = re.search(r"/web/(\d{14})id_/(.+)$", r["final_url"] or "")
    cap = m.group(1) if m else None
    rec = {"roles": it["roles"], "original": it["original"], "requested_timestamp": it["ts"], "capture_timestamp": cap,
           "capture_date": f"{cap[:4]}-{cap[4:6]}-{cap[6:8]}" if cap else None,
           "archive_url": f"https://web.archive.org/web/{cap}/{m.group(2)}" if m else None,
           "status": r["status"], "error": r["error"], "attempts": (prev or {}).get("attempts", 0) + 1,
           "content_type": r["headers"].get("Content-Type") or r["headers"].get("content-type"),
           "fetched_at": now_iso(), "path": None, "bytes": len(r["body"])}
    body = r["body"]
    if r["status"] == 200 and body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
            rec["gzip_decoded"] = True
        except (OSError, EOFError):
            pass
    if r["status"] == 200 and body:
        rel = f"{it['roles'][0]}_{cap or it['ts']}_{hashlib.sha1(it['original'].encode()).hexdigest()[:8]}.html.gz"
        with gzip.GzipFile(os.path.join(d, rel), "wb", compresslevel=6, mtime=0) as fh:
            fh.write(body[:MAX_BYTES])
        rec.update(path=rel, sha1=hashlib.sha1(body).hexdigest(), bytes=len(body))
    if prev:
        st["captures"][st["captures"].index(prev)] = rec
    else:
        st["captures"].append(rec)
    save_site(d, st)
    print(f"  {st['slug'][:11]:11s} {st['key'][:30]:30s} {'+'.join(it['roles'])[:12]:12s} {r['status']} {rec['capture_date']} "
          f"{r['error'] or ''} {elapsed():.0f}s", flush=True)


# ------------------------------------------------------------------------------------------------ run
def reg_ok(prog):
    if prog.get("stops"):
        prog["stops"] = 0
        jsave(PROGRESS, prog)


def cmd_run(budget, limit, phase):
    BUDGET[0] = budget
    prog = jload(PROGRESS, {"stops": 0, "cooldown_until": 0, "zipped": []})
    wait = prog.get("cooldown_until", 0) - time.time()
    if wait > 0:
        if wait > budget - 60:
            print(f"COOLDOWN: the archive pushed back; {wait:.0f} s left. Run again after that.")
            return 3
        print(f"cool-down: sleeping {wait:.0f} s")
        time.sleep(wait)
    targets = jload(TARGETS, None)["sites"]
    n_new = 0
    pending = []
    for t in targets:
        if phase and t.get("phase") != phase:
            continue
        sj = jload(os.path.join(site_dir(t), "site.json"), None)
        if sj and sj.get("done"):
            continue
        pending.append((t, sj))
    try:
        # A. keep a buffer of 4 sites whose CDX listing is in hand, so the second half of a call (when a listing can
        #    no longer be started) still has captures to fetch
        ready = sum(1 for _, sj in pending[:12] if sj and sj.get("cdx") and sj["cdx"].get("status") == 200)
        for t, sj in pending[:12]:
            if ready >= 4 or not can_start("cdx"):
                break
            if sj and sj.get("cdx") and sj["cdx"].get("status") == 200:
                continue
            work_site(t, prog, cdx_only=True)
            sj2 = jload(os.path.join(site_dir(t), "site.json"), None)
            if sj2 and sj2.get("cdx") and sj2["cdx"].get("status") == 200:
                ready += 1
        # B. work the sites in order
        for t, _ in pending:
            if limit and n_new >= limit:
                break
            if elapsed() > budget:
                break
            res = work_site(t, prog)
            if res is True:
                n_new += 1
            elif res is False:
                break
    except Stop as e:
        prog["stops"] = prog.get("stops", 0) + 1
        cool = min(60 * 2 ** (prog["stops"] - 1), 600)
        prog["cooldown_until"] = time.time() + cool
        if e.args[1:] == ("conn",):
            prog["pace_bump"] = min(prog.get("pace_bump", 0) + 1, 6)
        jsave(PROGRESS, prog)
        print(f"STOP: {e.args[0]} -> cool-down {cool} s (stop #{prog['stops']}); re-run later")
        return 2
    left = sum(1 for t in targets if not (jload(os.path.join(site_dir(t), "site.json"), {}) or {}).get("done"))
    print(f"finished {n_new} sites this call; {left} of {len(targets)} still to do; {elapsed():.0f}s")
    return 0


def cmd_status():
    targets = jload(TARGETS, None)["sites"]
    prog = jload(PROGRESS, {})
    from collections import Counter
    c, notes = Counter(), Counter()
    for t in targets:
        sj = jload(os.path.join(site_dir(t), "site.json"), None)
        k = (t["phase"], "done" if sj and sj.get("done") else "started" if sj else "todo")
        c[k] += 1
        if sj and sj.get("done"):
            notes[(sj.get("note") or "").split(":")[0][:40] or ("captured" if sj["captures"] else "?")] += 1
    for k in sorted(c):
        print(k, c[k])
    print("done notes:", dict(notes))
    print("zipped sites:", len(prog.get("zipped", [])), "| stops:", prog.get("stops"), "| cooldown_until:", prog.get("cooldown_until"))


def cmd_zip(minimum, everything):
    prog = jload(PROGRESS, {"zipped": []})
    targets = jload(TARGETS, None)["sites"]
    z = set(prog.get("zipped", []))
    ready = []
    for t in targets:
        sj = jload(os.path.join(site_dir(t), "site.json"), None)
        if sj and sj.get("done") and f"{t['slug']}/{t['key']}" not in z:
            ready.append(t)
    if not ready or (len(ready) < minimum and not everything):
        print(f"{len(ready)} finished sites not yet zipped (need {minimum}); nothing zipped")
        return 0
    os.makedirs(OUT, exist_ok=True)
    n = 1 + len([f for f in os.listdir(OUT) if f.startswith("batch_")])
    name = os.path.join(OUT, f"batch_{n:02d}.zip")
    with zipfile.ZipFile(name, "w", zipfile.ZIP_STORED) as zf:
        for t in ready:
            d = site_dir(t)
            for fn in sorted(os.listdir(d)):
                zf.write(os.path.join(d, fn), f"{t['slug']}/{t['key'].replace('/', '__')}/{fn}")
            z.add(f"{t['slug']}/{t['key']}")
    prog["zipped"] = sorted(z)
    jsave(PROGRESS, prog)
    print(f"{name}: {len(ready)} sites, {os.path.getsize(name) / 1e6:.1f} MB")
    return 0


def main():
    global PACE
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "status", "zip"])
    ap.add_argument("--budget", type=float, default=150)
    ap.add_argument("--pace", type=float, default=4.0, help="seconds between request starts (never below 4)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--phase", default="")
    ap.add_argument("--min", type=int, default=100)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    PACE = max(4.0, a.pace) + jload(PROGRESS, {}).get("pace_bump", 0)
    if a.cmd == "run":
        return cmd_run(a.budget, a.limit, a.phase)
    if a.cmd == "status":
        cmd_status()
        return 0
    return cmd_zip(a.min, a.all)


if __name__ == "__main__":
    sys.exit(main())
