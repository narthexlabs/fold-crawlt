#!/usr/bin/env python3
"""National parish-website crawl (lane G4): California's method, every US diocese.

    python3 -I ingest/us/sites/run_state.py inputs                         # build site lists for every diocese with a source
    python3 -I ingest/us/sites/run_state.py chunk --minutes 100 [--workers 24] [--state TX] [--diocese <slug>,...]
    python3 -I ingest/us/sites/run_state.py parse <diocese-slug> [--all]   # (re)parse cached sites of one diocese
    python3 -I ingest/us/sites/run_state.py readme                         # data/real/us/sites/README.md tables
    python3 -I ingest/us/sites/run_state.py status

Sources of the website list, per diocese (dioceses = data/real/us/dioceses.json slugs):
  1. the diocese's own directory, data/real/us/dioceses/<slug>/parishes.json (ingest/us/dioceses/directory_probe.py)
  2. DiscoverMass, data/real/us/discovermass/<ST>.websites.json (lane G3), assigned to a diocese by the page's diocese
     line; a website the directory already lists keeps the directory's diocese. Unmatched lines -> `unassigned-<st>`.
Sites are keyed by domain exactly as California keys them (ingest/california/sites/build_input.site_key). Not crawled:
social-media pages (recorded as social_only, as in California), the dioceses' own sites, LPi-hosted pages
(parishesonline.com: bulletin host, read by the bulletin lane), and hosts that read as a school.

Crawl: ingest/california/sites/fetch_site.Site (imported at run time, never copied) with one shared pacer: one request at
a time per host, >= 1 s apart, robots.txt Disallow and Crawl-delay honoured (cap 30 s), honest FindTheChurch UA, TLS
verified, challenge / 403 / 429 recorded and never retried harder. Many hosts at once (--workers, default 24).
Parse: ingest/california/sites/run_diocese.cmd_parse (lane A's parser, templates, calendar feeds, privacy screen and
summary), pointed at the US folders. Output data/real/us/sites/<diocese>/<domain>.json + summary.json, same shape as
data/real/california/sites/.

Resumable: data/raw/us/sites/<domain>/manifest.json (gitignored) per site; a site whose record already exists in
data/real/us/sites/ (fresh clone) is not fetched again. `--minutes N` stops starting new sites after N minutes minus a
parse reserve. If our own egress is closed (the sandbox proxy refuses every host), the chunk records that in
progress.json and stops; sites are never written down as dead because of our network.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CA_SITES = ROOT / "ingest" / "california" / "sites"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(CA_SITES))
import common_fetch as CF  # noqa: E402
import fetch_site as fs  # noqa: E402
import build_input as BI  # noqa: E402

US = ROOT / "data" / "real" / "us"
OUT = US / "sites"
RAW = ROOT / "data" / "raw" / "us" / "sites"
INPUTS = RAW / "_inputs"
PROGRESS = OUT / "progress.json"
DM_DIR = US / "discovermass"
DIR_DIR = US / "dioceses"
URL_DIR = US / "url_sources"   # lanes G6/G7: Wikidata, OSM, Wikipedia *.websites.json (same shape as DiscoverMass)

FINAL_REC = {"ok", "challenge", "blocked_403", "blocked_429", "http_202", "robots_disallowed", "dns", "not_found", "parked",
             "ssl_error", "conn_error", "timeout", "unreachable_via_proxy", "http_4xx", "http_5xx", "not_html", "social_only"}
EGRESS = {"proxy_denied"}          # our network refused us: says nothing about the site; never final
SKIP_HOST = re.compile(r"(?:^|\.)(?:parishesonline\.com|4lpi\.com|discovermass\.com|ecatholic\.com/?$|usccb\.org|"
                       r"masstimes\.org|catholicmasstime\.org|google\.com|goo\.gl|yelp\.com|wikipedia\.org)$", re.I)
SCHOOL_HOST = re.compile(r"school|academy|prep\b|highschool|\bhs\b|college|universit", re.I)
CHURCH_WORD = re.compile(r"church|parish|catholic|cathedral|basilica|shrine|mission|chapel|newman|st[-.]?|saint|our-?lady|"
                         r"holy|sacred|blessed", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


# ------------------------------------------------------------------------------------------------ dioceses
def load_sees() -> list[dict]:
    return json.loads((US / "dioceses.json").read_text())["dioceses"]


def _nm(s: str) -> str:
    s = re.sub(r"^(?:the\s+)?(?:roman\s+catholic\s+)?(?:arch)?(?:diocese|eparchy|archeparchy)\s+of\s+", "", (s or "").strip(), flags=re.I)
    return re.sub(r"[^a-z]", "", s.lower().replace("saint", "st"))


def diocese_matcher(sees: list[dict]):
    by = {}
    for s in sees:
        by.setdefault(_nm(s["name"]), s["slug"])
    alias = {"portlandinoregon": "portland", "stpaulandminneapolis": "stpaulminneapolis", "washingtondc": "washington"}
    for a, b in list(alias.items()):
        if a in by:
            by.setdefault(b, by[a])

    def match(line: str | None) -> str | None:
        k = _nm(line or "")
        if not k:
            return None
        if k in by:
            return by[k]
        for kk, slug in by.items():                  # "Galveston-Houston" vs "Galveston Houston"; "St. Paul & Minneapolis"
            if kk and (kk == k.replace("and", "") or kk.replace("and", "") == k):
                return slug
        return None
    return match


def state_order() -> list[str]:
    """States by DiscoverMass church count (a stand-in for Catholic population; no national population file yet)."""
    idx = json.loads((US / "discovermass_index.json").read_text())
    counts = idx.get("by_state_excluding_dup_pages") or {}
    if isinstance(counts, dict) and counts:
        return [k for k, _ in sorted(counts.items(), key=lambda kv: -(kv[1] if isinstance(kv[1], int) else kv[1].get("pages", 0)))]
    return ["NY", "PA", "TX", "CA", "IL", "OH", "MI", "WI", "NJ", "MN", "MA", "FL", "LA", "MO", "IA", "IN", "CT"]


# ------------------------------------------------------------------------------------------------ inputs
def build_inputs() -> dict[str, dict]:
    sees = load_sees()
    match = diocese_matcher(sees)
    see_state = {s["slug"]: s["see_state"] for s in sees}
    diocese_hosts = {BI.host_of(BI.norm_url(s["website"]) or "") for s in sees if s.get("website")}
    recs = []    # (url, src, id, name, city, diocese, state)
    for p in sorted(DIR_DIR.glob("*/parishes.json")):
        slug = p.parent.name
        d = json.loads(p.read_text())
        for r in d.get("records", d if isinstance(d, list) else []):
            recs.append((BI.norm_url(r.get("website")), "directory", f"{slug}:{r.get('rest_id') or r.get('name')}", r.get("name"),
                         r.get("city"), slug, see_state.get(slug)))
    social_dm = []
    website_files = sorted(DM_DIR.glob("*.websites.json")) + sorted(URL_DIR.glob("**/*.websites.json"))
    for p in website_files:
        st = p.name.split(".")[0].upper()
        d = json.loads(p.read_text())
        # other sources may give a flat list of {url, name, city, diocese, slug}; wrap it in the DiscoverMass shape
        if isinstance(d, list):
            d = {"websites": [{"url": w.get("url") or w.get("website"), "churches": [w]} for w in d]}
        elif "websites" in d and d["websites"] and "churches" not in d["websites"][0]:
            d = {**d, "websites": [{"url": w.get("url") or w.get("website"), "churches": [w]} for w in d["websites"]]}
        for w in d.get("websites", []):
            for c in w.get("churches", []):
                recs.append((BI.norm_url(w.get("url")), "discovermass", c.get("slug"), c.get("name"), c.get("city"),
                             match(c.get("diocese")), st))
        for c in d.get("social_only", []):
            social_dm.append((BI.norm_url(c.get("url")), "discovermass", c.get("slug"), c.get("name"), c.get("city"),
                              match(c.get("diocese")), st))
    recs += social_dm
    by_host = defaultdict(set)
    for u, *_ in recs:
        if u:
            by_host[BI.host_of(u)].add(BI.first_seg(u))
    split_hosts = {h for h, segs in by_host.items() if len(segs) > 1 and any(segs)}
    sites: dict[str, dict] = {}
    for u, src, rid, name, city, dio, st in recs:
        if not u:
            continue
        k = BI.site_key(u, split_hosts)
        s = sites.setdefault(k, {"key": k, "home": u, "directory_ids": [], "discovermass_ids": [], "names": [], "cities": [],
                                 "social_only": False, "homes_seen": [], "_dio": Counter(), "_dio_dir": None, "states": []})
        if u not in s["homes_seen"]:
            s["homes_seen"].append(u)
        lst = s["directory_ids"] if src == "directory" else s["discovermass_ids"]
        if rid and rid not in lst:
            lst.append(rid)
        for f, v in (("names", name), ("cities", city), ("states", st)):
            if v and v not in s[f]:
                s[f].append(v)
        if src == "directory" and dio:
            s["_dio_dir"] = s["_dio_dir"] or dio
        elif dio:
            s["_dio"][dio] += 1
    out: dict[str, dict] = {}
    skipped = Counter()
    for s in sites.values():
        hs = sorted(s.pop("homes_seen"), key=lambda x: (len(urlparse(x).path.strip("/")), not x.startswith("https"), len(x)))
        s["home"] = hs[0]
        h = BI.host_of(s["home"])
        s["social_only"] = any(h == x or h.endswith("." + x) for x in BI.SOCIAL_HOSTS)
        dio = s.pop("_dio_dir") or (s["_dio"].most_common(1)[0][0] if s["_dio"] else None)
        s.pop("_dio")
        if not dio:
            dio = "unassigned-" + (s["states"][0].lower() if s["states"] else "xx")
        s["slug"] = dio
        if h in diocese_hosts and not s["social_only"]:
            skipped["diocese_site"] += 1
            continue
        if SKIP_HOST.search(h):
            skipped["non_parish_host"] += 1
            continue
        if SCHOOL_HOST.search(h) and not CHURCH_WORD.search(h.replace("school", "")):
            skipped["school_host"] += 1
            continue
        inp = out.setdefault(dio, {"slug": dio, "directory_records": 0, "discovermass_records": 0, "directory_without_website": 0,
                                   "discovermass_without_website": 0, "sites": []})
        inp["sites"].append(s)
    for dio, inp in out.items():
        inp["sites"].sort(key=lambda s: s["key"])
        inp["directory_records"] = sum(len(s["directory_ids"]) for s in inp["sites"])
        inp["discovermass_records"] = sum(len(s["discovermass_ids"]) for s in inp["sites"])
        inp["built_at"] = now_iso()
        inp["skipped_hosts_all_dioceses"] = dict(skipped)
    INPUTS.mkdir(parents=True, exist_ok=True)
    for dio, inp in out.items():
        (INPUTS / f"{dio}.json").write_text(json.dumps(inp, indent=1, ensure_ascii=False) + "\n", "utf-8")
    return out


def diocese_order(inputs: dict[str, dict]) -> list[str]:
    sees = {s["slug"]: s for s in load_sees()}
    so = state_order()
    rank = {st: i for i, st in enumerate(so)}

    def key(d):
        st = (sees.get(d) or {}).get("see_state") or (d.split("-")[-1].upper() if d.startswith("unassigned-") else "ZZ")
        return (rank.get(st, 99), -len(inputs[d]["sites"]))
    return sorted(inputs, key=key)


# ------------------------------------------------------------------------------------------------ crawl
def out_exists(dio: str, key: str) -> bool:
    fn = OUT / dio / (key.replace("/", "__") + ".json")
    if not fn.exists():
        return False
    try:
        st = json.loads(fn.read_text()).get("crawl_status")
        return st in FINAL_REC or str(st).startswith("wayback-")     # recovered from the Wayback Machine: final
    except ValueError:
        return False


def manifest(key: str) -> dict | None:
    mp = RAW / key.replace("/", "__") / "manifest.json"
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text())
    except ValueError:
        return None


def need_fetch(dio: str, s: dict) -> bool:
    if s["social_only"] or out_exists(dio, s["key"]):
        return False
    m = manifest(s["key"])
    return not (m and m.get("complete") and m.get("status") not in EGRESS)


def crawl(queue: list[tuple[str, dict]], workers: int, stop_at: float, log) -> dict:
    pacer = fs.Pacer(1.0)
    stats = Counter()
    lock = threading.Lock()
    egress_run = [0]
    halt = threading.Event()
    RAW.mkdir(parents=True, exist_ok=True)

    def work(item):
        dio, s = item
        if halt.is_set() or time.time() > stop_at:
            return
        site = fs.Site(s["key"], CF.https_only(s["home"]), str(RAW), pacer, False, today(), "sandbox",
                       time.monotonic() + max(30.0, min(fs.SITE_SECONDS, stop_at - time.time())))
        m = site.run()
        st = m.get("status") or "?"
        if st in EGRESS:
            shutil.rmtree(RAW / s["key"].replace("/", "__"), ignore_errors=True)     # never record our own refusal
            with lock:
                egress_run[0] += 1
                stats["egress_refused"] += 1
                if egress_run[0] >= 15:
                    ok, _ = CF.egress_open()
                    if not ok:
                        halt.set()
                        log("EGRESS CLOSED mid-chunk: stopping")
                    egress_run[0] = 0
            return
        with lock:
            egress_run[0] = 0
            stats[st] += 1
            stats["done" if m.get("complete") else "cut"] += 1
            n = stats["done"] + stats["cut"]
            if n % 25 == 0:
                log(f"  fetched {n}: {dict(stats)}")

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, queue))
    stats["halted_egress"] = int(halt.is_set())
    return dict(stats)


# ------------------------------------------------------------------------------------------------ parse (lane A's code)
_RD = None


def run_diocese():
    """ingest/california/sites/run_diocese.py, pointed at the US folders (imported at run time: picks up lane A's work)."""
    global _RD
    if _RD is None:
        import requests
        import requests.utils
        requests.utils.default_user_agent = lambda *a, **k: CF.UA      # honest name for the live calendar-feed reads too
        import run_diocese as rd
        rd.RAW, rd.OUT, rd.TODAY = RAW, OUT, today()
        _RD = rd
    return _RD


def parse(dio: str, only_all: bool = False, log=print) -> int:
    rd = run_diocese()
    inp = json.loads((INPUTS / f"{dio}.json").read_text())
    keys = set()
    for s in inp["sites"]:
        fn = OUT / dio / (s["key"].replace("/", "__") + ".json")
        if s["social_only"]:
            if not fn.exists():
                keys.add(s["key"])
            continue
        m = manifest(s["key"])
        if not m or not m.get("complete") or m.get("status") in EGRESS:
            continue
        if fn.exists() and out_exists(dio, s["key"]) and str(json.loads(fn.read_text()).get("crawl_status", "")).startswith("wayback-") \
                and m.get("status") != "ok":
            continue                      # a Wayback recovery is not overwritten by a re-parse of the live challenge page
        if only_all or not fn.exists() or fn.stat().st_mtime < (RAW / s["key"].replace("/", "__") / "manifest.json").stat().st_mtime:
            keys.add(s["key"])
    if not keys:
        if (OUT / dio).exists() and any(p.name != "summary.json" for p in (OUT / dio).glob("*.json")):
            rd.finalize(dio)
        return 0
    log(f"  parse {dio}: {len(keys)} sites")
    rd.cmd_parse(dio, keys, False)        # parses `keys`, writes records, then finalize(dio) -> summary.json
    return len(keys)


# ------------------------------------------------------------------------------------------------ progress / readme
def write_progress(inputs: dict[str, dict], extra: dict | None = None) -> dict:
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}
    prog.setdefault("chunks", [])
    if extra:
        prog["chunks"].append(extra)
        prog["chunks"] = prog["chunks"][-60:]
    ds = {}
    for dio in diocese_order(inputs):
        sites = inputs[dio]["sites"]
        recs = [json.loads(p.read_text()) for p in (OUT / dio).glob("*.json") if p.name != "summary.json"] if (OUT / dio).exists() else []
        st = Counter("wayback" if str(r.get("crawl_status")).startswith("wayback-") else r.get("crawl_status") for r in recs)
        done = sum(1 for r in recs if r.get("crawl_status") in FINAL_REC or str(r.get("crawl_status")).startswith("wayback-"))
        ds[dio] = {"sites_in_list": len(sites), "records": done, "finished": done >= len(sites),
                   "ok": st.get("ok", 0), "by_status": dict(st.most_common())}
    prog["dioceses"] = ds
    tot = Counter()
    for v in ds.values():
        tot["sites_in_list"] += v["sites_in_list"]
        tot["records"] += v["records"]
        tot["ok"] += v["ok"]
    prog["totals"] = dict(tot)
    prog["us_parish_pages_discovermass"] = 20147
    prog["updated_at"] = now_iso()
    OUT.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(prog, indent=1, ensure_ascii=False) + "\n")
    return prog


