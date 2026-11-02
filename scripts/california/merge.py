"""One record per parish for each California diocese, merged from every source we hold, and the static files the /ca
pages read. Called by scripts/california/build_ca_static.py (which also writes search.json); can be run alone:

    python3 -I scripts/california/build_ca_static.py                 # every diocese + search.json
    python3 -I scripts/california/build_ca_static.py --only san-diego --skip-search --no-geocode

Nothing here writes to Supabase. Oakland reads our database read-only with the anon key (build_ca_static.build_oakland);
every other diocese is built from files:

    data/real/california/dioceses/<slug>/       the diocese's own directory: parishes, structure, entities, events (official)
    data/real/california/dioceses/los-angeles/map/  the archdiocese's regional parish map (region + deanery, geocoded)
    data/real/california/discovermass/churches.json DiscoverMass / MassTimes.org listing (secondary)
    data/real/california/sites/<slug>/<domain>.json the parish's own website: schedule, groups, events, social, bulletin
    data/real/california/schools_cde.json, health.json (if present)     schools (CDE + NCES), Catholic health facilities
These are read, never written. The DiscoverMass publisher owns public/ca/<slug>/index.json and schedules.json.

Matching, per diocese. The diocese's directory is the list when it exists (status comes from it); DiscoverMass churches,
the Los Angeles regional map and crawled sites are matched to it by (1) website domain, (2) normalised street address,
(3) name + city, then (4) within 120 m with a shared name word. Every match records `match_method`. A crawled site is
attached through the ids its crawl matched (directory or DiscoverMass), else by domain. Where a diocese has no
directory yet, DiscoverMass churches are the list (secondary) and status is "not collected yet".

Schedules, per kind (Mass / confession / adoration). Precedence: hand-checked (curated) or the parish's own website >
the parish bulletin > an archived (Wayback) copy of the parish website > the diocese's directory > DiscoverMass. Once a
source has rows of a kind, lower sources' rows of that kind are hidden (kept in data/real/california/merged/, with
`hidden`). Rows flagged needs_review (or DiscoverMass typo flags) are never shown and never count for precedence.
Identical rows are folded; of the same time with and without a language, the one with the language is kept.

Writes (all owned here):
    public/ca/<slug>/map.json        pins (id, name, city, lat, lng, region, deanery, kind, has_times, has_groups,
                                     has_events), pastoral layer, deaneries, sources, "what we have" line. First paint.
    public/ca/<slug>/p/<id>.json     one parish: address, office phone, website, social, schedules by kind with source +
                                     date, groups, next 30 days of events, bulletin, sources. On demand.
    public/ca/<slug>/groups.json     compact group index for the Groups tab. On demand.
    public/ca/<slug>/events.json     next 60 days, compact, weekly series folded. On demand.
    public/ca/<slug>/places.json     schools, charities, cemeteries, shrines, retreat centers, health, Newman. On demand.
    data/real/california/merged/<slug>.json   every merged record with all rows (hidden ones marked) and matches
    data/real/california/merged/geocode_cache.json   US Census Geocoder answers (public domain), reused across runs
    data/real/california/coverage.json        per-diocese counts with their sources
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[2]
PUB = ROOT / "public" / "ca"
REAL = ROOT / "data" / "real" / "california"
MERGED = REAL / "merged"
DIOCESES = json.loads((REAL / "dioceses.json").read_text())
DM_FILE = REAL / "discovermass" / "churches.json"
GEO_CACHE = MERGED / "geocode_cache.json"
CURATED_SOURCE = "curated-2026-10-26"
TZ = ZoneInfo("America/Los_Angeles")
TODAY = datetime.now(TZ).date()

for sub in ("ingest/public", "ingest/public/groups"):
    sys.path.insert(0, str(ROOT / sub))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold(s: str | None) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def slug(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(s).replace("'", "").replace("’", "")).strip("-")


def r5(x):
    return None if x is None else round(float(x), 5)


def write(path: Path, obj, indent=None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if indent is None else None, indent=indent)
    path.write_text(s + ("\n" if indent else ""))
    return len(s.encode())


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def dist_m(a, b) -> float:
    dy = (a[0] - b[0]) * 111_000
    dx = (a[1] - b[1]) * 111_000 * math.cos(math.radians(a[0]))
    return (dx * dx + dy * dy) ** 0.5


def day_of(iso: str | None) -> str | None:
    return (iso or "")[:10] or None


# ------------------------------------------------------------------------------------------------ keys for matching
STOP = {"st", "saint", "sts", "saints", "of", "the", "church", "parish", "catholic", "and", "de", "la", "el", "los", "las",
        "our", "lady", "san", "santa", "mission", "community", "roman", "rc", "chapel", "center", "centre", "shrine", "a",
        "cathedral", "basilica", "co", "parroquia", "iglesia", "nuestra", "senora", "del", "holy"}


def tokens(s: str | None) -> set[str]:
    t = re.sub(r"[^a-z0-9 ]+", " ", fold(s).replace("'", "")).split()
    return {w for w in t if w not in STOP and len(w) > 1}


SUFFIX = {"n", "s", "e", "w", "north", "south", "east", "west", "st", "street", "ave", "avenue", "blvd", "boulevard", "rd",
          "road", "dr", "drive", "pl", "place", "way", "ln", "lane", "ct", "court", "hwy", "highway", "pkwy", "parkway"}


def street_key(s: str | None) -> str | None:
    if not s:
        return None
    m = re.match(r"\s*(\d+)[a-z]?\s+(.*)", fold(s))
    if not m:
        return None
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", m.group(2)).split() if w not in SUFFIX]
    return f"{m.group(1)} {words[0] if words else ''}"


# Hosts that many parishes share: a path segment tells them apart, or they never identify a parish.
PATH_HOSTS = {"sites.google.com", "facebook.com", "m.facebook.com", "instagram.com", "linktr.ee", "wixsite.com"}
NEVER_HOSTS = {"catholicindex.org", "discovermass.com", "masstimes.org", "la-archdiocese.org", "lacatholics.org", "google.com",
               "goo.gl", "maps.google.com", "youtube.com", "flocknote.com", "x.com", "twitter.com"}


# The dioceses' own websites: a page there is the diocese's, never a parish's own site (its groups, social links and
# bulletin are the diocese's). Sites on these hosts are not attached to a parish.
DIOCESAN_HOSTS = {re.sub(r"^www\.", "", (urlparse(d["website"]).hostname or "")) for d in DIOCESES["dioceses"]} | {
    "la-archdiocese.org", "sfarchdiocese.org", "sbdiocese.org", "oakdiocese.org", "rcbo.org"}
NEVER_HOSTS |= DIOCESAN_HOSTS


def domain(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    try:
        p = urlparse(u)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    host = re.sub(r"^(www\d?|m)\.", "", host)
    if not host or "." not in host:
        return None
    base = ".".join(host.split(".")[-2:])
    if host in NEVER_HOSTS or base in NEVER_HOSTS:
        return None
    if host in PATH_HOSTS or base in PATH_HOSTS:
        seg = [x for x in p.path.split("/") if x][:2 if host == "sites.google.com" else 1]
        return f"{host}/{'/'.join(seg).lower()}" if seg else None
    return host


# ------------------------------------------------------------------------------------------------ geocoding
def geocode_all(addresses: list[str], enabled: bool) -> dict[str, list[float] | None]:
    """US Census Geocoder (public domain), batch endpoint: one request for every address not yet in the cache."""
    cache = load_json(GEO_CACHE, {}) or {}
    todo = sorted({a for a in addresses if a and a not in cache})
    if todo and enabled:
        print(f"geocoding {len(todo)} addresses (Census batch)")
        for i in range(0, len(todo), 2000):
            chunk = todo[i:i + 2000]
            buf = io.StringIO()
            w = csv.writer(buf)
            for j, a in enumerate(chunk):
                street, city, zp = a.split("|")
                w.writerow([j, street, city, "CA", zp])
            try:
                r = requests.post("https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
                                  files={"addressFile": ("a.csv", buf.getvalue(), "text/csv")},
                                  data={"benchmark": "Public_AR_Current"}, timeout=600)
                r.raise_for_status()
            except requests.RequestException as e:
                print("geocoder unavailable:", e)
                break
            got = {}
            for row in csv.reader(io.StringIO(r.text)):
                if len(row) >= 6 and row[2] == "Match" and row[5]:
                    lng, lat = (float(x) for x in row[5].split(","))
                    got[int(row[0])] = [round(lat, 6), round(lng, 6), row[3], row[4]]
            for j, a in enumerate(chunk):
                cache[a] = got.get(j)
        write(GEO_CACHE, dict(sorted(cache.items())), indent=0)
    return cache


def addr_key(street: str | None, city: str | None, zp: str | None) -> str | None:
    if not street or not re.match(r"\s*\d", street) or not city:
        return None
    return f"{street.strip()}|{city.strip()}|{(zp or '').strip()[:5]}"


# ------------------------------------------------------------------------------------------------ schedules
def tier(source: str | None) -> int:
    """Lower is better. Mirrors lib/find.ts sourceTier(). Decided 2026-10-31 on the independent check
    (docs/review/ca-check-2026-10-31.md: automatic parish-site readings 47% wrong per Mass slot, DiscoverMass 10.5%) and
    the template grading (ingest/california/sites/templates/README.md: DiscoverMass is itself 78% precise against the
    hand-checked Oakland rows, so a parish-site template that meets the bar outranks it):
    0 hand-checked · 1 the parish bulletin read by hand · 2 an archived copy read by hand · 3 the parish website read by a
    trusted template ('site-<template>-<date>': the template passed its bar, the site's parse is high quality and agrees
    with DiscoverMass on Sunday; passed as of 2026-11-01: generic_strict (Oakland truth, 92.8% precision, 85.7% Sunday
    recall) and ecatholic (live US truth, held-out 12: weekend 90.0% precision, 87.1% Sunday recall; weekday 95.3%,
    confession 96.4%, adoration 100% pooled precision; templates/grades.json `live`, `kinds`) · 4 DiscoverMass · 5 the diocese's directory, read automatically · 6 the parish website,
    read automatically and untrusted (crawled-, sidedoor-, and the site crawl's own bulletin-PDF and archived-page
    readings, crawled-bulletin- / crawled-wayback-) · 7 weekly entries repeated in the parish's own calendar feed
    (calendar-; 2026-11-01), used only for a kind and day bucket no posted schedule lists."""
    s = source or ""
    if s == CURATED_SOURCE or s.startswith("curated-"):
        return 0
    if s == "parish website" or s.startswith(("crawled-", "sidedoor-")):
        return 6
    if s.startswith("bulletin-"):
        return 1
    if s.startswith("wayback-"):
        return 2
    if s.startswith("site-"):
        return 3
    if s.startswith("secondary-"):
        return 4
    if s.startswith("calendar-"):
        return 7   # a weekly entry repeated in the parish's calendar feed (parse_site.calendar_rows): below every posted schedule
    return 5  # the diocese's directory ('oakdiocese.org directory', 'directory-<slug>-<date>')


CALENDAR_ROWS = False   # see the calendar loop in build_diocese; True once lib/find.ts has the 'calendar-' tier (7)
T_TRUSTED, T_DM, T_DIR, T_AUTO, T_CAL = 3, 4, 5, 6, 7
AUTO_TIERS = {T_TRUSTED, T_DIR, T_AUTO, T_CAL}   # read by a program, not a person: the not-a-Mass and conflict checks apply
TIER_TEXT = {0: "checked", 1: "bulletin", 2: "wayback", 3: "site", 4: "secondary", 5: "directory", 6: "own", 7: "calendar"}
RANGE = re.compile(r"\d(?::\d\d)?\s*(?:a\.?m\.?|p\.?m\.?)?\s*[-–]\s*\d{1,2}(?::\d\d|\s*[ap]\.?\s?m\b)", re.I)  # lib/schedule.ts RANGE


def display_kind(r: dict) -> str:
    """As lib/schedule.ts kindOf(): a 'mass' row that only gives a time range, without the word Mass, is not a Mass."""
    k = r.get("kind") or "other"
    if k not in ("mass", "confession", "adoration"):
        return "other"
    if tier(r.get("source")) != 0 and k == "mass" and RANGE.search(r.get("raw") or "") and not re.search(r"\b(mass|misa)\b", r.get("raw") or "", re.I):
        return "other"
    return k


def site_bracket(raw: str | None) -> str | None:
    m = re.match(r"^\[([^\]]+)\]", raw or "")
    return m.group(1) if m else None


# ---- a consistency check on rows read from free text (parish sites, the directory's lines): the parser can carry a
# day or a language across a line ("Saturday 5:30pm; Sunday 8am, 9:45am, 1:15pm (Spanish)"). A row whose own line puts
# its time under another day is held back (needs_review: day_mismatch); a language the line ties to another time is
# dropped (language_parsed keeps what the parser said). Lines about classes, livestreams or opening hours are held back.
TIME_TOK = re.compile(r"\b(\d{1,2})(?::(\d\d))?\s*([ap])\.?\s*m\b\.?|\bnoon\b", re.I)
DAYW = {"sunday": 0, "sun": 0, "domingo": 0, "monday": 1, "mon": 1, "lunes": 1, "tuesday": 2, "tue": 2, "tues": 2, "martes": 2,
        "wednesday": 3, "wed": 3, "miercoles": 3, "thursday": 4, "thu": 4, "thur": 4, "thurs": 4, "jueves": 4, "friday": 5, "fri": 5,
        "viernes": 5, "saturday": 6, "sat": 6, "sabado": 6}
LETTER = {"m": 1, "tu": 2, "w": 3, "th": 4, "f": 5, "sa": 6, "su": 0}
DAY_RX = re.compile(r"\b(?P<a>" + "|".join(sorted(DAYW, key=len, reverse=True)) + r")s?(?![a-z])\.?(?:(?:\s*(?:-|–|—)\s*|\s+(?:to|through|thru|a|al|hasta)\s+)(?P<b>"
                    + "|".join(sorted(DAYW, key=len, reverse=True)) + r")s?(?![a-z]))?|\b(?P<wk>weekdays?|daily|every day)\b"
                    r"|\b(?P<sl>(?:m|tu|w|th|f|sa|su)(?:\s*/\s*(?:m|tu|w|th|f|sa|su))+)\b", re.I)
NOT_SERVICE = re.compile(r"\b(class(es)?|ocia|rcia|livestream|live stream|office hours?|church is open|open daily|gift shop|meeting|rehearsal|"
                         r"choir|bingo|religious education|catechism|faith formation|school|practice|coffee|perpetual help|knights)\b", re.I)
# A line naming a non-Mass service is never a Mass, even when it says "Mass" elsewhere ("Communion service in place of Mass").
NOT_MASS_LINE = re.compile(r"communion service|eucharistic service|liturgy service|word and communion|celebration of the word|liturgy of the hours|"
                           r"morning prayer|evening prayer|vespers|\blauds\b", re.I)
# Lines that are about something else even when they name a Mass (an intention, a homily note, a dated one-off event).
NOT_SCHEDULE = re.compile(r"intention|withheld|prayed for|homil|\bnote\b|parish hall|\bhall\b|prayer group|\bmeets\b|"
                          r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", re.I)
LANG_WORDS = {"Spanish": r"spanish|espa[nñ]ol", "Vietnamese": r"vietnamese|vi[eệ]t", "Tagalog": r"tagalog|filipino", "Korean": r"korean",
              "Chinese": r"chinese|cantonese|mandarin", "Latin": r"latin|tridentine|extraordinary form", "Portuguese": r"portuguese",
              "English": r"english", "Polish": r"polish", "Italian": r"italian", "French": r"french", "Tongan": r"tongan"}


def _hhmm(m) -> str:
    if m.group(0).lower() == "noon":
        return "12:00"
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return f"{h:02d}:{m.group(2) or '00'}"


def _days(m) -> set[int]:
    if m.group("wk"):
        return set(range(1, 6)) if m.group("wk").lower().startswith("weekday") else set(range(7))
    if m.group("sl"):
        return {LETTER[x.strip().lower()] for x in m.group("sl").split("/")}
    a = DAYW[fold(m.group("a"))]
    if not m.group("b"):
        return {a}
    b, out, i = DAYW[fold(m.group("b"))], set(), a
    while True:
        out.add(i)
        if i == b:
            return out
        i = (i + 1) % 7


def _row_day(day: str | None) -> set[int] | None:
    if not day:
        return None
    ms = list(DAY_RX.finditer(fold(day)))
    if not ms:
        return None
    out = set()
    for m in ms:
        out |= _days(m)
    return out


def sanity(r: dict) -> dict:
    raw = fold(re.sub(r"[\u2013\u2014]", "-", r.get("raw") or ""))
    if not raw or r.get("needs_review"):
        return r
    if NOT_SERVICE.search(raw) and not re.search(r"\b(mass|misa|confess|reconcil|ador)", raw):
        r["needs_review"], r["review_reason"] = True, "not_a_service"
        return r
    if r.get("kind") == "mass" and tier(r.get("source")) in AUTO_TIERS and NOT_MASS_LINE.search(raw):
        r["needs_review"], r["review_reason"] = True, "not_a_mass"
        return r
    if NOT_SCHEDULE.search(raw):
        r["needs_review"], r["review_reason"] = True, "not_a_schedule_line"
        return r
    # "Friday of the Month: 7 pm" (no ordinal) read as every Friday: which week is not said, so hold it back
    if re.search(r"\bof (the|each|every) month\b", raw) and r.get("day") and "month" not in fold(r["day"]) and not re.search(
            r"\b(first|second|third|fourth|last|1st|2nd|3rd|4th)\s+\w+\s+of\s+(each|the|every)\s+month", raw):
        r["needs_review"], r["review_reason"] = True, "monthly_rule_unclear"
        return r
    # "First Friday of each month: 7:15 p.m." read as plain "Friday": put the line's own monthly rule back
    mo = re.search(r"\b(first|second|third|fourth|last|1st|2nd|3rd|4th)\s+(sunday|monday|tuesday|wednesday|thursday|friday|saturday)s?\s+of\s+(each|the|every)\s+month", raw)
    if mo and r.get("day") and "month" not in fold(r["day"]) and fold(r["day"]).startswith(mo.group(2)):
        nth = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth"}.get(mo.group(1), mo.group(1))
        r["day_parsed"], r["day"] = r["day"], f"{nth.capitalize()} {mo.group(2).capitalize()} of the month"
    t = (r.get("time") or "")[:5]
    if re.match(r"\d\d:\d\d", t):
        h, mm = int(t[:2]), t[3:5]
        h12 = (h + 11) % 12 + 1
        pats = [rf"(?<![\d:]){h12}[:.]{mm}(?!\d)"]
        if mm == "00":
            pats.append(rf"(?<![\d:]){h12}\s*(?:[ap]\.?\s?m\b|o'?clock)")
        if t == "12:00":
            pats.append(r"\bnoon\b|\bmediod")
        if not any(re.search(x, raw) for x in pats):
            r["needs_review"], r["review_reason"] = True, "time_not_in_line"
            return r
    toks = [(m.start(), _hhmm(m)) for m in TIME_TOK.finditer(raw)]
    pos = next((p for p, x in toks if x == t), None)
    want = _row_day(r.get("day"))
    if pos is not None and want:
        # the days named between this time and the time before it; if none, the days before that time, and so on
        cuts = [0] + [m.end() for m in TIME_TOK.finditer(raw) if m.end() <= pos] + [pos]
        named = set()
        for a, b in reversed(list(zip(cuts, cuts[1:]))):
            for m in DAY_RX.finditer(raw, a, b):
                named |= _days(m)
            if named:
                break
        if named and not (named & want):
            r["needs_review"], r["review_reason"] = True, "day_mismatch"
            return r
    lang = r.get("language")
    if lang and pos is not None and len({x for _, x in toks}) > 1:
        first = lang.split(" ")[0]
        rx = LANG_WORDS.get(first) or next((v for v in LANG_WORDS.values() if re.fullmatch(v, fold(first))), None) or re.escape(fold(first))
        lm = [m.start() for m in re.finditer(rx, raw)] if rx else []
        if lm:
            cuts = [i for i, ch in enumerate(raw) if ch in ";|\n"]
            seg = (max([c for c in cuts if c < pos], default=-1), min([c for c in cuts if c > pos], default=len(raw)))
            in_seg = [p for p in lm if seg[0] < p < seg[1]]
            times_seg = [(p, x) for p, x in toks if seg[0] < p < seg[1]]
            keep = False
            if in_seg and times_seg:
                lp = in_seg[0]
                if lp < times_seg[0][0]:
                    keep = True
                else:
                    before = [x for p, x in times_seg if p < lp]
                    keep = bool(before) and before[-1] == t
            if not keep:
                r["language_parsed"], r["language"] = lang, None
    return r


# ---- precedence works per (kind, day bucket): a source hides another only for the buckets it lists itself. Buckets:
# the Saturday vigil (from 2 PM), Sunday, each weekday on its own, Saturday daytime, other (holy days, monthly).
BUCKETS = ("vigil", "sunday", "mon", "tue", "wed", "thu", "fri", "saturday", "other")
DAY_BUCKET = ("sunday", "mon", "tue", "wed", "thu", "fri", "saturday")
WEEKDAY_BUCKETS = ("mon", "tue", "wed", "thu", "fri")
BUCKET_GROUP = {"vigil": "weekend", "sunday": "weekend", "mon": "weekdays", "tue": "weekdays", "wed": "weekdays", "thu": "weekdays",
                "fri": "weekdays", "saturday": "saturday", "other": "other"}
VIGIL_FROM = "14:00"   # lib/schedule.ts VIGIL_FROM
WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def buckets_text(bs, kind: str = "mass") -> str:
    """As lib/find.ts bucketsText(): "the Saturday vigil, Sundays and weekdays", "Mondays and Fridays"; for confession
    and adoration the Saturday buckets read "Saturdays from 2 PM" / "Saturdays before 2 PM"."""
    bs = set(bs)
    out = []
    if "vigil" in bs:
        out.append("the Saturday vigil" if kind == "mass" else "Saturdays from 2 PM")
    if "sunday" in bs:
        out.append("Sundays")
    wk = [b for b in WEEKDAY_BUCKETS if b in bs]
    out += ["weekdays"] if len(wk) == 5 else [f"{WEEKDAY[DAY_BUCKET.index(b)]}s" for b in wk]
    if "saturday" in bs:
        out.append("Saturday daytime" if kind == "mass" else "Saturdays before 2 PM")
    if "other" in bs:
        out.append("monthly, holy days and other times")
    return out[0] if len(out) == 1 else ", ".join(out[:-1]) + " and " + out[-1] if out else ""


def is_overnight(time: str | None) -> bool:
    m = re.match(r"^(\d\d):(\d\d)-(\d\d):(\d\d)", time or "")
    return bool(m) and m.group(3) + m.group(4) < m.group(1) + m.group(2)


RANGE_WORDS = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*(?:-|–|—|to|through|thru|until)\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)
RANGE_LETTERS = re.compile(r"(?<![A-Za-z])(Mo|Tu|We|Th|Fr|Sa|Su|M|W|F)\.?\s*(?:-|–|—|thru|through|to)\s*(Mo|Tu|We|Th|Fr|Sa|Su|M|W|F)\.?(?![A-Za-z])")
ABBR = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
LETTER_D = {"Su": 0, "M": 1, "Mo": 1, "Tu": 2, "W": 3, "We": 3, "Th": 4, "F": 5, "Fr": 5, "Sa": 6}


def _span(a: int, b: int) -> set[int]:
    out, i = set(), a
    while True:
        out.add(i)
        if i == b:
            return out
        i = (i + 1) % 7


def clause_of(raw: str, time: str | None) -> str:
    """As lib/find.ts clauseOf(): the ;- or |-separated part of a line that holds the row's time."""
    parts = re.split(r"[;|]", raw or "")
    if len(parts) < 2 or not time:
        return raw or ""
    h, mm = int(time[:2]), time[3:5]
    h12 = (h + 11) % 12 + 1
    rx = rf"(?<![\d:]){h12}(?::|\.){mm}(?!\d)" + (rf"|(?<![\d:]){h12}\s*[ap]\.?\s?m" if mm == "00" else "")
    return next((x for x in parts if re.search(rx, x, re.I)), raw)


def daily_days(kind: str | None, raw: str | None, time: str | None) -> set[int]:
    """As lib/find.ts dailyDays(): "Daily" is the days its own line gives ("Daily: M-F"), else Monday to Saturday (Monday
    to Friday when the line gives Saturday its own time); daily adoration is every day."""
    if kind == "adoration":
        return set(range(7))
    c = clause_of(raw or "", time)
    m = RANGE_WORDS.search(c)
    if m:
        return _span(ABBR[m.group(1).lower()[:3]], ABBR[m.group(2).lower()[:3]])
    m = RANGE_LETTERS.search(c)
    if m:
        return _span(LETTER_D[m.group(1)], LETTER_D[m.group(2)])
    named = {ABBR[x.lower()] for x in re.findall(r"\b(sun|mon|tue|wed|thu|fri|sat)(?:day|s|nesday|sday|rsday|urday)?s?\b", c, re.I)}
    if named:
        return named
    return set(range(1, 6)) if re.search(r"\bsat(?:urday)?s?\b|s[aá]bados?", raw or "", re.I) else set(range(1, 7))


def day_set(day: str | None, time: str | None = None, raw: str | None = None, kind: str | None = None) -> set[int] | None:
    """The weekdays a row starts on (0 = Sunday), as lib/find.ts rowRule() reads it; None for monthly, seasonal,
    holy-day or unreadable days (those go to the 'other' bucket). An overnight row written over two days
    ("Friday-Saturday 7 PM-7 AM") starts on the first."""
    if not day:
        return None
    s = fold(re.sub(r"[\u2013\u2014]", "-", day))
    if re.search(r"of the month|each month|\bholy\b|\blent\b|advent|easter|summer|winter|daylight|christmas|\d", s):
        return None
    s = re.sub(r"\([^)]*\)", " ", s).strip()
    out = set()
    if re.match(r"(daily|every day)\b", s):
        out = daily_days(kind, raw, time)
    elif re.match(r"weekdays?\b", s):
        out = set(range(1, 6))
    elif re.match(r"(saturday )?vigil\b", s):
        out = {6}
    else:
        for m in DAY_RX.finditer(s):
            out |= _days(m)
    if len(out) == 2 and is_overnight(time):
        a, b = sorted(out)
        first = a if (a + 1) % 7 == b else b if (b + 1) % 7 == a else None
        if first is not None:
            out = {first}
    return out or None


def bucket_of_day(d: int, time: str | None) -> str:
    if d == 6:
        return "vigil" if (time or "")[:5] >= VIGIL_FROM else "saturday"
    return DAY_BUCKET[d]


def days_text(ds: list[int]) -> str:
    ds = sorted(set(ds))
    if len(ds) >= 3 and ds == list(range(ds[0], ds[-1] + 1)):
        return f"{WEEKDAY[ds[0]]}\u2013{WEEKDAY[ds[-1]]}"
    return ", ".join(WEEKDAY[d] for d in ds)


def split_by_bucket(r: dict, oid: int) -> list[dict]:
    """A row whose days fall in several buckets ("Monday-Friday 8:00 AM": five) becomes one part per bucket, so each
    is weighed apart; `_orig` and `_days` let rejoin() put the surviving parts back together for display."""
    ds = day_set(r.get("day"), r.get("time"), r.get("raw"), r.get("kind"))
    if not ds:
        return [dict(r, bucket="other", _orig=oid, _days=None, _parts=1)]
    by = defaultdict(list)
    for d in ds:
        by[bucket_of_day(d, r.get("time"))].append(d)
    if len(by) == 1:
        b, xs = next(iter(by.items()))
        return [dict(r, bucket=b, _orig=oid, _days=sorted(xs), _parts=1)]
    return [dict(r, day=days_text(xs), day_parsed=r.get("day_parsed") or r.get("day"), bucket=b, _orig=oid, _days=sorted(xs),
                 _parts=len(by), _day_whole=r.get("day")) for b, xs in by.items()]


def rejoin(rows: list[dict]) -> list[dict]:
    """Parts of one source row with the same outcome (`hidden`) back into one row: the original day when every part
    is there, else the surviving days ("Monday-Friday" with Monday hidden -> "Tuesday-Friday"). Order kept."""
    groups, order = defaultdict(list), []
    for r in rows:
        k = (r.get("_orig"), r.get("hidden"))
        if k not in groups:
            order.append(k)
        groups[k].append(r)
    out = []
    for k in order:
        ps = groups[k]
        if k[0] is None or len(ps) == 1 and ps[0].get("_parts", 1) == 1:
            out += ps
            continue
        x = dict(ps[0])
        if len(ps) == ps[0].get("_parts", 1):
            x["day"] = ps[0].get("_day_whole") or ps[0].get("day")
            if x.get("day_parsed") == x["day"]:
                x.pop("day_parsed", None)
        else:
            x["day"] = days_text([d for p in ps for d in (p.get("_days") or [])])
        x["bucket"] = ",".join(p["bucket"] for p in ps)
        out.append(x)
    return out


def day_pairs(rows: list[dict]) -> set[tuple[int, str]]:
    out = set()
    for r in rows:
        for d in r.get("_days") or day_set(r.get("day"), r.get("time"), r.get("raw"), r.get("kind")) or []:
            out.add((d, (r.get("time") or "")[:5]))
    return out


def choose_rows(rows: list[dict]) -> list[dict]:
    """Marks every row with `hidden` (None when shown): needs_review, lower_precedence or duplicate, per (kind, day
    bucket). Returns the rows (multi-bucket rows split). Rows of a bucket where the winning source lists fewer
    times than DiscoverMass does carry the bucket in `fewer_than_listed` on the first shown row (see kind_source)."""
    rows = [x for i, r in enumerate(rows) for x in split_by_bucket(r, i)]
    for r in rows:
        r["hidden"] = "needs_review" if r.get("needs_review") or r.get("flag") else None
    clash = [not r["hidden"] and auto_conflict(r, rows) for r in rows]   # all judged on the same state, as the client does
    for r, c in zip(rows, clash):
        if c:
            r["hidden"], r["review_reason"] = "needs_review", "auto_reading_conflicts"
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[display_kind(r)].append(r)
    any_curated = any(tier(r["source"]) == 0 for r in rows)
    for k, rs in by_kind.items():
        if k == "other":
            if any_curated:
                for r in rs:
                    if not r["hidden"] and tier(r["source"]) != 0:
                        r["hidden"] = "lower_precedence"
            continue
        # a source read by a person (tiers 0-2) speaks for its whole group (lib/find.ts BUCKET_GROUP): weekday Masses
        # listed on some days mean none on the others, and a weekend list covers both the vigil and Sunday
        group_best = {}
        for r in rs:
            if not r["hidden"]:
                g = BUCKET_GROUP[r["bucket"]]
                group_best[g] = min(group_best.get(g, 9), tier(r["source"]))
        for b in BUCKETS:
            live = [r for r in rs if not r["hidden"] and r["bucket"] == b]
            if not live:
                continue
            best = min(tier(r["source"]) for r in live)
            if group_best.get(BUCKET_GROUP[b], 9) <= 2:
                best = group_best[BUCKET_GROUP[b]]
            for r in live:
                if tier(r["source"]) > best:
                    r["hidden"] = "lower_precedence"
        # fold identical rows: same kind, day, start, church; keep the one that states a language
        shown = [r for r in rs if not r["hidden"]]
        groups = defaultdict(list)
        for r in shown:  # parts carry their own days, so "Monday-Friday 8:00" and "Friday 8:00 (Spanish)" meet on Friday
            groups[(tuple(r.get("_days") or ()) or r.get("day"), (r.get("time") or "")[:5], site_bracket(r.get("raw")))].append(r)
        for xs in groups.values():
            if len(xs) < 2:
                continue
            langs = {x.get("language") for x in xs if x.get("language")}
            keep = []
            for x in sorted(xs, key=lambda x: (x.get("language") is None, tier(x["source"]))):
                lang = x.get("language")
                if lang is None and langs and stated_english(x.get("raw")):
                    keep.append(x)   # its own line says English: a second Mass at that time (lib/find.ts statedEnglish)
                elif lang is None and langs:
                    x["hidden"] = "duplicate"
                elif any((y.get("language") == lang) for y in keep):
                    x["hidden"] = "duplicate"
                else:
                    keep.append(x)
    return rows


def _start(t: str | None) -> str:
    return (t or "")[:5]


def auto_conflict(x: dict, rows: list[dict]) -> bool:
    """As lib/find.ts autoConflict(): an automatic reading (tier 3 trusted template, 5 directory, 6 parish site) is left out when a better source lists
    something else at the same day and time, and when three or more of its times on one day are exactly a better
    source's times on another day while that source lists nothing on this one (a Sunday list read under a Saturday
    heading). Rows already held (needs_review) do not count as a better source."""
    k, tx = display_kind(x), tier(x.get("source"))
    if tx not in AUTO_TIERS or k == "other" or not x.get("_days"):
        return False
    t = _start(x.get("time"))
    better = [y for y in rows if tier(y.get("source")) < tx and y.get("hidden") != "needs_review"]
    # a better row's weekdays: its bucket's, else the days its text names ("First Saturday of the month" -> Saturday)
    if any(_start(y.get("time")) == t and display_kind(y) != k and set(y.get("_days") or _row_day(y.get("day")) or ()) & set(x["_days"])
           for y in better):
        return True
    if k != "mass":
        return False
    mine = {_start(y.get("time")) for y in rows if tier(y.get("source")) == tx and display_kind(y) == "mass" and y["bucket"] == x["bucket"]
            and y.get("hidden") != "needs_review"}
    if len(mine) < 3 or any(display_kind(y) == "mass" and y["bucket"] == x["bucket"] for y in better):
        return False
    by = defaultdict(set)
    for y in better:
        if display_kind(y) == "mass" and y["bucket"] != "other":
            by[y["bucket"]].add(_start(y.get("time")))
    return any(b != x["bucket"] and mine <= ts for b, ts in by.items())


def stated_english(raw: str | None) -> bool:
    """As lib/find.ts statedEnglish(): the line says English (or Inglés) and no other language."""
    t = re.sub(r"^\[[^\]]+\]\s*", "", raw or "")
    return bool(re.search(r"\b(english|ingl[eé]s)\b", t, re.I)) and not re.search(
        r"\b(spanish|espa[nñ]ol|vietnamese|vi[eệ]t|tagalog|filipino|korean|chinese|cantonese|mandarin|portuguese|latin|polish|italian|"
        r"french|tongan|bilingual)\b", t, re.I)


def fewer_than_listed(rows: list[dict], k: str) -> list[dict]:
    """Buckets of kind k where the source shown lists fewer times than DiscoverMass does (both lists kept apart), as
    one line: [{bucket: "sunday,mon", on: "Sundays and Mondays", shown, listed, url}] or []."""
    hit, shown_n, listed_n, url = [], 0, 0, None
    for b in BUCKETS[:-1]:
        shown = [r for r in rows if display_kind(r) == k and r["bucket"] == b and not r["hidden"]]
        dm = [r for r in rows if display_kind(r) == k and r["bucket"] == b and tier(r["source"]) == T_DM and r["hidden"] in (None, "lower_precedence")]
        if not shown or not dm or min(tier(r["source"]) for r in shown) >= T_DM:
            continue
        own_n, dm_n = len(day_pairs(shown)), len(day_pairs(dm))
        if dm_n > own_n:
            hit.append(b)
            shown_n, listed_n = shown_n + own_n, listed_n + dm_n
            url = url or next((r.get("source_url") for r in dm if r.get("source_url")), None)
    return [{"bucket": ",".join(hit), "on": buckets_text(hit, k), "shown": shown_n, "listed": listed_n, "url": url}] if hit else []


def kinds_of(rows: list[dict]) -> dict:
    """Per kind: the main source (best tier shown), any other source shown for other day buckets, and the buckets
    where other listings show more times. `buckets` is one phrase list ("the Saturday vigil", "Sundays", "weekdays")."""
    out = {}
    shown = [x for x in rows if not x["hidden"]]
    for k in ("mass", "confession", "adoration"):
        ks = [x for x in shown if display_kind(x) == k]
        if not ks:
            continue
        tiers = sorted({tier(x["source"]) for x in ks})
        main = kind_source([x for x in ks if tier(x["source"]) == tiers[0]])
        main["buckets"] = [buckets_text([x["bucket"] for x in ks if tier(x["source"]) == tiers[0]], k)]
        main["also"] = []
        for t in tiers[1:]:
            sub = [x for x in ks if tier(x["source"]) == t]
            ks_ = kind_source(sub)
            ks_["buckets"] = [buckets_text([x["bucket"] for x in sub], k)]
            main["also"].append(ks_)
        main["fewer"] = fewer_than_listed(rows, k)
        out[k] = main
    return out


MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def nice_date(iso: str | None) -> str | None:
    if not iso or not re.match(r"\d{4}-\d\d-\d\d", iso):
        return None
    y, m, d = iso[:10].split("-")
    return f"{MON[int(m) - 1]} {int(d)}, {y}"


def host_of(u: str | None) -> str | None:
    h = urlparse(u or "").hostname or ""
    return re.sub(r"^www\.", "", h) or None


def kind_source(rows: list[dict]) -> dict | None:
    """Where the shown rows of one kind came from, said plainly, with a link and the date read."""
    if not rows:
        return None
    t = min(tier(r["source"]) for r in rows)
    rs = [r for r in rows if tier(r["source"]) == t]
    src = rs[0]["source"] or ""
    url = next((r.get("source_url") for r in rs if r.get("source_url")), None)
    m = re.search(r"(\d{4}-\d\d-\d\d)", src)
    dt = max((r.get("fetched_at") or "")[:10] for r in rs) or (m.group(1) if m else None)
    if t == 0:
        text, sure = "Checked by hand against the parish's own pages", "checked"
    elif t == 1:
        text, sure = "From the parish bulletin", "unverified"
    elif t == 2:
        text, sure = "From an archived copy of the parish website (Internet Archive)", "unverified"
    elif t == T_TRUSTED:
        text, sure = "From the parish website, read by a checked template", "unverified"
    elif t == T_DM:
        text, sure = "From DiscoverMass.com (MassTimes.org listing), which has no last-updated date", "secondary"
    elif t == T_DIR:
        text, sure = "From the diocese's parish directory (read automatically, unverified)", "auto"
    else:
        text, sure = "From the parish website (read automatically, unverified)", "auto"
    return {"tier": TIER_TEXT[t], "text": text, "url": url, "date": dt, "date_text": nice_date(dt), "sure": sure}


def public_row(r: dict) -> dict:
    return {"day": r.get("day"), "time": r.get("time"), "language": r.get("language"), "kind": r.get("kind"),
            "raw": (r.get("raw") or "")[:140] or None, "source": r.get("source"), "source_url": r.get("source_url")}


# ------------------------------------------------------------------------------------------------ events
# A port of lib/parish-events.ts keep() / typeOf() / languageOf(): the same public-calendar rules as the Oakland pages.
DROP_CATEGORIES = {"notice", "meetings_governance", "liturgical_ministries", "school"}
BOOKING = re.compile(r"\b(meet|closed|closure|practice|rehearsal|meeting|mtg|set ?-?up|reception|reserved|hold|office|no class(es)?|no school|prep|cleaning|gym|polling|staff|choir|coro|rental|storage|kitchen|parking|room|hall|lunch|counting|count team|training for ushers|ushers|lectors?|sacristans?|altar servers?|eucharistic ministers?|music|band|bingo set|wedding|funeral|memorial for|baptism\b(?! (class|prep|seminar))|quincea|private)\b", re.I)
CELEBRANT = re.compile(r"(?::|\s)\s*(?:fr\.?\s+|guest\s+)?[A-Z]{2,3}\s*$")
ROUTINE = re.compile(r"^(?:\d{1,2}(?::\d\d)?\s*(?:am|pm)?\s*)?(?:daily|sunday|saturday|weekday|weekend|morning|evening|english|spanish|vigil)?\s*(?:holy\s+)?(?:mass(?:es)?|misa|confessions?|reconciliation|sacrament of (?:confession|reconciliation)!?)\b", re.I)
SPECIAL = re.compile(r"advent|lent|penance service|communal|simbang|feast|fiesta|solemnity|all souls|all saints|d[ií]a de|immaculate|guadalupe|christmas|navidad|healing|anointing|novena|blessing|requiem|thanksgiving|memorial mass|bishop|confirmation|first communion|procession|posadas|epiphany|candlelight|taize|taizé|lessons and carols|carols|sensory|vocation", re.I)
FACILITY_KEEP = re.compile(r"simbang|posadas|novena|feast|fiesta|festival|guadalupe|retreat|procession|penance service|reconciliation service|christmas|concert|pilgrimage|healing mass|anointing|d[ií]a de|all souls|lessons and carols", re.I)


def keep_event(title: str, category: str | None, tags: list[str] | None) -> bool:
    t = (title or "").strip()
    if re.search(r"-\s?[A-Z]{2,5}(?:/[A-Z]{2,5})*\s*$|^[A-Z]{3,6}-", t) or re.search(r"\b(shower|classrooms?|cl'?rooms?|rm|birthday)\b", t, re.I):
        return False  # room-booking codes ("Blanket Drive-MBW", "STRE-Classrooms") and private bookings
    if len(t) < 4 or re.search(r"_|\bq\d\b", t, re.I) or re.fullmatch(r"(english|espa[nñ]ol|spanish|vietnamese|mass|misa)", t, re.I):
        return False
    if not re.search(r"[a-z]", t) and not SPECIAL.search(t):
        return False
    if (category or "") in DROP_CATEGORIES or BOOKING.search(t) or CELEBRANT.search(t):
        return False
    if re.search(r"\bmass:\s*[A-Z]{2,3}\b", t) or re.search(r"\b[A-Z]{2,3} [A-Z]{2,3}\b", t):
        return False
    if re.match(r"no\s", t, re.I) or re.match(r"\d+(st|nd|rd|th) (sunday|week)\b", t, re.I):
        return False
    if "facility_calendar" in (tags or []) and not FACILITY_KEEP.search(t):
        return False
    if category in ("liturgy", "reconciliation") and ROUTINE.search(t) and not SPECIAL.search(t):
        return False
    return True


def event_type(category: str | None, title: str) -> str:
    c = category or ""
    if c == "reconciliation" or re.search(r"penance|reconciliation service", title, re.I):
        return "confession"
    if c in ("liturgy", "devotion_prayer"):
        return "liturgy"
    if c in ("formation", "sacramental_preparation"):
        return "formation"
    if c == "youth_young_adults":
        return "youth"
    if c == "charity_service":
        return "charity"
    return "formation" if re.search(r"retreat|bible|study|class|ocia|rcia|formation|talk|speaker", title, re.I) else "community"


def diocese_event_type(cats: list[str], title: str) -> str:
    c = " ".join(cats or []) + " " + title
    if re.search(r"catholic charities|immigration|charity|service|svdp|st\. vincent", c, re.I):
        return "charity"
    if re.search(r"youth|young adult|campus|teen", c, re.I):
        return "youth"
    if re.search(r"penance|reconciliation|confession", c, re.I):
        return "confession"
    if re.search(r"mass|liturg|prayer|adoration|rosary|holy hour|vespers|recollection", c, re.I):
        return "liturgy"
    if re.search(r"formation|retreat|training|marriage|catechesis|ocia|study|workshop|conference|class", c, re.I):
        return "formation"
    return "community"


def language_of(title: str) -> str | None:
    if re.search(r"vi[eệ]t|th[aá]nh l[eễ]|\blễ\b", title, re.I):
        return "Vietnamese"
    if re.search(r"simbang gabi|tagalog|filipino", title, re.I):
        return "Filipino"
    if re.search(r"\b(misa|espa[nñ]ol|span\.|spanish|en espa|grupo|hombres|mujeres|oraci[oó]n|j[oó]venes|retiro|posadas|virgen|santo|rosario|catequesis|bautizo|pl[aá]ticas)\b", title, re.I):
        return "Spanish"
    return None


def wall(start: str, all_day: bool) -> tuple[str, str | None]:
    d = start[:10]
    hhmm = start[11:16] if not all_day and len(start) > 10 else None
    return d, (hhmm if hhmm and hhmm != "00:00" else None)


def fold_events(evs: list[dict]) -> tuple[list[dict], list[dict]]:
    """evs: {pid, date, time, title, type, lang, url, end}. Weekly series (same parish, title, weekday and time three or
    more times in the file) become one line each; the rest stay dated."""
    norm = lambda t: re.sub(r"\s+", " ", t.lower()).strip()  # noqa: E731
    key = lambda e: (e["pid"], norm(e["title"]), date.fromisoformat(e["date"]).weekday(), e["time"])  # noqa: E731
    cnt = Counter(key(e) for e in evs)
    one, series, seen = [], {}, set()
    for e in sorted(evs, key=lambda e: (e["date"], e["time"] or "")):
        dk = (e["pid"], norm(e["title"]), e["date"], e["time"])
        if dk in seen:
            continue
        seen.add(dk)
        k = key(e)
        if cnt[k] >= 3:
            s = series.setdefault(k, dict(e, n=0, wd=(date.fromisoformat(e["date"]).weekday() + 1) % 7))
            s["n"] += 1
        else:
            one.append(e)
    return one, list(series.values())


# ------------------------------------------------------------------------------------------------ social links
# Website-builder and theme defaults left in a template, platform accounts, and accounts that are never one parish's
# own (the Pope's, news, aggregators, publishers). Handles compared folded to letters and digits.
SOCIAL_DENY = {"wix", "wixcom", "squarespace", "weebly", "godaddy", "wordpress", "wordpresscom", "teepublic", "elementor", "divi",
               "elegantthemes", "themeforest", "envato", "webflow", "duda", "jimdo", "strikingly", "ecatholic", "ecatholicofficial",
               "diocesanpub", "diocesanpublications", "discovermass", "masstimes", "masstimesus", "masstimesorg", "catholicmasstime",
               "pontifex", "vaticannews", "vatican", "usccb", "ewtn", "catholicnewsagency", "formed", "formedorg", "jspaluch",
               "lpiparishes", "oursundayvisitor", "osvnews", "flocknote", "facebook", "instagram", "twitter", "youtube", "google",
               "tiktok", "share", "sharer", "home", "pages", "groups", "p", "pg", "watch", "user", "channel", "c", "x"}
SOCIAL_DENY_HOSTS = re.compile(r"masstime\.us|masstimes\.org|discovermass\.com|catholicmasstime|ecatholic\.com|wix\.com|squarespace\.com",
                               re.I)
# words that say nothing about which parish: never enough on their own for a handle to "relate" to the parish
NOT_DISTINCT = {"chaldean", "catholic", "maronite", "melkite", "byzantine", "ukrainian", "syro", "malabar", "armenian", "coptic",
                "syriac", "romanian", "ruthenian", "russian", "greek", "eastern", "rite", "community", "parish", "church", "mission",
                "center", "centre", "cathedral", "shrine", "basilica", "chapel", "newman", "campus", "ministry", "ministries",
                "spanish", "english", "vietnamese", "filipino", "korean", "chinese", "school", "academy", "the", "and", "for"}
# Sites that are an eparchy's or a diocese's though parishes list them as their website (the Chaldean eparchy's
# stpeterdiocese.org for its parishes, a diocese's Wix site, Stockton's older domain).
BODY_HOSTS = DIOCESAN_HOSTS | {"stpeterdiocese.org", "sbdiocese.wixsite.com", "dioceseofstockton.org", "archdiocese.la", "byzantinecatholic.org"}
SCHOOL_WORD = re.compile(r"school|academy|elementary|highschool|\bprep\b|preparatory|preschool|montessori", re.I)
OTHER_BODY = re.compile(r"diocese|archdiocese|eparchy|dioceseof|catholiccharities|ccharities|knights|kofc|council\d|cemetery|mortuary", re.I)


def social_handle(plat: str, url: str) -> str | None:
    """The account a social link names (facebook.com/<h>, instagram.com/<h>, x.com/<h>, youtube.com/@h | c/h | user/h,
    tiktok.com/@h, <h>.flocknote.com); None when the link names no readable account (a YouTube channel id, a page id)."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    segs = [x for x in p.path.split("/") if x]
    if plat == "flocknote":
        sub = host.split(".")[0]
        return sub if sub not in ("www", "flocknote") else (segs[0] if segs else None)
    if not segs:
        return None
    if plat == "youtube":
        if segs[0].startswith("@"):
            return segs[0][1:]
        if segs[0] in ("c", "user") and len(segs) > 1:
            return segs[1]
        return None                                   # /channel/UC...: an opaque id
    if plat == "facebook" and segs[0] in ("pages", "people") and len(segs) > 1:
        return segs[1]
    if plat == "facebook" and (segs[0] == "profile.php" or re.fullmatch(r"\d+", segs[0])):
        return None
    return segs[0].lstrip("@")


def _initials(name: str) -> str:
    ws = [w for w in re.sub(r"[^a-z ]+", " ", fold(name).replace("'", "")).split() if w not in ("the", "of", "de", "la", "del", "and", "y")]
    return "".join(w[0] for w in ws)


def social_is_own(plat: str, url: str, r: dict) -> bool:
    """A social link is the parish's own when it is not a template default or a borrowed account (SOCIAL_DENY), not a
    school's or another body's (unless the parish's own name says so), and its handle relates to the parish: it holds
    a distinctive word of the parish's name, its website's name, or the initials of its name ("sjbcostamesa" for St.
    John the Baptist, Costa Mesa). A link whose account cannot be read (a channel id) or relate is dropped: when unsure,
    leave it out. Livestream links are kept unless a builder default or an aggregator."""
    if SOCIAL_DENY_HOSTS.search(url or ""):
        return False
    h = social_handle(plat, url) if plat != "livestream" else "live"
    if h is None:
        return False
    hk = re.sub(r"[^a-z0-9]", "", fold(h))
    if hk in SOCIAL_DENY or len(hk) < 3:
        return False
    if plat == "livestream":
        return True
    body = lambda d: any(d == h or d.endswith("." + h) or d.startswith(h.split(".")[0] + ".") for h in BODY_HOSTS)  # noqa: E731
    names = [r.get("name") or ""] + [n for s in r.get("sites") or [] if not body(domain(s.get("domain")) or "") for n in (s.get("names") or [])]
    joined = " ".join(names)
    if SCHOOL_WORD.search(hk) and not SCHOOL_WORD.search(joined):
        return False
    if OTHER_BODY.search(hk) and not OTHER_BODY.search(fold(joined).replace(" ", "")):
        return False
    labels = set()
    for u in [r.get("website")] + [s.get("domain") for s in r.get("sites") or []]:
        d = domain(u) or ""
        if body(d):
            continue   # an eparchy's or diocese's site names the eparchy, not this parish: its name proves nothing
        lab = d.split("/")[-1] if "/" in d else d.split(".")[0] if d.count(".") == 1 else (d.split(".")[-3] if d.count(".") >= 2 else d)
        lab = re.sub(r"[^a-z0-9]", "", lab.lower())
        if len(lab) >= 4 and lab not in ("sites", "google"):
            labels.add(lab)
    if any(lab in hk or hk in lab or (len(lab) >= 4 and hk.startswith(lab)) for lab in labels):
        return True
    toks = {t for n in names + [r.get("city") or ""] for t in tokens(n) if len(t) >= 4 and t not in NOT_DISTINCT}
    if any(t in hk for t in toks):
        return True
    # a long run in common with the website's or the parish's name ("hachurchcolma" ~ hacolma.org, "transfigsanjose" ~
    # sanjosetransfig.org), unless the run is only a word like "parish", "mission" or "chaldean"
    keys = labels | {re.sub(r"[^a-z0-9]", "", fold(n)) for n in names}
    for k in keys:
        m = SequenceMatcher(None, hk, k, autojunk=False).find_longest_match(0, len(hk), 0, len(k))
        run = hk[m.a:m.a + m.size]
        if m.size >= 5 and not any(run in w or w in run and len(run) - len(w) < 3 for w in NOT_DISTINCT | {"saint", "church", "parish"}):
            return True
    inis = {_initials(n) for n in names} | {_initials(re.sub(r"^(st|saint)\.?\s+", "", n, flags=re.I)) for n in names}
    if any(len(i) >= 3 and hk.startswith(i) for i in inis):
        return True
    return any(len(i) == 2 and re.fullmatch(rf"{i}(?:parish|church|catholic|cc)", hk) for i in inis)   # "miparish", Mary Immaculate


def school_site(s: dict, r: dict) -> bool:
    """The parish's listed website is its school's (sanmiguelcatholicschool.com): its social links and bulletin are the
    school's, not the parish's."""
    return bool(SCHOOL_WORD.search(s.get("domain") or "")) and not SCHOOL_WORD.search(r.get("name") or "")


# ------------------------------------------------------------------------------------------------ inputs
_DM = None


def dm_all() -> tuple[list[dict], dict]:
    global _DM
    if _DM is None:
        d = load_json(DM_FILE, {}) or {}
        _DM = ([r for r in d.get("records", []) if not r.get("unavailable") and not r.get("excluded") and r.get("id")],
               {"built_at": d.get("built_at"), "caveat": d.get("caveat")})
    return _DM


def sites_for(slug_: str) -> list[dict]:
    out = []
    for f in sorted((REAL / "sites" / slug_).glob("*.json")):
        if f.name == "summary.json":
            continue
        d = load_json(f)
        if d:
            d["_file"] = str(f.relative_to(ROOT))
            out.append(d)
    return out


TRUST_STATS = Counter()


def _sunday_set(rows: list[dict]) -> set[str]:
    return {(x.get("time") or "")[:5] for x in rows if x.get("kind") == "mass" and (x.get("day") or "").strip() == "Sunday"
            and not x.get("needs_review") and not x.get("flags") and re.match(r"\d\d:\d\d", x.get("time") or "")}


def sunday_agrees(site_rows: list[dict], dm: dict | None) -> bool:
    """The last condition for a trusted template parse (ingest/california/sites/templates trusted()): its Sunday Mass
    start times equal DiscoverMass's, or overlap by Jaccard >= 0.8 when DiscoverMass lists 5 or more. Where DiscoverMass
    lists no Sunday Mass there is nothing to disagree with. The site parse must itself list a Sunday Mass."""
    a = _sunday_set(site_rows)
    if not a:
        return False
    b = _sunday_set((dm or {}).get("schedule") or [])
    if not b:
        return True
    return a == b or (len(b) >= 5 and len(a & b) / len(a | b) >= 0.8)


def rows_for_church(rows: list[dict], church: str | None) -> list[dict]:
    """A multi-church site's rows for one church (templates.rows_for_church): rows tagged "[<other church>]" are left out."""
    try:
        sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
        import templates as _T  # noqa: E402
    except Exception:  # noqa: BLE001 - without the package every row stays
        return rows
    return _T.rows_for_church(rows, church)


def bulletins_for(slug_: str) -> tuple[dict, dict]:
    """The bulletin pass (data/real/california/bulletins/<slug>.json, ingest/california/bulletins/): its parishes indexed
    by site file (robust to merged-id changes) and by parish id (old ids followed through id_redirects.json)."""
    d = load_json(REAL / "bulletins" / f"{slug_}.json", {}) or {}
    moved = ((load_json(REAL / "id_redirects.json", {}) or {}).get("dioceses") or {}).get(slug_, {})
    by_file, by_pid = defaultdict(list), {}
    for p in d.get("parishes", []):
        pid = moved.get(p.get("parish_id"), p.get("parish_id"))
        p = dict(p, parish_id=pid)
        if pid:
            by_pid[pid] = p
        for ln in p.get("links") or []:
            if ln.get("site_file"):
                by_file[ln["site_file"]].append(p)
    return by_file, by_pid


def wayback_for(slug_: str) -> dict:
    """Archived copies of the parish websites the crawl could not read (data/real/california/wayback/<slug>.json), by
    domain. Their groups, bulletin page and social links are used; their schedule rows are all needs_review and are not."""
    d = load_json(REAL / "wayback" / f"{slug_}.json", {}) or {}
    return {s["domain"]: s for s in d.get("sites", []) if s.get("domain")}


def wayback_site_records(sites: list[dict], wb_by_dom: dict) -> int:
    """Site files recovered by fold-crawlt's `wayback` job (crawl_status 'wayback-<capture date>', crawl_env 'wayback',
    copied over a challenged/dead read) are archived copies, not live reads: their groups, social and bulletin link are
    moved into the wayback record shape (so the 540-day capture rule and the 'archived copy' labels apply), and their
    schedule rows are dropped here (every one is needs_review; a Wayback schedule is shown only after a person reads it,
    via the hand-checked file). The site record stays listed with its non-ok status. Returns how many were adapted."""
    n = 0
    for s in sites:
        st = str(s.get("crawl_status") or "")
        if not st.startswith("wayback") or not s.get("domain"):
            continue
        w = s.get("wayback") or {}
        cap0 = w.get("capture_date") or st[len("wayback-"):][:10] or None
        caps = w.get("captures") or []
        cap_of = {c.get("original"): c for c in caps if c.get("original")}
        groups = []
        for g in s.get("groups") or []:
            c = cap_of.get(g.get("source_url")) or {}
            groups.append({**g, "fetched_at": c.get("capture") or c.get("capture_date") or cap0})
        b = s.get("bulletin") or None
        bul = None
        if b and b.get("url"):
            bc = cap_of.get(b.get("source_url")) or {}
            bul = {"page_url": b.get("source_url"), "page_archive_url": bc.get("archive_url"), "best_link": b}
        rec = {"domain": s["domain"], "groups": groups, "social": s.get("social") or {}, "bulletin": bul,
               "wayback": {"captures": [{"capture_date": c.get("capture") or c.get("capture_date"),
                                         "roles": [c.get("kind")] + (["bulletin"] if bul and c.get("original") == bul["page_url"] else []),
                                         "archive_url": c.get("archive_url")} for c in caps],
                           "capture_age_days": w.get("capture_age_days")}}
        if bul and not any("bulletin" in c["roles"] for c in rec["wayback"]["captures"]):
            rec["wayback"]["captures"].append({"capture_date": cap0, "roles": ["bulletin"], "archive_url": None})
        old = wb_by_dom.get(s["domain"])
        old_cap = max([c.get("capture_date") or "" for c in ((old or {}).get("wayback") or {}).get("captures", [])] or [""])
        if not old or (cap0 or "") >= old_cap:
            wb_by_dom[s["domain"]] = rec
        s["wayback_rows_held"] = len(s.get("schedule_rows") or [])
        s["groups"], s["social"], s["bulletin"], s["schedule_rows"] = [], {}, None, []
        n += 1
    return n


GROUP_STOP = re.compile(r"\b(ministry|ministries|group|groups|society|the|of|st|saint|parish)\b")
BULLETIN_MAX_AGE = 180      # days: a group last seen in an older issue is not shown (the bulletin README's integration note)
WAYBACK_MAX_AGE = 540       # days: groups from an archived page captured longer ago than this are not shown


def group_key(nm: str | None) -> str:
    k = GROUP_STOP.sub(" ", fold(group_name(nm) or ""))
    return re.sub(r"[^a-z]", "", k)


def next_text(nm: dict | None) -> str | None:
    """"Next: Nov 12, 2026, 7:00 PM" from a bulletin's dated next meeting, if it is still ahead."""
    if not nm or not nm.get("date") or nm["date"] < TODAY.isoformat():
        return None
    t = nm.get("time")
    tt = ""
    if t and re.match(r"\d\d:\d\d", t):
        h, mi = int(t[:2]), t[3:5]
        tt = f", {(h % 12) or 12}:{mi} {'AM' if h < 12 else 'PM'}"
    return f"Next: {nice_date(nm['date'])}{tt}"


READ_OK = {"ok", "social_only"}
REFUSED = {"challenge", "blocked_403", "blocked_429", "robots_disallowed"}


# ------------------------------------------------------------------------------------------------ the directory's own schedule text
_parsers = None


def parse_schedule_text(lines: list[str], source: str, url: str | None, fetched: str | None) -> list[dict]:
    """The diocese directory's Mass-time lines (San Bernardino) through the Oakland parsers the site crawl also uses."""
    global _parsers
    if _parsers is None:
        try:
            import crawled_mass_schedule as cms_rows  # noqa: E402
            import parish_sites as ps  # noqa: E402
            _parsers = (ps, cms_rows)
        except Exception as e:  # noqa: BLE001
            print("schedule parsers unavailable:", e)
            _parsers = False
    if not _parsers or not lines:
        return []
    ps, cms_rows = _parsers
    blk = "\n".join(lines)
    try:
        parsed = ps.parse_mass(blk)
        rows = cms_rows.rows_from_record({"id": "x", "website": url, "mass_schedule": {"raw": blk, "parsed": parsed, "source_url": url}}, source)
    except Exception as e:  # noqa: BLE001
        print("directory schedule not parsed:", e)
        return []
    out = []
    for r in rows:
        bad = not re.match(r"^(0[5-9]|1\d|2[0-2]):", r["time"] or "") or not r["day"]
        out.append({"kind": r["kind"], "day": r["day"], "time": r["time"], "language": r["language"], "raw": r["raw"],
                    "source": source, "source_url": url, "fetched_at": fetched, "needs_review": bad,
                    "review_reason": "implausible_hour_or_no_day" if bad else None})
    return out


# ------------------------------------------------------------------------------------------------ holy-day blocks
SITES_RAW = ROOT / "data" / "raw" / "california" / "sites"
_psite, _site_lines = None, {}
HOLY_STATS = Counter()
SOCIAL_STATS = Counter()


def site_page_lines(domain_: str) -> dict[str, list[str]] | None:
    """The crawl's cached pages for one site (data/raw/california/sites/<domain>/, local only), url -> visible lines."""
    global _psite
    if domain_ in _site_lines:
        return _site_lines[domain_]
    out = None
    try:
        if _psite is None:
            sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
            import parse_site as _ps  # noqa: E402
            _psite = _ps
        d = SITES_RAW / domain_.replace("/", "__")
        mp = d / "manifest.json"
        if mp.exists():
            out = {}
            for pg in _psite.load_pages(d, json.loads(mp.read_text())):
                for u in {pg.url, getattr(pg, "req_url", None)}:
                    if u:
                        out[u] = pg.lines
    except Exception as e:  # noqa: BLE001
        print("site pages not read:", domain_, e)
        out = None
    _site_lines[domain_] = out
    return out


def holy_day_check(r: dict, domain_: str) -> dict:
    """A row read from under a "Holy Day(s)" heading is the holy-day schedule: kind other, day "Holy days"
    (kind_parsed / day_parsed keep the parser's reading). Rows whose page is not cached here are counted, not changed."""
    if r.get("kind") not in ("mass", "confession", "adoration") or not r.get("raw"):
        return r
    pages = site_page_lines(domain_)
    lines = (pages or {}).get(r.get("source_url") or "")
    if lines is None:
        HOLY_STATS["page_not_cached"] += 1
        r["holy_day_check"] = "page not cached"
        return r
    HOLY_STATS["checked"] += 1
    if _psite.in_holy_day_block(lines, r["raw"]):
        HOLY_STATS["holy_day_block"] += 1
        r.update(kind_parsed=r["kind"], day_parsed=r.get("day"), kind="other", day="Holy days", review_reason="holy_day_block")
    return r


# ------------------------------------------------------------------------------------------------ group names
NAMEISH = re.compile(r"^(.*?):\s*(?:\([^)]*\)\s*)?([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s*(?:/|&|\s)\s*(?:y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})\s*$")


def group_name(nm: str | None) -> str | None:
    """A group's name without a person's name after a colon ("Sacristan: <name>" -> "Sacristan"); None for
    password-protected pages. The crawl's privacy screen does not catch names in this position."""
    nm = (nm or "").strip()
    if not nm or re.match(r"protected:", nm, re.I):
        return None
    m = NAMEISH.match(nm)
    return m.group(1).strip() if m and m.group(1).strip() else nm


# ------------------------------------------------------------------------------------------------ kinds of place
def pin_kind(t: str | None, name: str = "") -> str:
    s = (t or "").lower()
    if s == "cathedral" or re.search(r"\bcathedral\b", name, re.I):
        return "cathedral"
    if s in ("shrine", "basilica"):
        return "shrine"
    if s == "mission":
        return "mission"
    if s in ("chapel", "campus_ministry", "campus ministry", "monastery", "oratory"):
        return "chapel"
    return "parish"


PLACE_TYPES = {"school": "School", "charity": "Charity", "cemetery": "Cemetery", "shrine": "Shrine", "retreat": "Retreat center",
               "health": "Health care", "newman": "Campus ministry", "seminary": "Seminary"}


def place_type(e: dict) -> str | None:
    t, sub = (e.get("type") or "").lower(), (e.get("subtype") or "").lower()
    if t == "school":
        return "school"
    if t == "catholic_charities":
        return "charity"
    if t == "cemetery":
        return "cemetery"
    if t == "retreat_center":
        return "retreat"
    if t == "campus_ministry":
        return "newman"
    if t == "seminary":
        return "seminary"
    if t == "shrine":
        return "shrine"
    if t == "other" and sub == "hospital":
        return "health"
    return None


# ------------------------------------------------------------------------------------------------ one diocese
def match_rows(base: list[dict], cands: list[dict], get) -> dict[int, tuple[int, str]]:
    """One-to-one matches base[i] -> (cands[j], method). get(x) -> (website, street, city, zip, name, lat, lng)."""
    B = [get(x) for x in base]
    C = [get(x) for x in cands]
    used_b, used_c, out = set(), set(), {}

    def take(i, j, how):
        out[i] = (j, how)
        used_b.add(i)
        used_c.add(j)

    # 1. website domain, only where the domain names one place on each side
    bd = Counter(domain(b[0]) for b in B if domain(b[0]))
    cd = Counter(domain(c[0]) for c in C if domain(c[0]))
    cidx = {domain(c[0]): j for j, c in enumerate(C) if domain(c[0]) and cd[domain(c[0])] == 1}
    for i, b in enumerate(B):
        d = domain(b[0])
        if d and bd[d] == 1 and d in cidx and cidx[d] not in used_c:
            take(i, cidx[d], "website domain")
    # 2. normalised street address (number + street name), same ZIP or city
    for i, b in enumerate(B):
        if i in used_b or not street_key(b[1]):
            continue
        hits = [j for j, c in enumerate(C) if j not in used_c and street_key(c[1]) == street_key(b[1])
                and ((b[3] and c[3] and b[3][:5] == c[3][:5]) or fold(b[2]) == fold(c[2]))]
        if len(hits) > 1:
            hits = [j for j in hits if tokens(b[4]) & tokens(C[j][4])] or hits
        if len(hits) == 1:
            take(i, hits[0], "street address")
    # 3. name + city
    for i, b in enumerate(B):
        if i in used_b:
            continue
        bt = tokens(b[4])
        hits = []
        for j, c in enumerate(C):
            if j in used_c or fold(b[2]) != fold(c[2]) or not bt:
                continue
            ct = tokens(c[4])
            if ct and (bt <= ct or ct <= bt or len(bt & ct) / len(bt | ct) >= 0.5):
                hits.append(j)
        if len(hits) == 1:
            take(i, hits[0], "name + city")
    # 4. within 120 m and a shared name word
    for i, b in enumerate(B):
        if i in used_b or b[5] is None:
            continue
        near = [(dist_m((b[5], b[6]), (c[5], c[6])), j) for j, c in enumerate(C)
                if j not in used_c and c[5] is not None and tokens(b[4]) & tokens(c[4])]
        near = [x for x in near if x[0] <= 120]
        if len(near) == 1:
            take(i, near[0][1], f"within {int(near[0][0])} m, name word agrees")
    return out


def dm_get(r):
    return (r.get("website"), r.get("street"), r.get("city"), r.get("zip"), r.get("name"), r.get("lat"), r.get("lng"))


def dir_get(r):
    return (r.get("website"), r.get("address"), r.get("city"), r.get("zip"), r.get("name"), r.get("lat"), r.get("lng"))


def build_diocese(slug_: str, geocode: bool = True) -> dict:
    dio = next(d for d in DIOCESES["dioceses"] if d["slug"] == slug_)
    ddir = REAL / "dioceses" / slug_
    directory = load_json(ddir / "parishes.json", []) or []
    structure = load_json(ddir / "structure.json", {}) or {}
    entities = load_json(ddir / "entities.json", []) or []
    dio_events = load_json(ddir / "events.json", []) or []
    dm_recs_all, dm_meta = dm_all()
    dm = [r for r in dm_recs_all if r.get("diocese") == slug_]
    sites = sites_for(slug_)
    bul_by_file, bul_by_pid = bulletins_for(slug_)
    wb_by_dom = wayback_for(slug_)
    wayback_site_records(sites, wb_by_dom)
    has_dir = bool(directory)

    # ---- the list: the directory, else DiscoverMass
    recs = []
    if has_dir:
        for p in directory:
            recs.append({"id": f"{slug_}--{p['id']}" if not p["id"].startswith(slug_ + "--") else p["id"], "dir": p, "dm": None,
                         "name": p["name"], "kind": pin_kind(p.get("type"), p["name"]), "type": p.get("type"),
                         "address": p.get("address"), "city": p.get("city"), "zip": p.get("zip"), "county": p.get("county"),
                         "lat": p.get("lat"), "lng": p.get("lng"),
                         "coords_source": p.get("geo_source") or ("diocese's directory" if p.get("lat") is not None else None),
                         "phone": p.get("phone"), "website": p.get("website"), "status": p.get("status"),
                         "status_note": p.get("status_note"), "rite": p.get("rite"), "languages": p.get("languages") or [],
                         "listed_by": "directory", "match": {}, "sites": []})
        m = match_rows(directory, dm, lambda x: dir_get(x) if "dm_slug" not in x else dm_get(x))
        for i, (j, how) in m.items():
            recs[i]["dm"] = dm[j]
            recs[i]["match"]["discovermass"] = {"id": dm[j]["id"], "url": dm[j]["source_url"], "match_method": how}
        dm_unmatched = [r for j, r in enumerate(dm) if j not in {v[0] for v in m.values()}]
    else:
        for r in dm:
            recs.append({"id": r["id"], "dir": None, "dm": r, "name": r["name"], "kind": pin_kind(r.get("type"), r["name"]),
                         "type": r.get("type"), "address": r.get("street"), "city": r.get("city"), "zip": r.get("zip"),
                         "county": r.get("county"), "lat": r.get("lat"), "lng": r.get("lng"),
                         "coords_source": r.get("latlng_source"), "phone": r.get("phone"), "website": r.get("website"),
                         "status": None, "status_note": "Status not collected yet: the diocese's own directory is not in hand.",
                         "rite": r.get("rite"), "languages": [], "listed_by": "discovermass",
                         "match": {"discovermass": {"id": r["id"], "url": r["source_url"], "match_method": "listing itself"}}, "sites": []})
        dm_unmatched = []
    for r in recs:
        d = r["dm"]
        if d:
            if r["lat"] is None and d.get("lat") is not None:
                r["lat"], r["lng"], r["coords_source"] = d["lat"], d["lng"], "DiscoverMass page (Directions link)"
            if not r["phone"] and d.get("phone"):
                r["phone"] = d["phone"]
            if not r["website"] and d.get("website") and not d.get("website_is_social") and not any(
                    h in (d["website"] or "") for h in DIOCESAN_HOSTS):
                r["website"] = d["website"]

    # ---- Los Angeles: the archdiocese's regional map (region and deanery cross-check, coordinates for the rest)
    if slug_ == "los-angeles" and has_dir:
        lmap = load_json(ddir / "map" / "parishes.json", {}) or {}
        lp = lmap.get("parishes", [])
        mm = match_rows([r["dir"] for r in recs], lp, lambda x: dir_get(x))
        for i, (j, how) in mm.items():
            q = lp[j]
            recs[i]["match"]["la_map"] = {"id": q["id"], "match_method": how, "region": q.get("region"), "deanery": q.get("deanery")}
            if recs[i]["lat"] is None and q.get("lat") is not None:
                recs[i]["lat"], recs[i]["lng"], recs[i]["coords_source"] = q["lat"], q["lng"], q.get("coords_source")

    # ---- crawled parish sites
    by_dir = {r["dir"]["id"]: r for r in recs if r["dir"]}
    by_dm = {r["dm"]["id"]: r for r in recs if r["dm"]}
    dom_count = Counter(domain(r["website"]) for r in recs if domain(r["website"]))
    by_dom = {domain(r["website"]): r for r in recs if domain(r["website"]) and dom_count[domain(r["website"])] == 1}
    site_stats = Counter()
    for s in sites:
        st = s.get("crawl_status")
        if ".".join((s.get("domain") or "").split(".")[-2:]) in DIOCESAN_HOSTS:
            site_stats["diocese_site_skipped"] += 1
            continue
        site_stats["listed"] += 1
        site_stats["read" if st in READ_OK else "refused" if st in REFUSED else "unreachable"] += 1
        targets, how = [], None
        for i in s.get("matched", {}).get("directory", []):
            if i in by_dir:
                targets.append(by_dir[i])
                how = "site crawl list (directory id)"
        if not targets:
            for i in s.get("matched", {}).get("discovermass", []):
                if i in by_dm:
                    targets.append(by_dm[i])
                    how = "site crawl list (DiscoverMass id)"
        if not targets:
            d = domain(s.get("home_url")) or s.get("domain")
            if d in by_dom:
                targets.append(by_dom[d])
                how = "website domain"
        for t in targets:
            t["sites"].append(s)
            t["match"].setdefault("sites", []).append({"domain": s["domain"], "crawl_status": st, "match_method": how, "file": s["_file"]})
            if not t["website"] and s.get("final_url"):
                t["website"] = s["final_url"]

    # ---- geocode what is still not on the map (directory addresses; Census, public domain)
    need = [addr_key(r["address"], r["city"], r["zip"]) for r in recs if r["lat"] is None]
    places_raw = []
    if has_dir:
        for e in entities:
            pt = place_type(e)
            if pt and pt != "school":  # schools come from the CDE/NCES file, which has coordinates
                places_raw.append((pt, e))
                if e.get("lat") is None:
                    need.append(addr_key(e.get("address"), e.get("city"), e.get("zip")))
    geo = geocode_all([a for a in need if a], geocode)
    for r in recs:
        if r["lat"] is None:
            g = geo.get(addr_key(r["address"], r["city"], r["zip"]) or "")
            if g:
                r["lat"], r["lng"], r["coords_source"] = g[0], g[1], f"US Census Geocoder ({g[2]}), from the directory's address"

    # ---- units: regions / deaneries / vicariates
    units = structure.get("units") or []
    unit_label = "Deaneries"
    if any(u.get("kind") == "vicariate" for u in units):
        unit_label = "Vicariates"
    for r in recs:
        p = r["dir"] or {}
        r["region"] = slug(p.get("region_name")) if p.get("region_name") else None
        if slug_ == "los-angeles" and p.get("deanery_number"):
            r["deanery"] = f"d{p['deanery_number']}"
        elif p.get("vicariate") and unit_label == "Vicariates":
            r["deanery"] = slug(p["vicariate"])
        elif p.get("deanery"):
            r["deanery"] = slug(p["deanery"])
        else:
            r["deanery"] = None
    deaneries = {}
    for r in recs:
        p = r["dir"] or {}
        if r["deanery"] and r["deanery"] not in deaneries:
            nm = p.get("vicariate") if unit_label == "Vicariates" else p.get("deanery")
            if slug_ == "los-angeles":
                nm = f"Deanery {p['deanery_number']}"
            deaneries[r["deanery"]] = {"id": r["deanery"], "name": nm, "region": r["region"]}

    def dkey(d):
        m = re.search(r"\d+", d["name"] or "")
        return (int(m.group()) if m else 999, d["name"] or "")

    deaneries = sorted(deaneries.values(), key=dkey)

    # ---- schedules, groups, events, social, bulletin per parish
    window30, window60 = TODAY + timedelta(days=30), TODAY + timedelta(days=60)
    all_events, all_groups, social_n, bulletin_n = [], [], 0, 0
    gstats = Counter()
    for r in recs:
        rows = []
        for s in r["sites"]:
            env = s.get("crawl_env")
            st = s.get("schedule_template") or {}
            srows = s.get("schedule_rows") or []
            if st.get("template") and st.get("template") != "generic-fallback":
                srows = rows_for_church(srows, r["name"]) if len(s.get("matched_church_ids") or []) > 1 or sum(
                    1 for q in recs if s in q["sites"]) > 1 else srows
            trusted_site = bool(st.get("trusted_candidate")) and sunday_agrees(srows, r["dm"])
            if st.get("trusted_candidate"):
                TRUST_STATS["trusted" if trusted_site else "sunday_disagrees_with_discovermass"] += 1
            for x in srows:
                u = x.get("source_url") or ""
                dt = (x.get("fetched_at") or s.get("fetched_at") or "")[:10]
                # every row here is the site crawl's automatic reading (parse_site.py), whatever page it came from: a
                # bulletin PDF or an archived page read by the parser is not a row read by hand ('bulletin-', 'wayback-')
                if "web.archive.org" in u:
                    m = re.search(r"/web/(\d{4})(\d\d)(\d\d)", u)
                    label = f"crawled-wayback-{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else f"crawled-wayback-{dt}"
                elif re.search(r"\.pdf($|\?)|parishesonline|bulletin", u, re.I):
                    label = f"crawled-bulletin-{host_of(u)}-{dt}"
                elif trusted_site and not x.get("needs_review"):
                    label = f"site-{st['template']}-{dt}"   # a template that passed its bar; outranks DiscoverMass (tier 3)
                elif env == "sidedoor" or "wp-json" in u:
                    label = f"sidedoor-{dt}"
                else:
                    label = f"crawled-{dt}"
                rows.append(holy_day_check(sanity({"kind": x.get("kind"), "day": x.get("day"), "time": x.get("time"), "language": x.get("language"),
                                    "raw": x.get("raw"), "source": label, "source_url": u or s.get("source_url"), "fetched_at": x.get("fetched_at"),
                                    "needs_review": bool(x.get("needs_review")), "review_reason": x.get("review_reason")}), s["domain"]))
            # weekly calendar entries: tier 7, below every posted schedule. Off until lib/find.ts sourceTier() knows 'calendar-'
            # (docs/patches/2026-11-01-find-calendar-tier.patch: it could not be built in the sandbox, which has no npm registry
            # access; unpatched, find.ts would read 'calendar-' as tier 5 and rank it above the parish's own posted rows).
            for x in (s.get("calendar_schedule_rows") or []) if CALENDAR_ROWS else []:
                rows.append(sanity({"kind": x.get("kind"), "day": x.get("day"), "time": x.get("time"), "language": x.get("language"),
                                    "raw": x.get("raw"), "source": f"calendar-{(x.get('fetched_at') or '')[:10]}",
                                    "source_url": x.get("source_url") or s.get("source_url"), "fetched_at": x.get("fetched_at"),
                                    "needs_review": bool(x.get("needs_review")), "review_reason": x.get("review_reason")}))
        p = r["dir"] or {}
        mt = p.get("mass_times")
        if isinstance(mt, dict) and mt:
            lines = []
            for head, ls in mt.items():
                for ln in ls or []:
                    lines.append(f"{head}: {ln}" if re.search(r"confess|reconcil|adorat", head, re.I) else ln)
            rows += map(sanity, parse_schedule_text(lines, f"directory-{slug_}-{(p.get('fetched_at') or '')[:10]}",
                                        p.get("page_url") or p.get("source_url"), p.get("fetched_at")))
        d = r["dm"]
        if d:
            lab = f"secondary-discovermass-{(d.get('fetched_at') or '')[:10]}"
            for x in d.get("schedule") or []:
                t = x.get("time")
                if t and x.get("end_time"):
                    t = f"{t}-{x['end_time']}"
                rows.append({"kind": x.get("kind"), "day": x.get("day"), "time": t, "language": x.get("language"),
                             "raw": x.get("raw"), "source": lab, "source_url": d.get("source_url"), "fetched_at": d.get("fetched_at"),
                             "needs_review": bool(x.get("flags")), "review_reason": ",".join(x.get("flags") or []) or None})
        r["rows"] = choose_rows(rows)
        r["kinds"] = kinds_of(r["rows"])
        r["has_times"] = "mass" in r["kinds"]

        # groups (the parish's own site, privacy-screened by the crawl)
        gs, seen = [], set()
        for s in r["sites"]:
            for g in s.get("groups") or []:
                nm = group_name(g.get("name"))
                k = (nm or "").lower()
                if not nm or k in seen or not g.get("category"):
                    continue
                seen.add(k)
                gs.append({"name": nm, "category": g["category"], "subcategory": g.get("subcategory"),
                           "audience": g.get("audience"), "language": g.get("language"),
                           "language_assumed": bool(g.get("language_assumed")), "meets": g.get("meets"),
                           "source_url": g.get("source_url"), "fetched_at": g.get("fetched_at"), "confidence": g.get("confidence"),
                           "via": "website", "source_label": "Listed on the parish website"})
        site_subcats = {g["subcategory"] for g in gs if g.get("subcategory")}

        def add_group(g: dict, via: str, label: str, fetched: str | None, meets: str | None) -> None:
            """One group from the bulletin or an archived page: merged into a group already listed (same name, or same
            kind and language) as a second source, else added. Contact stays "ask the parish office"."""
            nm = group_name(g.get("name"))
            if not nm or not g.get("category"):
                return
            gk, sk = group_key(nm), (g.get("subcategory"), g.get("language"))
            for x in gs:
                if (gk and group_key(x["name"]) == gk) or (g.get("subcategory") and (x.get("subcategory"), x.get("language")) == sk):
                    x.setdefault("also", []).append({"via": via, "label": label, "source_url": g.get("source_url"), "fetched_at": fetched})
                    if not x.get("meets") and meets:
                        x["meets"] = meets
                    gstats[f"{via}_merged"] += 1
                    return
            gs.append({"name": nm, "category": g["category"], "subcategory": g.get("subcategory"), "audience": g.get("audience"),
                       "language": g.get("language"), "language_assumed": bool(g.get("language_assumed")), "meets": meets,
                       "source_url": g.get("source_url"), "fetched_at": fetched, "confidence": g.get("confidence"),
                       "via": via, "source_label": label, "informal": g.get("informal")})
            gstats[f"{via}_added"] += 1

        # the parish bulletin (ingest/california/bulletins/): by the site file the bulletin link was found on, else the parish id
        bents, seen_b = [], set()
        for s in r["sites"]:
            cands = bul_by_file.get(s["_file"], [])
            mine = [p for p in cands if p.get("parish_id") == r["id"]]
            for p in mine or cands:
                if id(p) not in seen_b:
                    seen_b.add(id(p))
                    bents.append(p)
        if not bents and r["id"] in bul_by_pid:
            bents.append(bul_by_pid[r["id"]])
        for p in bents:
            for g in p.get("groups") or []:
                if (g.get("last_seen") or "") < (TODAY - timedelta(days=BULLETIN_MAX_AGE)).isoformat():
                    gstats["bulletin_stale"] += 1
                    continue
                if g.get("informal") is False and g.get("subcategory") in site_subcats:
                    gstats["bulletin_formal_dup"] += 1
                    continue
                iss = next((i for ln in (p.get("links") or []) for i in (ln.get("issues") or []) if i.get("url") == g.get("source_url")), None)
                add_group(g, "bulletin", g.get("source_label") or f"From the parish bulletin, {nice_date(g.get('last_seen'))}",
                          (iss or {}).get("fetched_at") or g.get("last_seen"),
                          ((g.get("meets") or {}).get("text") if isinstance(g.get("meets"), dict) else g.get("meets")) or next_text(g.get("next_meeting")))
        # an archived copy of the parish website (Wayback Machine), for sites the crawl could not read
        for s in r["sites"]:
            wb = wb_by_dom.get(s["domain"])
            if not wb or s.get("crawl_status") in READ_OK:
                continue
            for g in wb.get("groups") or []:
                cap = (g.get("fetched_at") or "")[:10]
                if not cap or cap < (TODAY - timedelta(days=WAYBACK_MAX_AGE)).isoformat():
                    gstats["wayback_old"] += 1
                    continue
                add_group(g, "wayback", f"From an archived copy of the parish website, {nice_date(cap)}", cap, g.get("meets"))
        r["groups"] = gs
        all_groups += [(r["id"], g) for g in gs]

        # events (the parish's own calendar feeds, privacy-screened by the crawl)
        evs = []
        for s in r["sites"]:
            for e in s.get("events") or []:
                if not e.get("start") or not keep_event(e.get("title") or "", e.get("category"), e.get("tags")):
                    continue
                dd, hh = wall(e["start"], bool(e.get("all_day")))
                try:
                    dv = date.fromisoformat(dd)
                except ValueError:
                    continue
                if dv < TODAY - timedelta(days=0) or dv > TODAY + timedelta(days=90):
                    continue
                evs.append({"pid": r["id"], "date": dd, "time": hh, "title": e["title"].strip()[:120],
                            "type": event_type(e.get("category"), e["title"]), "lang": language_of(e["title"]),
                            "url": e.get("source_url"), "fetched_at": e.get("fetched_at")})
        one, ser = fold_events(evs)
        r["events"] = [e for e in one if date.fromisoformat(e["date"]) <= window30]
        r["weekly"] = ser
        r["has_events"] = bool(r["events"] or r["weekly"])
        all_events += [e for e in one if date.fromisoformat(e["date"]) <= window60]
        r["series"] = ser

        # social: the parish's own site first, then the directory, then DiscoverMass
        soc = {}

        def add(plat, url, via):
            plat = {"fb": "facebook", "twitter": "x", "ig": "instagram", "yt": "youtube"}.get(plat, plat)  # Orange's directory says "fb"
            if not url or plat in soc:
                return
            u = url if isinstance(url, str) else url.get("url")
            if not u:
                return
            if plat == "facebook" and re.search(r"facebook\.com/(\d{1,6}|sharer|share|tr|plugins|dialog|groups/?$)(/|$|\?)", u):
                return
            # a person's own profile ("facebook.com/first.last123", "profile.php?id="), not the parish's page: never kept
            if plat == "facebook" and re.search(r"\.com/(profile\.php|people/|[a-z]+\.[a-z]+\.?\d+/?$)", u, re.I):
                return
            if plat == "livestream" and isinstance(url, dict) and re.search(r"donat|give|giving|tithe", (url.get("label") or "") + u, re.I):
                return
            if not social_is_own(plat, u, r):
                SOCIAL_STATS[f"dropped_{plat}"] += 1
                return
            soc[plat] = {"url": u, "via": via}

        for s in r["sites"]:
            if school_site(s, r):
                continue
            for plat, us in (s.get("social") or {}).items():
                for u in (us or []):
                    add(plat, u, "parish website")
        for s in r["sites"]:
            wb = wb_by_dom.get(s["domain"])
            if wb and s.get("crawl_status") not in READ_OK and not school_site(s, r):
                for plat, us in (wb.get("social") or {}).items():
                    for u in (us or []):
                        add(plat, u, "archived copy of the parish website")
        for plat, us in ((p.get("social") or {}) if isinstance(p.get("social"), dict) else {}).items():
            for u in (us if isinstance(us, list) else [us]):
                add(plat, u, "diocese's directory")
        if d:
            for plat, us in (d.get("social") or {}).items():
                for u in us or []:
                    add(plat, u, "DiscoverMass page")
        r["social"] = soc
        social_n += bool(soc)
        b = next((s.get("bulletin") for s in r["sites"] if s.get("bulletin") and s["bulletin"].get("url") and not school_site(s, r)), None)
        r["bulletin"] = None
        if b:
            bd = b.get("date")
            recent = bool(bd) and str(TODAY - timedelta(days=60)) <= bd <= str(TODAY + timedelta(days=14))
            # an old or odd date means the link found is not this week's bulletin: point to the page it was found on
            r["bulletin"] = {"url": b["url"] if recent or not bd else (b.get("source_url") or b["url"]), "date": bd if recent else None,
                             "kind": b.get("kind"), "found_on": b.get("source_url"), "date_on_link": bd}
        else:
            # the bulletin page or host an archived copy of the parish website links to (the parish's own address, not the
            # archive's: the current issue lives there; a dated file link is old, so the page is linked instead)
            for s in r["sites"]:
                wb = wb_by_dom.get(s["domain"])
                if not wb or s.get("crawl_status") in READ_OK or school_site(s, r) or not wb.get("bulletin"):
                    continue
                wbl, bl = wb["bulletin"], (wb["bulletin"].get("best_link") or {})
                url = (bl.get("url") if bl.get("kind") in ("flipbook", "page", "host", "folder") or not bl.get("date") else None) or wbl.get("page_url") or bl.get("url")
                if url:
                    cap = next((c.get("capture_date") for c in (wb.get("wayback") or {}).get("captures", []) if "bulletin" in (c.get("roles") or [])), None)
                    r["bulletin"] = {"url": url, "date": None, "kind": bl.get("kind") or "page", "found_on": wbl.get("page_archive_url"),
                                     "date_on_link": bl.get("date"), "via": f"archived copy of the parish website, {nice_date(cap)}" if cap else "archived copy of the parish website"}
                    b = r["bulletin"]
                    gstats["wayback_bulletin"] += 1
                    break
        bulletin_n += bool(b)

    # ---- diocesan events (the diocese's own calendar)
    for e in dio_events:
        if not e.get("start"):
            continue
        dd, hh = wall(e["start"], bool(e.get("all_day")))
        try:
            dv = date.fromisoformat(dd)
        except ValueError:
            continue
        if dv < TODAY or dv > window60:
            continue
        all_events.append({"pid": None, "date": dd, "time": hh, "title": e["title"].strip()[:120],
                           "type": diocese_event_type(e.get("categories") or [], e["title"]), "lang": language_of(e["title"]),
                           "url": e.get("url") or e.get("source_url"), "venue": (e.get("location") or "")[:90] or None,
                           "end": day_of(e.get("end")) if e.get("all_day") and day_of(e.get("end")) != dd else None, "dio": True})

    # ---- other places: directory entities, CDE/NCES schools, Catholic health (if present)
    places = []
    for pt, e in places_raw:
        lat, lng, cs = e.get("lat"), e.get("lng"), "diocese's directory" if e.get("lat") is not None else None
        if lat is None:
            g = geo.get(addr_key(e.get("address"), e.get("city"), e.get("zip")) or "")
            if g:
                lat, lng, cs = g[0], g[1], "US Census Geocoder, from the directory's address"
        places.append({"id": slug(f"{e.get('name')}-{e.get('city') or ''}")[:80], "name": e.get("name"), "type": pt,
                       "subtype": e.get("subtype"), "city": e.get("city"), "address": e.get("address"), "lat": r5(lat), "lng": r5(lng),
                       "phone": e.get("phone"), "website": e.get("website"), "source": "diocese's directory",
                       "source_url": e.get("source_url"), "fetched_at": (e.get("fetched_at") or "")[:10], "coords_source": cs})
    cde = load_json(REAL / "schools_cde.json", {}) or {}
    for s in cde.get("records", []):
        if s.get("diocese") != slug_:
            continue
        places.append({"id": f"cde-{s.get('cds_code')}", "name": s.get("name"), "type": "school", "subtype": s.get("grades"),
                       "city": s.get("city"), "address": s.get("address"), "lat": r5(s.get("lat")), "lng": r5(s.get("lng")),
                       "phone": s.get("phone"), "website": None, "source": "California Department of Education private-school affidavit + NCES survey",
                       "source_url": "https://www.cde.ca.gov/ds/si/ps/", "fetched_at": (cde.get("fetched_at") or "")[:10],
                       "coords_source": "NCES Private School Survey", "enrollment": s.get("enrollment"), "school_year": s.get("school_year")})
    health = load_json(REAL / "health.json", {}) or {}
    hrecs = health.get("records", health.get("facilities", [])) if isinstance(health, dict) else []
    if hrecs:  # the statewide health file (state licence data + sponsor evidence) replaces the directories' hospital rows
        places = [x for x in places if x["type"] != "health"]
    for h in hrecs:
        if (h.get("diocese_by_county") or h.get("diocese")) != slug_:
            continue
        places.append({"id": slug(f"health-{h.get('name')}-{h.get('city') or ''}")[:80], "name": h.get("name"), "type": "health",
                       "subtype": h.get("subtype") or h.get("type"), "city": h.get("city"), "address": h.get("address"),
                       "lat": r5(h.get("lat")), "lng": r5(h.get("lng")), "phone": h.get("phone"), "website": h.get("website"),
                       "source": "Catholic hospitals and nursing facilities: CDPH facility list + sponsor evidence",
                       "source_url": h.get("source_url") or h.get("state_source_url"),
                       "fetched_at": (health.get("built_at") or "")[:10], "coords_source": None})
    seen_ids = Counter()
    for pl in places:
        seen_ids[pl["id"]] += 1
        if seen_ids[pl["id"]] > 1:
            pl["id"] = f"{pl['id']}-{seen_ids[pl['id']]}"

    return {"slug": slug_, "dio": dio, "has_dir": has_dir, "recs": recs, "structure": structure, "unit_label": unit_label,
            "deaneries": deaneries, "events": all_events, "groups": all_groups, "places": places, "dm_meta": dm_meta,
            "dm_count": len(dm), "dm_unmatched": dm_unmatched, "site_stats": site_stats, "sites_n": len(sites),
            "social_n": social_n, "bulletin_n": bulletin_n, "directory_n": len(directory), "group_stats": dict(gstats),
            "sites": sites, "wayback_n": len(wb_by_dom), "bulletin_parishes": len(bul_by_pid)}


# ------------------------------------------------------------------------------------------------ output
def sources_for(b: dict) -> list[dict]:
    slug_, st = b["slug"], b["structure"]
    out = []
    if b["has_dir"]:
        p0 = b["recs"][0]["dir"]
        out.append({"what": "Parishes, status, addresses, office phones", "label": f"{b['dio']['name']}: its own parish directory",
                    "url": p0.get("list_source_url") or p0.get("page_url") or p0.get("directory_url") or p0.get("source_url"),
                    "fetched_at": (p0.get("fetched_at") or "")[:10]})
    if b["dm_count"]:
        out.append({"what": "Mass times where nothing better exists; some coordinates and phones" if b["has_dir"] else "Churches, Mass times",
                    "label": "DiscoverMass.com (MassTimes.org listing), secondary, no last-updated date", "url": "https://discovermass.com/",
                    "fetched_at": (b["dm_meta"].get("built_at") or "")[:10]})
    if b["sites_n"]:
        out.append({"what": "Schedules, groups, events, social links and bulletins", "label": f"The parishes' own websites ({b['site_stats']['read']} of {b['sites_n']} read)",
                    "url": None, "fetched_at": TODAY.isoformat()})
    if b.get("bulletin_parishes"):
        out.append({"what": "Groups (men's, women's, Bible study, young adults…)", "label": "The parishes' own bulletins (PDF), read automatically and privacy-screened",
                    "url": None, "fetched_at": TODAY.isoformat()})
    if b.get("wayback_n"):
        out.append({"what": "Groups, bulletin and social links for parish websites that could not be read", "label": "Archived copies of the parish websites (Internet Archive Wayback Machine)",
                    "url": "https://web.archive.org/", "fetched_at": TODAY.isoformat()})
    if any(r["coords_source"] and "Census" in r["coords_source"] for r in b["recs"]):
        out.append({"what": "Map locations where the directory gives none", "label": "US Census Geocoder (public domain)",
                    "url": "https://geocoding.geo.census.gov/", "fetched_at": TODAY.isoformat()})
    if st.get("units"):
        out.append({"what": b["unit_label"], "label": f"{b['dio']['name']}: its published structure", "url": st.get("counties_source_url") or (st.get("source_urls") or [None])[0],
                    "fetched_at": (st.get("fetched_at") or "")[:10]})
    return out


def coverage_of(b: dict) -> dict:
    recs = b["recs"]
    n = len(recs)
    own = sum(1 for r in recs if any(x["source"] and tier(x["source"]) in (T_TRUSTED, T_AUTO) and display_kind(x) == "mass" and not x["hidden"] for x in r["rows"]))
    by_tier = Counter(r["kinds"]["mass"]["tier"] for r in recs if "mass" in r["kinds"])
    return {
        "diocese": b["slug"], "name": b["dio"]["name"], "built_at": now_iso(),
        "parishes": {"n": n, "source": "the diocese's own directory" if b["has_dir"] else ("DiscoverMass.com listing (secondary)" if n else None),
                     "on_map": sum(r["lat"] is not None for r in recs)},
        "status_from_directory": b["has_dir"],
        "with_times": {"n": sum(r["has_times"] for r in recs), "by_source": dict(by_tier)},
        "with_own_site_times": {"n": own, "source": "the parish's own website, read by a checked template (above DiscoverMass) or automatically "
                                                      "(shown only where no better source lists that day)"},
        "with_confession_times": sum("confession" in r["kinds"] for r in recs),
        "possibly_incomplete": {"n": sum(any(v.get("fewer") for v in r["kinds"].values()) for r in recs),
                                "mass": sum(bool(r["kinds"].get("mass", {}).get("fewer")) for r in recs),
                                "note": "parishes where the source shown for a day lists fewer times than DiscoverMass does for that day; "
                                        "the card says so and links the listing"},
        "mixed_sources": sum(bool(r["kinds"].get("mass", {}).get("also")) for r in recs),
        "with_adoration_times": sum("adoration" in r["kinds"] for r in recs),
        "with_groups": {"n": sum(bool(r["groups"]) for r in recs), "groups": sum(len(r["groups"]) for r in recs),
                        "source": "the parish's own website and bulletin (an archived copy of the website where it could not be read)",
                        "by_source": {v: sum(1 for g in (g for r in recs for g in r["groups"]) if (g.get("via") or "website") == v)
                                      for v in ("website", "bulletin", "wayback")},
                        "parishes_by_source": {v: sum(1 for r in recs if any((g.get("via") or "website") == v for g in r["groups"]))
                                               for v in ("website", "bulletin", "wayback")},
                        "merge_stats": b.get("group_stats") or {}},
        "with_events": {"n": sum(r["has_events"] for r in recs), "source": "the parish's own calendar feed"},
        "diocesan_events_60d": sum(1 for e in b["events"] if e.get("dio")),
        "with_social": {"n": b["social_n"], "source": "the parish's own website, the directory, DiscoverMass"},
        "with_bulletin_link": b["bulletin_n"],
        "sites": {"listed": b["sites_n"], "read": b["site_stats"]["read"], "refused": b["site_stats"]["refused"],
                  "unreachable": b["site_stats"]["unreachable"], "source": "data/real/california/sites/" + b["slug"] + "/"},
        "sites_read": sites_read(b),
        "discovermass_listings": b["dm_count"], "discovermass_not_in_directory": len(b["dm_unmatched"]) if b["has_dir"] else None,
        "other_places": dict(Counter(p["type"] for p in b["places"])),
    }


def sites_read(b: dict) -> dict:
    """What the site crawl got, apart from what is shown. A site that yielded Mass times may still show none: DiscoverMass
    (or a source read by a person) outranks an automatic reading for every day it lists. So "sites that yielded times" is
    the crawl's yield, and with_times.by_source / with_own_site_times are what the cards show."""
    ss = [s for s in b.get("sites") or [] if ".".join((s.get("domain") or "").split(".")[-2:]) not in DIOCESAN_HOSTS]
    ok = [s for s in ss if s.get("crawl_status") == "ok"]
    live = lambda s, k: any(x.get("kind") == k and not x.get("needs_review") for x in s.get("schedule_rows") or [])  # noqa: E731
    recs = b["recs"]
    yielded = [r for r in recs if any(live(s, "mass") for s in r["sites"])]
    shown = [r for r in yielded if any(not x["hidden"] and tier(x["source"]) in (T_TRUSTED, T_AUTO) and display_kind(x) == "mass" for x in r["rows"])]
    return {
        "note": "the crawl's own yield, separate from the source shown on the cards (a better source wins per day)",
        "sites_listed": len(ss),
        "crawl_ok": len(ok),
        "crawl_ok_by_env": dict(Counter(s.get("crawl_env") or "sandbox" for s in ok)),
        "sidedoor_only": sum(1 for s in ss if s.get("crawl_env") == "sidedoor"),
        "yielded_any_schedule_rows": sum(1 for s in ss if s.get("schedule_rows")),
        "yielded_mass_times": sum(1 for s in ss if live(s, "mass")),
        "yielded_confession_times": sum(1 for s in ss if live(s, "confession")),
        "yielded_groups": sum(1 for s in ss if s.get("groups")),
        "yielded_events": sum(1 for s in ss if s.get("events")),
        "yielded_bulletin_link": sum(1 for s in ss if (s.get("bulletin") or {}).get("url")),
        "trusted_template_parses": sum(1 for s in ss if (s.get("schedule_template") or {}).get("trusted_candidate")),
        "parishes_with_site_mass_times": len(yielded),
        "parishes_showing_site_mass_times": len(shown),
        "parishes_site_times_outranked": len(yielded) - len(shown),
        "wayback_copies": b.get("wayback_n", 0),
        "bulletin_parishes": b.get("bulletin_parishes", 0),
        "crawl_status": dict(Counter(s.get("crawl_status") for s in ss)),
    }


def what_we_have(c: dict) -> list[dict]:
    """The quiet line under the diocese name: facts, each with where it came from. No ranking."""
    p = c["parishes"]
    out = []
    if p["n"]:
        out.append({"text": f"{p['n']} parishes and missions", "src": p["source"]})
    else:
        out.append({"text": "X parishes", "src": "not collected yet", "placeholder": True})
    t = c["with_times"]
    if t["n"]:
        said = {"checked": "checked by hand", "bulletin": "from the parish bulletin", "wayback": "from an archived copy of the parish website",
                "site": "from the parish's own website (checked template)", "secondary": "from DiscoverMass", "directory": "from the diocese's directory, read automatically",
                "own": "from the parish's own website, read automatically"}
        bits = [f"{t['by_source'][k]} {v}" for k, v in said.items() if t["by_source"].get(k)]
        out.append({"text": f"Mass times for {t['n']}", "src": ", ".join(bits)})
    else:
        out.append({"text": "Mass times for X", "src": "not collected yet", "placeholder": True})
    g = c["with_groups"]
    out.append({"text": f"groups at {g['n']}", "src": g["source"]} if g["n"] else {"text": "groups at X", "src": "not collected yet", "placeholder": True})
    e = c["with_events"]
    out.append({"text": f"calendars from {e['n']}", "src": e["source"]} if e["n"] else {"text": "calendars from X", "src": "not collected yet", "placeholder": True})
    s = c["sites"]
    if s["listed"]:
        sr = c.get("sites_read") or {}
        y = f"{sr['yielded_mass_times']} gave Mass times, {sr['yielded_groups']} groups; " if sr.get("yielded_mass_times") is not None else ""
        out.append({"text": f"{s['read']} of {s['listed']} parish websites read", "src": f"{y}{s['refused']} refused automated reading"})
    else:
        out.append({"text": "X parish websites read", "src": "not collected yet", "placeholder": True})
    return out


def is_latin_rite(rite: str | None) -> bool:
    """The Latin (Roman) rite in any spelling the sources use: "Latin" (directories), "Roman-Latin" (DiscoverMass),
    "Roman Catholic", "Latin Rite". Only a church of another rite gets the eparchy notice on its card."""
    return not rite or bool(re.search(r"\blatin\b|^roman(?:\s+catholic)?$", fold(rite).replace("-", " ").strip()))


def detail(r: dict, b: dict) -> dict:
    shown = rejoin([x for x in r["rows"] if not x["hidden"]])
    held = Counter(x["hidden"] for x in r["rows"] if x["hidden"])
    srcs = []
    if r["dir"]:
        p = r["dir"]
        srcs.append({"what": "Parish, status, address and office phone", "label": f"{b['dio']['name']} directory",
                     "url": p.get("page_url") or p.get("directory_url") or p.get("source_url"), "fetched_at": (p.get("fetched_at") or "")[:10]})
    if r["coords_source"]:
        srcs.append({"what": "Map location", "label": r["coords_source"], "url": None, "fetched_at": None})
    for s in r["sites"]:
        srcs.append({"what": f"Parish website ({s.get('crawl_status')})", "label": s["domain"], "url": s.get("final_url") or s.get("home_url"),
                     "fetched_at": (s.get("fetched_at") or "")[:10]})
    if r["dm"]:
        srcs.append({"what": "DiscoverMass listing (secondary)", "label": "DiscoverMass.com", "url": r["dm"]["source_url"],
                     "fetched_at": (r["dm"].get("fetched_at") or "")[:10]})
    addr = None
    if r["address"]:
        addr = ", ".join(x.strip(" ,") for x in [r["address"], r["city"], f"CA {r['zip'] or ''}".strip()] if x and x.strip(" ,"))
    return {
        "id": r["id"], "name": r["name"], "city": r["city"], "address": addr, "phone": r["phone"], "website": r["website"],
        "kind": r["kind"], "region": r["region"], "deanery": r["deanery"], "closing": False, "parish_page": None,
        "status": r["status"], "status_note": r["status_note"], "rite": None if is_latin_rite(r["rite"]) else r["rite"],
        "rows": [public_row(x) for x in shown], "kinds": r["kinds"], "held": dict(held),
        "groups": [{k: g.get(k) for k in ("name", "category", "subcategory", "audience", "language", "language_assumed", "meets", "source_url", "via", "source_label")}
                   for g in r["groups"]],
        "groups_via": sorted({g.get("via") or "website" for g in r["groups"]}),
        "events": [{k: e.get(k) for k in ("date", "time", "title", "type", "url")} for e in r["events"][:40]],
        "weekly": [{"wd": s["wd"], "time": s["time"], "title": s["title"], "type": s["type"], "url": s["url"]} for s in r["weekly"][:24]],
        "events_from": next((e.get("url") for e in r["events"] + r["weekly"] if e.get("url")), None),
        "social": {k: v["url"] for k, v in r["social"].items()},
        "social_via": sorted({v["via"] for v in r["social"].values()}),
        "groups_read": nice_date(max(((g.get("fetched_at") or g.get("last_seen") or "")[:10] for g in r["groups"]), default="") or None),
        "events_read": nice_date(max(((e.get("fetched_at") or "")[:10] for e in r["events"] + r["weekly"]), default="") or None),
        "bulletin": r["bulletin"], "sources": srcs, "match": r["match"],
        "site_read": any(s.get("crawl_status") in READ_OK for s in r["sites"]) or r.get("site_status") == "ok",
        "social_collected": bool(r["sites"]) or bool(r["dm"]),
    }


def outputs(b: dict) -> dict:
    """Writes every public file for one diocese (not map.json: build_ca_static writes it with the layer). Returns the
    map index pieces."""
    slug_ = b["slug"]
    recs = sorted(b["recs"], key=lambda r: r["name"])
    rows, details = [], {}
    for r in recs:
        rows.append([r["id"], r["name"], r["city"], r5(r["lat"]), r5(r["lng"]), r["region"], r["deanery"], r["kind"],
                     1 if r["has_times"] else 0, 1 if r["groups"] else 0, 1 if r["has_events"] else 0])
        details[r["id"]] = detail(r, b)
    # groups.json: compact, one row per group, urls by index
    urls, uidx = [], {}

    def ui(u):
        if not u:
            return None
        if u not in uidx:
            uidx[u] = len(urls)
            urls.append(u)
        return uidx[u]

    gcols = ["pid", "name", "category", "subcategory", "audience", "language", "language_assumed", "meets", "url", "via", "read"]
    grows = [[pid, g["name"], g["category"], g.get("subcategory"), g.get("audience"), g.get("language"), 1 if g.get("language_assumed") else 0,
              g.get("meets"), ui(g.get("source_url")), g.get("via") or "website", (g.get("fetched_at") or "")[:10] or None] for pid, g in b["groups"]]
    groups = {"v": 2, "diocese": slug_, "built_at": now_iso(), "cols": gcols, "rows": grows, "urls": urls,
              "source": "Each parish's own website (ministries pages), its bulletin, or an archived copy of its website where the site "
                        "itself could not be read; privacy-screened: no personal names, phones or e-mails.",
              "fetched_at": TODAY.isoformat()}
    # events.json: next 60 days; weekly series apart
    eurls, eidx = [], {}

    def eu(u):
        if not u:
            return None
        if u not in eidx:
            eidx[u] = len(eurls)
            eurls.append(u)
        return eidx[u]

    one, seen_e = [], set()
    for e in sorted(b["events"], key=lambda e: (e["date"], e["time"] or "")):
        k = (e["date"], e["time"], e["title"].lower(), e.get("url"))
        if k not in seen_e:
            seen_e.add(k)
            one.append(e)
    ecols = ["date", "time", "title", "pid", "type", "lang", "url", "venue", "end"]
    erows = [[e["date"], e["time"], e["title"], e["pid"], e["type"], e.get("lang"), eu(e.get("url")), e.get("venue"), e.get("end")] for e in one]
    series = []
    for r in recs:
        for s in r["series"]:
            series.append([r["id"], s["wd"], s["time"], s["title"], s["type"], s.get("lang"), eu(s.get("url"))])
    series.sort(key=lambda x: (x[1], x[2] or "", x[3]))
    events = {"v": 1, "diocese": slug_, "built_at": now_iso(), "from": TODAY.isoformat(), "to": (TODAY + timedelta(days=59)).isoformat(),
              "cols": ecols, "rows": erows, "series_cols": ["pid", "weekday", "time", "title", "type", "lang", "url"], "series": series, "urls": eurls,
              "sources": [{"what": "Parish events", "label": "the parishes' own public calendar feeds (Google Calendar, iCal, The Events Calendar), privacy-screened", "fetched_at": TODAY.isoformat()},
                          {"what": "Diocesan events", "label": f"{b['dio']['name']}: its own events calendar", "fetched_at": (b["structure"].get("fetched_at") or "")[:10]}],
              "note": "Regular Masses and confessions, room bookings, meetings and anything naming a private person are left out."}
    pcols = ["id", "name", "type", "city", "lat", "lng", "address", "phone", "website", "src"]
    psrc, pidx = [], {}
    prow = []
    for pl in sorted(b["places"], key=lambda x: (x["type"], x["name"] or "")):
        k = (pl["source"], pl.get("source_url"), pl.get("fetched_at"))
        if k not in pidx:
            pidx[k] = len(psrc)
            psrc.append({"label": pl["source"], "url": pl.get("source_url"), "fetched_at": pl.get("fetched_at")})
        prow.append([pl["id"], pl["name"], pl["type"], pl["city"], pl["lat"], pl["lng"], pl["address"], pl.get("phone"), pl.get("website"), pidx[k]])
    places = {"v": 1, "diocese": slug_, "built_at": now_iso(), "cols": pcols, "rows": prow, "sources": psrc, "types": PLACE_TYPES}
    return {"rows": rows, "details": details, "groups": groups, "events": events, "places": places}


def merged_dump(b: dict) -> dict:
    out = []
    for r in b["recs"]:
        out.append({k: r[k] for k in ("id", "name", "kind", "type", "status", "status_note", "listed_by", "address", "city", "zip", "county",
                                      "lat", "lng", "coords_source", "phone", "website", "region", "deanery", "rite", "languages", "match",
                                      "kinds", "social", "bulletin")}
                   | {"rows": [{k: x.get(k) for k in ("kind", "kind_parsed", "day", "day_parsed", "time", "language", "language_parsed", "bucket", "raw", "source", "source_url", "fetched_at", "review_reason", "hidden")} for x in rejoin(r["rows"])],
                      "groups_n": len(r["groups"]), "events_30d": len(r["events"]), "weekly_series": len(r["weekly"])})
    return {"diocese": b["slug"], "built_at": now_iso(), "built_by": "scripts/california/merge.py",
            "match_methods": dict(Counter(r["match"].get("discovermass", {}).get("match_method", "no DiscoverMass match") for r in b["recs"])),
            "site_match_methods": dict(Counter(m["match_method"] for r in b["recs"] for m in r["match"].get("sites", []))),
            "discovermass_not_in_directory": [{"id": d["id"], "name": d["name"], "city": d.get("city"), "url": d["source_url"]} for d in b["dm_unmatched"]],
            "records": out}
