"""Crawl parish websites (from data/real/parishes_directory.json) and extract public signals.

For each parish: fetch the home page plus up to 7 likely subpages found in its links
(mass times, staff, ministries, bulletin, giving, contact). Raw HTML goes to
data/raw/parish_sites/<id>/ (gitignored). Extracted, per-field-sourced output goes to --out.

Usage:
  python ingest/public/parish_sites.py --ids st-ambrose-berkeley,st-joseph-the-worker-berkeley --out data/real/parish_sites/test.json
  python ingest/public/parish_sites.py --shard 1/4 --out data/real/parish_sites/shard_1_of_4.json
  python ingest/public/parish_sites.py --shard 1/1 --delay 2.5 --out data/real/parish_sites/full.json
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, REAL, SESSION, load_json, now_iso, save_json  # noqa: E402
from privacy import RULES, screen  # noqa: E402

DIRECTORY = REAL / "parishes_directory.json"
RAW_DIR = RAW / "parish_sites"
MAX_PAGES = 8
DELAY = 1.0
TIMEOUT = 15

# page category -> regex on link text / href
CATEGORIES = {
    "mass_times": r"mass(?:[\s_-]*(?:times?|schedule))?|liturgy|worship|horario|misas?|sacraments?|confession",
    "staff": r"staff|clergy|our[\s_-]*priests|pastor|leadership|contact[\s_-]*us|about[\s_-]*us|personnel",
    "ministries": r"ministr|groups|organizations|get[\s_-]*involved",
    "bulletin": r"bulletin|boletin|newsletter",
    "giving": r"giv(e|ing)|donat|tith|offertory|stewardship|support[\s_-]*us",
    "contact": r"contact|office[\s_-]*hours|directions|location",
}
TECH = {
    "flocknote": r"flocknote\.com",
    "parishsoft": r"parishsoft\.com|parishsoft",
    "ministryplatform": r"ministryplatform",
    "realm_acs": r"onrealm\.org|acstechnologies|acsevents",
    "pushpay": r"pushpay\.com",
    "osv_online_giving": r"osvhub\.com|onlinegiving\.org|osv\.com|osvonlinegiving",
    "weshare": r"weshareonline\.org|\bweshare\b",
    "vanco": r"vanco(?:payments)?\.com|eservicepayments\.com",
    "tithely": r"tithe\.ly|tithely",
    "givecentral": r"givecentral\.org",
    "ecatholic": r"ecatholic\.com|ecatholiccdn",
    "lpi_parishesonline": r"parishesonline\.com|4lpi\.com|lpi\.com|lpicommunities",
    "discover_mass": r"discovermass\.com",
    "jspaluch": r"jspaluch\.com|church-bulletins|churchbulletins",
    "diocesan_publications": r"discovermass|diocesan\.com|dpi-bulletins",
    "wix": r"wix\.com|wixstatic\.com|_wix",
    "squarespace": r"squarespace\.com|squarespace-cdn|static1\.squarespace",
    "wordpress": r"wp-content|wp-includes",
    "weebly": r"weebly\.com|editmysite",
    "youtube_livestream": r"youtube\.com/(?:@|channel|c/|user/|live|embed)|youtu\.be",
    "facebook": r"facebook\.com/",
    "facebook_live": r"facebook\.com/[^\"'\s]*/(?:live|videos)",
    "givelify": r"givelify\.com",
    "faithdirect": r"faithdirect\.net",
    "paypal": r"paypal\.com/(?:donate|cgi-bin)",
    "formed": r"formed\.org",
    "myparish_app": r"myparishapp\.com|myparish\.app",
}
GIVING_PROVIDERS = ["osv_online_giving", "weshare", "vanco", "pushpay", "tithely", "givecentral",
                    "faithdirect", "givelify", "parishsoft", "realm_acs", "ministryplatform", "paypal"]
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Weekday", "Daily", "Vigil", "Domingo", "Sábado", "Sabado", "Lunes", "Martes", "Miércoles",
        "Miercoles", "Jueves", "Viernes"]
DAY_RE = re.compile(r"\b(" + "|".join(DAYS) + r"|Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:s)?\b\.?", re.I)
DATED_RE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                      r"\s+\d{1,2},\s+20\d{2}\b|^video of\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\b|\bnoon\b", re.I)
LANGS = ["English", "Spanish", "Español", "Vietnamese", "Tiếng Việt", "Tagalog", "Filipino", "Portuguese",
         "Chinese", "Cantonese", "Mandarin", "Korean", "Polish", "Latin", "Italian", "Tongan", "Igbo",
         "French", "Eritrean", "Ge'ez", "Croatian", "Hungarian", "Indonesian", "Japanese", "Malayalam",
         "Samoan", "Fijian", "ASL", "Haitian", "Burmese", "Arabic", "German", "Lithuanian",
         "Inglés", "Ingles", "Cantonés", "Vietnamita"]
TITLE_RE = re.compile(r"\b(Pastor|Parochial Vicar|Administrator|Deacon|Pastoral Associate|Business Manager|"
                      r"Office Manager|Secretary|Receptionist|Director|Coordinator|Principal|Music|Bookkeeper|"
                      r"Facilities|Youth Minister|DRE|Rector|In Residence|Associate Pastor|Vicar|Steward|Manager|Consultant|"
                      r"Minister|Liaison|Assistant|Accountant|Custodian|Maintenance|Sacristan|Communications|"
                      r"Administrative|Bookkeeping|Receptionist|Chaplain)s?\b", re.I)
NAME_RE = re.compile(r"^(?:(?:Rev\.?|Fr\.?|Father|Msgr\.?|Monsignor|Deacon|Dcn\.?|Sr\.?|Sister|Br\.?|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Very Rev\.?)\s+)?"
                     r"[A-Z][a-zA-Z'\-\.]+(?:\s+(?:[A-Z][a-zA-Z'\-\.]*|" r'"[A-Z][a-z]+"' r"|“[A-Z][a-z]+”|\([A-Z][a-z]+\))){1,4}$")
# capitalized phrases that look like names but are places/orgs/page sections
NOT_NAME_RE = re.compile(r"\b(School|Church|Parish|Ministry|Ministries|Center|Centre|Council|Office|Questions|Welcome|"
                         r"Mission|Vision|Society|Program|Staff|Contact|Hours|Mass|Catholic|Diocese|Friary|Profile|View|"
                         r"Page|Top|Email|Phone|Directory|Button|Retired|Click|Councils|Finance)\b|"
                         r"^St\.?\s|^Saint\s|^Our Lady\b", re.I)


def fetch(url: str):
    try:
        r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        return r
    except Exception as e:  # noqa: BLE001 - skip any failure
        return e


def safe_name(url: str, i: int) -> str:
    p = urlparse(url).path.strip("/").replace("/", "_") or "home"
    return f"{i:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '', p)[:60]}.html"


def same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.lower().removeprefix("www."), urlparse(b).netloc.lower().removeprefix("www.")
    return ha == hb


def discover(soup: BeautifulSoup, base: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {k: [] for k in CATEGORIES}
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].strip())
        if not href.startswith("http") or not same_site(href, base):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|docx?|zip|mp3|mp4)(\?|$)", href, re.I):
            continue
        hay = (a.get_text(" ", strip=True) + " " + urlparse(href).path).lower()
        for cat, rx in CATEGORIES.items():
            if re.search(rx, hay, re.I) and href.split("#")[0] not in found[cat]:
                found[cat].append(href.split("#")[0])
    return found


def _is_site_header(h) -> bool:
    """True for the site-wide page header (logo, menu, social icons), False for a <header> that heads a section of
    the page content. eCatholic and similar CMSes put the "Mass Times" / "Confession Times" / "Adoration Times"
    headings of the main content inside <header> elements, so dropping every <header> loses or mislabels schedules.
    The site-wide one contains the <nav> (or role=banner/navigation), is a direct child of <body>, or is mostly links;
    a section heading has no <nav> and (almost) no links."""
    if (h.get("role") or "").lower() in ("banner", "navigation") or h.find("nav") or h.find(attrs={"role": "navigation"}):
        return True
    if h.parent is not None and h.parent.name in ("body", "html", "[document]"):
        return True
    return len(h.find_all("a")) >= 4


def strip_chrome(s: BeautifulSoup, extra: tuple[str, ...] = ()) -> BeautifulSoup:
    """Remove scripts, styles, navigation, footers and the site-wide page header from a (copied) soup, in place.
    Section headings inside <header> elements of the main content are kept (see _is_site_header)."""
    site_headers = [h for h in s.find_all("header") if _is_site_header(h)]
    for t in s(["script", "style", "noscript", "svg", "nav", "footer", *extra]):
        t.decompose()
    for h in site_headers:
        if not getattr(h, "decomposed", False):
            h.decompose()
    return s


def visible_lines(soup: BeautifulSoup) -> list[str]:
    s = strip_chrome(BeautifulSoup(str(soup), "html.parser"), ("form",))
    for br in s.find_all("br"):
        br.replace_with("\n")
    txt = s.get_text("\n")
    return [re.sub(r"\s+", " ", ln).strip() for ln in txt.split("\n") if ln.strip()]


def mass_block(lines: list[str]) -> str | None:
    """Return the densest window of lines around 'Mass' with day+time mentions."""
    best, best_score = None, 0
    for i, ln in enumerate(lines):
        if not re.search(r"\bmass|misa|liturg|schedule|horario\b", ln, re.I):
            continue
        win = lines[i:i + 25]
        score = sum(bool(TIME_RE.search(w)) for w in win)
        if score > best_score:
            best_score, best = score, win
    if not best or best_score < 2:
        return None
    # trim trailing lines after last time mention
    last = max(j for j, w in enumerate(best) if TIME_RE.search(w))
    return "\n".join(best[:last + 1])


def parse_mass(block: str | None) -> list[dict]:
    if not block:
        return []
    out, day, kind = [], None, "mass"
    seen = set()
    for ln in block.split("\n"):
        if DATED_RE.search(ln):
            continue
        if re.search(r"reconciliation|confession|confesiones|adoration|adoraci|rosary|office hours|novena", ln, re.I):
            kind = "other"
        elif re.search(r"\bmass(es)?\b|\bmisas?\b|\bvigil\b", ln, re.I):
            kind = "mass"
        dm = DAY_RE.search(ln)
        if dm:
            day = dm.group(1).title()
        lang = next((L for L in LANGS if re.search(r"\b" + re.escape(L) + r"\b", ln, re.I)), None)
        for tm in TIME_RE.finditer(ln):
            if tm.group(0).lower() == "noon":
                t = "12:00"
            else:
                h, m, ap = int(tm.group(1)), tm.group(2) or "00", tm.group(3).lower()
                if h > 12:
                    continue
                h = (h % 12) + (12 if ap == "p" else 0)
                t = f"{h:02d}:{m}"
            key = (day, t, lang, kind)
            if key in seen:
                continue
            seen.add(key)
            out.append({"day": day, "time": t, "language": lang, "kind": kind, "line": ln[:200]})
    return out


ORDER_SUFFIX_RE = re.compile(r",?\s+(?:O\.?F\.?M\.?(?:\s*Conv\.?|\s*Cap\.?)?|S\.?J\.?|O\.?P\.?|C\.?S\.?C\.?|S\.?D\.?B\.?|"
                             r"C\.?M\.?F\.?|M\.?S\.?|O\.?S\.?A\.?|S\.?V\.?D\.?|C\.?Ss\.?R\.?|Ph\.?D\.?|D\.?Min\.?|OFS)\.?\s*$")
SINGLE_WORD_RE = re.compile(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ'\-]+$")


def _staff_lines(lines: list[str]) -> list[str]:
    """Repair names split across DOM nodes (drop-cap 'R' + 'ev. X', or 'First' / 'Last' cards)."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if len(ln) == 1 and ln.isupper() and re.match(r"^[a-z]", nxt):
            out.append(ln + nxt); i += 2; continue
        if (SINGLE_WORD_RE.match(ln) and SINGLE_WORD_RE.match(nxt) and not TITLE_RE.search(ln + " " + nxt)
                and i + 2 < len(lines) and TITLE_RE.search(lines[i + 2])):
            out.append(ln + " " + nxt); i += 2; continue
        out.append(ln); i += 1
    return out


