#!/usr/bin/env python3
"""Build the crawl list for one diocese (run fresh at the start of each diocese pass).

    python3 -I ingest/california/sites/build_input.py <slug> [--out data/raw/california/sites/_inputs/<slug>.json]

Sources (read-only, both still being written by other agents):
  data/real/california/discovermass/churches.json          records with a `website` and a `diocese`
  data/real/california/dioceses/<slug>/parishes.json        the diocese's own directory (`website`)

Records are matched by normalised website domain (lower case, no "www.", no port, no trailing dot). A domain that
several records share (a parish and its missions, a parish and its school's church) is one site with several
matched ids. A host that serves several parishes under different top-level paths (diocese-hosted sub-sites,
sites.google.com/view/x, a wixsite.com user) is keyed by host + first path segment. A website that is only a
Facebook / Instagram page is not crawled: it is listed as `social_only` and its link goes straight to `social`.

Output: a list of
  {key, home, slug, directory_ids[], discovermass_ids[], names[], cities[], social_only}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
CA = ROOT / "data" / "real" / "california"
INPUTS = ROOT / "data" / "raw" / "california" / "sites" / "_inputs"

SOCIAL_HOSTS = ("facebook.com", "m.facebook.com", "fb.com", "fb.me", "instagram.com", "twitter.com", "x.com", "youtube.com",
                "tiktok.com", "linktr.ee")
PATH_HOSTS = ("sites.google.com", "wixsite.com", "weebly.com", "godaddysites.com", "square.site", "carrd.co", "webador.com",
              "parishesonline.com", "squarespace.com")


def norm_url(w: str | None) -> str | None:
    if not w or not isinstance(w, str):
        return None
    w = w.strip().strip("<>\"' ").split()[0] if w.strip() else ""
    if not w or "@" in w.split("/")[0] and "://" not in w:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", w, re.I):
        w = "https://" + w
    u = urlparse(w)
    host = (u.hostname or "").lower().rstrip(".")
    if not host or "." not in host or re.search(r"[^a-z0-9.-]", host):
        return None
    if u.scheme.lower() not in ("http", "https"):
        return None
    path = re.sub(r"/{2,}", "/", u.path or "/")
    return f"{u.scheme.lower()}://{host}{path}" + (f"?{u.query}" if u.query and len(u.query) < 40 else "")


def host_of(u: str) -> str:
    h = urlparse(u).hostname or ""
    return h[4:] if h.startswith("www.") else h


def first_seg(u: str) -> str:
    segs = [s for s in urlparse(u).path.split("/") if s]
    return segs[0].lower() if segs else ""


def site_key(u: str, split_hosts: set[str]) -> str:
    h = host_of(u)
    if any(h == x or h.endswith("." + x) for x in SOCIAL_HOSTS):
        segs = [s for s in urlparse(u).path.split("/") if s]
        return h + "/" + "/".join(s.lower() for s in segs[:1])
    if any(h == x or h.endswith("." + x) for x in PATH_HOSTS) or h in split_hosts:
        segs = [s.lower() for s in urlparse(u).path.split("/") if s]
        take = 2 if h == "sites.google.com" and segs[:1] in (["view"], ["site"]) else 1
        return h + ("/" + "/".join(segs[:take]) if segs else "")
    return h


def load_dm(slug: str) -> list[dict]:
    p = CA / "discovermass" / "churches.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text("utf-8"))
    return [r for r in d.get("records", []) if r.get("diocese") == slug]


def load_dir(slug: str) -> list[dict]:
    p = CA / "dioceses" / slug / "parishes.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text("utf-8"))
    return d if isinstance(d, list) else d.get("records", d.get("parishes", []))


def build(slug: str) -> dict:
    dm, dr = load_dm(slug), load_dir(slug)
    recs = []     # (url, source, id, name, city)
    for r in dr:
        recs.append((norm_url(r.get("website")), "directory", r.get("id") or r.get("name"), r.get("name"), r.get("city")))
    for r in dm:
        recs.append((norm_url(r.get("website")), "discovermass", r.get("id"), r.get("name"), r.get("city")))
    # hosts that serve several parishes under different first path segments
    by_host = defaultdict(set)
    for u, *_ in recs:
        if u:
            by_host[host_of(u)].add(first_seg(u))
    split_hosts = {h for h, segs in by_host.items() if len(segs) > 1 and any(segs)}
    sites: dict[str, dict] = {}
    for u, src, rid, name, city in recs:
        if not u:
            continue
        k = site_key(u, split_hosts)
        s = sites.setdefault(k, {"key": k, "home": u, "slug": slug, "directory_ids": [], "discovermass_ids": [],
                                 "names": [], "cities": [], "social_only": False, "homes_seen": []})
        if u not in s["homes_seen"]:
            s["homes_seen"].append(u)
        key = "directory_ids" if src == "directory" else "discovermass_ids"
        if rid and rid not in s[key]:
            s[key].append(rid)
        if name and name not in s["names"]:
            s["names"].append(name)
        if city and city not in s["cities"]:
            s["cities"].append(city)
    for s in sites.values():
        # home: the shortest URL seen (a bare domain beats a deep link), https preferred when both exist
        hs = sorted(s["homes_seen"], key=lambda x: (len(urlparse(x).path.strip("/")), not x.startswith("https"), len(x)))
        s["home"] = hs[0]
        h = host_of(s["home"])
        s["social_only"] = any(h == x or h.endswith("." + x) for x in SOCIAL_HOSTS)
        del s["homes_seen"]
    no_site_dir = [r.get("id") or r.get("name") for r in dr if not norm_url(r.get("website"))]
    no_site_dm = [r.get("id") for r in dm if not norm_url(r.get("website"))]
    return {"slug": slug, "directory_records": len(dr), "discovermass_records": len(dm),
            "directory_without_website": len(no_site_dir), "discovermass_without_website": len(no_site_dm),
            "sites": sorted(sites.values(), key=lambda s: s["key"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = build(a.slug)
    out = Path(a.out) if a.out else INPUTS / f"{a.slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", "utf-8")
    both = sum(1 for s in res["sites"] if s["directory_ids"] and s["discovermass_ids"])
    print(f"{a.slug}: directory {res['directory_records']} ({res['directory_without_website']} no website), "
          f"discovermass {res['discovermass_records']} ({res['discovermass_without_website']} no website) -> "
          f"{len(res['sites'])} sites ({both} matched in both, {sum(s['social_only'] for s in res['sites'])} social-only) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
