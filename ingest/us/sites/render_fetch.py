#!/usr/bin/env python3
"""Rendered re-read of parish sites whose pages are built by JavaScript (Wix, Duda, Squarespace, Weebly, Google Sites).

    python3 -I ingest/us/sites/render_fetch.py count  [--shard 0/4]
    python3 -I ingest/us/sites/render_fetch.py run    --minutes 60 [--workers 6] [--shard 0/4] [--only a.org,b.org] [--limit N]

Why: the plain crawl (run_state.py chunk -> fetch_site.Site) reads HTML without running JavaScript. On site builders that
fill the page in the browser it sees an empty shell, so a site can read `ok`, have Mass times (often from a page the builder
does render server-side) and still show no groups. This re-reads those sites ONCE in headless Chromium (Playwright).

Which sites (all must hold): record crawl_status == "ok" (the host answered our plain crawl: never a challenge, 403 or 429
host), not read from the laptop / Wayback (those hosts refuse datacenter IPs), schedule rows present, no groups, not already
rendered or tried, and either cms_guess in RENDER_CMS or a JS-shell home page (tiny visible text / an app root) when the
plain raw cache is on this machine. --shard k/n keeps hosts with int(sha1(domain)) % n == k (disjoint runners).

How, per site (same plan as fetch_site.Site, which this subclasses): robots.txt by the plain fetcher (Disallow and
Crawl-delay honoured), then the home page and the same candidate pages fetch_site picks from the (now rendered) links,
each opened one at a time in a fresh browser context with the honest FindTheChurch user agent, waiting for network idle
(<= 15 s), and page.content() stored exactly where fetch_site stores a body. JSON/feed requests stay plain HTTP.
Images, media and fonts are not downloaded. No clicks, no cookie banners dismissed, no login, TLS verified. If a rendered
page answers 403/429 or a challenge, the site stops at once and keeps its plain record (render_attempt notes why).

Output: the rendered cache replaces data/raw/us/sites/<key>/ (manifest gets rendered: true, fetch_mode: "rendered"), then
run_state.parse(<diocese>) re-parses with lane A's parser unchanged (privacy screen included) and the record gets
fetch_mode: "rendered". If the rendered read loses Mass rows and finds no new groups, the plain record is restored with
render_attempt.result = "no_gain". Resumable: a site is done once its record carries fetch_mode or render_attempt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_state as RS  # noqa: E402  (also puts ingest/us and ingest/california/sites on sys.path)

fs, CF = RS.fs, RS.CF
RENDER_CMS = {"wix", "duda", "squarespace", "weebly", "google_sites"}
STAGE = RS.RAW / "_render"
NAV_TIMEOUT_MS = 30000
IDLE_TIMEOUT_MS = 15000
SITE_SECONDS = 420
JS_ROOT = re.compile(rb"""<div[^>]+id=["'](?:root|app|__next|__nuxt|svelte|main-app)["'][^>]*>\s*</div>""", re.I)
BLOCK_TYPES = {"image", "media", "font"}


def now_iso() -> str:
    return RS.now_iso()


# ------------------------------------------------------------------------------------------------ eligibility
def visible_text_len(html: bytes) -> int:
    t = re.sub(rb"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", b" ", html)
    t = re.sub(rb"(?s)<[^>]+>", b" ", t)
    return len(re.sub(rb"\s+", b" ", t).strip())


def js_shell(key: str) -> bool:
    """True when the plain home page in the local raw cache is an app shell (only checked where that cache exists)."""
    m = RS.manifest(key)
    if not m:
        return False
    d = RS.RAW / key.replace("/", "__")
    for p in m.get("pages", []):
        if p.get("kind") == "home" and p.get("class") == "ok" and p.get("file"):
            try:
                import gzip
                body = gzip.open(d / p["file"], "rb").read()
            except OSError:
                return False
            return visible_text_len(body) < 800 or (bool(JS_ROOT.search(body)) and visible_text_len(body) < 3000)
    return False


def in_shard(key: str, shard: str) -> bool:
    if not shard:
        return True
    k, n = (int(x) for x in shard.split("/"))
    return int(hashlib.sha1(key.encode()).hexdigest(), 16) % n == k


def candidates(inputs: dict, shard: str = "", only: set[str] | None = None) -> tuple[list[tuple[str, dict, dict]], Counter]:
    out, why = [], Counter()
    for dio, inp in inputs.items():
        for s in inp["sites"]:
            if s["social_only"] or (only and s["key"] not in only):
                continue
            fn = RS.OUT / dio / (s["key"].replace("/", "__") + ".json")
            if not fn.exists():
                continue
            try:
                rec = json.loads(fn.read_text())
            except ValueError:
                continue
            if rec.get("crawl_status") != "ok" or not rec.get("schedule_rows") or rec.get("groups"):
                continue
            why["ok_times_no_groups"] += 1
            if rec.get("crawl_env") in ("laptop", "sidedoor") or rec.get("laptop_attempt") or rec.get("refetch_attempt"):
                why["skip_host_refused_somewhere"] += 1
                continue
            cms = (rec.get("cms_guess") or {}).get("cms") if isinstance(rec.get("cms_guess"), dict) else None
            if cms in RENDER_CMS:
                reason = cms
            elif js_shell(s["key"]):
                reason = "js_shell"
            else:
                continue
            why["eligible"] += 1
            why["eligible_" + reason] += 1
            if rec.get("fetch_mode") == "rendered" or rec.get("render_attempt"):
                why["already_done"] += 1
                continue
            if not in_shard(s["key"], shard):
                continue
            out.append((dio, s, rec))
    return out, why


# ------------------------------------------------------------------------------------------------ browser
class Browser:
    """One Playwright + Chromium per worker thread (the sync API is per-thread)."""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self.b = self.pw.chromium.launch(headless=True, proxy={"server": px} if px else None)

    def close(self):
        try:
            self.b.close()
        finally:
            self.pw.stop()


def _net_class(msg: str) -> str:
    s = msg or ""
    if "ERR_NAME_NOT_RESOLVED" in s:
        return "dns"
    if "ERR_CERT" in s or "SSL" in s:
        return "ssl_error"
    if "ERR_TUNNEL_CONNECTION_FAILED" in s or "ERR_PROXY" in s:
        return "unreachable_via_proxy"
    if "Timeout" in s or "ERR_TIMED_OUT" in s:
        return "timeout"
    if "Download is starting" in s or "ERR_ABORTED" in s:
        return "not_html"
    return "conn_error"


class RenderSite(fs.Site):
    """fetch_site.Site with HTML pages opened in Chromium; robots.txt, JSON and feeds stay plain HTTP."""

    def __init__(self, browser: Browser, *a, **k):
        super().__init__(*a, **k)
        self.browser = browser
        self.deadline = time.monotonic() + SITE_SECONDS
        self.m["rendered"] = True
        self.m["fetch_mode"] = "rendered"

    def get(self, url, kind, accept=None, ext=".html", feed=False, respect_robots=True):
        if feed or ext != ".html":
            return super().get(url, kind, accept=accept, ext=ext, feed=feed, respect_robots=respect_robots)
        rec = self.byurl.get(url)
        if rec is not None:
            return rec
        if time.monotonic() > self.deadline:
            raise fs.Deadline()
        if respect_robots and self.rp is not None and not self.rp.can_fetch("*", url):
            rec = {"url": url, "kind": kind, "class": "robots_disallowed", "status": None, "fetched_at": now_iso(), "env": self.env}
        else:
            self.pacer.wait(fs.host_of(url), self.delay)          # one page at a time per host, Crawl-delay honoured
            resp = self._render(url)
            cls = fs.classify(resp)
            rec = {"url": url, "kind": kind, "class": cls, "status": resp["status"], "final_url": resp["final_url"],
                   "ctype": resp["ctype"][:80], "bytes": len(resp["body"]), "fetched_at": now_iso(), "env": self.env,
                   "tries": 1, "rendered": True}
            if resp.get("idle_timeout"):
                rec["idle_timeout"] = True
            if resp["err"]:
                rec["err"] = resp["err"]
            if cls in ("ok", "challenge", "blocked_403", "blocked_429", "parked", "http_5xx") and resp["body"]:
                import gzip
                name = hashlib.sha1(url.encode()).hexdigest()[:12]
                rel = f"{len(self.m['pages']):02d}_{name}{ext}.gz"
                with gzip.GzipFile(os.path.join(self.dir, rel), "wb", compresslevel=4, mtime=0) as f:
                    f.write(resp["body"])
                rec["file"] = rel
        self.m["pages"].append(rec)
        self.byurl[url] = rec
        self.save()
        return rec

    def _render(self, url: str) -> dict:
        out = {"url": url, "status": None, "ctype": "", "final_url": url, "body": b"", "err": None, "headers": {}}
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]        # same upgrade as fetch_site.http_get
        ctx = self.browser.b.new_context(user_agent=fs.USER_AGENT, ignore_https_errors=False, java_script_enabled=True,
                                         accept_downloads=False, service_workers="block")
        try:
            ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in BLOCK_TYPES else r.continue_())
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as e:  # noqa: BLE001
                out["err"] = _net_class(str(e))
                return out
            if resp is not None:
                out["status"] = resp.status
                out["headers"] = {k.lower(): v for k, v in resp.headers.items()}
                out["ctype"] = out["headers"].get("content-type", "")
            if out["status"] in (401, 403, 429, 503) or out["headers"].get("cf-mitigated"):
                try:                                              # keep what the server sent; never wait or retry
                    out["body"] = resp.body()[:fs.MAX_BYTES]
                except Exception:  # noqa: BLE001
                    out["body"] = b""
                out["final_url"] = page.url
                return out
            try:
                page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - chat widgets / analytics keep the network busy: take the DOM as it is
                out["idle_timeout"] = True
            out["final_url"] = page.url
            try:
                out["body"] = page.content().encode("utf-8")[:fs.MAX_BYTES]
            except Exception as e:  # noqa: BLE001
                out["err"] = "error:" + type(e).__name__
            return out
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------------------------------------ run
def _mass_rows(rec: dict) -> int:
    return sum(1 for r in rec.get("schedule_rows") or [] if r.get("kind") == "mass")


