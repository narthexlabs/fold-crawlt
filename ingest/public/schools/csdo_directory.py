"""Diocesan school directory -> data/real/schools_directory.json

The official list (csdo.org, the Department of Catholic Schools) and oakdiocese.org's school
finder both return 403 to scripted requests from this environment (Cloudflare / Azure WAF), and
the Wayback Machine is blocked by egress policy. So the directory is assembled from:

  1. The school universe: CDE affidavit + NCES PSS (schools_cde.json / schools_nces.json).
  2. Lumen Christi Academies (lumenchristiacademies.org): its schools and principals.
  3. Each school's own website (domain verified by fetching it and checking the <title>, or
     taken from links on parish sites / LCA). Principal names are scraped from the homepage
     and up to six likely subpages (principal / administration / leadership / staff / about).
  4. Parish affiliation: the parish of the same title in the same city (the school's own site
     or the parish site links them). Order-run and independent schools have none.

Run after cde_affidavit.py and nces_pss.py:  python -I ingest/public/schools/csdo_directory.py
"""
from __future__ import annotations

import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schools import REAL, SESSION, load_json, now_iso, operator_for, save_json  # noqa: E402

LCA = "https://lumenchristiacademies.org"

# cds_code -> (website, how the domain was confirmed, parish affiliation or None)
SITES = {
    "01611926965909": ("https://ascshayward.org/", "title", "All Saints Parish, Hayward"),
    "01612916972574": ("https://www.assumptionsanleandro.org/", "domain-only (Cloudflare)", "Assumption of the Blessed Virgin Mary Parish, San Leandro"),
    "01612596932784": ("https://www.bishopodowd.org/", "title", None),
    "07617546933345": ("https://www.carondeleths.org/", "title", None),
    "07100746172985": (None, "not found", None),
    "07617546967624": ("https://www.ctkschool.org/", "title", "Christ the King Parish, Pleasant Hill"),
    "01612756972632": ("https://corpuschristischool.org/", "title", "Corpus Christi Parish, Piedmont"),
    "01612596154736": ("https://cristoreydelasalle.org/", "title", None),
    "07617546158992": (None, "not found", None),
    "07617546933394": ("https://www.dlshs.org/", "title", None),
    "01612596932941": ("https://www.hnhsoakland.org/", "title", None),
    "07616486967640": ("https://holyrosaryantioch.org/", "domain-only (Cloudflare; parish site)", "Most Holy Rosary Parish, Antioch"),
    "01611766972731": ("https://holyspiritschool.org/", "title", "Holy Spirit Parish, Fremont"),
    "01611926933006": ("https://www.moreaucatholic.org/", "title", None),
    "01611506970651": ("https://www.olgschool.org/", "title + parish-site link", "Our Lady of Grace Parish, Castro Valley"),
    "01611766970628": (None, "not found (school closed after 2023-24)", "Our Lady of Guadalupe Parish, Fremont"),
    "07617546967673": ("https://qasconcord.org/", "title + LCA link", "Queen of All Saints Parish, Concord"),
    "07617546967681": ("https://stagnes-school.org/", "domain-only (Cloudflare)", "St. Agnes Parish, Concord"),
    "01611926965917": ("https://www.stclementschool.org/", "title", "St. Clement Parish, Hayward"),
    "07617546967657": ("https://www.sfaconcord.org/", "title", "St. Francis of Assisi Parish, Concord"),
    "01611196933154": ("https://www.sjnd.org/", "title", None),
    "01612596973010": ("https://www.stleothegreat.org/", "title", "St. Leo the Great Parish, Oakland"),
    "01611436933162": ("https://www.saintmaryschs.org/", "title", None),
    "07617966933493": ("https://www.salesian.com/", "title", None),
    "01611436973085": ("https://themadeleine.com/", "title", "St. Mary Magdalen Parish, Berkeley"),
    "01612596967517": ("https://stanthony-oakland.org/", "LCA link (site unreachable; school closed after 2023-24)", "St. Anthony Parish, Oakland"),
    "01611926972871": (None, "not found", "St. Bede Parish, Hayward"),
    "07617396969919": ("https://stcatherinemartinez.com/", "title + LCA link", "St. Catherine of Siena Parish, Martinez"),
    "07617966972442": ("https://www.stdavidschool.org/", "title", "St. David of Wales Parish, Richmond"),
    "01612346972921": ("https://www.stedwardschool.org/", "domain-only (Cloudflare)", "St. Edward Parish, Newark"),
    "01612596972939": ("https://saintelizabeth.us/", "title + LCA link", "St. Elizabeth Parish, Oakland"),
    "01613096972947": ("https://stfelicitas-school.org/", "parish-site link (Cloudflare)", "St. Felicitas Parish, San Leandro"),
    "07618046972459": ("https://stisidoreschool.org/", "title", "St. Isidore Parish, Danville"),
    "01611926972970": ("https://stjoachimschool.org/", "parish-site link", "St. Joachim Parish, Hayward"),
    "01613096970602": ("https://www.stjohncatholicschool.org/", "parish-site link (Cloudflare)", "St. John the Baptist Parish, San Lorenzo"),
    "07617966967699": ("https://www.sjbschool.org/", "title", "St. John the Baptist Parish, El Cerrito"),
    "01611196965891": ("https://stjosephalameda.org/", "title", "St. Joseph Basilica, Alameda"),
    "01611766972996": ("https://www.stjosephschoolfremont.org/", "title + parish-site link", "St. Joseph Parish (Mission San Jose), Fremont"),
    "07617966972475": ("https://stjosephpinole.org/", "domain-only (Cloudflare)", "St. Joseph Parish, Pinole"),
    "01612916970636": ("https://stleanderschool.org/", "parish-site link (Cloudflare)", "St. Leander Parish, San Leandro"),
    "07618126967707": (None, "not found", "St. Mary Parish, Walnut Creek"),
    "01612006967525": ("https://smsliv-org.sites.ecatholic.com/", "search result (Cloudflare)", "St. Michael Parish, Livermore"),
    "07616976970669": ("https://www.stpatrickschoolrodeo.org/", "parish-site link (Cloudflare)", "St. Patrick Parish, Rodeo"),
    "07617966972483": ("https://st-paulschool.org/", "title + LCA link", "St. Paul Parish, San Pablo"),
    "07617136972491": ("https://stperpetuaschool.org/", "title", "St. Perpetua Parish, Lafayette"),
    "07617886965933": ("https://stpetermartyrschool.org/", "title + LCA link", "St. Peter Martyr Parish, Pittsburg"),
    "01611196973077": ("https://www.spnalameda.org/", "title", "St. Philip Neri - St. Albert the Great Parish, Alameda"),
    "01750937012222": ("https://www.straymond.org/", "title", "St. Raymond Parish, Dublin"),
    "01612596967533": ("https://sttheresaschool.org/", "title", "St. Theresa Parish, Oakland"),
    "07617547092109": ("https://www.woodroseacademy.org/", "title", None),
}
# Addresses for schools absent from NCES PSS, taken from the school's own or its parish's site.
ADDR_SUPPLEMENT = {
    "01612916970636": {"address": "451 Davis Street", "city": "San Leandro", "zip": "94577", "phone": "5103514144",
                       "source_url": "https://stleanderchurch.org/our-school/"},
    "07617966972483": {"address": "1825 Church Lane", "city": "San Pablo", "zip": None, "phone": None,
                       "source_url": "https://st-paulschool.org/"},
}
LCA_SCHOOLS = {"07617546967673", "07617396969919", "01612596972939", "07617966972483", "07617886965933",
               "01612596967517"}

