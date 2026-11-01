#!/usr/bin/env python3
"""Find parish websites by name with the Brave Search API (key in env BRAVE_KEY; never in the repo).

Candidates = parishes we know by name + city + state but have no website for: catholicparish.net's sitemap slugs
(<name>-<city>-<st>, the whole country), diocesan directory records without a website, GCatholic churches without one.
Each candidate is one query: "<name> <city> <state> catholic church". The first result whose host is not a known
directory / social / diocese / aggregator host, and whose title or URL looks like a parish, is kept.

Output: data/real/us/url_sources/brave/<ST>.websites.json in the shape run_state.py reads, plus brave.json with the
query, the chosen URL and the rejected ones (evidence). Resumable: data/raw/us/url_sources/brave/done.jsonl.

  BRAVE_KEY=... python3 -I ingest/us/url_sources/brave_lookup.py --budget 8000 --qps 5 [--states PA,MI]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, glob, collections
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[3]
US = ROOT / "data" / "real" / "us"
OUT = US / "url_sources" / "brave"
RAW = ROOT / "data" / "raw" / "us" / "url_sources" / "brave"
DONE = RAW / "done.jsonl"
API = "https://api.search.brave.com/res/v1/web/search"

DENY = ("catholicparish.net", "discovermass.com", "masstimes.org", "gcatholic.org", "wikipedia.org", "wikidata.org",
        "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "yelp.com", "mapquest.com",
        "google.com", "bing.com", "yellowpages.com", "manta.com", "parishesonline.com", "ecatholic.com",
        "catholicdirectory.com", "discovercatholicbusiness.com", "thecatholicdirectory.com", "findamass",
        "churchfinder", "churchangel", "usccb.org", "vatican.va", "guidestar.org", "propublica.org", "charitynavigator.org",
        "zillow.com", "tripadvisor.com", "foursquare.com", "nextdoor.com", "linkedin.com", "archive.org", "bizapedia.com",
        "chamberofcommerce", "patch.com", "legacy.com", "findagrave", "dignitymemorial", "tributearchive", "newspapers.com",
        "catholicmasstime", "catholiccharities", "faithstreet.com", "localcatholicchurches.com", "wanderlog.com", "churchesnearme", "catholicchurchesnearme", "thecatholicchurches", "massschedule", "church-finder", "churchfinder.com", "usachurches.org", "catholic-church", "religiousorganizations", "bestcatholic", "catholicfinder", "amazon.com", "apple.com", "mycatholicparish", "weconnect.com",
        "parishsoft", "flocknote.com", "onlinegiving", "osvhub.com", "pushpay", "givecentral", "signupgenius",
        # found by the 2026-11-01 precision audit (aggregators / unrelated hosts chosen for a parish)
        "churchadvise.com", "church.org", "opengovny.com", "atly.com", "historypointer.com", "whiting-turner.com",
        "cityofwestonwv.gov", "ohiocountylibrary.org", "sundevilcentral.eoss.asu.edu", "madisonsd.com", "catholicchurchtimes.com")
CITY_PREFIX = {"salt", "san", "santa", "new", "west", "east", "north", "south", "fort", "ft", "mount", "mt", "las", "los", "el", "lake", "oak", "grand", "great", "port", "little", "big", "sioux", "baton", "boise", "st", "saint", "palm", "long", "oklahoma", "kansas", "sun", "falls", "des", "eau", "la", "del", "rio", "cedar", "green", "white", "blue", "red", "rock", "silver"}
CITY_SUFFIX = {"city", "falls", "springs", "beach", "valley", "park", "heights", "hill", "hills", "lake", "grove", "ridge", "harbor", "junction", "mtn", "creek", "rapids", "river", "island", "rock", "haven"}
GOOD_HINT = re.compile(r"parish|catholic|church|st[-.]?|saint|holy|our[-.]?lady|sacred|blessed|immaculate|christ|cathedral|basilica|mission|chapel", re.I)
STATE_RE = re.compile(r"^[A-Z]{2}$")
DENY_DYNAMIC: set[str] = set()


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(the|of|parish|church|catholic|roman|saint|st\.?|sts\.?|holy|our|lady|community|congregation)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def host_of(u: str) -> str:
    try:
        return (urlparse(u).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def known_hosts_and_names():
    """Hosts we already crawl / list, and (name,city,state) keys we already have a website for."""
    hosts, named = set(), set()
    for s in json.load(open(US / "dioceses.json"))["dioceses"]:
        if s.get("website"):
            DENY_DYNAMIC.add(host_of(s["website"]))
    for f in glob.glob(str(US / "sites" / "*" / "*.json")):
        if f.endswith("summary.json"):
            continue
        try:
            j = json.load(open(f))
        except Exception:
            continue
        hosts.add(Path(f).stem.lower())
        for n in j.get("names") or []:
            for c in j.get("cities") or [""]:
                for st in j.get("states") or [""]:
                    named.add((norm(n), norm(c), (st or "").upper()))
    for f in glob.glob(str(US / "url_sources" / "**" / "*.websites.json"), recursive=True) + glob.glob(str(US / "discovermass" / "*.websites.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for w in (d.get("websites", d) if isinstance(d, dict) else d):
            u = w.get("url") or w.get("website") or ""
            if u:
                hosts.add(host_of(u))
            for c in w.get("churches", [w]):
                named.add((norm(c.get("name", "")), norm(c.get("city", "")), (c.get("state") or Path(f).stem.split(".")[0]).upper()))
    for f in glob.glob(str(US / "dioceses" / "*" / "parishes.json")):
        d = json.load(open(f))
        for r in d.get("records", []):
            if r.get("website"):
                hosts.add(host_of(r["website"]))
                named.add((norm(r.get("name", "")), norm(r.get("city", "")), (r.get("state") or "").upper()))
    return hosts, named


def candidates(states: set[str] | None):
    sees = {s["slug"]: s.get("see_state") for s in json.load(open(US / "dioceses.json"))["dioceses"]}
    cands = {}

    def add(name, city, st, src, key, keyname=None):
        if not name or not st or not STATE_RE.match(st or ""):
            return
        # keyname: the pre-split "name city" string, so dedupe/done keys stay identical to rows done before the split existed
        k = (norm(keyname or name), "" if keyname else norm(city), st.upper())
        if k not in cands:
            cands[k] = {"name": name.strip(), "city": (city or "").strip(), "state": st.upper(), "src": src, "id": key, "keyname": keyname}

    # catholicparish.net sitemap slugs (cached sitemap file, fetched once with the honest UA)
    sm = RAW / "catholicparish-sitemap.xml"
    if not sm.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        req = Request("https://catholicparish.net/sitemap-parishes-0.xml", headers={"User-Agent": "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"})
        sm.write_bytes(urlopen(req, timeout=60).read())
    for slug in re.findall(r"catholicparish\.net/parish/([^/<]+)/", sm.read_text("utf-8", "replace")):
        parts = slug.split("-")
        if len(parts) < 3:
            continue
        st = parts[-1].upper()
        # city = last 1-2 tokens before the state is a guess; keep the whole remainder as "name city" for the query
        toks = [p.capitalize() for p in parts[:-1]]
        full = " ".join(toks)
        # heuristic split: city = last token, or last two when the second-to-last is a typical city prefix / the last a typical city suffix
        n_city = 1
        if len(toks) >= 4 and (toks[-2].lower() in CITY_PREFIX or toks[-1].lower() in CITY_SUFFIX):
            n_city = 2
        if len(toks) - n_city < 2:   # keep at least a 2-token parish name ("St Mary")
            n_city = 0
        name, city = (" ".join(toks[:-n_city]), " ".join(toks[-n_city:])) if n_city else (full, "")
        add(name, city, st, "catholicparish.net", slug, keyname=full if n_city else None)
    for f in glob.glob(str(US / "dioceses" / "*" / "parishes.json")):
        slug = Path(f).parent.name
        for r in json.load(open(f)).get("records", []):
            if not r.get("website"):
                add(r.get("name"), r.get("city"), r.get("state") or sees.get(slug) or "", "directory", f"{slug}:{r.get('name')}")
    gj = US / "url_sources" / "gcatholic.json"
    if gj.exists():
        g = json.load(open(gj))
        for r in (g.get("records", g) if isinstance(g, dict) else g):
            if not r.get("website") and (r.get("status") in (None, "parish", "other")):
                add(r.get("name"), r.get("city"), r.get("state"), "gcatholic", r.get("gc_id"))
    out = list(cands.values())
    if states:
        out = [c for c in out if c["state"] in states]
    return out


def brave(q: str, key: str) -> dict:
    url = API + "?" + "q=" + quote(q) + "&count=5&country=us&safesearch=moderate"
    req = Request(url, headers={"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": "FindTheChurch/0.1"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429:
                time.sleep(2.0 * (attempt + 1)); continue
            if e.code in (401, 403, 402):
                raise SystemExit(f"Brave API refused ({e.code}): check the key / plan")
            time.sleep(1.0)
        except (URLError, ConnectionError, OSError, TimeoutError):
            time.sleep(3.0 * (attempt + 1))
    return {}


def pick(res: dict, cand: dict, known_hosts: set[str]) -> tuple[str | None, list]:
    rejected = []
    toks = set(norm(cand["name"]).split())
    for r in (res.get("web") or {}).get("results", [])[:5]:
        u = r.get("url") or ""; h = host_of(u)
        if not h or h in DENY_DYNAMIC or any(h == d or h.endswith("." + d) or d in h for d in DENY):
            rejected.append((u, "deny")); continue
        if h in known_hosts:
            rejected.append((u, "already-known")); continue
        title = (r.get("title") or "") + " " + (r.get("description") or "")
        tt = set(norm(title).split()) | set(norm(h.replace(".", " ")).split())
        if GOOD_HINT.search(title) or GOOD_HINT.search(h) or (toks & tt):
            # keep the site root, not a deep page
            return f"{urlparse(u).scheme}://{urlparse(u).hostname}/", rejected
        rejected.append((u, "no-parish-signal"))
    return None, rejected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=8000)
    ap.add_argument("--qps", type=float, default=5.0)
    ap.add_argument("--states", default="")
    ap.add_argument("--build", action="store_true", help="only rebuild outputs from done.jsonl")
    a = ap.parse_args()
    key = os.environ.get("BRAVE_KEY", "")
    RAW.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    done = {}
    if DONE.exists():
        for line in DONE.read_text().splitlines():
            try:
                j = json.loads(line); done[j["key"]] = j
            except Exception:
                pass
    if not a.build:
        if not key:
            print("BRAVE_KEY not set"); return 2
        hosts, named = known_hosts_and_names()
        states = {x.strip().upper() for x in a.states.split(",") if x.strip()} or None
        cands = [c for c in candidates(states) if (norm(c["name"]), norm(c["city"]), c["state"]) not in named]
        # lowest-completion states first: use counts of known sites per state as a proxy (fewest known first)
        known_by_state = collections.Counter(st for (_, _, st) in named)
        cands.sort(key=lambda c: (known_by_state.get(c["state"], 0), c["state"], c["name"]))
        print(f"candidates {len(cands)} (already done {len(done)}); budget {a.budget}", flush=True)
        n = 0; t0 = time.time(); last = 0.0
        for c in cands:
            k = f"{c['state']}|{norm(c.get('keyname') or c['name'])}|{'' if c.get('keyname') else norm(c['city'])}"
            if k in done:
                continue
            if n >= a.budget:
                print("budget reached"); break
            q = f"{c['name']} {c['city']} {c['state']} catholic church".replace("  ", " ")
            wait = (1.0 / a.qps) - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            res = brave(q, key); last = time.time(); n += 1
            url, rej = pick(res, c, hosts)
            rec = {"key": k, "name": c["name"], "city": c["city"], "state": c["state"], "src": c["src"], "id": c["id"],
                   "query": q, "url": url, "rejected": rej[:5], "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            with open(DONE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[k] = rec
            if url:
                hosts.add(host_of(url))
            if n % 200 == 0:
                hit = sum(1 for d in done.values() if d.get("url"))
                print(f"  {n} queries, {hit} hits total, {n/(time.time()-t0):.1f} q/s", flush=True)
    # build outputs
    by_state = collections.defaultdict(dict)
    freq = collections.Counter(host_of(d["url"]) for d in done.values() if d.get("url"))
    aggregators = {h for h, n in freq.items() if n > 4}   # one host chosen for many parishes = a directory, not a parish
    for d in done.values():
        if not d.get("url"):
            continue
        h = host_of(d["url"])
        if h in aggregators:
            continue
        by_state[d["state"]].setdefault(h, {"url": d["url"], "churches": []})["churches"].append(
            {"slug": f"brave:{d['key']}", "name": d["name"], "city": d["city"], "state": d["state"], "diocese": None, "source": "brave-search", "query": d["query"]})
    for st, hosts_ in by_state.items():
        (OUT / f"{st}.websites.json").write_text(json.dumps({"state": st, "source": "Brave Search API lookup by parish name (facts: the parish's own site URL)",
                                                               "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                               "websites": list(hosts_.values())}, indent=1, ensure_ascii=False) + "\n")
    hits = sum(len(h) for h in by_state.values()); tot = len(done)
    (OUT / "README.md").write_text(f"# Brave Search lookup\n\nQueries so far: {tot}; distinct parish sites found: {hits} "
                                   f"({100*hits/max(tot,1):.0f}% hit rate). One query per parish name + city + state; first result whose host is not a "
                                   f"directory/social/diocese host and whose title or URL looks like a parish. Evidence per query in data/raw (done.jsonl).\n\n"
                                   f"Resume: `BRAVE_KEY=... python3 -I ingest/us/url_sources/brave_lookup.py --budget N`\n")
    print(f"built: {tot} queries, {hits} sites in {len(by_state)} states")
    return 0


if __name__ == "__main__":
    sys.exit(main())