def _write(fn: Path, rec: dict) -> None:
    fn.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", "utf-8")


def cmd_run(a) -> int:
    t0 = time.time()
    log = lambda s: print(f"{now_iso()} {s}", flush=True)  # noqa: E731
    inputs = RS.build_inputs()
    only = {x for x in a.only.split(",") if x} or None
    todo, why = candidates(inputs, a.shard, only)
    if a.limit:
        todo = todo[:a.limit]
    log(f"render: {dict(why)}; {len(todo)} to render in shard {a.shard or 'all'}")
    stop_at = t0 + a.minutes * 60 - min(20 * 60, a.minutes * 60 * 0.3)
    q: queue.Queue = queue.Queue()
    for item in todo:
        q.put(item)
    pacer = fs.Pacer(1.0)
    results: dict[str, dict] = {}
    lock = threading.Lock()
    STAGE.mkdir(parents=True, exist_ok=True)

    def worker():
        br = None
        try:
            while time.time() < stop_at:
                try:
                    dio, s, rec = q.get_nowait()
                except queue.Empty:
                    return
                if br is None:
                    br = Browser()
                key = s["key"]
                sdir = STAGE / key.replace("/", "__")
                shutil.rmtree(sdir, ignore_errors=True)
                try:
                    site = RenderSite(br, key, CF.https_only(s["home"]), str(STAGE), pacer, False, RS.today(), "runner-rendered"
                                      if os.environ.get("GITHUB_ACTIONS") else "sandbox-rendered", time.monotonic() + SITE_SECONDS)
                    m = site.run()
                except Exception as e:  # noqa: BLE001 - one site must not stop the worker
                    m = {"status": "error:" + type(e).__name__, "complete": False, "pages": []}
                with lock:
                    results[key] = {"dio": dio, "status": m.get("status"), "complete": m.get("complete"),
                                    "pages": len(m.get("pages", [])), "note": m.get("note")}
                    log(f"  [{len(results)}/{len(todo)}] {key:40s} {m.get('status')!s:12s} pages={len(m.get('pages', []))} "
                        f"{m.get('note') or ''}")
        finally:
            if br is not None:
                br.close()

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, a.workers))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    # swap rendered caches in, remember the plain records, parse, compare
    before: dict[str, dict] = {}
    key_dio = {s["key"]: d for d, s, _ in todo}
    touched: set[str] = set()
    tally = Counter()
    for dio, s, rec in todo:
        key = s["key"]
        r = results.get(key)
        if not r:
            continue
        fn = RS.OUT / dio / (key.replace("/", "__") + ".json")
        sdir = STAGE / key.replace("/", "__")
        refused = any(p.get("class") in ("challenge", "blocked_403", "blocked_429") for p in
                      (json.loads((sdir / "manifest.json").read_text()).get("pages", []) if (sdir / "manifest.json").exists() else []))
        if not r["complete"] or r["status"] != "ok" or refused:
            if r["complete"] or r["status"] != "ok":         # a cut run (time cap) is simply tried again next time
                rec["render_attempt"] = {"status": r["status"], "refused": refused, "note": r.get("note"), "at": RS.today()}
                _write(fn, rec)
                touched.add(dio)
            tally["not_ok_" + str(r["status"])] += 1
            shutil.rmtree(sdir, ignore_errors=True)
            continue
        dst = RS.RAW / key.replace("/", "__")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(sdir), str(dst))
        before[key] = rec
        touched.add(dio)
    for dio in sorted(touched):
        try:
            RS.parse(dio, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  PARSE FAILED {dio}: {e!r}")
    report = []
    for key, old in before.items():
        dio = key_dio[key]
        fn = RS.OUT / dio / (key.replace("/", "__") + ".json")
        new = json.loads(fn.read_text())
        g0, g1, m0, m1 = len(old.get("groups") or []), len(new.get("groups") or []), _mass_rows(old), _mass_rows(new)
        report.append({"key": key, "cms": (old.get("cms_guess") or {}).get("cms"), "groups": [g0, g1], "mass_rows": [m0, m1],
                       "events": [len(old.get("events") or []), len(new.get("events") or [])]})
        if new.get("crawl_status") != "ok" or (m1 < m0 and g1 <= g0):
            old["render_attempt"] = {"status": new.get("crawl_status"), "result": "no_gain", "groups": g1, "mass_rows": m1,
                                     "at": RS.today()}
            _write(fn, old)
            tally["kept_plain_no_gain"] += 1
        else:
            new["fetch_mode"] = "rendered"
            _write(fn, new)
            tally["rendered_kept"] += 1
            tally["gained_groups"] += int(g1 > g0)
    for dio in sorted(touched):
        try:
            RS.run_diocese().finalize(dio)
        except Exception as e:  # noqa: BLE001
            log(f"  finalize failed {dio}: {e!r}")
    RS.RAW.mkdir(parents=True, exist_ok=True)
    (RS.RAW / "_last_touched.json").write_text(json.dumps(sorted(touched)))
    (RS.RAW / "_render_report.json").write_text(json.dumps(report, indent=1))
    log(f"render done: {dict(tally)}; groups {sum(r['groups'][0] for r in report)} -> {sum(r['groups'][1] for r in report)}, "
        f"mass rows {sum(r['mass_rows'][0] for r in report)} -> {sum(r['mass_rows'][1] for r in report)} over {len(report)} sites")
    for r in report:
        log(f"    {r['key']:40s} {r['cms']!s:12s} groups {r['groups'][0]}->{r['groups'][1]}  mass rows "
            f"{r['mass_rows'][0]}->{r['mass_rows'][1]}  events {r['events'][0]}->{r['events'][1]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["count", "run"])
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--shard", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "count":
        todo, why = candidates(RS.build_inputs(), a.shard)
        print(json.dumps({**dict(why), "to_render_in_shard": len(todo)}, indent=1))
        return 0
    return cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
