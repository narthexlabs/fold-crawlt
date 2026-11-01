"""Crawl every California church page on discovermass.com once, politely, and parse it into a clean record.

    python3 -I ingest/california/discovermass_crawl.py index              # sitemap -> ordered list (cached)
    python3 -I ingest/california/discovermass_crawl.py crawl [--max-seconds 6900] [--limit N] [--only slug,slug]
    python3 -I ingest/california/discovermass_crawl.py build              # cache -> data/real/california/discovermass/churches.json
    python3 -I ingest/california/discovermass_crawl.py show <slug>        # parse one cached page and print it

Source: discovermass.com (Diocesan Publications; schedules "Powered by MassTimes.org"). A SECONDARY source: the page
carries no "last updated" date (the sitemap's <lastmod> is the WordPress post's modified time), and a hidden
COVID-19 notice is still in every page. Oakland test (docs/research/13-mass-times-accuracy.md): about three in four
weekend times agree with the parish's own site.

Rules (docs/design/scaling-california.md):
  * robots.txt is read at the start of every run (cached 12 h) and honoured: "User-agent: * / Crawl-delay: 10".
    One request at a time (a lock file stops a second crawler), at least max(Crawl-delay, 10) s + 0.5 s between
    the end of one request and the start of the next, carried across runs in state.json.
  * Plain default User-Agent (python-requests), no cookies, no JavaScript, no bot-check work-arounds.
    A 403 / 429 / challenge page stops the run and is logged as BLOCKED; nothing is retried harder.
  * Raw HTML cached in data/raw/california/discovermass/pages/ (gitignored); a cached URL is never fetched again.
    Pages fetched for the Oakland pilot (data/raw/hosts/discovermass.com/) are copied in, not refetched.
  * Free text (schedule comments) passes ingest/public/privacy.py; no individuals beyond public clergy roles.
  * Files only: nothing is written to Supabase.

Order: the provisional diocese of the slug's city (Census place -> county -> diocese; ca_geo.py), Los Angeles first,
so a map prototype gets Los Angeles pins early. The real diocese is assigned later from the church's lat/lng
(discovermass_publish.py).
"""
from __future__ import annotations

import argparse
import fcntl
import html as htmlmod
import json
import re
import shutil
import sys
import time
import urllib.robotparser
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ingest" / "public"))
import ca_geo  # noqa: E402
import privacy  # noqa: E402

BASE = "https://discovermass.com"
CACHE = ROOT / "data" / "raw" / "california" / "discovermass"
PAGES = CACHE / "pages"
MAPS = CACHE / "sitemaps"
LOG = CACHE / "crawl.log"
STATE = CACHE / "state.json"
INDEX = CACHE / "index.json"
OLD_CACHE = ROOT / "data" / "raw" / "hosts" / "discovermass.com"
OUT_DIR = ROOT / "data" / "real" / "california" / "discovermass"
MIN_GAP = 10.0       # seconds; robots Crawl-delay is 10 and we never go below it
MARGIN = 0.5
SESSION = requests.Session()  # default python-requests User-Agent; nothing spoofed

ORDER = ["los-angeles", "orange", "san-bernardino", "san-diego", "fresno", "monterey", "san-jose", "san-francisco",
         "oakland", "sacramento", "stockton", "santa-rosa", None]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


# ------------------------------------------------------------------------------------------------ polite fetcher
class Blocked(Exception):
    pass


