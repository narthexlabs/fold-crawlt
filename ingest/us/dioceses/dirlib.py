"""Shared engine for the diocese-directory readers (lane G4b): directory_pages.py and directory_html.py.

What it does for one see (all reads polite: robots.txt, Crawl-delay, <=1 request/s per host, honest FindTheChurch UA, a
challenge or a 403 is recorded and never worked around; HTTPS only, see common_fetch.https_only):

  * Cache   every response is cached in data/raw/us/dioceses/<slug>/ (gitignored) under the same key as directory_probe.py,
            so a rerun asks only for what is missing (resumable).
  * Sitemap parish-like sub-sitemaps (parish / church / director / location in the URL) -> parish detail-page URLs.
  * List    a directory page -> entries: an external (non-diocese, non-social, non-vendor) link and the smallest page block
            around it that holds a parish-looking name; address / city / zip / coordinates from the same block.
            Pagination (rel=next, ?page=N, /page/N/) followed up to 30 pages.
  * Detail  one parish page -> name (h1 / og:title), address, city, zip, coordinates, and the parish's own website: an
            external link labelled "website" or whose text is a domain, else the first external link in the main content
            that is not page chrome. Chrome = hosts linked from the diocese home page or from >= 25% of the see's detail
            pages (header, footer, sidebars).

Privacy: only name, address, city, state, zip, coordinates, website and source URL are kept. Phones and e-mails are
removed from every kept string (ingest/public/privacy.strip_contacts) and no person's name is taken (pastor, staff and
contact fields are never read). Parish names are checked with privacy.private_name_risk only when they carry a lay title.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "ingest" / "public"))
import common_fetch as CF  # noqa: E402

try:
    import privacy as PV  # noqa: E402
except Exception:  # noqa: BLE001
    PV = None
from bs4 import BeautifulSoup  # noqa: E402

US = ROOT / "data" / "real" / "us"
OUT = US / "dioceses"
RAW = ROOT / "data" / "raw" / "us" / "dioceses"
STATUS = OUT / "directory_status.json"
READER_STATUS = OUT / "reader_status.json"
STATUS_LOCK = threading.Lock()

# ------------------------------------------------------------------------------------------------ vocabulary
PARISHY = re.compile(
    r"\b(?:st\.?|sts\.?|ss\.?|saint|saints|santa|san|santo|santissim[oa]|sainte|our lady|nuestra|notre|holy|sacred|blessed|"
    r"immaculate|christ|cathedral|co-cathedral|basilica|parish|parroquia|church|iglesia|mission|chapel|nativity|resurrection|"
    r"transfiguration|assumption|annunciation|visitation|epiphany|good shepherd|precious blood|corpus christi|trinity|"
    r"divine|sagrad[oa]|queen|mary|marys|infant|incarnation|ascension|all saints|newman|shrine|oratory|catholic|"
    r"pentecost|guadalupe|lourdes|fatima|holy family|holy cross|holy spirit|holy name|holy rosary|redeemer|savior|saviour|"
    r"sts?\b|prince of peace|light of|mother of|cristo|jesus|madonna|annunciation|sorrows|perpetual|carmel|"
    r"emmanuel|immanuel|messiah|eucharist|sacrament|apostle|evangelist|martyrs?|angels?|cross|community|quasi)\b", re.I)
NOT_PARISH_NAME = re.compile(r"^(?:home|about|contact|news|events?|calendar|search|map|directions|website|visit|more|"
                             r"read more|learn more|details|view|parishes|parish directory|find a parish|donate|give|"
                             r"login|menu|next|previous|back|parish search|to zoom|click here|parish locator)\b", re.I)
SOCIAL = re.compile(r"(?:^|\.)(?:facebook|fb|instagram|twitter|x|youtube|youtu|tiktok|linkedin|pinterest|vimeo|flickr|"
                    r"threads|snapchat|wa|whatsapp|t)\.(?:com|me|be|net)$", re.I)
INFRA = re.compile(
    r"google\.|goo\.gl|maps\.app|apple\.com|bing\.com|mapquest|openstreetmap|waze|usccb\.org|vatican\.va|"
    r"discovermass|masstimes\.org|catholic\.org$|ewtn|formed\.org|wordonfire|flocknote|paypal|givecentral|onlinegiving|"
    r"wesharegiving|weshare|osvhub|osv\.com|faithdirect|parishgiving|givelify|pushpay|tithe\.ly|vancopayments|"
    r"eventbrite|constantcontact|mailchimp|list-manage|w3\.org|schema\.org|wordpress\.(?:org|com)$|gravatar|cloudflare|"
    r"jquery|ecatholic\.com|diocesan\.com|squarespace|wix\.com|wixsite\.com$|elementor|godaddy|bit\.ly|tinyurl|"
    r"parishesonline\.com|lpi\.|4lpi|jspaluch|church-bulletin|e-bulletin|bulletins?\.|myparishapp|parishsoft\.com$|"
    r"ministryplatform|acceptiva|blackbaud|qgiv|givebutter|donorbox|classy\.org|virtus|praesidium|shieldthevulnerable|"
    r"reportbishopabuse|safeenvironment|adobe\.com|microsoft\.com|office\.com|sharepoint|outlook|live\.com|zoom\.us|"
    r"dropbox|box\.com|issuu|calameo|yumpu|flipsnack|smore|canva|linktr\.ee|addtoany|sharethis|addthis|amazon\.|"
    r"spotify|soundcloud|podbean|anchor\.fm|apple\.co|itunes|stitcher|catholicnewsagency|ncregister|aleteia|"
    r"americamagazine|osvnews|catholicnews|cruxnow|crs\.org|crsricebowl|ccusa|catholiccharitiesusa|kofc\.org|"
    r"magiscenter|ascensionpress|dynamiccatholic|augustineinstitute|ignatius|lighthousecatholicmedia|myparish\.app|"
    r"realtor|zillow|weebly\.com$|blogspot\.com$|translate\.|recaptcha|hcaptcha|typekit|fonts\.|gstatic|doubleclick|"
    r"ncsservices|eservicepayments|mobilecause|myowngiving|backspin360|batchgeo|doldata\.com|"
    r"rebrand\.ly|ltp\.org|ibreviary|getholy|catholic\.com$|jotform|"
    r"youtube-nocookie|ytimg|vimeocdn|jotform|formstack|surveymonkey|wufoo|typeform|forms\.gle|docs\.google", re.I)
WEBSITE_LABEL = re.compile(r"\b(?:web\s*-?\s*site|visit (?:the |our )?(?:parish|church|web|site)|parish (?:site|home ?page)|"
                           r"home ?page|sitio web|p[aá]gina web|go to (?:parish|site|website)|parish web|url)\b", re.I)
DOMAIN_TEXT = re.compile(r"^(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9.-]*\.(?:org|com|net|church|us|info|edu|catholic|faith|"
                         r"parish|site|online|cc|co|biz|ws|life|community|io)(?:/\S*)?$", re.I)
ZIP_RX = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
STREET_RX = re.compile(
    r"\b\d{1,6}[A-Za-z]?\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9 .'#-]{2,40}?\b(?:st|street|rd|road|ave|avenue|av|blvd|boulevard|dr|"
    r"drive|way|ln|lane|ct|court|pl|place|hwy|highway|pkwy|parkway|cir|circle|ter|terrace|sq|square|trl|trail|pike|route|"
    r"rte|loop|row|plaza|crossing|xing|run|pass|path|alley|grove|hill|park|point|ridge|bend|valley|turnpike|tpke|"
    r"expressway|fwy|freeway|calle|camino|avenida|broadway)\b\.?(?:\s+(?:[NSEW]|NE|NW|SE|SW)\b\.?)?", re.I)
CSZ_RX = re.compile(r"([A-Z][A-Za-z .'-]{1,30}?),?\s+(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|"
                    r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY]|Alabama|Alaska|Arizona|Arkansas|California|Colorado|"
                    r"Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|"
                    r"Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|"
                    r"New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
                    r"Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|"
                    r"Washington|West Virginia|Wisconsin|Wyoming)\.?,?\s+(\d{5})(?:-\d{4})?\b")
LATLNG_RX = re.compile(r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")
SKIP_PATH = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|svg|docx?|xlsx?|pptx?|zip|mp3|mp4|ics)(?:\?|$)|/wp-(?:json|admin|login|"
                       r"content)|/feed/?$|/tag/|/category/|/author/|/\d{4}/\d{2}/|/events?/|/news/|/blog/|/search|/login|"
                       r"/cart|/donate|/giving|/calendar|/staff|/clergy|/priests?/|/people/|/person/|/schools?/|/jobs?/|"
                       r"/employment|mailto:|tel:|javascript:|[?&](?:share|print|replytocom|ical|outlook|lang)=", re.I)
DIR_LINK_TEXT = re.compile(r"\b(?:find a (?:parish|church)|parish(?:es)?(?: directory| finder| locator| search| list(?:ing)?)?|"
                           r"church(?:es)?(?: directory| finder| locator)?|directory|mass (?:times|finder|schedules?)|"
                           r"locations|parroquias|our parishes|all parishes|looking for a (?:church|parish))\b", re.I)
SITEMAP_PARISH = re.compile(r"parish|church|director|location|parroquia|crb_parish|cpt_par|mission|worship|mass", re.I)
SITEMAP_NOT = re.compile(r"school|staff|clergy|priest|people|person|event|news|post-sitemap|page-sitemap|category|tag|"
                         r"author|attachment|media|job|product", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(str(s))).strip()


def scrub(s: str | None) -> str | None:
    if not s:
        return None
    if PV is not None:
        s = PV.strip_contacts(s)
    s = re.sub(r"(?:phone|tel|fax|e-?mail|office)\s*:?\s*$", "", s, flags=re.I).strip(" ,;:-|")
    return s or None


def reg_domain(host: str) -> str:
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def clean_href(u: str) -> str:
    u = (u or "").strip()
    u = re.sub(r"(?:%20|\s).*$", "", u)          # 'http://x.com/%20rel=' and stray attributes
    u = u.rstrip(".,;)\"'\\")
    return u


def website_ok(u: str, own_hosts: set[str]) -> bool:
    if not u or not u.lower().startswith(("http://", "https://")):
        return False
    if "@" in u.split("?")[0] or re.search(r"\d{3}[-.]\d{3}[-.]\d{4}", u):   # an e-mail typed as a website; a phone in a URL
        return False
    h = CF.host_of(u)
    if not h or "." not in h:
        return False
    if SOCIAL.search(h) or INFRA.search(h) or re.search(r"\.(?:pdf|jpe?g|png|gif|docx?)(?:\?|$)", u, re.I):
        return False
    if reg_domain(h) in {reg_domain(x) for x in own_hosts}:
        return False
    return True


def norm_site(u: str) -> str:
    p = urlparse(u)
    path = p.path if p.path not in ("", "/") else "/"
    if re.search(r"/(?:index\.(?:html?|php|aspx?))$", path, re.I):
        path = path.rsplit("/", 1)[0] + "/"
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path}"


def coords_from(soup_or_text, html_text: str = "") -> tuple[float | None, float | None]:
    """Coordinates only from an unambiguous source: a Google Maps link with @lat,lng / q=lat,lng / ll=, data-lat attrs,
    or a JSON-LD geo block. Values outside the US bounding range are ignored."""
    cands = []
    soup = soup_or_text
    if hasattr(soup, "find_all"):
        for a in soup.find_all(["a", "iframe"], href=True) + soup.find_all("iframe", src=True):
            u = a.get("href") or a.get("src") or ""
            if "google" not in u and "maps" not in u:
                continue
            u = unquote(u)
            m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", u) or re.search(r"[?&](?:q|ll|daddr|destination|center)=(-?\d+\.\d+),\s*(-?\d+\.\d+)", u) \
                or re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", u)
            if m:
                cands.append((float(m.group(1)), float(m.group(2))))
        for el in soup.find_all(attrs={"data-lat": True}):
            try:
                cands.append((float(el["data-lat"]), float(el.get("data-lng") or el.get("data-lon") or el.get("data-long"))))
            except (TypeError, ValueError):
                pass
    for m in re.finditer(r'"latitude"\s*:\s*"?(-?\d+\.\d+)"?\s*,\s*"longitude"\s*:\s*"?(-?\d+\.\d+)', html_text):
        cands.append((float(m.group(1)), float(m.group(2))))
    for lat, lng in cands:
        if 17 < lat < 72 and -180 < lng < -60:
            return round(lat, 6), round(lng, 6)
    return None, None


def address_from(txt: str) -> dict:
    t = clean(txt)
    out = {"address": None, "city": None, "state": None, "zip": None}
    st = STREET_RX.search(t)
    rest = t[st.end():] if st else t
    m = CSZ_RX.search(rest[:160]) if st else CSZ_RX.search(t)
    if st and (m or ZIP_RX.search(rest[:80])):
        out["address"] = scrub(st.group(0).strip(" ,"))
    if m:
        city = re.sub(r"^(?:.*\b(?:suite|ste|unit|apt|box|floor|fl)\.?\s*#?\s*\w+\s*,?\s*)", "", m.group(1), flags=re.I).strip(" ,.")
        city = re.sub(r"^\d+\s*", "", city).strip()
        if 1 < len(city) <= 30 and not re.search(r"\d", city):
            out["city"] = city
        out["state"] = m.group(2)
        out["zip"] = m.group(3)
    elif st:
        z = ZIP_RX.search(rest[:80])
        out["zip"] = z.group(1) if z else None
    return out


def name_ok(n: str) -> bool:
    n = clean(n)
    if not (3 <= len(n) <= 120) or NOT_PARISH_NAME.search(n) or "@" in n or re.search(r"\d{3}[-.) ]\d{3}", n):
        return False
    if DOMAIN_TEXT.match(n):
        return False
    return bool(PARISHY.search(n))


STRONG_NAME = re.compile(
    r"\b(?:st\.?|sts\.?|ss\.?|saint|saints|santa|san|santo|santissim[oa]|sainte|our lady|nuestra|notre|holy|sacred|"
    r"blessed|immaculate|christ|cathedral|co-cathedral|basilica|parish|parroquia|church|iglesia|mission|chapel|nativity|"
    r"resurrection|transfiguration|assumption|annunciation|visitation|epiphany|good shepherd|precious blood|corpus christi|"
    r"trinity|divine|sagrad[oa]|queen|mary|infant|incarnation|ascension|newman|shrine|oratory|pentecost|guadalupe|"
    r"lourdes|fatima|redeemer|savior|saviour|prince of peace|mother of|cristo|madonna|sorrows|perpetual|carmel|emmanuel|"
    r"immanuel|apostles?|evangelist|martyrs|angels|cross|mar|mart|quasi-parish|eparchy|pastorate)\b", re.I)
NOT_PARISH_ENTITY = re.compile(r"\b(?:schools?|academy|high school|cemeter(?:y|ies)|charities|foundation|appeal|podcast|"
                               r"retreat|camp|seminary|college|university|office|department|ministry|ministries|"
                               r"movement|campaign|council|study|program|resources?|bishops?|cardinal|archbishop|"
                               r"chaplains?|statement|boundaries|employment|jobs?|news|donate)\b", re.I)
KEEP_ANYWAY = re.compile(r"\b(?:parish|church|cathedral|basilica|mission|chapel|shrine|newman|oratory|parroquia|iglesia)\b", re.I)


HARD_REJECT = re.compile(r"\b(?:records|statement|teachings?|ministers|catechists?|library|polic(?:y|ies)|procedures|"
                         r"way of the cross|youth|young adult|conference|cursillo|campus ministry|cemetery|mausoleum|"
                         r"podcast|appeal|team|safety|request|formation|resources?|portal|employment|annual|"
                         r"encountering|harmony|mission statement|our faith|nuestra fe|movement)\b", re.I)


def hard_reject(name: str) -> bool:
    return bool(HARD_REJECT.search(name or "")) and not re.search(r"\b(?:parish|parroquia|iglesia)\b", name or "", re.I)


def is_parish_record(name: str) -> bool:
    if hard_reject(name):
        return False
    if not STRONG_NAME.search(name or "") or NOT_PARISH_NAME.search(name) or \
            re.fullmatch(r"(?:the\s+)?(?:parish|church|catholic)(?:es)?\s*\w*", name.strip(), re.I):
        return False
    return not (NOT_PARISH_ENTITY.search(name) and not KEEP_ANYWAY.search(name))


def name_clean(n: str, diocese_name: str = "") -> str:
    n = clean(n)
    n = re.split(r"\s*::\s*", n)[0]
    parts = re.split(r"\s+[|\u2013\u2014-]\s+", n)
    while len(parts) > 1 and re.search(r"dioc|eparch|melkite|roman catholic|catholic church of|locator|directory|"
                                       r"archdiocese|catholic community of [a-z ]+$", parts[-1], re.I):
        parts.pop()
    n = " - ".join(parts)
    n = re.split(r"\s+\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][a-z]", n)[0]          # 'Guardian Angel Parish 320 Franklin...'
    n = re.sub(r"\s*\(\s*$", "", n)
    n = re.split(r"\s+[|–—-]\s+(?=(?:the\s+)?(?:arch)?dioc|catholic dioc|eparchy|roman catholic)", n, flags=re.I)[0]
    if diocese_name:
        n = n.replace(" - " + diocese_name, "").replace(" | " + diocese_name, "")
    return n.strip(" -|:")


# ------------------------------------------------------------------------------------------------ fetcher
class Fetcher:
    """Cached polite GET, same cache key and layout as directory_probe.Probe.get."""

    def __init__(self, slug: str, base: str, pol: CF.Polite, refresh: bool = False, budget: int = 900):
        self.slug, self.base, self.pol, self.refresh = slug, base, pol, refresh
        self.raw = RAW / slug
        self.raw.mkdir(parents=True, exist_ok=True)
        self.n_net = 0
        self.n_cache = 0
        self.budget = budget
        self.classes = Counter()

    def get(self, url: str, accept: str | None = None) -> dict:
        url = CF.https_only(url if url.startswith("http") else urljoin(self.base + "/", url))
        url = url.split("#")[0]
        fn = self.raw / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
        if fn.exists() and not self.refresh:
            try:
                d = json.loads(fn.read_text())
                d["body"] = d.get("body", "").encode("utf-8")
                self.n_cache += 1
                self.classes[d.get("class")] += 1
                return d
            except ValueError:
                pass
        if self.n_net >= self.budget:
            return {"url": url, "status": None, "class": "budget", "body": b"", "final_url": url, "headers": {}}
        r = self.pol.get(url, accept=accept)
        self.n_net += 1
        rec = {"url": url, "final_url": r.get("final_url"), "status": r.get("status"), "class": r["class"],
               "ctype": r.get("ctype", ""), "headers": {k: v for k, v in (r.get("headers") or {}).items()
                                                        if k in ("x-wp-total", "x-wp-totalpages", "content-type")},
               "fetched_at": now_iso()}
        body = r.get("body") or b""
        if r["class"] not in CF.EGRESS_FAIL and r["class"] != "budget":
            fn.write_text(json.dumps(dict(rec, body=body.decode("utf-8", "replace") if r["class"] == "ok" else ""),
                                     ensure_ascii=False))
        rec["body"] = body if r["class"] == "ok" else b""
        self.classes[rec["class"]] += 1
        return rec

    def soup(self, url: str) -> tuple[BeautifulSoup | None, dict]:
        r = self.get(url)
        if r["class"] != "ok" or not r["body"]:
            return None, r
        try:
            return BeautifulSoup(r["body"].decode("utf-8", "replace"), "lxml"), r
        except Exception:  # noqa: BLE001
            return None, r


def cms_guess(body: bytes, gen: str = "") -> str:
    t = body[:600000].lower()
    g = (gen or "").lower()
    for key, pat in [("ecatholic", [b"ecatholic"]), ("diocesan", [b"diocesan.com", b"diocesan publications", b"dpi-"]),
                     ("parishsoft", [b"parishsoft"]), ("weconnect", [b"weconnect", b"we connect"]),
                     ("squarespace", [b"squarespace"]), ("wix", [b"wix.com", b"wixstatic"]),
                     ("drupal", [b"drupal"]), ("silverstripe", [b"silverstripe"]), ("concrete5", [b"concrete5", b"/concrete/"]),
                     ("finalsite", [b"finalsite"]), ("wordpress", [b"wp-content", b"wp-includes"]),
                     ("joomla", [b"joomla"]), ("dnn", [b"dnnvariable", b"dotnetnuke"]), ("hubspot", [b"hs-scripts", b"hubspot"])]:
        if any(p in t for p in pat) or key in g:
            return key
    return "unknown"


def soup_of(body: bytes):
    try:
        return BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
    except Exception:  # noqa: BLE001
        return None


def strip_chrome(soup):
    for t in soup.find_all(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    return soup


def main_area(soup):
    for sel in ["main", "[role=main]", "article", "#content", "#main", ".entry-content", ".content", "#primary"]:
        el = soup.select_one(sel)
        if el and len(el.get_text(" ", strip=True)) > 40:
            return el
    body = soup.body or soup
    for t in body.find_all(["header", "nav", "footer"]):
        t.decompose()
    return body


def ext_hosts(soup, own: set[str]) -> set[str]:
    out = set()
    for a in soup.find_all("a", href=True):
        h = clean_href(a["href"])
        if h.startswith("http"):
            hh = CF.host_of(h)
            if hh and reg_domain(hh) not in {reg_domain(x) for x in own}:
                out.add(hh)
    return out


# ------------------------------------------------------------------------------------------------ sitemap
def sitemap_urls(F: Fetcher, start: list[str], limit: int = 900) -> tuple[list[str], list[str]]:
    """Parish-detail URLs from parish-like sitemaps. Returns (urls, sitemaps_used)."""
    seen, used, urls = set(), [], []
    queue = list(dict.fromkeys(start))
    while queue and len(seen) < 40:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        r = F.get(sm, accept="application/xml,text/xml,*/*")
        if r["class"] != "ok":
            continue
        body = r["body"]
        locs = [clean(x.decode("utf-8", "replace")) for x in re.findall(rb"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", body)]
        if b"<sitemapindex" in body[:3000].lower() or (locs and all(re.search(r"sitemap[^/]*\.xml", x, re.I) for x in locs[:5])):
            for x in locs:
                if SITEMAP_PARISH.search(x.rsplit("/", 1)[-1]) and not SITEMAP_NOT.search(x.rsplit("/", 1)[-1]):
                    queue.append(x)
            continue
        fname = sm.rsplit("/", 1)[-1]
        if SITEMAP_PARISH.search(fname) and not SITEMAP_NOT.search(fname):
            used.append(sm)
            urls += [x for x in locs if not SKIP_PATH.search(x)]
        else:   # an ordinary flat sitemap: keep URLs whose path says parish/church/location
            keep = [x for x in locs if re.search(r"/(?:parish(?:es)?|churches|church|locations?|directory|parroquias?)/[^/?#]+/?$",
                                                  urlparse(x).path, re.I) and not SKIP_PATH.search(x)]
            if keep:
                used.append(sm)
                urls += keep
    urls = list(dict.fromkeys(urls))
    # drop the listing pages themselves (/parishes/) and keep children
    urls = [u for u in urls if urlparse(u).path.strip("/").count("/") >= 1 or PARISHY.search(urlparse(u).path.replace("-", " "))]
    return urls[:limit], used


def sitemap_starts(F: Fetcher, base: str) -> list[str]:
    st = []
    rb = F.pol.robots_for(base + "/")
    rp = rb.get("rp")
    if rp is not None and getattr(rp, "site_maps", None) and rp.site_maps():
        st += [CF.https_only(x) for x in rp.site_maps()]
    st += [base + "/sitemap.xml", base + "/sitemap_index.xml", base + "/wp-sitemap.xml"]
    return list(dict.fromkeys(st))


# ------------------------------------------------------------------------------------------------ list pages
def list_entries(soup, page_url: str, own: set[str], chrome: set[str]) -> list[dict]:
    """Entries on a directory page: each external website link + the block around it that names a parish."""
    soup = strip_chrome(soup)
    area = main_area(soup)
    anchors = []
    for a in area.find_all("a", href=True):
        u = clean_href(urljoin(page_url, a["href"]))
        if website_ok(u, own):
            anchors.append((a, u))
    # hosts also linked from the home page are page chrome (charities, schools) -- unless most of this list is on the
    # home page too (a home page with a parish drop-down), then the home page proves nothing
    hosts = {CF.host_of(u) for _, u in anchors}
    if hosts and len(hosts & chrome) < 0.3 * len(hosts):
        anchors = [(a, u) for a, u in anchors if CF.host_of(u) not in chrome]
    if not anchors:
        return []
    ahosts = {id(a): CF.host_of(u) for a, u in anchors}
    out = []
    for a, u in anchors:
        h = ahosts[id(a)]
        blk = a
        best = None
        for _ in range(8):
            par = blk.parent
            if par is None or par is area.parent:
                break
            others = {ahosts[id(x)] for x in par.find_all("a", href=True) if id(x) in ahosts} - {h}
            if others:
                break
            blk = par
            best = blk
        if best is None:
            best = a.parent or a
        txt = best.get_text(" | ", strip=True)
        if len(txt) > 1500:
            continue
        name = None
        for el in best.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "a", "dt", "th", "td", "p", "span", "div"]):
            t = clean(el.get_text(" ", strip=True))
            if el.name in ("p", "span", "div", "td") and len(t) > 90:
                continue
            if name_ok(t) and not DOMAIN_TEXT.match(t):
                name = t
                break
        if not name:
            at = clean(a.get_text(" ", strip=True))
            if name_ok(at):
                name = at
        if not name:
            # a heading just before the block (accordion / definition layouts)
            prev = best.find_previous(["h2", "h3", "h4", "h5", "strong"])
            if prev is not None:
                t = clean(prev.get_text(" ", strip=True))
                if name_ok(t) and len(t) < 90:
                    name = t
        if not name:
            continue
        name = re.split(r"\s+\|\s+", name)[0]
        addr = address_from(txt.replace(" | ", ", "))
        lat, lng = coords_from(best)
        oph, oem = office_contacts(best)
        out.append(dict(name=name_clean(name), website=norm_site(u), lat=lat, lng=lng, **addr,
                        office_phone=oph, office_email=oem,
                        basis="list-labelled" if WEBSITE_LABEL.search(a.get_text(" ") or "") or DOMAIN_TEXT.match(clean(a.get_text())) else "list-block"))
    # one record per (name, website)
    seen, uniq = set(), []
    for e in out:
        k = (e["name"].lower(), CF.host_of(e["website"]))
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def detail_candidates(soup, page_url: str, base_host: str, names: dict | None = None) -> list[str]:
    """Same-host links that look like per-parish pages: the largest family of sibling paths (>= 5 members) under one
    parent path, or root-level links whose text names a parish."""
    fam: dict[str, dict[str, str]] = {}
    els = [(a, a["href"]) for a in soup.find_all("a", href=True)]
    els += [(e, e.get("data-url") or e.get("data-href") or e.get("data-link"))
            for e in soup.find_all(lambda t: t.name != "a" and (t.has_attr("data-url") or t.has_attr("data-href") or
                                                                 t.has_attr("data-link")))]
    for a, href in els:
        if not href:
            continue
        u = urljoin(page_url, href).split("#")[0]
        if not u.startswith("http") or CF.host_of(u) != base_host or SKIP_PATH.search(u):
            continue
        p = urlparse(u)
        path = p.path.rstrip("/")
        if not path or "/page/" in path:
            continue
        parent = path.rsplit("/", 1)[0]
        t = clean(a.get("data-title") or a.get("data-name") or a.get_text(" ", strip=True))
        if parent == "" and not name_ok(t):
            continue
        if p.query and not re.search(r"(?:^|&)(?:id|parish|church|location|p)=", p.query):
            continue
        fam.setdefault(parent, {})[CF.https_only(u)] = t
    best = []
    for parent, mem in fam.items():
        n_par = sum(1 for t in mem.values() if name_ok(t))
        if len(mem) >= 5 and n_par >= max(3, 0.4 * len(mem)):
            if n_par > len(best):
                best = [u for u, t in mem.items() if name_ok(t) or parent != ""]
                if names is not None:
                    for u, t in mem.items():
                        if name_ok(t):
                            names.setdefault(u, t)
    return best


def next_pages(soup, page_url: str, base_host: str) -> list[str]:
    out = []
    for a in soup.find_all("a", href=True):
        u = urljoin(page_url, a["href"]).split("#")[0]
        if CF.host_of(u) != base_host:
            continue
        rel = " ".join(a.get("rel") or [])
        t = clean(a.get_text(" ", strip=True)).lower()
        if "next" in rel or t in ("next", "next »", "»", "›", ">", "next page", "older") or re.fullmatch(r"\d{1,2}", t):
            if re.search(r"[?&](?:page|pg|paged|p|start)=\d+|/page/\d+/?$", u, re.I):
                out.append(u)
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------------------------------------ detail pages
def detail_record(soup, page_url: str, own: set[str], diocese_name: str, fallback_name: str | None = None) -> dict | None:
    html_text = str(soup)[:800000]
    title = None
    og = soup.find("meta", property="og:title")
    strip_chrome(soup)
    h1 = soup.find("h1")
    heads = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])[:6]]
    for cand in [h1.get_text(" ", strip=True) if h1 else None, og.get("content") if og else None,
                 soup.title.get_text(" ", strip=True) if soup.title else None] + heads:
        if cand and name_ok(name_clean(cand, diocese_name)) and is_parish_record(name_clean(cand, diocese_name)):
            title = name_clean(cand, diocese_name)
            break
    if not title and fallback_name and is_parish_record(name_clean(fallback_name, diocese_name)):
        title = name_clean(fallback_name, diocese_name)     # the list page's link text names the parish
    if not title:
        return None
    lat, lng = coords_from(soup, html_text)
    area = main_area(soup)
    links = []
    for a in area.find_all("a", href=True):
        u = clean_href(urljoin(page_url, a["href"]))
        if website_ok(u, own):
            t = clean(a.get_text(" ", strip=True))
            lab = clean(" ".join([a.get("aria-label") or "", a.get("title") or "", " ".join(a.get("class") or [])]))
            prev = clean(a.find_previous(string=True) or "")[-40:]
            links.append({"url": u, "host": CF.host_of(u), "text": t, "label": lab, "prev": prev})
    if not links:     # some themes print the parish's contact box outside <main>; chrome hosts are removed later
        for a in (soup.body or soup).find_all("a", href=True):
            u = clean_href(urljoin(page_url, a["href"]))
            if website_ok(u, own):
                t = clean(a.get_text(" ", strip=True))
                lab = clean(" ".join([a.get("aria-label") or "", a.get("title") or "", " ".join(a.get("class") or [])]))
                prev = clean(a.find_previous(string=True) or "")[-40:]
                links.append({"url": u, "host": CF.host_of(u), "text": t, "label": lab, "prev": prev, "outside_main": True})
    txt = area.get_text(" | ", strip=True)
    addr = address_from(txt.replace(" | ", ", "))
    if not addr["address"] and not addr["zip"]:
        addr = address_from((soup.body or soup).get_text(" | ", strip=True).replace(" | ", ", "))
    oph, oem = office_contacts(area)
    return {"name": title, "links": links, "office_phone": oph, "office_email": oem, "lat": lat, "lng": lng, **addr, "source_url": page_url}


def choose_website(links: list[dict], chrome: set[str]) -> tuple[str | None, str | None]:
    links = [x for x in links if x["host"] not in chrome]
    if links and all(x.get("outside_main") for x in links):
        links = [x for x in links if WEBSITE_LABEL.search(x["text"]) or WEBSITE_LABEL.search(x["label"]) or
                 DOMAIN_TEXT.match(x["text"].lower().rstrip("/"))]
    for x in links:
        if WEBSITE_LABEL.search(x["text"]) or WEBSITE_LABEL.search(x["label"]) or re.search(r"web\s*-?site\s*:?\s*$", x["prev"], re.I):
            return norm_site(x["url"]), "labelled"
    for x in links:
        tt = x["text"].lower().rstrip("/")
        if DOMAIN_TEXT.match(tt) and reg_domain(CF.host_of(tt if "://" in tt else "https://" + tt)) == reg_domain(x["host"]):
            return norm_site(x["url"]), "domain-text"
    for x in links:
        if re.search(r"school|academy|prep|college|universit|cemeter|charities|foundation|ccd|youth|camp|retreat|"
                     r"knights|kofc|council", x["host"] + " " + x["text"], re.I):
            continue
        return norm_site(x["url"]), "first-external"
    return None, None


def finish_details(raw: list[dict], chrome_base: set[str]) -> list[dict]:
    """Choose each detail page's website after measuring cross-page chrome."""
    cnt = Counter()
    for r in raw:
        for h in {x["host"] for x in r["links"]}:
            cnt[h] += 1
    n = len(raw)
    chrome = set(chrome_base) | {h for h, c in cnt.items() if n >= 4 and c >= max(3, 0.25 * n)}
    out = []
    for r in raw:
        site, basis = choose_website(r["links"], chrome)
        if basis == "first-external" and cnt[CF.host_of(site)] > 3:
            site, basis = None, None
        rec = {k: r.get(k) for k in ("name", "address", "city", "state", "zip", "lat", "lng", "source_url",
                                     "office_phone", "office_email")}
        rec["website"] = site
        rec["website_basis"] = basis
        out.append(rec)
    return out


