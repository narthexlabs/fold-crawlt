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
import argparse, json, os, re, sys, time, glob, collections, subprocess, threading
from concurrent.futures import ThreadPoolExecutor
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
        "cityofwestonwv.gov", "ohiocountylibrary.org", "sundevilcentral.eoss.asu.edu", "madisonsd.com", "catholicchurchtimes.com",
        # 2026-11-01 second-run spot check (40 hits): aggregators, generated church pages, news/history/town hosts
        "alluschurches.com", "edan.io", "railway.app", "detroit1701.org", "blogspot.com", "flcatholictribune.com",
        "okhistory.org", "wkow.com", "salisburypa.com", "sistersofihm.org", "stemma.org",
        # 2026-11-01 third-run spot check (40 hits, ~53% right): towns, travel, news, other denominations, unrelated
        "phillipsburgnj.org", "sharonct.org", "catholicindiana.org", "scipiochurch.com", "ctinsider.com", "dc-yfs.com",
        "ebay.com", "catholicssharingjesus.org", "shkcfctampa.com", "trinityny.church", "discoverwhitewater.org",
        "allsaintsvillage.com", "peterandpaulcathedral.com", "wgpfoundation.org", "catholictravelusa.com",
        "popecountyareachurches.com", "smythcountychurches.com", "cmac.ws", "episcopalassetmap.org", "orthodox-world.org")
# host patterns that are never a parish's own site: government, universities, tourism boards, newspapers, town pages,
# historical societies / museums / libraries, other denominations (UCC, Orthodox, Episcopal, Baptist, Lutheran, Methodist)
DENY_RE = re.compile(r"\.gov(\.|$)|\.govoffice|\.edu$|^visit(?!ation)|^explore|^destination|travel|tourism|^(city|town|village)of(?!st)|"
                     r"(news|times|tribune|herald|journal|gazette|observer)(online)?\.(com|org|net)$|news12|historical|history|museum|"
                     r"library|ucc\.(org|com)$|-ucc\.|orthodox|episcopal|(?<!john)(?<!the)(?<!the-)baptist|lutheran|methodist|presbyterian")
CITY_PREFIX = {"salt", "san", "santa", "new", "west", "east", "north", "south", "fort", "ft", "mount", "mt", "las", "los", "el", "lake", "oak", "grand", "great", "port", "little", "big", "sioux", "baton", "boise", "st", "saint", "palm", "long", "oklahoma", "kansas", "sun", "falls", "des", "eau", "la", "del", "rio", "cedar", "green", "white", "blue", "red", "rock", "silver"}
CITY_SUFFIX = {"city", "falls", "springs", "beach", "valley", "park", "heights", "hill", "hills", "lake", "grove", "ridge", "harbor", "junction", "mtn", "creek", "rapids", "river", "island", "rock", "haven"}
GOOD_HINT = re.compile(r"parish|catholic|church|st[-.]?|saint|holy|our[-.]?lady|sacred|blessed|immaculate|christ|cathedral|basilica|mission|chapel", re.I)
STATE_RE = re.compile(r"^[A-Z]{2}$")
DENY_DYNAMIC: set[str] = set()


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(the|of|parish|church|catholic|roman|saint|st\.?|sts\.?|holy|our|lady|community|congregation)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


TOK_DROP = {"and", "ss", "the", "of", "a", "de", "del", "la", "el", "los", "las", "san", "santa", "santo", "mission", "chapel", "shrine"}
TOK_MAP = {"mt": "mount", "ft": "fort", "hts": "heights", "spgs": "springs", "spg": "springs", "twp": "township"}


def toks(s: str) -> frozenset:
    """Normalised name tokens for fuzzy matching: drop filler words, unify abbreviations, strip plural/possessive s."""
    out = set()
    for t in norm(s).split():
        t = TOK_MAP.get(t, t)
        if t in TOK_DROP:
            continue
        if len(t) > 3 and t.endswith("s"):
            t = t[:-1]
        out.add(t)
    return frozenset(out)