def cmd_readme() -> None:
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"dioceses": {}, "chunks": []}
    dstat = {}
    p = DIR_DIR / "directory_status.json"
    if p.exists():
        dstat = json.loads(p.read_text())
    L = ["# US parish websites (`data/real/us/sites/`)", "",
         "Lane G4 of docs/HANDOFF.md: California's parish-website crawl (data/real/california/sites/) run for every US diocese. "
         "Files only; nothing here is in Supabase. Code: `ingest/us/sites/run_state.py` (list + crawl + parse), which imports "
         "`ingest/california/sites/fetch_site.py` (crawler) and `run_diocese.py` / `parse_site.py` / `events.py` (parser, "
         "templates, calendar feeds, privacy screen) at run time, so lane A's template work applies here automatically.", "",
         "## Sources of the website list", "",
         "1. The diocese's own directory where it is machine-readable: `data/real/us/dioceses/<slug>/parishes.json`, written by "
         "`ingest/us/dioceses/directory_probe.py` (status per see in `data/real/us/dioceses/directory_status.json`). Primary.",
         "2. DiscoverMass church pages, `data/real/us/discovermass/<ST>.websites.json` (lane G3). Fallback; each website is "
         "assigned to a diocese by the DiscoverMass diocese line (unmatched lines go to `unassigned-<st>`).",
         "Sites are keyed by domain (one site may serve a parish and its missions). Not crawled: social-media pages (kept as "
         "`social_only` with the link), the dioceses' own sites, LPi-hosted pages (parishesonline.com) and hosts that read as schools.", "",
         "## Rules kept", "",
         "- Honest user agent `FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`; robots.txt Disallow and "
         "Crawl-delay honoured; one request at a time per host, at least 1 s apart; up to 24 different hosts at once; TLS verified; "
         "no cookies, no JavaScript.",
         "- A challenge (Cloudflare, Sucuri, Imperva, captcha) is recorded as `challenge`; 403/429 as `blocked_403`/`blocked_429`. "
         "Never retried harder, solved or worked around.",
         "- If our own network refuses a request (the sandbox proxy answers 403 to CONNECT), nothing is recorded for that site and "
         "the chunk stops; it is not a fact about the parish.",
         "- Privacy: the same screens as California (`ingest/public/privacy.py` screen + contact sweep on every record; calendar "
         "events through `ingest/connectors/calendars/privacy.py` and the shared detector). No names, phones or e-mails of private "
         "people.", "",
         "## Files", "",
         "Same shape as `data/real/california/sites/` (see its README): `<diocese>/<domain>.json` per site, `<diocese>/summary.json`, "
         "and `progress.json` (per-diocese counts and the log of chunks).", "",
         "## Resume", "",
         "From a clean clone: `bash scripts/us/sites_chunk.sh` (pull, 100 minutes over the next unfinished dioceses, commit, push). "
         "By hand: `python3 -I ingest/us/sites/run_state.py chunk --minutes 100`. Directory probe: "
         "`python3 -I ingest/us/dioceses/directory_probe.py`.", "",
         "## Status per diocese", ""]
    ds = prog.get("dioceses", {})
    if not ds:
        L.append("No diocese crawled yet (see the log below).")
    else:
        L += ["| diocese | sites in list | records | ok | challenge | blocked | dead | social-only |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        dead = {"dns", "not_found", "parked", "ssl_error", "conn_error", "timeout", "unreachable_via_proxy", "http_4xx", "http_5xx", "not_html"}
        for dio, v in ds.items():
            b = v["by_status"]
            L.append(f"| {dio} | {v['sites_in_list']} | {v['records']} | {b.get('ok', 0)} | {b.get('challenge', 0)} | "
                     f"{b.get('blocked_403', 0) + b.get('blocked_429', 0) + b.get('http_202', 0) + b.get('robots_disallowed', 0)} | "
                     f"{sum(n for k, n in b.items() if k in dead)} | {b.get('social_only', 0)} |")
    if dstat.get("counts"):
        L += ["", f"Diocese directory probe: {dstat['counts']}."]
    L += ["", "## Chunk log", ""]
    for c in prog.get("chunks", [])[-15:]:
        L.append(f"- {c.get('started_at')}: {c.get('result')}")
    L.append("")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "README.md").write_text("\n".join(L), "utf-8")


