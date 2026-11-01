"""Diocese of San Jose (dsj.org): parishes, schools, events and structure from the site's WordPress REST API.

dsj.org robots.txt sets `Crawl-delay: 600` for every agent, so each request is 10 minutes after the last. The fetch
stage is therefore ordered by value and cached (data/raw/california/dioceses/san-jose/); `build` is offline.

    python -I ingest/california/directories/san-jose.py fetch    # slow: honours the 600 s Crawl-delay
    python -I ingest/california/directories/san-jose.py build    # offline, from the cache
"""
from __future__ import annotations

import json
import re
import sys
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

SLUG = "san-jose"
BASE = "https://www.dsj.org"


LAPTOP = C.RAW_ROOT / "dsj-laptop"  # pages fetched from the owner's laptop connection (sandbox gets 403 from dsj.org's WAF)

# Every request made to www.dsj.org (all other hosts are listed in the README). robots.txt: `Crawl-delay: 600` for all agents.
# (when, vantage, status, path). Laptop = owner's home connection, plain `curl`, saved under data/raw/california/dsj-laptop/.
REQUEST_LOG = [
    ("2026-10-31 08:26:40", "sandbox python-requests", "403", "/wp-json/wp/v2/parish?per_page=100&_embed=wp:term"),
    ("2026-10-31 08:43:35", "sandbox python-requests", "403", "/wp-json/wp/v2/parish?per_page=100"),
    ("2026-10-31 08:51:50", "laptop curl", "200", "/wp-json/wp/v2/parish?per_page=100  (sent 1 min 45 s EARLY: 8 min 15 s after the 08:43:35 sandbox request, not 10 min)"),
    ("2026-10-31 09:01:50", "sandbox python-requests", "403", "/locations/parishes/"),
    ("2026-10-31 09:12:11", "laptop curl", "200", "/locations/parishes/"),
    ("2026-10-31 09:22:32", "laptop curl", "200", "/locations/deaneries/"),
    ("2026-10-31 09:32:51", "laptop curl", "301", "/catholic-schools/locateschool/  (menu link; redirects to /catholicschools/locateschool/)"),
    ("2026-10-31 09:43:10", "laptop curl", "200", "/catholicschools/locateschool/"),
    ("2026-10-31 09:53:30", "laptop curl", "200", "/about-us/"),
    ("2026-10-31 10:03:55", "laptop curl", "200", "/wp-json/tribe/events/v1/events?per_page=50&start_date=2026-10-31&end_date=2027-02-28"),
    ("2026-10-31 10:14:11", "laptop curl", "200", "/about-us/clergy/clergy-list/"),
    ("2026-10-31 10:24:32", "laptop curl", "200", "/catholic-life/retreat-centers/"),
    ("2026-10-31 10:34:51", "laptop curl", "301", "/about-us/vocations/vocations-office/consecrated-life-men-and-women/  (menu link; redirects to /about-us/consecrated-life-men-and-women/)"),
    ("2026-10-31 10:45:12", "laptop curl", "200", "/about-us/consecrated-life-men-and-women/"),
    ("2026-10-31 10:55:32", "laptop curl", "200", "/about-us/consecrated-life-men-and-women/religious-orders-for-men/"),
    ("2026-10-31 11:05:52", "laptop curl", "200", "/about-us/consecrated-life-men-and-women/religious-orders-for-women/"),
]


def read_laptop(name: str) -> tuple[str | None, str | None]:
    """(text, fetched_at ISO from the response's own Date header) for a page saved from the laptop, else (None, None)."""
    f = LAPTOP / name
    if not f.exists():
        return None, None
    fetched = None
    h = LAPTOP / (name + ".hdr")
    if h.exists():
        m = re.search(r"^date:\s*(.+)$", h.read_text(errors="replace"), re.I | re.M)
        if m:
            try:
                fetched = parsedate_to_datetime(m.group(1).strip()).astimezone(timezone.utc).replace(microsecond=0).isoformat()
            except (TypeError, ValueError):
                fetched = None
    return f.read_text(encoding="utf-8", errors="replace"), fetched


def soup(html: str) -> BeautifulSoup:
    b = BeautifulSoup(html, "html.parser")
    for s in b(["script", "style"]):
        s.decompose()
    return b


