"""Turn one fetched parish website (raw cache written by fetch_site.py) into a per-site record.

Reuses the Oakland parsers rather than rewriting them:
  ingest/public/parish_sites.py             visible_lines, mass_block, parse_mass, parse_ministries, bulletin_link, TIME_RE
  ingest/public/groups/build_groups.py      the groups taxonomy (classify), context() for meeting times, clean_name, smart_title
  ingest/public/groups/crawled_mass_schedule.py   rows_from_record: Mass / confession / adoration rows from parsed lines
  ingest/public/privacy.py                  screen() over every free-text field before anything is written

Output of parse_site(): groups, social, bulletin, schedule_rows, cms_guess, template, pages, feed candidates (no
network here). Events and calendar feeds are read by events.py.

Schedules from tables, images, PDFs or OCR mix columns, so such rows are kept but flagged `needs_review`.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[3]
for sub in ("ingest/public", "ingest/public/groups"):
    sys.path.insert(0, str(ROOT / sub))
sys.path.append(str(ROOT / "ingest" / "connectors" / "calendars"))
import build_groups as bg  # noqa: E402
import crawled_mass_schedule as cms_rows  # noqa: E402
import parish_sites as ps  # noqa: E402
from privacy import redact, screen, strip_contacts  # noqa: E402

TEMPLATE_VERSION = "ca-sites/2"  # 2: schedules from the per-platform templates (Phase B, 2026-10-31)

_orig_meets = bg.meets_from_text


def _safe_meets(text: str):
    """build_groups.meets_from_text looks Spanish weekday words up case-sensitively ("Viernes" -> KeyError)."""
    try:
        return _orig_meets(text)
    except KeyError:
        return _orig_meets(text.lower())


bg.meets_from_text = _safe_meets

# ------------------------------------------------------------------------------------------------ pages
JUNK_TAGS = ["script", "style", "noscript", "svg", "template", "iframe-placeholder"]


class Page:
    def __init__(self, rec: dict, body: bytes):
        self.rec, self.kind = rec, rec["kind"]
        self.url = rec.get("final_url") or rec["url"]
        self.req_url = rec["url"]
        self.fetched_at = rec.get("fetched_at")
        self.text = body.decode("utf-8", "replace")
        self._soup = self._lines = self._cells = None

    @property
    def soup(self) -> BeautifulSoup:
        if self._soup is None:
            s = BeautifulSoup(self.text, "lxml")
            for t in s(["script", "style", "noscript", "svg", "template"]):
                t.decompose()
            self._soup = s
        return self._soup

    @property
    def lines(self) -> list[str]:
        if self._lines is None:
            self._lines = ps.visible_lines(self.soup)
        return self._lines

    @property
    def cells(self) -> set[str]:
        """Text of every table cell and row, normalised, to recognise lines that came from a table."""
        if self._cells is None:
            out = set()
            for tag in self.soup.find_all(["td", "th", "tr"]):
                out.add(_n(tag.get_text(" ", strip=True)))
                for st in tag.stripped_strings:
                    out.add(_n(st))
            out.discard("")
            self._cells = out
        return self._cells


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def load_pages(site_dir: Path, m: dict) -> list[Page]:
    pages = []
    for rec in m.get("pages", []):
        if rec.get("class") != "ok" or not rec.get("file") or rec["kind"] in ("tribe_events", "ics", "wp_pages"):
            continue
        try:
            body = gzip.open(site_dir / rec["file"], "rb").read()
        except OSError:
            continue
        pages.append(Page(rec, body))
    return pages


# ------------------------------------------------------------------------------------------------ cms
CMS_RULES = [
    ("ecatholic", r"ecatholic\.com|files\.ecatholic|ecatholiccdn|simpledropdownmenu|quicklinkson|headertextoff"),
    ("catholicwebsite", r'content="catholicwebsite\.com|catholicwebsite\.com/'),
    ("lpi_parishesonline", r"parishesonline\.com/scripts/hostedsites|lpicommunities|hostedsites/org\.asp"),
    ("diocesan", r"cdn\.diocesan\.com|\.diocesan\.com/|diocesan publications"),
    ("parishsoft", r"parishsoft\.com|psfamilysuite"),
    ("wix", r"wixstatic\.com|static\.parastorage\.com|wix\.com/|x-wix-|wixsite\.com|content=\"wix\.com website builder"),
    ("squarespace", r"squarespace\.com|static1\.squarespace|squarespace-cdn|content=\"squarespace"),
    ("weebly", r"weebly\.com|editmysite\.com|weeblycloud"),
    ("godaddy", r"img1\.wsimg\.com|godaddy website builder|websites\.godaddy|secureserver\.net/|content=\"starfield"),
    ("duda", r"static\.cdn-website\.com|dudaone|duda_website|irp\.cdn-website"),
    ("google_sites", r"sites\.google\.com|content=\"google sites\""),
    ("joomla", r"content=\"joomla|/media/jui/|/components/com_"),
    ("drupal", r"content=\"drupal|sites/default/files|drupal\.settings"),
    ("wordpress", r"wp-content|wp-includes|wp-json|content=\"wordpress"),
    ("blogger", r"blogspot\.com|content=\"blogger\""),
]


def cms_guess(pages: list[Page], key: str) -> dict:
    home = next((p for p in pages if p.kind == "home"), pages[0] if pages else None)
    if "parishesonline.com" in key:
        return {"cms": "lpi_parishesonline", "evidence": ["host parishesonline.com"]}
    if key.startswith("sites.google.com"):
        return {"cms": "google_sites", "evidence": ["host sites.google.com"]}
    if not home:
        return {"cms": "unknown", "evidence": []}
    t = home.text[:900000].lower()
    for name, rx in CMS_RULES:
        m = re.search(rx, t)
        if m:
            return {"cms": name, "evidence": [m.group(0)[:60]]}
    return {"cms": "unknown", "evidence": []}


# ------------------------------------------------------------------------------------------------ social
FB_SKIP = {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "tr", "login", "login.php", "policies", "privacy",
           "help", "events", "hashtag", "l.php", "photo", "photo.php", "permalink.php", "story.php", "legal", "ads", "business",
           "marketplace", "gaming", "stories", "reel", "reels", "watch", "fbml", "ajax", "v2.0", "v3.0", "recover", "mobile", "about"}
IG_SKIP = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct", "about", "legal", "web", "developer", "privacy"}
X_SKIP = {"intent", "share", "home", "i", "search", "hashtag", "login", "explore", "settings", "tos", "privacy", "widgets.js"}
LIVE_TEXT = re.compile(r"live[\s_-]*stream|livestream|watch[\s_-]*(?:mass|live|online)|mass[\s_-]*(?:online|live)|online[\s_-]*mass|"
                       r"streaming|virtual[\s_-]*mass|en[\s_-]*vivo|transmisi[oó]n", re.I)
LIVE_HOST = re.compile(r"boxcast\.com|livestream\.com|churchstreaming|subsplash\.com|vimeo\.com/(?:event|live|channels)|"
                       r"dacast\.com|restream\.io|mcn\.live|onetouchlive|parishlive|cloud\.church|streamspot|churchonline|"
                       r"vimeo\.com/\d+", re.I)
SOCIAL_KEYS = ["facebook", "instagram", "youtube", "x", "tiktok", "flocknote"]
MAIL_RE = re.compile(r"mailto:|tel:|javascript:", re.I)


# Accounts a site template links by default, and accounts that are never a parish's own; the merge
# (scripts/california/merge.py social_is_own) also checks that a handle relates to the parish.
TEMPLATE_HANDLES = {"wix", "squarespace", "weebly", "godaddy", "wordpress", "wordpresscom", "teepublic", "elementor", "webflow",
                    "pontifex", "masstime.us", "facebook", "instagram", "twitter", "youtube"}


def social_of(url: str) -> tuple[str, str, bool] | None:
    """(platform, canonical url, is_live) for a social link worth recording, else None."""
    r = _social_of(url)
    if r and r[0] != "youtube_video":
        segs = [x for x in urlparse(r[1]).path.split("/") if x]
        if segs and re.sub(r"[^a-z0-9.]", "", segs[-1].lower().lstrip("@")) in TEMPLATE_HANDLES:
            return None
    return r


def _social_of(url: str) -> tuple[str, str, bool] | None:
    u = urlparse(url)
    h = (u.hostname or "").lower()
    h = h[4:] if h.startswith("www.") else h
    segs = [s for s in u.path.split("/") if s]
    s0 = segs[0].lower() if segs else ""
    live = bool(re.search(r"/live(?:/|$)|/videos/?$|/streams?/?$|live_stream", u.path.lower() + "?" + u.query.lower()))
    if h in ("facebook.com", "m.facebook.com", "web.facebook.com", "fb.com", "fb.me"):
        if not segs or s0 in FB_SKIP:
            return None
        if s0 == "profile.php":
            q = parse_qs(u.query).get("id")
            return ("facebook", f"https://www.facebook.com/profile.php?id={q[0]}", live) if q else None
        if s0 in ("pages", "people", "groups") and len(segs) >= 3:
            return ("facebook", "https://www.facebook.com/" + "/".join(segs[:3]), live)
        if s0 in ("pages", "people", "groups"):
            return None
        return ("facebook", f"https://www.facebook.com/{segs[0]}", live)
    if h in ("instagram.com", "instagr.am"):
        if not segs or s0 in IG_SKIP:
            return None
        return ("instagram", f"https://www.instagram.com/{segs[0]}/", False)
    if h in ("youtube.com", "m.youtube.com", "youtube-nocookie.com", "youtu.be"):
        if h == "youtu.be":
            return None
        if s0.startswith("@"):
            return ("youtube", f"https://www.youtube.com/{segs[0]}", live or (len(segs) > 1 and segs[1] in ("live", "streams")))
        if s0 in ("channel", "c", "user") and len(segs) >= 2:
            return ("youtube", f"https://www.youtube.com/{s0}/{segs[1]}", live or (len(segs) > 2 and segs[2] in ("live", "streams")))
        if s0 == "embed" and "live_stream" in (segs[1] if len(segs) > 1 else ""):
            ch = parse_qs(u.query).get("channel")
            return ("youtube", f"https://www.youtube.com/channel/{ch[0]}", True) if ch else None
        if s0 == "live" and len(segs) >= 2:
            return ("youtube_video", url.split("#")[0], True)
        return None
    if h in ("twitter.com", "x.com", "mobile.twitter.com"):
        if not segs or s0 in X_SKIP or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", segs[0]):
            return None
        return ("x", f"https://x.com/{segs[0]}", False)
    if h == "tiktok.com" or h.endswith(".tiktok.com"):
        if s0.startswith("@"):
            return ("tiktok", f"https://www.tiktok.com/{segs[0]}", False)
        return None
    if h.endswith("flocknote.com"):
        sub = h[: -len("flocknote.com")].rstrip(".")
        if sub and sub not in ("www", "app", "support", "api", "help", "blog", "assets", "cdn"):
            return ("flocknote", f"https://{sub}.flocknote.com", False)
        if s0 and s0 not in ("login", "signin", "about", "pricing", "features", "church", "churches", "resources", "demo", "blog",
                             "support", "login.php", "signup", "assets", "static", "images", "js", "css"):
            return ("flocknote", f"https://flocknote.com/{segs[0]}", False)
        return None
    return None


def extract_social(pages: list[Page], home_url: str) -> tuple[dict, list[dict]]:
    found: dict[str, dict[str, float]] = {k: {} for k in SOCIAL_KEYS}
    live: dict[str, dict] = {}
    base_host = urlparse(home_url).hostname or ""

    def add(url: str, text: str, weight: float, page_url: str) -> None:
        r = social_of(url)
        if r:
            plat, canon, is_live = r
            if plat == "youtube_video":
                live.setdefault(canon, {"url": canon, "label": (text or "YouTube live")[:60], "platform": "youtube", "kind": "video"})
                return
            found[plat][canon] = found[plat].get(canon, 0) + weight
            if is_live and (plat in ("facebook", "youtube")):
                live.setdefault(url.split("#")[0][:200], {"url": url.split("#")[0][:200], "label": (text or f"{plat} live")[:60],
                                                          "platform": plat, "kind": "link"})
            return
        if LIVE_HOST.search(url) or (LIVE_TEXT.search(text or "") and not MAIL_RE.match(url) and not url.lower().endswith(".pdf")):
            if urlparse(url).scheme in ("http", "https"):
                live.setdefault(url.split("#")[0][:200], {"url": url.split("#")[0][:200], "label": (text or "livestream")[:60],
                                                          "platform": (urlparse(url).hostname or "")[:40], "kind": "link"})

    for p in pages:
        wt = 2.0 if p.kind == "home" else 1.0
        for a in p.soup.find_all("a", href=True):
            href = a["href"].strip()
            if MAIL_RE.match(href):
                continue
            full = urljoin(p.url, href)
            txt = a.get_text(" ", strip=True) or a.get("aria-label") or a.get("title") or ""
            add(full, txt[:80], wt, p.url)
        for fr in p.soup.find_all(["iframe", "embed"], src=True):
            add(urljoin(p.url, fr["src"].strip()), "", wt, p.url)
    # JSON-LD / inline scripts on the home page: schema.org sameAs lists
    home = next((p for p in pages if p.kind == "home"), None)
    if home:
        for m in re.finditer(r'"sameAs"\s*:\s*(\[[^\]]*\]|"[^"]+")', home.text):
            for u in re.findall(r'https?:[^"\s\\\]]+', m.group(1).replace("\\/", "/")):
                add(u, "", 1.5, home.url)
        for u in re.findall(r'https?://(?:www\.)?(?:facebook|instagram|twitter|x|tiktok)\.com/[A-Za-z0-9_.@/-]{2,80}',
                            home.text.replace("\\/", "/")):
            add(u, "", 0.5, home.url)
    social = {}
    for plat in SOCIAL_KEYS:
        items = sorted(found[plat].items(), key=lambda kv: -kv[1])
        if items:
            social[plat] = [u for u, _ in items[:3]]
    ls = [v for v in live.values()][:4]
    if ls:
        social["livestream"] = ls
    return social, ls


# ------------------------------------------------------------------------------------------------ bulletin
DATE_IN_URL = re.compile(r"(20\d{2})[-_./]?(0[1-9]|1[0-2])[-_./]?(0[1-9]|[12]\d|3[01])(?!\d)")
DATE_US = re.compile(r"(?<!\d)(0?[1-9]|1[0-2])[-_./](0?[1-9]|[12]\d|3[01])[-_./](20\d{2}|\d{2})(?!\d)")
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DATE_TEXT = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s_-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s_-]+(20\d{2})", re.I)
LPI_RE = re.compile(r"https?://[a-z0-9.-]*parishesonline\.com/[^\s\"'<>]*|https?://[a-z0-9.-]*4lpi\.com/[^\s\"'<>]*", re.I)
ECAT_RE = re.compile(r"https?://files\.ecatholic\.com/(\d+)/bulletins/(\d{8})\.pdf", re.I)
BULLETIN_HOSTS = re.compile(r"jspaluch|paluch|discovermass|diocesan\.com|issuu\.com|calameo|flipsnack|yumpu|parishesonline|4lpi|"
                            r"church-bulletins|churchbulletins|bulletins\.|myparishapp|ministryplatform|smore\.com|mailchi|flocknote", re.I)


def date_of(s: str) -> str | None:
    m = DATE_IN_URL.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = DATE_TEXT.search(s)
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = DATE_US.search(s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def find_bulletin(pages: list[Page]) -> dict | None:
    cands = []
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            href = urljoin(p.url, a["href"].strip())
            if MAIL_RE.match(href) or not href.startswith("http"):
                continue
            txt = a.get_text(" ", strip=True)[:100]
            hay = f"{txt} {href}".lower()
            is_pdf = bool(re.search(r"\.pdf(?:\?|#|$)", href, re.I))
            if not (re.search(r"bulletin|boletin|bulletn", hay) or BULLETIN_HOSTS.search(href)):
                continue
            if not is_pdf and re.search(r"/(?:tag|category|author)/|facebook\.com|instagram\.com|twitter\.com|mailto", href, re.I):
                continue
            kind = ("ecatholic_pdf" if ECAT_RE.search(href) else "pdf" if is_pdf else
                    "lpi" if LPI_RE.search(href) else "paluch" if re.search(r"paluch", href, re.I) else
                    "discovermass" if re.search(r"discovermass|diocesan\.com", href, re.I) else
                    "flipbook" if re.search(r"issuu|calameo|flipsnack|yumpu", href, re.I) else "page")
            d = date_of(href) or date_of(txt)
            cands.append({"url": href.split("#")[0], "date": d, "kind": kind, "text": txt, "source_url": p.url})
        for m in ECAT_RE.finditer(p.text):
            cands.append({"url": m.group(0), "date": date_of(m.group(2)), "kind": "ecatholic_pdf", "text": "", "source_url": p.url})
        for m in re.finditer(r"https?://container\.parishesonline\.com/bulletins/\d+/\d+/(\d{8})B?\.pdf", p.text):
            cands.append({"url": m.group(0), "date": date_of(m.group(1)), "kind": "lpi_pdf", "text": "", "source_url": p.url})
    if not cands:
        return None
    rank = {"ecatholic_pdf": 0, "lpi_pdf": 0, "pdf": 1, "lpi": 2, "paluch": 2, "discovermass": 3, "flipbook": 3, "page": 4}
    cands.sort(key=lambda c: (rank.get(c["kind"], 5), "" if not c["date"] else "~" + "".join(chr(255 - ord(ch)) for ch in c["date"])))
    # newest dated direct file first; otherwise the best bulletin page / service link
    dated = [c for c in cands if c["date"] and c["kind"] in ("ecatholic_pdf", "lpi_pdf", "pdf")]
    best = max(dated, key=lambda c: c["date"]) if dated else cands[0]
    best = dict(best)
    best["text"] = strip_contacts(best.get("text") or "")[:80]
    return best


# ------------------------------------------------------------------------------------------------ schedule
RX_CONF = re.compile(r"confess|reconcil|penance|confesi", re.I)
RX_ADOR = re.compile(r"adorat|exposition|holy hour|adoraci|benediction", re.I)
HOUR_OK = re.compile(r"^(0[5-9]|1\d|2[0-2]):")
DAYN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
RANGE_DAY = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*(?:-|–|—|to|through|thru|until)\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)


LETTER_DAY = {"M": 0, "Mo": 0, "Tu": 1, "W": 2, "We": 2, "Th": 3, "F": 4, "Fr": 4, "Sa": 5, "Su": 6}
# "M-F", "M–Sa", "Tu-F" (capitals only, so "a-m" never reads as a range)
LETTER_RANGE = re.compile(r"(?<![A-Za-z])(Mo|Tu|We|Th|Fr|Sa|Su|M|W|F)\.?\s*(?:-|–|—|thru|through|to)\s*(Mo|Tu|We|Th|Fr|Sa|Su|M|W|F)\.?(?![A-Za-z])")


def _span(a: int, b: int) -> list[str]:
    return [DAYN[(a + i) % 7] for i in range(((b - a) % 7) + 1)]


def day_range(text: str) -> tuple[list[str], str] | None:
    """The day range a line states ("Monday-Saturday", "Mon thru Fri", "M-F"), or None."""
    m = RANGE_DAY.search(text or "")
    if m:
        a = next((i for i, d in enumerate(DAYN) if d.lower().startswith(m.group(1).lower())), None)
        b = next((i for i, d in enumerate(DAYN) if d.lower().startswith(m.group(2).lower())), None)
        if a is not None and b is not None and 1 < len(_span(a, b)) <= 7:
            return _span(a, b), m.group(0)
    m = LETTER_RANGE.search(text or "")
    if m:
        sp = _span(LETTER_DAY[m.group(1)], LETTER_DAY[m.group(2)])
        if 1 < len(sp) <= 7:
            return sp, m.group(0)
    return None


def expand_days(day: str | None, raw: str, context: str | None = None, kind: str = "mass") -> tuple[list[str | None], str | None]:
    """The parser keeps only the first day of "Mon-Sat 8am" / "Weekdays 8am" / "Daily (Mon-Sat)"; spread the row over the
    days the line states, or (for a time on its own line) the heading line the day came from (`context`). "Daily" never
    includes Sunday: a range written with it is read literally ("Daily: M-F" is Monday to Friday); bare "Daily" is
    Monday to Saturday, or Monday to Friday when the line gives Saturday a time of its own."""
    texts = [raw] + ([context] if context and context != raw else [])
    if day is None or day in ("Daily", "Weekday"):
        for t in texts:
            r = day_range(t)
            if r:
                return r
        if day == "Weekday" or (day is None and re.search(r"\bweekdays?\b", raw, re.I)):
            own = [m for m in DAY_TOK.finditer(raw) if not re.search(r"except|\bno\b|\bnot\b|\bbut\b", raw[max(0, m.start() - 14):m.start()], re.I)]
            idx = sorted({i for i in (_day_index(m.group(0)) for m in own) if i is not None})
            if idx and max(idx) <= 4:      # "Weekdays - Monday 12:00 noon", "Weekday-Wednesday 9 am": the named day only
                return [DAYN[i] for i in idx], None
        if day == "Daily" and kind == "adoration":
            return DAYN[:], "Daily"                 # a chapel open daily for adoration: every day
        if day == "Daily":
            sat = re.search(r"\bsat(?:urday)?s?\b|s[aá]bados?", raw, re.I)
            return (DAYN[:5], "Daily (Monday-Friday)") if sat else (DAYN[:6], "Daily (Monday-Saturday)")
        if day == "Weekday" or re.search(r"\bweekdays?\b", raw, re.I):
            return DAYN[:5], "Weekday"
        return [day], None
    for t in texts:
        r = day_range(t)
        if r and r[0][0] == day:
            return r
    return [day], None


# Lines that name something other than a Mass, a confession or adoration: choir and rehearsals, devotions, Communion
# services, the Liturgy of the Hours, office / shop / coffee hours, classes and meetings. A time whose nearest keyword
# in its own clause is one of these is not a service; "choir" alone does not refuse a line that says Mass.
NOT_SERVICE_WORD = re.compile(
    r"rehears\w*|practice|choir|coro\b|schola|communion service|eucharistic service|liturgy service|word and communion|celebration of the word|"
    r"liturgy of the (?:word|hours)|morning prayer|evening prayer|night prayer|vespers|lauds|divine office|office hours|"
    r"office (?:is )?open|coffee|caf[eé]\b|gift shop|book ?store|\bshop\b|bingo|stations of the cross|via crucis|chaplet|"
    r"novena|rosary|rosario|devotion\w*|perpetual help|knights|caballeros|meeting|\bclass(?:es)?\b|bible study|"
    r"church (?:is )?open|open daily|doors open|food (?:pantry|bank)|religious education|faith formation|ocia|rcia", re.I)
SERVICE_WORD = re.compile(r"\bmass(?:es)?\b|\bmisas?\b|\bvigil\b|eucharist(?!ic\s+(?:adoration|exposition))|"
                          r"confess\w*|reconcil\w*|penance|confesi\w*|adorat\w*|adoraci\w*|exposition|holy hour|hora santa|benediction", re.I)
CHOIR_ONLY = re.compile(r"choir|coro\b|schola", re.I)
CLAUSE_CUT = re.compile(r"[;|]|(?<![ap]\.m)(?<!\bst)\.\s+(?=\d|[A-Z][a-z]+s?\s*:)|\s[-–—]\s(?=\d)")
TIME_TOK = re.compile(r"\b(\d{1,2})(?::(\d\d))?\s*([ap])\.?\s*m\b\.?|\bnoon\b", re.I)


def _hhmm(m) -> str:
    if m.group(0).lower() == "noon":
        return "12:00"
    return f"{int(m.group(1)) % 12 + (12 if m.group(3).lower() == 'p' else 0):02d}:{m.group(2) or '00'}"


def clause_kind(raw: str, time: str | None) -> str | None:
    """What the clause holding this time says it is: 'mass', 'confession', 'adoration', 'not_service', or None when the
    clause names nothing ("5:00 PM English Mass. 7:00 PM Spanish Mass. Confessions:" -> mass, mass)."""
    if not raw or not time:
        return None
    toks = [m for m in TIME_TOK.finditer(raw) if _hhmm(m) == (time or "")[:5]]
    if not toks:
        return None
    pos = toks[0].start()
    cuts = [0] + [m.end() for m in CLAUSE_CUT.finditer(raw)] + [len(raw)]
    a = max(c for c in cuts if c <= pos)
    b = min(c for c in cuts if c > pos)
    seg = raw[a:b]
    t0, t1 = pos - a, toks[0].end() - a
    hits = []
    for rx, what in ((NOT_SERVICE_WORD, "not_service"), (SERVICE_WORD, "service")):
        for m in rx.finditer(seg):
            d = m.start() - t1 if m.start() >= t1 else t0 - m.end()
            hits.append((max(d, 0), m.start() < t0, what, m.group(0).lower()))
    if not hits:
        return None
    if all(w == "not_service" and CHOIR_ONLY.fullmatch(g) for _, _, w, g in hits if w == "not_service") and any(
            w == "service" and re.match(r"mass|misa", g) for _, _, w, g in hits):
        hits = [h for h in hits if h[2] == "service"]       # "10:00 AM Mass (choir)": a Mass
    d, _, what, word = min(hits)
    if what == "not_service":
        return "not_service"
    if re.match(r"confess|reconcil|penance|confesi", word):
        return "confession"
    if re.match(r"adorat|adoraci|exposition|holy hour|hora santa|benediction", word):
        return "adoration"
    return "mass"


# A location sub-heading: a line with no time that names a church or centre with its address ("Trung Tam Cong Giao
# (2849 S. White Rd. San Jose, CA 95148):"), or a church name ending in a colon. Rows under one that is not the parish's
# own (no name word in common with it) are another place's schedule and are dropped.
ADDR_IN = re.compile(r"\(\s*\d{1,6}\s+[^)]*\b(?:st|street|rd|road|ave|avenue|blvd|boulevard|dr|drive|way|ln|lane|ct|court|pl|place|"
                     r"hwy|highway|pkwy|parkway)\b\.?[^)]*\)", re.I)
PLACE_WORD = re.compile(r"\b(?:church|chapel|mission|cent(?:er|re)|capilla|iglesia|parish|parroquia|cathedral|shrine|campus|"
                        r"oratory|gi[aá]o x[ứu]|nh[aà] th[ờo])\b", re.I)
SCHEDULE_WORD = re.compile(r"\bmass|misa|schedule|horario|times|confess|reconcil|adorat|th[aá]nh l[eễ]|gi[ờo]\b", re.I)
NAME_STOP = {"st", "saint", "sts", "of", "the", "church", "parish", "catholic", "and", "de", "la", "el", "los", "las", "our", "lady",
             "san", "santa", "mission", "community", "chapel", "center", "centre", "shrine", "cathedral", "holy", "ca", "giao",
             "xu", "duc", "me"}


def is_location_heading(ln: str) -> bool:
    if ps.TIME_RE.search(ln) or len(ln) > 140:
        return False
    if ADDR_IN.search(ln):
        return True
    return bool(re.search(r":\s*$", ln) and PLACE_WORD.search(ln) and not SCHEDULE_WORD.search(ln))


def _name_words(s: str) -> set[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", t).split() if w not in NAME_STOP and len(w) > 2 and not w.isdigit()}


def location_of(lines: list[str], raw: str) -> str | None:
    """The location sub-heading the row's line sits under (None when there is none)."""
    key = _n(raw)[:40]
    idx = next((i for i, ln in enumerate(lines) if key and _n(ln).startswith(key)), None)
    if idx is None:
        return None
    for ln in reversed(lines[max(0, idx - 12):idx]):
        if is_location_heading(ln):
            return ln
    return None