class Fetcher:
    def __init__(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.lockf = (CACHE / ".lock").open("w")
        try:
            fcntl.flock(self.lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("another discovermass crawler holds the lock; one request at a time")
        self.gap = MIN_GAP
        self.rp = None
        self.requests = 0
        self.errors_in_row = 0

    def _last(self) -> float:
        try:
            return float(json.loads(STATE.read_text())["last_request_end"])
        except (FileNotFoundError, KeyError, ValueError):
            return 0.0

    def _wait(self):
        wait = self._last() + self.gap + MARGIN - time.time()
        if wait > 0:
            time.sleep(wait)

    def _mark(self):
        STATE.write_text(json.dumps({"last_request_end": time.time(), "at": now_iso(), "gap_s": self.gap}))

    def _get(self, url: str):
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
        """robots.txt: cached 12 h; Disallow honoured, Crawl-delay raises the gap (never below 10 s)."""
        p, mp = CACHE / "robots.txt", CACHE / "robots.json"
        body = None
        if p.exists() and mp.exists():
            m = json.loads(mp.read_text())
            if datetime.now(timezone.utc) - datetime.fromisoformat(m["fetched_at"]) < timedelta(hours=12):
                body = p.read_text()
        if body is None:
            r, err = self._get(f"{BASE}/robots.txt")
            if err or r.status_code != 200:
                log(f"ROBOTS unreadable ({err or r.status_code}); keeping the 10 s gap and stopping to be safe")
                raise Blocked("robots.txt unreadable")
            body = r.text
            p.write_text(body)
            mp.write_text(json.dumps({"fetched_at": now_iso(), "status": r.status_code}))
            log(f"ROBOTS fetched: {body.strip()[:200]!r}")
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.splitlines())
        cd = rp.crawl_delay("*")
        self.gap = max(MIN_GAP, float(cd or 0))
        self.rp = rp
        return body

    def fetch(self, url: str, body_path: Path, meta_path: Path) -> dict:
        if self.rp is not None and not self.rp.can_fetch("*", url):
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
        meta = {"url": url, "final_url": r.url, "status": r.status_code,
                "content_type": r.headers.get("content-type", ""), "bytes": len(r.content), "fetched_at": now_iso()}
        if r.status_code == 200:
            self.errors_in_row = 0
            body_path.write_bytes(r.content)
            meta_path.write_text(json.dumps(meta))
        elif r.status_code in (404, 410):
            self.errors_in_row = 0
            meta_path.write_text(json.dumps(meta))  # negative cache: never refetched
        else:
            self.errors_in_row += 1
            log(f"ERROR {url} HTTP {r.status_code}")
            if self.errors_in_row >= 3:
                raise Blocked(f"3 errors in a row (last HTTP {r.status_code})")
        return meta


# ------------------------------------------------------------------------------------------------ index
def _seed_from_old_cache():
    """Copy the Oakland pilot's cached sitemaps and church pages (same day) instead of refetching them."""
    if not OLD_CACHE.exists():
        return 0
    n = 0
    for mp in OLD_CACHE.glob("*.json"):
        m = json.loads(mp.read_text())
        url, bp = m.get("url", ""), mp.with_suffix(".body")
        if m.get("status") != 200 or not bp.exists():
            continue
        mm = re.search(r"/church/([^/]+)/?$", url)
        if mm:
            dst = PAGES / f"{mm.group(1)}.html"
        elif re.search(r"/wp-sitemap[^/]*\.xml$", url):
            dst = MAPS / url.rsplit("/", 1)[1]
        else:
            continue
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bp, dst)
        dst.with_suffix(".json").write_text(json.dumps(dict(m, copied_from="data/raw/hosts/discovermass.com/" + bp.name)))
        n += 1
    return n


def slug_city(slug: str) -> str | None:
    """City part of '<name>-<city>-ca': the longest trailing token run that is a known CA place or postal name."""
    toks = slug[:-3].split("-") if slug.endswith("-ca") else slug.split("-")
    keys = ca_geo.place_counties().keys() | ca_geo.NEIGHBOURHOODS.keys()
    for n in range(min(5, len(toks) - 1), 0, -1):
        c = "-".join(toks[-n:])
        if c in keys:
            return c
        if c.startswith("saint-") and c.replace("saint-", "st-", 1) in keys:
            return c
    return None


def build_index(fetcher: Fetcher | None) -> list[dict]:
    seeded = _seed_from_old_cache()
    if seeded:
        log(f"SEED copied {seeded} cached files from the Oakland pilot (data/raw/hosts/discovermass.com)")
    MAPS.mkdir(parents=True, exist_ok=True)
    top = MAPS / "wp-sitemap.xml"
    if not top.exists():
        if fetcher is None:
            sys.exit("no cached sitemap; run 'index' through the crawler")
        fetcher.fetch(f"{BASE}/wp-sitemap.xml", top, top.with_suffix(".json"))
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", top.read_text()) if "posts-item" in u]
    rows = []
    for u in maps:
        p = MAPS / u.rsplit("/", 1)[1]
        if not p.exists():
            if fetcher is None:
                sys.exit(f"missing sitemap {p.name}")
            fetcher.fetch(u, p, p.with_suffix(".json"))
        fetched = json.loads(p.with_suffix(".json").read_text())["fetched_at"]
        for loc, lastmod in re.findall(r"<loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?", p.read_text()):
            mm = re.search(r"/church/([^/]+-ca)/?$", loc)
            if mm:
                rows.append({"url": loc, "slug": mm.group(1), "sitemap_lastmod": lastmod or None,
                             "sitemap": p.name, "sitemap_fetched_at": fetched})
    for r in rows:
        c = slug_city(r["slug"])
        county, how = ca_geo.city_county(c)
        r.update(slug_city=c, slug_county=county, slug_county_how=how,
                 provisional_diocese=ca_geo.provisional_diocese(county))
    rank = {d: i for i, d in enumerate(ORDER)}
    rows.sort(key=lambda r: (rank.get(r["provisional_diocese"], len(ORDER)), r["slug_city"] or "~", r["slug"]))
    for i, r in enumerate(rows):
        r["order"] = i
    INDEX.write_text(json.dumps({"built_at": now_iso(), "sitemaps": maps, "count": len(rows), "churches": rows},
                                indent=0))
    log(f"INDEX {len(rows)} California church pages; by provisional diocese "
        f"{dict(Counter(r['provisional_diocese'] for r in rows))}")
    return rows


def load_index() -> list[dict]:
    if not INDEX.exists():
        return build_index(None)
    return json.loads(INDEX.read_text())["churches"]


# ------------------------------------------------------------------------------------------------ crawl
def page_paths(slug: str) -> tuple[Path, Path]:
    return PAGES / f"{slug}.html", PAGES / f"{slug}.json"


