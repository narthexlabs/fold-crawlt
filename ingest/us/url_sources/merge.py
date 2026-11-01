"""Merge wikidata.json + osm.json into data/real/us/url_sources/<ST>.websites.json, in the shape
ingest/us/discovermass_crawl.py writes (and ingest/us/sites/run_state.py reads):
  {"websites": [{"url", "churches": [{"slug","name","city","diocese","lat","lng"}]}], "social_only": [{... "url"}], ...}

Deduplicated by registrable-ish host (www. stripped; path-hosting platforms keep their first path segment). Skips what
run_state skips: dioceses' own sites, LPi/DiscoverMass/USCCB/aggregator hosts and school hosts. Social-media pages go
to `social_only`. With --exclude-discovermass, hosts already listed in data/real/us/discovermass/*.websites.json
are dropped, so only NEW websites remain (default when such files exist).
Records with no state land in _nostate.json (not fed to the crawler).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
US = ROOT / "data" / "real" / "us"
OUT = US / "url_sources"
SOCIAL_HOSTS = ("facebook.com", "m.facebook.com", "fb.com", "fb.me", "instagram.com", "twitter.com", "x.com", "youtube.com",
                "tiktok.com", "linktr.ee")
PATH_HOSTS = ("sites.google.com", "wixsite.com", "weebly.com", "godaddysites.com", "square.site", "carrd.co", "webador.com",
              "parishesonline.com", "squarespace.com")
SKIP_HOST = re.compile(r"(?:^|\.)(?:parishesonline\.com|4lpi\.com|discovermass\.com|ecatholic\.com/?$|usccb\.org|"
                       r"masstimes\.org|catholicmasstime\.org|google\.com|goo\.gl|yelp\.com|wikipedia\.org)$", re.I)
SCHOOL_HOST = re.compile(r"school|academy|prep\b|highschool|\bhs\b|college|universit", re.I)
CHURCH_WORD = re.compile(r"church|parish|catholic|cathedral|basilica|shrine|mission|chapel|newman|st[-.]?|saint|our-?lady|"
                         r"holy|sacred|blessed", re.I)


def norm_url(w: str | None) -> str | None:
    if not w or not isinstance(w, str) or not w.strip():
        return None
    w = w.strip().strip("<>\"' ").split(";")[0].split()[0] if w.strip() else ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", w, re.I):
        if w.lower().startswith(("mailto:", "tel:")) or "@" in w.split("/")[0]:
            return None
        w = "https://" + w
    u = urlparse(w)
    if u.scheme not in ("http", "https") or not u.netloc or "." not in u.netloc:
        return None
    return w


def host_of(u: str) -> str:
    return urlparse(u).netloc.lower().split(":")[0].removeprefix("www.")


def key_of(u: str) -> str:
    h = host_of(u)
    if any(h == x or h.endswith("." + x) for x in PATH_HOSTS + SOCIAL_HOSTS):
        seg = [s for s in urlparse(u).path.split("/") if s]
        return h + "/" + (seg[0].lower() if seg else "")
    return h


def load_sources() -> list[dict]:
    out = []
    p = OUT / "wikidata.json"
    if p.exists():
        for r in json.loads(p.read_text())["records"]:
            out.append({"src": "wikidata", "slug": f"wd-{r['qid']}", "name": r["name"], "city": r.get("city"),
                        "state": r.get("state"), "diocese": r.get("diocese"), "lat": r.get("lat"), "lng": r.get("lng"),
                        "website": r["website"]})
    p = OUT / "osm.json"
    if p.exists():
        for r in json.loads(p.read_text())["records"]:
            out.append({"src": "osm", "slug": f"osm-{r['osm_type']}-{r['osm_id']}", "name": r["name"], "city": r.get("city"),
                        "state": r.get("state"), "diocese": None, "lat": r.get("lat"), "lng": r.get("lng"),
                        "website": r["website"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-exclude-discovermass", action="store_true")
    a = ap.parse_args()
    sees = json.loads((US / "dioceses.json").read_text())["dioceses"]
    dio_hosts = {host_of(norm_url(s["website"]) or "") for s in sees if s.get("website")}
    see_state = {}
    for s in sees:
        see_state[re.sub(r"[^a-z]", "", re.sub(r"^(?:the\s+)?(?:arch)?(?:diocese|eparchy)\s+of\s+", "", s["name"].lower()))] = s["see_state"]
    dm_keys = set()
    if not a.no_exclude_discovermass:
        for p in (US / "discovermass").glob("*.websites.json"):
            for w in json.loads(p.read_text()).get("websites", []):
                u = norm_url(w.get("url"))
                if u:
                    dm_keys.add(key_of(u))
    skipped, srcs = Counter(), defaultdict(Counter)
    groups: dict[str, dict] = {}
    for r in load_sources():
        u = norm_url(r["website"])
        srcs[r["src"]]["records"] += 1
        if not u:
            skipped["bad_url"] += 1
            continue
        h, k = host_of(u), key_of(u)
        social = any(h == x or h.endswith("." + x) for x in SOCIAL_HOSTS)
        if h in dio_hosts and not social:
            skipped["diocese_site"] += 1
            continue
        if SKIP_HOST.search(h):
            skipped["non_parish_host"] += 1
            continue
        if SCHOOL_HOST.search(h) and not CHURCH_WORD.search(h.replace("school", "")):
            skipped["school_host"] += 1
            continue
        if k in dm_keys:
            skipped["already_in_discovermass"] += 1
            continue
        g = groups.setdefault(k, {"url": u, "social": social, "churches": [], "srcs": set()})
        if len(u) < len(g["url"]) and not g["social"]:
            g["url"] = u
        g["srcs"].add(r["src"])
        g["churches"].append(r)
    by_state: dict[str, dict] = defaultdict(lambda: {"websites": [], "social_only": []})
    for g in groups.values():
        st = next((c["state"] for c in g["churches"] if c["state"]), None)
        if not st:
            for c in g["churches"]:
                m = re.sub(r"[^a-z]", "", re.sub(r"^(?:the\s+)?(?:arch)?(?:diocese|eparchy)\s+of\s+", "", (c["diocese"] or "").lower()))
                if m in see_state:
                    st = see_state[m]
                    break
        st = st or "_nostate"
        ents = [{"slug": c["slug"], "name": c["name"], "city": c["city"], "diocese": c["diocese"], "lat": c["lat"], "lng": c["lng"],
                 "source": {"wikidata": "Wikidata (CC0)", "osm": "OpenStreetMap contributors, ODbL"}[c["src"]]} for c in g["churches"]]
        if g["social"]:
            by_state[st]["social_only"] += [dict(e, url=g["url"]) for e in ents]
        else:
            by_state[st]["websites"].append({"url": g["url"], "sources": sorted(g["srcs"]), "churches": ents})
    built = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for old in OUT.glob("*.websites.json"):      # only files this script wrote (other lanes may share the folder)
        try:
            if json.loads(old.read_text()).get("source_label") == "url-sources":
                old.unlink()
        except (ValueError, OSError):
            pass
    (OUT / "_nostate.json").unlink(missing_ok=True)
    counts = {}
    for st, d in sorted(by_state.items()):
        d["websites"].sort(key=lambda e: e["churches"][0]["slug"])
        counts[st] = {"distinct_websites": len(d["websites"]), "social_only": len(d["social_only"]),
                      "churches": sum(len(w["churches"]) for w in d["websites"])}
        (OUT / (f"{st}.websites.json" if st != "_nostate" else "_nostate.json")).write_text(json.dumps({
            "source": "Wikidata (CC0) + OpenStreetMap contributors (ODbL): website tags", "source_label": "url-sources",
            "state": st.upper(), "built_at": built, "consumer": "lane G4 national parish-site crawler (needs run_state glob change, see README)",
            "note": "URLs unverified; one entry per distinct host. Not DiscoverMass: no times were read from these sources.",
            "counts": counts[st], "websites": d["websites"], "social_only": d["social_only"]},
            ensure_ascii=False, indent=1) + "\n")
    (OUT / "merge_summary.json").write_text(json.dumps({"built_at": built, "skipped": dict(skipped), "by_state": counts,
                                                         "excluded_discovermass_hosts": len(dm_keys)}, indent=1) + "\n")
    tot = sum(c["distinct_websites"] for c in counts.values())
    print("distinct websites after merge:", tot, "skipped:", dict(skipped))
    print(sorted(((c["distinct_websites"], s) for s, c in counts.items()), reverse=True)[:10])
    return 0


if __name__ == "__main__":
    sys.exit(main())
