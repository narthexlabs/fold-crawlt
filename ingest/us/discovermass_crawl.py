"""Crawl DiscoverMass church pages for any US state (except California, done separately) and publish per-state files.

    python3 -I ingest/us/discovermass_crawl.py crawl --state TX [--minutes 100]
    python3 -I ingest/us/discovermass_crawl.py crawl --states TX,NY,IL --minutes 100
    python3 -I ingest/us/discovermass_crawl.py crawl --all --minutes 100      # ORDER below, then the rest by count
    python3 -I ingest/us/discovermass_crawl.py publish [--state TX | --all]   # cache (+ earlier published files) -> files
    python3 -I ingest/us/discovermass_crawl.py status

WHY: DiscoverMass is a SECONDARY source for Mass times (about 78% agree with parish sites). Its value here is as the
fastest national address book: each church page gives the parish website URL, coordinates, the diocese line and,
secondarily, times. The website lists (<state>.websites.json) feed the national parish-site crawler (lane G4).

Rules (same as ingest/california/discovermass_crawl.py, whose page parser is reused unchanged):
  * robots.txt read at the start of every run and honoured (Crawl-delay 10 -> 10.5 s between requests).
  * ONE crawler per machine: flock on /tmp/discovermass-crawl.lock (plus one in the cache dir). A second start exits.
  * Honest user agent; no cookies/JS; 403/429/challenge stops the run (BLOCKED), nothing retried harder.
  * Resumable: data/raw/us/discovermass/<state>/<slug>.html|.json (gitignored). A fresh clone has no cache, so a slug
    already present in data/real/us/discovermass/<state>.json (any status) is also treated as done.
  * Files only; nothing goes to Supabase. Free text passes ingest/public/privacy.py.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import re
import sys
import time
import urllib.robotparser
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "california"))
sys.path.insert(0, str(ROOT / "ingest" / "public"))
import privacy  # noqa: E402

try:
    import ca_geo  # noqa: F401  (California county lookup; needs pyshp, unused here)
except ImportError:
    import types
    sys.modules["ca_geo"] = types.ModuleType("ca_geo")  # parse_page does not touch it; only California's record_for does

_spec = importlib.util.spec_from_file_location("ca_discovermass_crawl", ROOT / "ingest" / "california" / "discovermass_crawl.py")
CA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CA)  # reuse parse_page / PRIV_RULES / text(); the California file is not modified

BASE = "https://discovermass.com"
UA = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"
INDEX = ROOT / "data" / "real" / "us" / "discovermass_index.json"
OUT = ROOT / "data" / "real" / "us" / "discovermass"
CACHE = ROOT / "data" / "raw" / "us" / "discovermass"
PROGRESS = OUT / "progress.json"
GLOBAL_LOCK = Path("/tmp/discovermass-crawl.lock")
GLOBAL_STATE = Path("/tmp/discovermass-crawl.state.json")  # last request end, shared by every worktree on this machine
MIN_GAP, MARGIN = 10.0, 0.5
FIRST = ["tx", "ny", "il", "fl", "pa", "nj", "ma", "oh", "mi", "wi", "mn"]
SKIP = {"ca"}  # California is crawled by ingest/california/discovermass_crawl.py
US_BOX = (17.5, 72.0, -180.0, -64.0)  # 50 states + DC (+ Hawaii/Alaska), loose sanity box
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / "crawl.log").open("a") as f:
        f.write(line + "\n")


class Blocked(Exception):
    pass


# ------------------------------------------------------------------------------------------------ index / order
def load_index() -> dict[str, list[dict]]:
    d = json.loads(INDEX.read_text())
    by: dict[str, list[dict]] = {}
    for c in d["churches"]:
        if c.get("dup"):
            continue
        by.setdefault(c["state"] or "unresolved", []).append(c)
    return by


def state_order(by: dict) -> list[str]:
    rest = sorted((s for s in by if s not in FIRST and s not in SKIP and s != "unresolved"), key=lambda s: -len(by[s]))
    order = [s for s in FIRST if s in by] + rest + (["unresolved"] if "unresolved" in by else [])
    return [s for s in order if s not in SKIP]


def published_slugs(state: str) -> set[str]:
    p = OUT / f"{state}.json"
    if not p.exists():
        return set()
    return {r["slug"] for r in json.loads(p.read_text())["records"]}


def cache_paths(state: str, slug: str) -> tuple[Path, Path]:
    return CACHE / state / f"{slug}.html", CACHE / state / f"{slug}.json"


def cached_done(state: str, slug: str) -> bool:
    bp, mp = cache_paths(state, slug)
    if not mp.exists():
        return False
    st = json.loads(mp.read_text()).get("status")
    return (st == 200 and bp.exists()) or st in (404, 410) or st == "disallowed by robots.txt"


# ------------------------------------------------------------------------------------------------ polite fetcher
class Fetcher:
    def __init__(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.locks = []
        for p in (GLOBAL_LOCK, CACHE / ".lock"):
            f = p.open("a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.seek(0)
                sys.exit(f"another discovermass crawler holds {p} (pid {f.read().strip() or '?'}); one request at a time")
            f.seek(0)
            f.truncate()
            f.write(str(__import__("os").getpid()))
            f.flush()
            self.locks.append(f)  # flock dies with the process, so a dead holder never blocks us
        self.gap, self.rp, self.requests, self.errors_in_row = MIN_GAP, None, 0, 0

    def _last(self) -> float:
        best = 0.0
        for p in (GLOBAL_STATE, CACHE / "state.json"):
            try:
                best = max(best, float(json.loads(p.read_text())["last_request_end"]))
            except (FileNotFoundError, KeyError, ValueError):
                pass
        return best

    def _wait(self):
        wait = self._last() + self.gap + MARGIN - time.time()
        if wait > 0:
            time.sleep(wait)

    def _mark(self):
        s = json.dumps({"last_request_end": time.time(), "at": now_iso(), "gap_s": self.gap})
        GLOBAL_STATE.write_text(s)
        (CACHE / "state.json").write_text(s)

    def _get(self, url):
        self._wait()
        try:
            r = SESSION.get(url, timeout=(15, 60), allow_redirects=True)
            self.requests += 1
            return r, None
        except requests.RequestException as e:
            self.requests += 1
            return None, f"{type(e).__name__}: {str(e)[:160]}"
        finally:
            self._mark()

    def robots(self):
        p, mp = CACHE / "robots.txt", CACHE / "robots.json"
        body = None
        if p.exists() and mp.exists():
            m = json.loads(mp.read_text())
            if datetime.now(timezone.utc) - datetime.fromisoformat(m["fetched_at"]) < timedelta(hours=12):
                body = p.read_text()
        if body is None:
            r, err = self._get(f"{BASE}/robots.txt")
            if err or r.status_code != 200:
                log(f"ROBOTS unreadable ({err or r.status_code}); stopping to be safe")
                raise Blocked("robots.txt unreadable")
            body = r.text
            p.write_text(body)
            mp.write_text(json.dumps({"fetched_at": now_iso(), "status": r.status_code}))
            log(f"ROBOTS fetched: {body.strip()[:200]!r}")
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.splitlines())
        self.gap = max(MIN_GAP, float(rp.crawl_delay("*") or 0))
        self.rp = rp

    def fetch(self, url: str, body_path: Path, meta_path: Path) -> dict:
        if self.rp is not None and not self.rp.can_fetch(UA, url):
            meta = {"url": url, "status": "disallowed by robots.txt", "fetched_at": now_iso()}
            meta_path.write_text(json.dumps(meta))
            return meta
        r, err = self._get(url)
        if err:
            self.errors_in_row += 1
            log(f"ERROR {url} {err}")
            if self.errors_in_row >= 3:
                raise Blocked(f"3 errors in a row (last: {err})")
            return {"url": url, "status": f"error: {err}", "fetched_at": now_iso()}
        head = r.content[:5000].decode("utf-8", "ignore").lower()
        if r.status_code in (403, 429) or "just a moment..." in head or "cf-challenge" in head or \
                "captcha" in head[:2000] or "access denied" in head[:2000]:
            log(f"BLOCKED {url} HTTP {r.status_code}; stopping, not retried")
            (CACHE / "blocked.json").write_text(json.dumps({"url": url, "status": r.status_code, "at": now_iso(),
                                                             "head": head[:400]}))
            raise Blocked(f"HTTP {r.status_code} on {url}")
        meta = {"url": url, "final_url": r.url, "status": r.status_code, "bytes": len(r.content),
                "fetched_at": now_iso()}
        if r.status_code == 200:
            self.errors_in_row = 0
            body_path.write_bytes(r.content)
            meta_path.write_text(json.dumps(meta))
        elif r.status_code in (404, 410):
            self.errors_in_row = 0
            meta_path.write_text(json.dumps(meta))
        else:
            self.errors_in_row += 1
            log(f"ERROR {url} HTTP {r.status_code}")
            if self.errors_in_row >= 3:
                raise Blocked(f"3 errors in a row (last HTTP {r.status_code})")
        return meta


# ------------------------------------------------------------------------------------------------ crawl
def crawl(states: list[str], minutes: float | None) -> int:
    f = Fetcher()
    t0 = time.time()
    budget = minutes * 60 if minutes else float("inf")
    try:
        f.robots()
    except Blocked as e:
        log(f"STOP {e}")
        return 3
    by = load_index()
    code, fetched = 0, Counter()
    log(f"RUN start: states {','.join(s.upper() for s in states)}; gap {f.gap + MARGIN:.1f} s; budget "
        f"{'none' if minutes is None else f'{minutes:.0f} min'}")
    for st in states:
        rows = by.get(st, [])
        have = published_slugs(st)
        todo = [r for r in rows if r["slug"] not in have and not cached_done(st, r["slug"])]
        log(f"STATE {st.upper()}: {len(rows)} in index, {len(rows) - len(todo)} done, {len(todo)} to fetch")
        (CACHE / st).mkdir(parents=True, exist_ok=True)
        bad = 0
        for r in todo:
            if time.time() - t0 + f.gap + MARGIN + 70 > budget:
                log("RUN time budget reached; stopping cleanly")
                code = 2
                break
            bp, mp = cache_paths(st, r["slug"])
            try:
                meta = f.fetch(f"{BASE}/church/{r['slug']}/", bp, mp)
            except Blocked as e:
                log(f"STOP {e}")
                code = 3
                break
            fetched[st] += 1
            s = meta.get("status")
            if s == 200 and b'class="postId"' not in bp.read_bytes():
                bad += 1
                log(f"WARN {r['slug']}: 200 but no church markup")
                if bad >= 5:
                    log("STOP 5 pages without church markup; site may be serving a block page")
                    code = 3
                    break
            if fetched[st] % 25 == 0 or s != 200:
                log(f"PROGRESS {st.upper()} fetched {fetched[st]} this run; last {r['slug']} HTTP {s}")
        if code:
            break
    log(f"RUN end: fetched {sum(fetched.values())} pages in {time.time() - t0:.0f} s {dict(fetched)}; exit {code}")
    return code


# ------------------------------------------------------------------------------------------------ publish
def norm_url(u: str) -> str:
    u = re.sub(r"^https?://(www\.)?", "", u.strip().lower())
    return u.split("#")[0].rstrip("/")


def record_for(st: str, ix: dict) -> dict | None:
    bp, mp = cache_paths(st, ix["slug"])
    if not mp.exists():
        return None
    meta = json.loads(mp.read_text())
    base = {"slug": ix["slug"], "state": st.upper(), "city_guess": ix.get("city_guess"),
            "source_url": f"{BASE}/church/{ix['slug']}/", "fetched_at": meta.get("fetched_at"),
            "http_status": meta.get("status"), "sitemap_lastmod": ix.get("lastmod")}
    if meta.get("status") != 200 or not bp.exists():
        return dict(base, unavailable=True)
    p = CA.parse_page(bp.read_bytes(), base["source_url"])
    flags = [f for f in p["page_flags"] if "outside California" not in f]
    lat, lng, how = p["lat"], p["lng"], p["latlng_source"]
    if lat is None and p.get("latlng_rejected"):
        la, ln = p["latlng_rejected"]
        if US_BOX[0] <= la <= US_BOX[1] and US_BOX[2] <= ln <= US_BOX[3]:
            lat, lng, how = la, ln, "page: Directions link (maps.google.com daddr)"
        else:
            flags.append(f"coordinates outside the US ({la},{ln}); not used")
    if meta.get("final_url") and meta["final_url"].rstrip("/") != base["source_url"].rstrip("/"):
        flags.append(f"redirected to {meta['final_url']}")
    cnt = privacy.screen(p, CA.PRIV_RULES)
    times = [{"kind": r["kind"], "day": r["day"], "time": r["time"], "end_time": r["end_time"],
              "language": r["language"], "comment": r["comment"], "raw": r["raw"], "flags": r["flags"],
              "source_label": "secondary-discovermass"} for r in p["schedule"]]
    return dict(base, name=p["name"], type=p["type"], street=p["street"], city=p["city"], zip=p["zip"],
                state_on_page=p["state"], diocese_line=p["diocese_on_page"],
                diocese_norm=p["diocese_on_page_norm"], rite=p["rite"],
                website=p["website"], website_listed=p["website_listed"], website_note=p.get("website_note"),
                website_is_social=bool(p.get("website_is_social")), social=p["social"],
                lat=lat, lng=lng, latlng_source=how, languages_listed=p["languages_listed"],
                times=times, has_times=any(t["kind"] == "mass" and not t["flags"] for t in times),
                page_flags=flags, privacy=({k: v for k, v in cnt.items() if v} or None))


def publish_state(st: str, by: dict) -> dict:
    p = OUT / f"{st}.json"
    recs = {}
    if p.exists():  # earlier published records survive in a clone without the raw cache
        for r in json.loads(p.read_text())["records"]:
            recs[r["slug"]] = r
    for ix in by.get(st, []):
        r = record_for(st, ix)
        if r is not None:
            recs[r["slug"]] = r
    if not recs:
        return {}
    order = {c["slug"]: i for i, c in enumerate(by.get(st, []))}
    records = sorted(recs.values(), key=lambda r: order.get(r["slug"], 1 << 30))
    ok = [r for r in records if not r.get("unavailable")]
    built = now_iso()
    caveat = ("SECONDARY source (DiscoverMass: about 78% of weekend times agree with the parish's own site; no "
              "last-updated date on pages). Used here as an address book: website, coordinates, diocese line. "
              "Prefer the parish's own site for times.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{st}.json").write_text(json.dumps({
        "source": "discovermass.com church pages", "source_label": "secondary-discovermass", "caveat": caveat,
        "state": st.upper(), "built_at": built, "index_count": len(by.get(st, [])), "records": records},
        ensure_ascii=False, indent=1) + "\n")
    sites: dict[str, dict] = {}
    social_only = []
    for r in ok:
        if not r.get("website"):
            continue
        ent = {"slug": r["slug"], "name": r["name"], "city": r["city"] or r["city_guess"],
               "diocese": r["diocese_norm"], "lat": r["lat"], "lng": r["lng"]}
        if r["website_is_social"]:
            social_only.append(dict(ent, url=r["website"]))
            continue
        e = sites.setdefault(norm_url(r["website"]), {"url": r["website"], "churches": []})
        e["churches"].append(ent)
    wl = sorted(sites.values(), key=lambda e: e["churches"][0]["slug"])
    (OUT / f"{st}.websites.json").write_text(json.dumps({
        "source": "discovermass.com church pages (website field)", "source_label": "secondary-discovermass",
        "state": st.upper(), "built_at": built, "consumer": "lane G4 national parish-site crawler",
        "note": "URLs are as listed on DiscoverMass; unverified. One entry per distinct URL; several churches may share one.",
        "counts": {"churches_parsed": len(ok), "distinct_websites": len(wl), "social_only": len(social_only)},
        "websites": wl, "social_only": social_only}, ensure_ascii=False, indent=1) + "\n")
    return {"index_count": len(by.get(st, [])), "records": len(records), "parsed": len(ok),
            "unavailable": len(records) - len(ok), "with_website": sum(bool(r.get("website")) for r in ok),
            "distinct_websites": len(wl), "social_only": len(social_only),
            "with_latlng": sum(r["lat"] is not None for r in ok), "with_times": sum(r["has_times"] for r in ok),
            "published_at": built}


def publish(states: list[str]) -> None:
    by = load_index()
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"states": {}}
    for st in states:
        s = publish_state(st, by)
        if s:
            prog["states"][st.upper()] = s
            print(st.upper(), s)
    tot = prog["states"].values()
    prog["updated_at"] = now_iso()
    prog["totals"] = {k: sum(s[k] for s in tot) for k in ("index_count", "records", "parsed", "with_website",
                                                          "distinct_websites", "with_latlng", "with_times")}
    prog["order"] = state_order(by)
    OUT.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(prog, indent=1) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["crawl", "publish", "status"])
    ap.add_argument("--state")
    ap.add_argument("--states")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--minutes", type=float)
    a = ap.parse_args()
    by = load_index()
    order = state_order(by)
    if a.state:
        states = [a.state.lower()]
    elif a.states:
        states = [s.strip().lower() for s in a.states.split(",") if s.strip()]
    else:
        states = order
        if a.cmd == "crawl" and not a.all:
            sys.exit("give --state XX, --states XX,YY or --all")
    bad = [s for s in states if s not in by or s in SKIP]
    if bad:
        sys.exit(f"not crawlable here: {bad} (California has its own crawler; unknown states are not in the index)")
    if a.cmd == "crawl":
        sys.exit(crawl(states, a.minutes))
    elif a.cmd == "publish":
        publish(states)
    else:
        prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"states": {}}
        for s in order:
            d = prog["states"].get(s.upper(), {})
            print(s.upper(), len(by[s]), d.get("records", 0), d.get("with_website", 0))


if __name__ == "__main__":
    main()
