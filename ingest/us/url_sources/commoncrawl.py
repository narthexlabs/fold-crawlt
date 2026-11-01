#!/usr/bin/env python3
"""Common Crawl index (CDX API) -> candidate parish-website domains for the national site crawler.

We crawl nothing here: the index is a public list of URLs Common Crawl has already fetched. A parish site is recognisable
by pages named like /mass-times, /confession, /parish-staff; finding such URLs yields its domain. We do NOT know the
parish name or state from this source - the site crawler reads the site and its parser extracts name and address. So the
output is data/real/us/url_sources/commoncrawl/XX.websites.json ("XX" = state unknown; run_state.build_inputs files these
under the `unassigned-xx` bucket; crawl them with `run_state.py chunk --state XX`).

  python3 -u ingest/us/url_sources/commoncrawl.py --minutes 60        # resumable scan (progress.json), then --build
  python3 -I ingest/us/url_sources/commoncrawl.py --build             # only rebuild outputs from cache + earlier evidence
  python3 -u ingest/us/url_sources/commoncrawl.py --probe             # 5 tiny queries, prints what the API answers
  options: --crawls 3  --tlds church,catholic,org,us,net,com  --page-size 10

HOW THE INDEX CAN (AND CANNOT) BE QUERIED. The CDX API (pywb) accepts only a leading `*.domain` (domain match) or a
trailing `*` (prefix match). A pattern like `*/mass-times*` returns "No Captures found" (checked 2026-11-01): the index
is sorted by reversed host, so there is no path-only lookup. We therefore scan one TLD at a time (`url=*.org`, paged with
page/pageSize) and let the server apply regex filters (CDX `filter=~url:...`, ANDed): the path must look like a
parish page, and for the big TLDs the host must also contain a church word (st-joseph, parish, catholic ...).
Only matching rows come back, so a scan is many small requests (>= 1.1 s apart, one at a time, 503/429/reset backed off).
`.church` and `.catholic` need no host filter. Progress per (crawl, tld) is a page cursor in progress.json (committed), so
any machine resumes where the last stopped. Common Crawl terms: https://commoncrawl.org/terms-of-use
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
import fetch_site as fs  # noqa: E402  (honest user agent + proxy-aware GET; never spoofed)

US = ROOT / "data" / "real" / "us"
OUT_DIR = US / "url_sources" / "commoncrawl"
OUT_EVIDENCE = US / "url_sources" / "commoncrawl.json"
PROGRESS = OUT_DIR / "progress.json"
RAW = ROOT / "data" / "raw" / "us" / "url_sources" / "commoncrawl"
INDEX = "https://index.commoncrawl.org"
ATTR = "Common Crawl index (https://commoncrawl.org/terms-of-use): URLs only"
MIN_GAP = 1.1
DEFAULT_TLDS = ["church", "catholic", "org", "us", "net", "com"]
KEEP_TLDS = {"org", "com", "net", "church", "us", "catholic", "info"}

# ---- patterns. STRONG = pages Catholic parishes have and most other churches do not; WEAK = common to any church.
STRONG = ["mass-times", "mass-time", "masstimes", "masstime", "mass-schedule", "massschedule", "mass-schedules",
          "mass-and-confession", "mass-and-confessions", "masses", "weekend-masses", "daily-mass", "confession-times",
          "reconciliation", "sacrament-of-reconciliation", "eucharistic-adoration", "adoration", "rcia", "parish-staff",
          "parish-office", "parish-bulletin", "holy-days", "holydays", "holy-days-of-obligation"]
# WEAK paths (bulletin, sacraments, baptism, worship, liturgy, plain "confession") are NOT queried: the first Actions run
# (2026-11-01) showed they are overwhelmingly Protestant/Anglican pages on .church. Kept only to describe evidence.
WEAK = ["bulletin", "bulletins", "sacraments", "sacrament"]
PATH_RE = r"/(?:%s)(?:[/._?#-]|$)" % "|".join(sorted(set(STRONG), key=len, reverse=True))

# church words in a host (applied server-side on the big TLDs)
SAINTS = ("joseph|mary|marys|peter|paul|john|johns|anne|annes|ann|thomas|francis|michael|patrick|anthony|mark|luke|james|"
          "matthew|catherine|agnes|bernard|benedict|dominic|ignatius|jude|margaret|teresa|theresa|elizabeth|rose|gregory|"
          "cecilia|basil|monica|vincent|lawrence|charles|edward|george|philip|leo|jerome|martin|augustine|ambrose|andrew|"
          "bartholomew|boniface|brendan|bridget|casimir|clare|columba|cyril|denis|eugene|frances|gabriel|"
          "gerard|helen|hubert|isidore|joan|joachim|jean|justin|kateri|kevin|lucy|louis|maximilian|nicholas|norbert|"
          "pius|raphael|robert|stephen|sylvester|timothy|ursula|veronica|victor|vitus|wenceslaus|william|barnabas|blase|"
          "bonaventure|cornelius|damien|dismas|edmund|gertrude|hedwig|hyacinth|jane|julie|katharine|lucia|"
          "matilda|rita|scholastica|sebastian|simon|therese")
HOST_WORDS = ["church", "parish", "catholic", "cathedral", "basilica", "shrine", "saint", "holy", "sacred", "immaculate",
              "ourlady", "our-lady", "blessed", "assumption", "annunciation", "nativity", "transfiguration",
              "ascension", "epiphany", "resurrection", "christtheking", "corpuschristi", "mission",
              "newman", "rosary", "trinity", "angels", "guadalupe", "carmel", "loyola", "vianney"]
HOST_RE = r"^https?://[^/]*(?:%s|st[-_.]?(?:%s)|saint[-_]?(?:%s))" % ("|".join(HOST_WORDS), SAINTS, SAINTS)

# ---- exclusions
NOT_CATHOLIC = re.compile(r"baptist|methodist|lutheran|presbyterian|pentecost|adventist|jesuschrist|(?:^|[.-])lds(?:[.-]|$)|"
                          r"(?:^|[.-])jw(?:[.-]|$)|anglican|episcopal|orthodox|evangel|nazarene|reformed|christian|"
                          r"calvary|vineyard|assembl|gospel|biblechurch|churchofgod|churchofchrist|unitarian|quaker|"
                          r"mennonite|missionary|ministr|apostolic|worldwide|megachurch|lifechurch|crosspoint|"
                          r"elevation|hillsong|community-?church|bible", re.I)
SCHOOL_OTHER = re.compile(r"school|academy|prep(?:\b|[.-])|highschool|college|universit|seminary|hospital|health|medical|"
                          r"k12|cyo|elementary|catholiccharit|knightsof|kofc|council\d", re.I)
SKIP_DOMAIN = {
    "parishesonline.com", "4lpi.com", "discovermass.com", "ecatholic.com", "usccb.org", "masstimes.org", "catholicmasstime.org",
    "google.com", "goo.gl", "yelp.com", "wikipedia.org", "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "linktr.ee", "weebly.com", "wixsite.com", "wix.com", "squarespace.com", "godaddysites.com",
    "sites.google.com", "square.site", "carrd.co", "webador.com", "wordpress.com", "blogspot.com", "tumblr.com",
    "ewtn.com", "catholic.org", "catholic.com", "catholicculture.org", "catholicnewsagency.com", "ncregister.com",
    "catholicnewsservice.com", "catholicreview.org", "vatican.va", "gcatholic.org", "catholic-hierarchy.org",
    "churchofjesuschrist.org", "lds.org", "jw.org", "findmass.org", "masstimes.com", "catholicmasstimes.com",
    "catholicsaints.info", "newadvent.org", "catholicchurchsupplies.com", "churchpublishing.org", "churchlink.com",
    "church.org", "eventbrite.com", "flocknote.com", "osvparish.com", "parishsoft.com", "pushpay.com", "givecentral.org",
    "onrealm.org", "subsplash.com", "faithdirect.net", "myparishapp.com", "wordpress.org", "churchcalendar.com",
    "churchfinder.com", "church-directory.com", "churchangel.com", "mapquest.com", "tripadvisor.com", "yellowpages.com",
    "chamberofcommerce.com",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------------------------------ domains
def reg_domain(host: str) -> str | None:
    """Registrable domain: last two labels; for *.us the locality form (name.ca.us) keeps one more."""
    host = (host or "").lower().strip(".")
    host = host[4:] if host.startswith("www.") else host
    labels = host.split(".")
    if len(labels) < 2 or labels[-1] not in KEEP_TLDS:
        return None
    if labels[-1] == "us" and len(labels) >= 3 and len(labels[-2]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def pattern_of(path: str) -> tuple[str | None, str | None]:
    """(pattern, 'strong'|'weak') for the first path segment that names a parish page; strong wins."""
    found = None
    for part in (path or "").lower().split("?")[0].split("/"):
        part = part.rsplit(".", 1)[0]
        if part in STRONG:
            return part, "strong"
        if part in WEAK and not found:
            found = (part, "weak")
    return found or (None, None)


# ------------------------------------------------------------------------------------------------ API
class Api:
    def __init__(self, deadline: float, log):
        self.deadline, self.log, self.last = deadline, log, 0.0
        self.consec_fail = 0
        self.requests = 0

    def get(self, url: str, tries: int = 4) -> tuple[int | None, str]:
        """(status, body). 503/429/5xx/reset are retried with backoff (Retry-After honoured); never faster than MIN_GAP."""
        delay = 4.0
        for i in range(tries):
            wait = self.last + MIN_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            r = fs.http_get(url, timeout=120, max_bytes=40_000_000)
            self.requests += 1
            st = r.get("status")
            if st in (200, 404, 400):
                self.consec_fail = 0
                return st, (r.get("body") or b"").decode("utf-8", "replace")
            ra = (r.get("headers") or {}).get("retry-after")
            pause = float(ra) if ra and ra.replace(".", "").isdigit() else delay
            self.log(f"  retry {i + 1}/{tries} status={st} err={r.get('err')} wait={pause:.0f}s")
            if time.monotonic() + pause > self.deadline:
                break
            time.sleep(min(pause, 180))
            delay = min(delay * 2, 120)
        self.consec_fail += 1
        return None, ""


def collinfo(api: Api, n: int) -> list[str]:
    st, body = api.get(INDEX + "/collinfo.json")
    if st != 200:
        return []
    try:
        return [c["id"] for c in json.loads(body)[:n]]       # newest first
    except (ValueError, KeyError):
        return []


def cdx(crawl: str, params: dict) -> str:
    return f"{INDEX}/{crawl}-index?" + urlencode(params, doseq=True, quote_via=quote)


def num_pages(api: Api, crawl: str, tld: str, page_size: int) -> int | None:
    st, body = api.get(cdx(crawl, {"url": f"*.{tld}", "output": "json", "showNumPages": "true", "pageSize": page_size}))
    if st != 200:
        return None
    try:
        return int(json.loads(body.strip().splitlines()[0])["pages"])
    except (ValueError, KeyError, IndexError):
        return None


def page_params(tld: str, page: int, page_size: int) -> dict:
    f = ["status:200", "~url:(?i).*" + PATH_RE]
    if tld not in ("church", "catholic"):
        f.append("~url:(?i)" + HOST_RE)
    return {"url": f"*.{tld}", "output": "json", "fl": "url,status", "filter": f, "page": page, "pageSize": page_size}


def fetch_page(api: Api, crawl: str, tld: str, page: int, page_size: int) -> list[str] | None:
    """URLs matching on one page; [] when empty; None when the page could not be fetched (retry on a later run)."""
    cache = RAW / crawl / tld / f"p{page:06d}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    st, body = api.get(cdx(crawl, page_params(tld, page, page_size)))
    if st in (404, 400):          # "No Captures found" / page past the end
        urls: list[str] = []
    elif st == 200:
        urls = []
        for line in body.splitlines():
            try:
                urls.append(json.loads(line)["url"])
            except (ValueError, KeyError):
                continue
    else:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(urls))
    return urls


# ------------------------------------------------------------------------------------------------ progress
def load_progress() -> dict:
    try:
        return json.loads(PROGRESS.read_text())
    except (OSError, ValueError):
        return {"units": {}}


def save_progress(pr: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pr["updated_at"] = now_iso()
    PROGRESS.write_text(json.dumps(pr, indent=1) + "\n", "utf-8")


def scan(minutes: float, n_crawls: int, tlds: list[str], page_size: int, log) -> None:
    deadline = time.monotonic() + minutes * 60
    api = Api(deadline, log)
    pr = load_progress()
    crawls = collinfo(api, n_crawls)
    if not crawls:
        log("collinfo.json unreachable; nothing done (re-run later)")
        pr["last_run"] = {"at": now_iso(), "result": "collinfo unreachable"}
        save_progress(pr)
        return
    pr["crawls"] = crawls
    log(f"crawls: {crawls}")

    def stop() -> bool:
        return time.monotonic() > deadline or api.consec_fail >= 8
    for crawl in crawls:
        for tld in tlds:
            if stop():
                break
            key = f"{crawl}/{tld}"
            u = pr["units"].setdefault(key, {"pages": None, "page_size": page_size, "next": 0, "failed": [], "hits": 0})
            if u["pages"] is not None and u["next"] >= u["pages"] and not u["failed"]:
                continue
            if u["pages"] is None or u.get("page_size") != page_size:
                n = num_pages(api, crawl, tld, page_size)
                if n is None:
                    log(f"{key}: could not get page count")
                    continue
                u.update(pages=n, page_size=page_size, next=0, failed=[], hits=0)
                log(f"{key}: {n} pages of {page_size} blocks")
            todo = list(u["failed"]) + list(range(u["next"], u["pages"]))
            u["failed"] = []
            for k, pg in enumerate(todo):
                if stop():
                    u["failed"] += [p for p in todo[k:] if p < u["next"]]
                    break
                got = fetch_page(api, crawl, tld, pg, page_size)
                if got is None:
                    u["failed"].append(pg)
                    log(f"{key}: page {pg} failed; kept for the next run")
                else:
                    u["hits"] += len(got)
                u["next"] = max(u["next"], pg + 1)
                if k % 50 == 49:
                    log(f"{key}: page {u['next']}/{u['pages']} hits={u['hits']} requests={api.requests}")
                    save_progress(pr)
            log(f"{key}: next={u['next']}/{u['pages']} failed={len(u['failed'])} hits={u['hits']}")
            save_progress(pr)
    pr["last_run"] = {"at": now_iso(), "minutes": minutes, "requests": api.requests, "stopped_for_errors": api.consec_fail >= 8}
    save_progress(pr)


def probe(log) -> None:
    api = Api(time.monotonic() + 900, log)
    crawls = collinfo(api, 3)
    log(f"collinfo: {crawls}")
    if not crawls:
        return
    c = crawls[0]
    tests = {
        "path-only wildcard (expected: no captures)": {"url": "*/mass-times*", "output": "json", "limit": 3},
        "page count .church": {"url": "*.church", "output": "json", "showNumPages": "true", "pageSize": 10},
        "page count .org": {"url": "*.org", "output": "json", "showNumPages": "true", "pageSize": 10},
        ".church filtered page 0": page_params("church", 0, 10),
        ".org filtered page 100": page_params("org", 100, 10),
    }
    for name, p in tests.items():
        st, body = api.get(cdx(c, p), tries=3)
        log(f"[{name}] status={st} bytes={len(body)} head={body[:300]!r}")


# ------------------------------------------------------------------------------------------------ build
def existing_domains() -> set[str]:
    """Registrable domains already known to the crawler through any other source."""
    seen: set[str] = set()

    def add(u):
        if isinstance(u, str) and u:
            d = reg_domain(urlparse(u if "://" in u else "https://" + u).hostname or "")
            if d:
                seen.add(d)
    for p in list((US / "url_sources").glob("**/*.websites.json")) + list((US / "discovermass").glob("*.websites.json")):
        if OUT_DIR in p.parents:
            continue
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        items = d if isinstance(d, list) else d.get("websites", []) + d.get("social_only", [])
        for w in items:
            add(w.get("url") or w.get("website"))
    for p in (US / "dioceses").glob("*/parishes.json"):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        for r in d.get("records", d if isinstance(d, list) else []):
            add(r.get("website"))
    sites = US / "sites"
    if sites.exists():
        for p in sites.glob("*/*.json"):
            if p.name != "summary.json":
                add(p.stem.split("__")[0])
    return seen


def diocese_domains() -> set[str]:
    out = set()
    try:
        for s in json.loads((US / "dioceses.json").read_text())["dioceses"]:
            if s.get("website"):
                d = reg_domain(urlparse(s["website"]).hostname or "")
                if d:
                    out.add(d)
    except (OSError, ValueError, KeyError):
        pass
    return out


def build(log) -> dict:
    # evidence = earlier committed evidence + every cached page (so a lost cache never loses domains)
    ev: dict[str, dict] = {}
    try:
        for d in json.loads(OUT_EVIDENCE.read_text()).get("domains", []):
            ev[d["domain"]] = {"hosts": Counter(d.get("hosts", {})), "urls": list(d.get("evidence_urls", [])),
                               "patterns": set(d.get("patterns", [])), "strong": set(d.get("patterns", [])) & set(STRONG),
                               "crawls": set(d.get("crawls", []))}
    except (OSError, ValueError):
        pass
    raw_urls = 0
    if RAW.exists():
        for f in sorted(RAW.glob("*/*/p*.json")):
            crawl = f.parts[-3]
            for u in json.loads(f.read_text()):
                raw_urls += 1
                pu = urlparse(u)
                dom = reg_domain(pu.hostname or "")
                pat, kind = pattern_of(pu.path)
                if not dom or not kind:
                    continue
                e = ev.setdefault(dom, {"hosts": Counter(), "urls": [], "patterns": set(), "strong": set(), "crawls": set()})
                e["hosts"][f"{pu.scheme}://{pu.hostname}"] += 1
                e["patterns"].add(pat)
                if kind == "strong":
                    e["strong"].add(pat)
                e["crawls"].add(crawl)
                if len(e["urls"]) < 6 and u not in e["urls"]:
                    e["urls"].append(u)
    have, dio = existing_domains(), diocese_domains()
    drop: Counter = Counter()
    keep = []
    for dom, e in ev.items():
        if dom in dio:
            drop["diocese_site"] += 1
        elif dom in SKIP_DOMAIN or any(dom.endswith("." + s) for s in SKIP_DOMAIN):
            drop["non_parish_host"] += 1
        elif NOT_CATHOLIC.search(dom):
            drop["other_denomination_or_non_parish_word"] += 1
        elif SCHOOL_OTHER.search(dom) and not re.search(r"parish|church|cathedral|basilica", dom):
            drop["school_hospital_or_group_host"] += 1
        elif not e["strong"]:
            drop["no_strong_catholic_page"] += 1
        elif dom in have:
            drop["already_known_or_crawled"] += 1
        else:
            keep.append(dom)
    keep.sort(key=lambda d: (-len(ev[d]["strong"]), -len(ev[d]["patterns"]), d))
    keep_set = set(keep)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    built = now_iso()
    sites = []
    for d in keep:
        e = ev[d]
        home = (e["hosts"].most_common(1)[0][0] if e["hosts"] else "https://" + d) + "/"
        sites.append({"url": home, "sources": ["commoncrawl"],
                      "churches": [{"slug": "cc-" + d, "source": "Common Crawl index", "evidence_urls": e["urls"]}]})
    pr = load_progress()
    (OUT_DIR / "XX.websites.json").write_text(json.dumps({
        "source": "Common Crawl index (CDX API): URL-pattern search for parish pages", "source_label": "url-sources",
        "attribution": ATTR, "state": "XX", "built_at": built, "consumer": "national parish-site crawler (run_state --state XX)",
        "note": "Domains only: name, address and state come from reading the site (unassigned-xx bucket). Precision is "
                "approximate; see ../commoncrawl.json for evidence. URLs unverified.",
        "counts": {"distinct_websites": len(sites), "churches": len(sites), "social_only": 0},
        "websites": sites, "social_only": []}, indent=1, ensure_ascii=False) + "\n", "utf-8")
    OUT_EVIDENCE.write_text(json.dumps({
        "source": "Common Crawl index", "attribution": ATTR, "built_at": built, "crawls": pr.get("crawls", []),
        "patterns": {"strong": STRONG, "weak": WEAK, "path_regex": PATH_RE, "host_regex": HOST_RE},
        "counts": {"matched_urls_in_cache": raw_urls, "candidate_domains": len(ev), "excluded": dict(drop),
                   "written_to_XX_websites": len(keep)},
        "domains": [{"domain": d, "hosts": dict(e["hosts"]), "evidence_urls": e["urls"], "patterns": sorted(e["patterns"]),
                     "strong_patterns": sorted(e["strong"]), "crawls": sorted(e["crawls"]), "kept": d in keep_set}
                    for d, e in sorted(ev.items())]}, indent=1, ensure_ascii=False) + "\n", "utf-8")
    log(f"build: {len(ev)} candidate domains, excluded {dict(drop)}, wrote {len(keep)}")
    return {"candidates": len(ev), "excluded": dict(drop), "written": len(keep)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0, help="scan for N minutes (resumable); 0 = no scan")
    ap.add_argument("--crawls", type=int, default=3)
    ap.add_argument("--tlds", default=",".join(DEFAULT_TLDS))
    ap.add_argument("--page-size", type=int, default=5)
    ap.add_argument("--build", action="store_true", help="rebuild outputs from the cache and earlier evidence")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()

    def log(m):
        print(f"{datetime.now(timezone.utc):%H:%M:%S} {m}", flush=True)
    if a.probe:
        probe(log)
    if a.minutes > 0:
        scan(a.minutes, a.crawls, [t.strip().lstrip(".") for t in a.tlds.split(",") if t.strip()], a.page_size, log)
    if a.build or a.minutes > 0:
        build(log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
