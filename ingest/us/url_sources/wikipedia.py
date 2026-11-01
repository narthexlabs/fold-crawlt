"""Parish website URLs from English Wikipedia (lane G7).  Facts (URLs, names, coordinates) only; text is CC BY-SA 4.0.

    python3 -I ingest/us/url_sources/wikipedia.py discover   # find list pages + state categories (API search/categories)
    python3 -I ingest/us/url_sources/wikipedia.py lists      # fetch + parse each list page's wikitext
    python3 -I ingest/us/url_sources/wikipedia.py articles   # infobox website/coords for linked articles + state categories
    python3 -I ingest/us/url_sources/wikipedia.py build      # write wikipedia.json + wikipedia/<ST>.websites.json
    python3 -I ingest/us/url_sources/wikipedia.py all        # all of the above; resumable (raw API responses are cached)

MediaWiki API only, honest UA, one request at a time, >= 1.1 s apart. Raw responses: data/raw/us/url_sources/wikipedia/
(gitignored); a rerun reads the cache and makes no request. Shape of the output websites files matches
data/real/us/discovermass/<ST>.websites.json (consumer: ingest/us/sites/run_state.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data/raw/us/url_sources/wikipedia"
OUT = ROOT / "data/real/us/url_sources"
API = "https://en.wikipedia.org/w/api.php"
UA = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"
LICENSE = "CC BY-SA 4.0 (text); URLs are facts"
STATE_FILE = OUT / "wikipedia_state.json"      # discovered pages + parsed entries (resume point)

STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia (U.S. state)": "GA", "Hawaii": "HI", "Idaho": "ID",
          "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
          "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
          "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
          "New York (state)": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
          "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
          "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington (state)": "WA", "West Virginia": "WV",
          "Wisconsin": "WI", "Wyoming": "WY", "Washington, D.C.": "DC"}
STATE_NAMES = {k.split(" (")[0]: v for k, v in STATES.items()}
STATE_NAMES["District of Columbia"] = "DC"

BAD_HOST = re.compile(
    r"(?:^|\.)(?:wikipedia\.org|wikimedia\.org|wikidata\.org|wikimediafoundation\.org|mediawiki\.org|archive\.org|archive\.is|"
    r"facebook\.com|fb\.com|instagram\.com|twitter\.com|x\.com|youtube\.com|youtu\.be|tiktok\.com|linkedin\.com|pinterest\.com|"
    r"flickr\.com|google\.[a-z.]+|goo\.gl|yelp\.com|tripadvisor\.com|findagrave\.com|nps\.gov|nrhp\.com|loc\.gov|"
    r"discovermass\.com|masstimes\.org|parishesonline\.com|usccb\.org|catholic-hierarchy\.org|gcatholic\.org|"
    r"nytimes\.com|latimes\.com|washingtonpost\.com|wsj\.com|doi\.org|worldcat\.org|jstor\.org|books\.google\.com|"
    r"geohack\.toolforge\.org|toolforge\.org|census\.gov|ancestry\.com|newspapers\.com|bing\.com|apple\.com|waymarking\.com|"
    r"patch\.com|dioceseofbrooklyn\.org|archny\.org)$", re.I)
DIOCESE_HOST = re.compile(r"(?:^|\.)(?:archdiocese|diocese|archdio|archbalt|rcab|archchicago|arlingtondiocese|"
                          r"adw|adlarc|la-archdiocese|archatl|archgh|dioceseofsandiego|sfarch|dio[a-z]*)\.[a-z]+$", re.I)
SCHOOL_HOST = re.compile(r"school|academy|prep\b|highschool|college|universit|seminary|\.edu$", re.I)
GOV_HOST = re.compile(r"\.gov$|\.mil$", re.I)

_last = [0.0]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def api(params: dict, tag: str = "") -> dict:
    """Cached GET to the MediaWiki API."""
    p = dict(params, format="json", formatversion="2", maxlag="5")
    q = urllib.parse.urlencode(sorted(p.items()))
    key = hashlib.sha1(q.encode()).hexdigest()[:16]
    RAW.mkdir(parents=True, exist_ok=True)
    f = RAW / f"{(tag or p.get('action', 'x'))[:24]}-{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(14):
        wait = 2.0 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            ra = getattr(getattr(e, "headers", None), "get", lambda *_: None)("Retry-After")
            print("  api error", repr(e)[:80], "retry-after", ra, file=sys.stderr)
            time.sleep(min(int(ra) if ra and str(ra).isdigit() else 10 + 5 * attempt, 90))   # 429s from the shared egress: back off, never hammer
            continue
        if "error" in d and d["error"].get("code") == "maxlag":
            time.sleep(6)
            continue
        d["_params"] = p
        f.write_text(json.dumps(d, ensure_ascii=False))
        return d
    raise RuntimeError("API failed: " + q[:200])


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"pages": {}, "categories": {}, "entries": [], "articles": {}}


def save_state(st: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=0) + "\n")


# ------------------------------------------------------------------------------------------------ dioceses
def load_sees() -> list[dict]:
    return json.loads((ROOT / "data/real/us/dioceses.json").read_text())["dioceses"]


def _nm(s: str) -> str:
    s = re.sub(r"^(?:the\s+)?(?:roman\s+catholic\s+)?(?:arch)?(?:diocese|eparchy|archeparchy)\s+of\s+", "", s.strip(), flags=re.I)
    return re.sub(r"[^a-z]", "", s.lower().replace("saint", "st"))


class Dio:
    def __init__(self):
        self.sees = load_sees()
        self.by = {}
        for s in self.sees:
            self.by.setdefault(_nm(s["name"]), s)

    def match_title(self, title: str):
        """List page title -> (diocese name, state) or (None, None)."""
        m = re.search(r"((?:Arch)?[Dd]iocese|Eparchy|Archeparchy|Archdiocese) of (?:the )?(.+?)(?:\s*\(.*\))?$", title)
        if m:
            s = self.by.get(_nm(m.group(2)))
            if s:
                return s["name"], s["see_state"]
            return m.group(0), STATE_NAMES.get(m.group(2).split(",")[0].strip())
        return None, None


def state_of_text(t: str):
    m = re.search(r"\b(" + "|".join(re.escape(n) for n in sorted(STATE_NAMES, key=lambda x: -len(x))) + r")\b", t or "")
    return STATE_NAMES[m.group(1)] if m else None


def state_from_title(title: str):
    for n, ab in sorted(STATE_NAMES.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(n) + r"\b", title):
            return ab
    return None


# ------------------------------------------------------------------------------------------------ discover
LIST_TITLE = re.compile(r"^List of (?:(?:Roman )?Catholic )?(?:churches|parishes|parish churches|cathedrals|basilicas|missions)\b", re.I)
SEARCHES = ['intitle:"List of churches" diocese', 'intitle:"List of Catholic churches"', 'intitle:"List of parishes" diocese',
            'intitle:"List of Roman Catholic churches"', 'intitle:"List of churches in the Archdiocese"',
            'intitle:"List of churches in the Diocese"', 'intitle:"List of Catholic parishes"',
            'intitle:"List of Catholic churches" "United States"', 'intitle:"List of Roman Catholic parishes"',
            'intitle:"List of churches" "Roman Catholic Church in"', 'intitle:"Roman Catholic churches in" list']
SEED_CATS = ["Category:Lists of Roman Catholic churches in the United States", "Category:Lists of churches in the United States by diocese",
             "Category:Lists of churches in the United States", "Category:Lists of Catholic churches in the United States",
             "Category:Lists of Roman Catholic churches"]


def cat_members(cat: str, types="page|subcat") -> list[dict]:
    out, cont = [], {}
    while True:
        d = api(dict(action="query", list="categorymembers", cmtitle=cat, cmlimit="500", cmtype=types, **cont), "catmem")
        out += d.get("query", {}).get("categorymembers", [])
        if "continue" in d:
            cont = d["continue"]
        else:
            return out


def discover(st: dict, dio: Dio, dio_cats: bool = False) -> None:
    cand: dict[str, str] = {}   # title -> how found
    for q in SEARCHES:
        cont = {}
        for _ in range(6):
            d = api(dict(action="query", list="search", srsearch=q, srlimit="50", srnamespace="0", **cont), "search")
            for r in d.get("query", {}).get("search", []):
                cand.setdefault(r["title"], "search")
            if "continue" in d:
                cont = {"sroffset": d["continue"]["sroffset"]}
            else:
                break
    seen_cats, queue = set(), list(SEED_CATS)
    for dsee in (dio.sees if dio_cats else []):   # each diocese's own category (second pass: `discover2`)
        base = re.sub(r"^Roman Catholic ", "", dsee["name"])
        queue.append(f"Category:Roman Catholic {base}")
        queue.append(f"Category:{base}")
    depth = {c: 0 for c in queue}
    while queue:
        c = queue.pop(0)
        if c in seen_cats:
            continue
        seen_cats.add(c)
        try:
            mem = cat_members(c)
        except RuntimeError:
            continue
        st["categories"][c] = len(mem)
        for m in mem:
            t = m["title"]
            if t.startswith("Category:"):
                if depth[c] < 2 and re.search(r"list|churches|diocese|parishes", t, re.I) and "Orthodox" not in t:
                    depth.setdefault(t, depth[c] + 1)
                    queue.append(t)
            elif LIST_TITLE.match(t) or re.match(r"^List of .*(?:churches|parishes)", t):
                cand.setdefault(t, "category:" + c)
    for t, how in sorted(cand.items()):
        if not (LIST_TITLE.match(t) or re.match(r"^List of .*(?:churches|parishes)", t)):
            continue
        dn, stt = dio.match_title(t)
        stt = stt or state_from_title(t)
        if not stt and "United States" not in t:
            continue                                # not recognisably US
        if re.search(r"Orthodox|Lutheran|Episcopal|Baptist|Methodist|Presbyterian|Anglican|Mormon|Latter", t):
            continue
        st["pages"].setdefault(t, {"title": t, "diocese": dn, "state": stt, "found_by": how})
    save_state(st)
    print(f"discover: {len(st['pages'])} candidate US list pages, {len(seen_cats)} categories read")


# ------------------------------------------------------------------------------------------------ parse lists
URL_RE = re.compile(r"https?://[^\s\]\|<>{}\"']+", re.I)


def host_of(u: str) -> str:
    h = urllib.parse.urlparse(u).netloc.lower().split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def norm_url(u: str) -> str | None:
    u = (u or "").strip().strip("<>").rstrip(".,;)")
    if not u:
        return None
    if not re.match(r"^https?://", u, re.I):
        if re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", u):
            u = "http://" + u
        else:
            return None
    u = re.sub(r"\s", "", u)
    p = urllib.parse.urlparse(u)
    if not p.netloc or "." not in p.netloc:
        return None
    return u


def good_site(u: str | None) -> bool:
    if not u:
        return False
    h = host_of(u)
    if BAD_HOST.search(h) or GOV_HOST.search(h):
        return False
    return True


def strip_refs(t: str) -> str:
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<ref[^>/]*/>", "", t)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.S)
    t = re.sub(r"\{\{\s*(?:cite|citation|webarchive|web archive|sfn|harv)[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}", "", t, flags=re.I)
    return t


def ext_urls(blob: str) -> list[str]:
    """External URLs in a wikitext fragment: [url label], {{official website|..}}, {{URL|..}}, bare."""
    out = []
    for m in re.finditer(r"\{\{\s*(?:official website|official url|official|url|website)\s*\|\s*(?:1\s*=\s*)?([^|}]+)", blob, re.I):
        u = norm_url(m.group(1))
        if u:
            out.append(u)
    for m in URL_RE.finditer(blob):
        out.append(m.group(0).rstrip(".,;)"))
    seen, res = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


def clean_text(s: str) -> str:
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"'''?|<[^>]+>|&nbsp;", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -–—,;:|")


def wikilinks(blob: str) -> list[tuple[str, str]]:
    res = []
    for m in re.finditer(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]", blob):
        tgt = m.group(1).strip()
        if re.match(r"^(?:File|Image|Category|Wikipedia|Template|Help|Special|Portal|Talk):", tgt, re.I):
            continue
        res.append((tgt, clean_text(m.group(2) or tgt)))
    return res


GENERIC_HEAD = re.compile(r"see also|references|external links|notes|further reading|sources|former|closed|defunct|history|"
                          r"churches|parishes|cathedral|list|deaneries?|vicariate|region|other|missions?|chapels?|national|"
                          r"institutes?|oratories|shrines?|colleges?|campus|navigation|bibliography", re.I)


def parse_list(title: str, text: str, info: dict) -> list[dict]:
    text = strip_refs(text)
    entries = []
    heading = None
    colmap: dict[str, int] = {}
    in_table = False
    rows: list[tuple[str, str | None, dict | None]] = []      # (blob, heading, colmap)
    # split into lines, assembling table rows
    cur: list[str] = []

    def flush():
        nonlocal cur
        if cur:
            rows.append(("\n".join(cur), heading, dict(colmap)))
        cur = []
    for line in text.split("\n"):
        h = re.match(r"^(={2,6})\s*(.*?)\s*\1\s*$", line)
        if h:
            flush()
            heading = clean_text(h.group(2))
            continue
        if line.startswith("{|"):
            flush(); in_table = True; colmap = {}; hdr_cells = []
            continue
        if line.startswith("|}"):
            flush(); in_table = False; colmap = {}
            continue
        if in_table:
            if line.startswith("!"):
                cells = [clean_text(c) for c in re.split(r"!!|\n!", line.lstrip("!"))]
                cells = [re.sub(r"^.*\|", "", c).strip() if "|" in c else c for c in cells]
                if not colmap and cells:
                    colmap = {c.lower(): i for i, c in enumerate(cells)}
                continue
            if line.startswith("|-"):
                flush()
                continue
            if line.startswith("|") or cur:
                cur.append(line)
            continue
        if line.lstrip().startswith(("*", "#")) and not line.lstrip().startswith("**") or line.lstrip().startswith("**"):
            flush()
            rows.append((line.lstrip("*#: ").strip(), heading, None))
    flush()
    for blob, hd, cm in rows:
        urls = [u for u in ext_urls(blob)]
        links = wikilinks(blob)
        if not links and not urls:
            continue
        name = city = None
        city_src = None
        if cm is not None:                               # table row
            cells = re.split(r"\n\|(?!\|)|\|\|", "\n" + blob)
            cells = [re.sub(r"^\s*\|", "", c) for c in cells if c.strip()]
            cells = [re.sub(r"^[^|\[{]*\|(?!\|)", "", c, count=1) if re.match(r"^\s*[\w\s=\"'%:;#-]+\|", c) and "[[" not in c.split("|")[0] else c for c in cells]
            idx_name = next((i for k, i in cm.items() if re.search(r"name|church|parish|cathedral", k)), 0)
            idx_city = next((i for k, i in cm.items() if re.search(r"city|town|location|municipality|neighborhood|community|address", k)), None)
            if idx_name < len(cells):
                nl = wikilinks(cells[idx_name])
                name = nl[0][1] if nl else clean_text(cells[idx_name])
                cl = nl[0][0] if nl else None
            else:
                cl = None
            if idx_city is not None and idx_city < len(cells):
                city = clean_text(cells[idx_city]) or None
                city_src = "table column"
            if not name:
                continue
            article = cl
        else:
            if links:
                name, article = links[0][1], links[0][0]
            else:
                name, article = clean_text(blob.split("[http")[0]), None
            m = re.search(r"\]\]\s*(?:[-–—,(]|in|\bat\b)\s*([^\[\]{}()<>|]{2,40})", blob)
            if m and clean_text(m.group(1)):
                city, city_src = clean_text(m.group(1)).split(",")[0].strip(), "after name"
            elif len(links) > 1 and re.search(r"[-–—,]", blob):
                city, city_src = links[1][1], "second link"
        if not city and hd and not GENERIC_HEAD.search(hd):
            city, city_src = hd, "section heading"
        if not name or len(name) > 120 or re.match(r"^(?:name|church|parish)$", name, re.I):
            continue
        if re.match(r"^(?:Arch)?diocese of|^Ecclesiastical|^Roman Catholic (?:Arch)?diocese", name, re.I) or \
                (article and re.search(r"(?:Arch)?diocese of|^[A-Z][a-z]+, [A-Z][a-z]+$", article) and not re.search(r"church|cathedral|parish|basilica|shrine", article, re.I)):
            continue                                     # a diocese or a city, not a church
        # keep the article link only when the entry is not itself the only external link
        e = {"name": name, "city": city, "city_source": city_src, "article": article if article and not article.startswith("List of") else None,
             "urls": urls}
        entries.append(e)
    return entries


NOT_CATHOLIC = re.compile(r"Anabaptist|Anglo-Catholic|Congregational|Norwegian|Pentecostal|Unitarian|megachurch|Orthodox|"
                          r"Lutheran|Episcopal|Baptist|Methodist|Presbyterian|Anglican|Mormon|Latter|Landmark|Swedish|German|"
                          r"Danish|Finnish|Icelandic|Scandinavian|Quaker|Amish|Mennonite|Reformed|Adventist|Moravian|Coptic|Armenian",
                          re.I)


def wanted(t: str) -> bool:
    """A Catholic, US list page: a diocese list, or a Catholic/cathedral/parish list."""
    if NOT_CATHOLIC.search(t) and not re.search(r"Catholic|diocese|archdiocese", t, re.I):
        return False
    return bool(re.search(r"Catholic|diocese|archdiocese|parish|cathedral|basilica", t, re.I))


def run_lists(st: dict, dio: Dio) -> None:
    titles = sorted(t for t in st["pages"] if wanted(t))
    for i in range(0, len(titles), 20):
        chunk = [t for t in titles[i:i + 20] if "parsed" not in st["pages"][t]]
        for t in chunk:
            try:
                d = api(dict(action="parse", page=t, prop="wikitext|revid", redirects="1"), "parse")
            except RuntimeError as e:
                st["pages"][t]["parsed"] = "error"
                continue
            pg = d.get("parse")
            if not pg:
                st["pages"][t]["parsed"] = "missing"
                continue
            info = st["pages"][t]
            wt = pg["wikitext"]
            ents = parse_list(t, wt, info)
            info.update(parsed=len(ents), revid=pg.get("revid"), fetched_at=now_iso(), n_urls=sum(1 for e in ents if e["urls"]),
                        source_url="https://en.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_")))
            st["entries"] = [e for e in st["entries"] if e["list_page"] != t]
            for e in ents:
                e.update(list_page=t, diocese=info.get("diocese"), state=info.get("state"), source_url=info["source_url"])
                st["entries"].append(e)
        save_state(st)
    print(f"lists: {sum(1 for p in st['pages'].values() if isinstance(p.get('parsed'), int))} pages parsed, {len(st['entries'])} entries")


# ------------------------------------------------------------------------------------------------ articles
def parse_infobox(wt: str) -> dict:
    wt = strip_refs(wt)
    res = {}
    m = re.search(r"\|\s*(?:website|url|web|official[_ ]website|homepage)\s*=\s*(.*)", wt, re.I)
    if m:
        line = m.group(1)
        # value may continue to the next '|' parameter; take this line only plus template
        us = ext_urls(line)
        if not us:
            t = re.sub(r"\{\{[^|}]*\|\s*(?:1\s*=\s*)?([^|}]+)[^}]*\}\}", r"\1", line)
            u = norm_url(t.split()[0] if t.split() else "")
            us = [u] if u else []
        res["urls"] = us
    c = re.search(r"\{\{\s*(?:coord|coordinates)\s*\|([^}]*)\}\}", wt, re.I)
    if c:
        ll = coord_parse(c.group(1))
        if ll:
            res["lat"], res["lng"] = ll
    return res


def coord_parse(s: str):
    parts = [p.strip() for p in s.split("|") if p.strip() and "=" not in p]
    nums, hemi = [], []
    for p in parts:
        if re.match(r"^-?\d+(?:\.\d+)?$", p):
            nums.append(float(p))
        elif p in ("N", "S", "E", "W"):
            hemi.append(p)
            if len(hemi) == 2:
                break
        else:
            break
    try:
        if hemi and len(hemi) == 2:
            k = len(nums) // 2
            la, lo = nums[:k], nums[k:]
            lat = sum(abs(v) / 60 ** i for i, v in enumerate(la))
            lng = sum(abs(v) / 60 ** i for i, v in enumerate(lo))
            return (round(-lat if hemi[0] == "S" else lat, 6), round(-lng if hemi[1] == "W" else lng, 6))
        if len(nums) >= 2:
            return (round(nums[0], 6), round(nums[1], 6))
    except Exception:  # noqa: BLE001
        pass
    return None


def fetch_articles(st: dict, titles: list[str]) -> None:
    todo = [t for t in dict.fromkeys(titles) if t not in st["articles"]]
    print(f"articles: {len(todo)} to fetch")
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        d = api(dict(action="query", prop="revisions|coordinates", titles="|".join(batch), rvprop="content", rvslots="main",
                     redirects="1", colimit="max"), "art")
        q = d.get("query", {})
        redir = {r["from"]: r["to"] for r in q.get("redirects", []) + q.get("normalized", [])}
        pages = {p["title"]: p for p in q.get("pages", [])}
        for t in batch:
            tt = t
            for _ in range(2):
                tt = redir.get(tt, tt)
            p = pages.get(tt)
            rec = {"title": tt, "fetched_at": now_iso()}
            if p and not p.get("missing"):
                revs = p.get("revisions") or []
                wt = revs[0]["slots"]["main"]["content"] if revs else ""
                rec.update(parse_infobox(wt))
                rec["is_church"] = bool(re.search(r"infobox (?:church|religious building|building|nrhp|historic|cathedral|monastery|mosque|synagogue)", wt, re.I)) \
                    or bool(re.search(r"Catholic", wt[:6000]))
                if p.get("coordinates") and "lat" not in rec:
                    rec["lat"], rec["lng"] = p["coordinates"][0]["lat"], p["coordinates"][0]["lon"]
                elif p.get("coordinates"):
                    pass
                rec["source_url"] = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(tt.replace(" ", "_"))
            else:
                rec["missing"] = True
            st["articles"][t] = rec
        if (i // 50) % 10 == 9:
            save_state(st)
    save_state(st)


def run_articles(st: dict, dio: Dio) -> None:
    # 1) articles linked from list entries
    fetch_articles(st, [e["article"] for e in st["entries"] if e.get("article")])
    # 2) state categories "Roman Catholic churches in <State>" (articles in the category and its subcategories, depth 1)
    st.setdefault("cat_articles", {})
    for sname, ab in STATES.items():
        base = sname.split(" (")[0]
        for cat in (f"Category:Roman Catholic churches in {base}", f"Category:Roman Catholic cathedrals in {base}"):
            if cat in st["cat_articles"]:
                continue
            try:
                mem = cat_members(cat)
            except RuntimeError:
                continue
            arts = [m["title"] for m in mem if not m["title"].startswith("Category:") and not m["title"].startswith("List of")]
            subs = [m["title"] for m in mem if m["title"].startswith("Category:") and re.search(r"churches|cathedrals|basilicas", m["title"], re.I)]
            for sc in subs:
                try:
                    arts += [m["title"] for m in cat_members(sc, "page") if not m["title"].startswith("List of")]
                except RuntimeError:
                    pass
            st["cat_articles"][cat] = {"state": ab, "titles": sorted(set(arts)), "source_url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(cat.replace(" ", "_"))}
        save_state(st)
    allc = [t for v in st["cat_articles"].values() for t in v["titles"]]
    print(f"category articles: {len(allc)}")
    fetch_articles(st, allc)


# ------------------------------------------------------------------------------------------------ build
def dom_key(u: str) -> str:
    h = host_of(u)
    return h


def build(st: dict, dio: Dio) -> None:
    fetched = now_iso()
    recs = []                                           # one per parish/church with a usable site
    for e in st["entries"]:
        art = st["articles"].get(e["article"]) if e.get("article") else None
        urls = [u for u in e["urls"] if good_site(u)]
        how = "list page external link"
        if not urls and art and art.get("urls"):
            urls = [u for u in art["urls"] if good_site(u)]
            how = "article infobox website"
        e_state = e["state"] or state_of_text(" ".join(filter(None, [e.get("article"), e["city"]])))
        recs.append(dict(name=e["name"], city=e["city"], state=e_state, diocese=e["diocese"], website=urls[0] if urls else None,
                         website_via=how if urls else None, lat=(art or {}).get("lat"), lng=(art or {}).get("lng"),
                         article=e.get("article"), source_url=e["source_url"],
                         article_url=(art or {}).get("source_url"), fetched_at=fetched, license=LICENSE))
    seen_art = {r["article"] for r in recs if r["article"]}
    for cat, v in st.get("cat_articles", {}).items():
        for t in v["titles"]:
            a = st["articles"].get(t)
            if not a or a.get("missing") or t in seen_art:
                continue
            urls = [u for u in a.get("urls", []) if good_site(u)]
            recs.append(dict(name=a["title"], city=None, state=v["state"], diocese=None, website=urls[0] if urls else None,
                             website_via="article infobox website" if urls else None, lat=a.get("lat"), lng=a.get("lng"),
                             article=t, source_url=v["source_url"], article_url=a.get("source_url"), fetched_at=fetched, license=LICENSE))
            seen_art.add(t)
    # state fallback: unknown state -> try diocese match, else leave null
    recs = [r for r in recs if r["state"] or r["website"]]
    NOISE = re.compile(r"Episcopal|Anglican|Anglo-Catholic|Lutheran|Methodist|Baptist|Presbyterian|Orthodox|United Kingdom|England|Australia|"
                       r"Canada|Ireland|Scotland|Wales|New Zealand|Eastern Orthodox|Unitarian|Congregational", re.I)
    TOWN = re.compile(r"\.(?:ny|pa|nj|oh|ma|ct|nh|vt|me|ri|md|va|il|mi|wi|mn|ia|tx|fl|ca|wa|or|co)\.us$|(?:^|\.)town|village|city|county|"
                      r"chamber|historical|museum|library|\.org/?$" if False else r"\.[a-z]{2}\.us$|^(?:town|village|city|county)of|chamber|historical|museum|library", re.I)
    def keep(r):
        if not r["website"]:
            return True
        h = host_of(r["website"])
        txt = " ".join(filter(None, [r["name"], r["city"], r["article"]]))
        if NOISE.search(txt) or TOWN.search(h):
            return False
        if not r["state"] and not re.search(r"Catholic|St\.|Saint|Our Lady|Holy|Sacred|Cathedral|Basilica|Shrine|Mission", txt):
            return False
        return True
    recs = [r for r in recs if keep(r)]
    withsite = [r for r in recs if r["website"] and not SCHOOL_HOST.search(host_of(r["website"]))
                and not (DIOCESE_HOST.search(host_of(r["website"])) and re.search(r"diocese|archdiocese", host_of(r["website"]), re.I))]
    diocese_hosts = {host_of(s["website"]) for s in dio.sees if s.get("website")}
    withsite = [r for r in withsite if host_of(r["website"]) not in diocese_hosts]
    # deduplicate by domain
    by_dom: dict[str, list[dict]] = defaultdict(list)
    for r in withsite:
        by_dom[dom_key(r["website"])].append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    pages = [{k: v for k, v in p.items() if k in ("title", "diocese", "state", "found_by", "parsed", "n_urls", "revid", "fetched_at", "source_url")}
             for p in st["pages"].values()]
    per_state: dict[str, list[dict]] = defaultdict(list)
    for dom, rs in sorted(by_dom.items()):
        rs.sort(key=lambda r: (not r["state"], r["name"]))
        stt = next((r["state"] for r in rs if r["state"]), None) or "unresolved"
        per_state[stt].append({"url": rs[0]["website"], "domain": dom, "churches": [
            {"slug": "wikipedia:" + re.sub(r"[^a-z0-9]+", "-", (r["article"] or r["name"]).lower()).strip("-"), "name": r["name"],
             "city": r["city"], "diocese": r["diocese"], "lat": r["lat"], "lng": r["lng"], "wikipedia_article": r["article_url"],
             "source_url": r["source_url"], "website_via": r["website_via"]} for r in rs]})
    (OUT / "wikipedia").mkdir(exist_ok=True)
    for old in (OUT / "wikipedia").glob("*.websites.json"):
        old.unlink()
    for stt, ws in per_state.items():
        (OUT / "wikipedia" / f"{stt}.websites.json").write_text(json.dumps({
            "source": "English Wikipedia (list pages and article infoboxes, MediaWiki API)", "source_label": "secondary-wikipedia",
            "state": stt, "built_at": fetched, "consumer": "lane G4 national parish-site crawler", "license": LICENSE,
            "note": "URLs as listed on Wikipedia; unverified; several churches may share one URL. Social, diocese, school, news and archive hosts dropped.",
            "counts": {"entries_with_website": sum(len(w["churches"]) for w in ws), "distinct_websites": len(ws), "social_only": 0},
            "websites": ws, "social_only": []}, ensure_ascii=False, indent=1) + "\n")
    summary = {st_: len(w) for st_, w in sorted(per_state.items(), key=lambda x: -len(x[1]))}
    (OUT / "wikipedia.json").write_text(json.dumps({
        "source": "English Wikipedia via MediaWiki API", "license": LICENSE, "built_at": fetched,
        "counts": {"list_pages": len(pages), "list_pages_parsed": sum(1 for p in pages if isinstance(p.get("parsed"), int)),
                   "list_entries": len(st["entries"]), "articles_read": len(st["articles"]), "records": len(recs),
                   "records_with_website": sum(1 for r in recs if r["website"]), "distinct_websites": len(by_dom),
                   "distinct_websites_by_state": summary},
        "list_pages": pages, "records": recs}, ensure_ascii=False, indent=1) + "\n")
    print("build:", json.dumps({"records": len(recs), "with_website": sum(1 for r in recs if r["website"]), "distinct": len(by_dom),
                                "by_state_top10": list(summary.items())[:10]}))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    dio, st = Dio(), load_state()
    if cmd in ("discover", "all"):
        discover(st, dio)
    if cmd == "discover2":                       # extra pass over each diocese's own category (~400 API calls)
        discover(st, dio, dio_cats=True)
    if cmd in ("lists", "all"):
        run_lists(st, dio)
    if cmd in ("articles", "all"):
        run_articles(st, dio)
    if cmd in ("build", "all"):
        build(st, dio)


if __name__ == "__main__":
    main()
