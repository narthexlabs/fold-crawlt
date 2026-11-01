"""Download the latest 1-2 bulletin issues of every California parish (outside Oakland) whose website links a bulletin.

    python3 -I ingest/california/bulletins/fetch.py [--slugs los-angeles,orange] [--limit N] [--workers 10] [--refresh]

Input: the site crawl's bulletin link (data/real/california/sites/<slug>/<site>.json, field `bulletin`) for every site
that scripts/california/merge.py attaches to a parish (data/real/california/merged/<slug>.json, match.sites[].file).
The crawl's cached pages (data/raw/california/sites/<site>/, fetched 2026-10-31) are read first, so a bulletins page
the crawl already saw is not fetched again.

Resolving a link to PDFs
  * direct PDF (parish host, Wix /_files/ugd, GoDaddy wsimg, edl.io, Webflow, e-churchbulletins.com): the link itself,
    plus every other bulletin PDF on the page where it was found; newest dates first (date from the link text, then the
    URL, then the PDF's own first pages).
  * bulletins page: every PDF-like link on it (and on the crawl's other cached bulletin pages).
  * LPi: a container code seen in a link (container.parishesonline.com/bulletins/<dd>/<nnnn>/...) is probed for the last
    5 Sundays at the documented public pattern (ingest/public/bulletins/hosts.py). LPi's own app pages
    (parishesonline.com/organization/..., publicationWidget) need a keyed API and carry no code: recorded, not worked around.
  * eCatholic: files.ecatholic.com/<id>/bulletins/<YYYYMMDD>.pdf from the parish's bulletins page; only when that page
    could not be read are the last 4 Sundays probed at the same pattern for an id the parish published.
  * DiscoverMass/Diocesan: the church page's Bulletins tab (cached DiscoverMass pages first; Crawl-delay 10 honoured).
  * Flocknote: a note (flocknote.com/note/<id>) is read as an HTML issue; other Flocknote links (sign-up, give,
    dashboard) are not bulletins and are recorded as such.
  * Google Drive file links (uc?export=download), Dropbox (dl=1). Drive folders need JavaScript: recorded.

Rules: the HTTP library's plain default User-Agent (fetch_site.http_get), no cookies or JavaScript; robots.txt read per
host (the crawl's cached copy for parish hosts) and Disallow honoured; at most 1 request per second per host and one
request in flight per host (Crawl-delay honoured; container.parishesonline.com 3 s; discovermass.com 10 s; a host asking
more than 60 s is skipped and recorded). A challenge page, 403 or 429 is recorded and that host is not asked again this
run (403 on the two S3 file hosts means "no such file"). TLS always verified.

Outputs (gitignored)
  data/raw/california/bulletins/<slug>/<site>/<date>__<hash>.pdf (+ .txt, .ocr.txt)
  data/raw/california/bulletins/_state/<slug>/<site>.json   per site: links tried, statuses, issues (resumable)
  data/raw/california/bulletins/_http/                      cached HTML responses
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
import urllib.robotparser
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "public" / "bulletins"))
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
import hosts  # noqa: E402  (LPi pattern, recent_sundays, dm_parse, lpi_codes_in)
import fetch_site as fs  # noqa: E402  (http_get with the plain default UA, classify, Pacer, host_of)
from bs4 import BeautifulSoup  # noqa: E402

REAL = ROOT / "data" / "real" / "california"
RAW = ROOT / "data" / "raw" / "california" / "bulletins"
SITES_RAW = ROOT / "data" / "raw" / "california" / "sites"
DM_RAW = ROOT / "data" / "raw" / "california" / "discovermass" / "pages"
HTTP_CACHE = RAW / "_http"
STATE = RAW / "_state"
SLUGS = ["los-angeles", "san-francisco", "fresno", "monterey", "orange", "sacramento", "san-bernardino", "san-diego",
         "san-jose", "santa-rosa", "stockton"]
TODAY = datetime.now(timezone.utc).date()
WANT = 2
PDF_MAX = 45_000_000
HTML_MAX = 3_000_000
OCR_MAX_PAGES = 12
HOST_DELAY = {"container.parishesonline.com": 3.0, "discovermass.com": 10.0, "bulletins.discovermass.com": 10.0}
S3_HOSTS = {"container.parishesonline.com", "files.ecatholic.com"}   # 403 = file not there, not a refusal
MAX_CRAWL_DELAY = 60.0
OCR_SLOTS = threading.Semaphore(2)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(p)


# ------------------------------------------------------------------------------------------------ polite network
class Net:
    """Per-host: robots.txt (Disallow + Crawl-delay), >= 1 s between requests, one request in flight, refusals remembered."""

    def __init__(self, robots_seed: dict[str, str | None]):
        self.pacer = fs.Pacer(1.0)
        self.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.glock = threading.Lock()
        self.robots: dict[str, tuple] = {}
        self.seed = robots_seed                    # host -> robots.txt text the site crawl cached today (or None)
        self.blocked: dict[str, str] = {}
        self.requests = 0
        self.robots_file = RAW / "_robots.json"
        self.robots_cache = load(self.robots_file, {}) or {}

    def _lock(self, host: str) -> threading.Lock:
        with self.glock:
            return self.locks[host]

    def _robots(self, url: str):
        p = urlparse(url)
        host = fs.host_of(url)
        if host in self.robots:
            return self.robots[host]
        text, note = None, ""
        if host in self.seed:
            text, note = self.seed[host], "site crawl's cached robots.txt (2026-10-31)"
        elif host in self.robots_cache:
            text, note = self.robots_cache[host].get("text"), "robots.txt cached by this run"
        else:
            self.pacer.wait(host, 1.0)
            r = fs.http_get(f"{p.scheme or 'https'}://{p.netloc}/robots.txt", timeout=15, max_bytes=300_000)
            self.requests += 1
            if r["status"] == 200 and r["body"] and b"<html" not in r["body"][:600].lower():
                text = r["body"].decode("utf-8", "replace")[:200_000]
            note = f"robots.txt HTTP {r['status'] or r['err']}"
            with self.glock:
                self.robots_cache[host] = {"text": text, "status": r["status"], "err": r["err"], "fetched_at": now_iso()}
                save(self.robots_file, self.robots_cache)
        rp, delay = None, HOST_DELAY.get(host, 1.0)
        if text:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(text.splitlines())
            cd = rp.crawl_delay("*")
            if cd:
                delay = max(delay, float(cd))
        self.robots[host] = (rp, delay, note)
        return self.robots[host]

    def get(self, url: str, max_bytes: int = HTML_MAX, cache: bool = True, timeout: int = 40) -> dict:
        """Returns {url, status, cls, final_url, ctype, body, fetched_at, from_cache}. Never raises."""
        host = fs.host_of(url)
        key = hashlib.sha1(url.encode()).hexdigest()[:20]
        cdir = HTTP_CACHE / host
        if cache and (cdir / f"{key}.json").exists():
            m = load(cdir / f"{key}.json", {})
            body = gzip.decompress((cdir / f"{key}.body.gz").read_bytes()) if (cdir / f"{key}.body.gz").exists() else b""
            return dict(m, body=body, from_cache=True)
        base = {"url": url, "status": None, "final_url": url, "ctype": "", "body": b"", "fetched_at": now_iso(),
                "from_cache": False}
        if host in self.blocked:
            return dict(base, cls="skipped_host_refused_earlier", note=self.blocked[host])
        with self._lock(host):
            rp, delay, _ = self._robots(url)
            if delay > MAX_CRAWL_DELAY:
                return dict(base, cls="crawl_delay_too_long", note=f"Crawl-delay {delay:.0f} s")
            if rp is not None and not rp.can_fetch("*", url):
                return dict(base, cls="robots_disallowed")
            self.pacer.wait(host, delay)
            r = fs.http_get(url, timeout=timeout, max_bytes=max_bytes)
            self.requests += 1
        cls = fs.classify(r)
        if host in S3_HOSTS and cls == "blocked_403":
            cls = "not_found"
        if cls in ("challenge", "blocked_403", "blocked_429"):
            self.blocked[host] = f"{cls} on {url[:120]}"
        if cls == "not_html" and r["body"][:4] == b"%PDF":
            cls = "pdf"
        out = dict(base, status=r["status"], cls=cls, final_url=r["final_url"], ctype=r["ctype"][:80],
                   body=r["body"], truncated=bool(r.get("truncated")), fetched_at=now_iso(),
                   last_modified=(r.get("headers") or {}).get("last-modified"))
        if cache and cls == "ok" and r["body"] and r["body"][:4] != b"%PDF":
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / f"{key}.body.gz").write_bytes(gzip.compress(r["body"], 4))
            save(cdir / f"{key}.json", {k: v for k, v in out.items() if k != "body"})
        return out


# ------------------------------------------------------------------------------------------------ dates
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
ES_MON = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
          "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
MON_WORD = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
D_PATTERNS = [
    ("ymd", re.compile(r"(?<!\d)(20[12]\d)[-_/. ]?(0[1-9]|1[0-2])[-_/. ]?(0[1-9]|[12]\d|3[01])(?!\d)")),
    ("mdy_word", re.compile(rf"\b{MON_WORD}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(20\d\d)\b", re.I)),
    ("dmy_word", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MON_WORD}\.?,?\s+(20\d\d)\b", re.I)),
    ("es", re.compile(r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
                      r"octubre|noviembre|diciembre)(?:\s+(?:de|del)\s+(20\d\d))?", re.I)),
    ("mdy", re.compile(r"(?<![\d/])(0?[1-9]|1[0-2])[-_/.](0?[1-9]|[12]\d|3[01])[-_/.](20\d\d|\d\d)(?![\d/])")),
    ("md_word", re.compile(rf"\b{MON_WORD}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*,?\s*[:\d])", re.I)),
    ("mdy6", re.compile(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(2[4-7])(?!\d)")),
]


def _ok(d: date) -> bool:
    return TODAY - timedelta(days=900) <= d <= TODAY + timedelta(days=14)


def _yearless(m: int, d: int) -> date | None:
    for y in (TODAY.year, TODAY.year + 1, TODAY.year - 1):
        try:
            x = date(y, m, d)
        except ValueError:
            return None
        if TODAY - timedelta(days=330) <= x <= TODAY + timedelta(days=14):
            return x
    return None


def find_dates(s: str, allow_yearless: bool = True) -> list[date]:
    out = []
    s = unquote(s or "")
    for kind, rx in D_PATTERNS:
        if kind in ("md_word",) and not allow_yearless:
            continue
        for m in rx.finditer(s):
            try:
                if kind == "ymd":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif kind == "mdy_word":
                    d = date(int(m.group(3)), MON[m.group(1).lower()[:3]], int(m.group(2)))
                elif kind == "dmy_word":
                    d = date(int(m.group(3)), MON[m.group(2).lower()[:3]], int(m.group(1)))
                elif kind == "es":
                    mo = ES_MON[m.group(2).lower()]
                    d = date(int(m.group(3)), mo, int(m.group(1))) if m.group(3) else _yearless(mo, int(m.group(1)))
                elif kind == "mdy":
                    y = int(m.group(3))
                    d = date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
                elif kind == "mdy6":
                    d = date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
                else:
                    d = _yearless(MON[m.group(1).lower()[:3]], int(m.group(2)))
            except (ValueError, KeyError):
                continue
            if d and _ok(d):
                out.append(d)
        if out:
            return out
    return out


def old_date(*texts: str) -> bool:
    """A full date older than the window (an archive issue): never used to fill in for a missing current one."""
    for t in texts:
        t = unquote(t or "")
        for rx in (D_PATTERNS[0][1], D_PATTERNS[1][1]):
            for m in rx.finditer(t):
                y = int(m.group(1) if rx is D_PATTERNS[0][1] else m.group(3))
                if y < (TODAY - timedelta(days=900)).year:
                    return True
    return False


def date_of(*texts: str) -> str | None:
    """Full dates (any of the texts, in order) before a month-day without a year."""
    for yearless in (False, True):
        for t in texts:
            ds = find_dates(t, allow_yearless=yearless)
            if ds:
                return max(ds).isoformat() if len(set(ds)) <= 2 else None
    return None


def date_from_pdf_text(text: str) -> str | None:
    """Issue date printed in the bulletin: the most frequent plausible date on the first two pages, else None."""
    pages = text.split("\f")[:2]
    c = defaultdict(int)
    for p in pages:
        for d in find_dates(p[:6000], allow_yearless=False):
            c[d] += 1
    if not c:
        return None
    best = sorted(c.items(), key=lambda kv: (-kv[1], abs((kv[0] - TODAY).days)))[0][0]
    return best.isoformat()


# ------------------------------------------------------------------------------------------------ candidate links
PDFISH = re.compile(r"\.pdf(?:$|[?#&])|files\.ecatholic\.com/\d+/bulletins/|container\.parishesonline\.com/bulletins/|"
                    r"drive\.google\.com/(?:file/d/|open\?id=|uc\?)|docs\.google\.com/(?:document|file)/d/|"
                    r"dropbox\.com/.+\.pdf|e-churchbulletins\.com/bulletins/|bulletins\.discovermass\.com/download|"
                    r"flocknote\.com/note/\d+", re.I)
BULL_WORD = re.compile(r"bulletin|boletin|boletín|bolet[ií]n|bulletins|weekly|semanal|주보|bản tin|ban tin|"
                       r"newsletter|announcements|anuncios|sunday|domingo|\bweek\b", re.I)
NOT_BULL = re.compile(r"child|\bkids?\b|niños|ninos|coloring|calendar|registration|register|\bforms?\b|application|"
                      r"flyer|menu|tuition|handbook|annual report|financial|appeal|\bmusic\b|songs|lyrics|missal|"
                      r"worship aid|program(?:me)?\b|policy|policies|brochure|\bmap\b|school|report card|minutes|agenda|"
                      r"safe ?environment|waiver|sponsor|certificate|baptism|wedding|quincea|funeral|flier|poster|"
                      r"volunteer|schedule of|lector schedule|ministry schedule|readings|novena|prayer card|stations|"
                      r"pledge|envelope|donation|giving|tax|census|survey|questionnaire|ocia|rcia|confirmation|"
                      r"first communion|catechism|curriculum|syllabus|lesson|campaign|letter\b|update letter", re.I)
KNOWN_BULL_HOST = re.compile(r"files\.ecatholic\.com/\d+/bulletins/|container\.parishesonline\.com/bulletins/|"
                             r"bulletins\.discovermass\.com|e-churchbulletins\.com/bulletins/", re.I)
LPI_URL = re.compile(r"container\.parishesonline\.com/bulletins/(\d\d)/(\d{4})/(\d{8})B?\.pdf", re.I)
ECATH_URL = re.compile(r"files\.ecatholic\.com/(\d+)/bulletins/(\d{8})\.pdf", re.I)
PDF_IN_TEXT = re.compile(r"https?://[^\s\"'<>()]+?\.pdf(?:\?[^\s\"'<>]*)?", re.I)


def norm_url(u: str) -> str:
    p = urlparse(u)
    q = "&".join(x for x in (p.query or "").split("&") if x and not re.match(r"^(t|v|ver|ts|_)=", x))
    return f"{p.netloc.lower().removeprefix('www.')}{p.path}{'?' + q if q else ''}"


def unwrap(u: str) -> str:
    """Viewer wrappers -> the PDF they show (Google Docs viewer ?url=, pdf.js ?file=, Wix/Weebly ?pdf=)."""
    p = urlparse(u)
    qs = parse_qs(p.query)
    for k in ("url", "file", "pdf", "src"):
        if k in qs and re.search(r"\.pdf|bulletin", qs[k][0], re.I):
            return urljoin(u, qs[k][0])
    return u


def candidates_from_html(body: bytes, base: str, page_rank: int) -> list[dict]:
    html = body.decode("utf-8", "replace")
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for i, tag in enumerate(soup.find_all(["a", "iframe", "embed", "object", "source"])):
        href = tag.get("href") or tag.get("src") or tag.get("data")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        u = unwrap(urljoin(base, href.strip()))
        if not PDFISH.search(u):
            continue
        k = norm_url(u)
        if k in seen:
            continue
        seen.add(k)
        text = tag.get_text(" ", strip=True)[:160] if tag.name == "a" else (tag.get("title") or "")[:160]
        par = tag.find_parent(["li", "p", "div", "td", "tr"])
        ctx = par.get_text(" ", strip=True)[:240] if par else ""
        out.append({"url": u, "text": text, "ctx": ctx, "order": i, "page": base, "page_rank": page_rank})
    for j, m in enumerate(PDF_IN_TEXT.finditer(html)):   # PDFs referenced only in scripts / data attributes
        u = m.group(0).replace("\\/", "/")
        k = norm_url(u)
        if k in seen:
            continue
        seen.add(k)
        out.append({"url": u, "text": "", "ctx": "", "order": 10_000 + j, "page": base, "page_rank": page_rank + 1})
    return out


def is_bulletin(c: dict, bulletin_url_key: str) -> bool:
    blob = f"{c['text']} {unquote(c['url'].rsplit('/', 1)[-1])}"
    if re.search(r"child|\bkids?\b|niños|ninos|coloring|children", blob, re.I):
        return False
    if norm_url(c["url"]) == bulletin_url_key:
        return True
    if KNOWN_BULL_HOST.search(c["url"]):
        return not NOT_BULL.search(c["text"] or "")
    if NOT_BULL.search(blob) and not re.search(r"bulletin|boletin|boletín|주보|bản tin", blob, re.I):
        return False
    if re.search(r"bulletin|boletin|boletín|bolet[ií]n|주보|bản tin|ban-tin", blob + " " + c["url"], re.I):
        return True
    # a PDF on the parish's bulletins page itself: untitled (thumbnails), dated, or titled like an issue
    return c["page_rank"] == 0 and (not re.search(r"[A-Za-z]{3}", c["text"] or "") or bool(c.get("date")) or
                                    bool(re.search(r"week|sunday|domingo|issue|edition|espa[nñ]ol|english|spanish|"
                                                   r"latest|current|this week|download|view|pdf", c["text"], re.I)))


# ------------------------------------------------------------------------------------------------ work list
def work_items(slugs: list[str]) -> list[dict]:
    items: dict[tuple, dict] = {}
    for slug in slugs:
        m = load(REAL / "merged" / f"{slug}.json", {"records": []})
        for r in m["records"]:
            for s in (r.get("match") or {}).get("sites") or []:
                f = ROOT / s["file"]
                site = load(f, {}) or {}
                b = site.get("bulletin")
                if not b or not b.get("url"):
                    continue
                k = (slug, f.stem)
                it = items.setdefault(k, {
                    "slug": slug, "site_key": f.stem, "site_file": s["file"], "domain": site.get("domain"),
                    "home_url": site.get("final_url") or site.get("home_url"), "crawl_status": site.get("crawl_status"),
                    "crawl_env": site.get("crawl_env"), "bulletin": b, "parishes": []})
                dm = ((r.get("match") or {}).get("discovermass") or {}).get("url")
                if r["id"] not in [p["id"] for p in it["parishes"]]:
                    it["parishes"].append({"id": r["id"], "name": r.get("name"), "city": r.get("city"), "dm_url": dm})
    return list(items.values())


def site_cache(site_key: str) -> tuple[dict, dict]:
    """(url -> cached page record, manifest) from the site crawl's cache."""
    mf = load(SITES_RAW / site_key / "manifest.json", {}) or {}
    by = {}
    for p in mf.get("pages") or []:
        if p.get("file") and p.get("class") == "ok":
            for u in (p.get("url"), p.get("final_url")):
                if u:
                    by.setdefault(u, p)
                    by.setdefault(u.rstrip("/"), p)
    return by, mf