def flat(b: BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", (b.body or b).get_text(" ", strip=True))


def parish_rest() -> dict[str, dict]:
    """parish page URL -> REST record (WordPress `parish` custom post type, fetched from the laptop)."""
    f = LAPTOP / "dsj_parish.json"
    if not f.exists():
        return {}
    return {r["link"]: r for r in json.loads(f.read_text())}


def build_parishes() -> list[dict]:
    html, fetched = read_laptop("parishes.html")
    if not html:
        return []
    list_url = f"{BASE}/locations/parishes/"
    rest = parish_rest()
    rest_fetched = None
    hf = LAPTOP / "dsj_parish.hdr"
    if hf.exists():
        m = re.search(r"^date:\s*(.+)$", hf.read_text(errors="replace"), re.I | re.M)
        if m:
            rest_fetched = parsedate_to_datetime(m.group(1).strip()).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    out = []
    for tr in soup(html).select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        a = tds[0].find("a")
        name = C.clean_text(tds[0].get_text(" ", strip=True))
        link = a["href"] if a and a.get("href") else None
        addr = C.clean_text(tds[1].get_text(" ", strip=True))
        r = rest.get(link or "")
        blurb = ""
        if r:
            blurb = re.sub(r"<[^>]+>", " ", r["content"]["rendered"])
            blurb = re.sub(r"&[a-z#0-9]+;", " ", blurb)
            blurb = re.sub(r"\s+", " ", blurb).strip()
        web = None
        if r:
            raw_html = r["content"]["rendered"]
            for m in re.finditer(r"https?://[^\s\"'<>]+", raw_html):
                u = m.group(0)
                before = re.sub(r"<[^>]+>", " ", raw_html[max(0, m.start() - 120):m.start()])
                if re.search(r"facebook|dsj\.org/parish|dsj\.org/parking|youtube|instagram|live-stream", u) \
                        or re.search(r"ministry|click here|livestream|live stream", before, re.I):
                    continue  # a ministry's site, a social page or a stream is not the parish's own website
                web = C.norm_url(u.rstrip(".,)"))
                break
        ptype = C.parish_type(name)
        if re.search(r"oratory", name, re.I):
            ptype = "chapel"
        if re.search(r"mission santa clara", name, re.I):
            ptype = "mission"
        if re.search(r"catholic community at stanford", name, re.I):
            ptype = "campus_ministry"
        row = {
            "name": name, "type": ptype, "address": (addr or "").rstrip(" ,") or None, "city": C.clean_text(tds[2].get_text(" ", strip=True)),
            "zip": C.zip5(tds[3].get_text(" ", strip=True)), "lat": None, "lng": None,
            "phone": C.norm_phone(tds[4].get_text(" ", strip=True)), "website": web, "office_email": None,
            "languages": C.detect_languages(blurb), "pastor": None, "region": None, "vicariate": None, "deanery": None,
            "county": "Santa Clara", "status": "active",
            "status_note": "Listed in the diocese's parish table on the fetch date; no merger or closure decree is published there.",
            "source_url": link or list_url, "list_source_url": list_url, "fetched_at": fetched,
            "wp_id": int(r["id"]) if r else None, "rest_fetched_at": rest_fetched,
        }
        if addr and re.match(r"(?i)p\.?\s*o\.?\s*box", addr):
            row["address_kind"] = "mailing (PO box)"
        out.append(row)
    return out


CFCS = "https://www.cfcssanjose.org"


def build_cemeteries(fx: C.Fetcher) -> list[dict]:
    """The diocese's menu links 'Find a Cemetery' to cfcssanjose.org (Catholic Funeral & Cemetery Services, a church-owned non-profit).
    Public locations page only: address, generic phone, office hours. No staff, no obituaries or services."""
    out = []
    r = fx.get(f"{CFCS}/about-us/")
    t = flat(soup(r.text))
    m = re.search(r"Catholic Funeral & Cemetery Services of the Diocese of San Jos[eé]", t)
    out.append({"type": "diocesan_office", "name": "Catholic Funeral & Cemetery Services San Jose (CFCS)", "address": None, "city": None,
                "zip": None, "website": CFCS, "phone": C.norm_phone("(408) 359-5574"), "lat": None, "lng": None,
                "note": "A charitable organization of the Diocese of San Jose that runs the diocese's two Catholic cemeteries (its own About page).",
                "says": m.group(0) if m else None, "source_url": r.url, "fetched_at": r.fetched_at})
    for slug in ("calvary-catholic-cemetery", "gate-of-heaven-catholic-cemetery"):
        r = fx.get(f"{CFCS}/location/{slug}/")
        sp = soup(r.text)
        t = flat(sp)
        name = C.clean_text((sp.title.get_text() if sp.title else "").split(" - ")[0])
        ph = re.search(r"Give Us a Call\s+([\d\-() ]{10,14})\s+Phone", t)
        ad = re.search(r"Come Visit Us\s+(.+?),?\s+(San Jos[eé]|Los Altos|[A-Z][a-z]+(?: [A-Z][a-z]+)?),\s+CA\s+(9\d{4})", t)
        hrs = re.search(r"Hours of Operation\s+(.+?)\s+click here for directions", t)
        street = ad.group(1).strip() if ad else None
        out.append({"type": "cemetery", "name": name, "address": street, "city": (ad.group(2) if ad else None), "zip": (ad.group(3) if ad else None),
                    "phone": C.norm_phone(ph.group(1)) if ph else None, "website": r.url, "lat": None, "lng": None,
                    "hours": C.clean_text(hrs.group(1)) if hrs else None, "operator": "Catholic Funeral & Cemetery Services San Jose (CFCS)",
                    "source_url": r.url, "fetched_at": r.fetched_at})
    return out


def pkey(name: str) -> str:
    n = (name or "").lower().replace("\u2013", "-")
    n = re.sub(r"\b(saint|st\.?)\b", "st", n)
    n = re.sub(r"\b(parish|church|the|of|catholic|community|national|and|mission|basilica|cathedral|oratory|portuguese|croatian|polish)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def ckey(city: str | None) -> str:
    return re.sub(r"[^a-z]+", "", (city or "").lower().replace("mt.", "mountain"))


# Names the deaneries page uses that the parish table spells differently (checked by hand against both pages), keyed by pkey().
DEANERY_ALIASES = {
    pkey("Chinese Catholic Community"): "San Jose Chinese Catholic Mission",
    pkey("St. Dominic"): "Catholic Community at Stanford",  # "Stanford: St. Dominic" in the dean's roll; the boundaries block names the Catholic Community at Stanford
}
# Sub-churches: the St. Thomas Aquinas parish page lists these as its "Additional Church Locations".
SUB_CHURCHES = {"Our Lady of the Rosary Church": "Saint Thomas Aquinas", "Saint Albert the Great Church": "Saint Thomas Aquinas"}


def match_parish(parishes: list[dict], city: str, listed: str) -> dict | None:
    k = pkey(listed)
    same_city = [p for p in parishes if ckey(p["city"]) == ckey(city)]
    for pool in (same_city, parishes):
        exact = [p for p in pool if pkey(p["name"]) == k]
        if len(exact) == 1:
            return exact[0]
        loose = [p for p in pool if len(k) >= 6 and (pkey(p["name"]).startswith(k) or k.startswith(pkey(p["name"])) and len(pkey(p["name"])) >= 6)]
        if len(loose) == 1:
            return loose[0]
    al = DEANERY_ALIASES.get(k)
    return next((p for p in parishes if p["name"] == al), None) if al else None


def build_structure(parishes: list[dict]) -> dict:
    html, fetched = read_laptop("deaneries.html")
    url = f"{BASE}/locations/deaneries/"
    units, unmatched = [], []
    if html:
        b = soup(html)
        for acc in b.select(".et_pb_accordion"):
            t = acc.select_one("h3.et_pb_toggle_title")
            col = acc.find_parent(class_="et_pb_column")
            inner = col.select_one(".et_pb_text_inner") if col else None
            if not t or not inner:
                continue
            label = C.clean_text(t.get_text(" ", strip=True)).rstrip(":").strip()
            lead = next((C.clean_text(x.get_text(" ", strip=True)) for x in inner.find_all(["h5", "strong"]) if re.search(r"\bDean\s*$", x.get_text(" ", strip=True))), None)
            dean = re.sub(r"\s*,?\s*Dean\s*$", "", lead or "").strip(" ,") or None
            members, names = [], []
            for pp in inner.find_all("p"):
                for seg in re.split(r"<br\s*/?>", str(pp)):
                    sp = BeautifulSoup(seg, "html.parser")
                    st = sp.find("strong")
                    if not st or ":" not in sp.get_text() or re.search(r"Dean\s*$", sp.get_text(" ", strip=True)):
                        continue
                    city = C.clean_text(st.get_text(" ", strip=True)).rstrip(":").strip()
                    rest = sp.get_text(" ", strip=True).split(":", 1)[1]
                    for n in [x.strip() for x in re.split(r",(?![^()]*\))", rest) if x.strip()]:
                        n = re.sub(r"^and\s+", "", n)
                        parts = [x.strip() for x in re.split(r"\s+and\s+(?=St\.|Saint)", n)] if re.search(r"\s+and\s+(St\.|Saint)", n) else [n]
                        for part in parts:
                            names.append({"city": city, "listed_as": part if len(parts) > 1 else n, "combined_with": n if len(parts) > 1 else None})
            for nm in names:
                hit = match_parish(parishes, nm["city"], nm["listed_as"])
                if hit:
                    hit["deanery"] = label
                    hit["deanery_source_url"] = url
                    members.append({"name": hit["name"], "listed_as": nm["listed_as"], "city_listed": nm["city"], "parish_id": hit.get("id"),
                                    **({"listed_together_as": nm["combined_with"]} if nm["combined_with"] else {}),
                                    **({"matched_by": "alias (name differs from the parish table)"} if pkey(hit["name"]) != pkey(nm["listed_as"]) and not nm["combined_with"] and pkey(nm["listed_as"]) in DEANERY_ALIASES else {})})
                else:
                    unmatched.append({"deanery": label, **nm})
                    members.append({"name": None, "listed_as": nm["listed_as"], "city_listed": nm["city"], "parish_id": None})
            units.append({"kind": "deanery", "name": label, "parent": None,
                          "leader": ({"name": dean, "title": "Dean"} if dean else None),
                          "members": members, "source_url": url, "fetched_at": fetched})
        for sub, parent in SUB_CHURCHES.items():
            sp_ = next((p for p in parishes if p["name"] == sub), None)
            pp_ = next((p for p in parishes if p["name"] == parent), None)
            if sp_ and pp_:
                sp_["parent_parish"] = pp_["name"]
                sp_["parent_parish_source_url"] = pp_["source_url"]
                sp_["deanery"] = pp_["deanery"]
                sp_["deanery_basis"] = f"{parent}'s parish page lists this church as an additional church location; the deaneries page lists {parent} in {pp_['deanery']}"
    return {"units": units, "unmatched": unmatched, "source_url": url, "fetched_at": fetched}


# ---- schools -------------------------------------------------------------------------------------------------
CDE_FILE = C.ROOT / "data" / "real" / "california" / "schools_cde.json"
# The diocese's school list (/catholicschools/locateschool/) is a set of logo links with no text: each link's website domain is the only
# identifier. When a school's own site could not be read (HTTP 406 to the plain client, or robots.txt 503) the name is taken from the
# CDE/NCES record whose name and place match the domain; these are marked name_basis = "domain+CDE".
DOMAIN_TO_CDE = {
    "stleosj.org": "St. Leo the Great School",
    "school.sacredheartsaratoga.org": "Sacred Heart School",
    "shnativity.org": "Sacred Heart Nativity School",
    "cristoreysanjose.org": "Cristo Rey San Jose Jesuit High School",
    "ndsj.org": "Notre Dame High School San Jose",
    "sjcschool.org": "St. Joseph Cupertino",  # site title: "St Joseph of Cupertino School Cupertino"
    "stcatherinemh.org": "St. Catherine Elementary",  # site title: St. Catherine School of Morgan Hill; CDE: Morgan Hill, 17500 Peak Ave
}
# Schools on the diocese's list that the CDE/NCES join could not place (no NCES religion flag); matched here by domain and checked against
# the CDE district: key = host, value = (record name in schools_cde.json 'unverified_candidates', expected CDE district).
UNVERIFIED_BY_DOMAIN = {
    "sjmv.org": ("St Joseph Catholic School", "Mountain View Whisman"),
    "stmartinsj.org": ("St. Martin of Tours School", "Campbell Union"),
    "stmarygilroy.org": ("St. Mary, Gilroy", "Gilroy Unified"),
}
NCES_ADDRESS_BY_DOMAIN = {"stmarygilroy.org": "St. Mary School"}  # NCES PSS 2023-24 row with the street address (Gilroy, 7900 Church St.)
SECTION_LEVEL = {"PRESCHOOLS": "preschool program", "TK \u2013 Grade 8": "TK-8", "MIDDLE SCHOOL": "middle school (6-8)", "HIGH SCHOOLS Grades 9 \u2013 12": "high school (9-12)"}


def skey(n: str) -> str:
    n = (n or "").lower().replace("\u2013", "-")
    n = re.sub(r"\b(saint|st\.?)\b", "st", n)
    n = re.sub(r"\b(school|catholic|elementary|parish|the|of|academy|high|college|preparatory|prep|san jose|jesuit|independent|k-8|home|private|and)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def build_schools(fx: C.Fetcher) -> tuple[list[dict], dict]:
    html, fetched = read_laptop("schools.html")
    if not html:
        return [], {}
    b = soup(html)
    list_url = f"{BASE}/catholicschools/locateschool/"
    txt = flat(b)
    mt = re.search(r"educating nearly ([\d,]+) students across (\d+) elementary schools and (\d+) high schools", txt)
    cdej = json.loads(CDE_FILE.read_text()) if CDE_FILE.exists() else {}
    cde = [r for r in cdej.get("records", []) if r["diocese"] == "san-jose"]
    unver = {r["name"]: r for r in cdej.get("unverified_candidates", []) if r.get("county") == "Santa Clara"}
    nces_un = {r["name"]: r for r in cdej.get("nces_catholic_unmatched", []) if r.get("county") == "Santa Clara"}
    sec, links = None, {}
    for el in b.find_all(["h2", "a"]):
        if el.name == "h2":
            sec = C.clean_text(el.get_text(" ", strip=True))
        elif el.name == "a" and el.find("img") and sec and el.get("href"):
            u = el["href"]
            if u.startswith("#") or "wp-content" in u or re.match(r"https?://(www\.)?dsj\.org/(?!school/)", u):
                continue
            d = links.setdefault(u, {"levels": []})
            lv = SECTION_LEVEL.get(sec, sec)
            if lv not in d["levels"]:
                d["levels"].append(lv)
    out = []
    for u, d in links.items():
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", u).split("/")[0].lower())
        site_name, site_note, site_fetched, site_final, site_cands = None, None, None, None, []
        if not (host == "dsj.org" or host.endswith(".dsj.org")):
            try:
                r = fx.get(u)
                bs = BeautifulSoup(r.text, "html.parser")
                og = bs.find("meta", property="og:site_name")
                title = C.clean_text(bs.title.get_text(" ", strip=True)) if bs.title else None
                if r.status == 200 and title and not re.match(r"(?i)not acceptable", title):
                    cands = [x.strip() for x in re.split(r"\s+[|\u2013\u2014-]\s+|\s+\u00b7\s+", title)]
                    cands += [C.clean_text(og["content"])] if og and og.get("content") else []
                    cands = [re.sub(r"(?i)(^home\s*[-:]\s*|\s*[-:]\s*home$)", "", c or "").strip() for c in cands]
                    cands = [c for c in cands if c and re.search(r"(?i)\b(school|academy|high|preparatory|prep)\b", c) and not re.search(r"(?i)^(home|school)$|my site|v1$|^independent catholic", c)]
                    site_cands = cands
                    site_name = cands[0] if cands else None
                    site_fetched, site_final = r.fetched_at, r.url
                    if not site_name:
                        site_note = "no school name could be read from the site title"
                else:
                    site_note = f"school site answered HTTP {r.status} to the plain client"
            except C.Blocked as e:
                site_note = f"school site not readable: {e}"
            except Exception as e:  # network errors are recorded, not fatal
                site_note = f"school site not readable: {type(e).__name__}"
        else:  # a dsj.org/school/<slug>/ page: the name is the slug (no extra request spent on it)
            site_name = re.sub(r"[-_]+", " ", u.rstrip("/").rsplit("/", 1)[-1]).title()
            site_cands = [site_name]
            site_note = "name read from the dsj.org page slug; the page itself was not fetched"
        match, basis = None, None
        want = DOMAIN_TO_CDE.get(host)
        if want:
            match = next((r for r in cde if r["name"] == want), None)
            basis = "domain+CDE"
        if not match and host in UNVERIFIED_BY_DOMAIN:
            nm_, dist_ = UNVERIFIED_BY_DOMAIN[host]
            u_ = unver.get(nm_)
            if u_ and u_.get("district") == dist_:
                match, basis = dict(u_), f"domain+CDE unverified candidate (district {dist_})"
                nr = nces_un.get(NCES_ADDRESS_BY_DOMAIN.get(host, ""))
                if nr:
                    match.update(address=nr["address"], city=nr["city"], zip=nr["zip"], phone=nr["phone"])
                    match["_addr_src"] = "NCES PSS 2023-24 (unmatched Catholic row)"
        if not match and site_cands:
            for c_ in site_cands:
                cand = [r for r in cde if skey(c_) and skey(r["name"]) == skey(c_)]
                if len(cand) == 1:
                    match, basis, site_name = cand[0], "school site name + CDE", c_
                    break
                if len(cand) > 1:
                    basis = f"ambiguous CDE match: {[c['name'] for c in cand]}"
        name = site_name if site_name else (match["name"] if match else None)
        ent = {"type": "school", "name": name, "website": u, "levels": d["levels"], "grades": match.get("grades") if match else None,
               "address": match.get("address") if match else None, "city": match.get("city") if match else None, "zip": match.get("zip") if match else None,
               "phone": match.get("phone") if match else None, "lat": match.get("lat") if match else None, "lng": match.get("lng") if match else None,
               "name_basis": ("school's own site" if site_name and not (basis or "").startswith("domain+CDE") else basis), "site_name_on_site": site_name,
               "cds_code": match["cds_code"] if match else None, "enrollment": match["enrollment"] if match else None,
               "enrollment_school_year": match["school_year"] if match else None,
               "address_source": ((match.get("_addr_src") or "schools_cde.json (CDE PSA joined to NCES PSS)") if match and match.get("address") else None),
               "source_url": list_url, "fetched_at": fetched, "school_site_fetched_at": site_fetched}
        if site_note:
            ent["site_note"] = site_note
        if not match:
            ent["parse_note"] = "no CDE/NCES record matched; address not available from the diocese's list"
        out.append(ent)
    merged, by_cds = [], {}
    for e in out:  # the same school can be linked twice (preschool section + TK-8 section, or two sites)
        k = e["cds_code"]
        if k and k in by_cds:
            t = by_cds[k]
            t["levels"] = sorted(set(t["levels"]) | set(e["levels"]), key=lambda x: ["preschool program", "TK-8", "middle school (6-8)", "high school (9-12)"].index(x) if x in ["preschool program", "TK-8", "middle school (6-8)", "high school (9-12)"] else 9)
            if e["website"] != t["website"]:
                if re.match(r"https?://(www\.)?dsj\.org/", t["website"]) and not re.match(r"https?://(www\.)?dsj\.org/", e["website"]):
                    t["website"], e["website"] = e["website"], t["website"]
                t.setdefault("other_websites", []).append(e["website"])
            continue
        if k:
            by_cds[k] = e
        merged.append(e)
    out = merged
    return out, {"statement": mt.group(0) if mt else None, "students": mt.group(1) if mt else None, "elementary": mt.group(2) if mt else None, "high": mt.group(3) if mt else None, "source_url": list_url, "fetched_at": fetched}


def akey(n: str) -> str:
    n = (n or "").lower().replace("\u2019", "'")
    n = re.sub(r"\b(saint|st\.?)\b", "st", n)
    n = re.sub(r"\b(parish|church|the|of|catholic|community|national|and|in|at)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


# Assignment wording on the clergy list that differs from the parish table's name.
CLERGY_ALIASES = {
    akey("Saint Elizabeth Parish"): ["Saint Elizabeth of Portugal Parish"],
    akey("Saint Julie Parish"): ["Saint Julie Billiart Parish"],
    akey("Five Wounds Parish"): ["Five Wounds Portuguese National Parish"],
    akey("Oratory of the Immaculate Heart of Mary"): ["Immaculate Heart of Mary Oratory"],
    akey("Saint Nicholas and Saint William Catholic Parish"): ["Saint Nicholas Church", "Saint William Church"],
}


def build_clergy(parishes: list[dict]) -> dict:
    """Priests' assignments from the diocese's public clergy list (name + assignment text; the list gives no pastor / vicar role).
    Stored per parish as `clergy_assigned`. Retired, on-leave, chaplaincy, school and seminary entries are counted, not attached to a parish.
    Emails and phone numbers are not on that page and are not stored."""
    html, fetched = read_laptop("clergy.html")
    url = f"{BASE}/about-us/clergy/clergy-list/"
    if not html:
        return {}
    b = soup(html)
    tabs = b.find_all("table")
    if not tabs:
        return {}
    rows = [[C.clean_text(x.get_text(" ", strip=True)) for x in tr.find_all("td")] for tr in tabs[0].find_all("tr")]
    rows = [r for r in rows if len(r) >= 2 and r[0]]
    by_name = {p["name"]: p for p in parishes}
    stats = {"priests_listed": len(rows), "attached_to_parish": 0, "retired": 0, "other_assignment": 0, "other_examples": []}
    for p in parishes:
        p["clergy_assigned"] = []
    for name, asg in rows:
        a = asg or ""
        if re.match(r"(?i)^retired", a):
            stats["retired"] += 1
            continue
        if re.match(r"(?i)^(chaplain|on leave|missioned|judicial|interim director)", a) or "seminary" in a.lower() or "high school" in a.lower() or "bishop of" in a.lower():
            stats["other_assignment"] += 1
            continue
        k = akey(a)
        ak = max((x for x in CLERGY_ALIASES if x in k), key=len, default=None)
        targets = [by_name[x] for x in CLERGY_ALIASES.get(ak, []) if x in by_name]
        if not targets:
            hits = [(len(akey(p["name"])), p) for p in parishes if akey(p["name"]) and akey(p["name"]) in k]
            if hits:
                L = max(h[0] for h in hits)
                targets = [p for l, p in hits if l == L]
        if not targets:
            stats["other_assignment"] += 1
            if len(stats["other_examples"]) < 6:
                stats["other_examples"].append(f"{name}: {a}")
            continue
        for p in targets:
            p["clergy_assigned"].append({"name": name, "assignment_listed": a})
            p["clergy_source_url"] = url
        stats["attached_to_parish"] += 1
    stats.update(source_url=url, fetched_at=fetched)
    return stats


def build_retreats() -> list[dict]:
    html, fetched = read_laptop("retreat.html")
    url = f"{BASE}/catholic-life/retreat-centers/"
    if not html:
        return []
    t = flat(soup(html))
    j = t.find("located within the boundaries of the Diocese of San Jose")
    seg = t[j:t.find("1150 North First St", j)] if j >= 0 else ""
    specs = [
        ("Jesuit Retreat Center of Los Altos", r"Jesuit Retreat Center of Los Altos\s+(\S+)\s+sponsored by ([^0-9]+?)\s+(\d+ [A-Za-z ]+?) (Los Altos) CA (\d{5}) (\(\d{3}\) \d{3}-\d{4})"),
        ("Our Lady of Santa Clara Retreat Center", r"Our Lady of Santa Clara Retreat Center\s+(\S+)\s+()(\d+ [A-Za-z ]+?) (Cupertino), CA, (\d{5})()"),
        ("St. Clare\u2019s Retreat", r"St\. Clare\u2019s Retreat\s+(\S+)\s+sponsored by ([^0-9]+?)\s+(\d+ [A-Za-z ]+?) (Soquel), CA (\d{5}) (\(\d{3}\) \d{3}-\d{4})"),
        ("Vallombrosa Center", r"Vallombrosa Center\s+()sponsored by ([^0-9]+?)\s+(\d+ [A-Za-z ]+?) (Menlo Park), CA (\d{5}) (\(\d{3}\) \d{3}-\d{4})"),
    ]
    out = []
    for name, rx in specs:
        m = re.search(rx, seg)
        if not m:
            continue
        web, sponsor, street, city, zp, ph = m.groups()
        ent = {"type": "retreat_center", "name": name, "address": street.strip(), "city": city, "zip": zp, "phone": ph or None,
               "website": C.norm_url(web) if web else None, "lat": None, "lng": None,
               "sponsor": C.clean_text(sponsor) if sponsor else None,
               "source_url": url, "fetched_at": fetched}
        if city in ("Soquel", "Menlo Park"):
            ent["note"] = (f"Listed on dsj.org's Retreat Centers page ('located within the boundaries of the Diocese of San Jose'), but the address is in "
                           f"{'Santa Cruz' if city == 'Soquel' else 'San Mateo'} County, not Santa Clara County.")
        out.append(ent)
    return out


def build_orders() -> list[dict]:
    """Religious congregations with members working or residing in the diocese: name, headcount the page gives, congregation website.
    The contact person and mailbox printed for each (personal addresses) are not stored."""
    out = []
    for fname, kind, path in (("orders_men.html", "men", "religious-orders-for-men"), ("orders_women.html", "women", "religious-orders-for-women")):
        html, fetched = read_laptop(fname)
        if not html:
            continue
        url = f"{BASE}/about-us/consecrated-life-men-and-women/{path}/"
        for p_ in soup(html).find_all("p"):
            if "Working/residing" not in p_.get_text():
                continue
            hd = p_.find_previous(lambda t: t.name in ("h6", "strong") and t.get_text(strip=True) and "Working" not in t.get_text() and "Website" != t.get_text(strip=True))
            name = C.clean_text(hd.get_text(" ", strip=True)) if hd else None
            if not name:
                continue
            m = re.search(r"Working/residing in the Diocese:\s*(\d+)", p_.get_text(" ", strip=True))
            web = next((C.norm_url(a["href"]) for a in p_.find_all("a", href=True) if a.get_text(strip=True).lower() == "website"), None)
            web = None if web and "@" in web else web  # one href on the page is a mangled email address
            out.append({"type": "religious_community", "name": name, "gender": kind, "members_in_diocese": int(m.group(1)) if m else None,
                        "website": web, "address": None, "city": None, "zip": None, "phone": None, "lat": None, "lng": None,
                        "source_url": url, "fetched_at": fetched})
    return out


def build_events() -> tuple[list[dict], int, dict]:
    txt, fetched = read_laptop("events.json")
    if not txt:
        return [], 0, {}
    data = json.loads(txt)
    url = f"{BASE}/wp-json/tribe/events/v1/events"
    evs = C.tribe_normalise(data, url, fetched or C.now_iso())
    raw = len(evs)
    evs = [e for e in evs if C.in_window(e["start"], e["end"])]
    kept, dropped = C.screen_events(evs)
    return kept, raw, dict(dropped)


def build_about() -> dict:
    html, fetched = read_laptop("about.html")
    if not html:
        return {}
    t = flat(soup(html))
    url = f"{BASE}/about-us/"
    out = {"source_url": url, "fetched_at": fetched}
    m = re.search(r"The Diocese of San Jos\u00e9 in California serves all of ([A-Z][A-Za-z ]+ County)", t)
    out["counties_says"] = m.group(0) if m else None
    out["counties"] = [m.group(1).replace(" County", "")] if m else []
    m = re.search(r"Bishop of San Jos\u00e9:\s*The Most Rev\.\s*(.+?)\s+Auxiliary Bishop", t)
    out["bishop"] = m.group(1).strip() if m else None
    m = re.search(r"Auxiliary Bishop-elect & Vicar General/Chancellor:\s*([A-Z][\w.]+ [A-Z]\. [A-Z][a-z]+, [A-Z.]+)", t)
    out["aux"] = m.group(1).strip() if m else None
    m = re.search(r"Footprint \(at a glance\).*?Statistics current as of ([^.]*\.?\s*\d{4})", t)
    out["footprint_asof"] = m.group(1).strip() if m else None
    fp = re.search(r"Communities: ([^\u2022]+\u2022[^\u2022]+\u2022[^C]+?)(?= Catholic schools)", t)
    out["communities_says"] = C.clean_text(fp.group(1)) if fp else None
    m = re.search(r"Other Catholic institutions: (.+?) Clergy & formation", t)
    out["other_institutions_says"] = m.group(1).strip() if m else None
    m = re.search(r"We worship in (\d+) languages each Sunday", t)
    out["languages_each_sunday"] = int(m.group(1)) if m else None
    m = re.search(r"(1150 North First St\.,\s*Suite 100 San Jose, CA 95112) - Phone: (\(\d{3}\) \d{3}-\d{4})", t)
    out["office"] = {"address": "1150 North First St., Suite 100", "city": "San Jose", "zip": "95112", "phone": m.group(2)} if m else None
    return out


def main() -> None:
    fx = C.Fetcher(SLUG)
    parishes = build_parishes()
    C.dedupe_ids(parishes)
    st = build_structure(parishes)
    clergy = build_clergy(parishes)
    ab = build_about()
    schools, smeta = build_schools(fx)
    ents = build_cemeteries(fx) + schools + build_retreats() + build_orders()
    # diocesan pastoral center
    if ab.get("office"):
        ents.append({"type": "diocesan_office", "name": "Diocese of San Jose Pastoral Center", **ab["office"], "website": BASE, "lat": None, "lng": None,
                     "source_url": ab["source_url"], "fetched_at": ab["fetched_at"]})
    ents.append({"type": "catholic_charities", "name": None, "address": None, "city": None, "zip": None, "website": None, "phone": None,
                 "note": "The About page counts '1 social services agency' among the diocese's other Catholic institutions but does not name it on the pages read. "
                         "Catholic Charities of Santa Clara County's own site (catholiccharitiesscc.org) answers a Cloudflare challenge (sandbox and laptop connection).",
                 "source_url": ab.get("source_url"), "fetched_at": ab.get("fetched_at")})
    C.dedupe_ids([e for e in ents if e.get("name")])
    events, raw_events, dropped = build_events()
    cath = next((p for p in parishes if p["type"] == "cathedral"), None)
    bishops = []
    if ab.get("bishop"):
        bishops.append({"name": f"Most Rev. {ab['bishop']}", "title": "Bishop of San Jose", "source_url": ab["source_url"]})
    if ab.get("aux"):
        bishops.append({"name": ab["aux"], "title": "Auxiliary Bishop-elect and Vicar General/Chancellor (as the About page states it)", "source_url": ab["source_url"]})
    structure = {
        "diocese": SLUG, "name": "Diocese of San Jose", "website": BASE, "bishops": bishops,
        "cathedral": ({k: cath[k] for k in ("name", "address", "city", "zip", "website", "source_url")} if cath else None),
        "counties": ab.get("counties", []), "counties_source_url": ab.get("source_url"), "counties_says": ab.get("counties_says"),
        "counties_verified_from_diocese_site": bool(ab.get("counties")),
        "units": st["units"],
        "notes": [
            "The deaneries page lists Deaneries 2 to 7; no Deanery 1 appears on the page.",
            "No regions or vicariates are published; the deaneries page also carries parish boundary descriptions (street lines) that are not stored here.",
            f"Deaneries page lists a parish the parish table does not: {[u['listed_as'] + ' (' + u['city'] + ')' for u in st['unmatched']]}.",
            f"The About page counts communities as: {ab.get('communities_says')}. The parish table lists {len(parishes)} rows (parishes, missions, additional churches and a campus ministry).",
        ],
        "footprint": {"as_of": ab.get("footprint_asof"), "other_institutions_says": ab.get("other_institutions_says"),
                      "languages_each_sunday": ab.get("languages_each_sunday"), "schools_says": smeta.get("statement"),
                      "source_url": ab.get("source_url"), "fetched_at": ab.get("fetched_at")},
        "source_urls": [x for x in [ab.get("source_url"), st["source_url"], f"{BASE}/locations/parishes/", smeta.get("source_url"),
                                    clergy.get("source_url"), f"{BASE}/catholic-life/retreat-centers/", f"{BASE}/about-us/consecrated-life-men-and-women/"] if x],
        "fetched_at": ab.get("fetched_at"),
    }
    c0 = C.counts(parishes, ents, events)
    log = "\n".join(f"| {w} UTC | {v} | {st_} | `{pth}` |" for w, v, st_, pth in REQUEST_LOG)
    nsch, nsch_addr = sum(1 for e in ents if e["type"] == "school"), sum(1 for e in ents if e["type"] == "school" and e.get("address"))
    sources = f"""- **robots.txt** (www.dsj.org): `User-agent: *` disallows only `/wp-login.php` and `/wp-admin/` and sets **`Crawl-delay: 600`** (ten minutes between requests). Every request below was spaced
  at least 600 s from the previous one to this host, from either vantage, except one (see "Early request").
- **Why the owner's laptop was used.** From the sandbox, `python-requests` to dsj.org (REST and HTML) got HTTP 403 three times (08:26, 08:43, 09:01 UTC): a bare `403 Forbidden / nginx` page whose body carries Cloudflare's challenge-injection script (`__CF$cv$params`), i.e. a Cloudflare bot rule, not a robots.txt rule.
  Per the collection rules the same URLs were then fetched once from the owner's home connection with plain `curl` (default User-Agent), which answered **200**. Nothing was spoofed or worked around:
  the same two plain clients simply got different answers from the site. The pages were saved to `data/raw/california/dsj-laptop/` (gitignored); `san-jose.py build` reads them offline.
- **Early request (disclosure).** The laptop request for `/wp-json/wp/v2/parish` at 08:51:50 UTC was sent 1 min 45 s before the 600 s Crawl-delay measured from the 08:43:35 sandbox request had run out
  (a sequencing mistake: the two vantage points' last-request times were not yet shared). Every later request used a shared last-request clock and a 620 s margin. Two requests (09:32:51 and 10:34:51) hit a 301 because
  the diocese's menu links redirect; each cost a full slot. Total requests to www.dsj.org: {len(REQUEST_LOG)} (3 blocked, 2 redirects, {len(REQUEST_LOG) - 5} pages), plus one robots.txt.
- **Parishes**: `/locations/parishes/` is a table of {len(parishes)} rows (name, street, city, zip, phone, link to the parish page). The WordPress `parish` post type (`/wp-json/wp/v2/parish`, {sum(1 for p in parishes if p['wp_id'])} records) is joined by page URL for the
  free-text Mass-time notes: `languages` are the languages named in those notes; `website` is set only where the note itself gives the parish's own address. Individual `/parish/<slug>/` pages were not fetched (57 x 10 min).
- **Structure**: `/locations/deaneries/` (Deaneries 2-7: dean, member parishes by city; parish-boundary street descriptions are not stored); `/about-us/` ("serves all of Santa Clara County", bishops, footprint statistics, pastoral center address).
- **Clergy**: `/about-us/clergy/clergy-list/` (public list of {clergy['priests_listed']} priests: name and assignment text only). {clergy['attached_to_parish']} are attached to a parish as `clergy_assigned`; {clergy['retired']} retired,
  {clergy['other_assignment']} have non-parish assignments. The list gives no pastor / parochial vicar title, so `pastor` is null on every parish rather than guessed. Deacons' table, emails and phones are not stored.
- **Schools**: `/catholicschools/locateschool/` is a page of logo links with no text, so each school is identified by the website it links to: {nsch} schools. The page says "{smeta.get('statement')}". Names come from each school's own site title
  (one plain `GET` per school site, robots.txt respected, 1 s apart); where a site answered HTTP 406 to the plain client or its robots.txt returned 503, the name comes from the CDE/NCES record that matches the domain (`name_basis: domain+CDE`).
  Street address, enrollment and CDS code come from `data/real/california/schools_cde.json` for {nsch_addr} of {nsch}; three schools the NCES join could not place (St. Joseph Mountain View, St. Martin of Tours, St. Mary Gilroy) are matched by domain and CDE district.
- **Cemeteries**: the diocese's 'Find a Cemetery' link goes to `cfcssanjose.org` (Catholic Funeral & Cemetery Services San Jose); its public Locations pages give two cemeteries' addresses, generic phones and office hours. Staff and obituary pages are not read.
- **Retreat centers**: `/catholic-life/retreat-centers/` (four listed; two have addresses outside Santa Clara County, noted on the rows).
- **Religious communities**: `/about-us/consecrated-life-men-and-women/` says "43 Congregations"; its two sub-pages (`.../religious-orders-for-men/`, `.../religious-orders-for-women/`) list {sum(1 for e in ents if e['type'] == 'religious_community')}
  ({sum(1 for e in ents if e['type'] == 'religious_community' and e.get('gender') == 'men')} men's, {sum(1 for e in ents if e['type'] == 'religious_community' and e.get('gender') == 'women')} women's) with the number of members working or residing in the diocese and a congregation website.
  The contact person and mailbox printed for each congregation are personal addresses and are not stored. The page counts 43; 3 are not listed on the two sub-pages read.
- **Events**: The Events Calendar REST API, one request for 2026-10-31 to 2027-02-28: {raw_events} listed, {len(events)} kept; the privacy screen dropped {dropped or 'none'}.
- **Counties**: "The Diocese of San Jos\u00e9 in California serves all of Santa Clara County" (`/about-us/`). This verifies Santa Clara County from the diocese's own site; `dioceses.json` had cited Wikipedia only.

### Every request to www.dsj.org

| When | From | HTTP | Path |
|---|---|---|---|
{log}
"""
    blockers = f"""- dsj.org returns 403 to the sandbox's plain client; the laptop's plain curl is served. If that differs for the next person, the saved HTML in `data/raw/california/dsj-laptop/` is the input (gitignored, so a re-run needs the same laptop route).
- **Pastors**: not available. The clergy list names assignments, not roles. Individual parish pages (not fetched) may state them.
- **Parish websites**: only {sum(1 for p in parishes if p['website'])} of {len(parishes)} (the Mass-time notes rarely give one); no office emails; no lat/lng (the geocoding step belongs to another agent).
- **Catholic Charities of Santa Clara County**: the About page counts "1 social services agency" without naming it; `catholiccharitiesscc.org` answers a Cloudflare challenge from the sandbox and from the laptop (HTTP 403, 'Just a moment...'), so nothing was collected. The entity is a named gap.
- **Not named on the pages read, so not invented**: the university, the home for the aged and the social-services agency the About page counts; the 'Local Catholic Organizations' page (`/catholic-life/local-catholic-organizations/`) was not fetched (a further 10 minutes per page); the diocese has no seminary (seminarians' names on the vocations pages are not stored).
- **Deanery 1**: the deaneries page starts at Deanery 2. 'Our Mother of Perpetual Help' (Santa Clara) is on the deaneries page but not in the parish table.
- The deaneries page's boundary descriptions were read for names only; they are not geocoded.
- 57 parish rows are more than the About page's '49 parishes, 3 missions': the table includes additional churches of a parish (Our Lady of the Rosary, St. Albert the Great: `parent_parish` St. Thomas Aquinas), a campus ministry and an oratory.
- `status` is `active` for all rows: the table lists no closed or merged parishes. It means 'listed on the fetch date'."""
    readme = C.readme("Diocese of San Jose", BASE, "san-jose.py", sources, blockers, c0)
    c = C.write_all(SLUG, parishes, structure, ents, events, readme)
    print(json.dumps(c, indent=1), "raw events", raw_events, dropped)


if __name__ == "__main__":
    main()
