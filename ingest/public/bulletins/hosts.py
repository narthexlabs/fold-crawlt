"""Fetchers for the two central bulletin / schedule hosts: LPi (parishesonline.com) and Diocesan (discovermass.com).

Written for the Diocese of Oakland pilot, built to run unchanged for all of California (and any US diocese).

robots.txt, checked 2026-10-30 (re-read on every run; the module refuses a URL robots.txt disallows)
  discovermass.com            "User-agent: *  Crawl-delay: 10"  -> no Disallow; one request per 10 s, honoured.
                              The sitemap (wp-sitemap.xml -> wp-sitemap-posts-item-N.xml, ~11 files) lists every
                              church page /church/<name>-<city>-<state>/. Bulletin PDFs linked from those pages are
                              fetched at the same 10 s pace whatever host they sit on, unless that host's own robots
                              says more.
  parishesonline.com          no robots.txt (the path returns the React app's HTML); www. redirects there.
                              The site is a client-rendered app: its search and parish pages call a keyed API
                              (X-API-KEY) that we do not use. So LPi cannot be searched by parish name from here.
  container.parishesonline.com  S3 bucket; robots.txt returns 403 AccessDenied (no rules). Bulletin PDFs live at the
                              documented public pattern /bulletins/<dd>/<nnnn>/<YYYYMMDD>B.pdf. We request only
                              codes we have *seen* (on a parish site, in a file name, in a bulletin's printed LPi job
                              number, or in bulletin_manual.json) and only the last few Sundays: never enumerate codes.
                              Polite delay 3 s.

Rules kept here: plain default User-Agent (python-requests); no cookies, logins, JS execution or bot-check evasion;
a 403/429/challenge page is recorded as such and not retried within the run.

Matching (DiscoverMass): the church slug ends with "-<city>-<state>". A parish matches a page only if the city part
is equal and the name tokens (minus saint/parish/church/catholic/of/the...) are equal or one set contains the other
with at least one distinctive token. More than one candidate, or only a weak one, is logged as ambiguous, never
picked. Confirmed matches from earlier work (mass_times_fill_2026-10-30.json) and data/real/bulletins/dm_overrides.json
win over the matcher.
"""
from __future__ import annotations

import hashlib
import html as htmlmod
import json
import re
import time
import unicodedata
import urllib.robotparser
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
CACHE = RAW / "hosts"
REAL = ROOT / "data" / "real"

