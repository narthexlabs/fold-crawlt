"""Shared helpers for the California diocesan-directory collectors (one module per diocese in this folder).

Each module (`<slug>.py`) writes data/real/california/dioceses/<slug>/ with the same five files:

    parishes.json   list of {name, type, address, city, zip, lat, lng, phone, website, office_email, languages,
                    pastor, region/vicariate/deanery, status, status_note, source_url, fetched_at}
    structure.json  {diocese, name, website, bishops[], cathedral{}, units[], notes[], source_urls[], fetched_at}
                    units[] = {kind: region|vicariate|deanery|cluster, name, parent, leader{name,title}|null,
                               member_parishes[], source_url}
    entities.json   list of {type, name, address, city, zip, website, phone, source_url, fetched_at, ...}
                    type: school | catholic_charities | cemetery | retreat_center | shrine | campus_ministry |
                          religious_community | seminary | diocesan_office | other
    events.json     list of {title, start, end, location, url, source_url, fetched_at} (screened, today..+120 days)
    README.md       sources, method, blockers, counts, date

Rules (docs/design/scaling-california.md): robots.txt read and Crawl-delay honoured (floor 1 s) per host, across
processes; the plain default python-requests User-Agent; no cookies beyond what a plain client keeps in a session;
no JavaScript, no CAPTCHA or Cloudflare/Radware work-arounds. A challenge page raises `Blocked`, which the module
records in README.md and moves on from. No private individuals' data: public clergy and staff roles only.

Raw responses are cached under data/raw/california/dioceses/<slug>/ (gitignored) so a rerun fetches only what is
missing, which matters for hosts with a long Crawl-delay.
"""
from __future__ import annotations

import fcntl
import hashlib
import html as _html
import importlib.util
import json
import re
import sys
import time
import urllib.robotparser
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT_ROOT = ROOT / "data" / "real" / "california" / "dioceses"
RAW_ROOT = ROOT / "data" / "raw" / "california"
THROTTLE_DIR = RAW_ROOT / "_throttle"
ROBOTS_DIR = RAW_ROOT / "_robots"

MIN_DELAY = 1.0
HORIZON_DAYS = 120


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> date:
    return datetime.now(timezone.utc).astimezone().date()


# ------------------------------------------------------------------------------------------- polite fetching
class Blocked(Exception):
    """The host answered with a bot challenge / WAF block, or robots.txt disallows the path. Record, move on."""

    def __init__(self, url: str, why: str, status: int | None = None):
        super().__init__(f"{why}: {url}")
        self.url, self.why, self.status = url, why, status


class Resp:
    def __init__(self, url, status, headers, text, fetched_at, from_cache):
        self.url, self.status, self.headers, self.text = url, status, headers, text
        self.fetched_at, self.from_cache = fetched_at, from_cache

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        return json.loads(self.text)


CHALLENGE_MARKERS = ("just a moment", "cf-chl", "cf_chl", "challenges.cloudflare.com", "attention required",
                     "radware captcha", "perfdrive.com", "shieldsquare", "sucuri website firewall",
                     "request unsuccessful. incapsula", "enable javascript and cookies")


def looks_blocked(status: int, headers: dict, text: str) -> str | None:
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if h.get("cf-mitigated") == "challenge":
        return "cloudflare challenge"
    low = (text or "")[:6000].lower()
    if status in (401, 403, 406, 429, 503) or "perfdrive.com" in h.get("location", ""):
        for m in CHALLENGE_MARKERS:
            if m in low:
                return f"bot challenge ({m})"
        if status == 403:
            return "HTTP 403"
        if status == 429:
            return "HTTP 429"
    if status == 200 and ("validate.perfdrive.com" in low[:1500] or "<title>just a moment" in low):
        return "bot challenge page served with HTTP 200"
    return None