# ------------------------------------------------------------------------------------------------ output
def finalize_records(recs: list[dict], confidence: str, extracted_by: str, filter_names: bool = True) -> list[dict]:
    """filter_names=False for records from a parish post type (the diocese already filed them as parishes)."""
    out, seen = [], set()
    for r in recs:
        r["name"] = name_clean(r.get("name") or "") if filter_names else clean(r.get("name"))
    recs = [r for r in recs if r["name"] and not hard_reject(r["name"]) and
            (not filter_names or (len(r["name"]) <= 110 and is_parish_record(r["name"])))]
    # a website host shared by > 20% of a list of 10+ is chrome or a data vendor, not each parish's own site
    hc = Counter(CF.host_of(r["website"]) for r in recs if r.get("website"))
    for r in recs:
        if r.get("website") and len(recs) >= 10 and hc[CF.host_of(r["website"])] > max(8, 0.2 * len(recs)):
            r["website"], r["website_basis"] = None, None
    # a coordinate pair shared by 3+ records is page chrome (the chancery map pin), not the parish
    cc = Counter((r.get("lat"), r.get("lng")) for r in recs if r.get("lat") is not None)
    ac = Counter((r.get("address"), r.get("zip")) for r in recs if r.get("address"))
    for r in recs:
        if r.get("lat") is not None and cc[(r.get("lat"), r.get("lng"))] >= 3:
            r["lat"] = r["lng"] = None
        if r.get("address") and ac[(r.get("address"), r.get("zip"))] >= 4:   # the chancery address in a page footer
            r["address"] = r["city"] = r["zip"] = r["state"] = None
    pc = Counter(r.get("office_phone") for r in recs if r.get("office_phone"))
    ec = Counter(r.get("office_email") for r in recs if r.get("office_email"))
    for r in recs:   # one number / mailbox on 4+ parishes is the chancery's footer, not each parish's office
        if r.get("office_phone") and pc[r["office_phone"]] >= 4:
            r["office_phone"] = None
        if r.get("office_email") and ec[r["office_email"]] >= 4:
            r["office_email"] = None
    for r in recs:
        name = clean(r.get("name"))
        if not name:
            continue
        if PV is not None and re.search(r"\b(?:mr|mrs|ms|dr)\.?\s", name, re.I) and PV.private_name_risk(name):
            continue
        k = (re.sub(r"\W+", " ", name.lower()).strip(), CF.host_of(r.get("website") or "") or r.get("address") or r.get("source_url"))
        if k in seen:
            continue
        seen.add(k)
        rec = {"name": name, "type_in_source": r.get("type_in_source"),
               "address": scrub(r.get("address")), "city": r.get("city"), "state": r.get("state"), "zip": r.get("zip"),
               "lat": r.get("lat"), "lng": r.get("lng"), "website": r.get("website"),
               "office_phone": norm_phone(r.get("office_phone")),
               "office_email": email_fits_parish(office_email_ok(r.get("office_email")), name, r.get("website")),
               "website_basis": r.get("website_basis") if r.get("website") else None,
               "source_url": r.get("source_url"), "rest_id": r.get("rest_id"),
               "confidence": confidence, "extracted_by": extracted_by}
        out.append(rec)
    return out