def is_done(slug: str) -> bool:
    bp, mp = page_paths(slug)
    if not mp.exists():
        return False
    st = json.loads(mp.read_text()).get("status")
    return (st == 200 and bp.exists()) or st in (404, 410) or st == "disallowed by robots.txt"


def crawl(max_seconds: float, limit: int | None, only: list[str] | None) -> int:
    f = Fetcher()
    t0 = time.time()
    try:
        f.robots()
    except Blocked as e:
        log(f"STOP {e}")
        return 3
    rows = load_index()
    if only:
        rows = [r for r in rows if r["slug"] in set(only)]
    todo = [r for r in rows if not is_done(r["slug"])]
    log(f"RUN start: {len(rows) - len(todo)} of {len(rows)} cached, {len(todo)} to fetch; gap {f.gap + MARGIN:.1f} s; "
        f"max {max_seconds:.0f} s")
    PAGES.mkdir(parents=True, exist_ok=True)
    done = unexpected = 0
    code = 0
    for r in todo:
        if limit is not None and done >= limit:
            break
        if time.time() - t0 + f.gap + MARGIN + 60 > max_seconds:
            log("RUN time budget reached; relaunch to continue (cache makes it resumable)")
            code = 2
            break
        bp, mp = page_paths(r["slug"])
        try:
            meta = f.fetch(r["url"], bp, mp)
        except Blocked as e:
            log(f"STOP {e}")
            code = 3
            break
        done += 1
        st = meta.get("status")
        ok = st == 200 and bp.exists() and b'class="postId"' in bp.read_bytes()
        if st == 200 and not ok:
            unexpected += 1
            log(f"WARN {r['slug']}: 200 but no church markup")
            if unexpected >= 5:
                log("STOP 5 pages without church markup; the site may have changed or be serving a block page")
                code = 3
                break
        if done % 25 == 0 or st != 200:
            left = len([x for x in rows if not is_done(x["slug"])])
            log(f"PROGRESS fetched {done} this run; {len(rows) - left}/{len(rows)} cached; last {r['slug']} "
                f"({r['provisional_diocese']}) HTTP {st}")
    left = len([x for x in rows if not is_done(x["slug"])])
    log(f"RUN end: fetched {done} pages in {time.time() - t0:.0f} s ({f.requests} requests incl. robots); "
        f"{len(rows) - left}/{len(rows)} cached; {left} left; exit {code}")
    if left == 0:
        log("ALL DONE: every California church page is cached")
    return code


# ------------------------------------------------------------------------------------------------ parse
TAGS = re.compile(r"<[^>]+>")


def text(h: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(TAGS.sub(" ", h or ""))).strip()


DAY_ABBR = {"sun": "Sunday", "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
            "thr": "Thursday", "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "fri": "Friday",
            "sat": "Saturday", "weekdays": "Monday–Friday", "weekday": "Monday–Friday", "daily": "Daily"}
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
ORDINAL = {"1st": "First", "first": "First", "2nd": "Second", "second": "Second", "3rd": "Third", "third": "Third",
           "4th": "Fourth", "fourth": "Fourth", "5th": "Fifth", "fifth": "Fifth", "last": "Last"}
ORD_RE = re.compile(r"\b(1st|first|2nd|second|3rd|third|4th|fourth|5th|fifth|last)\.?\s*"
                    r"(sun|mon|tue|wed|thu|thr|fri|sat)[a-z]*\.?(?:\s*(?:only|of the month|of each month|each month))?",
                    re.I)
ORD2_RE = re.compile(r"(1st|first|2nd|second|3rd|third|4th|fourth|5th|fifth|last)\.?\s*(?:and|&|,)\s*"
                     r"(1st|first|2nd|second|3rd|third|4th|fourth|5th|fifth|last)\.?\s*"
                     r"(sun|mon|tue|wed|thu|thr|fri|sat)[a-z]*\.?", re.I)
LANGS = ("Spanish|Vietnamese|Korean|Tagalog|Filipino|Chinese|Mandarin|Cantonese|Portuguese|Latin|French|Italian|"
         "Polish|German|Japanese|Samoan|Tongan|Armenian|Arabic|Croatian|Hungarian|Indonesian|Igbo|Haitian Creole|"
         "Hmong|Lithuanian|Malayalam|Ukrainian|Syro-Malabar|Chaldean|Maronite|Eritrean|Ge'ez|Ilocano|Bilingual|"
         "ASL|Sign|American Sign Language|Ethiopian|Amharic|Swahili|Czech|Slovak|Irish|Gaelic|Basque|Español|Espanol")
LANG_ONLY = re.compile(rf"^(?:in\s+)?({LANGS})(?:\s+(?:mass|misa|language))?\.?$", re.I)
DIRECTORY_SITE = re.compile(r"catholicindex\.org|masstimes\.org|discovermass\.com|thecatholicdirectory\.com|"
                            r"catholicmasstime\.org|yelp\.com|google\.com/maps|mapquest\.com", re.I)
NOT_MASS = re.compile(r"liturgy of (the )?word|communion service|word and communion|celebration of the word|"
                      r"no mass|no masses", re.I)