def cached_body(site_key: str, rec: dict) -> bytes:
    try:
        return gzip.open(SITES_RAW / site_key / rec["file"]).read()
    except OSError:
        return b""


# ------------------------------------------------------------------------------------------------ download + text
def pdf_text(pdf: Path) -> tuple[Path | None, int]:
    txt = pdf.with_suffix(".txt")
    if not txt.exists():
        try:
            r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, timeout=240)
            txt.write_bytes(r.stdout)
        except subprocess.TimeoutExpired:
            return None, 0
    t = txt.read_text(errors="ignore")
    return txt, t.count("\f") or 1


def ocr_if_needed(pdf: Path, txt: Path) -> tuple[Path, list[int]]:
    """OCR pages with almost no text layer (tesseract eng; flagged `ocr`). Returns (text path to use, pages OCR'd)."""
    o = pdf.with_suffix(".ocr.txt")
    meta = pdf.with_suffix(".ocr.json")
    if o.exists() and meta.exists():
        return o, load(meta, {}).get("pages", [])
    pages = txt.read_text(errors="ignore").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    thin = [i for i, p in enumerate(pages, 1) if len(p.strip()) < 120]
    if not thin or len(thin) < max(1, len(pages) // 3):
        return txt, []
    thin = thin[:OCR_MAX_PAGES]
    done = []
    work = pdf.parent / "_ocr"
    work.mkdir(exist_ok=True)
    with OCR_SLOTS:
        for p in thin:
            stem = work / f"{pdf.stem}_p{p}"
            subprocess.run(["pdftoppm", "-r", "200", "-gray", "-f", str(p), "-l", str(p), "-png", str(pdf), str(stem)],
                           capture_output=True, timeout=240)
            imgs = sorted(work.glob(f"{pdf.stem}_p{p}*.png"))
            if not imgs:
                continue
            try:
                res = subprocess.run(["tesseract", str(imgs[0]), "-", "--psm", "3", "-l", "eng"], capture_output=True,
                                     timeout=300)
                pages[p - 1] = res.stdout.decode("utf-8", "ignore")
                done.append(p)
            except subprocess.TimeoutExpired:
                pass
            for i in imgs:
                i.unlink()
    o.write_text("\f".join(pages))
    save(meta, {"pages": done, "engine": "tesseract eng --psm 3, 200 dpi gray"})
    return o, done


def to_fetchable(u: str) -> str:
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=)([\w-]{20,})", u)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    if "dropbox.com" in u:
        return re.sub(r"([?&])dl=0", r"\1dl=1", u) if "dl=0" in u else u + ("&" if "?" in u else "?") + "dl=1"
    return u


