#!/usr/bin/env python3
"""Recover parish websites the live crawl could not read, from the Wayback Machine (part B of the `wayback` job).

    python3 -I ingest/us/wayback/sites.py [--minutes 60] [--only domain,domain] [--limit N] [--retry]

Targets: data/real/us/sites/<diocese>/<domain>.json with crawl_status challenge, blocked_403/429 (first), then dead
(dns, ssl_error, conn_error, not_found, http_4xx/5xx, timeout, unreachable_via_proxy). Never an `ok` read; never a file
already carrying crawl_status "wayback-...". Per site: one CDX request (newest 200/html capture of every page since 2023),
then the SAME page plan fetch_site.Site uses (home -> mass-times / ministries / bulletin / calendar / contact links, per-CMS
guesses) answered from the archive as raw `id_` captures, then ingest/california/sites/parse_site.parse_site + privacy
screen. The result is merged into the existing site file: crawl_status "wayback-<capture date>", groups / social /
bulletin / pages / cms; schedule rows keep `needs_review: true` (a person reads a Wayback schedule before it is shown;
California's rule). The earlier status is kept in `wayback.prior_status`. State: data/real/us/wayback/sites_state.json
(committed) + data/raw/us/wayback/sites/ (gitignored caches, one folder per site).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json, os, zlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
sys.path.insert(0, str(ROOT / "ingest" / "public"))
import wblib as WB  # noqa: E402
import fetch_site as fs  # noqa: E402
import parse_site as ps  # noqa: E402

SITES = ROOT / "data" / "real" / "us" / "sites"
OUTW = ROOT / "data" / "real" / "us" / "wayback"
_SH = os.environ.get("WB_SHARD", "").replace("/", "of")
STATE = OUTW / (f"sites_state.{_SH}.json" if _SH else "sites_state.json")   # per-shard state: concurrent runners never edit one file
RAWW = ROOT / "data" / "raw" / "us" / "wayback" / "sites"
FIRST = ("challenge", "blocked_403", "blocked_429")
DEAD = ("dns", "not_found", "ssl_error", "conn_error", "http_4xx", "http_5xx", "timeout", "unreachable_via_proxy",
        "https_only")
KEEP = {"domain", "diocese", "matched_church_ids", "matched", "names", "cities", "home_url", "source_url"}
STATE_LOCK_KEYS = ("result", "capture_date", "groups", "bulletin", "schedule_rows", "pages_used", "checked_at")


def _in_shard(key: str) -> bool:
    """WB_SHARD="i/n" (env) splits the target list across n concurrent runners by a stable hash; unset = all."""
    sh = os.environ.get("WB_SHARD", "")
    if "/" not in sh:
        return True
    i, n = (int(x) for x in sh.split("/"))
    return zlib.crc32(key.encode()) % n == i


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class WaybackSite(fs.Site):
    """fetch_site.Site with every request answered from the archive (a URL with no capture = not_found, no request)."""

    def __init__(self, arch: WB.Archive, idx: WB.Index, key, home, raw_root, deadline_at):
        super().__init__(key, home, raw_root, fs.Pacer(1.0), False, "2026-11-01", "wayback", deadline_at)
        self.arch, self.idx = arch, idx

    def _robots(self) -> None:
        return None

    def _feeds(self, hint, home_scan) -> None:
        return None

    def get(self, url, kind, accept=None, ext=".html", feed=False, respect_robots=True):
        if feed:
            return None
        rec = self.byurl.get(url)
        if rec is not None:
            return rec
        if time.monotonic() > self.deadline:
            raise fs.Deadline()
        hit = self.idx.lookup(url)
        if not hit:
            rec = {"url": url, "kind": kind, "class": "not_found", "status": 404, "fetched_at": now_iso(),
                   "env": "wayback", "wayback": "no-capture"}
        else:
            ts, orig = hit
            resp = self.arch.capture(ts, orig)
            cls = fs.classify(resp)
            rec = {"url": url, "kind": kind, "class": cls, "status": resp["status"], "final_url": orig,
                   "ctype": (resp.get("ctype") or "")[:80], "bytes": len(resp.get("body") or b""),
                   "fetched_at": WB.iso_of(ts), "env": "wayback", "wayback_ts": ts,
                   "archive_url": WB.archive_url(ts, orig)}
            if resp.get("err"):
                rec["err"] = resp["err"]
            if cls in ("ok", "challenge") and resp.get("body"):
                name = hashlib.sha1(url.encode()).hexdigest()[:12]
                rel = f"{len(self.m['pages']):02d}_{name}{ext}.gz"
                with gzip.GzipFile(str(Path(self.dir) / rel), "wb", compresslevel=4, mtime=0) as f:
                    f.write(resp["body"])
                rec["file"] = rel
        if url in self.byurl:
            self.m["pages"][self.m["pages"].index(self.byurl[url])] = rec
        else:
            self.m["pages"].append(rec)
        self.byurl[url] = rec
        self.save()
        return rec


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    OUTW.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def list_targets(only: set[str], retry: bool, state: dict) -> list[Path]:
    first, rest = [], []
    for f in sorted(SITES.glob("*/*.json")):
        if f.name in ("summary.json", "progress.json"):
            continue
        if only and f.stem not in only and f.name not in only:
            continue
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        st = d.get("crawl_status")
        if not isinstance(d, dict) or not st:
            continue
        sid = f"{f.parent.name}/{f.stem}"
        if not only and not retry and sid in state:
            continue
        if not _in_shard(sid):
            continue
        if only:
            first.append(f)
        elif st in FIRST:
            first.append(f)
        elif st in DEAD:
            rest.append(f)
    return first + rest


def domain_key(d: dict, f: Path) -> str:
    return d.get("domain") or f.stem.replace("__", "/")


def one(arch: WB.Archive, f: Path, today: str) -> dict:
    d = json.loads(f.read_text())
    st = d.get("crawl_status")
    key = domain_key(d, f)
    dio = f.parent.name
    res = {"prior_status": st, "checked_at": now_iso()}
    home = d.get("home_url") or ("https://" + key.split("/")[0] + "/")
    raw = RAWW / dio
    cache = raw / (key.replace("/", "__") + "__cdx.json")
    if "/" in key:
        idx = WB.Index(arch, cache, key, "prefix", limit=1500)
    else:
        idx = WB.Index(arch, cache, key, "domain", limit=2000)
    if not idx.ok:
        return dict(res, result="cdx-failed")
    idx.add_home(home)
    if not idx.map:
        return dict(res, result="no-capture")
    site = WaybackSite(arch, idx, key, home, str(raw), time.monotonic() + 240)
    m = site.run()
    if m.get("status") != "ok":
        return dict(res, result="no-usable-capture", home_class=m.get("status"))
    info = {"key": key, "home": home, "slug": dio, "directory_ids": (d.get("matched") or {}).get("directory", []),
            "discovermass_ids": (d.get("matched") or {}).get("discovermass", []), "names": d.get("names", []),
            "cities": d.get("cities", [])}
    rec = ps.parse_site(Path(site.dir), m, info, today)
    rec.pop("_pages", None)
    rec = ps.screen_record(rec)
    homep = next(p for p in m["pages"] if p["kind"] == "home")
    ts = homep["wayback_ts"]
    date = WB.date_of(ts)
    # --- schedule rows: always a person's read first (California's rule for Wayback rows)
    for r in rec.get("schedule_rows", []):
        r["needs_review"] = True
        r["review_reason"] = f"wayback capture {date}: read by a person before it is shown"
        r["source"] = f"wayback-{date}"
        cap = next((p for p in m["pages"] if (p.get("final_url") == r.get("source_url") or p.get("url") == r.get("source_url"))
                    and p.get("archive_url")), homep)
        r["archive_url"] = cap["archive_url"]
        r["capture_date"] = WB.date_of(cap["wayback_ts"])
    for g in rec.get("groups", []):
        g["source"] = f"wayback-{date}"
    new = dict(d)
    for k, v in rec.items():
        if k not in KEEP:
            new[k] = v
    new.pop("laptop_pending", None)
    caps = [{"kind": p["kind"], "original": p.get("final_url"), "capture": WB.date_of(p["wayback_ts"]),
             "archive_url": p["archive_url"]} for p in m["pages"] if p.get("wayback_ts") and p.get("class") == "ok"]
    new["crawl_status"] = f"wayback-{date}"
    new["crawl_env"] = "wayback"
    new["fetched_at"] = now_iso()
    new["wayback"] = {"capture_date": date, "capture_age_days": (datetime.now(timezone.utc).date() -
                      datetime.strptime(date, "%Y-%m-%d").date()).days, "prior_status": st, "captures": caps,
                      "note": "Read from archived copies (Wayback Machine) because the live site could not be read. "
                              "Groups, social and bulletin links are as of the capture; schedule rows are needs_review."}
    cur = json.loads(f.read_text())
    if cur.get("crawl_status") == "ok":                       # the live crawl got in first: never overwrite an ok read
        return dict(res, result="live-ok-meanwhile")
    f.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
    return dict(res, result="written", capture_date=date, pages_used=len(caps), groups=len(new.get("groups") or []),
                bulletin=bool(new.get("bulletin")), schedule_rows=len(new.get("schedule_rows") or []))


def run(arch: WB.Archive, minutes: float, only=(), limit=0, retry=False, on_batch=None, today=None) -> dict:
    t0 = time.time()
    today = today or datetime.now(timezone.utc).date().isoformat()
    state = load_state()
    todo = list_targets(set(only), retry, state)
    print(f"B: {len(todo)} sites to try (budget {minutes:.0f} min)", flush=True)
    n = 0
    for f in todo:
        if time.time() - t0 > minutes * 60 - 60 or (limit and n >= limit):
            break
        sid = f"{f.parent.name}/{f.stem}"
        try:
            r = one(arch, f, today)
        except WB.WbStop:
            raise
        except Exception as e:  # noqa: BLE001
            r = {"result": "error", "error": repr(e)[:300], "checked_at": now_iso()}
        state[sid] = r
        save_state(state)
        n += 1
        if n % 10 == 0 or r.get("result") == "written":
            print(f"  {sid:70s} {r.get('result'):18s} {r.get('capture_date', '')} g={r.get('groups', '')} "
                  f"b={r.get('bulletin', '')} s={r.get('schedule_rows', '')}", flush=True)
        if on_batch and n % 25 == 0:
            on_batch("sites")
    return state


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retry", action="store_true")
    a = ap.parse_args()
    run(WB.Archive(), a.minutes, [x for x in a.only.split(",") if x], a.limit, a.retry)
