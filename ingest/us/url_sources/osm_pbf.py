"""OpenStreetMap (Geofabrik state extracts) -> Catholic places of worship with a website tag, per US state.

No Overpass: for each state, one file https://download.geofabrik.de/north-america/us/<state>-latest.osm.pbf is streamed
to a temp file, filtered, and DELETED; only the extracted facts are kept (data/raw/us/url_sources/osm/<ST>.json,
gitignored, plus the committed outputs below). We use OSM as a lookup of facts (name, website tag, location of a church),
not as a redistributed database. Attribution: (c) OpenStreetMap contributors, ODbL 1.0
(https://www.openstreetmap.org/copyright).

Filter: amenity=place_of_worship + religion=christian + denomination in (catholic, roman_catholic) + a website /
contact:website / url tag. Nodes, ways (centroid = mean of node locations) and relations (tags only; no centroid).
Tooling: pyosmium (`pip install osmium`); if the `osmium` command (osmium-tool) exists the extract is first cut down with
`osmium tags-filter` so pyosmium only reads a tiny file; otherwise pyosmium reads the whole state with a disk-backed node index.

Outputs
  data/real/us/url_sources/osm.json               every record found (all states done so far) + counts
  data/real/us/url_sources/osm/<ST>.websites.json websites in the run_state shape, deduplicated by host, minus hosts that are
      diocese/school/social/aggregator sites (rules of merge.py) and minus hosts already in other websites files
      (url_sources/**, discovermass/) or already crawled (data/real/us/sites/**).

Resume: python3 -u ingest/us/url_sources/osm_pbf.py --minutes 110     (states already in osm.json are skipped)
        python3 -u ingest/us/url_sources/osm_pbf.py --states DE,RI    (just these; --force to redo)
        python3 -I ingest/us/url_sources/osm_pbf.py --build           (rebuild outputs from the saved records, no download)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import merge as M  # noqa: E402  (norm_url, host_of, key_of, SKIP_HOST, SCHOOL_HOST, CHURCH_WORD, SOCIAL_HOSTS)

ROOT = M.ROOT
US = M.US
OUT = US / "url_sources"
OSM_OUT = OUT / "osm"
RAW = ROOT / "data" / "raw" / "us" / "url_sources" / "osm"
UA = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"
BASE = "https://download.geofabrik.de/north-america/us/"
LABEL = "OpenStreetMap contributors, ODbL"
STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district-of-columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii",
    "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire", "NJ": "new-jersey",
    "NM": "new-mexico", "NY": "new-york", "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode-island", "SC": "south-carolina", "SD": "south-dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming",
}
# smallest first, so a short run finishes many states and a failure on a big one costs little
ORDER = ["DC", "DE", "RI", "VT", "WY", "HI", "NH", "ND", "SD", "AK", "ME", "MT", "ID", "NE", "WV", "NM", "NV", "KS", "UT", "AR",
         "MS", "IA", "CT", "OK", "OR", "SC", "AL", "KY", "LA", "CO", "MD", "MN", "TN", "WI", "AZ", "MO", "IN", "VA", "MA", "WA",
         "NJ", "NC", "GA", "OH", "IL", "MI", "PA", "FL", "NY", "TX", "CA"]
DENOM = {"catholic", "roman_catholic"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def wanted(t) -> bool:
    return (t.get("amenity") == "place_of_worship" and t.get("religion") == "christian"
            and (t.get("denomination") or "").strip().lower() in DENOM)


def website_of(t) -> str | None:
    for k in ("website", "contact:website", "url"):
        v = t.get(k)
        if v and v.strip():
            return v.strip()
    return None


def mkrec(kind: str, oid: int, t, lat, lng, st: str, fetched_at: str) -> dict:
    return {"osm_type": kind, "osm_id": oid, "name": t.get("name"), "website": website_of(t), "lat": lat, "lng": lng,
            "state": st, "city": t.get("addr:city"), "source": LABEL,
            "source_url": f"https://www.openstreetmap.org/{kind}/{oid}", "fetched_at": fetched_at}


def extract(pbf: str, st: str, fetched_at: str) -> tuple[list[dict], int]:
    """Return (records with a website, count of matching Catholic churches without one)."""
    import osmium

    recs: list[dict] = []
    nosite = [0]

    class H(osmium.SimpleHandler):
        def _take(self, kind, o, lat, lng):
            t = {tg.k: tg.v for tg in o.tags}
            if not wanted(t):
                return
            if not website_of(t):
                nosite[0] += 1
                return
            recs.append(mkrec(kind, o.id, t, lat, lng, st, fetched_at))

        def node(self, n):
            if n.tags and n.location.valid():
                self._take("node", n, n.location.lat, n.location.lon)

        def way(self, w):
            if not w.tags:
                return
            lat = lng = None
            try:
                pts = [(x.lat, x.lon) for x in w.nodes if x.location.valid()]
                if pts:
                    lat = round(sum(p[0] for p in pts) / len(pts), 7)
                    lng = round(sum(p[1] for p in pts) / len(pts), 7)
            except Exception:
                pass
            self._take("way", w, lat, lng)

        def relation(self, r):
            if r.tags:
                self._take("relation", r, None, None)

    h = H()
    tmp = tempfile.mkdtemp(prefix="osmidx-")
    try:
        h.apply_file(pbf, locations=True, idx="sparse_file_array," + os.path.join(tmp, "nodes.idx"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return recs, nosite[0]


def prefilter(pbf: Path, work: Path) -> Path:
    """osmium-tool: keep only place_of_worship objects (and the nodes of matching ways). Falls back to the full file."""
    if not shutil.which("osmium"):
        return pbf
    out = work / "worship.osm.pbf"
    r = subprocess.run(["osmium", "tags-filter", "-O", "-o", str(out), str(pbf), "nwr/amenity=place_of_worship"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        print("osmium tags-filter failed, reading full file:", r.stderr[-300:], file=sys.stderr)
        return pbf
    return out


def download(slug: str, dest: Path) -> tuple[bool, str]:
    url = f"{BASE}{slug}-latest.osm.pbf"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            return True, f"{dest.stat().st_size / 1e6:.0f} MB"
        except Exception as e:  # noqa: BLE001
            why = f"{type(e).__name__}: {e}"
            print(f"  download attempt {attempt + 1} failed: {why}", file=sys.stderr)
            dest.unlink(missing_ok=True)
            time.sleep(20 * (attempt + 1))
    return False, why


def process_state(st: str) -> dict | None:
    slug = STATES[st]
    work = Path(tempfile.mkdtemp(prefix=f"osm-{st}-"))
    try:
        pbf = work / f"{slug}.osm.pbf"
        ok, info = download(slug, pbf)
        if not ok:
            return {"state": st, "failed": info}
        fetched_at = now()
        small = prefilter(pbf, work)
        recs, nosite = extract(str(small), st, fetched_at)
        return {"state": st, "fetched_at": fetched_at, "records": recs, "catholic_without_website": nosite,
                "pbf_size": info, "geofabrik_url": f"{BASE}{slug}-latest.osm.pbf"}
    finally:
        shutil.rmtree(work, ignore_errors=True)       # the extract is never kept


# --------------------------------------------------------------------------------------------- outputs
def load_done() -> tuple[dict, dict]:
    """(records by state, per-state meta) from osm.json and the raw cache."""
    recs: dict[str, list] = {}
    meta: dict[str, dict] = {}
    p = OUT / "osm.json"
    if p.exists():
        d = json.loads(p.read_text())
        for r in d.get("records", []):
            recs.setdefault(r["state"], []).append(r)
        meta = d.get("states", {})
        for st in meta:
            recs.setdefault(st, [])
    for f in RAW.glob("*.json") if RAW.exists() else []:
        if f.stem in STATES and f.stem not in meta:
            d = json.loads(f.read_text())
            recs[f.stem] = d["records"]
            meta[f.stem] = {k: d[k] for k in ("fetched_at", "catholic_without_website", "pbf_size") if k in d}
    return recs, meta


def excluded_keys() -> tuple[set, set]:
    """Website keys already present elsewhere, and hosts already crawled."""
    other = set()
    files = list(OUT.glob("*.websites.json")) + [p for p in OUT.glob("**/*.websites.json") if OSM_OUT not in p.parents]
    files += list((US / "discovermass").glob("*.websites.json"))
    for p in files:
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        for w in (d.get("websites", []) if isinstance(d, dict) else d):
            if isinstance(w, dict) and w.get("sources") == ["osm"]:
                continue                         # merge.py's copy of OUR records is not "another source"
            u = M.norm_url(w.get("url") or w.get("website") if isinstance(w, dict) else None)
            if u:
                other.add(M.key_of(u))
    crawled = set()
    sd = US / "sites"
    if sd.exists():
        for p in sd.glob("*/*.json"):
            crawled.add(p.stem.lower().removeprefix("www."))
    return other, crawled


def build_outputs(recs: dict, meta: dict) -> dict:
    sees = json.loads((US / "dioceses.json").read_text())["dioceses"]
    dio_hosts = {M.host_of(M.norm_url(s["website"]) or "") for s in sees if s.get("website")}
    other, crawled = excluded_keys()
    OSM_OUT.mkdir(parents=True, exist_ok=True)
    built = now()
    skipped: Counter = Counter()
    by_state_sites: dict[str, int] = {}
    seen_global: set = set()
    allrecs = [r for st in sorted(recs) for r in recs[st]]
    for st in sorted(recs):
        groups: dict[str, dict] = {}
        social = []
        for r in recs[st]:
            u = M.norm_url(r["website"])
            if not u:
                skipped["bad_url"] += 1
                continue
            h, k = M.host_of(u), M.key_of(u)
            is_social = any(h == x or h.endswith("." + x) for x in M.SOCIAL_HOSTS)
            if h in dio_hosts and not is_social:
                skipped["diocese_site"] += 1
                continue
            if M.SKIP_HOST.search(h):
                skipped["non_parish_host"] += 1
                continue
            if M.SCHOOL_HOST.search(h) and not M.CHURCH_WORD.search(h.replace("school", "")):
                skipped["school_host"] += 1
                continue
            if k in other:
                skipped["already_in_other_websites_files"] += 1
                continue
            if h in crawled or h.removeprefix("www.") in crawled:
                skipped["already_crawled"] += 1
                continue
            ent = {"slug": f"osm-{r['osm_type']}-{r['osm_id']}", "name": r["name"], "city": r.get("city"), "diocese": None,
                   "lat": r.get("lat"), "lng": r.get("lng"), "source": LABEL}
            if is_social:
                social.append(dict(ent, url=u))
                continue
            if k in seen_global and k not in groups:
                skipped["duplicate_in_other_state"] += 1
                continue
            g = groups.setdefault(k, {"url": u, "sources": ["osm"], "churches": []})
            if len(u) < len(g["url"]):
                g["url"] = u
            g["churches"].append(ent)
        seen_global |= set(groups)
        sites = sorted(groups.values(), key=lambda g: g["churches"][0]["slug"])
        by_state_sites[st] = len(sites)
        (OSM_OUT / f"{st}.websites.json").write_text(json.dumps({
            "source": "OpenStreetMap contributors (ODbL): website tags from Geofabrik state extracts, used as a lookup of facts",
            "source_label": "osm-pbf", "state": st, "built_at": built,
            "note": "URLs unverified; one entry per distinct host; hosts already in other websites files or already crawled are excluded.",
            "counts": {"osm_records_with_website": len(recs[st]), "distinct_websites": len(sites), "social_only": len(social)},
            "websites": sites, "social_only": social}, ensure_ascii=False, indent=1) + "\n")
    counts = {"records": len(allrecs), "states_done": len(recs), "distinct_websites_after_exclusions": sum(by_state_sites.values()),
              "skipped": dict(skipped),
              "by_state": {st: {"osm_records_with_website": len(recs[st]), "websites_after_exclusions": by_state_sites[st],
                                "catholic_without_website": meta.get(st, {}).get("catholic_without_website")}
                           for st in sorted(recs)}}
    (OUT / "osm.json").write_text(json.dumps({
        "source": "OpenStreetMap contributors, ODbL (Geofabrik state extracts)", "license": "ODbL 1.0, https://www.openstreetmap.org/copyright",
        "attribution": "© OpenStreetMap contributors, ODbL. Used as a lookup of facts, not redistributed as a database.",
        "built_at": built, "states": meta, "counts": counts, "records": allrecs}, ensure_ascii=False, indent=1) + "\n")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states")
    ap.add_argument("--minutes", type=float, default=0, help="stop starting new states after this many minutes (0 = no limit)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--build", action="store_true", help="only rebuild outputs from saved records")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    recs, meta = load_done()
    t0 = time.time()
    if not a.build:
        todo = a.states.upper().split(",") if a.states else ORDER
        fails = 0
        for st in todo:
            if st not in STATES:
                print("unknown state", st, file=sys.stderr)
                continue
            if st in meta and not a.force:
                continue
            if a.minutes and (time.time() - t0) / 60 > a.minutes * 0.8:      # a big state needs time left
                print("time budget reached; stopping before", st, file=sys.stderr)
                break
            print(f"{st}: downloading {STATES[st]} ...", file=sys.stderr, flush=True)
            res = process_state(st)
            if res.get("failed"):
                fails += 1
                print(f"{st}: FAILED {res['failed']}", file=sys.stderr)
                if fails >= 3:
                    break
                continue
            fails = 0
            (RAW / f"{st}.json").write_text(json.dumps(res))
            recs[st] = res["records"]
            meta[st] = {k: res[k] for k in ("fetched_at", "catholic_without_website", "pbf_size", "geofabrik_url")}
            print(f"{st}: {len(res['records'])} churches with a website ({res['catholic_without_website']} without) "
                  f"[{res['pbf_size']}, {(time.time() - t0) / 60:.1f} min]", file=sys.stderr, flush=True)
            counts = build_outputs(recs, meta)           # write after every state so a cancelled run loses nothing
    counts = build_outputs(recs, meta)
    print(json.dumps({k: counts[k] for k in ("records", "states_done", "distinct_websites_after_exclusions", "skipped")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
