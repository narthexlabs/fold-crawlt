#!/usr/bin/env python3
"""GCatholic.org church index -> website lists for the national parish-site crawler (lane G8).

Facts only (name, city, state, status, website URL, coordinates decoded from the Plus Code, GCatholic id); none of
GCatholic's descriptive text is kept. robots.txt allows us (disallows /temp/ and AI-training bots); ONE crawler,
>= 2.0 s between requests, stop and record on 429 / 403 / challenge. Honest user agent (fetch_site default).

  python3 ingest/us/url_sources/gcatholic.py --minutes 100          # resumable; raw HTML cached under data/raw/
  python3 ingest/us/url_sources/gcatholic.py --build               # only rebuild outputs from the cache
  --only CODE[,CODE]  limit to dioceses; --index-only  just fetch/parse the diocese index
"""
from __future__ import annotations

import argparse
import html as H
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
sys.path.insert(0, str(ROOT / "ingest" / "us" / "url_sources"))
sys.path.insert(0, str(ROOT / "ingest" / "public"))
import fetch_site as fs  # noqa: E402
import merge as M  # noqa: E402  (read-only reuse of the URL normaliser and skip rules)
from common import decode_plus_code  # noqa: E402

US = ROOT / "data" / "real" / "us"
OUT = US / "url_sources"
GC_OUT = OUT / "gcatholic"
RAW = ROOT / "data" / "raw" / "us" / "url_sources" / "gcatholic"
BASE = "https://gcatholic.org"
INDEX_URL = BASE + "/dioceses/country/US"
ATTR = "GCatholic.org (facts only: names, URLs, coordinates)"
MIN_GAP = 2.2
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
          "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
          "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
          "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
          "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
          "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
          "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
          "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR", "Guam": "GU"}
LATIN_TYPES = ("metr", "dioc", "mltr", "pord")
# GCatholic code -> USCCB slug where the names differ too much to match (Eastern sees, military, ordinariate, etc.)
ALIAS = {"unit0": "archdiocese-of-the-military-services", "chai0": "diocese-of-the-personal-ordinariate-of-the-chair-of-st-peter",
         "kans0": "archdiocese-of-kansas-city-in-kansas", "spri1": "diocese-of-springfield-massachusetts",
         "vann0": "holy-protection-of-mary-byzantine-catholic-eparchy-of-phoenix",
         "olde0": "syriac-catholic-diocese-of-our-lady-of-deliverance-in-the-united-states",
         "olle0": "maronite-eparchy-of-our-lady-of-lebanon",
         "usaa0": "armenian-catholic-eparchy-of-our-lady-of-nareg-in-the-usa-canada",
         "pass0": "byzantine-catholic-eparchy-of-passaic", "zgeo1": "eparchy-of-st-george-in-canton-for-the-romanians",
         "zjos7": "ukrainian-catholic-eparchy-of-st-josaphat-parma-oh",
         "smqp0": "syro-malankara-catholic-eparchy-in-the-usa", "znic1": "st-nicholas-of-chicago-for-ukrainians",
         "zpet2": "chaldean-catholic-eparchy-of-st-peter-the-apostle", "ztho2": "st-thomas-syro-malabar-diocese-of-chicago",
         "ztho1": "chaldean-eparchy-of-saint-thomas-the-apostle", "stam0": "ukrainian-catholic-eparchy-of-stamford",
         "pitt0": "byzantine-catholic-archeparchy-of-pittsburgh", "phil1": "ukrainian-catholic-archeparchy-of-philadelphia"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Stop(Exception):
    pass


class Fetcher:
    def __init__(self, deadline: float):
        self.last = 0.0
        self.deadline = deadline
        self.n = 0

    def get(self, path: str, cache: Path) -> str | None:
        """Cached HTML, else one polite GET. Raises Stop on 403/429/challenge/time. Returns None on 404."""
        if cache.exists():
            return cache.read_text("utf-8", "replace")
        if time.time() > self.deadline:
            raise Stop("time")
        wait = MIN_GAP - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        r = fs.http_get(BASE + path, timeout=30, max_bytes=3_000_000)
        self.last = time.monotonic()
        self.n += 1
        st, body = r["status"], r["body"] or b""
        txt = body.decode("utf-8", "replace")
        if st in (403, 429) or (st == 200 and len(txt) < 30000 and re.search(r"Just a moment|cf-challenge|challenge-platform|verify you are human", txt, re.I)):
            note = {"at": now(), "path": path, "status": st, "bytes": len(txt)}
            (GC_OUT / "STOPPED.json").write_text(json.dumps(note) + "\n")
            raise Stop(f"stopped: HTTP {st} on {path}")
        if st == 404:
            return None
        if st != 200:
            if r["err"] in ("proxy_denied", "unreachable_via_proxy"):
                raise Stop(f"egress: {r['err']}")
            return None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(txt, "utf-8")
        return txt


# --------------------------------------------------------------------------------------------- parsing
def nm(s: str) -> str:
    s = re.sub(r"^(?:the\s+)?(?:roman\s+catholic\s+)?(?:metropolitan\s+)?(?:arch)?(?:diocese|eparchy|archeparchy)\s+of\s+", "",
               (s or "").strip(), flags=re.I)
    s = re.sub(r"\s*\(.*?\)", "", s)
    return re.sub(r"[^a-z]", "", s.lower().replace("saint", "st").replace("&", "and"))


def parse_index(h: str) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r"\['([^']*)','(s\d+-[a-z]+)','/dioceses/diocese/([a-z0-9]+)','([^']*)','([^']*)'\]", h):
        _, stype, code, name, typ = m.groups()
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": H.unescape(name), "type": typ, "stype": stype,
                    "latin": stype.split("-")[1] in LATIN_TYPES and "Rite)" not in name and "Eparchy" not in typ})
    return out