TITLE_WORDS = r"(?:Head of School|Principal|President|Interim Principal|Director)"
NAME = r"((?:Mrs?\.|Ms\.|Dr\.|Sr\.|Sister|Br\.|Brother|Fr\.|Father|Mr\.)?\s?[A-Z][a-zA-Z'\-]+(?:\s[A-Z]\.)?(?:\s[A-Z][a-zA-Z'\-]+){1,2})"
BAD = re.compile(r"(?i)\b(welcome|message|school|catholic|our|from|the|meet|contact|office|vice|assistant|"
                 r"academy|parish|church|high|elementary|admissions|letter|corner|desk|events?|calendar|news|"
                 r"donate|apply|board|staff|faculty|about|home|search|menu|learn|more|read|how|to|parent|teacher|association|"
                 r"award|winning|global|managing|team|support|giving|mission|vision|program|student|students|view|profile)\b")


def fetch(url: str) -> str | None:
    try:
        r = SESSION.get(url, timeout=20)
    except Exception:
        return None
    if r.status_code != 200 or "Just a moment" in r.text[:2000]:
        return None
    return r.text


def text_of(html: str) -> str:
    s = re.sub(r"<(script|style|noscript)[\s\S]*?</\1>", " ", html)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s)


def find_leader(text: str) -> tuple[str, str] | None:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    pats = [
        re.compile(rf"^{NAME},?\s*[-–|,]?\s*({TITLE_WORDS})\b"),
        re.compile(rf"^({TITLE_WORDS})\s*[:\-–|,]\s*{NAME}$"),
    ]
    for i, ln in enumerate(lines):
        if len(ln) > 80:
            continue
        for p in pats:
            m = p.match(ln)
            if m:
                g = m.groups()
                name, title = (g[0], g[1]) if p is pats[0] else (g[1], g[0])
                if not BAD.search(name):
                    return name.strip(), title
        # name on one line, bare title on the next
        if re.fullmatch(rf"{TITLE_WORDS}", ln) and i > 0:
            prev = lines[i - 1]
            m = re.fullmatch(NAME, prev)
            if m and not BAD.search(prev) and len(prev) < 40:
                return prev, ln
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            m = re.fullmatch(NAME, nxt)
            if m and not BAD.search(nxt) and len(nxt) < 40:
                return nxt, ln
    return None