class KnownIndex:
    """(name tokens, city tokens) of every parish we already have a website for, per state. A candidate matches when a
    known parish's name tokens and city tokens are both contained in the candidate's tokens (or the reverse for the
    name, when the candidate's name is the shorter one), so 'St Marys Star Of The Sea Oceanside' matches
    'Saint Mary Star of the Sea / Oceanside' even though the exact keys differ."""

    def __init__(self, named):
        self.by = collections.defaultdict(lambda: collections.defaultdict(list))   # st -> city toks -> [name toks]
        for (n, c, st) in named:
            nt, ct = toks(n), toks(c)
            if nt and ct:
                self.by[st][ct].append(nt)

    def match(self, name: str, city: str, st: str, full: str | None = None) -> bool:
        cities = self.by.get(st)
        if not cities:
            return False
        if full:   # slug whose name/city split is a guess: test against the whole token set
            T = toks(full)
            for ct, names in cities.items():
                if ct <= T:
                    rest = T - ct
                    for nt in names:
                        if nt <= T and (nt <= rest or rest <= nt):
                            return True
            return False
        nt0, ct0 = toks(name), toks(city)
        if not nt0 or not ct0:
            return False
        for ct, names in cities.items():
            if ct <= ct0 or ct0 <= ct:
                for nt in names:
                    if nt <= nt0 or nt0 <= nt:
                        return True
        return False


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
                src = "browser" if "browser" in str(r.get("extracted_by") or "").lower() else "directory"
                add(r.get("name"), r.get("city"), r.get("state") or sees.get(slug) or "", src, f"{slug}:{r.get('name')}")
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


class BalanceGone(Exception):
    """HTTP 402 from the pay-as-you-go key: the prepaid balance is spent."""


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
            if e.code == 402:
                raise BalanceGone()
            if e.code in (401, 403):
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
        if not h or h in DENY_DYNAMIC or DENY_RE.search(h) or any(h == d or h.endswith("." + d) or d in h for d in DENY):
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


def tiers(cands, named):
    """Tier 1: directory / browser-read / GCatholic records with no website and no fuzzy match to a known site (clean
    name + city + state). Tier 2: catholicparish.net slugs with no fuzzy match. Tier 3: the rest (likely parishes we
    already have under a slightly different name). Also returns per-state coverage = share of catholicparish.net slugs
    (the nationwide parish list) that already match a known website."""
    idx = KnownIndex(named)
    named = set(named)
    slug_tot, slug_known = collections.Counter(), collections.Counter()
    t = {1: [], 2: [], 3: []}
    for c in cands:
        if c["src"] == "catholicparish.net":
            exact = (norm(c["name"]), norm(c["city"]), c["state"]) in named
            m = exact or idx.match(c["name"], c["city"], c["state"], full=c.get("keyname") or (c["name"] + " " + c["city"]))
            slug_tot[c["state"]] += 1; slug_known[c["state"]] += m
            t[3 if m else 2].append(c)
        else:
            m = idx.match(c["name"], c["city"], c["state"]) or not c["city"]
            t[3 if m else 1].append(c)
    cov = {st: slug_known[st] / slug_tot[st] for st in slug_tot}
    return t, cov, slug_tot