def map_dioceses(gcs: list[dict]) -> tuple[dict, list[dict]]:
    sees = json.loads((US / "dioceses.json").read_text())["dioceses"]
    by: dict[tuple[str, bool], str] = {}
    for s in sees:
        by.setdefault((nm(s["name"]), s["rite"] == "Latin"), s["slug"])
    alias = {"washingtondc": "washington", "portlandinmaine": "portlandinmaine", "stpaulandminneapolis": "stpaulandminneapolis"}
    mp, un = {}, []
    for g in gcs:
        if g["code"] in ALIAS:
            mp[g["code"]] = ALIAS[g["code"]]
            continue
        k = nm(g["name"])
        slug = by.get((k, g["latin"])) or by.get((alias.get(k, k), g["latin"])) or by.get((k, not g["latin"]))
        if not slug:                                  # "Boise City" vs "Boise", "Wheeling-Charleston" etc.
            cands = [v for (kk, lat), v in by.items() if lat == g["latin"] and (kk.startswith(k) or k.startswith(kk)) and min(len(k), len(kk)) > 4]
            slug = cands[0] if len(cands) == 1 else None
        if slug:
            mp[g["code"]] = slug
        else:
            un.append(g)
    return mp, un


ROW = re.compile(r'<tr id="L\d+"><td><span class="znote">([^<]*)</span></td><td>(.*?)</td></tr>', re.S)


def parse_churches(h: str) -> list[dict]:
    out = []
    for m in ROW.finditer(h):
        city = H.unescape(m.group(1)).strip()
        cell = m.group(2)
        a = re.search(r'<a href="([^"]+)" class="chw">(.*?)</a>(?:<span class="znote">\s*\(([^)]*)\)</span>)?', cell, re.S)
        if not a:
            continue
        href, name, stat = a.groups()
        mm = re.search(r"([a-z0-9-]+)/(\d+)$", href)
        if not mm:
            continue
        out.append({"path": f"/churches/{mm.group(1)}/{mm.group(2)}", "gc_id": mm.group(2),
                    "name": re.sub(r"<[^>]+>", "", H.unescape(name)).strip(), "city": city,
                    "status": (stat or "").strip().lower() or "other"})
    return out