def write_parishes(slug: str, see_name: str, source: str, source_label: str, method: str, cms: str, recs: list[dict],
                   note: str, extra: dict | None = None) -> dict:
    d = OUT / slug
    d.mkdir(parents=True, exist_ok=True)
    meta = {"diocese": slug, "name": see_name, "source": source, "source_label": source_label, "fetched_at": now_iso(),
            "method": method, "cms_guess": cms, "note": note,
            "count": len(recs), "with_website": sum(1 for r in recs if r.get("website")),
            "with_office_phone": sum(1 for r in recs if r.get("office_phone")),
            "with_office_email": sum(1 for r in recs if r.get("office_email")), **(extra or {}), "records": recs}
    (d / "parishes.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", "utf-8")
    return meta


def existing(slug: str) -> dict | None:
    p = OUT / slug / "parishes.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            return None
    return None


def save_reader_status(slug: str, rec: dict) -> None:
    import fcntl
    with STATUS_LOCK, open(str(RAW / "reader_status.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)   # directory_pages.py and directory_html.py may run at the same time
        st = json.loads(READER_STATUS.read_text()) if READER_STATUS.exists() else {"sees": {}}
        st["sees"][slug] = rec
        st["generated_at"] = now_iso()
        st["method"] = ("ingest/us/dioceses/directory_pages.py and directory_html.py (see ingest/us/dioceses/dirlib.py); "
                        "per-see status of the reader pass that follows directory_probe.py")
        st["user_agent"] = CF.UA
        st["counts"] = dict(Counter(v.get("result") for v in st["sees"].values()).most_common())
        READER_STATUS.write_text(json.dumps(st, indent=1, ensure_ascii=False) + "\n")


def load_reader_status() -> dict:
    return json.loads(READER_STATUS.read_text()) if READER_STATUS.exists() else {"sees": {}}


# ------------------------------------------------------------------------------------------------ one see, end to end
def home_dir_links(soup, page_url: str, base_host: str) -> list[str]:
    out = []
    for a in soup.find_all("a", href=True):
        t = clean(a.get_text(" ", strip=True))
        u = urljoin(page_url, a["href"]).split("#")[0]
        if not u.startswith("http") or len(t) > 40:
            continue
        h = CF.host_of(u)
        if (h == base_host or reg_domain(h) == reg_domain(base_host)) and DIR_LINK_TEXT.search(t) and not SKIP_PATH.search(u) \
                and not re.search(r"school|staff|clergy|cemeter|employ|bulletin", t + u, re.I):
            out.append(CF.https_only(u))
    return list(dict.fromkeys(out))


def read_see(see: dict, probe: dict, pol: CF.Polite, refresh: bool = False, max_detail: int = 450,
             budget: int = 900, minutes: float = 25) -> dict:
    """Generic reader: directory list pages first; per-parish detail pages (from the sitemap or the list page's own
    links) when the list gives fewer websites than parishes. Returns {"records": [...], "log": {...}}."""
    t0 = time.time()
    slug = see["slug"]
    base = probe.get("base") or "https://" + CF.host_of(see["website"])
    final = probe.get("directory_page")
    base_host = CF.host_of(base)
    F = Fetcher(slug, base, pol, refresh, budget)
    log = {"lists": [], "detail_pages": 0, "sitemaps": [], "cms_guess": None, "notes": []}
    own = {base_host}
    if final:
        own.add(CF.host_of(final))
    # home page: CMS guess, chrome hosts, directory links
    hs, hr = F.soup(base + "/")
    if hs is None and hr.get("class") in ("dns", "ssl_error", "conn_error", "timeout", "not_found", "http_4xx"):
        h0 = urlparse(base).netloc
        alt = "https://" + (h0[4:] if h0.startswith("www.") else "www." + h0)
        hs2, hr2 = F.soup(alt + "/")
        if hs2 is not None:
            hs, hr = hs2, hr2
            log["notes"].append(f"home page answered on {alt}")
    if hs is not None:
        fh = CF.host_of(hr.get("final_url") or base)
        if fh and fh != base_host:     # the diocese moved (seattlearchdiocese.org -> archseattle.org)
            log["notes"].append(f"home page redirects to {fh}")
            base = "https://" + urlparse(hr.get("final_url")).netloc
            base_host = fh
            F.base = base
    chrome = set()
    cands = []
    if hs is not None:
        own.add(CF.host_of(hr.get("final_url") or base))
        gen = (hs.find("meta", attrs={"name": "generator"}) or {}).get("content", "") if hs.find("meta", attrs={"name": "generator"}) else ""
        log["cms_guess"] = cms_guess(hr["body"], gen)
        chrome = ext_hosts(hs, own)
        cands += home_dir_links(hs, hr.get("final_url") or base, base_host)
    else:
        log["notes"].append(f"home page: {hr.get('class')}")
    pre = []
    if final:
        pre.append(CF.https_only(final))
    for h in (probe.get("doors") or {}).get("wp_search_parish", []) or []:
        u = h.get("url") or ""
        if u.startswith("/"):
            u = base + u
        if re.search(r"/(?:find-a-parish|parishes|parish-directory|our-parishes|parishes-2|parish-finder|parish-map|"
                     r"parishes-contacts|directory|mass-finder|map-of-parishes|places)(?:/|$)", urlparse(u).path, re.I):
            pre.append(CF.https_only(u))
    home_links = cands
    for p in ["/parishes/", "/find-a-parish/", "/parish-directory/", "/parishes", "/our-parishes/"]:
        pre.append(base + p)
    # the home page's own "find a parish" links first (they are what the diocese points people to)
    cands = list(dict.fromkeys(home_links[:4] + pre + home_links[4:]))[:12]
    entries, detail_urls, list_ok, fams = [], [], [], []
    link_names: dict[str, str] = {}
    for c in cands:
        if time.time() - t0 > minutes * 60:
            break
        pages, seen_pages = [c], set()
        while pages and len(seen_pages) < 30:
            u = pages.pop(0)
            if u in seen_pages:
                continue
            seen_pages.add(u)
            s, r = F.soup(u)
            if s is None:
                if u == c:
                    log["lists"].append({"url": u, "class": r.get("class")})
                break
            fu = r.get("final_url") or u
            if CF.host_of(fu) not in own and reg_domain(CF.host_of(fu)) != reg_domain(base_host):
                own.add(CF.host_of(fu))  # moved to a sister domain (e.g. a new diocese domain)
            if log["cms_guess"] in (None, "unknown"):
                log["cms_guess"] = cms_guess(r["body"])
            s2 = soup_of(r["body"])
            # a parish finder on a sister host (aodfinder.org for aod.org): follow it once
            if u == c:
                for a in s2.find_all("a", href=True):
                    fh = CF.host_of(urljoin(fu, a["href"]))
                    if fh and fh not in own and re.search(r"finder|locator|directory", fh) and not INFRA.search(fh) \
                            and not SOCIAL.search(fh) and re.search(r"parish|church|find", a["href"] + a.get_text(), re.I):
                        own.add(fh)
                        fl = urljoin(fu, a["href"])
                        if urlparse(fl).path in ("", "/"):
                            fl = fl.rstrip("/") + "/parishes"
                        pages.append(CF.https_only(fl))
            dets = detail_candidates(s2, fu, CF.host_of(fu), link_names)
            ents = list_entries(s, fu, own, chrome)
            if u == c:
                log["lists"].append({"url": u, "entries": len(ents), "detail_links": len(dets)})
            if len(ents) >= 3:
                entries += ents
                list_ok.append(u)
            if len(dets) >= 5:
                fams.append(dets)
            if len(ents) >= 3 or len(dets) >= 5:
                pages += [x for x in next_pages(s2, fu, CF.host_of(fu)) if x not in seen_pages]
        if len({e['website'] for e in entries}) >= 25 and not detail_urls:
            break
    if fams:
        detail_urls = max(fams, key=len)
        log["detail_family_sizes"] = sorted((len(f) for f in fams), reverse=True)[:6]
    # sitemap detail pages
    pl = ((probe.get("doors") or {}).get("sitemap") or {}).get("parishlike") or []
    sm_start = [x for x in pl if x.endswith(".xml")] + sitemap_starts(F, base)
    sm_urls, used = sitemap_urls(F, sm_start)
    log["sitemaps"] = used
    if len(sm_urls) >= 5:
        detail_urls = sm_urls + detail_urls
    detail_urls = [CF.https_only(u) for u in dict.fromkeys(detail_urls) if not SKIP_PATH.search(u)]
    n_list_sites = len({e["website"] for e in entries})
    recs = []
    for e in entries:
        recs.append(dict(e, source_url=list_ok[0] if list_ok else None, website_basis=e.pop("basis", None),
                         _conf="list"))
    # details only when the list did not already give (nearly) one website per parish page
    if detail_urls and n_list_sites < 0.8 * len(detail_urls):
        raw = []
        for u in detail_urls[:max_detail]:
            if time.time() - t0 > minutes * 60 or F.n_net >= budget:
                log["notes"].append("stopped early (time or request budget); rerun resumes from the cache")
                break
            s, r = F.soup(u)
            if s is None:
                continue
            d = detail_record(s, r.get("final_url") or u, own, see.get("name", ""), link_names.get(u))
            if d:
                raw.append(d)
        log["detail_pages"] = len(raw)
        dets = finish_details(raw, chrome)
        # merge: detail pages fill list entries' gaps; new parishes appended
        by_name = {r["name"].lower(): r for r in recs}
        for d in dets:
            m = by_name.get(d["name"].lower())
            if m:
                for k in ("address", "city", "state", "zip", "lat", "lng", "office_phone", "office_email"):
                    m[k] = m.get(k) or d.get(k)
                if not m.get("website") and d.get("website"):
                    m["website"], m["website_basis"] = d["website"], d["website_basis"]
            else:
                recs.append(dict(d, _conf="page"))
    log["requests_net"] = F.n_net
    log["requests_cached"] = F.n_cache
    log["fetch_classes"] = dict(F.classes)
    log["seconds"] = round(time.time() - t0)
    return {"records": recs, "log": log, "base": base}


# ------------------------------------------------------------------------------------------------ office contacts
# Matthew, 2026-11-01: the parish OFFICE phone, the general office e-mail and the mailing address are institutional data
# and are kept (office_phone, office_email, address). A named person's phone or e-mail (pastor, staff, a lay contact)
# is never kept: an e-mail whose local part reads as a person's name is dropped, and a phone is taken only from an
# office/parish/phone label or a vendor's parish-level phone field, never next to a person's title or name.
EMAIL_ONE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
PHONE_ONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-](\d{4})(?!\d)")
INSTIT_LOCAL = re.compile(r"office|parish|info|secretar|church|rectory|admin|contact|reception|frontdesk|front\.desk|"
                          r"welcome|hello|mail|team|staff$|business|ministry|parroquia|iglesia|oficina|mission|cathedral|"
                          r"basilica|shrine|newman|catholic|^st[a-z]|^saint|^ss|holy|sacred|ourlady|olo|olg|olph|blessed|"
                          r"immaculate|christ|trinity|resurrection|nativity|assumption|annunciation|guadalupe", re.I)
PERSON_CTX = re.compile(r"\b(?:fr|father|rev|reverend|msgr|monsignor|deacon|dcn|sr|sister|br|brother|mr|mrs|ms|dr|"
                        r"pastor|administrator|vicar|director|coordinator|manager|principal|secretary to|dre|youth|"
                        r"music|bookkeeper|business manager|contact person|attn)\b\.?", re.I)
OFFICE_CTX = re.compile(r"\b(?:office|parish|rectory|phone|tel|telephone|main|call|church|tel[eé]fono|oficina)\b", re.I)


def _given_name_led(local: str) -> bool:
    if PV is None:
        return bool(re.fullmatch(r"[a-z]+[._-][a-z]+", local, re.I))
    try:
        given, _amb = PV._given_names()
        sur = PV._surnames()
    except Exception:  # noqa: BLE001
        return bool(re.fullmatch(r"[a-z]+[._-][a-z]+", local, re.I))
    parts = [p for p in re.split(r"[._\-+0-9]+", local.lower()) if p]
    if len(parts) >= 2 and (parts[0] in given or parts[-1] in sur or len(parts[0]) == 1):
        return True                                             # john.smith, j.smith, mary_jones
    if len(parts) == 1:
        p = parts[0]
        if p in given or p in sur:
            return True                                         # maria@, kowalski@
        for g in sorted((x for x in given if len(x) >= 4 and p.startswith(x)), key=len, reverse=True)[:1]:
            if p[len(g):] in sur or (len(p) - len(g) >= 3 and not INSTIT_LOCAL.search(p)):
                return True                                     # johnsmith@
        if re.fullmatch(r"[a-z]\w{2,}", p) and p[1:] in sur:
            return True                                         # jsmith@
    return False


def office_email_ok(e: str | None) -> str | None:
    if not e:
        return None
    m = EMAIL_ONE.search(str(e))
    if not m:
        return None
    local, dom = m.group(1), m.group(2).lower()
    if re.search(r"example|domain\.com|sentry|wixpress|\.png$|\.jpg$", dom):
        return None
    if _given_name_led(local) and not re.search(r"office|parish|info|church|rectory|secretar", local, re.I):
        return None
    if re.match(r"(?:fr|father|rev|msgr|deacon|dcn|sr|sister|pastor)[._-]?[a-z]", local, re.I) and \
            not re.search(r"office|parish|info|church|rectory", local, re.I):
        return None                                             # frjoe@, deaconmike@, pastor.tom@
    if re.fullmatch(r"(?:sec|secretary)", local, re.I):
        return f"{local}@{dom}".lower()
    if not INSTIT_LOCAL.search(local) and re.search(r"[a-z]", local, re.I) and len(local) < 4:
        return None
    return f"{local}@{dom}".lower()


def norm_phone(p: str | None) -> str | None:
    m = PHONE_ONE.search(str(p or ""))
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else None


def office_contacts(block) -> tuple[str | None, str | None]:
    """(office_phone, office_email) from a parish's own block / page area (a bs4 element)."""
    phone = email = None
    if block is None:
        return None, None
    for a in block.find_all("a", href=True):
        h = a["href"]
        pre = clean(a.find_previous(string=True) or "")[-50:]
        ctx = pre + " " + clean(a.get_text(" ", strip=True)) + " " + clean(a.get("aria-label") or "")
        if h.lower().startswith("mailto:") and not email:
            if not PERSON_CTX.search(pre):
                email = office_email_ok(unquote(h[7:]).split("?")[0])
        elif h.lower().startswith("tel:") and not phone:
            if not PERSON_CTX.search(pre) and (OFFICE_CTX.search(ctx) or not re.search(r"[A-Z][a-z]+ [A-Z][a-z]+\s*:?\s*$", pre)):
                phone = norm_phone(unquote(h[4:]))
    txt = block.get_text(" | ", strip=True)
    if not phone:
        for m in PHONE_ONE.finditer(txt):
            pre = txt[max(0, m.start() - 45):m.start()]
            seg = pre.split("|")[-2:] if "|" in pre else [pre]
            seg = " ".join(seg)
            if re.search(r"fax", seg, re.I) or PERSON_CTX.search(seg):
                continue
            if OFFICE_CTX.search(seg) or re.search(r"\|\s*$", pre) or not re.search(r"[A-Z][a-z]+ [A-Z][a-z]+", seg):
                phone = norm_phone(m.group(0))
                break
    if not email:
        for m in EMAIL_ONE.finditer(txt):
            pre = txt[max(0, m.start() - 45):m.start()]
            if PERSON_CTX.search(pre.split("|")[-1] if "|" in pre else pre):
                continue
            email = office_email_ok(m.group(0))
            if email:
                break
    return phone, email


def email_fits_parish(email: str | None, name: str, website: str | None) -> str | None:
    """Second gate, with the record in hand: keep an office e-mail only when its local part is plainly institutional
    (office, parish, info, secretary, a saint/church word) or shares a 5-letter run with the parish's name or its
    website's domain (stvincentalbany@ for St. Vincent, Albany). Anything else may be a person's mailbox: dropped."""
    if not email:
        return None
    local = email.split("@")[0].lower()
    if re.search(r"office|parish|info|secretar|church|rectory|admin|contact|reception|welcome|hello|business|"
                 r"parroquia|iglesia|oficina|^sec$|cathedral|basilica|shrine|newman|mission", local):
        return email
    key = re.sub(r"[^a-z]", "", local)
    hay = re.sub(r"[^a-z]", "", (name or "").lower().replace("saint", "st")) + " " + \
        re.sub(r"[^a-z]", "", (name or "").lower()) + " " + re.sub(r"[^a-z]", "", CF.host_of(website or "").split(".")[0])
    for i in range(0, max(0, len(key) - 4)):
        if key[i:i + 5] in hay:
            return email
    return None