def context_line(lines: list[str], raw: str) -> str | None:
    """For a time on a line with no day of its own, the nearest line above that names a day (where the parser took it)."""
    key = _n(raw)[:40]
    idx = next((i for i, ln in enumerate(lines) if key and _n(ln).startswith(key)), None)
    if idx is None:
        return None
    for ln in reversed(lines[max(0, idx - 6):idx + 1]):
        if ps.DAY_RE.search(ln):
            return ln
    return None


def _block(lines: list[str], rx: re.Pattern) -> str | None:
    best, score = None, 0
    for i, ln in enumerate(lines):
        if not rx.search(ln):
            continue
        win = lines[i:i + 25]
        sc = sum(bool(ps.TIME_RE.search(w)) for w in win)
        if sc > score:
            best, score = win, sc
    if not best or score < 1:
        return None
    last = max(j for j, w in enumerate(best) if ps.TIME_RE.search(w))
    return "\n".join(best[:last + 1])


HOLY_HEAD = re.compile(r"\bholy\s*days?\b|\bd[ií]as?\s+(santos|de\s+precepto)|\bsolemnit", re.I)
DAY_HEAD = re.compile(r"^\s*(sunday|saturday|monday|tuesday|wednesday|thursday|friday|weekdays?|daily|weekend|domingo|s[aá]bado|"
                      r"lunes|martes|mi[eé]rcoles|jueves|viernes|confession|reconciliation|adoration|regular)\b.{0,40}$", re.I)


