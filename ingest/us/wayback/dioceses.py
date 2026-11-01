#!/usr/bin/env python3
"""Recover diocesan parish directories from the Wayback Machine (part A of the `wayback` job).

    python3 -I ingest/us/wayback/dioceses.py [--minutes 40] [--only slug,slug] [--per-see-minutes 8] [--retry]

Targets: sees whose live directory could not be read (data/real/us/dioceses/directory_status.json status challenge, dead,
blocked, html_unknown) and that have no parishes.json yet. Per see: one CDX request for the newest capture of every
parish/directory-looking page of the diocese site (+ one for the home page), then dirlib.read_see (the same readers as
directory_html.py: home-page nav links, /parishes/ /find-a-parish/ /parish-directory/ paths, list entries, detail pages)
runs against the ARCHIVED copies. Writes data/real/us/dioceses/<slug>/parishes.json with source_label
"wayback-<capture date>" and the archive URL. State: data/real/us/wayback/dioceses_state.json (committed) and
data/raw/us/wayback/dioceses/<slug>/ (gitignored caches).
"""
from __future__ import annotations

import argparse
import json, os, zlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ingest" / "us" / "dioceses"))
sys.path.insert(0, str(ROOT / "ingest" / "us"))
import wblib as WB  # noqa: E402
import common_fetch as CF  # noqa: E402
import dirlib as DL  # noqa: E402
import runner as RN  # noqa: E402

WANT = ("challenge", "dead", "blocked", "html_unknown")
OUTW = ROOT / "data" / "real" / "us" / "wayback"
_SH = os.environ.get("WB_SHARD", "").replace("/", "of")
STATE = OUTW / (f"dioceses_state.{_SH}.json" if _SH else "dioceses_state.json")   # per-shard state: concurrent runners never edit one file
RAWW = ROOT / "data" / "raw" / "us" / "wayback" / "dioceses"
DL.RAW = RAWW                                 # dirlib.Fetcher caches here, not beside the live-read caches
SCRIPT = "ingest/us/wayback/dioceses.py"
RX = r"(?i).*(parish|church|director|locat|find|mass|deanery|chancery|ministr|iglesia).*"


def _in_shard(key: str) -> bool:
    """WB_SHARD="i/n" (env) splits the target list across n concurrent runners by a stable hash; unset = all."""
    sh = os.environ.get("WB_SHARD", "")
    if "/" not in sh:
        return True
    i, n = (int(x) for x in sh.split("/"))
    return zlib.crc32(key.encode()) % n == i