# ------------------------------------------------------------------------------------------------ main
def cmd_chunk(a) -> int:
    t0 = time.time()
    log = lambda s: print(f"{now_iso()} {s}", flush=True)  # noqa: E731
    started = now_iso()
    if not a.skip_egress_check:
        ok, probes = CF.egress_open()
        if not ok:
            log("EGRESS CLOSED: this machine cannot reach ordinary websites (proxy refusal). Nothing crawled.")
            inputs = build_inputs()
            write_progress(inputs, {"started_at": started, "result": "egress closed (sandbox proxy refused every probe host); nothing crawled",
                                    "probes": probes})
            cmd_readme()
            return 3
    inputs = build_inputs()
    order = diocese_order(inputs)
    if a.state:
        sees = {s["slug"]: s["see_state"] for s in load_sees()}
        sts = {x.upper() for x in a.state.split(",")}
        order = [d for d in order if sees.get(d) in sts or (d.startswith("unassigned-") and d[-2:].upper() in sts)]
    if a.diocese:
        want = [x for x in a.diocese.split(",") if x]
        order = [d for d in want if d in inputs]
    queue = []
    for d in order:
        queue += [(d, s) for s in inputs[d]["sites"] if need_fetch(d, s)]
    log(f"{len(inputs)} dioceses with a list, {sum(len(v['sites']) for v in inputs.values())} sites; {len(queue)} to fetch this chunk")
    reserve = min(25 * 60, a.minutes * 60 * 0.25)
    stop_at = t0 + a.minutes * 60 - reserve
    stats = crawl(queue, a.workers, stop_at, log) if queue else {}
    log(f"crawl: {stats}")
    touched = []
    for d in order:
        if time.time() > t0 + a.minutes * 60 - 60:
            break
        try:
            if parse(d, log=log):
                touched.append(d)
        except Exception as e:  # noqa: BLE001 - one diocese must not stop the chunk
            log(f"  PARSE FAILED {d}: {e!r}")
    write_progress(inputs, {"started_at": started, "ended_at": now_iso(), "result": f"fetched {stats}; parsed {len(touched)} dioceses",
                            "touched": touched})
    cmd_readme()
    (RAW / "_last_touched.json").write_text(json.dumps(touched))
    log(f"done; touched {touched}")
    return 4 if stats.get("halted_egress") else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["inputs", "chunk", "parse", "readme", "status"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--state", default="")
    ap.add_argument("--diocese", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--skip-egress-check", action="store_true", help="local tests only")
    a = ap.parse_args()
    if a.cmd == "inputs":
        inp = build_inputs()
        for d in diocese_order(inp):
            print(f"{d:70s} {len(inp[d]['sites'])}")
    elif a.cmd == "chunk":
        return cmd_chunk(a)
    elif a.cmd == "parse":
        parse(a.slug, a.all)
        write_progress(build_inputs())
    elif a.cmd == "readme":
        cmd_readme()
    elif a.cmd == "status":
        print(json.dumps(write_progress(build_inputs())["totals"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
