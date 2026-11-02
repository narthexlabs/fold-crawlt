#!/usr/bin/env python3
"""Laptop runner for the Wayback pass over US parish sites (python 3.10 standard library only; no parsing here).

Lives on Matthew's laptop as wayback-us/wayback_us_run.py next to a copy of the repo files it imports
(repo/ingest/us/wayback/{wblib,sites}.py, repo/ingest/california/sites/fetch_site.py) and targets.json
(ingest/us/wayback/laptop_targets.py). It FETCHES ONLY: per site the CDX listing (wblib.Index, cached as
raw/<diocese>/<key>__cdx.json) and the captures fetch_site.Site's page plan asks for (sites.WaybackSite, cached as
raw/<diocese>/<key>/manifest.json + gzipped bodies) — the same cache layout sites.py writes under data/raw/us/wayback/sites/.
The records are built in the project sandbox by ingest/us/wayback/laptop_build.py, which replays sites.one() on these caches
with no network, so the output is byte-for-byte the runner's shape.

    python3 wayback_us_run.py run --budget 165     # one device-shell call; resumable; prints a one-line summary
    python3 wayback_us_run.py status
    python3 wayback_us_run.py zip [--min 150|--all] # out/batch_NN.zip of finished, not-yet-zipped sites

Politeness: one request at a time, >= 4.1 s apart (PACE), honest FindTheChurch UA (fetch_site), no request
is started that could outlive the call's budget; 429 -> 120 s, refused connection -> 300 s persisted cool-down; 6 failed requests in a row -> 600 s
cool-down. A site whose CDX listing fails twice is finished as cdx-failed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "repo" / "ingest" / "us" / "wayback"))
sys.modules.setdefault("parse_site", types.ModuleType("parse_site"))   # parsing happens in the sandbox, not here
import wblib as WB  # noqa: E402
import sites as S  # noqa: E402

fs = WB.fs
fs.SITE_SECONDS = 10 ** 6
RAW = HERE / "raw"
PROG = HERE / "progress.json"
TARGETS = HERE / "targets.json"
OUT = HERE / "out"
# web.archive.org refuses connections for a while when a client goes faster than ~15 requests a minute (seen 2026-11-01:
# conn_error streak at 1.1 s pace), so the laptop paces every request 4.1 s apart, as the California laptop run did.
PACE = 4.1


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Budget(fs.Deadline):
    """The call's time is up (or a cool-down began): state is saved, run again."""


def load_prog() -> dict:
    if PROG.exists():
        return json.loads(PROG.read_text())
    return {"sites": {}, "cooldown_until": 0, "zipped": [], "batches": 0, "requests": 0, "calls": 0}


def save_prog(p: dict) -> None:
    tmp = PROG.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=0, ensure_ascii=False))
    os.replace(tmp, PROG)


class LaptopArchive(WB.Archive):
    def __init__(self, deadline: float, prog: dict):
        super().__init__(pace=PACE)
        self.deadline, self.prog = deadline, prog

    def get(self, url, timeout=60, max_bytes=3_000_000):
        r = {}
        for attempt in range(2):
            rem = self.deadline - time.time()
            if rem < 15:
                raise Budget("budget")
            t = int(min(timeout, rem - 6))
            self.last_start = time.time()
            r = self._once(url, t, max_bytes)
            st = r.get("status")
            self.diag = (st, r.get("err"), round(time.time() - self.last_start, 1), url[:60])
            if st == 429:
                self.prog["cooldown_until"] = time.time() + 120
                raise Budget("429")
            if r.get("err") or (st and st >= 500):
                if r.get("err") == "timeout" and t < timeout:
                    raise Budget("timeout clamped by budget")
                if r.get("err") == "conn_error":           # the archive is refusing us: cool down, never push harder
                    self.prog["cooldown_until"] = time.time() + 300
                    raise Budget("conn_error: archive refusing connections, 300 s cool-down")
                if attempt == 0 and self.deadline - time.time() > 40:
                    time.sleep(8)
                    continue
            break
        bad = bool(r.get("err")) or (r.get("status") or 0) >= 500
        self.fail_run = self.fail_run + 1 if bad else 0
        if self.fail_run >= 6:
            self.prog["cooldown_until"] = time.time() + 600
            raise WB.WbStop(f"6 failed requests in a row; last {r.get('status')} {r.get('err')}")
        return r