OVERNIGHT = re.compile(r"night|overnight|24[- ]?h|24 hours|perpetual|midnight|vigil|next day|"
                       r"(?:to|until|till)\s+(?:the\s+)?(?:next\s+)?(?:sun|mon|tue|wed|thu|fri|sat)[a-z]*\.?\s+morning", re.I)
TYPO_FLAGS = ("implausible hour", "late hour", "not after start", "duplicate")  # the rest: incomplete, not typos
SOCIAL = {
    "facebook": re.compile(r"https?://(?:[a-z]+\.)?(?:facebook\.com|fb\.com|fb\.me)/[^\s\"'<>]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[^\s\"'<>]+", re.I),
    "youtube": re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/[^\s\"'<>]+", re.I),
    "x": re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^\s\"'<>]+", re.I),
}
NOT_CHURCH_SOCIAL = re.compile(r"DiocesanPublications|Diocesan_Pub|sharer|/share|intent/tweet|/dialog/", re.I)
CA_BOX = (32.4, 42.1, -124.6, -114.0)


def to_hhmm(t: str) -> str | None:
    t = (t or "").strip().lower().replace(".", "")
    if t in ("noon", "12 noon"):
        return "12:00"
    if t == "midnight":
        return "00:00"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])m?$", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if h > 12 or mi > 59 or h == 0:
        return None
    if h == 12:
        h = 0
    if ap == "p":
        h += 12
    return f"{h:02d}:{mi:02d}"


def split_range(t: str) -> tuple[str | None, str | None, str]:
    """'3:45pm-4:45pm' -> ('15:45', '16:45', raw). A start without am/pm borrows the end's ('3:30-4:30pm')."""
    raw = (t or "").strip()
    parts = re.split(r"\s*(?:-|–|—|to)\s*", raw, maxsplit=1)
    start = to_hhmm(parts[0])
    end = to_hhmm(parts[1]) if len(parts) > 1 else None
    if start is None and len(parts) > 1 and end and re.match(r"^\d{1,2}(:\d{2})?$", parts[0].strip()):
        start = to_hhmm(parts[0].strip() + parts[1].strip()[-2:])
    return start, end, raw


def norm_lang(s: str | None) -> str | None:
    if not s:
        return None
    s = htmlmod.unescape(s).strip().strip("()").strip()
    return s or None


def kind_for(section: str, label: str, comment: str | None) -> str:
    sec, lab = section.lower(), label.lower()
    if "confess" in lab or "reconcil" in lab or "penance" in lab:
        return "confession"
    if "adoration" in lab or "exposition" in lab or "holy hour" in lab:
        return "adoration"
    if "mass" in sec:
        return "other" if comment and NOT_MASS.search(comment) else "mass"
    if "devotion" in lab or "rosary" in lab or "novena" in lab:
        return "other"
    if "mass" in lab or "holy day" in lab:
        return "mass"
    return "other"


def day_for(section: str, label: str, prefix: str | None) -> str | None:
    lab = label.strip()
    if lab in DAYS:
        return lab
    if lab.lower() in DAY_ABBR:
        return DAY_ABBR[lab.lower()]
    if prefix:
        p = prefix.strip().rstrip(":").strip()
        if p.lower().rstrip(".") in DAY_ABBR:
            return DAY_ABBR[p.lower().rstrip(".")]
        if p in DAYS:
            return p
        return p  # e.g. "Holy Days" stays as written
    if "mass" in section.lower():
        return lab or None  # "Holy Days", "Vigil of Holy Days"
    return None