def save_issue(net: Net, it: dict, url: str, date_hint: str | None, via: str) -> dict:
    """Download one candidate; a viewer/attachment HTML page is followed one level to the PDF it shows."""
    d = RAW / it["slug"] / it["site_key"]
    rec = {"url": url, "found_via": via, "date_hint": date_hint}
    r = net.get(to_fetchable(url), max_bytes=PDF_MAX, cache=False, timeout=150)
    rec.update(status=r["status"], cls=r["cls"], fetched_at=r["fetched_at"])
    body = r["body"]
    if r["cls"] == "ok" and "flocknote.com/note/" in url:
        return _save_html_issue(d, rec, body, url)
    if r["cls"] == "ok" and body[:4] != b"%PDF":         # an HTML wrapper page: one level deeper
        inner = [c for c in candidates_from_html(body, r["final_url"] or url, 0) if not NOT_BULL.search(c["text"])]
        if inner:
            rec["wrapper"] = url
            url = inner[0]["url"]
            r = net.get(to_fetchable(url), max_bytes=PDF_MAX, cache=False, timeout=150)
            rec.update(url=url, status=r["status"], cls=r["cls"], fetched_at=r["fetched_at"])
            body = r["body"]
    if body[:4] != b"%PDF":
        rec["ok"] = False
        if rec["cls"] in ("ok", "not_html"):
            rec["cls"] = "not_a_pdf"
        return rec
    if r.get("truncated"):
        rec.update(ok=False, cls="pdf_too_large")
        return rec
    h = hashlib.sha1(body).hexdigest()[:10]
    d.mkdir(parents=True, exist_ok=True)
    pdf = d / f"{date_hint or 'undated'}__{h}.pdf"
    if not pdf.exists():
        pdf.write_bytes(body)
    txt, npages = pdf_text(pdf)
    rec.update(ok=True, sha1=h, bytes=len(body), pdf_path=str(pdf.relative_to(ROOT)), pages=npages)
    if txt is None:
        rec.update(ok=False, cls="pdftotext_timeout")
        return rec
    use, ocr_pages = ocr_if_needed(pdf, txt)
    text = use.read_text(errors="ignore")
    rec.update(text_path=str(use.relative_to(ROOT)), ocr=bool(ocr_pages), ocr_pages=ocr_pages, chars=len(text.strip()))
    printed = date_from_pdf_text(text)
    rec["date_printed"] = printed
    rec["date"] = date_hint or printed
    rec["date_source"] = "link" if date_hint else ("printed in the bulletin" if printed else None)
    return rec