def parse_church(h: str) -> dict:
    d: dict = {}
    m = re.search(r'<span class="label">Website: </span><a href="([^"]+)"[^>]*class="link"', h) or \
        re.search(r'<span class="label">Website: </span><a [^>]*href="([^"]+)"', h)
    d["website"] = H.unescape(m.group(1)).strip() if m else None
    m = re.search(r"query=([0-9A-Z]{4,8}(?:%2B|\+)[0-9A-Z]*)", h)
    d["plus_code"] = m.group(1).replace("%2B", "+") if m else None
    d["lat"] = d["lng"] = None
    if d["plus_code"]:
        pc = d["plus_code"]
        if len(pc.split("+")[0]) == 8:
            ll = decode_plus_code(pc)
            if ll:
                d["lat"], d["lng"] = ll
    m = re.search(r'<span class="label">Address: </span>(.*?)</p>', h, re.S)
    d["state"] = d["zip"] = None
    if m:
        txt = m.group(1)
        r = re.search(r'<span class="zregion">([^<]+)</span>\s*(\d{5})?', txt)
        if r:
            d["state"] = STATES.get(H.unescape(r.group(1)).strip())
            d["zip"] = r.group(2)
    return d


# --------------------------------------------------------------------------------------------- run
def see_state_map() -> dict:
    return {s["slug"]: s["see_state"] for s in json.loads((US / "dioceses.json").read_text())["dioceses"]}


def priority(mp: dict, gcs: list[dict]) -> list[dict]:
    try:
        st = json.loads((US / "dioceses" / "directory_status.json").read_text())["sees"]
    except Exception:
        st = {}
    bad = {"challenge", "dead", "blocked", "egress_closed"}
    rank = lambda g: 0 if (st.get(mp.get(g["code"]), {}) or {}).get("status") in bad else 1
    return sorted([g for g in gcs if g["code"] in mp], key=lambda g: (rank(g), g["name"]))


def crawl(args) -> int:
    GC_OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    if (GC_OUT / "STOPPED.json").exists() and not args.force:
        print("STOPPED.json present (a 403/429/challenge was seen); delete it deliberately to resume"); return 2
    f = Fetcher(time.time() + args.minutes * 60)
    status = "ok"
    try:
        h = f.get("/dioceses/country/US", RAW / "index.html")
        gcs = parse_index(h or "")
        mp, un = map_dioceses(gcs)
        (GC_OUT / "diocese_map.json").write_text(json.dumps({
            "source_url": INDEX_URL, "fetched_at": now(), "gcatholic_dioceses": len(gcs), "mapped": len(mp), "unmatched": un,
            "map": {g["code"]: {"slug": mp[g["code"]], "name": g["name"], "type": g["type"]} for g in gcs if g["code"] in mp}},
            indent=1, ensure_ascii=False) + "\n")
        print(f"index: {len(gcs)} GCatholic sees, mapped {len(mp)}, unmatched {len(un)}", flush=True)
        if args.index_only:
            return 0
        only = set(args.only.split(",")) if args.only else None
        for g in priority(mp, gcs):
            if only and g["code"] not in only:
                continue
            c = g["code"]
            lh = f.get(f"/churches/local/{c}", RAW / "local" / f"{c}.html")
            rows = parse_churches(lh or "")
            todo = sum(1 for r in rows if not (RAW / "church" / f"{r['gc_id']}.html").exists())
            print(f"{c} {g['name']}: {len(rows)} churches, {todo} to fetch", flush=True)
            for r in rows:
                f.get(r["path"], RAW / "church" / f"{r['gc_id']}.html")
            (RAW / "local" / f"{c}.done").write_text(now())
    except Stop as e:
        status = str(e)
        print("STOP:", status, flush=True)
    build()
    print("requests this run:", f.n, "status:", status)
    return 0 if status in ("ok", "time") else 1