NEGATED = re.compile(r"\b(?:except|excepto|no|not|but not|other than|skip\w*)\b[^.;]{0,25}$", re.I)
# an ordinal that is part of a date range ("November to 1st Sund. in March") is not a monthly rule
DATE_BEFORE = re.compile(r"\b(?:to|until|till|through|thru|from|between|after|before)\s*$", re.I)
DATE_AFTER = re.compile(r"^\s*(?:in|of)\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)


def _date_phrase(c: str, m) -> bool:
    return bool(DATE_BEFORE.search(c[:m.start()]) or DATE_AFTER.match(c[m.end():]))


def _other_time(c: str, own: str | None) -> bool:
    """The comment names a time other than the row's own ("6:30pm mass every third Wednesday" at 18:30 does not)."""
    for m in privacy.TIME_RE.finditer(c):
        if not own or to_hhmm(re.sub(r"\s+", "", m.group(0))) != own:
            return True
    return False


def refine_day(day: str | None, comment: str | None, time: str | None = None) -> str | None:
    """'Friday' + 'First Fridays only' -> 'First Friday of the month' (only when the weekday agrees). The ordinal may
    stand anywhere in the comment ("Chaldean First Saturday of the Month", "Rosary. 2nd and 4th Sun."), but not after
    "except" / "no" ("except 1st Sunday": still weekly), and not when the comment gives another time of its own ("First
    Friday all night Adoration 7:15pm - 7:45am" describes a second event), unless it says "only"."""
    if not day or not comment or day not in DAYS:
        return day
    c = comment.strip()
    for m2 in ORD2_RE.finditer(c):  # "First and Third Sundays Only", "2nd and 4th Sun."
        if NEGATED.search(c[:m2.start()]) or _date_phrase(c, m2) or _other_time(c, time):
            continue
        if DAY_ABBR.get(m2.group(3).lower()[:3]) == day:
            return f"{ORDINAL[m2.group(1).lower()]} and {ORDINAL[m2.group(2).lower()]} {day} of the month"
    for m in ORD_RE.finditer(c):
        monthly = re.search(r"only|month", m.group(0), re.I)
        if NEGATED.search(c[:m.start()]) or _date_phrase(c, m) or (_other_time(c, time) and not monthly):
            continue
        wd = DAY_ABBR.get(m.group(2).lower()[:3]) or DAY_ABBR.get(m.group(2).lower())
        if wd == day:
            return f"{ORDINAL[m.group(1).lower()]} {day} of the month"
    return day


# Comments that make a listed time something other than a weekly Mass (2026-10-31 check: 91 shown weekly rows in 57
# parishes carried one). The comment is the listing's own note on that time.
NOT_MASS_SUBJECT = re.compile(r"^\s*(?:morning prayer|evening prayer|night prayer|liturgy of the hours|divine office|lauds|vespers|"
                              r"communion service|rosar(?:y|io)|novena|chaplet|divine mercy|stations|via crucis|devotions?|"
                              r"prayer service|word service|celebration of the word|holy hour)\b", re.I)
NOT_MASS_ANY = re.compile(r"morning prayer|evening prayer|liturgy of the hours|\blauds\b|\bvespers\b|communion service|"
                          r"word and communion|celebration of the word|liturgy of the word|no mass|no masses", re.I)
SCHOOL_TERM = re.compile(r"academic (?:year|calendar)|school (?:year|is in session|in session|mass)|while school|when (?:students|school)|"
                         r"students (?:are )?in session|university holidays|semester|during the school|school days|follows the school", re.I)
SEASON = re.compile(r"\b(summer|winter|lent(?:en)?|advent|easter|dst|daylight saving|memorial day to labor day|june|july|august)\b", re.I)


def comment_rules(row: dict) -> dict:
    """The listing's comment on a time, applied: not a Mass (kind other), a school-term or seasonal note (a qualifier in
    brackets after the day: readDay keeps seasonal rows off the date lists and shows the rest as a note), a language
    the comment leads with ("Chaldean First Saturday ..."). Records what it did in `comment_rule`."""
    c = (row.get("comment") or "").strip()
    if not c:
        return row
    done = []
    other_time = bool(privacy.TIME_RE.search(c))
    if row["kind"] == "mass" and (NOT_MASS_ANY.search(c) or (NOT_MASS_SUBJECT.match(c) and not re.search(r"\bmass|misa\b", c, re.I)
                                                               and not other_time)):
        row["kind"], row["kind_parsed"] = "other", "mass"
        done.append("not a Mass")
    day = row.get("day")
    if day and "(" not in day:
        q = None
        if SCHOOL_TERM.search(c):
            q = "during the school year"
        elif re.search(r"\b(?:not|no|except)\b[^.;]*memorial day to labor day", c, re.I):
            q = "not between Memorial Day and Labor Day"
        else:
            sm = SEASON.search(c)
            if sm:
                w = sm.group(1).lower()
                q = ("summer" if w in ("summer", "memorial day to labor day", "june", "july", "august") else
                     "winter" if w in ("winter",) else "daylight saving time" if w in ("dst", "daylight saving") else
                     "Lent" if w.startswith("lent") else w.capitalize())
        if q:
            row["day"] = f"{day} ({q})"
            done.append(f"qualifier: {q}")
    if not row.get("language"):
        lm = re.match(rf"^\s*({LANGS})\b", c, re.I)
        if lm and lm.group(1).lower() not in ("sign", "bilingual"):
            row["language"], row["language_from"] = lm.group(1).title(), "comment"
            done.append("language")
    if done:
        row["comment_rule"] = done
    return row


def check_row(row: dict) -> list[str]:
    """Obvious typos are flagged, never dropped silently (the earlier fill left out '12:30am' Sunday Masses)."""
    flags = []
    t, k = row["time"], row["kind"]
    ctx = " ".join(x for x in (row.get("comment"), row.get("raw")) if x)
    if t is None:
        flags.append("no structured time (free text)" if not (row.get("time_raw") or "").strip()
                     else f"time not parsed ({row['time_raw']})")
        return flags
    h = int(t[:2])
    if t == "00:00" and row.get("end_time") and row["end_time"] >= "23:00":
        row["all_day"] = True  # "12:00am-11:55pm": all-day (e.g. perpetual) adoration, not a typo
        return flags + (["no day given"] if row["day"] is None else [])
    if k == "mass":
        if h < 5 and not re.search(r"midnight", ctx, re.I):
            flags.append(f"implausible hour for a Mass ({row['time_raw']}): likely am/pm typo")
        elif h >= 22 and not re.search(r"midnight|night", ctx, re.I):
            flags.append(f"late hour for a Mass ({row['time_raw']}): check")
    elif h < 5 and not OVERNIGHT.search(ctx):
        flags.append(f"implausible hour ({row['time_raw']}): likely am/pm typo")
    if row["day"] is None:
        flags.append("no day given")
    e = row.get("end_time")
    if e and e <= t and k == "adoration" and t >= "12:00" and e <= "09:00" and e != t:
        row["overnight"] = True  # "8:00pm-5:00am": adoration through the night into the next morning
    elif e and e <= t and not OVERNIGHT.search(ctx) and e != "00:00":
        flags.append(f"end {e} not after start {t}")
    return flags


def parse_schedule(h: str) -> list[dict]:
    rows = []
    for box in re.finditer(r"<ul><li><h5>([^<]+)</h5></li>(.*?)</ul>", h, re.S):
        section = htmlmod.unescape(box.group(1)).strip()
        for li in re.finditer(r"<li class=\"([^\"]*)\"><span class=\"label\">([^<]+)</span>(.*?)</li>", box.group(2),
                              re.S):
            label = htmlmod.unescape(li.group(2)).strip()
            for inner in li.group(3).split("<span class='serviceTime'>")[1:]:
                times = re.findall(r"<span class='time'>([^<]+)</span>", inner)
                cm = re.search(r"<span class='comment'>(.*?)</span>", inner, re.S)
                lg = re.search(r"<span class='language'>([^<]+)</span>", inner)
                prefix = text(re.split(r"<span class='time'>", inner)[0]).rstrip(":").strip() or None
                comment = text(cm.group(1)) if cm else None
                lang = norm_lang(lg.group(1)) if lg else None
                if not times:
                    times = [""]
                for traw in times:
                    start, end, traw = split_range(htmlmod.unescape(traw))
                    day = day_for(section, label, prefix)
                    kind = kind_for(section, label, comment)
                    if "mass" in section.lower():
                        bits = [label, (prefix + ":") if prefix else None, traw]
                    else:  # "Confessions Sat: 3:45pm-4:45pm"
                        bits = [label, (prefix + ":") if prefix else None, traw]
                    raw = " ".join(b for b in bits if b)
                    if lang:
                        raw += f" ({lang})"
                    if comment:
                        raw += f" - {comment}"
                    lang_from = "page" if lang else None
                    if not lang and comment and LANG_ONLY.match(comment):
                        lang, lang_from = LANG_ONLY.match(comment).group(1).title(), "comment"
                    row = {"kind": kind, "day": refine_day(day, comment, start), "time": start, "end_time": end,
                           "language": lang, "language_from": lang_from, "comment": comment, "raw": raw,
                           "section": section, "label": label, "time_raw": traw}
                    row = comment_rules(row)
                    row["flags"] = check_row(row)
                    rows.append(row)
    seen = set()
    for r in rows:
        key = (r["kind"], r["day"], r["time"], r["language"])
        if key in seen:
            r["flags"].append("duplicate of an earlier row")
        seen.add(key)
    return rows


TYPE_RULES = [
    ("cathedral", re.compile(r"\bcathedral\b|\bco-cathedral\b", re.I)),
    ("basilica", re.compile(r"\bbasilica\b", re.I)),
    ("historic mission", re.compile(r"\bold mission\b|\bmission (?:san|santa|nuestra|dolores)\b|"
                                    r"\b(?:san|santa) [a-z]+(?: [a-z]+){0,3} mission\b|\bcarmel mission\b", re.I)),
    ("shrine", re.compile(r"\bshrine\b|\bsantuario\b", re.I)),
    ("campus ministry", re.compile(r"\bnewman\b|\bcampus ministry\b|\buniversity\b|\bcollege\b|\bcatholic center\b", re.I)),
    ("monastery", re.compile(r"\bmonastery\b|\babbey\b|\bhermitage\b|\bpriory\b|\bconvent\b", re.I)),
    ("chapel", re.compile(r"\bchapel\b|\bcapilla\b|\boratory\b", re.I)),
    ("parish", re.compile(r"\bparish\b|\bparroquia\b", re.I)),
    ("mission", re.compile(r"\bmission\b|\bmisi[oó]n\b", re.I)),
]


def church_type(name: str) -> tuple[str | None, str | None]:
    """Type only when the listing's own name states it ('St. Agnes Mission', 'Newman Center'); else None.
    First rule wins: cathedral, basilica, historic mission, shrine, campus ministry, monastery, chapel, parish, mission."""
    for t, rx in TYPE_RULES:
        if rx.search(name or ""):
            return t, "name"
    return None, None


def clean_url(u: str | None) -> str | None:
    if not u:
        return None
    u = htmlmod.unescape(u).strip()
    if not re.match(r"^https?://", u, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", u, re.I):
            u = "http://" + u
        else:
            return None
    return u


def parse_page(page: bytes, url: str) -> dict:
    h = page.decode("utf-8", "ignore")
    body = re.sub(r"<script\b[^>]*>.*?</script>", " ", h, flags=re.S)
    body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.S)
    out: dict = {}
    m = re.search(r'<meta property="og:title" content="([^"]*)"', h) or re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    out["name"] = text(m.group(1)) if m else None
    if not out["name"]:
        m = re.search(r"<title>([^<]+)</title>", h)
        out["name"] = htmlmod.unescape(m.group(1)).split("|")[0].strip() if m else None
    m = re.search(r'<h2 id="theaddress"[^>]*>(.*?)</h2>', body, re.S)
    addr_raw = text(m.group(1)) if m else None
    out["address_raw"] = addr_raw
    street = city = state = zipc = None
    if addr_raw:
        mm = re.match(r"^(.*?)[\s,]*,\s*([^,]+?),\s*([A-Z]{2})\.?\s*(\d{5})?(?:-\d{4})?\s*$", addr_raw)
        if mm:
            street, city, state, zipc = (mm.group(1).strip(" ,") or None), mm.group(2).strip(), mm.group(3), mm.group(4)
        else:
            mm = re.match(r"^([^,]+?),\s*([A-Z]{2})\.?\s*(\d{5})?\s*$", addr_raw)
            if mm:
                city, state, zipc = mm.group(1).strip(), mm.group(2), mm.group(3)
            z = re.search(r"\b(9[0-6]\d{3})\b", addr_raw)
            zipc = zipc or (z.group(1) if z else None)
    out.update(street=street, city=city, state=state, zip=zipc)
    # details block: address<br/>phone<br/><a>website</a><br/><br/><span underline>diocese</span><br/>Rite: ...
    det = re.search(r'<div class="col-md-12" style="padding-left: 0px; margin-bottom: 20px;">(.*?)</div>', body, re.S)
    det_h = det.group(1) if det else ""
    side = re.search(r'<div id="sidebar-info">(.*?)</div>', body, re.S)
    side_h = side.group(1) if side else ""
    m = re.search(r"side-phone attribute'>([^<]+)<", side_h)
    phone = text(m.group(1)) if m else None
    if not phone:
        for part in re.split(r"<br\s*/?>", det_h):
            t = text(part)
            if re.fullmatch(r"[\d()+.\-\s]{10,20}(?:\s*(?:x|ext\.?)\s*\d+)?", t or ""):
                phone = t
                break
    out["phone"] = phone
    digits = re.sub(r"\D", "", phone or "")
    out["phone_e164"] = f"+1{digits[-10:]}" if len(digits) in (10, 11) else None
    m = re.search(r"side-website attribute'><a href='([^']+)'", side_h) or \
        re.search(r"<a href='([^']+)' target='_blank'>", det_h)
    listed = clean_url(m.group(1)) if m else None
    out["website_listed"] = listed
    out["website"] = None if (listed and DIRECTORY_SITE.search(listed)) else listed
    if listed and not out["website"]:
        out["website_note"] = "the listing links a directory page, not the parish's own site"
    m = re.search(r"<span style='text-decoration: underline'>([^<]*)</span>", det_h)
    dline = text(m.group(1)) if m else None
    out["diocese_on_page"] = dline
    out["diocese_on_page_norm"] = re.sub(r"^(?:arch)?diocese of\s+", "", dline or "", flags=re.I).strip() or None
    m = re.search(r"Rite:\s*([^<\n]+)", det_h)
    out["rite"] = text(m.group(1)) if m else None
    m = re.search(r"side-language attribute'>([^<]+)<", side_h)
    out["languages_listed"] = [x.strip() for x in re.split(r"[,/]", text(m.group(1))) if x.strip()] if m else []
    # coordinates: the "Get Directions" / sidebar link carries daddr=lat,lng (no JSON-LD on these pages)
    lat = lng = None
    how = None
    mm = re.search(r"maps\.google\.com/maps\?daddr=(-?\d+\.\d+),\s*(-?\d+\.\d+)", h)
    if mm:
        lat, lng, how = float(mm.group(1)), float(mm.group(2)), "page: Directions link (maps.google.com daddr)"
    if lat is None:
        ma = re.search(r'value="(-?\d+\.\d+)"\s+data-street-lat', h)
        mb = re.search(r'value="(-?\d+\.\d+)"\s+data-street-lng', h)
        if ma and mb:
            lat, lng, how = float(ma.group(1)), float(mb.group(1)), "page: street-view data attribute"
    flags = []
    if lat is not None and not (CA_BOX[0] <= lat <= CA_BOX[1] and CA_BOX[2] <= lng <= CA_BOX[3]):
        flags.append(f"coordinates outside California ({lat},{lng}); not used")
        out["latlng_rejected"] = [lat, lng]
        lat = lng = None
        how = None
    out.update(lat=lat, lng=lng, latlng_source=how)
    # social links: only in the church's own blocks (details, description, sidebar), never DiscoverMass's footer/share
    own = det_h + side_h
    dm = re.search(r'<div class="inner-items">(.*?)</div>', body, re.S)
    own += dm.group(1) if dm else ""
    social = {}
    for k, rx in SOCIAL.items():
        found = sorted({u.rstrip("/.,;") for u in rx.findall(own) if not NOT_CHURCH_SOCIAL.search(u)})
        if out["website"] and rx.match(out["website"]) and not NOT_CHURCH_SOCIAL.search(out["website"]):
            found = sorted(set(found) | {out["website"].rstrip("/")})
        if found:
            social[k] = found
    out["social"] = social
    if out["website"] and any(rx.match(out["website"]) for rx in SOCIAL.values()):
        out["website_is_social"] = True
    m = re.search(r'class="postId">(\d+)<', h)
    out["post_id"] = m.group(1) if m else None
    m = re.search(r"masstimesId=(\d+)", h)
    out["masstimes_id"] = m.group(1) if m else None
    out["covid_notice_hidden"] = bool(re.search(r'<div id="covid"[^>]*display:\s*none', h))
    out["type"], out["type_source"] = church_type(out["name"] or "")
    # bulletins tab
    i = h.find('id="bulletins"')
    pane = h[i: i + 40000] if i >= 0 else ""
    out["bulletins_listed"] = 0 if (not pane or "no bulletins available" in pane.lower()) else \
        len(set(re.findall(r"href=[\"']([^\"']+\.pdf)[\"']", pane, re.I)))
    out["schedule"] = parse_schedule(h)
    out["page_flags"] = flags
    return out


PRIV_RULES = [("schedule[].comment", "schedule"), ("schedule[].raw", "schedule")]


def record_for(ix: dict) -> dict | None:
    bp, mp = page_paths(ix["slug"])
    if not mp.exists():
        return None
    meta = json.loads(mp.read_text())
    base = {"dm_slug": ix["slug"], "source_url": ix["url"], "fetched_at": meta.get("fetched_at"),
            "http_status": meta.get("status"), "sitemap_lastmod": ix.get("sitemap_lastmod"),
            "slug_city": ix.get("slug_city"), "provisional_diocese": ix.get("provisional_diocese")}
    if meta.get("status") != 200 or not bp.exists():
        return dict(base, unavailable=True)
    p = parse_page(bp.read_bytes(), ix["url"])
    if meta.get("final_url") and meta["final_url"].rstrip("/") != ix["url"].rstrip("/"):
        p["page_flags"].append(f"redirected to {meta['final_url']}")
    cnt = privacy.screen(p, PRIV_RULES)
    rec = dict(base, **p)
    rec["privacy"] = {k: v for k, v in cnt.items() if v} or None
    sched = rec["schedule"]
    clean = [r for r in sched if not r["flags"]]
    rec["has_times"] = any(r["kind"] == "mass" and not r["flags"] for r in sched)
    rec["counts"] = dict(Counter(r["kind"] for r in sched), flagged=sum(1 for r in sched if r["flags"]),
                         rows=len(sched), clean=len(clean))
    county = ca_geo.county_at(rec["lat"], rec["lng"])
    rec["county"], rec["county_from"] = (county, "lat/lng in Census county polygon") if county else (None, None)
    if not county:
        c, how = ca_geo.city_county(rec.get("city") or ix.get("slug_city"))
        if c:
            rec["county"], rec["county_from"] = c, f"city name ({how})"
    return rec


def build(write: bool = True) -> dict:
    """All cached pages -> records. write=False returns them for discovermass_publish.py (which adds dioceses)."""
    rows = load_index()
    recs = []
    for ix in rows:
        r = record_for(ix)
        if r is not None:
            recs.append(r)
    ok = [r for r in recs if not r.get("unavailable")]
    flagged = [{"dm_slug": r["dm_slug"], "name": r["name"], "city": r["city"], "raw": s["raw"], "flags": s["flags"],
                "source_url": r["source_url"]} for r in ok for s in r["schedule"] if s["flags"]]
    summary = {
        "pages_in_sitemap": len(rows), "pages_cached": len(recs), "parsed": len(ok),
        "unavailable": len(recs) - len(ok),
        "with_times": sum(r["has_times"] for r in ok), "with_website": sum(bool(r["website"]) for r in ok),
        "with_latlng": sum(r["lat"] is not None for r in ok),
        "with_social": {k: sum(bool(r["social"].get(k)) for r in ok) for k in SOCIAL},
        "schedule_rows": sum(len(r["schedule"]) for r in ok), "flagged_rows": len(flagged),
    }
    out = {
        "source": "discovermass.com church pages (Diocesan Publications; schedules 'Powered by MassTimes.org')",
        "source_label": "secondary-discovermass",
        "caveat": "Secondary source. Pages carry no last-updated date (sitemap_lastmod is the WordPress post's "
                  "modified time) and still contain a hidden COVID-19 notice. Prefer the parish's own site; rows "
                  "with flags are suspected typos and should not be shown as-is.",
        "built_at": now_iso(), "summary": summary, "records": recs}
    if not write:
        return out
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "churches.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    (OUT_DIR / "flagged_rows.json").write_text(json.dumps({"built_at": out["built_at"], "count": len(flagged),
                                                           "rows": flagged}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "crawl", "build", "show"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--max-seconds", type=float, default=6900)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only")
    a = ap.parse_args()
    if a.cmd == "index":
        build_index(None)
    elif a.cmd == "crawl":
        sys.exit(crawl(a.max_seconds, a.limit, a.only.split(",") if a.only else None))
    elif a.cmd == "build":
        build()
    elif a.cmd == "show":
        ix = next((r for r in load_index() if r["slug"] == a.slug), {"slug": a.slug, "url": f"{BASE}/church/{a.slug}/"})
        print(json.dumps(record_for(ix), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