def principal_from_site(base: str) -> dict | None:
    home = fetch(base)
    if not home:
        return None
    pages = [(base, home)]
    host = urlparse(base).netloc.replace("www.", "")
    links = []
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>([\s\S]*?)</a>', home):
        u = urljoin(base, unescape(href))
        if urlparse(u).netloc.replace("www.", "") != host:
            continue
        key = (u + " " + re.sub(r"<[^>]+>", " ", label)).lower()
        if re.search(r"principal|head-of-school|administration|leadership|our-staff|faculty|staff-directory|"
                     r"about-us|welcome|who-we-are|contact", key) and u not in links:
            links.append(u)
    links.sort(key=lambda u: (0 if re.search(r"principal|head", u.lower()) else
                              1 if re.search(r"admin|leader", u.lower()) else 2))
    for u in links[:6]:
        h = fetch(u)
        if h:
            pages.append((u, h))
    # Prefer an explicit principal/head over president (high schools have both).
    found = []
    for u, h in pages:
        r = find_leader(text_of(h))
        if r:
            found.append({"name": r[0], "title": r[1], "source_url": u})
    if not found:
        return None
    found.sort(key=lambda f: 0 if re.search(r"Principal|Head of School", f["title"]) else 1)
    return found[0]


def lca_principals() -> dict:
    """LCA leadership page: school heading, then principal name + csdo.org email."""
    url = f"{LCA}/about-us/leadership"
    h = fetch(url)
    out = {}
    if not h:
        return out
    t = text_of(h)
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    for school, key in [("Queen of All Saints School", "07617546967673"), ("St. Paul School", "07617966972483"),
                        ("Saint Elizabeth School", "01612596972939"), ("St. Peter Martyr School", "07617886965933")]:
        # Section heading is an exact line (the nav menu has "(City)" suffixes); the first
        # csdo.org email after it belongs to that school's principal, whose name precedes it.
        for i, ln in enumerate(lines):
            if ln != school:
                continue
            for j in range(i + 1, min(i + 12, len(lines))):
                if lines[j].endswith("@csdo.org"):
                    names = [x for x in lines[i + 1:j] if re.fullmatch(NAME, x) and not BAD.search(x)]
                    if names:
                        out[key] = {"name": names[-1], "title": "Principal", "email": lines[j], "source_url": url}
                    break
            break
    # St. Catherine of Siena is led by LCA's assistant superintendent (named on the same page).
    m = re.search(r"Jessica Griswold[\s\S]{0,400}?St\. Catherine of Siena", t)
    if m:
        out["07617396969919"] = {"name": "Jessica Griswold", "title": "Assistant Superintendent (serves St. Catherine of Siena)",
                                 "email": "jgriswold@csdo.org", "source_url": url}
    return out