def build(done):
    by_state = collections.defaultdict(dict)
    freq = collections.Counter(host_of(d["url"]) for d in done.values() if d.get("url"))
    aggregators = {h for h, n in freq.items() if n > 4}   # one host chosen for many parishes = a directory, not a parish
    for d in done.values():
        if not d.get("url"):
            continue
        h = host_of(d["url"])
        if h in aggregators or DENY_RE.search(h) or any(h == x or h.endswith("." + x) or x in h for x in DENY):
            continue
        by_state[d["state"]].setdefault(h, {"url": d["url"], "churches": []})["churches"].append(
            {"slug": f"brave:{d['key']}", "name": d["name"], "city": d["city"], "state": d["state"], "diocese": None, "source": "brave-search", "query": d["query"]})
    for st, hosts_ in by_state.items():
        (OUT / f"{st}.websites.json").write_text(json.dumps({"state": st, "source": "Brave Search API lookup by parish name (facts: the parish's own site URL)",
                                                               "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                               "websites": list(hosts_.values())}, indent=1, ensure_ascii=False) + "\n")
    hits = sum(len(h) for h in by_state.values()); tot = len(done)
    by_tier = collections.Counter((d.get("tier"), bool(d.get("url"))) for d in done.values())
    tier_line = "; ".join(f"tier {t}: {by_tier[(t, True)] + by_tier[(t, False)]} queries, {by_tier[(t, True)]} hits" for t in (1, 2, 3)
                          if by_tier[(t, True)] + by_tier[(t, False)])
    (OUT / "README.md").write_text(f"# Brave Search lookup\n\nQueries so far: {tot}; distinct parish sites found: {hits} "
                                   f"({100*hits/max(tot,1):.0f}% hit rate). One query per parish name + city + state; first result whose host is not a "
                                   f"directory/social/diocese host and whose title or URL looks like a parish. Evidence per query in data/raw (done.jsonl).\n\n"
                                   f"Second run (2026-11-01, ranked): {tier_line or 'n/a'}. Tier 1 = diocesan directory / GCatholic / browser-read records "
                                   f"with no website; tier 2 = catholicparish.net slugs that do not fuzzy-match (name tokens + city) a parish we already "
                                   f"have a site for; tier 3 = the rest. States with >=70% of catholicparish.net slugs already matched are skipped outside "
                                   f"tier 1. Queries run in batches of 500; a tier stops when its last-500 new-domain rate falls below 30%.\n\n"
                                   f"Third run (2026-11-01): 4,849 queries (tier 2 to exhaustion, tier 3 gated at 22%; then states 70-85% covered: "
                                   f"tier 2 gated at 17%, tier 3 at 19%). A 40-hit spot check of that run found ~53% right, so a host-pattern deny "
                                   f"(DENY_RE: .gov, .edu, tourism, newspapers, town pages, other denominations) now filters picks and the build.\n\n"
                                   f"Resume: `BRAVE_KEY=... python3 -I ingest/us/url_sources/brave_lookup.py --budget N [--checkpoint-cmd CMD]`\n")
    print(f"built: {tot} queries, {hits} sites in {len(by_state)} states", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=7500)
    ap.add_argument("--qps", type=float, default=5.0)
    ap.add_argument("--states", default="")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--gate", type=float, default=0.30, help="stop a tier when its rolling new-domain rate drops below this")
    ap.add_argument("--coverage-skip", type=float, default=0.70, help="skip tier 2/3 candidates in states with this share already known")
    ap.add_argument("--checkpoint-every", type=int, default=1000)
    ap.add_argument("--checkpoint-cmd", default="", help="shell command run after each checkpoint build (commit/push/dispatch)")
    ap.add_argument("--dry-run", action="store_true", help="print tier counts and stop")
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
    if a.build:
        build(done); return 0
    if not key and not a.dry_run:
        print("BRAVE_KEY not set"); return 2
    hosts, named = known_hosts_and_names()
    start_hosts = set(hosts)
    states = {x.strip().upper() for x in a.states.split(",") if x.strip()} or None

    def dkey(c):
        return f"{c['state']}|{norm(c.get('keyname') or c['name'])}|{'' if c.get('keyname') else norm(c['city'])}"

    allc = candidates(states)
    _, cov, slug_tot = tiers([c for c in allc if c["src"] == "catholicparish.net"], named)   # coverage over every slug
    cands = [c for c in allc if (norm(c["name"]), norm(c["city"]), c["state"]) not in named and dkey(c) not in done]
    tiered, _, _ = tiers(cands, named)
    skipped = collections.Counter()
    for t in (2, 3):
        keep = []
        for c in tiered[t]:
            if cov.get(c["state"], 0) >= a.coverage_skip:
                skipped[t] += 1
            else:
                keep.append(c)
        tiered[t] = keep
    # within a tier: least-covered states first, then by name
    for t in tiered:
        tiered[t].sort(key=lambda c: (cov.get(c["state"], 0), c["state"], c["name"]))
    print(f"candidates (not yet queried): tier1 {len(tiered[1])}, tier2 {len(tiered[2])} (+{skipped[2]} skipped, state >= "
          f"{a.coverage_skip:.0%} covered), tier3 {len(tiered[3])} (+{skipped[3]} skipped); done before {len(done)}; budget {a.budget}", flush=True)
    hi = sorted((st for st in cov if cov[st] >= a.coverage_skip))
    print(f"states >= {a.coverage_skip:.0%} covered: {', '.join(f'{st} {cov[st]:.0%}' for st in hi) or 'none'}", flush=True)
    if a.dry_run:
        return 0
    n = 0; t0 = time.time(); last = 0.0
    stats = {t: {"q": 0, "new": 0, "window": collections.deque(maxlen=a.batch), "curve": []} for t in tiered}
    stop_all = None
    # Brave answers in ~1.1 s, so a serial loop runs at ~0.9 q/s: fetch in small parallel chunks, paced to --qps overall
    lock = threading.Lock(); slot = [0.0]

    def paced(q):
        with lock:
            now = time.time(); at = max(now, slot[0]); slot[0] = at + 1.0 / a.qps
        if at > now:
            time.sleep(at - now)
        return brave(q, key)

    pool = ThreadPoolExecutor(max_workers=max(1, int(a.qps * 2)))
    try:
        for t in (1, 2, 3):
            st_ = stats[t]
            todo = [c for c in tiered[t] if dkey(c) not in done]
            gated = False
            for i in range(0, len(todo), 25):
                if n >= a.budget:
                    stop_all = "budget"; break
                chunk = todo[i:i + min(25, a.budget - n)]
                qs = [f"{c['name']} {c['city']} {c['state']} catholic church".replace("  ", " ") for c in chunk]
                results = list(pool.map(paced, qs))
                for c, q, res in zip(chunk, qs, results):
                    k = dkey(c); n += 1
                    url, rej = pick(res, c, hosts)
                    h = host_of(url) if url else ""
                    is_new = bool(h) and h not in start_hosts
                    rec = {"key": k, "name": c["name"], "city": c["city"], "state": c["state"], "src": c["src"], "id": c["id"], "tier": t,
                           "query": q, "url": url, "new_domain": is_new, "rejected": rej[:5], "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    with open(DONE, "a") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    done[k] = rec
                    if url:
                        hosts.add(h)
                    st_["q"] += 1; st_["new"] += is_new; st_["window"].append(is_new)
                    if st_["q"] % a.batch == 0:
                        rate = sum(st_["window"]) / len(st_["window"])
                        st_["curve"].append(round(rate, 3))
                        print(f"  tier {t}: batch {st_['q'] // a.batch} rate {rate:.0%} (tier total {st_['new']}/{st_['q']}); "
                              f"run {n} queries, {n/(time.time()-t0):.1f} q/s", flush=True)
                        if rate < a.gate:
                            print(f"  tier {t} below gate ({rate:.0%} < {a.gate:.0%}): stopping tier", flush=True)
                            gated = True
                    if n % a.checkpoint_every == 0:
                        build(done)
                        if a.checkpoint_cmd:
                            subprocess.run(a.checkpoint_cmd, shell=True)
                if gated:
                    break
            if not gated and st_["q"] % a.batch and st_["window"]:
                st_["curve"].append(round(sum(st_["window"]) / len(st_["window"]), 3))
            if stop_all:
                break
    except BalanceGone:
        stop_all = "402 balance gone"
        print("Brave returned 402: prepaid balance spent; stopping cleanly", flush=True)
    build(done)
    summ = {t: {"queries": s_["q"], "new_domains": s_["new"], "curve": s_["curve"]} for t, s_ in stats.items()}
    print(f"RUN SUMMARY: {n} queries; stop={stop_all or 'all tiers exhausted or gated'}; " + json.dumps(summ), flush=True)
    (RAW / "last_run.json").write_text(json.dumps({"queries": n, "stop": stop_all, "tiers": summ}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
