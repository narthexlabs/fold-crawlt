#!/usr/bin/env python3
"""Wayback Machine reader for parish sites we cannot read directly. Python 3 standard library only (urllib).

Run it from a connection that reaches web.archive.org (the project's sandbox cannot): copy this file and a
targets.json (ingest/public/wayback_targets.py) into an empty folder on a home machine and run it there, in chunks of
a few minutes. It is the stdlib twin of ingest/public/wayback.py. Copy the folder's captures/, manifest.json and
cdx_*.json back to data/raw/wayback/ and run ingest/public/wayback_build.py. The 2026-10-31 run: 18 CDX listings,
149 capture requests (145 read) for 13 parishes, from Matthew's laptop, at a 4 s pace.
The planner (which captures to read) is a short hand-written plan.json per round ({"items": [{entity_id, role,
original, timestamp}]}), chosen from the CDX listings; data/raw/wayback/plan*.json are the five rounds used.

    python3 wayback_home.py robots                         # web.archive.org/robots.txt (cached in robots.txt)
    python3 wayback_home.py cdx   [--budget 150]           # CDX listings for targets.json "cdx_queries" not yet saved
    python3 wayback_home.py fetch [--budget 150] [--plan plan.json]   # planned captures, raw (id_) content
    python3 wayback_home.py status

Resumable: each finished request is written straight away (cdx_<key>.json + cdx_index.json, or manifest.json), and
a re-run skips whatever is already done, so it can be run in chunks of a few minutes.

CDX: https://web.archive.org/cdx/search/cdx?url=<url>&matchType=<domain|prefix|exact>&output=json
     &filter=statuscode:200&from=<year>&fl=timestamp,original,mimetype ; the listing comes back oldest first and
     is read newest first by the planner (sorted by timestamp, descending).
Capture: https://web.archive.org/web/<timestamp>id_/<original>  (id_ = the page as archived, no Wayback toolbar).
     When the timestamp is not exact the archive redirects to the nearest capture; the final URL gives the real
     capture timestamp, which is what manifest.json records.

Rules: one request at a time and at least 1.1 s between request starts (redirects count; --pace raises it: the
2026-10-31 run used 4 s after archive.org refused connections at 1.1 s); default urllib User-Agent; no cookies;
nothing spoofed; no challenge is ever answered. HTTP 429/503 or a refused/reset connection from the archive stops
the run at once (back off and try again later); failed requests are not recorded as done, so a re-run retries
them. web.archive.org's robots.txt is read first and obeyed if present (it was a 404 on 2026-10-31: nothing disallowed).
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
import urllib.robotparser
from urllib.parse import urljoin, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = os.path.join(HERE, "targets.json")
MANIFEST = os.path.join(HERE, "manifest.json")
CDX_INDEX = os.path.join(HERE, "cdx_index.json")
ROBOTS = os.path.join(HERE, "robots.txt")
PACE = 1.1
MAX_BYTES = 40 * 1024 * 1024
_last = [0.0]
T0 = time.time()


class Stop(Exception):
    pass


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


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def jload(p, default):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def jsave(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


_rp = [None]


def robots_ok(url):
    if _rp[0] is None:
        if os.path.exists(ROBOTS):
            txt = open(ROBOTS, encoding="utf-8", errors="ignore").read()
        else:
            r = get("https://web.archive.org/robots.txt", timeout=30, check_robots=False)
            txt = r["body"].decode("utf-8", "ignore") if r["status"] == 200 else ""
            with open(ROBOTS, "w", encoding="utf-8") as f:
                f.write(txt if r["status"] == 200 else f"# HTTP {r['status']} at {now_iso()}: no robots.txt, nothing disallowed\n")
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(txt.splitlines())
        _rp[0] = rp
    return _rp[0].can_fetch("*", url)


def get(url, timeout=90, check_robots=True):
    if check_robots and not robots_ok(url):
        return {"status": None, "error": "disallowed-by-robots", "body": b"", "final_url": url, "headers": {}}
    pace()
    out = {"status": None, "error": None, "body": b"", "final_url": url, "headers": {}}
    try:
        with OPENER.open(urllib.request.Request(url), timeout=timeout) as r:
            out.update(status=r.status, final_url=r.geturl(), headers=dict(r.headers.items()))
            out["body"] = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        out.update(status=e.code, final_url=e.geturl() or url, headers=dict(e.headers.items()) if e.headers else {})
        try:
            out["body"] = e.read(200000)
        except Exception:  # noqa: BLE001
            pass
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        out["error"] = f"{type(e).__name__}: {str(getattr(e, 'reason', e))[:160]}"
        if re.search(r"refused|reset|Errno 10[46]|Errno 111", out["error"]) and "archive.org" in urlparse(url).netloc:
            raise Stop(f"{out['error']} from {urlparse(url).netloc}: the archive is pushing back; stopping, re-run later")
    if out["status"] in (429, 503):
        raise Stop(f"HTTP {out['status']} from {urlparse(url).netloc}: stopping; try again later")
    return out


def safe(key):
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


# ------------------------------------------------------------------------------------------------ CDX
def cmd_cdx(budget):
    t = jload(TARGETS, None)
    idx = jload(CDX_INDEX, {})
    for q in t["cdx_queries"]:
        key = q["key"]
        f = "cdx_" + safe(key) + ".json"
        fp = os.path.join(HERE, f)
        if key in idx and idx[key].get("status") == 200:
            continue
        if os.path.exists(fp) and key not in idx:  # listing saved by an earlier run (same query shape): reuse it
            try:
                rows = jload(fp, [])
                idx[key] = {"file": f, "status": 200, "rows": max(0, len(rows) - 1), "reused": True, "at": now_iso()}
                jsave(CDX_INDEX, idx)
                print("reused", key, idx[key]["rows"], "rows")
                continue
            except Exception:  # noqa: BLE001
                pass
        left = budget - (time.time() - T0)
        if left < 45:
            print("budget reached; re-run to continue")
            break
        u = (f"https://web.archive.org/cdx/search/cdx?url={q['url']}&matchType={q.get('matchType', 'domain')}"
             f"&output=json&filter=statuscode:200&from={q.get('from', '2025')}&fl=timestamp,original,mimetype"
             f"&limit={q.get('limit', 30000)}" + (f"&to={q['to']}" if q.get("to") else "")
             + (f"&collapse={q['collapse']}" if q.get("collapse") else ""))
        r = get(u, timeout=max(30, int(left) - 8))
        rec = {"file": f, "query": u, "status": r["status"], "error": r["error"], "at": now_iso()}
        if r["status"] == 200:
            body = r["body"].decode("utf-8", "ignore").strip() or "[]"
            try:
                rows = json.loads(body)
            except ValueError:
                rows = []
                rec["error"] = "unparseable listing"
                rec["status"] = None
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
            rec["rows"] = max(0, len(rows) - 1)
        idx[key] = rec
        jsave(CDX_INDEX, idx)
        print(key, rec.get("status"), rec.get("rows"), rec.get("error") or "", f"{time.time() - T0:.0f}s", flush=True)
    done = sum(1 for q in t["cdx_queries"] if idx.get(q["key"], {}).get("status") == 200)
    print(f"cdx done {done}/{len(t['cdx_queries'])}")


# ------------------------------------------------------------------------------------------------ captures
def ext_for(it, ctype, body):
    if body[:4] == b"%PDF" or "pdf" in (ctype or "").lower():
        return ".pdf"
    if (ctype or "").startswith("image/"):
        return "." + ctype.split("/")[1].split(";")[0][:5]
    return ".html"


def cmd_fetch(budget, plan_path):
    plan = jload(plan_path, {"items": []})
    man = jload(MANIFEST, {"note": "Wayback Machine captures read with wayback_run.py (id_ raw content)",
                           "captures": {}})
    done = lambda it: raw_url(it) in man["captures"] and man["captures"][raw_url(it)].get("status") is not None  # noqa: E731
    todo = [it for it in plan["items"] if not done(it)]
    print(f"{len(todo)} to fetch of {len(plan['items'])} planned")
    for it in todo:
        if time.time() - T0 > budget:
            print("budget reached; re-run to continue")
            break
        ru = raw_url(it)
        r = get(ru, timeout=max(30, int(budget - (time.time() - T0)) + 20))
        m = re.search(r"/web/(\d{14})id_/(.+)$", r["final_url"] or "")
        cap = m.group(1) if m else None
        rec = {"entity_id": it["entity_id"], "role": it.get("role"), "original": it["original"],
               "requested_timestamp": it["timestamp"], "capture_timestamp": cap,
               "capture_date": f"{cap[:4]}-{cap[4:6]}-{cap[6:8]}" if cap else None,
               "archive_url": f"https://web.archive.org/web/{cap}/{m.group(2)}" if m else None,
               "raw_url": ru, "final_url": r["final_url"], "status": r["status"], "error": r["error"],
               "content_type": r["headers"].get("Content-Type") or r["headers"].get("content-type"),
               "memento_datetime": r["headers"].get("Memento-Datetime") or r["headers"].get("memento-datetime"),
               "fetched_at": now_iso(), "path": None, "bytes": len(r["body"])}
        if it.get("from_page"):
            rec["from_page"] = it["from_page"]
        if r["status"] == 200 and r["body"][:2] == b"\x1f\x8b":  # archived with Content-Encoding: gzip; urllib leaves it
            try:
                r["body"] = gzip.decompress(r["body"])
                rec["gzip_decoded"] = True
            except (OSError, EOFError):
                pass
        if r["status"] == 200 and r["body"]:
            e = ext_for(it, rec["content_type"], r["body"])
            rel = os.path.join("captures", it["entity_id"],
                               f"{cap or it['timestamp']}_{hashlib.sha1(it['original'].encode()).hexdigest()[:10]}{e}")
            os.makedirs(os.path.join(HERE, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(HERE, rel), "wb") as fh:
                fh.write(r["body"][:MAX_BYTES])
            rec.update(path=rel.replace(os.sep, "/"), sha1=hashlib.sha1(r["body"]).hexdigest(),
                       truncated=len(r["body"]) > MAX_BYTES)
            if e == ".html":
                html = r["body"].decode("utf-8", "ignore")
                base = it["original"]
                pdfs = sorted({urljoin(base, h) for h in re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, re.I)})
                rec["pdf_links"] = pdfs[:80]
        man["captures"][ru] = rec
        jsave(MANIFEST, man)
        print(f"{it['entity_id'][:30]:30s} {r['status']} {rec['capture_date']} {it.get('role','')[:10]:10s} "
              f"{it['original'][-60:]} {r['error'] or ''}", flush=True)
    left = sum(1 for it in plan["items"] if not done(it))
    print(f"remaining {left}; manifest {len(man['captures'])} records; {time.time() - T0:.0f}s")


def raw_url(it):
    return f"https://web.archive.org/web/{it['timestamp']}id_/{it['original']}"


def cmd_status():
    t = jload(TARGETS, {"cdx_queries": []})
    idx = jload(CDX_INDEX, {})
    man = jload(MANIFEST, {"captures": {}})
    print("cdx:", sum(1 for q in t["cdx_queries"] if idx.get(q["key"], {}).get("status") == 200), "/", len(t["cdx_queries"]))
    from collections import Counter
    print("captures:", Counter(str(c["status"]) for c in man["captures"].values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["robots", "cdx", "fetch", "status"])
    ap.add_argument("--budget", type=float, default=150)
    ap.add_argument("--plan", default=os.path.join(HERE, "plan.json"))
    ap.add_argument("--pace", type=float, default=PACE, help="seconds between request starts (never below 1.1)")
    a = ap.parse_args()
    globals()["PACE"] = max(1.1, a.pace)
    try:
        if a.cmd == "robots":
            print("robots allows /cdx/ and /web/:", robots_ok("https://web.archive.org/cdx/search/cdx?url=x"),
                  robots_ok("https://web.archive.org/web/2026id_/https://example.org/"))
        elif a.cmd == "cdx":
            cmd_cdx(a.budget)
        elif a.cmd == "fetch":
            cmd_fetch(a.budget, a.plan)
        else:
            cmd_status()
    except Stop as e:
        print("STOP:", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