SESSION = requests.Session()  # default python-requests User-Agent, nothing spoofed
DEFAULT_DELAY = 3.0
DM_BASE = "https://discovermass.com"
LPI_CONTAINER = "https://container.parishesonline.com/bulletins/{dd}/{nnnn}/{ymd}B.pdf"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------------------------------ polite fetcher
class Polite:
    """Per-host pacing from robots.txt Crawl-delay (else DEFAULT_DELAY), robots Disallow honoured, disk cache."""

    def __init__(self, cache: Path = CACHE, log=print):
        self.cache = cache
        self.last: dict[str, float] = defaultdict(float)
        self.robots: dict[str, tuple[urllib.robotparser.RobotFileParser | None, float, str]] = {}
        self.blocked: set[str] = set()
        self.log = log
        self.requests = 0

    def _robots(self, host: str, scheme: str = "https"):
        if host in self.robots:
            return self.robots[host]
        url = f"{scheme}://{host}/robots.txt"
        note, rp, delay = "", None, DEFAULT_DELAY
        try:
            r = SESSION.get(url, timeout=30)
            self.requests += 1
            self.last[host] = time.time()
            body = r.text if r.status_code == 200 else ""
            if r.status_code == 200 and "<html" not in body[:500].lower():
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.splitlines())
                cd = rp.crawl_delay("*")
                if cd:
                    delay = max(float(cd), 1.0)
                note = f"robots.txt: crawl-delay {cd or 'none'}"
            else:
                note = f"no robots.txt rules (HTTP {r.status_code}{', app HTML' if body else ''})"
        except requests.RequestException as e:
            note = f"robots.txt unreachable ({type(e).__name__}); default pace"
        self.robots[host] = (rp, delay, note)
        return self.robots[host]

    def _paths(self, url: str) -> tuple[Path, Path]:
        h = hashlib.sha1(url.encode()).hexdigest()[:20]
        d = self.cache / urlparse(url).netloc
        return d / f"{h}.body", d / f"{h}.json"

    def cached(self, url: str, max_age_days: float | None = None):
        body, meta = self._paths(url)
        if not (body.exists() and meta.exists()):
            return None
        m = json.loads(meta.read_text())
        if max_age_days is not None:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(m["fetched_at"])
            if age > timedelta(days=max_age_days):
                return None
        return m, body.read_bytes()

    def get(self, url: str, max_age_days: float | None = 7, timeout: int = 60, head_only: bool = False):
        """Return (meta, body bytes). meta: url, final_url, status, content_type, fetched_at, from_cache."""
        c = self.cached(url, max_age_days)
        if c:
            m, b = c
            return dict(m, from_cache=True), b
        p = urlparse(url)
        host = p.netloc
        if host in self.blocked:
            return {"url": url, "status": "skipped: host blocked earlier this run", "fetched_at": now_iso()}, b""
        rp, delay, _ = self._robots(host, p.scheme or "https")
        if rp is not None and not rp.can_fetch("*", url):
            return {"url": url, "status": "disallowed by robots.txt", "fetched_at": now_iso()}, b""
        wait = self.last[host] + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = SESSION.get(url, timeout=timeout, allow_redirects=True, stream=head_only)
            self.requests += 1
            body = b"" if head_only else r.content
            if head_only:
                r.close()
        except requests.RequestException as e:
            self.last[host] = time.time()
            return {"url": url, "status": f"error:{type(e).__name__}", "fetched_at": now_iso()}, b""
        self.last[host] = time.time()
        txt = body[:3000].decode("utf-8", "ignore").lower()
        status = r.status_code
        if status in (403, 429) or "just a moment..." in txt or "cf-challenge" in txt:
            if "container.parishesonline.com" not in host:  # S3 403 = file not there, not a block
                self.blocked.add(host)
                self.log(f"  ! {host} returned {status} / challenge: not retried this run")
        meta = {"url": url, "final_url": r.url, "status": status, "content_type": r.headers.get("content-type", ""),
                "bytes": len(body), "fetched_at": now_iso()}
        if status == 200 and not head_only:
            bp, mp = self._paths(url)
            bp.parent.mkdir(parents=True, exist_ok=True)
            bp.write_bytes(body)
            mp.write_text(json.dumps(meta))
        return dict(meta, from_cache=False), body

    def robots_notes(self) -> dict[str, str]:
        return {h: v[2] for h, v in self.robots.items()}


# ------------------------------------------------------------------------------------------------ name keys
STOP = {"st", "saint", "sts", "saints", "parish", "church", "catholic", "of", "the", "and", "community", "roman",
        "chapel", "mission", "shrine", "basilica", "cathedral", "co", "ca", "de", "la", "el", "los", "our"}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s.replace("'", "")).strip()


def slugify(s: str) -> str:
    return fold(s).replace(" ", "-")


def name_tokens(s: str) -> set[str]:
    s = fold(s)
    s = re.sub(r"\bsaint\b", "st", s)
    return {t for t in s.split() if t not in STOP and len(t) > 1}


# ------------------------------------------------------------------------------------------------ DiscoverMass
def dm_index(f: Polite, state: str = "ca", max_age_days: float = 14) -> list[dict]:
    """Every DiscoverMass church page for a state, from the WordPress sitemaps (≈12 requests, cached)."""
    out_path = CACHE / f"discovermass_index_{state}.json"
    if out_path.exists():
        d = json.loads(out_path.read_text())
        if datetime.now(timezone.utc) - datetime.fromisoformat(d["built_at"]) < timedelta(days=max_age_days):
            return d["churches"]
    _, idx = f.get(f"{DM_BASE}/wp-sitemap.xml", max_age_days=max_age_days)
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", idx.decode("utf-8", "ignore")) if "posts-item" in u]
    churches = []
    for u in maps:
        m, b = f.get(u, max_age_days=max_age_days)
        for loc in re.findall(r"<loc>([^<]+)</loc>", b.decode("utf-8", "ignore")):
            mm = re.search(r"/church/([^/]+)/?$", loc)
            if mm and mm.group(1).endswith("-" + state):
                churches.append({"url": loc, "slug": mm.group(1)})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"built_at": now_iso(), "sitemaps": maps, "churches": churches}))
    return churches