class WbPolite:
    """Stands in for common_fetch.Polite: every GET is answered from the archive (miss = not_found, no request)."""

    def __init__(self, arch: WB.Archive, idx: WB.Index):
        self.arch, self.idx = arch, idx
        self.used: dict[str, str] = {}
        self.n = 0

    def robots_for(self, url):
        return {"class": "ok", "rp": None, "delay": 1.0, "status": 200}

    def allowed(self, url):
        return True

    def get(self, url, accept=None, timeout=25, max_bytes=4_000_000):
        url = CF.https_only(url)
        out = {"url": url, "status": None, "body": b"", "err": None, "headers": {}, "ctype": "", "final_url": url}
        hit = self.idx.lookup(url)
        if not hit:
            return dict(out, status=404, **{"class": "not_found"})
        ts, orig = hit
        r = self.arch.capture(ts, orig)
        cls = WB.fs.classify(r)
        self.used[url] = ts
        self.n += 1
        return dict(out, status=r.get("status"), body=r.get("body") or b"", ctype=r.get("ctype", ""),
                    headers={}, **{"class": cls})


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    OUTW.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def one(slug: str, see: dict, probe: dict, arch: WB.Archive, minutes: float) -> dict:
    base = probe.get("base") or "https://" + CF.host_of(see["website"])
    host = CF.host_of(base)
    host = host[4:] if host.startswith("www.") else host
    idx = WB.Index(arch, RAWW / slug / "cdx.json", host, "domain", regex=RX, limit=3000)
    res = {"slug": slug, "host": host, "checked_at": DL.now_iso()}
    if not idx.ok:
        return dict(res, result="cdx-failed")
    idx.add_home(base + "/")
    res["indexed_pages"] = len(idx.map)
    if not idx.map:
        return dict(res, result="no-capture")
    pol = WbPolite(arch, idx)
    out = DL.read_see(see, dict(probe, directory_page=None), pol, False, max_detail=150, budget=300, minutes=minutes)
    recs, log = out["records"], out["log"]
    base = out.get("base") or base
    recs_out = []
    for conf in ("vendor", "list", "page", "rest"):
        part = [r for r in recs if r.get("_conf") == conf]
        recs_out += DL.finalize_records(part, RN.CONF[conf], f"{SCRIPT} (wayback)", filter_names=True)
    raw_n = len({(DL.clean(r.get("name")).lower(), CF.host_of(r.get("website") or "") or r.get("source_url")) for r in recs})
    n, w = len(recs_out), sum(1 for r in recs_out if r.get("website"))
    res.update(records=n, with_website=w, records_before_filter=raw_n, cms_guess=log.get("cms_guess"),
               pages_read=pol.n, notes=log.get("notes"))
    if not (n >= 5 and n >= 0.6 * raw_n):
        return dict(res, result="no-list" if raw_n < 5 else "rejected-not-a-parish-list")
    lists = [x for x in (log.get("lists") or []) if x.get("entries")]
    list_url = lists[0]["url"] if lists else None
    ts_list = pol.used.get(CF.https_only(list_url)) if list_url else None
    ts = ts_list or max(pol.used.values())
    date = WB.date_of(ts)
    src_orig = (idx.lookup(list_url) or (ts, list_url))[1] if list_url else base + "/"
    if list_url and idx.lookup(list_url):
        ts = idx.lookup(list_url)[0]
        date = WB.date_of(ts)
    arc = WB.archive_url(ts, src_orig)
    for r in recs_out:
        r["capture_date"] = date
    old = DL.existing(slug)
    if old and not str(old.get("source_label", "")).startswith("wayback-"):
        return dict(res, result="kept-live-list")            # a live list appeared meanwhile; it wins
    DL.write_parishes(slug, see["name"], arc, f"wayback-{date}", SCRIPT, log.get("cms_guess") or "unknown", recs_out,
                      "Read from an ARCHIVED copy (Wayback Machine) of the diocese's own parish directory because the live "
                      "site could not be read (bot challenge or dead). Names, cities and websites are as of the capture "
                      "date and may be out of date; spot-check before treating as the official list. No phones, e-mails "
                      "or personal names kept.",
                      extra={"archive_url": arc, "capture_date": date, "live_status": probe.get("status"),
                             "directory_page_original": src_orig})
    return dict(res, result="written", capture_date=date, archive_url=arc)


def targets(only: set[str], retry: bool):
    sees, st = RN.load()
    state = load_state()
    todo = []
    order = {"challenge": 0, "html_unknown": 1, "blocked": 2, "dead": 3}
    for slug, p in sorted(st.items(), key=lambda kv: (order.get(kv[1].get("status"), 9), kv[0])):
        if (only and slug not in only) or (not only and p.get("status") not in WANT):
            continue
        if not _in_shard(slug):
            continue
        ex = DL.existing(slug)
        if ex and not str(ex.get("source_label", "")).startswith("wayback-"):
            continue
        if not only and not retry and slug in state and state[slug].get("result") in (
                "written", "no-capture", "no-list", "rejected-not-a-parish-list", "kept-live-list"):
            continue
        todo.append((slug, sees[slug], p))
    return todo


def run(arch: WB.Archive, minutes: float, only=(), per_see=8.0, retry=False, on_batch=None) -> dict:
    t0 = time.time()
    state = load_state()
    todo = targets(set(only), retry)
    print(f"A: {len(todo)} sees to try (budget {minutes:.0f} min)", flush=True)
    done = 0
    for slug, see, p in todo:
        left = minutes * 60 - (time.time() - t0)
        if left < 90:
            break
        try:
            r = one(slug, see, p, arch, min(per_see, left / 60 - 1))
        except WB.WbStop:
            raise
        except Exception as e:  # noqa: BLE001
            r = {"slug": slug, "result": "error", "error": repr(e)[:300], "checked_at": DL.now_iso()}
        state[slug] = r
        save_state(state)
        done += 1
        print(f"  {slug:50s} {r.get('result'):26s} {r.get('records', 0):4} recs {r.get('with_website', 0):4} web "
              f"{r.get('capture_date', '')}", flush=True)
        if on_batch and done % 5 == 0:
            on_batch("dioceses")
    return state


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--only", default="")
    ap.add_argument("--per-see-minutes", type=float, default=8)
    ap.add_argument("--retry", action="store_true")
    a = ap.parse_args()
    run(WB.Archive(), a.minutes, [x for x in a.only.split(",") if x], a.per_see_minutes, a.retry)