def build() -> dict:
    ix = RAW / "index.html"
    if not ix.exists():
        return {}
    gcs = parse_index(ix.read_text("utf-8", "replace"))
    mp, un = map_dioceses(gcs)
    sstate = see_state_map()
    names = {g["code"]: g["name"] for g in gcs}
    usccb = {s["slug"]: s["name"] for s in json.loads((US / "dioceses.json").read_text())["dioceses"]}
    recs, done = [], []
    for g in gcs:
        c = g["code"]
        lp = RAW / "local" / f"{c}.html"
        if not lp.exists() or c not in mp:
            continue
        slug = mp[c]
        complete = True
        for r in parse_churches(lp.read_text("utf-8", "replace")):
            cp = RAW / "church" / f"{r['gc_id']}.html"
            if not cp.exists():
                complete = False
                continue
            d = parse_church(cp.read_text("utf-8", "replace"))
            recs.append({"gc_id": r["gc_id"], "name": r["name"], "city": r["city"], "state": d["state"] or sstate.get(slug),
                         "status": r["status"], "diocese_slug": slug, "gc_diocese": c, "website": d["website"],
                         "lat": d["lat"], "lng": d["lng"], "plus_code": d["plus_code"],
                         "source_url": BASE + r["path"], "fetched_at": datetime.fromtimestamp(cp.stat().st_mtime, timezone.utc)
                         .replace(microsecond=0).isoformat()})
        if complete:
            done.append(c)
    recs.sort(key=lambda r: (r["state"] or "", r["diocese_slug"], r["city"], r["name"]))
    (OUT / "gcatholic.json").write_text(json.dumps({
        "source": "GCatholic.org church pages", "source_label": "url-sources", "attribution": ATTR,
        "robots": "robots.txt allows; /temp/ and AI-training bots disallowed; no Crawl-delay; we wait >= 2.2 s",
        "built_at": now(), "dioceses_complete": len(done), "counts": {
            "churches": len(recs), "with_website": sum(bool(r["website"]) for r in recs),
            "with_latlng": sum(r["lat"] is not None for r in recs)}, "records": recs}, ensure_ascii=False, indent=1) + "\n")
    # DiscoverMass-shaped website files per state
    by_state: dict[str, list] = defaultdict(list)
    for r in recs:
        by_state[r["state"] or "_nostate"].append(r)
    skipped = Counter()
    nsites = Counter()
    for st, rs in by_state.items():
        sites: dict[str, dict] = {}
        social = []
        for r in rs:
            u = M.norm_url(r["website"])
            if not u:
                continue
            ent = {"slug": f"gc-{r['gc_id']}", "name": r["name"], "city": r["city"], "diocese": usccb.get(r["diocese_slug"]),
                   "lat": r["lat"], "lng": r["lng"], "source": "GCatholic.org"}
            h = M.host_of(u)
            if any(h == x or h.endswith("." + x) for x in M.SOCIAL_HOSTS):
                social.append(dict(ent, url=u))
                continue
            if M.SKIP_HOST.search(h):
                skipped["non_parish_host"] += 1
                continue
            if M.SCHOOL_HOST.search(h) and not M.CHURCH_WORD.search(h.replace("school", "")):
                skipped["school_host"] += 1
                continue
            e = sites.setdefault(M.key_of(u), {"url": u, "sources": ["gcatholic"], "churches": []})
            e["churches"].append(ent)
        if st == "_nostate":
            continue
        wl = sorted(sites.values(), key=lambda e: e["churches"][0]["slug"])
        nsites[st] = len(wl)
        (GC_OUT / f"{st}.websites.json").write_text(json.dumps({
            "source": "GCatholic.org church pages (website field)", "source_label": "url-sources", "attribution": ATTR,
            "state": st, "built_at": now(), "consumer": "lane G4 national parish-site crawler",
            "note": "URLs unverified; one entry per distinct host. Diocese-site filtering happens in run_state.build_inputs.",
            "counts": {"churches": len(rs), "distinct_websites": len(wl), "social_only": len(social)},
            "websites": wl, "social_only": social}, ensure_ascii=False, indent=1) + "\n")
    summ = {"built_at": now(), "gcatholic_sees": len(gcs), "mapped": len(mp), "unmatched": [u["name"] for u in un],
            "dioceses_complete": len(done), "churches": len(recs), "websites_by_state": dict(nsites.most_common()),
            "skipped_hosts": dict(skipped)}
    (GC_OUT / "summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False) + "\n")
    print(f"build: {len(done)} dioceses complete, {len(recs)} churches, {sum(nsites.values())} distinct sites", flush=True)
    return summ


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--only")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.build:
        build()
        sys.exit(0)
    sys.exit(crawl(a))