CITY_ALIASES = {"point richmond": ["richmond"], "bay point": ["pittsburg"], "el sobrante": ["richmond"],
                "mission san jose": ["fremont"], "castro valley": ["castro valley"]}


def dm_candidates(name: str, city: str, index: list[dict], state: str = "ca") -> list[dict]:
    cities = [city] + CITY_ALIASES.get(fold(city), [])
    want = name_tokens(name)
    out = []
    for c in cities:
        suf = f"-{slugify(c)}-{state}"
        for ch in index:
            if not ch["slug"].endswith(suf):
                continue
            stem = ch["slug"][: -len(suf)]
            got = name_tokens(stem.replace("-", " "))
            if not got or not want:
                continue
            inter = want & got
            if want == got:
                score = 3
            elif inter and (want <= got or got <= want):
                score = 2
            elif inter:
                score = 1
            else:
                continue
            out.append(dict(ch, score=score, city_used=c, alias=c != city))
    out.sort(key=lambda x: (-x["score"], x["alias"]))
    return out


def dm_match(name: str, city: str, index: list[dict]) -> tuple[str | None, str, list[dict]]:
    """Conservative: (url|None, reason, candidates)."""
    cands = dm_candidates(name, city, index)
    if not cands:
        return None, "no DiscoverMass page with this city and name", []
    best = [c for c in cands if c["score"] == cands[0]["score"] and c["alias"] == cands[0]["alias"]]
    if cands[0]["score"] == 3 and len(best) == 1:
        return best[0]["url"], "exact name + city", cands
    if cands[0]["score"] == 2 and len(best) == 1 and not cands[0]["alias"]:
        return best[0]["url"], "name contained + same city", cands
    return None, f"ambiguous: {len(cands)} candidate(s), best score {cands[0]['score']}", cands


TAGS = re.compile(r"<[^>]+>")


def _text(h: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(TAGS.sub(" ", h))).strip()


def to_hhmm(t: str) -> str | None:
    m = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\.?", t.lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if h == 12:
        h = 0
    if ap == "p":
        h += 12
    return f"{h:02d}:{mi:02d}"