def parse_staff(lines: list[str]) -> list[dict]:
    staff, seen = [], set()
    lines = [ORDER_SUFFIX_RE.sub("", ln) for ln in _staff_lines(lines)]
    for i, ln in enumerate(lines):
        if len(ln) > 120:
            continue
        # "Name, Title" / "Name - Title" / "Title: Name"
        m = re.match(r"^(.{3,60}?)\s*[,\-–|:]\s*(.{3,60})$", ln)
        cand = None
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            if TITLE_RE.search(b) and NAME_RE.match(a):
                cand = (a, b)
            elif TITLE_RE.search(a) and NAME_RE.match(b):
                cand = (b, a)
        elif (NAME_RE.match(ln) and (not TITLE_RE.search(ln) or re.match(r"(?:Deacon|Dcn)\.?\s+[A-Z]", ln))
              and i + 1 < len(lines) and TITLE_RE.search(lines[i + 1])
              and len(lines[i + 1]) < 70):
            cand = (ln, lines[i + 1])
        elif (i + 1 < len(lines) and len(ln) < 40 and TITLE_RE.search(ln) and NAME_RE.match(lines[i + 1])
              and not TITLE_RE.search(lines[i + 1])
              and (re.match(r"(?:Rev|Fr|Father|Msgr|Deacon|Dcn|Sr|Sister|Br)\b", lines[i + 1])
                   or (i + 2 < len(lines) and re.search(r"@|\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}", lines[i + 2])))):
            # "Pastor" / "Fr. Luke Vu"  or  "Parish Secretary" / "Jay Aquitania" / "parish@..." (title above name)
            cand = (lines[i + 1], ln)
            # plural heading ("Parochial Vicars", "Deacons"): collect further clergy names below, skipping emails
            if re.search(r"s$", ln.strip()):
                for nm in lines[i + 2:i + 8]:
                    if "@" in nm or re.match(r"^[\d(]", nm):
                        continue
                    if re.match(r"(?:Rev|Fr|Father|Msgr|Deacon|Dcn|Sr|Sister|Br)\b", nm) and NAME_RE.match(nm):
                        if nm.lower() not in seen and nm != cand[0]:
                            seen.add(nm.lower())
                            staff.append({"name": nm, "title": ln})
                    else:
                        break
        if cand:
            name, title = cand[0].strip(), cand[1].strip().lstrip(":–-| ").strip()
            if (name.lower() in seen or TITLE_RE.fullmatch(name) or NOT_NAME_RE.search(name)
                    or TITLE_RE.search(name) and not re.match(r"(?:Rev|Fr|Father|Msgr|Deacon|Dcn|Sr|Sister|Br|Dr)\b", name)):
                continue
            seen.add(name.lower())
            staff.append({"name": name, "title": title})
    return staff[:60]