# A one-off feast's own schedule ("All Souls' Day / Todos los Fieles Difuntos", then "Mon 7:30 AM & 6:00 PM Bilingual
# Mass"): only the line right above the times counts, since such headings are often repeated tab labels.
FEAST_HEAD = re.compile(r"\ball\s+souls|\ball\s+saints|fieles\s+difuntos|todos\s+los\s+santos|d[ií]a\s+de\s+(?:los\s+)?muertos|"
                        r"\bthanksgiving\b|\bchristmas\b|\bnavidad\b|nochebuena|new\s+year|ash\s+wednesday|mi[eé]rcoles\s+de\s+ceniza|"
                        r"holy\s+(?:thursday|week|saturday)|good\s+friday|viernes\s+santo|semana\s+santa|triduum|easter|pascua|"
                        r"immaculate\s+conception|inmaculada|simbang\s+gabi", re.I)


def in_holy_day_block(lines: list[str], raw: str, back: int = 14) -> bool:
    """True when the line a row was read from sits under a "Holy Day(s)" heading (no weekday heading in between), or
    right under a one-off feast's heading (FEAST_HEAD): its times are that schedule, whatever day the parser carried
    into it. Used here and by scripts/california/merge.py on rows already parsed."""
    key = _n(raw)[:40]
    if not key:
        return False
    idx = next((i for i, ln in enumerate(lines) if _n(ln).startswith(key) or key.startswith(_n(ln)[:40]) and len(_n(ln)) > 6), None)
    if idx is None:
        return False
    if idx and FEAST_HEAD.search(lines[idx - 1]) and len(lines[idx - 1]) < 80 and not ps.TIME_RE.search(lines[idx - 1]):
        return True
    for ln in reversed(lines[max(0, idx - back):idx + 1]):
        if HOLY_HEAD.search(ln) and len(ln) < 80:
            return True
        if ln is not lines[idx] and DAY_HEAD.match(ln) and len(ln) < 50:
            return False
    return False