def dm_parse(page: bytes, url: str) -> dict:
    """Name, diocese, website, schedule rows and bulletin links from one DiscoverMass church page."""
    h = page.decode("utf-8", "ignore")
    out = {"url": url, "name": None, "diocese": None, "website": None, "post_id": None, "schedule": [], "bulletins": []}
    m = re.search(r"<title>([^<]+)</title>", h)
    if m:
        out["name"] = htmlmod.unescape(m.group(1)).split("|")[0].split(" - ")[0].strip()
    m = re.search(r"(Diocese of [A-Z][A-Za-z .'-]+|Archdiocese of [A-Z][A-Za-z .'-]+)</span>", h)
    if m:
        out["diocese"] = m.group(1).strip()
    m = re.search(r"class=\"postId\">(\d+)<", h)
    if m:
        out["post_id"] = m.group(1)
    m = re.search(r"consult the <a href='([^']+)'[^>]*>parish website", h)
    if m:
        out["website"] = m.group(1)
    # schedule boxes: <ul><li><h5>Mass Times</h5></li><li><span class="label">Saturday</span><span class='serviceTime'>...
    for box in re.finditer(r"<ul><li><h5>([^<]+)</h5></li>(.*?)</ul>", h, re.S):
        section = box.group(1).strip()
        for li in re.finditer(r"<li class=\"([^\"]*)\"><span class=\"label\">([^<]+)</span>(.*?)</li>", box.group(2), re.S):
            label = htmlmod.unescape(li.group(2)).strip()
            for inner in li.group(3).split("<span class='serviceTime'>")[1:]:
                times = re.findall(r"<span class='time'>([^<]+)</span>", inner)
                comment = re.search(r"<span class='comment'>([^<]+)</span>", inner)
                lang = re.search(r"<span class='language'>([^<]+)</span>", inner)
                prefix = _text(re.split(r"<span class='time'>", inner)[0]).rstrip(":").strip()
                for t in times[:1]:
                    out["schedule"].append({
                        "section": section, "label": label, "day_prefix": prefix or None,
                        "time": to_hhmm(t.split("-")[0]), "time_raw": t.strip(),
                        "comment": htmlmod.unescape(comment.group(1)).strip() if comment else None,
                        "language": htmlmod.unescape(lang.group(1)).strip() if lang else None})
    # bulletins pane
    i = h.find('id="bulletins"')
    if i >= 0:
        j = h.find('class="tab-pane"', i + 20)
        pane = h[i: j if j > 0 else i + 40000]
        if "no bulletins available" not in pane.lower():
            for a in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", pane, re.S):
                href, label = a.group(1), _text(a.group(2))
                if href.startswith("#") or "mailto:" in href:
                    continue
                if re.search(r"\.pdf|bulletin|/b/|flipbook|issuu", href, re.I):
                    out["bulletins"].append({"url": urljoin(url, href), "label": label[:80],
                                             "date": parse_date(label) or parse_date(href)})
    return out


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}


def parse_date(s: str) -> str | None:
    s = s or ""
    m = re.search(r"(20\d\d)[-_/]?(\d\d)[-_/]?(\d\d)", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d\d)", s, re.I)
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2))).isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d|\d\d)\b", s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            pass
    return None


# ------------------------------------------------------------------------------------------------ LPi
LPI_CODE_RES = [
    re.compile(r"container\.parishesonline\.com/bulletins/(\d\d)/(\d{4})/"),
    re.compile(r"(?<![\w-])(\d\d)-(\d{4})_[^/\s\"']*\.pdf", re.I),   # LPi print file names: 05-1934_10-25-26_...pdf
]
LPI_JOB_RE = re.compile(r"\b(?:LPi|LPI|Liturgical Publications)[^\n]{0,80}?\b(\d\d)-(\d{4})\b|\b(\d\d)-(\d{4})\b(?=[^\n]{0,40}"
                        r"(?:LPi|www\.lpi|LPiseniorliving|lpi\.com|4LPi))")


def lpi_codes_in(text: str) -> set[str]:
    out = set()
    for rx in LPI_CODE_RES:
        for m in rx.finditer(text or ""):
            out.add(f"{m.group(1)}-{m.group(2)}")
    for m in LPI_JOB_RE.finditer(text or ""):
        g = [x for x in m.groups() if x]
        if len(g) >= 2:
            out.add(f"{g[0]}-{g[1]}")
    return out


def recent_sundays(n: int = 4, today: date | None = None) -> list[date]:
    d = today or datetime.now(timezone.utc).date() + timedelta(days=1)
    d -= timedelta(days=(d.weekday() + 1) % 7)  # back to Sunday
    return [d - timedelta(weeks=i) for i in range(n)]


def lpi_latest(f: Polite, code: str, want: int = 2, weeks: int = 5) -> tuple[list[dict], list[dict]]:
    """Probe the documented container URL for the last few Sundays of one seen code. Returns (found, tried)."""
    dd, nnnn = code.split("-")
    found, tried = [], []
    for d in recent_sundays(weeks):
        url = LPI_CONTAINER.format(dd=dd, nnnn=nnnn, ymd=d.strftime("%Y%m%d"))
        m, b = f.get(url, max_age_days=None)
        ok = m.get("status") == 200 and b[:4] == b"%PDF"
        tried.append({"url": url, "status": m.get("status")})
        if ok:
            found.append({"url": url, "date": d.isoformat(), "fetched_at": m["fetched_at"], "bytes": len(b)})
            if len(found) >= want:
                break
    return found, tried