MIN_JUNK_RE = re.compile(r"^(read more|learn more|more|home|return to top|back to top|click here|contact us|"
                         r"ministries|ministry|our ministries|serve|get involved|parish ministries)\.*$|@|https?://|\.\.\.$", re.I)
# prose fragments that happen to contain a ministry keyword
MIN_SENTENCE_RE = re.compile(r"^(there|we|our parish|as|become|sign up|please|join|help|if|this|these|you)\b|"
                             r"\b(is|are|we|our|provide|divided)\b.*\b(the|a|to|of|into)\b.*\s\w+\s\w+", re.I)


def parse_ministries(soup: BeautifulSoup, lines: list[str], is_ministry_page: bool = False) -> list[str]:
    items = []
    s = strip_chrome(BeautifulSoup(str(soup), "html.parser"))
    cands = [(el, el.get_text(" ", strip=True)) for el in s.find_all("li") if not el.find("ul")]
    if is_ministry_page:
        # many parish CMSes (Solutio, eCatholic, WordPress blocks) list ministries as headings/cards
        cands += [(el, el.get_text(" ", strip=True)) for el in s.find_all(["h2", "h3", "h4", "h5", "strong"])]
    for li, t in cands:
        t = re.sub(r"\s+", " ", t).strip(" :–-•")
        t = re.sub(r"\s*Item Link(?: List Item \d+)?$", "", t).strip()
        if (MIN_JUNK_RE.search(t) or MIN_SENTENCE_RE.search(t) or re.search(r"\s[a-z]{1,2}$", t)
                or re.search(r"\b(Director|Coordinator|Manager|Secretary)\b", t) and NAME_RE.match(t.split(" Director")[0])):
            continue
        if 3 < len(t) < 70 and re.search(r"ministr|society|council|knights|guild|choir|youth|"
                                                                r"rcia|ocia|bible|prayer|legion|st\.? vincent|svdp|"
                                                                r"altar|lector|usher|food|catechis|confirmation|"
                                                                r"religious ed|cursillo|charismatic|men'?s|women'?s|"
                                                                r"seniors|young adult|grupo|club|outreach|liturg|sacristan|"
                                                                r"usher|hospitality|music|ensemble|bereavement|"
                                                                r"grief|pastoral care|homebound|sick|stewardship|"
                                                                r"gathering|reaching out|worship|social justice|"
                                                                r"faith formation|catechist|columbiettes|federation|"
                                                                r"cyo|scouts|cursillo|fellowship|mercy|altar", t, re.I):
            if t.lower() not in {i.lower() for i in items}:
                items.append(t)
    return items[:80]