def _save_html_issue(d: Path, rec: dict, body: bytes, url: str) -> dict:
    soup = BeautifulSoup(body.decode("utf-8", "replace"), "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    text = "\n".join(x.strip() for x in soup.get_text("\n").splitlines() if x.strip())
    h = hashlib.sha1(body).hexdigest()[:10]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{rec.get('date_hint') or 'undated'}__{h}.note.txt"
    p.write_text(text)
    printed = date_from_pdf_text(text)
    rec.update(ok=True, sha1=h, bytes=len(body), pdf_path=None, text_path=str(p.relative_to(ROOT)), pages=1, ocr=False,
               ocr_pages=[], chars=len(text), kind="flocknote_note", date_printed=printed,
               date=rec.get("date_hint") or printed,
               date_source="link" if rec.get("date_hint") else ("printed in the note" if printed else None))
    return rec


# ------------------------------------------------------------------------------------------------ one site
def lpi_probe(net: Net, code: str, weeks: int = 5) -> tuple[list[dict], list[dict]]:
    dd, nnnn = code.split("-")
    found, tried = [], []
    for d in hosts.recent_sundays(weeks, TODAY + timedelta(days=1)):
        u = hosts.LPI_CONTAINER.format(dd=dd, nnnn=nnnn, ymd=d.strftime("%Y%m%d"))
        r = net.get(u, max_bytes=1, cache=False, timeout=30)      # existence check only (first bytes)
        tried.append({"url": u, "cls": r["cls"], "status": r["status"]})
        if r["status"] == 200:
            found.append({"url": u, "date": d.isoformat()})
            if len(found) >= WANT:
                break
    return found, tried


def ecatholic_probe(net: Net, eid: str, weeks: int = 4) -> tuple[list[dict], list[dict]]:
    found, tried = [], []
    for d in hosts.recent_sundays(weeks, TODAY + timedelta(days=1)):
        u = f"https://files.ecatholic.com/{eid}/bulletins/{d.strftime('%Y%m%d')}.pdf"
        r = net.get(u, max_bytes=1, cache=False, timeout=30)
        tried.append({"url": u, "cls": r["cls"], "status": r["status"]})
        if r["status"] == 200:
            found.append({"url": u, "date": d.isoformat()})
            if len(found) >= WANT:
                break
    return found, tried


def resolve_site(net: Net, it: dict) -> dict:
    b = it["bulletin"]
    url = b["url"]
    st = {"slug": it["slug"], "site_key": it["site_key"], "site_file": it["site_file"], "domain": it["domain"],
          "parishes": it["parishes"], "bulletin_link": {k: b.get(k) for k in ("url", "kind", "date", "text", "source_url")},
          "crawl_status": it["crawl_status"], "crawl_env": it["crawl_env"], "checked_at": now_iso(),
          "pages_read": [], "issues": [], "notes": []}
    by, mf = site_cache(it["site_key"])
    bkey = norm_url(url)
    cands: list[dict] = []

    def read_page(u: str, rank: int, role: str):
        rec = by.get(u) or by.get(u.rstrip("/"))
        if rec:
            body = cached_body(it["site_key"], rec)
            st["pages_read"].append({"url": u, "role": role, "from": "site crawl cache", "fetched_at": rec.get("fetched_at")})
        else:
            r = net.get(u)
            st["pages_read"].append({"url": u, "role": role, "from": "fetched" if not r["from_cache"] else "run cache",
                                     "cls": r["cls"], "status": r["status"], "fetched_at": r["fetched_at"]})
            if r["cls"] != "ok":
                return r["cls"]
            body = r["body"]
            u = r["final_url"] or u
        cands.extend(candidates_from_html(body, u, rank))
        for code in hosts.lpi_codes_in(body.decode("utf-8", "ignore")):
            st.setdefault("lpi_codes_seen", {}).setdefault(code, u)
        return "ok"

    kind = b.get("kind")
    host = fs.host_of(url)
    # ---- links that are not bulletins at all (sign-up / give / dashboard pages the crawl took for one)
    if "flocknote.com" in host and "/note/" not in url:
        st["status"] = "not_a_bulletin_link"
        st["notes"].append("Flocknote sign-up/give/dashboard page, not a bulletin")
    elif "schoolspeak.com" in host:
        st["status"] = "not_a_bulletin_link"
        st["notes"].append("school newsletter list (SchoolSpeak), not the parish bulletin")
    elif "drive.google.com/drive/folders" in url or ("dropbox.com/sh/" in url):
        st["status"] = "folder_needs_javascript"
        st["notes"].append("shared folder listing is rendered by JavaScript; individual files are not linked")
    if st.get("status"):
        return finish(st)

    # ---- listing pages
    if not PDFISH.search(url) and kind not in ("lpi", "discovermass"):
        res = read_page(url, 0, "bulletin link (page)")
        if res != "ok":
            st["notes"].append(f"bulletin page: {res}")
    elif PDFISH.search(url):
        cands.append({"url": url, "text": b.get("text") or "", "ctx": "", "order": -1, "page": b.get("source_url"),
                      "page_rank": 0})
    if b.get("source_url") and b["source_url"] != url:
        read_page(b["source_url"], 0 if re.search(r"bulletin|boletin", b["source_url"], re.I) else 1, "page the link was on")
    for p in mf.get("pages") or []:
        if p.get("kind") == "bulletin" and p.get("file") and p.get("class") == "ok" and \
                p["url"] not in {x["url"] for x in st["pages_read"]}:
            read_page(p["url"], 0, "crawl's bulletin page")

    # ---- LPi codes: from container links among the candidates or seen on the pages
    codes = dict(st.get("lpi_codes_seen") or {})
    for c in cands:
        m = LPI_URL.search(c["url"])
        if m:
            codes.setdefault(f"{m.group(1)}-{m.group(2)}", c["page"])
    m = LPI_URL.search(url)
    if m:
        codes.setdefault(f"{m.group(1)}-{m.group(2)}", "bulletin link")
    if kind == "lpi" and not codes:
        for f in sorted((SITES_RAW / it["site_key"]).glob("*.gz")):        # every page the crawl cached
            for code in hosts.lpi_codes_in(gzip.open(f).read().decode("utf-8", "ignore")):
                codes.setdefault(code, f"site crawl cache {f.name}")
    picked: list[dict] = []
    for code, where in sorted(codes.items()):
        found, tried = lpi_probe(net, code)
        st.setdefault("lpi_probes", []).append({"code": code, "seen_on": where, "tried": tried,
                                                "found": [x["date"] for x in found]})
        picked += [{"url": x["url"], "date": x["date"], "via": f"LPi container, code {code} seen on {where}"} for x in found]

    # ---- DiscoverMass Bulletins tab (the link itself, or the parish's DiscoverMass page when nothing else resolves)
    def dm_try(dm_url: str):
        slug = dm_url.rstrip("/").rsplit("/", 1)[-1]
        p = DM_RAW / f"{slug}.html"
        if p.exists():
            body, src = p.read_bytes(), "DiscoverMass cache"
        else:
            r = net.get(dm_url)
            body, src = (r["body"] if r["cls"] == "ok" else b""), f"fetched ({r['cls']})"
        st["pages_read"].append({"url": dm_url, "role": "DiscoverMass church page", "from": src})
        if body:
            pg = hosts.dm_parse(body, dm_url)
            for x in pg["bulletins"]:
                if x.get("date"):
                    picked.append({"url": x["url"], "date": x["date"], "via": "DiscoverMass Bulletins tab"})

    if kind == "discovermass":
        dm_try(url)

    # ---- rank page candidates
    if not [c for c in cands if is_bulletin(dict(c, date=None), bkey)]:
        # nothing yet: follow up to 2 same-site links that say "bulletin(s)" (e.g. /bulletin -> /bulletins)
        home_host = fs.host_of(it.get("home_url") or url)
        nxt = []
        for p in list(st["pages_read"]):
            rec = by.get(p["url"]) or by.get(p["url"].rstrip("/"))
            body = cached_body(it["site_key"], rec) if rec else (net.get(p["url"])["body"] if p.get("cls") == "ok" else b"")
            for a in BeautifulSoup(body.decode("utf-8", "replace"), "html.parser").find_all("a", href=True) if body else []:
                u2 = urljoin(p["url"], a["href"]).split("#")[0]
                if fs.host_of(u2) == home_host and re.search(r"bulletin|boletin", u2 + " " + a.get_text(" "), re.I) and \
                        not PDFISH.search(u2) and u2 not in nxt and u2.rstrip("/") not in {x["url"].rstrip("/") for x in st["pages_read"]}:
                    nxt.append(u2)
        for u2 in nxt[:2]:
            read_page(u2, 0, "same-site bulletins link")
    for c in cands:
        c["date"] = date_of(c["text"], unquote(c["url"].rsplit("/", 1)[-1]))
        if not c["date"] and c.get("ctx"):
            ds = set(find_dates(c["ctx"]))
            c["date"] = ds.pop().isoformat() if len(ds) == 1 else None
        if not c["date"]:
            m2 = ECATH_URL.search(c["url"])
            if m2:
                c["date"] = date_of(m2.group(2))
    good = [c for c in cands if is_bulletin(c, bkey) and not LPI_URL.search(c["url"])
            and not (not c["date"] and old_date(c["text"], c["url"].rsplit("/", 1)[-1]))]
    strong = [c for c in good if KNOWN_BULL_HOST.search(c["url"]) or norm_url(c["url"]) == bkey or
              re.search(r"bulletin|boletin|boletín|주보|bản tin", f"{c['text']} {c['url']}", re.I)]
    if strong:                      # the page names its bulletins: ignore its other PDFs (flyers, forms)
        good = strong
    seen = set()
    uniq = []
    for c in sorted(good, key=lambda c: (c["page_rank"], c["order"])):
        k = norm_url(c["url"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    dated = sorted([c for c in uniq if c["date"]], key=lambda c: c["date"], reverse=True)
    undated = [c for c in uniq if not c["date"]]
    ranked = [{"url": c["url"], "date": c["date"], "via": f"link on {c['page']}" + (f" ('{c['text'][:60]}')" if c["text"] else "")}
              for c in dated + undated]
    st["candidates_n"] = len(uniq)

    # ---- eCatholic: probe recent Sundays only when the parish's own bulletins page could not be read
    eids = {m.group(1) for c in cands + [{"url": url}] for m in [ECATH_URL.search(c["url"])] if m}
    page_failed = any(p.get("cls") not in (None, "ok") for p in st["pages_read"])
    if eids and (page_failed or not dated):
        for eid in sorted(eids):
            found, tried = ecatholic_probe(net, eid)
            st.setdefault("ecatholic_probes", []).append({"id": eid, "tried": tried, "found": [x["date"] for x in found]})
            picked += [{"url": x["url"], "date": x["date"], "via": f"eCatholic file pattern, id {eid} published by the parish"}
                       for x in found]

    # merge: LPi/DM/eCatholic picks (dated by the host) with page candidates, newest first, undated last
    allc, seen = [], set()
    for c in sorted([x for x in picked + ranked if x["date"]], key=lambda x: x["date"], reverse=True) + \
            [x for x in ranked if not x["date"]]:
        k = norm_url(c["url"])
        if k not in seen:
            seen.add(k)
            allc.append(c)
    if not allc and not picked:
        for p in it["parishes"]:
            if p.get("dm_url") and kind != "discovermass":
                dm_try(p["dm_url"])
        allc = [x for x in picked]

    # ---- download up to WANT issues: dated links newest first; undated ones (page order) only to fill, then the
    # newest by the date printed in each PDF are kept
    tried_dl = 0
    nok = lambda: len([x for x in st["issues"] if x.get("ok")])  # noqa: E731
    for c in [x for x in allc if x["date"]]:
        if nok() >= WANT or tried_dl >= WANT + 3:
            break
        tried_dl += 1
        st["issues"].append(save_issue(net, it, c["url"], c["date"], c["via"]))
    if nok() < WANT:
        extra = 0
        und = [x for x in allc if not x["date"]]
        if len(und) > WANT + 2:     # many untitled files (Wix): newest by the server's Last-Modified, first chunk only
            for x in und[:14]:
                r = net.get(to_fetchable(x["url"]), max_bytes=1, cache=False, timeout=30)
                x["last_modified"] = r.get("last_modified")
                try:
                    x["_lm"] = datetime.strptime(x["last_modified"], "%a, %d %b %Y %H:%M:%S %Z").isoformat()
                except (TypeError, ValueError):
                    x["_lm"] = ""
            und = sorted(und[:14], key=lambda x: x.get("_lm") or "", reverse=True) + und[14:]
        for c in und:
            if extra >= WANT + 2 or tried_dl >= WANT + 6:
                break
            tried_dl += 1
            rec = save_issue(net, it, c["url"], None, c["via"])
            st["issues"].append(rec)
            extra += bool(rec.get("ok"))
        oks = sorted([x for x in st["issues"] if x.get("ok")], key=lambda x: x.get("date") or "", reverse=True)
        sha = set()
        keep = []
        for x in oks:
            if x.get("sha1") in sha:
                x["kept"] = False
                x["why_not_kept"] = "same file as another link"
                continue
            sha.add(x.get("sha1"))
            keep.append(x)
        for x in keep[WANT:]:
            x["kept"] = False
            x["why_not_kept"] = "older than the newest two"
        for x in st["issues"]:
            if x.get("ok") and x.get("kept") is False:
                x["ok"] = False
    ok = [x for x in st["issues"] if x.get("ok")]
    if ok:
        st["status"] = "ok"
    else:
        clss = [x.get("cls") for x in st["issues"]] + [p.get("cls") for p in st["pages_read"] if p.get("cls")]
        if any(c in ("challenge",) for c in clss):
            st["status"] = "challenge"
        elif any(c in ("blocked_403", "blocked_429", "skipped_host_refused_earlier") for c in clss):
            st["status"] = "blocked"
        elif kind == "lpi" and not codes:
            st["status"] = "lpi_app_no_code"
            st["notes"].append("LPi app page (parishesonline.com) needs a keyed API; no container code is public")
        elif not allc:
            st["status"] = "no_pdf_found"
        else:
            st["status"] = "failed:" + ",".join(sorted({str(c) for c in clss if c}))[:80]
    return finish(st)


def finish(st: dict) -> dict:
    st["finished_at"] = now_iso()
    save(STATE / st["slug"] / f"{st['site_key']}.json", st)
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default=",".join(SLUGS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated site keys")
    a = ap.parse_args()
    slugs = [s for s in a.slugs.split(",") if s and s != "oakland"]
    items = work_items(slugs)
    if a.only:
        keep = set(a.only.split(","))
        items = [x for x in items if x["site_key"] in keep]
    if not a.refresh:
        items = [x for x in items if not (STATE / x["slug"] / f"{x['site_key']}.json").exists()]
    if a.limit:
        items = items[: a.limit]
    seed = {}
    for x in items:
        mf = load(SITES_RAW / x["site_key"] / "manifest.json", {}) or {}
        if "robots" in mf and x.get("home_url"):
            seed[fs.host_of(x["home_url"])] = (mf["robots"] or {}).get("text")
    net = Net(seed)
    print(f"{len(items)} sites to resolve ({sum(len(x['parishes']) for x in items)} parishes)", flush=True)
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(resolve_site, net, it): it for it in items}
        for i, f in enumerate(cf.as_completed(futs), 1):
            it = futs[f]
            try:
                st = f.result()
                ok = [x for x in st["issues"] if x.get("ok")]
                print(f"[{i}/{len(items)}] {it['slug']:15s} {it['site_key'][:38]:38s} {st['status']:22s} "
                      f"issues={len(ok)} {','.join(str(x.get('date')) for x in ok)} ocr={sum(bool(x.get('ocr')) for x in ok)}",
                      flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(items)}] {it['site_key']} ERROR {type(e).__name__}: {e}", flush=True)
                save(STATE / it["slug"] / f"{it['site_key']}.json",
                     {"slug": it["slug"], "site_key": it["site_key"], "site_file": it["site_file"], "domain": it["domain"],
                      "parishes": it["parishes"], "status": f"error:{type(e).__name__}", "issues": [], "pages_read": [],
                      "bulletin_link": it["bulletin"], "checked_at": now_iso()})
    print(f"done in {time.time() - t0:.0f} s; {net.requests} HTTP requests; hosts refused: {len(net.blocked)}")
    save(RAW / "_run_blocked_hosts.json", {"at": now_iso(), "blocked": net.blocked})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