def one(arch: LaptopArchive, t: dict, rec: dict) -> bool:
    """Fetch one site. True = finished (rec['state'] = 'done')."""
    key, home, dio = t["key"], t["home_url"], t["diocese"]
    raw = RAW / dio
    cache = raw / (key.replace("/", "__") + "__cdx.json")
    if "/" in key:
        idx = WB.Index(arch, cache, key, "prefix", limit=1500)
    else:
        idx = WB.Index(arch, cache, key, "domain", limit=2000)
    if not idx.ok:
        rec["cdx_fail"] = rec.get("cdx_fail", 0) + 1
        rec["cdx_last"] = list(getattr(arch, "diag", ()))
        if rec["cdx_fail"] >= 2:
            rec.update(state="done", result="cdx-failed")
            return True
        rec["state"] = "partial"
        return False
    idx.add_home(home)
    if not idx.map:
        rec.update(state="done", result="no-capture")
        return True
    site = S.WaybackSite(arch, idx, key, home, str(raw), time.monotonic() + 10 ** 6)
    m = site.run()
    if not m.get("complete"):
        rec["state"] = "partial"
        raise Budget(m.get("note") or "site cut")
    caps = sum(1 for p in m["pages"] if p.get("wayback_ts") and p.get("class") == "ok")
    rec.update(state="done", result="fetched" if m.get("status") == "ok" else "no-usable-capture",
               home_class=m.get("status"), captures=caps, pages=len(m["pages"]))
    return True


def cmd_run(budget: float) -> int:
    t0 = time.time()
    p = load_prog()
    p["calls"] = p.get("calls", 0) + 1
    if p.get("cooldown_until", 0) > t0:
        print(f"COOLDOWN {int(p['cooldown_until'] - t0)} s")
        return 0
    targets = json.loads(TARGETS.read_text())["targets"]
    arch = LaptopArchive(t0 + budget, p)
    n_done = 0
    msg = ""
    try:
        for t in targets:
            rec = p["sites"].get(t["sid"]) or {}
            if rec.get("state") == "done":
                continue
            if time.time() > t0 + budget - 20:
                break
            rec.setdefault("started", now_iso())
            p["sites"][t["sid"]] = rec
            try:
                fin = one(arch, t, rec)
            except (WB.WbStop, Budget, fs.Deadline) as e:
                rec["state"] = "partial"
                msg = f"{type(e).__name__}: {e}"
                break
            except Exception as e:  # noqa: BLE001 - one bad site never stops the run
                rec["errors"] = rec.get("errors", 0) + 1
                rec["last_error"] = repr(e)[:200]
                if rec["errors"] >= 2:
                    rec.update(state="done", result="error")
                fin = rec.get("state") == "done"
            rec["updated"] = now_iso()
            if fin:
                rec["finished"] = now_iso()
                n_done += 1
            save_prog(p)
    finally:
        p["requests"] = p.get("requests", 0) + arch.n
        save_prog(p)
    done = sum(1 for r in p["sites"].values() if r.get("state") == "done")
    print(f"call: {n_done} finished, {arch.n} requests, {int(time.time() - t0)} s. total done {done} / {len(targets)}; "
          f"{len(targets) - done} still to do; unzipped done {done - len(p['zipped'])}. {msg}")
    return 0


def cmd_status() -> int:
    p = load_prog()
    from collections import Counter
    c = Counter(r.get("result") or r.get("state") for r in p["sites"].values())
    print(json.dumps({"results": c, "zipped": len(p["zipped"]), "batches": p.get("batches"), "requests": p.get("requests"),
                      "calls": p.get("calls"), "cooldown_s": max(0, int(p.get("cooldown_until", 0) - time.time()))}))
    return 0


def cmd_zip(minimum: int) -> int:
    p = load_prog()
    zipped = set(p["zipped"])
    targets = {t["sid"]: t for t in json.loads(TARGETS.read_text())["targets"]}
    ready = [sid for sid, r in p["sites"].items() if r.get("state") == "done" and sid not in zipped]
    if len(ready) < minimum or not ready:
        print(f"only {len(ready)} ready (min {minimum})")
        return 0
    OUT.mkdir(exist_ok=True)
    n = p.get("batches", 0) + 1
    path = OUT / f"batch_{n:02d}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for sid in ready:
            t = targets[sid]
            k = t["key"].replace("/", "__")
            d = RAW / t["diocese"]
            for f in [d / (k + "__cdx.json")] + (sorted((d / k).rglob("*")) if (d / k).exists() else []):
                if f.is_file():
                    z.write(f, str(f.relative_to(RAW)))
        z.writestr("laptop_progress.json", json.dumps(
            {"batch": n, "zipped_at": now_iso(), "sites": {sid: dict(p["sites"][sid], **targets[sid]) for sid in ready}},
            indent=0, ensure_ascii=False))
    p["zipped"] = sorted(zipped | set(ready))
    p["batches"] = n
    save_prog(p)
    print(f"{path.name}: {len(ready)} sites, {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:] or ["status"]
    if a[0] == "run":
        b = float(a[a.index("--budget") + 1]) if "--budget" in a else 165
        raise SystemExit(cmd_run(b))
    if a[0] == "zip":
        raise SystemExit(cmd_zip(0 if "--all" in a else int(a[a.index("--min") + 1]) if "--min" in a else 150))
    raise SystemExit(cmd_status())