def bulletin_link(soups: list[tuple[str, BeautifulSoup]]) -> dict | None:
    best = None
    for url, s in soups:
        for a in s.find_all("a", href=True):
            href = urljoin(url, a["href"].strip())
            txt = a.get_text(" ", strip=True)
            hay = (txt + " " + href).lower()
            if re.search(r"bulletin|boletin", hay):
                is_pdf = bool(re.search(r"\.pdf(\?|$)", href, re.I)) or "parishesonline" in href or "jspaluch" in href
                cand = {"url": href, "is_pdf": is_pdf, "text": txt[:100], "source_url": url}
                if is_pdf:
                    return cand
                best = best or cand
    return best


def tech_signals(pages: list[tuple[str, str]]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for url, html in pages:
        for k, rx in TECH.items():
            if re.search(rx, html, re.I):
                hits.setdefault(k, [])
                if url not in hits[k]:
                    hits[k].append(url)
    return hits


def crawl(rec: dict) -> dict:
    pid, site = rec["id"], rec.get("website")
    out = {"id": pid, "name": rec["name"], "website": site, "crawled_at": now_iso(),
           "pages": [], "errors": []}
    if not site:
        out["errors"].append("no website in directory")
        return out
    rawdir = RAW_DIR / pid
    rawdir.mkdir(parents=True, exist_ok=True)
    queue, done, htmls, soups = [("home", site)], set(), [], []
    while queue and len(done) < MAX_PAGES:
        cat, url = queue.pop(0)
        if url in done:
            continue
        done.add(url)
        r = fetch(url)
        time.sleep(DELAY)
        if isinstance(r, Exception) or r is None:
            out["errors"].append({"url": url, "error": type(r).__name__ if r else "none"})
            continue
        ctype = r.headers.get("content-type", "")
        if not r.ok or "html" not in ctype:
            out["errors"].append({"url": url, "status": r.status_code, "content_type": ctype})
            continue
        final = r.url
        fn = safe_name(final, len(htmls))
        (rawdir / fn).write_text(r.text, encoding="utf-8", errors="replace")
        soup = BeautifulSoup(r.text, "html.parser")
        htmls.append((final, r.text))
        soups.append((final, soup))
        out["pages"].append({"category": cat, "url": final, "requested_url": url, "status": r.status_code, "raw_file": str(rawdir / fn)})
        if cat == "home":
            out["final_home_url"] = final
            found = discover(soup, final)
            out["discovered_links"] = found
            # one best link per category, then extras round-robin
            pref = {"staff": r"staff|clergy|priests|personnel|equipo", "ministries": r"/ministr[a-z]*/?$|ministerios",
                    "mass_times": r"mass[\s_-]*(times?|schedule)|mass-and|mass-confession|horario|worship/?$"}
            for c, rx in pref.items():
                found[c].sort(key=lambda u, rx=rx: 0 if re.search(rx, urlparse(u).path, re.I) else 1)
            picks = []
            for c in CATEGORIES:
                if found[c]:
                    picks.append((c, found[c][0]))
            for c in CATEGORIES:
                for u in found[c][1:2]:
                    picks.append((c, u))
            for c, u in picks:
                if u not in done and u != final and all(u != q[1] for q in queue):
                    queue.append((c, u))
    if not htmls:
        return out

    # --- mass schedule: prefer mass_times page, then home, then any
    order = sorted(range(len(soups)), key=lambda i: {"mass_times": 0, "home": 1}.get(out["pages"][i]["category"], 2))
    mass = None
    for i in order:
        blk = mass_block(visible_lines(soups[i][1]))
        if blk:
            mass = {"raw": blk, "parsed": parse_mass(blk), "source_url": soups[i][0]}
            break
    out["mass_schedule"] = mass

    # --- staff: staff pages first, else home
    staff = None
    for i in sorted(range(len(soups)), key=lambda i: {"staff": 0, "contact": 1, "home": 2}.get(out["pages"][i]["category"], 3)):
        s = parse_staff(visible_lines(soups[i][1]))
        if s:
            staff = {"items": s, "source_url": soups[i][0]}
            break
    out["staff"] = staff

    # --- ministries
    # union across all ministries-category pages (each item keeps its source page); else best other page
    mins = None
    mpages = [i for i in range(len(soups)) if out["pages"][i]["category"] == "ministries"]
    merged, srcs = [], {}
    for i in mpages:
        for it in parse_ministries(soups[i][1], [], is_ministry_page=True):
            if it.lower() not in srcs:
                srcs[it.lower()] = soups[i][0]
                merged.append(it)
    if len(merged) >= 3:
        mins = {"items": merged[:100], "source_url": soups[mpages[0]][0],
                "item_sources": {it: srcs[it.lower()] for it in merged[:100]}}
    else:
        for i in range(len(soups)):
            if i in mpages:
                continue
            m = parse_ministries(soups[i][1], [])
            if len(m) >= 3:
                mins = {"items": m, "source_url": soups[i][0]}
                break
    out["ministries"] = mins

    out["bulletin"] = bulletin_link(soups)
    tech = tech_signals(htmls)
    out["tech_signals"] = tech
    gp = [p for p in GIVING_PROVIDERS if p in tech]
    # rank by presence on the giving page
    giving_urls = {p["url"] for p in out["pages"] if p["category"] == "giving"}
    gp.sort(key=lambda p: 0 if set(tech[p]) & giving_urls else 1)
    out["giving_provider"] = {"provider": gp[0], "all": gp, "source_url": tech[gp[0]][0]} if gp else None
    cms = [c for c in ("wordpress", "wix", "squarespace", "weebly", "ecatholic", "lpi_parishesonline") if c in tech]
    out["cms_guess"] = {"cms": cms[0], "source_url": tech[cms[0]][0]} if cms else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ids", help="comma-separated directory ids")
    g.add_argument("--shard", help="N/M, 1-based (e.g. 2/5)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=DELAY, help="seconds between requests (default %(default)s)")
    a = ap.parse_args()
    globals()["DELAY"] = a.delay
    recs = load_json(DIRECTORY, {}).get("records", [])
    if a.ids:
        want = [x.strip() for x in a.ids.split(",") if x.strip()]
        by = {r["id"]: r for r in recs}
        missing = [w for w in want if w not in by]
        if missing:
            sys.exit(f"unknown ids: {missing}")
        todo = [by[w] for w in want]
    else:
        n, m = (int(x) for x in a.shard.split("/"))
        withsite = sorted([r for r in recs if r.get("website")], key=lambda r: r["id"])
        todo = [r for i, r in enumerate(withsite) if i % m == n - 1]
    results = []
    for i, r in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {r['id']} {r.get('website')}", flush=True)
        try:
            res = crawl(r)
        except Exception as e:  # noqa: BLE001 - never kill the shard
            res = {"id": r["id"], "website": r.get("website"), "errors": [f"crawler crash: {e!r}"], "crawled_at": now_iso()}
        results.append(res)
        print(f"    pages={len(res.get('pages', []))} mass={len((res.get('mass_schedule') or {}).get('parsed', []))} "
              f"staff={len((res.get('staff') or {}).get('items', []))} giving={(res.get('giving_provider') or {}).get('provider')} "
              f"tech={sorted(res.get('tech_signals', {}))}", flush=True)
        out = {"generated_at": now_iso(), "count": len(results), "records": results}
        screen(out, RULES["parish_sites"])  # private names out of staff lists, schedule lines and page text
        save_json(Path(a.out), out)
    print(f"wrote {len(results)} -> {a.out}")


if __name__ == "__main__":
    main()