def schedule_rows(pages: list[Page], key: str, home: str, today: str, names: list[str] | None = None,
                  dropped: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(rows, image_hints). Pages are tried in the order mass_times, home, other; every row keeps its page. `names` (the
    parish's names from the input list) tell its own location sub-heading from another place's; `dropped` counts what
    was left out (other_location, not_a_service)."""
    order = sorted(pages, key=lambda p: {"mass_times": 0, "home": 1}.get(p.kind, 2))
    own = set().union(*[_name_words(n) for n in names or []]) if names else set()
    dropped = {} if dropped is None else dropped
    rows, seen = [], set()
    img_hints = []
    for p in order:
        lines = p.lines
        blocks = []
        mb = ps.mass_block(lines)
        if mb:
            blocks.append(mb)
        for rx in (RX_CONF, RX_ADOR):
            b = _block(lines, rx)
            if b and b != mb:
                blocks.append(b)
        got = 0
        for blk in blocks:
            parsed = ps.parse_mass(blk)
            if not parsed:
                continue
            raw_lines = [ln for ln in blk.split("\n") if ln.strip()]
            from_table = sum(1 for ln in raw_lines if _n(ln) in p.cells)
            table_block = from_table >= max(2, len(raw_lines) // 3)
            lang_by = {(x["day"], x["time"]): x.get("language") for x in parsed}
            for r in cms_rows.rows_from_record({"id": key, "website": home,
                                                "mass_schedule": {"raw": blk, "parsed": parsed, "source_url": p.url}},
                                               f"crawled-{today}"):
                ck = clause_kind(r["raw"], r["time"])
                if ck == "not_service":
                    dropped["not_a_service"] = dropped.get("not_a_service", 0) + 1
                    continue
                kind = ck or r["kind"]
                loc = location_of(raw_lines, r["raw"])
                if loc and own and not (_name_words(loc) & own):
                    dropped["other_location"] = dropped.get("other_location", 0) + 1
                    continue
                lang = r["language"] or lang_by.get((r["day"], r["time"]))
                days, rng = expand_days(r["day"], r["raw"], context_line(raw_lines, r["raw"]), kind)
                for dday in days:
                    k = (kind, dday, r["time"], lang)
                    if k in seen:
                        continue
                    seen.add(k)
                    reasons = []
                    if table_block:
                        reasons.append("table")
                    if not HOUR_OK.match(r["time"] or ""):
                        reasons.append("implausible_hour")
                    if dday is None:
                        reasons.append("no_day")
                    row = {"kind": kind, "day": dday, "time": r["time"], "language": lang, "raw": r["raw"],
                           "source_url": p.url, "fetched_at": p.fetched_at,
                           "needs_review": bool(reasons), "review_reason": ",".join(reasons) or None}
                    if kind != r["kind"]:
                        row["kind_parsed"] = r["kind"]
                    if in_holy_day_block(lines, r["raw"]):  # a holy-day schedule, not the day the parser carried in
                        row.update(kind="other", day="Holy days", kind_parsed=kind, day_parsed=dday)
                    if rng and (len(days) > 1 or r["day"] in ("Daily", "Weekday")):
                        row["day_expanded_from"] = rng
                    rows.append(row)
                    got += 1
        if p.kind in ("mass_times", "home") and not got:
            for im in p.soup.find_all("img"):
                hay = f"{im.get('alt', '')} {im.get('src', '')} {im.get('data-src', '')}".lower()
                if re.search(r"mass|schedule|horario|misa|confession|bulletin", hay) and not re.search(
                        r"loving memory|in memoriam|memorial|\brip\b|obituar|funeral", hay):   # a memorial card names a person
                    src = im.get("data-src") or im.get("src") or ""
                    if src and not src.startswith("data:"):
                        img_hints.append({"url": urljoin(p.url, src)[:240], "alt": (im.get("alt") or "")[:80], "page": p.url})
        if len(rows) >= 120:   # a weekday line is now one row per day, so the cap is twice the old 60
            break
    return rows[:120], img_hints[:4]


# ------------------------------------------------------------------------------------------------ row hygiene
# docs/review/us-check-2026-11-01.md (28% slot error on "complete" US records). Applied to every row set, template or
# generic: (1) rows only from schedule text: not blog posts, not sentences, not cancelled/former/one-off notices, not
# baptism/anointing/funeral/wedding clauses; (2) each time bound to its nearest day words in the line itself instead of
# one day carried over or a cross product ("8:30 am Mon, Tues, Wed & Fri and 5:30 pm, Thurs"); (3) qualifiers kept:
# "(Sunday Anticipatory)" stays Saturday, "2nd Thursday" keeps `nth`, month ranges keep `season` (both needs_review).
# has_sunday() is the completeness bar: at least one Sunday or Saturday-vigil Mass.
MONTH_ALT_N = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
BLOG_URL = re.compile(r"/(?:post|posts|blog|blogs|single-post|news-?\d|article|articles)/|/20\d\d/\d\d/|[?&]p=\d", re.I)
# A notice, not a schedule: cancelled / former / changed-from-a-date lines. "beginning / starting / effective" count only with a
# date or "this/next" after them ("Confession is offered Tuesdays beginning at 5:30 pm" is a schedule line, us-check #12).
NOTICE = re.compile(r"\bcancel+ed\b|\bcancel+ation|\bformer(?:ly)?\b|no longer|discontinu|suspend|\binstead\b|\bwill (?:not )?be "
                    r"(?:held|moved|celebrated|changed)|\b(?:beginning|starting|effective)\s+(?:on\s+|this\s+|next\s+|today|tomorrow|"
                    r"(?:" + MONTH_ALT_N + r")\.?\s+\d|\d{1,2}/\d{1,2}|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+(?:" + MONTH_ALT_N +
                    r")\.?\s+\d)|\buntil further|\btemporar|\bthis (?:week|weekend)\b|\bnext (?:week|weekend)\b", re.I)
NOT_MASS_CLAUSE = re.compile(r"baptis|bautiz|bautism|anoint|unci[oó]n|funeral|wedding|matrimon|quincea|rehears", re.I)
SCHED_VOCAB = set("""mass masses misa misas sunday sundays saturday saturdays monday mondays tuesday tuesdays wednesday wednesdays
thursday thursdays friday fridays sun sat mon tue tues wed thu thur thurs fri weekday weekdays weekend daily vigil am pm a m p
noon and or the at in on of to through thru confession confessions reconciliation adoration english spanish espanol español
latin vietnamese polish portuguese korean tagalog chinese french italian schedule times time evening morning liturgy chapel
church holy days day eucharistic every first second third fourth last st sacrament exposition benediction mon-fri also by
appointment anticipated anticipatory sabado sábado domingo lunes martes miercoles miércoles jueves viernes y en la el de
weekly hours horario confesiones adoración adoracion parish main hall upstairs downstairs before after following""".split())
DAY_TOK = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day|days|s|\.|sday|nesday|rsday|urday|urdays|sdays|nesdays|rsdays|r|rs)?\b\.?"
                     r"|\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)\b", re.I)
SP_DAY = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5,
          "sabados": 5, "sábados": 5, "domingo": 6, "domingos": 6}
EN_DAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
LETTER_DAYS = re.compile(r"(?<![A-Za-z])(?<![AaPp]\.)(?<![AaPp] )(M|Tu|W|Th|F|Sa|Su)(?![A-Za-z])")  # not the M of "A.M."/"P.M."
NTH = re.compile(r"\b(1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth|last)\s+(mon|tue|wed|thu|fri|sat|sun)[a-z]*", re.I)
MONTH_ALT = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
SEASON = re.compile(r"\b(?:between|from)?\s*(" + MONTH_ALT + r")\.?\s*(?:-|–|—|to|through|thru|and|until)\s*(" + MONTH_ALT + r")\b", re.I)
SEP_ONLY = re.compile(r"^[\s,&/+.:;]*(?:and|y|&|,|/|-|–|—|to|thru|through)?[\s,&/+.:;]*(?:and|y)?[\s,&/+.:;]*$", re.I)
RANGE_SEP = re.compile(r"^\s*\.?\s*(?:-|–|—|to|thru|through)\s*$", re.I)


def _day_index(tok: str) -> int | None:
    t = tok.lower().rstrip(".")
    if t in SP_DAY:
        return SP_DAY[t]
    return EN_DAY.get(t[:3])


def _clause(raw: str, time: str) -> str:
    toks = [m for m in TIME_TOK.finditer(raw or "") if _hhmm(m) == (time or "")[:5]]
    if not toks:
        return raw or ""
    pos = toks[0].start()
    cuts = [0] + [m.end() for m in CLAUSE_CUT.finditer(raw)] + [len(raw)]
    return raw[max(c for c in cuts if c <= pos):min(c for c in cuts if c > pos)]


def _is_prose(raw: str) -> bool:
    body = raw.split("::", 1)[-1] if "::" in raw else raw
    words = re.findall(r"[A-Za-zÀ-ÿ']+", body)
    other = [w for w in words if w.lower() not in SCHED_VOCAB and not w.lower().startswith(("mass", "confess", "reconcil"))]
    return len(other) >= 9 and len(words) >= 14


def bind_days(line: str) -> dict[str, list[int]] | None:
    """time 'HH:MM' -> day indexes, read from the line's own order of day groups and time groups; None when the line
    names fewer than two days (nothing to rebind) or the pairing is ambiguous."""
    toks = []
    for m in DAY_TOK.finditer(line):
        i = _day_index(m.group(0))
        if i is None:
            continue
        pre = line[max(0, m.start() - 2):m.start()]
        post = line[m.end():m.end() + 14].lower()
        if "(" in pre and re.match(r"\s*(?:anticipat|vigil)", post):
            continue                      # "(Sunday Anticipatory)": a qualifier, not a day
        toks.append((m.start(), m.end(), "D", i))
    for m in LETTER_DAYS.finditer(line):
        if not any(a <= m.start() < b for a, b, _, _ in toks) and re.search(r"[-–,&]|\bthru\b", line[max(0, m.start() - 3):m.end() + 3]):
            toks.append((m.start(), m.end(), "D", {"M": 0, "Tu": 1, "W": 2, "Th": 3, "F": 4, "Sa": 5, "Su": 6}[m.group(1)]))
    for m in TIME_TOK.finditer(line):
        toks.append((m.start(), m.end(), "T", _hhmm(m)))
    toks.sort()
    if sum(1 for t in toks if t[2] == "D") < 2 or not any(t[2] == "T" for t in toks):
        return None
    # groups: consecutive same-type tokens separated only by list words; a day range "Mon - Fri" expands
    groups = []
    for a, b, typ, v in toks:
        if groups and groups[-1][0] == typ and SEP_ONLY.match(line[groups[-1][2]:a]):
            g = groups[-1]
            if typ == "D" and RANGE_SEP.match(line[g[2]:a]):
                g[1].extend(DAYN.index(d) for d in _span(g[1][-1], v)[1:])
            else:
                g[1].append(v)
            g[2] = b
        else:
            groups.append([typ, [v], b])
    out: dict[str, list[int]] = {}
    lead = groups[0][0]
    for i, (typ, vals, _) in enumerate(groups):
        if typ != "T":
            continue
        if lead == "D":
            prev = next((g for g in reversed(groups[:i]) if g[0] == "D"), None)
            ds = prev[1] if prev else None
        else:
            nxt = groups[i + 1] if i + 1 < len(groups) and groups[i + 1][0] == "D" else None
            ds = nxt[1] if nxt else None
        if ds is None:
            return None
        for t in vals:
            out.setdefault(t, [])
            out[t] += [d for d in ds if d not in out[t]]
    return out


def row_hygiene(rows: list[dict], dropped: dict) -> list[dict]:
    def drop(why):
        dropped[why] = dropped.get(why, 0) + 1
    nonblog = any(not BLOG_URL.search(r.get("source_url") or "") for r in rows)
    out, seen, rebound = [], set(), set()
    for r in rows:
        raw = r.get("raw") or ""
        kind = r.get("kind")
        if kind in ("mass", "confession", "adoration"):
            if BLOG_URL.search(r.get("source_url") or ""):
                drop("blog_post"); continue
            if NOTICE.search(raw):
                drop("notice_not_schedule"); continue
            if _is_prose(raw):
                drop("prose"); continue
            if kind == "mass" and NOT_MASS_CLAUSE.search(_clause(raw, r.get("time") or "")):
                drop("not_a_mass"); continue
        r = dict(r)
        t5 = (r.get("time") or "")[:5]
        seg = _clause(raw, t5)
        if r.get("day") == "Sunday" and re.search(r"\(\s*sunday\s+(?:anticipat|vigil)|anticipat", seg, re.I) and \
                not re.search(r"\bsunday\b(?!\s+(?:anticipat|vigil))", re.sub(r"\(\s*sunday\s+(?:anticipat|vigil)\w*\s*\)", "", seg, flags=re.I), re.I):
            r.update(day="Saturday", day_parsed="Sunday")
        b = bind_days(raw) if r.get("day") in DAYN and kind in ("mass", "confession", "adoration") and "-" not in (r.get("time") or "") \
            else None
        if b is not None and t5 in b:
            key = (raw, r.get("source_url"), kind, r.get("language"), t5)
            if key in rebound:
                continue
            rebound.add(key)
            for di in sorted(b[t5]):
                nr = dict(r, day=DAYN[di])
                if nr["day"] != r["day"]:
                    nr["day_parsed"] = r["day"]
                    nr["day_bound_from_line"] = True
                k = (nr["kind"], nr["day"], nr["time"], nr.get("language"), nr.get("location"))
                if k not in seen:
                    seen.add(k); out.append(nr)
            continue
        elif b is not None and t5 not in b:
            pass
        m = NTH.search(raw)
        if m and r.get("day") in DAYN and r["day"].lower().startswith(m.group(2).lower()[:3]):
            r["nth"] = m.group(1).lower()
            r["needs_review"] = True
            r["review_reason"] = ",".join(x for x in [r.get("review_reason"), "nth_of_month"] if x)
        s = SEASON.search(raw)
        if s:
            r["season"] = s.group(0).strip()
            r["needs_review"] = True
            r["review_reason"] = ",".join(x for x in [r.get("review_reason"), "seasonal"] if x)
        k = (r.get("kind"), r.get("day"), r.get("time"), r.get("language"), r.get("location"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def has_sunday(rows: list[dict]) -> bool:
    """The completeness bar: at least one Sunday Mass or Saturday vigil (14:00 or later, or a line saying vigil)."""
    for r in rows:
        if r.get("kind") != "mass":
            continue
        if r.get("day") == "Sunday":
            return True
        if r.get("day") == "Saturday" and ((r.get("time") or "") >= "14:00" or re.search(r"vigil|anticipat", r.get("raw") or "", re.I)):
            return True
    return False


# --- schedule boxes and grids on the home / Mass-times page (us-check re-check 2026-11-01: #11, #18, #20 — 33 of 49 missing
# slots). Two shapes the line-order reader misses: (1) a GRID, day labels in one cell/column and times in the next
# ("SATURDAY:<br>SUNDAY:" | "5:00 PM<br>8:00 AM<br>10:15 AM"), paired by line index inside the same container (a blank
# label line keeps the label above it; equal counts pair n-th with n-th); (2) a BOX, one leaf text container (Wix/Duda text
# block, Elementor column) read on its own, so a "Confessions" heading in a neighbouring box does not carry into it. Used
# only when the template rows hold fewer than two weekend Masses; a box row never replaces a row already found.
GRID_DAY = r"(?:sun|mon|tue|tues|wed|thu|thur|thurs|fri|sat)[a-z]*\.?"
GRID_LABEL = re.compile(rf"^\s*(?:(?:saturday\s+)?vigil|weekdays?|weekends?|daily|{GRID_DAY}(?:\s*(?:-|–|—|&|,|/|and|to|thru|through)\s*{GRID_DAY})*"
                        rf"(?:\s+vigil)?)\s*:?\s*$", re.I)
GRID_TIME = r"(?:\d{1,2}(?::\d\d)?\s*(?:[ap]\.?\s*m\.?|noon)|noon)"
TIME_ONLY = re.compile(rf"^\s*{GRID_TIME}(?:\s*(?:-|–|—|&|,|\+|and|to)\s*{GRID_TIME})*\s*(?:\((?:english|spanish|espa[nñ]ol|latin)\))?\s*$", re.I)
BOX_NOT_LEAF = ["div", "section", "article", "table", "ul", "ol", "main", "header", "footer", "nav", "aside", "form"]
BOX_TAGS = ["div", "section", "article", "td", "li", "p", "h2", "h3", "h4", "h5", "h6"]
HEAD_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
BOX_CONF = re.compile(r"confess|reconcil|penance|confesi|by appointment", re.I)
BOX_MASS = re.compile(r"\bmass(?:es)?\b|\bmisas?\b|\bvigil\b|liturg|worship|schedule|horario", re.I)
# lines a box keeps out: meetings and classes, seasonal / one-off liturgies, non-Catholic service names
BOX_NOT = re.compile(r"stud(?:y|ies)|council|meeting|class(?:es)?\b|zoom|office|\blent|station|christmas|easter|good friday|holy "
                     r"(?:thursday|saturday)|ash wednesday|passion|lord'?s supper|basket|\btbd\b|summer|school|rehears|choir|practice|"
                     r"bingo|dinner|breakfast|\brite i|holy communion|morning prayer|evensong|healing|\bdecember|\bjanuary|"
                     r"thanksgiving|new year|cemeter|funeral|wedding|baptis|anoint", re.I)
TIME_RANGE = re.compile(rf"{GRID_TIME}\s*(?:-|–|—|to|until)\s*{GRID_TIME}|\b\d{{1,2}}(?::\d\d)?\s*(?:-|–|—)\s*{GRID_TIME}", re.I)


def _box_lines_ok(lines: list[str], conf: bool) -> list[str]:
    out = []
    for ln in lines:
        if BOX_NOT.search(ln):
            continue
        if not TIME_TOK.search(ln) and (BOX_CONF.search(ln) or RX_ADOR.search(ln)):
            conf = True                  # a "Confession" sub-heading inside the box: spans below it are confession hours
        elif not TIME_TOK.search(ln) and BOX_MASS.search(ln):
            conf = False
        if TIME_TOK.search(ln) and TIME_RANGE.search(ln) and not conf and not BOX_CONF.search(ln) and not RX_ADOR.search(ln):
            continue                     # "Saturday: 8:30 AM - 10:30 AM": a span (office, adoration, confession), not a Mass
        out.append(ln)
    return out


def _day_time_line(ln: str) -> bool:
    return bool(DAY_TOK.search(ln) and TIME_TOK.search(ln) and len(ln.split()) <= 10)


def _el_lines(el) -> list[str]:
    """Lines of an element as rendered: <br> and block ends break lines; consecutive <br> keep a blank line (grid spacer)."""
    src = re.sub(r"\s+", " ", str(el))
    src = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>", " ", src)
    src = re.sub(r"(?i)<br\b[^>]*>", "\n", src)
    src = re.sub(r"(?i)</(?:p|div|li|tr|h\d|td|th|section|article)>", "\n", src)
    import html as _html
    txt = _html.unescape(re.sub(r"<[^>]+>", "", src)).replace("​", "").replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", x).strip() for x in txt.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _label_line(lines: list[str]) -> bool:
    nb = [x for x in lines if x]
    return bool(nb) and len(nb) <= 10 and all(GRID_LABEL.match(x) for x in nb)


def _time_line(lines: list[str]) -> bool:
    nb = [x for x in lines if x]
    return bool(nb) and len(nb) <= 20 and all(TIME_ONLY.match(x) for x in nb)


def _pair(labels: list[str], times: list[str]) -> list[str] | None:
    lab = [x for x in labels if x]
    tim = [x for x in times if x]
    if len(tim) < len(lab):
        return None
    if len(tim) == len(lab):
        return [f"{a.rstrip(': ')}: {b}" for a, b in zip(lab, tim)]
    out, cur = [], None
    for i, t in enumerate(times):           # line index alignment; a blank label line keeps the label above it
        if i < len(labels) and labels[i]:
            cur = labels[i]
        if t and cur:
            out.append(f"{cur.rstrip(': ')}: {t}")
    return out or None


def _heading_before(el) -> str | None:
    h = el.find_previous(HEAD_TAGS)
    if h is not None:
        t = re.sub(r"\s+", " ", h.get_text(" ", strip=True))
        if t and len(t) <= 80 and not TIME_TOK.search(t):
            return t
    return None


def grid_blocks(soup) -> tuple[list[tuple[str | None, list[str]]], set[int]]:
    """[(heading, ['Day: time', ...])] for label/time grids, and the ids of the grid containers (not read again as boxes)."""
    out, used = [], set()
    for el in soup.find_all(True):
        kids = [c for c in el.children if getattr(c, "name", None)]
        if len(kids) < 2 or len(kids) > 24:
            continue
        kl = [_el_lines(k) for k in kids]
        i = 0
        while i < len(kids) - 1:
            # (a) one label cell then one time cell, lines aligned by index
            if _label_line(kl[i]) and _time_line(kl[i + 1]):
                pr = _pair(kl[i], kl[i + 1])
                if pr:
                    out.append((_heading_before(el), pr)); used.add(id(el)); i += 2; continue
            # (b) a run of n single-label cells then a run of n single-time cells (repeater / flex grid columns)
            j = i
            while j < len(kids) and len([x for x in kl[j] if x]) == 1 and _label_line(kl[j]):
                j += 1
            n = j - i
            if n >= 2 and j + n <= len(kids) and all(len([x for x in kl[j + q] if x]) == 1 and _time_line(kl[j + q]) for q in range(n)):
                labs = [[x for x in kl[i + q] if x][0] for q in range(n)]
                tims = [[x for x in kl[j + q] if x][0] for q in range(n)]
                out.append((_heading_before(el), [f"{a.rstrip(': ')}: {b}" for a, b in zip(labs, tims)])); used.add(id(el))
                i = j + n
                continue
            i += 1
    return out, used


def box_blocks(soup, skip: set[int]) -> list[tuple[str | None, list[str]]]:
    """Leaf text containers that hold a schedule (>= 2 times, or a day and a time), each with its own kind heading."""
    cands = []
    for el in soup.find_all(BOX_TAGS):
        if id(el) in skip or el.find(BOX_NOT_LEAF) is not None or any(id(a) in skip for a in el.parents):
            continue
        cands.append(el)
    ids = {id(e) for e in cands}
    out = []
    for el in cands:
        if el.parent is not None and id(el.parent) in ids:
            continue                       # read the outermost leaf container once
        lines = [x for x in _el_lines(el) if x]
        if not lines or len(lines) > 30 or sum(len(x) for x in lines) > 1500:
            continue
        nt = sum(1 for x in lines if TIME_TOK.search(x))
        nd = sum(1 for x in lines if DAY_TOK.search(x))
        if nt < 2 and not (nt and nd):
            continue
        if not nd:
            continue
        prev = el.find_previous_sibling()
        while prev is not None and not prev.get_text(strip=True).replace("​", ""):
            prev = prev.find_previous_sibling()
        head = None
        if prev is not None and prev.name in HEAD_TAGS + ["p"]:
            t = re.sub(r"\s+", " ", prev.get_text(" ", strip=True))
            if len(t) <= 80 and not TIME_TOK.search(t):
                head = t
        out.append((head, lines))
    return out


class BoxPage:
    """A schedule box or grid read as a page of its own (duck-types Page for schedule_rows)."""
    def __init__(self, p: Page, lines: list[str]):
        self.url, self.req_url, self.fetched_at, self.kind, self.rec = p.url, p.req_url, p.fetched_at, "box", p.rec
        self.lines, self.cells = lines, set()
        self.soup = BeautifulSoup("", "lxml")


def box_rows(pages: list[Page], key: str, home: str, today: str, names: list[str] | None, dropped: dict) -> list[dict]:
    vps = []
    for p in pages:
        if p.kind not in ("home", "mass_times") or BLOG_URL.search(p.url or ""):
            continue
        grids, used = grid_blocks(p.soup)
        for head, lines in grids:
            hd = head if head and re.search(r"mass|misa|confess|reconcil|liturg|schedule", head, re.I) else "Mass Times"
            vps.append(BoxPage(p, [hd] + lines))
        for head, lines in box_blocks(p.soup, used):
            body = " ".join(lines)
            has_mass = bool(re.search(r"\bmass|\bmisa", body, re.I))
            if BOX_CONF.search(body) and not has_mass:
                hd = "Confessions"              # the box says so itself ("... Or by appointment")
            elif head and BOX_CONF.search(head) and not has_mass:
                continue                        # a neighbouring "Confessions" heading: which box it heads is not known
            else:
                hd = "Mass Times"
            lines = _box_lines_ok(lines, hd == "Confessions")
            dt = [ln for ln in lines if _day_time_line(ln)]
            if not dt:
                continue
            if hd == "Mass Times" and not (BOX_MASS.search(" ".join(lines)) or (head and BOX_MASS.search(head)) or len(dt) >= 2):
                continue                        # no Mass word anywhere and not a list of "Day time" lines
            pre = [head] if head and not BOX_CONF.search(head) and not re.search(r"\bmass|misa|schedule", head, re.I) else []
            vps.append(BoxPage(p, [hd] + pre + lines))
    if not vps:
        return []
    out = []
    for vp in vps:
        rows, _ = schedule_rows([vp], key, home, today, names, dropped)
        for r in rows:
            r["review_reason"] = ",".join(x for x in [r.get("review_reason"), "schedule_box"] if x)
        out += rows
    return out


def weekend_masses(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("kind") == "mass" and (r.get("day") == "Sunday" or (
        r.get("day") == "Saturday" and ((r.get("time") or "") >= "14:00" or re.search(r"vigil|anticipat", r.get("raw") or "", re.I)))))


def merge_box_rows(rows: list[dict], pages: list[Page], key: str, home: str, today: str, names: list[str] | None,
                   dropped: dict) -> list[dict]:
    """Template rows plus the box/grid reader's rows when the template found fewer than two weekend Masses. A box row is
    added only for a (kind, day, start time) not already present."""
    if weekend_masses(rows) >= 2:
        return rows
    bdrop: dict = {}
    extra = row_hygiene(tag_locations(box_rows(pages, key, home, today, names, bdrop), pages), bdrop)
    if weekend_masses(extra) <= weekend_masses(rows):
        return rows
    have = {(r.get("kind"), r.get("day"), (r.get("time") or "")[:5]) for r in rows}
    spans = [(r.get("kind"), r.get("day"), r["time"][:5], r["time"][6:11]) for r in rows if "-" in (r.get("time") or "")]
    added = 0
    for r in extra:
        k = (r.get("kind"), r.get("day"), (r.get("time") or "")[:5])
        if k in have or any(a == k[0] and b == k[1] and lo <= k[2] <= hi for a, b, lo, hi in spans):
            continue
        have.add(k)
        rows.append(r)
        added += 1
    if added:
        dropped["box_rows_added"] = dropped.get("box_rows_added", 0) + added
    return rows


# ------------------------------------------------------------------------------------------------ groups
MIN_PAGE_PATH = re.compile(r"ministr|group|societ|organi[sz]ation|involve|serve|apostolate|grupo|liturgical|formation|devotion", re.I)


# --- more page shapes (lane G10, 2026-10-31; docs/review/us-groups-recall-2026-10-31.md). parse_ministries reads <li> and
# headings with a keyword list; parish CMSes also list groups as cards (Wix/Duda/Webflow spans, plain links, an A-Z list of
# links), as one page per group (the group is the page title), or only under a "Ministries" menu. These readers add
# candidates; every candidate still passes build_groups.classify (taxonomy + noise/schedule/fragment screens) and the
# privacy screens below. Sacrament-preparation and adoration items are not taken from these new shapes (they are usually
# sacrament or schedule pages, not groups).
NEW_SHAPE_SKIP_SUB = {"baptism_prep", "first_communion_prep", "confirmation_prep", "marriage_prep", "eucharistic_adoration"}
MENU_HUB = re.compile(r"ministr|\bgroups?\b|organi[sz]ations|get[\s_-]*involved|involvement|societ|apostolat|parish[\s_-]*life|"
                      r"community[\s_-]*life|ministerios|grupos|\bserve\b|\bconnect\b|fellowship|h[ộo]i\s*đo[àa]n|grupy", re.I)
BLOCK_TAGS = ["div", "p", "li", "ul", "ol", "section", "article", "table", "h1", "h2", "h3", "h4", "h5", "h6", "header", "main",
              "aside", "form", "dl"]
NEW_SHAPE_NOT_GROUP = re.compile(r"safe\s*environment|protection|virtus|fingerprint|informa(?:tion|cion)|!|🔗|introduc|"
                                 r"welcome|upcoming|scholarship|\bform\b|certificat|seminar|planning|arrangements|columbarium|"
                                 r"mortuary|\bfaqs?\b|\bhow to\b", re.I)
CARD_STOP = re.compile(r"^(item link|list item\s*\d*|learn more|read more|more info|button|click here|details|view|open|"
                       r"see more|explore|explore more|info)$", re.I)


def _new_shape_ok(name: str) -> bool:
    """A candidate from a card / menu / page title: classify must give a specific taxonomy match (not the generic one-word
    'Music', not the 'ends in ministry' fallback unless it is a named society/guild/club) outside the skipped subcategories."""
    if NEW_SHAPE_NOT_GROUP.search(name):
        return False
    res, _ = bg.classify(name)
    if res is None:
        return False
    _k, _cat, sub, _aud, _lang, _disp, strength = res
    if sub in NEW_SHAPE_SKIP_SUB:
        return False
    if sub == "funeral_ministry" and not re.search(r"ministr|bereave|consol|luncheon|reception|guild|team|choir|greeter|"
                                                   r"server|apostolate|committee", name, re.I):
        return False                      # "Funerals" / "Funeral Planning" is a sacrament page, not a group
    if strength == "fallback":            # named parish societies only (school clubs / "Fellowship" headings are not)
        return bool(re.search(r"(society|guild|apostolate|sodality|confraternity)$", bg.norm(name)))
    return True


def _cand_text(t: str) -> str | None:
    t = re.sub(r"\s+", " ", t or "").replace("\u200b", "").strip(" :–-•|>»›")
    t = re.sub(r"\s*Item Link(?: List Item \d+)?$", "", t).strip()
    if not (3 < len(t) < 60) or CARD_STOP.match(t) or ps.MIN_JUNK_RE.search(t) or ps.MIN_SENTENCE_RE.search(t):
        return None
    if t.islower():                       # card titles set in lower case by CSS ("altar guild")
        t = bg.smart_title(t)
    return t


def card_items(p: Page) -> list[str]:
    """Short leaf texts of a ministry page's main content (cards, link lists, list widgets) that classify as groups.
    Kept only when the page gives at least two, so a lone match in a news blurb does not count."""
    s = ps.strip_chrome(BeautifulSoup(str(p.soup), "lxml"))
    out, seen = [], set()
    for el in s.find_all(["a", "span", "div", "p", "strong", "b", "dt", "button", "h1", "h2", "h3", "h4", "h5", "h6"]):
        if el.find(BLOCK_TAGS) is not None:
            continue
        t = _cand_text(el.get_text(" ", strip=True))
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        if _new_shape_ok(t):
            out.append(t)
    return out[:80] if len(out) >= 2 else []


def page_title_item(p: Page) -> str | None:
    """A page about one group: its <h1> (or <title> before the site name) names a group."""
    cands = []
    h1 = p.soup.find("h1")
    if h1:
        cands.append(h1.get_text(" ", strip=True))
    if p.soup.title and p.soup.title.string:
        cands.append(re.split(r"\s+[|–—-]\s+|\s*\|\s*", p.soup.title.string.strip())[0])
    for c in cands:
        t = _cand_text(c)
        if t and _new_shape_ok(t):
            return t
    return None


def menu_items(home: Page) -> list[str]:
    """Group names listed only in the site menu, under a top item such as Ministries / Get Involved / Groups / Parish Life."""
    out, seen = [], set()
    for li in home.soup.find_all("li"):
        sub = li.find(["ul", "ol"])
        if sub is None:
            continue
        head = ""
        for ch in li.children:
            if getattr(ch, "name", None) in ("ul", "ol") or (getattr(ch, "name", None) is not None and ch.find(["ul", "ol"]) is not None):
                break                     # the submenu, directly or inside a wrapper div (Weebly, Elementor)
            head += " " + (ch.get_text(" ", strip=True) if hasattr(ch, "get_text") else str(ch))
        if not MENU_HUB.search(head) or len(head) > 60:
            continue
        for a in sub.find_all("li"):
            if a.find(["ul", "ol"]) is not None:
                continue
            t = _cand_text(a.get_text(" ", strip=True))
            if t and t.lower() not in seen and _new_shape_ok(t):
                seen.add(t.lower())
                out.append(t)
    return out[:60]


# --- multi-church schedules (us-check 2026-11-01 #11): keep the church / location the page labels on each row. The templates
# tag a row "[<church>] ..." when the input list names two or more churches; otherwise the page's own location headings
# ("St. Mary Church (12 Main St):", "Holy Cross Chapel:") are used, but only on a page that labels two or more locations.
CHURCH_TAG = re.compile(r"^\[([^\]]{3,80})\]\s*")


def _loc_label(ln: str) -> str:
    t = ADDR_IN.sub("", ln)
    t = re.sub(r"\s+", " ", t).strip(" :–—-•|")
    return t[:80]


def tag_locations(rows: list[dict], pages: list[Page]) -> list[dict]:
    by_url = {p.url: p for p in pages}
    heads: dict[str, list[str]] = {}
    for r in rows:
        if r.get("location"):
            continue
        m = CHURCH_TAG.match(r.get("raw") or "")
        if m:
            r["location"] = m.group(1).strip()
            continue
        p = by_url.get(r.get("source_url"))
        if p is None:
            continue
        if p.url not in heads:
            heads[p.url] = list(dict.fromkeys(_loc_label(ln) for ln in p.lines if is_location_heading(ln)))
        if len(heads[p.url]) < 2:
            continue
        loc = location_of(p.lines, r.get("raw") or "")
        if loc:
            r["location"] = _loc_label(loc)
    return rows


# --- groups recall (us-check 2026-11-01: precision 91%, recall 37%). Ministry hubs also list groups as tables
# ("ministry / contact / phone": column 1 only), as "Organization - Contact person (phone)" lines, and only in a nav submenu
# under Ministries / Get Involved / Parish Life. Person names and phone numbers are stripped from the candidate, never kept.
# Every candidate still passes _new_shape_ok (taxonomy) and the privacy screens in groups_for.
PHONE_FRAG = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b|\b\d{3}[\s.-]\d{4}\b|\bext\.?\s*\d+", re.I)
HONORIFIC = r"(?:Mr|Mrs|Ms|Miss|Mx|Dr|Fr|Father|Sr|Sister|Br|Brother|Dcn|Deacon|Rev|Msgr)"
PERSON_FRAG = re.compile(rf"\b{HONORIFIC}\.?\s+(?:&\s*{HONORIFIC}\.?\s+)?[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*){{0,3}}")
DANGLING_HON = re.compile(rf"\s+{HONORIFIC}\.?\s*$")
PERSONISH = re.compile(rf"^(?:{HONORIFIC}\.?\s+)?[A-Z][a-z'’.-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][\w'’-]+){{1,2}}$")
TABLE_HEAD = re.compile(r"^(?:ministry|ministries|organi[sz]ations?|group|groups|name|committee|contact)\b", re.I)
# link labels / hub headings / form links that read like groups (us-check precision misses)
GROUP_LABEL_JUNK = re.compile(r"brochure|\bforms?\b|switch\s+language|select\s+language|translat|\bcomponents\b|^ministries\b|"
                              r"\bministries\s+(?:with|for|of|at|in)\b|sign[\s-]*up|\bregist(?:er|ration)\b|\bpdf\b|\bflyer\b|"
                              r"\bnewsletter\b|\bapplication\b|requirements?\b|\?$|^preparing\b|^sacrament(?:s)?\s+of\b|"
                              r"^(?:pre-?k|k|\d{1,2}(?:st|nd|rd|th)?)\s*(?:-|–|to)\s*\d{1,2}(?:st|nd|rd|th)?\s+grades?$|\bgallery\b|\bphotos?\b|\balbums?\b|\bfb\s+page\b|\bfacebook\b|\bwebsite\b|\bschool\s+(?:clubs?|board|band|athletics|sports|supply|supplies|uniforms?|tuition|calendar)\b|^(?:the\s+)?school\b|\bstaff\b|\bwebmaster\b|\bdirectory\b", re.I)
SACRAMENT_PREP_SUB = {"baptism_prep", "first_communion_prep", "confirmation_prep", "marriage_prep"}


def strip_person_phone(t: str) -> str:
    t = PHONE_FRAG.sub(" ", t or "")
    t = PERSON_FRAG.sub(" ", t)
    t = re.sub(r"\S+@\S+", " ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = DANGLING_HON.sub("", re.sub(r"\s+", " ", t)).strip(" ,;:–—-|/")
    return t


def _contactish(t: str) -> bool:
    """The right-hand part of 'Group - <this>' is a contact (a person, a phone, 'Vacant'), not a description."""
    t = (t or "").strip(" -–—:")
    if not t or re.fullmatch(r"vacant|tbd|tba|open", t, re.I):
        return True
    rest = strip_person_phone(t)
    if not rest or rest != t and len(rest.split()) <= 1:
        return True
    return bool(PERSONISH.match(re.sub(r"\s*\([^)]*\)\s*$", "", t)))


def table_items(p: Page) -> list[str]:
    """Column 1 of tables on a ministry page ('Ministry | Contact | Phone'), people and phones stripped."""
    out = []
    for tb in p.soup.find_all("table"):
        for tr in tb.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells or (tr.find("th") and not tr.find("td")):
                continue
            c0 = cells[0].get_text(" ", strip=True)
            if TABLE_HEAD.match(c0) and len(c0.split()) <= 3:
                continue
            t = _cand_text(strip_person_phone(c0))
            if t and _new_shape_ok(t):
                out.append(t)
    return out if len(out) >= 2 else []


def contact_line_items(p: Page) -> list[str]:
    """'Organization - Contact person (phone)' lines, or an organization line followed by a '- Person' line."""
    out, lines = [], p.lines
    for i, ln in enumerate(lines):
        ln = ln.strip()
        if len(ln) > 120 or ps.TIME_RE.search(ln):
            continue
        cand = None
        m = re.match(r"^(.{3,60}?)\s+[-–—]\s*(.*)$", ln) or re.match(r"^(.{3,60}?)\s*[-–—]$", ln)
        if m and _contactish(m.group(2) if m.lastindex and m.lastindex >= 2 else ""):
            cand = m.group(1)
        elif i + 1 < len(lines) and re.match(r"^\s*[-–—]\s*\S", lines[i + 1]) and _contactish(lines[i + 1]):
            cand = ln
        if cand:
            t = _cand_text(strip_person_phone(cand))
            if t and _new_shape_ok(t):
                out.append(t)
    return out if len(out) >= 2 else []


def groups_for(pages: list[Page], key: str, contact_url: str | None, today: str) -> tuple[list[dict], dict]:
    mp = [p for p in pages if p.kind == "ministries" or (p.kind in ("mass_times", "calendar") and MIN_PAGE_PATH.search(urlparse(p.url).path))]
    items: list[tuple[str, Page]] = []
    seen = set()
    for p in mp:
        for it in ps.parse_ministries(p.soup, [], is_ministry_page=True):
            if it.lower() not in seen:
                seen.add(it.lower())
                items.append((it, p))
    for p in mp:                          # cards / link lists / tables / contact lines / one-group pages (ministries pages only)
        if p.kind != "ministries":
            continue
        got = card_items(p) + table_items(p) + contact_line_items(p)
        one = page_title_item(p)
        if one:
            got = [one] + got
        for it in got:
            if it.lower() not in seen:
                seen.add(it.lower())
                items.append((it, p))
    if len(items) < 3:
        for p in pages:
            if p in mp:
                continue
            got = ps.parse_ministries(p.soup, [])
            if len(got) >= 3:
                items = [(it, p) for it in got]
                break
    if True:                              # groups listed in the site menu under a Ministries-like top item (us-check: #6, #7)
        home = next((p for p in pages if p.kind == "home"), None)
        if home is not None:
            for it in menu_items(home):
                if it.lower() not in seen:
                    seen.add(it.lower())
                    items.append((it, home))
    dropped: dict[str, int] = {}
    obs: dict[str, dict] = {}
    for item, p in items:
        raw = bg.clean_name(bg.fix_mojibake(item).replace("​", ""))
        name, desc_inline = raw, None
        m = re.match(r"^(.{3,60}?)\s*(?:–|—|:| - )\s*(.+)$", raw)
        if m and not re.search(r"council \d|court", m.group(2), re.I):
            right = m.group(2).strip()
            rw = right.split()
            if len(rw) >= 4 and sum(1 for w in rw if w[:1].islower()) / max(1, len(rw)) >= 0.4:
                name, desc_inline = m.group(1).strip(), right
            elif _contactish(right):          # "Secular Franciscan Order - Stanley Robb (909...)": keep the group only
                name = m.group(1).strip()
        name = strip_person_phone(name) or name
        if GROUP_LABEL_JUNK.search(name):
            dropped["label_not_group"] = dropped.get("label_not_group", 0) + 1
            continue
        res, why = bg.classify(name)
        if res is None:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        if bg.private_name_risk(name):
            dropped["private_name_risk"] = dropped.get("private_name_risk", 0) + 1
            continue
        gkey, cat, sub, aud, lang, disp, strength = res
        if sub in SACRAMENT_PREP_SUB:         # sacrament preparation is a sacrament page, not a group (us-check)
            dropped["sacrament_prep"] = dropped.get("sacrament_prep", 0) + 1
            continue
        meets, desc = bg.context(item, p.lines)
        if not desc and desc_inline and not bg.desc_risk(desc_inline) and not re.search(r"@|\d{3}[-.) ]\d{3}", desc_inline):
            desc = bg.clip_words(desc_inline, 40)
        nm = bg.smart_title(name).strip(" .,:;")
        if disp and (strength == "generic" or len(nm.split()) > 6 or re.match(r"^(\d|every|each|first|1st)", nm, re.I)
                     or re.search(rf"\b({bg.DAYS})s?\b", nm, re.I)):
            nm = disp
            pre = {"men": "Men's", "women": "Women's", "young_adult": "Young Adult", "youth": "Youth"}.get(aud)
            if pre and cat in ("scripture_study", "prayer_devotion", "service_mercy") and pre.split("'")[0].lower() not in nm.lower():
                nm = f"{pre} {nm}"
            if lang and lang != "en" and cat != "language_cultural":
                nm += " (" + {"es": "Spanish", "en+es": "English & Spanish", "vi": "Vietnamese", "zh": "Chinese", "tl": "Filipino",
                              "ko": "Korean", "pt": "Portuguese", "to": "Tongan", "ig": "Igbo"}.get(lang, lang) + ")"
        o = {"name": nm, "category": cat, "subcategory": sub, "audience": aud, "lang": lang, "meets": meets, "description": desc,
             "strength": strength, "source_url": p.url, "fetched_at": p.fetched_at, "key": gkey}
        prev = obs.get(gkey)
        score = (o["strength"] == "specific") * 2 + bool(meets) + bool(desc)
        if prev is None or score > prev["_score"]:
            o["_score"] = score
            obs[gkey] = o
    groups, seen_names = [], set()
    for gkey, o in obs.items():
        nk = re.sub(r"[^a-z0-9]+", "", o["name"].lower())
        if nk in seen_names:
            continue
        seen_names.add(nk)
        lang = o["lang"]
        assumed = lang is None
        groups.append({
            "id": f"{key}:{gkey}", "name": o["name"], "category": o["category"], "category_label": bg.CATEGORIES[o["category"]],
            "subcategory": o["subcategory"], "audience": o["audience"], "language": lang or "en", "language_assumed": assumed,
            "meets": o["meets"], "description": o["description"], "contact_url": contact_url,
            "contact_role": "parish office (see contact page)", "source_url": o["source_url"], "source_kind": "website",
            "fetched_at": o["fetched_at"], "confidence": "medium" if o["strength"] == "specific" else "low"})
    groups.sort(key=lambda g: (list(bg.CATEGORIES).index(g["category"]), g["name"]))
    return groups, dropped


# ------------------------------------------------------------------------------------------------ office contact
# The parish OFFICE's institutional contact (Matthew, 2026-10-31): general office e-mail, office phone, mailing address, from
# the home and contact pages (most sites put it in the footer). Never a named person: an e-mail is kept only when its local
# part is an office-type word (office, parishoffice, info, rectory, contact, frontdesk, reception, church, parish ...) and
# carries no personal given name; role mailboxes of individuals (pastor@, dre@, youth@ ...) are not the office and are
# skipped. Cloudflare's e-mail obfuscation (data-cfemail) is decoded from the page we already have (no request).
OFFICE_LOCAL = re.compile(r"^(?:info|office|offices|parishoffice|parish[._-]?office|churchoffice|church[._-]?office|"
                          r"rectory|rectoryoffice|contact|contactus|contact[._-]us|frontdesk|front[._-]desk|frontoffice|"
                          r"reception|receptionist|mainoffice|main[._-]office|parish|church|admin|administration|"
                          r"secretary|parishsecretary|office[._-]?manager|welcome|hello|general|mail|inquiries|information|"
                          r"oficina|parroquia)$|(?:office|parish|rectory|church|parroquia|iglesia)$|^(?:office|parish|rectory)",
                          re.I)
NOT_OFFICE = re.compile(r"pastor|priest|father|^fr[._]|deacon|dcn|^dre$|youth|music|choir|bulletin|webmaster|web|it$|"
                        r"school|principal|finance|bookkeep|account|business|ministr|formation|religious|re$|ccd|psr|ocia|"
                        r"rcia|baptism|wedding|marriage|funeral|youth|teen|safe|abuse|victim|noreply|no-reply|donotreply|"
                        r"wordpress|wix|example|sentry|user|domain|email|name|yourname", re.I)
PHONE_ANY = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-](\d{4})(?![\d-])")
PHONE_BAD_CTX = re.compile(r"fax|emergenc|after[\s-]*hours|school|cell|mobile|text\s*only|hotline|victim|abuse|crisis|"
                           r"suicide|lifeline|988|counsel|direct", re.I)
STREET = (r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|way|ln|lane|ct|court|pl|place|hwy|highway|pkwy|parkway|"
          r"cir|circle|ter|terrace|sq|square|pike|route|rte|trail|trl|loop|row|plaza|broadway|expressway|turnpike|run)")
ADDR_RE = re.compile(rf"\b\d{{1,6}}(?:\s*[A-Z]?)?\s+(?:[NSEW]\.?\s+)?[A-Z0-9][\w.'’-]*(?:\s+[\w.'’-]+){{0,5}}?\s+{STREET}\b\.?(?:\s+(?:NE|NW|SE|SW|N|S|E|W)\b\.?)?"
                     rf"(?:\s*(?:#|suite|ste\.?|unit)\s*\w+)?[\s,/|]+(?:[A-Z][\w.'’-]*\s*){{1,4}},?\s+"
                     rf"(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|PR|RI|S[CD]|T[NX]|UT|"
                     rf"V[TAI]|W[AVIY]|GU)\.?,?\s+\d{{5}}(?:-\d{{4}})?\b", re.I)
PO_RE = re.compile(r"\b(?:P\.?\s?O\.?\s*Box|Post Office Box)\s+\d+[\s,/|]+(?:[A-Z][\w.'’-]*\s*){1,4},?\s+[A-Z]{2}\.?,?\s+"
                   r"\d{5}(?:-\d{4})?\b", re.I)


def _all_lines(p: Page) -> list[str]:
    """Every text line of the page INCLUDING header/nav/footer (Page.lines strips them; the office contact lives there)."""
    t = p.soup.get_text("\n", strip=True)
    return [re.sub(r"\s+", " ", x).strip() for x in t.split("\n") if x.strip()]


def _cf_decode(hexs: str) -> str | None:
    try:
        b = bytes.fromhex(hexs)
        k = b[0]
        return "".join(chr(c ^ k) for c in b[1:])
    except (ValueError, IndexError):
        return None


def _emails_of(p: Page) -> list[tuple[str, str]]:
    """(email, where) on a page: mailto links, Cloudflare-protected spans, plain text."""
    out = []
    raw = BeautifulSoup(p.text, "lxml")
    for a in raw.find_all("a", href=True):
        h = a["href"].strip()
        if h.lower().startswith("mailto:"):
            e = unquote(h[7:].split("?")[0]).strip()
            out.append((e, "mailto"))
        elif "/cdn-cgi/l/email-protection#" in h:
            e = _cf_decode(h.split("#", 1)[1])
            if e:
                out.append((e, "cf"))
    for el in raw.select("[data-cfemail]"):
        e = _cf_decode(el.get("data-cfemail", ""))
        if e:
            out.append((e, "cf"))
    for ln in _all_lines(p):
        for e in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", ln, re.I):
            out.append((e, "text"))
    return [(e.lower().strip(" .,;:"), w) for e, w in out if re.match(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}$", e.strip(" .,;:"), re.I)]


def _office_email_ok(e: str) -> bool:
    local = e.split("@", 1)[0]
    if NOT_OFFICE.search(local) or not OFFICE_LOCAL.search(local):
        return False
    rest = re.sub(r"office|parish|rectory|church|info|contact|reception(?:ist)?|front|desk|main|admin|secretary|parroquia|"
                  r"iglesia|oficina|[._\d-]", " ", local, flags=re.I).split()
    given, _amb = privacy_given_names()
    for w in rest:                          # e.g. "maryoffice", "johnparish": a person's name riding on an office word
        if len(w) >= 3 and w.lower() in given:
            return False
    return True


def privacy_given_names():
    import privacy as _pv
    return _pv._given_names()


def office_contact(pages: list[Page], key: str) -> dict | None:
    host = key.split("/")[0]
    cand = [p for p in pages if p.kind == "contact"] + [p for p in pages if p.kind == "home"]
    if not cand:
        return None
    emails: dict[str, list] = {}
    for p in cand:
        for e, how in _emails_of(p):
            if _office_email_ok(e):
                emails.setdefault(e, [0, p.url, 0])
                emails[e][0] += 1
                emails[e][2] = max(emails[e][2], int(e.split("@", 1)[1].endswith(host.split(":")[0].removeprefix("www."))))
    email, email_src = None, None
    if emails:
        email, (_, email_src, _) = max(emails.items(), key=lambda kv: (kv[1][2], kv[1][0], -len(kv[0])))
    phones: dict[str, list] = {}
    for p in cand:
        raw = BeautifulSoup(p.text, "lxml")
        for a in raw.find_all("a", href=True):
            if a["href"].lower().startswith("tel:"):
                m = PHONE_ANY.search(unquote(a["href"]))
                ctx = (a.find_parent(["p", "li", "div"]) or a).get_text(" ", strip=True)[:120]
                if m and not PHONE_BAD_CTX.search(ctx):
                    k = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
                    phones.setdefault(k, [0, p.url])[0] += 2
        lines = _all_lines(p)
        for i, ln in enumerate(lines):
            if PHONE_BAD_CTX.search(ln) or len(ln) > 200 or (i and PHONE_BAD_CTX.search(lines[i - 1]) and len(lines[i - 1]) < 40):
                continue
            for m in PHONE_ANY.finditer(ln):
                k = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
                phones.setdefault(k, [0, p.url])[0] += 1
    phone, phone_src = None, None
    if phones:
        phone, (_, phone_src) = max(phones.items(), key=lambda kv: kv[1][0])
    address, addr_src = None, None
    for p in cand:
        text = " | ".join(_all_lines(p))
        mail = re.search(r"mailing\s+address\s*:?\s*\|?\s*(.{10,120})", text, re.I)
        for rx_text in ([mail.group(1)] if mail else []) + [text]:
            m = ADDR_RE.search(rx_text) or PO_RE.search(rx_text)
            if m:
                address = re.sub(r"\s*\|\s*", ", ", re.sub(r"\s+", " ", m.group(0))).strip(" ,|")
                addr_src = p.url
                break
        if address:
            break
    if address:
        red, n = redact(address)
        if n or red != address:
            address = None                  # the screen saw a person's name (or a contact) in it: not stored
    if not (email or phone or address):
        return None
    src = email_src or phone_src or addr_src
    return {"email": email, "phone": phone, "address": address, "source_url": src,
            "sources": {k: v for k, v in (("email", email_src), ("phone", phone_src), ("address", addr_src)) if v},
            "fetched_at": next((p.fetched_at for p in cand if p.url == src), None),
            "kind": "parish office (institutional contact; named people's e-mails and phones are never kept)"}


# ------------------------------------------------------------------------------------------------ whole site
LOCAL_RULES = [
    ("groups[].name", "text"), ("groups[].description", "raw"), ("groups[].meets", "text"),
    ("schedule_rows[].raw", "schedule"), ("bulletin.text", "text"),
    ("social.livestream[].label", "text"), ("schedule_images[].alt", "text"),
    ("events[].title", "text"), ("events[].location", "text"),
    ("calendar_feeds[].label", "text"), ("calendar_feeds[].note", "text"), ("calendar_feeds[].last_fetch.calendar_name", "text"),
]


def parse_site(site_dir: Path, m: dict, info: dict, today: str) -> dict:
    """info: the input-list entry (key, home, slug, directory_ids, discovermass_ids, names, cities)."""
    key = info["key"]
    pages = load_pages(site_dir, m)
    home_url = m.get("final_home") or info["home"]
    contact_page = next((p.url for p in pages if p.kind == "contact"), None)
    rec = {
        "domain": key, "diocese": info["slug"],
        "matched_church_ids": sorted(set(info.get("directory_ids", []) + info.get("discovermass_ids", []))),
        "matched": {"directory": info.get("directory_ids", []), "discovermass": info.get("discovermass_ids", [])},
        "names": info.get("names", []), "cities": info.get("cities", []),
        "home_url": info["home"], "final_url": home_url,
        "crawl_status": m.get("status"), "crawl_env": m.get("env"),
        "robots_note": (m.get("robots") or {}).get("class"),
        "pages": [{"category": p["kind"], "url": p.get("final_url") or p["url"], "status": p.get("status"), "class": p["class"]}
                  for p in m.get("pages", []) if p["kind"] not in ("tribe_events", "ics", "wp_pages")],
        "cms_guess": None, "template": "none",
        "groups": [], "calendar_feeds": [], "events": [], "social": {}, "bulletin": None, "schedule_rows": [],
        "source_url": info["home"], "fetched_at": (m.get("finished_at") or m.get("pages", [{}])[0].get("fetched_at")),
    }
    if m.get("redirected_off_host"):
        rec["redirected_off_host"] = m["redirected_off_host"]
    if not pages:
        rec["cms_guess"] = {"cms": "unknown", "evidence": []}
        return rec
    rec["cms_guess"] = cms_guess(pages, key)
    cms = rec["cms_guess"]["cms"]
    used_rest = any(p.get("kind") in ("wp_pages", "tribe_events") and p.get("class") == "ok" for p in m.get("pages", []))
    rec["template"] = ("ecatholic-fastpath" if cms == "ecatholic" else "wordpress-rest+html" if cms == "wordpress" and used_rest
                       else "wordpress-html" if cms == "wordpress" else f"generic-html:{cms}")
    soc, _ = extract_social(pages, home_url)
    rec["social"] = soc
    rec["bulletin"] = find_bulletin(pages)
    sched_dropped: dict = {}
    rows, imgs = schedule_rows_templated(rec, pages, key, home_url, today, info.get("names"), sched_dropped)
    rows = tag_locations(rows, pages)
    rows = row_hygiene(rows, sched_dropped)
    rows = merge_box_rows(rows, pages, key, home_url, today, info.get("names"), sched_dropped)
    rec["schedule_rows"] = rows
    rec["has_sunday"] = has_sunday(rows)
    if rows and not rec["has_sunday"]:
        rec["schedule_partial"] = True
    if sched_dropped:
        rec["schedule_dropped"] = sched_dropped
    if imgs and not rows:
        rec["schedule_images"] = imgs
        rec["schedule_note"] = "schedule appears to be an image; times not read (needs_review)"
    rec["groups"], rec["groups_dropped"] = groups_for(pages, key, contact_page or home_url, today)
    try:
        oc = office_contact(pages, key)
    except Exception:  # noqa: BLE001 - an odd page must not cost the rest of the record
        oc = None
    if oc:
        rec["office_contact"] = oc
    rec["_pages"] = pages          # for events.py (feed discovery); removed before writing
    return rec


def schedule_rows_templated(rec: dict, pages: list[Page], key: str, home: str, today: str, names: list[str] | None,
                            dropped: dict) -> tuple[list[dict], list[dict]]:
    """Schedule rows from the per-platform templates (ingest/california/sites/templates/, Phase B), with the generic reader
    as the fallback when a template raises, and for image hints. Writes rec["schedule_template"]: the template, its
    parse_quality, and `trusted_candidate` = the template passed its bar (templates.PASSED) and the parse is high quality.
    The last condition, agreement with DiscoverMass on Sunday, is checked in scripts/california/merge.py, which holds the
    DiscoverMass rows; only then are the rows labelled 'site-<template>-<date>' and outrank DiscoverMass."""
    gen_rows, imgs = schedule_rows(pages, key, home, today, names, {})
    try:
        import templates as T  # noqa: E402  (ingest/california/sites/templates)
        res = T.parse_schedule(pages, names)
    except Exception as e:  # noqa: BLE001 - one odd page must not stop the pass: the generic reader's rows stand
        rec["schedule_template"] = {"template": "generic-fallback", "error": repr(e)[:160], "trusted_candidate": False}
        rows, _ = schedule_rows(pages, key, home, today, names, dropped)
        return rows, imgs
    for k, v in (res.get("dropped") or {}).items():
        dropped[k] = dropped.get(k, 0) + v
    q = res.get("parse_quality") or {}
    rec["schedule_template"] = {"template": res["template"], "version": TEMPLATE_VERSION, "parse_quality": q,
                                "passed_bar": res["template"] in T.PASSED,
                                "trusted_candidate": T.trusted(res["template"], q),
                                "generic_rows": len(gen_rows)}
    return res["rows"], imgs


SWEEP_SKIP = {"matched_church_ids", "matched", "directory", "discovermass", "key", "domain", "id", "feed_id", "feed_key"}


def _sweep(o, cnt, skip=SWEEP_SKIP):
    """Last line of defence: no e-mail address or phone number survives in any stored text value, whatever field it is in.
    (Directory ids are join keys copied from the DiscoverMass file and are left alone; web addresses are not touched.)"""
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k in skip or k.endswith("_url") or k in ("url", "href", "page_url", "source_url", "final_url", "home_url"):
                continue
            if isinstance(v, str):
                n = strip_contacts(v)
                if n != v:
                    o[k] = n
                    cnt["contacts_swept"] += 1
            else:
                _sweep(v, cnt, skip)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            if isinstance(v, str):
                n = strip_contacts(v)
                if n != v:
                    o[i] = n
                    cnt["contacts_swept"] += 1
            else:
                _sweep(v, cnt, skip)


def screen_record(rec: dict) -> dict:
    oc = rec.pop("office_contact", None)       # institutional contact: screened on its own (below), not swept away
    cnt = screen(rec, LOCAL_RULES)
    _sweep(rec, cnt)
    if isinstance(oc, dict):
        if oc.get("email") and not _office_email_ok(oc["email"]):
            oc["email"] = None
            cnt["office_email_dropped"] += 1
        if oc.get("address"):
            red, n = redact(oc["address"])
            if n or red != oc["address"]:
                oc["address"] = None
                cnt["office_address_dropped"] += 1
        if oc.get("email") or oc.get("phone") or oc.get("address"):
            rec["office_contact"] = oc
    rec["privacy"] = {"screened_by": "ingest/public/privacy.py screen()", **{k: v for k, v in cnt.items()}}
    return rec