def main() -> None:
    fetched_at = now_iso()
    cde = load_json(REAL / "schools_cde.json", {}).get("records", [])
    xw = {r["cds_code"]: r for r in load_json(REAL / "schools_crosswalk.json", {}).get("rows", []) if r["cds_code"]}
    latest = {}
    for r in sorted(cde, key=lambda r: r["school_year"]):
        latest[r["cds_code"]] = r
    lca = lca_principals()
    recs = []
    for cds, c in sorted(latest.items(), key=lambda kv: kv[1]["name"]):
        site, how, parish = SITES.get(cds, (None, "not found", None))
        x = dict(xw.get(cds, {}))
        sup = ADDR_SUPPLEMENT.get(cds)
        if sup and not x.get("address"):
            x.update({k: v for k, v in sup.items() if k != "source_url"})
        principal = lca.get(cds)
        if principal is None and site:
            principal = principal_from_site(site)
        op = operator_for(c["name"])
        recs.append({
            "cds_code": cds,
            "nces_ppin": (x.get("nces", {}).get("2023-24") or x.get("nces", {}).get("2021-22") or {}).get("ppin"),
            "name": c["name"],
            "address": x.get("address"), "city": x.get("city") or c.get("city_hint"), "zip": x.get("zip"),
            "county": c["county"], "phone": x.get("phone"),
            "grades": c["grades"],
            "latest_affidavit_year": c["school_year"],
            "active_2025_26": c["school_year"] == "2025-26",
            "principal": principal["name"] if principal else None,
            "principal_title": principal["title"] if principal else None,
            "principal_email": (principal or {}).get("email"),
            "principal_source_url": principal["source_url"] if principal else None,
            "parish_affiliation": parish,
            "parish_affiliation_basis": "same-title parish in same city" if parish else None,
            "operator": op or ("Lumen Christi Academies (diocesan)" if cds in LCA_SCHOOLS else "Diocese of Oakland / parish"),
            "diocesan": op is None,
            "lumen_christi_academy": cds in LCA_SCHOOLS,
            "website": site,
            "website_verification": how,
            "address_source": ("nces_pss" if xw.get(cds, {}).get("address") else
                               (sup["source_url"] if sup else None)),
            "source_url": [u for u in [site, (principal or {}).get("source_url"), c["source_url"],
                                       (sup or {}).get("source_url")] if u],
            "fetched_at": fetched_at,
        })
    found = sum(1 for r in recs if r["principal"])
    save_json(REAL / "schools_directory.json", {
        "description": "Catholic schools in the Diocese of Oakland: principal, parish, website, operator.",
        "official_source_status": {
            "https://www.csdo.org/": "403 (Cloudflare) to scripted requests; WebFetch also blocked",
            "https://www.oakdiocese.org/schools/": "403 (Azure WAF)",
            "https://22403.sites.ecatholic.com/schoolfinder": "403 / WebFetch permission timeout",
            "web.archive.org": "blocked by egress policy",
        },
        "fallback_sources": [f"{LCA}/our-schools", f"{LCA}/about-us/leadership", "each school's website",
                             "https://www.cde.ca.gov/ds/si/ps/", "https://nces.ed.gov/surveys/pss/"],
        "notes": ["Principal names scraped heuristically from school sites; verify before publishing.",
                  "parish_affiliation is inferred from the school's name and city, not from an official list."],
        "counts": {"schools": len(recs), "active_2025_26": sum(r["active_2025_26"] for r in recs),
                   "with_principal": found, "with_website": sum(1 for r in recs if r["website"]),
                   "diocesan": sum(r["diocesan"] for r in recs)},
        "fetched_at": fetched_at,
        "records": recs,
    })
    print(f"schools_directory.json: {len(recs)} schools, {found} principals, "
          f"{sum(1 for r in recs if r['website'])} websites")
    for r in recs:
        print(f"  {r['name'][:40]:40} | {r['principal'] or '-':28} | {r['principal_title'] or ''}")


if __name__ == "__main__":
    main()