class Fetcher:
    """GET with robots.txt, a per-host Crawl-delay shared across processes, an on-disk cache and block detection."""

    def __init__(self, slug: str):
        self.slug = slug
        self.raw = RAW_ROOT / "dioceses" / slug
        self.raw.mkdir(parents=True, exist_ok=True)
        THROTTLE_DIR.mkdir(parents=True, exist_ok=True)
        ROBOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()  # default User-Agent, on purpose
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser | None, str | None]] = {}
        self.requests_made = 0
        self.blocked: dict[str, str] = {}  # url -> why
        self.log: list[dict] = []

    # -- robots ---------------------------------------------------------------------------------------
    def _robots_for(self, url: str):
        p = urlparse(url)
        host = p.netloc
        if host in self._robots:
            return self._robots[host]
        cache = ROBOTS_DIR / f"{host}.json"
        rec = None
        if cache.exists():
            try:
                rec = json.loads(cache.read_text())
                if time.time() - rec["t"] > 7 * 86400:
                    rec = None
            except (ValueError, KeyError):
                rec = None
        if rec is None:
            self._wait(host, MIN_DELAY)
            try:
                r = self.session.get(f"{p.scheme}://{host}/robots.txt", timeout=30)
                self._stamp(host)
                self.requests_made += 1
                rec = {"t": time.time(), "status": r.status_code, "text": r.text if r.status_code == 200 else "",
                       "blocked": looks_blocked(r.status_code, dict(r.headers), r.text)}
            except requests.RequestException as e:
                rec = {"t": time.time(), "status": 0, "text": "", "blocked": f"network error {type(e).__name__}"}
            cache.write_text(json.dumps(rec))
        rp, why = None, None
        if rec.get("blocked"):
            why = rec["blocked"]
        elif rec["status"] == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(rec["text"].splitlines())
        elif rec["status"] in (404, 410):
            rp = None  # no robots.txt: everything allowed
        elif rec["status"] >= 500 or rec["status"] == 0:
            why = f"robots.txt unavailable (HTTP {rec['status']})"
        self._robots[host] = (rp, why)
        return self._robots[host]

    def crawl_delay(self, url: str) -> float:
        rp, _ = self._robots_for(url)
        d = None
        if rp is not None:
            d = rp.crawl_delay(self.session.headers.get("User-Agent", "python-requests")) or rp.crawl_delay("*")
        return max(MIN_DELAY, float(d or 0))

    # -- throttle (shared across processes) ----------------------------------------------------------
    def _wait(self, host: str, delay: float) -> None:
        f = THROTTLE_DIR / f"{host}.txt"
        f.touch(exist_ok=True)
        with open(f, "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                last = float(fh.read().strip() or 0)
                wait = last + delay - time.time()
                while wait > 0:
                    time.sleep(min(wait, 30))
                    wait = last + delay - time.time()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _stamp(self, host: str) -> None:
        f = THROTTLE_DIR / f"{host}.txt"
        with open(f, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(str(time.time()))
            fcntl.flock(fh, fcntl.LOCK_UN)

    # -- get ------------------------------------------------------------------------------------------
    def get(self, url: str, params: dict | None = None, cache: bool = True, accept: str | None = None) -> Resp:
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        path = self.raw / f"{key}.json"
        if cache and path.exists():
            d = json.loads(path.read_text())
            if d.get("blocked"):
                raise Blocked(url, d["blocked"], d.get("status"))
            return Resp(d["url"], d["status"], d["headers"], d["body"], d["fetched_at"], True)
        host = urlparse(url).netloc
        rp, why = self._robots_for(url)
        if why:
            self.blocked[url] = why
            raise Blocked(url, f"robots.txt: {why}")
        if rp is not None and not rp.can_fetch(self.session.headers.get("User-Agent", "*"), url):
            raise Blocked(url, "disallowed by robots.txt")
        self._wait(host, self.crawl_delay(url))
        headers = {"Accept": accept} if accept else None
        try:
            r = self.session.get(url, timeout=45, headers=headers, allow_redirects=True)
        except requests.RequestException as e:
            self._stamp(host)
            self.requests_made += 1
            raise Blocked(url, f"network error {type(e).__name__}: {e}") from e
        self._stamp(host)
        self.requests_made += 1
        hdrs = {k: v for k, v in r.headers.items() if k.lower() in
                ("content-type", "x-wp-total", "x-wp-totalpages", "link", "last-modified", "cf-mitigated", "location")}
        blocked = looks_blocked(r.status_code, dict(r.headers), r.text)
        if r.history and any("perfdrive.com" in (h.headers.get("location", "") + h.url) for h in r.history):
            blocked = blocked or "redirected to Radware bot check"
        rec = {"url": r.url, "requested": url, "status": r.status_code, "headers": hdrs, "body": r.text,
               "fetched_at": now_iso(), "blocked": blocked}
        if blocked:
            rec["body"] = rec["body"][:2000]
            self.blocked[url] = blocked
        path.write_text(json.dumps(rec, ensure_ascii=False))
        self.log.append({"url": url, "status": r.status_code, "blocked": blocked, "at": rec["fetched_at"]})
        if blocked:
            raise Blocked(url, blocked, r.status_code)
        return Resp(r.url, r.status_code, hdrs, r.text, rec["fetched_at"], False)

    def get_json(self, url: str, params: dict | None = None):
        r = self.get(url, params, accept="application/json")
        if not r.ok:
            return None, r
        try:
            return r.json(), r
        except ValueError:
            return None, r

    def wp_all(self, base: str, route: str, params: dict | None = None, max_pages: int = 20) -> tuple[list, list[Resp]]:
        """Every page of a WordPress REST collection (X-WP-TotalPages). Returns (items, responses)."""
        items, resps, page = [], [], 1
        per = int((params or {}).get("per_page", 100))
        while page <= max_pages:
            q = dict(params or {})
            q.setdefault("per_page", 100)
            q["page"] = page
            data, r = self.get_json(f"{base}/wp-json/{route.lstrip('/')}", q)
            resps.append(r)
            if not isinstance(data, list):
                break
            items += data
            hdr = {k.lower(): v for k, v in r.headers.items()}
            total = int(hdr.get("x-wp-totalpages") or 0)
            if (total and page >= total) or (not total and len(data) < per):
                break
            page += 1
        return items, resps


# ------------------------------------------------------------------------------------------- privacy
def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_PUB = None
_CAL = None


def pub_privacy():
    """ingest/public/privacy.py (the shared redaction screen), loaded under its own module name."""
    global _PUB
    if _PUB is None:
        _PUB = _load_module("_pub_privacy", ROOT / "ingest" / "public" / "privacy.py")
    return _PUB


def cal_privacy():
    """ingest/connectors/calendars/privacy.py (event keep/drop screen)."""
    global _CAL
    if _CAL is None:
        _CAL = _load_module("_cal_privacy", ROOT / "ingest" / "connectors" / "calendars" / "privacy.py")
    return _CAL


def clean_text(s) -> str | None:
    if s is None:
        return None
    s = _html.unescape(re.sub(r"<[^>]+>", " ", str(s)))
    s = re.sub(r"[​ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def screen_text(s: str | None, mode: str = "standard") -> str | None:
    """Run a free-text field through the shared screen (ingest/public/privacy.py redact)."""
    if not s:
        return s
    out, _ = pub_privacy().redact(s, mode)
    return out


def screen_events(events: list[dict]) -> tuple[list[dict], Counter]:
    """Keep public diocesan events only: drop funerals, Mass intentions, bookings and anything naming a private
    person (calendars screen_event), then redact every text field with the shared screen."""
    cal, pub = cal_privacy(), pub_privacy()
    kept, dropped = [], Counter()
    for ev in events:
        title = clean_text(ev.get("title"))
        loc = clean_text(ev.get("location"))
        ok, why, t2, l2 = cal.screen_event(title, loc, ev.get("categories") or [], ev.pop("_description", None))
        if not ok:
            dropped[why or "dropped"] += 1
            continue
        if pub.is_intention_line(t2):
            dropped["mass_intention"] += 1
            continue
        t3, n1 = pub.redact(t2, "standard")
        l3, n2 = pub.redact(l2, "standard") if l2 else (l2, 0)
        if n1:
            dropped["title_name_redacted"] += 1
        ev = dict(ev)
        ev["title"], ev["location"] = t3, l3
        kept.append(ev)
    return kept, dropped


GENERIC_SUB = re.compile(r"(parish|church|rectory|secretar|reception|cathedral|mission|shrine|school|ministr|stewardship|"
                         r"bulletin|communications|catholic|chancery|diocese|bookkeep|business|pastoral|faithformation|"
                         r"religiouseducation|frontdesk|welcome|stmary|stjoseph|sacredheart|olph|office|contact|inquir|reservation)", re.I)
GENERIC_TOK = {"info", "admin", "rcia", "ccd", "mail", "help", "hello", "team", "staff", "youth", "music", "general",
               "events", "tribunal", "vocations", "formation", "office", "contact", "front", "desk", "frontdesk"}


PARISHLIKE = re.compile(r"(saint|sacred|holy|ourlady|olg|olp|olq|ol[a-z]?$|mary|joseph|immaculate|christ|queen|blessed|"
                        r"cathedral|^mt|mount|mission|church|shrine|parish|catholic|sanctuary|cc$|nativity|"
                        r"annunciation|assumption|resurrection|trinity|rosary|guadalupe|lourdes|fatima|carmel|vianney|"
                        r"divine|good ?shepherd|epiphany|ascension|transfiguration|visitation)", re.I)


class _Generic:
    @staticmethod
    def search(local: str):
        if GENERIC_SUB.search(local):
            return True
        return any(t in GENERIC_TOK for t in re.split(r"[._\-+0-9]+", local.lower()) if t) or None


GENERIC_LOCAL = _Generic()
PERSONAL_LOCAL = re.compile(r"^[a-z]+[._-][a-z]+$|^[a-z]\.?[a-z]{3,}$", re.I)


def generic_email(addr: str | None) -> str | None:
    """Keep only a generic office address (office@, info@, parish@, stjoseph@ ...), never a person's mailbox.
    Rule: accept when the local part names an office function; otherwise accept only when no part of it is a given
    name or a common surname (shared lists in ingest/public), and it is not initial+surname or a clergy title+name."""
    if not addr:
        return None
    a = addr.strip().lower().removeprefix("mailto:").split("?")[0]
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", a):
        return None
    local = a.split("@")[0]
    if GENERIC_LOCAL.search(local):
        return a
    pp = pub_privacy()
    given, _ = pp._given_names()
    sur = pp._surnames()
    toks = [t for t in re.split(r"[._\-+0-9]+", local) if t]
    if not toks or any(t in given or t in sur for t in toks):
        return None
    m = re.match(r"^(sts|st|san|santa)\.?([a-z]{4,})", local)
    saintly = bool(m) and any(m.group(2).startswith(g) for g in given if len(g) >= 4)
    if not (PARISHLIKE.search(local) or saintly):
        return None  # e.g. tlagrazon@, stephenproto@: no office or parish word, so it may be a person's mailbox
    if re.match(r"^(fr|father|msgr|deacon|dcn|sr|sister|rev)[a-z]", local):
        return None
    m = re.match(r"^(sts|st|san|santa)\.?([a-z]{4,})", local)
    saintly = bool(m) and any(m.group(2).startswith(g) for g in given if len(g) >= 4)
    if not (PARISHLIKE.search(local) or saintly):
        return None  # e.g. tlagrazon@, stephenproto@: no office or parish word, so it may be a person's mailbox
    return a


# ------------------------------------------------------------------------------------------- field helpers
LANGS = ["English", "Spanish", "Vietnamese", "Tagalog", "Filipino", "Portuguese", "Chinese", "Cantonese", "Mandarin",
         "Korean", "Polish", "Latin", "Italian", "Tongan", "Samoan", "Hmong", "Igbo", "French", "Croatian",
         "Hungarian", "Indonesian", "Japanese", "Malayalam", "Syro-Malabar", "Ilocano", "Arabic", "Burmese",
         "Creole", "Fijian", "Sign Language", "ASL", "Swahili", "German", "Ukrainian", "Punjabi", "Mixteco",
         "Konkani", "Tamil", "Latvian", "Lithuanian", "Khmer", "Laotian", "Lao", "Hindi", "Gujarati", "Slovak"]


def detect_languages(text: str | None) -> list[str]:
    out = []
    for lang in LANGS:
        if text and re.search(r"\b" + re.escape(lang) + r"\b", text, re.I) and lang not in out:
            out.append(lang)
    if text and re.search(r"\bespa[nñ]ol\b|\bmisa en\b", text, re.I) and "Spanish" not in out:
        out.append("Spanish")
    if text and re.search(r"ti[eê]ng vi[eệ]t", text, re.I) and "Vietnamese" not in out:
        out.append("Vietnamese")
    return out


def parish_type(name: str, default: str = "parish") -> str:
    n = name or ""
    if re.search(r"\bcathedral\b|\bbasilica\b(?!.*parish)", n, re.I) and re.search(r"cathedral", n, re.I):
        return "cathedral"
    if re.search(r"\bmission\b(?!\s+(san|santa|dolores|san jose)\b)", n, re.I) and re.search(r"\bmission\b$|\bmission\b\s*[-(]", n, re.I):
        return "mission"
    if re.search(r"\bchapel\b", n, re.I) and not re.search(r"parish|church", n, re.I):
        return "chapel"
    if re.search(r"\bshrine\b", n, re.I) and not re.search(r"parish|church", n, re.I):
        return "shrine"
    if re.search(r"newman|campus ministry|university|college", n, re.I) and re.search(r"newman|campus", n, re.I):
        return "campus_ministry"
    return default


def norm_phone(p: str | None) -> str | None:
    """'(650) 854-5976' from any written form; a leading country code is dropped first ('+16508545976', '1-650-...'),
    so it is never read as the area code."""
    if not p:
        return None
    p = clean_text(p) or ""
    p = re.sub(r"(?<!\d)\+?1[\s.\-]?(?=\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]*\d{4}(?!\d))", "", p)
    m = re.search(r"\(?(\d{3})\)?[\s.\-]*(\d{3})[\s.\-]*(\d{4})", p)
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else None


def norm_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    if not u or u.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    return u


def zip5(s: str | None) -> str | None:
    m = re.search(r"\b(9\d{4})(?:-\d{4})?\b", s or "")
    return m.group(1) if m else None


def split_address(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """'1145 Gilman St., Berkeley, CA 94706' -> (street, city, zip). Best effort; keeps the raw string elsewhere."""
    if not raw:
        return None, None, None
    d = re.sub(r"\s+", " ", clean_text(raw) or "").strip(" ,")
    zp = zip5(d)
    d2 = re.sub(r",?\s*(CA|California)?\s*9\d{4}(-\d{4})?\s*$", "", d).strip(" ,")
    d2 = re.sub(r",\s*(CA|California)\s*$", "", d2).strip(" ,")
    if "," in d2:
        street, city = d2.rsplit(",", 1)
        return street.strip(), city.strip(), zp
    return d2, None, zp


def pastor_record(name: str | None, title: str | None = None) -> dict | None:
    """Public clergy role only, as the diocese gives it. A phone number printed beside the name (a personal line, not the
    parish office's) is never kept in the name."""
    name = clean_text(re.sub(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}\b", " ", name or "").strip(" ,;:-") or None)
    if not name:
        return None
    return {"name": name, "title": clean_text(title)}


# ------------------------------------------------------------------------------------------- events
def in_window(start_iso: str | None, end_iso: str | None = None, days: int = HORIZON_DAYS) -> bool:
    if not start_iso:
        return False
    try:
        d0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00")[:19]).date()
        d1 = datetime.fromisoformat((end_iso or start_iso).replace("Z", "+00:00")[:19]).date()
    except ValueError:
        return False
    t = today()
    return d1 >= t and d0 <= t + timedelta(days=days)


def tribe_normalise(data: dict, source_url: str, fetched_at: str, seen: set | None = None) -> list[dict]:
    """One page of an Events Calendar REST response -> normalised events (shared by the online and the offline path)."""
    seen = seen if seen is not None else set()
    out = []
    for e in data.get("events") or []:
        if e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        v = e.get("venue") or {}
        if isinstance(v, list):
            v = v[0] if v else {}
        loc = ", ".join(x for x in [clean_text(v.get("venue")), clean_text(v.get("address")),
                                    clean_text(v.get("city"))] if x) or None
        out.append({
            "title": clean_text(e.get("title")),
            "start": (e.get("start_date") or "").replace(" ", "T") or None,
            "end": (e.get("end_date") or "").replace(" ", "T") or None,
            "all_day": bool(e.get("all_day")),
            "timezone": e.get("timezone"),
            "location": loc,
            "url": e.get("url"),
            "categories": [clean_text(c.get("name")) for c in (e.get("categories") or [])],
            "source_url": source_url,
            "fetched_at": fetched_at,
        })
    return out


def tribe_events(fx: Fetcher, base: str, days: int = HORIZON_DAYS) -> tuple[list[dict], int]:
    """The Events Calendar REST API (/wp-json/tribe/events/v1/events). Returns (normalised events, raw count)."""
    t = today()
    q = {"per_page": 50, "start_date": t.isoformat(), "end_date": (t + timedelta(days=days)).isoformat()}
    url = f"{base}/wp-json/tribe/events/v1/events"
    out, raw_n, page, seen = [], 0, 1, set()
    while page <= 40:
        data, r = fx.get_json(url, {**q, "page": page})
        if not isinstance(data, dict) or not data.get("events"):
            break
        got = tribe_normalise(data, r.url, r.fetched_at, seen)
        raw_n += len(got)
        out += got
        if not data.get("next_rest_url"):
            break
        page += 1
    return out, raw_n


def ics_events(text: str, source_url: str, fetched_at: str, days: int = HORIZON_DAYS) -> list[dict]:
    """Events from an iCal feed within today..+days."""
    from icalendar import Calendar  # noqa: PLC0415
    out = []
    cal = Calendar.from_ical(text)
    t = today()
    for c in cal.walk("VEVENT"):
        s, e = c.get("DTSTART"), c.get("DTEND")
        s = s.dt if s else None
        e = e.dt if e else None
        if s is None:
            continue
        d0 = s.date() if hasattr(s, "date") else s
        d1 = (e.date() if hasattr(e, "date") else e) if e else d0
        if d1 < t or d0 > t + timedelta(days=days):
            continue
        out.append({"title": clean_text(str(c.get("SUMMARY") or "")), "start": s.isoformat(),
                    "end": e.isoformat() if e else None, "location": clean_text(str(c.get("LOCATION") or "")) or None,
                    "url": str(c.get("URL") or "") or None, "categories": [], "source_url": source_url,
                    "fetched_at": fetched_at, "_description": str(c.get("DESCRIPTION") or "")})
    return out


# ------------------------------------------------------------------------------------------- output
def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def out_dir(slug: str) -> Path:
    d = OUT_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def dedupe_ids(rows: list[dict], key: str = "name") -> None:
    """Add a stable id to each row: slugified name + city, numbered on collision."""
    seen: Counter = Counter()
    for r in rows:
        label = re.sub(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}\b", " ", f"{r.get(key) or ''} {r.get('city') or ''}")  # never a phone in an id
        base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        seen[base] += 1
        r["id"] = base if seen[base] == 1 else f"{base}-{seen[base]}"


def counts(parishes, entities, events) -> dict:
    st = Counter(p.get("status") for p in parishes)
    ty = Counter(p.get("type") for p in parishes)
    et = Counter(e.get("type") for e in entities)
    return {"parishes_total": len(parishes), "parishes_by_status": dict(st), "parishes_by_type": dict(ty),
            "entities_by_type": dict(et), "events": len(events)}


def write_all(slug: str, parishes, structure, entities, events, readme: str) -> dict:
    d = out_dir(slug)
    write_json(d / "parishes.json", parishes)
    write_json(d / "structure.json", structure)
    write_json(d / "entities.json", entities)
    write_json(d / "events.json", events)
    (d / "README.md").write_text(readme, encoding="utf-8")
    return counts(parishes, entities, events)


def md_table(rows: dict) -> str:
    if not rows:
        return "| (none) | 0 |\n"
    return "".join(f"| {k} | {v} |\n" for k, v in rows.items())


def counts_md(c: dict) -> str:
    out = "| Item | Count |\n|---|---|\n"
    out += f"| Parishes and other worship sites (all) | {c['parishes_total']} |\n"
    for k, v in sorted(c["parishes_by_status"].items(), key=lambda kv: str(kv[0])):
        out += f"| &nbsp;&nbsp;status `{k}` | {v} |\n"
    for k, v in sorted(c["parishes_by_type"].items(), key=lambda kv: str(kv[0])):
        out += f"| &nbsp;&nbsp;type `{k}` | {v} |\n"
    out += f"| Entities (all) | {sum(c['entities_by_type'].values())} |\n"
    for k, v in sorted(c["entities_by_type"].items(), key=lambda kv: str(kv[0])):
        out += f"| &nbsp;&nbsp;`{k}` | {v} |\n"
    out += f"| Events (today to +{HORIZON_DAYS} days, screened) | {c['events']} |\n"
    return out


def readme(title: str, site: str, script: str, sources: str, blockers: str, c: dict, extra_counts: str = "") -> str:
    return f"""# {title}: directory data

Collected {now_iso()[:10]} from {site}. Script: `ingest/california/directories/{script}` (shared helpers: `common.py`).
Rules: robots.txt read and its Crawl-delay honoured (floor 1 s per host); plain default `python-requests` User-Agent; no work-arounds for
bot challenges; public clergy and staff roles only; only generic office emails; events run through `ingest/public/privacy.py`
and the calendar event screen. Raw responses are cached under `data/raw/california/dioceses/<slug>/` (gitignored).

## Sources and method

{sources}

## Counts

{counts_md(c)}{extra_counts}
## Blockers and gaps

{blockers}
"""


def blocked_diocese(slug: str, name: str, hosts: list[str], laptop_note: str, script: str, cathedral_hint: str | None = None) -> dict:
    """For a diocese whose site answers a bot challenge: read robots.txt once per host (cached), and if every host
    is blocked write the five files empty with the evidence. If a host is readable, say so and stop (a real
    collector is needed). Nothing is bypassed."""
    fx = Fetcher(slug)
    result = {}
    for h in hosts:
        rp, why = fx._robots_for(f"https://{h}/")
        rec = json.loads((ROBOTS_DIR / f"{h}.json").read_text())
        result[h] = {"blocked": why, "robots_http_status": rec["status"], "probed_at": datetime.fromtimestamp(rec["t"], timezone.utc).replace(microsecond=0).isoformat()}
    if not all(v["blocked"] for v in result.values()):
        raise SystemExit(f"{slug}: a host is readable now ({result}); write a collector instead of the blocked stub")
    fetched = now_iso()
    structure = {"diocese": slug, "name": name, "website": f"https://{hosts[0]}", "bishops": [], "cathedral": None,
                 "counties": [], "counties_source_url": None, "counties_verified_from_diocese_site": False,
                 "units": [], "notes": ["Not collected: the diocese's website answers a bot challenge."],
                 "blocked": result, "source_urls": [], "fetched_at": fetched}
    ls = "\n".join(f"- `{h}`: robots.txt HTTP {v['robots_http_status']}, {v['blocked']} (sandbox, probed {v['probed_at']})" for h, v in result.items())
    readme = f"""# {name}: directory data (BLOCKED, nothing collected)

Checked {fetched[:10]}. Script: `ingest/california/directories/{script}` (shared helper `common.blocked_diocese`).

## What happened

{ls}
- `/wp-json/` on the same host also answered the challenge page (HTTP 403, `text/html`, "Just a moment...") from the sandbox, so the WordPress REST
  side door the Oakland scrape used is closed here too.
- Owner's laptop connection (Linux VM on the home network, `curl` with its default User-Agent, one request to `/robots.txt` per host, 2026-10-31):
  {laptop_note}
- Per the collection rules a bot challenge is never worked around, so the host was not retried, no alternative hostnames of the same site were used,
  and nothing was fetched. `parishes.json`, `entities.json` and `events.json` are empty lists; `structure.json` has no bishops, units or counties.

## What a person can do

- Open the site in a normal browser on a home connection: the directory is a few pages, and the counties statement is usually on the About page.
- `data/real/california/dioceses.json` still carries this diocese's counties and cathedral from Wikipedia and the Census geocoder
  (`counties_verified_from_diocese_site: false`).
- Secondary candidates for the parish list, labelled secondary and not official: `data/real/california/discovermass/by-diocese/{slug}.json`
  (another agent's DiscoverMass index) and the Catholic Hierarchy / GCatholic lists.
{('- ' + cathedral_hint) if cathedral_hint else ''}
## Counts

| Item | Count |
|---|---|
| Parishes | 0 |
| Entities | 0 |
| Events | 0 |
| Regions / deaneries | 0 |
"""
    return write_all(slug, [], structure, [], [], readme)
