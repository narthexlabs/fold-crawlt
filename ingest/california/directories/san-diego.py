#!/usr/bin/env python3
"""Diocese of San Diego: official directory data -> data/real/california/dioceses/san-diego/.

sdcatholic.org (WordPress + Elementor + JetEngine) answers 403 to the sandbox for HTML pages (home, sitemaps, /maps/, /masses/) but
leaves /wp-json/ open, as the Oakland diocese does. Everything here is read from public REST responses:

  /wp-json/wp/v2/pages/<id>              rendered content of the Find a Parish page (a JetEngine grid: name, address, phone, website, and the
                                         map markers' coordinates), Find a School, Deans (Vicar Forane), Leadership, Religious Communities, Contact, Bishop pages
  /wp-json/wp/v2/masses, maps, catholic-org, wp/v2/<taxonomies>   Mass-language and city terms, school/parish map records, campus ministries
  /wp-json/tribe/events/v1/events        public events

Run: python3 -I ingest/california/directories/san-diego.py
"""
from __future__ import annotations

import html as htmllib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

SLUG = "san-diego"
BASE = "https://sdcatholic.org"
PAGES = {"find-a-parish": 34120, "find-a-school": 41308, "deans": 10757, "leadership": 1574, "religious": 15555, "contact": 10667,
         "bishop-pham": 2672, "pulido": 2686, "masses-languages": 5960, "history": 2333}
EASTERN = {"BYZANTINE": "Byzantine", "SYRIAC": "Syriac", "MARONITE": "Maronite", "CHALDEAN": "Chaldean", "MELKITE": "Melkite", "UKRAINIAN": "Ukrainian",
           "ARMENIAN": "Armenian"}
SMALL = {"of", "the", "de", "del", "la", "los", "y", "and", "a", "in", "at", "by", "to", "for", "on"}


def soup(h: str) -> BeautifulSoup:
    return BeautifulSoup(h, "html.parser")


def title_case(s: str) -> str:
    s = re.sub(r"\s+", " ", htmllib.unescape(s)).strip()
    out = []
    for i, w in enumerate(s.split(" ")):
        lw = w.lower()
        if i > 0 and lw in SMALL:
            out.append(lw)
        elif lw == "saint":
            out.append("St.")
        elif lw == "saints":
            out.append("Sts.")
        elif lw in ("bvm",):
            out.append("BVM")
        else:
            out.append("-".join(p.capitalize() if p else p for p in w.split("-")) if not w.startswith("(") else "(" + w[1:].capitalize())
    r = " ".join(out)
    r = re.sub(r"\b(\w)’S\b", r"\1’s", r)
    r = r.replace("’S ", "’s ").replace("'S ", "'s ")
    return r


def norm(s: str) -> str:
    s = htmllib.unescape(s).lower().replace("’", "'").replace("mt.", "mount").replace("á", "a").replace("é", "e").replace("è", "e")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\bsaints?\b|\bsts?\.?\b", " st ", s)
    s = re.sub(r"\b(the|church|of|parish|catholic|bvm|indian|mission)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get_page(fx: C.Fetcher, pid: int) -> dict:
    r = fx.get(f"{BASE}/wp-json/wp/v2/pages/{pid}", cache=True)
    d = r.json()
    d["_fetched_at"] = r.fetched_at
    return d


def page_text(d: dict) -> str:
    return soup(d["content"]["rendered"]).get_text("\n", strip=True)


# ------------------------------------------------------------------------------------------------ parishes
def parse_address(raw: str, locality: str | None):
    a = re.sub(r"\s+", " ", raw).strip()
    zp = C.zip5(a)
    a = re.sub(r",?\s*(CA|California),?\s*9\d{4}(-\d{4})?\s*$", "", a).strip(" ,")
    a = re.sub(r"\s*9\d{4}\s*$", "", a).strip(" ,")
    parts = [p.strip() for p in a.split(",") if p.strip()]
    city = None
    if len(parts) >= 2:
        city = parts[-1]
        street = ", ".join(parts[:-1])
    else:  # "135 East 4th Street Calexico"
        street = a
        if locality and a.lower().endswith(locality.lower()):
            street, city = a[: -len(locality)].strip(" ,"), locality
    # "Calexico" glued to the street when only one comma-less chunk
    if city is None and parts:
        city = locality
    return street, city, zp


def build_parishes(fx: C.Fetcher, pages: dict, maps: dict, masses: list, terms: dict):
    fp = pages["find-a-parish"]
    h = fp["content"]["rendered"]
    s = soup(h)
    m = re.search(r'data-markers="([^"]+)"', h)
    markers = {x["id"]: (x["latLang"]["lat"], x["latLang"]["lng"]) for x in json.loads(htmllib.unescape(m.group(1)))}
    src = f"{BASE}/find-a-parish/"
    rest_src = f"{BASE}/wp-json/wp/v2/pages/{PAGES['find-a-parish']}"
    fetched = fp["_fetched_at"]
    rows = []
    for it in s.select(".jet-listing-grid__item"):
        pid = int(it["data-post-id"])
        raw_title = htmllib.unescape(it.find("h2").get_text(" ", strip=True))
        texts = [x.get_text(" ", strip=True) for x in it.select(".elementor-icon-list-text")]
        web = next((a["href"] for a in it.find_all("a", href=True) if a["href"] != "#"), None)
        if "," in raw_title and not raw_title.endswith(")"):
            nm, locality = raw_title.rsplit(",", 1)
            locality = locality.strip()
        else:
            nm, locality = raw_title, None
        # "IMMACULATA, The, San Diego" -> name "The Immaculata"
        m2 = re.match(r"^(.*),\s*The,\s*(.+)$", raw_title)
        if m2:
            nm, locality = "The " + m2.group(1), m2.group(2).strip()
        street, city, zp = parse_address(texts[0], locality)
        phone = C.norm_phone(texts[1]) if len(texts) > 1 else None
        up = nm.upper()
        rite = None
        for k, v in EASTERN.items():
            if f"({k})" in up:
                rite = f"{v} (Eastern Catholic)"
        typ = "parish"
        if "CATHEDRAL" in up:
            typ = "cathedral"
        elif re.search(r"\bMISSION\b", up) and not up.startswith("MISSION SAN"):
            typ = "mission"
        designation = None
        if up.startswith("MISSION SAN"):
            designation = "historic mission (active parish)"
        elif "SHRINE" in up:
            designation = "shrine"
        elif "INDIAN" in up:
            designation = "Native American parish or mission"
        row = {"name": title_case(nm), "listed_name": raw_title, "base_name": nm.strip(), "locality": locality, "type": typ,
               "address": street, "city": city, "zip": zp,
               "county": "Imperial" if (zp and zp.startswith(("922",))) or (city in ("Calexico", "El Centro", "Brawley", "Imperial", "Holtville", "Calipatria", "Westmorland", "Winterhaven")) else "San Diego",
               "lat": round(markers[pid][0], 6) if pid in markers else None, "lng": round(markers[pid][1], 6) if pid in markers else None,
               "phone": phone, "website": C.norm_url(web), "office_email": None,
               "languages": [], "pastor": None, "region": None, "region_name": None, "deanery": None, "deanery_number": None, "vicariate": None,
               "rite": rite or "Latin", "designation": designation, "status": "active",
               "status_note": f"Listed on the diocese's Find a Parish page, fetched {fetched[:10]}; the page carries no closure or merger notice.",
               "directory_post_id": pid, "directory_url": (maps.get(pid) or {}).get("link"),
               "geo_source": "sdcatholic.org Find a Parish map marker (JetEngine)" if pid in markers else None,
               "source_url": rest_src, "page_url": src, "fetched_at": fetched}
        if rite:
            row["name"] = re.sub(r"\s*\(.*?\)$", lambda mm: mm.group(0), row["name"])
        rows.append(row)
    # disambiguate duplicate display names with the locality: "St. Mary (Escondido)"
    cnt = Counter(r["name"] for r in rows)
    for r in rows:
        if cnt[r["name"]] > 1 and r["locality"]:
            r["name"] = f"{r['name']} ({title_case(r['locality'])})"
    # languages and online Mass from the Masses directory
    ml = terms["masses-language"]
    mc = terms["masses-city"]
    matched = 0
    used = set()
    idx = defaultdict(list)
    for r in rows:
        idx[norm(r["base_name"])].append(r)
    unmatched_masses = []
    for x in masses:
        t = htmllib.unescape(x["title"]["rendered"])
        city = next((mc.get(c) for c in x["masses-city"]), None)
        keys = [norm(t)] + ([norm(t.rsplit(",", 1)[0])] if "," in t else [])
        cands = []
        for k in keys:
            cands = idx.get(k, [])
            if cands:
                break
        tail = t.rsplit(",", 1)[1].strip().lower() if "," in t else ""
        if len(cands) > 1 and tail:
            ct = [r for r in cands if tail in ((r.get("locality") or "").lower(), (r.get("city") or "").lower())]
            cands = ct or cands
        if len(cands) > 1 and city:
            c1 = [r for r in cands if (r.get("locality") or "").lower() == city.lower()]
            c2 = c1 or [r for r in cands if city.lower() == (r.get("city") or "").lower()]
            cands = c2 or cands
        if len(cands) == 1:
            r = cands[0]
            matched += 1
            used.add(x["id"])
            langs = [ml[i] for i in x["masses-language"] if i in ml]
            r["languages"] = sorted(set(r["languages"]) | set(langs))
            r["mass_city_label"] = city
            r["online_mass"] = bool(r.get("online_mass")) or 64 in x["mass-category"]
            r["mass_in_other_language"] = bool(r.get("mass_in_other_language")) or 65 in x["mass-category"]
            r["masses_url"] = x["link"]
        else:
            unmatched_masses.append((t, city, [ml[i] for i in x["masses-language"] if i in ml], x["link"], len(cands)))
    return rows, markers, matched, unmatched_masses, rest_src


# ------------------------------------------------------------------------------------------------ structure
def parse_deans(page: dict):
    s = soup(page["content"]["rendered"])
    t = [ln.replace("\u200b", "").replace("\xa0", " ").strip() for ln in s.get_text("\n", strip=True).split("\n")]
    # first block: JetEngine team cards (current); second block: a typed list that is older (flagged in notes)
    deans, old, groups = {}, {}, {}
    i = 0
    cur_block = "cards"
    while i < len(t):
        ln = t[i]
        if ln == "Deans" and i > 5:
            cur_block = "list"
        if ln.startswith("Very Rev.") and i + 1 < len(t) and t[i + 1].endswith("Deanery"):
            (deans if cur_block == "cards" else old)[t[i + 1].replace(" Deanery", "")] = ln
        if ln == "Deanery Groupings":
            j = i + 1
            g = None
            while j < len(t):
                if t[j].endswith(" Deanery"):
                    g = t[j].replace(" Deanery", "")
                    groups[g] = []
                elif g:
                    groups[g].append(t[j])
                j += 1
            break
        i += 1
    return deans, old, groups


ALIASES = {"basilica san diego de alcala": "san diego de alcala"}  # keys are norm() forms (norm drops "mission")


def match_group_member(label: str, rows: list[dict], assigned: dict):
    name, loc = label, ""
    if "," in label:
        a, b = label.rsplit(",", 1)
        if b.strip().lower() in {(r.get("locality") or "").lower() for r in rows} | {(r.get("city") or "").lower() for r in rows}:
            name, loc = a, b.strip().lower()
    k0 = norm(name)
    k = norm(ALIASES.get(k0, name)) if k0 in ALIASES else k0
    pool = [r for r in rows if norm(r["base_name"]) == k]
    if not pool:  # "Saint Francis, Vista" for "SAINT FRANCIS OF ASSISI, Vista": prefix match, only when a locality is given
        pool = [r for r in rows if loc and (norm(r["base_name"]).startswith(k + " ") or k.startswith(norm(r["base_name"]) + " "))]
    if loc:
        p1 = [r for r in pool if (r.get("locality") or "").lower() == loc]
        p2 = p1 or [r for r in pool if (r.get("city") or "").lower() == loc]
        pool = p2 or pool
    free = [r for r in pool if r["id"] not in assigned]
    if len(free) == 1:
        return free[0]
    if len(pool) == 1:
        return pool[0]
    if not loc and "," in label:  # "Saint Charles, Imperial Beach": the place is a neighbourhood the Find a Parish page files under San Diego
        a = label.rsplit(",", 1)[0]
        pool2 = [r for r in rows if norm(r["base_name"]) == norm(a) and r["id"] not in assigned]
        if len(pool2) == 1:
            return pool2[0]
    return None


def build_structure(pages, parishes, fx):
    deans, old, groups = parse_deans(pages["deans"])
    dsrc = f"{BASE}/deans-vicar-forane/"
    assigned: dict = {}
    unmatched = []
    for g, members in groups.items():
        for lab in members:
            r = match_group_member(lab, parishes, assigned)
            if r is None:
                unmatched.append((g, lab))
                continue
            assigned[r["id"]] = g
            r["deanery"] = f"{g} Deanery"
    order = ["Cathedral", "El Cajon", "El Centro", "Escondido", "Mission", "Oceanside", "South Bay"]
    units = []
    for n, g in enumerate(order, 1):
        for r in parishes:
            if r["deanery"] == f"{g} Deanery":
                r["deanery_number"] = None  # the diocese does not number its deaneries
        units.append({"kind": "deanery", "name": f"{g} Deanery", "parent": None,
                      "leader": {"name": deans.get(g), "title": "Dean (Vicar Forane)"} if deans.get(g) else None,
                      "member_parishes": sorted(r["name"] for r in parishes if r["deanery"] == f"{g} Deanery"),
                      "other_listed_sites": [lab for gg, lab in unmatched if gg == g],
                      "source_url": dsrc})
    bp, pu = page_text(pages["bishop-pham"]), page_text(pages["pulido"])
    assert "BISHOP OF SAN DIEGO" in bp and "AUXILIARY BISHOP OF SAN DIEGO" in pu
    bp = re.sub(r"\s+", " ", bp)
    spans = re.search(r"The diocese spans ([A-Za-z ]+ and [A-Za-z ]+ counties)", bp)
    fp = re.sub(r"\s+", " ", page_text(pages["find-a-parish"]))
    fpm = re.search(r"The Diocese of San Diego has (\d+) parishes and (\d+) missions across the nearly [\d,]+ square miles of ([A-Za-z ]+ and [A-Za-z ]+ counties)", fp)
    cath = next(p for p in parishes if p["type"] == "cathedral")
    diff = {g: (deans.get(g), old.get(g)) for g in order if deans.get(g) and old.get(g) and deans.get(g) != old.get(g)}
    return {
        "diocese": SLUG, "name": "Diocese of San Diego", "website": BASE,
        "counties": ["San Diego", "Imperial"],
        "counties_source_url": f"{BASE}/bishop-pham/",
        "counties_source_says": f"Bishop Pham's page: \"{spans.group(0) if spans else ''}\"; Find a Parish page: \"{fpm.group(0) if fpm else ''}\".",
        "counties_verified_from_diocese_site": bool(spans and fpm),
        "bishops": [
            {"name": "Most Rev. Michael M. Pham", "title": "Bishop of San Diego (installed 2025-07-17)", "source_url": f"{BASE}/bishop-pham/"},
            {"name": "Most Rev. Felipe Pulido", "title": "Auxiliary Bishop of San Diego; Vicar for Clergy", "source_url": f"{BASE}/auxiliary-bishop-pulido/"},
        ],
        "cathedral": {"name": cath["name"], "address": cath["address"], "city": cath["city"], "zip": cath["zip"], "lat": cath["lat"], "lng": cath["lng"],
                      "source_url": cath["source_url"]},
        "units": units,
        "notes": [
            "The diocese has seven deaneries (no regions or vicariates) and does not number them. Deans are the current cards on the Deans (Vicar Forane) page.",
            f"The same page also carries a typed list of deans that disagrees with the cards for: {diff}. The cards (the staff directory, updated 2026-10-30) are used; the typed list looks stale.",
            f"Parishes matched to a deanery grouping: {len(assigned)} of {len(parishes)}. Grouping entries not matched to a Find a Parish row (mostly mission churches without a row of their own): {unmatched}.",
            f"The Find a Parish page counts {fpm.group(1) if fpm else '?'} parishes and {fpm.group(2) if fpm else '?'} missions; its list view has {len(parishes)} rows.",
        ],
        "source_urls": [dsrc, f"{BASE}/find-a-parish/", f"{BASE}/bishop-pham/", f"{BASE}/auxiliary-bishop-pulido/"],
        "fetched_at": C.now_iso(),
    }, assigned, unmatched


# ------------------------------------------------------------------------------------------------ entities
def school_entities(pages, maps):
    p = pages["find-a-school"]
    h = p["content"]["rendered"]
    m = re.search(r'data-markers="([^"]+)"', h)
    markers = {x["id"]: (x["latLang"]["lat"], x["latLang"]["lng"]) for x in json.loads(htmllib.unescape(m.group(1)))}
    t = soup(h).get_text("\n", strip=True).split("\n")
    regions, cur = {}, None
    start = t.index("Catholic Schools") + 1 if "Catholic Schools" in t else 0
    for ln in t[start:]:
        if ln in ("Central San Diego", "San Diego Coastal", "South Bay", "East County", "Imperial Valley", "North County Coastal", "North County Inland"):
            cur = ln
        elif cur:
            regions.setdefault(cur, []).append(ln)
    reg_of = {}
    for r, names in regions.items():
        for n in names:
            reg_of[norm(n)] = r
    out = []
    for pid, (lat, lng) in markers.items():
        mp = maps.get(pid)
        if not mp or 249 not in mp["map-cat"]:
            continue
        raw = htmllib.unescape(mp["title"]["rendered"])
        name, _, city = raw.rpartition(",")
        name, city = name.strip(), city.strip()
        low = name.lower()
        sub = "preschool" if "preschool" in low else ("high_school" if "high school" in low or "st. augustine" in low or "academy of our lady of peace" in low
                                                       or "notre dame academy" in low or "cathedral catholic" in low or "cristo rey" in low or "(high school)" in low or low.startswith("saint joseph academy") and False else "elementary_middle")
        k = norm(name)
        listed = next((n for r_, ns in regions.items() for n in ns if norm(n) == k), None)
        region = reg_of.get(k) or next((v for kk, v in reg_of.items() if kk and (kk in k or k in kk)), None)
        if listed and "high school" in listed.lower():
            sub = "high_school"
        if name == "Mater Dei Catholic School":  # the page lists "Mater Dei Catholic High School" and "... Elementary" separately
            sub = "high_school"
        out.append({"type": "school", "subtype": sub, "name": name, "address": None, "city": city, "zip": None, "website": None, "phone": None,
                    "office_email": None, "lat": round(lat, 6), "lng": round(lng, 6), "region_label": region, "conducted_by": None,
                    "note": "Name, city and map coordinates from the diocese's Find a School map; the street address is not published in the page content.",
                    "directory_url": mp["link"], "source_url": f"{BASE}/wp-json/wp/v2/pages/{PAGES['find-a-school']}", "page_url": f"{BASE}/schools/find-a-school/",
                    "fetched_at": p["_fetched_at"]})
    return out, regions


def org_entities(maps_orgs, pages, fx):
    out = []
    for x in maps_orgs:
        t = htmllib.unescape(x["title"]["rendered"])
        c = soup(x["content"]["rendered"])
        txt = c.get_text(" | ", strip=True)
        if t in ("University of San Diego", "University of California, San Diego", "San Diego State University"):
            ad = re.search(r"(\d{3,5}[^|]*?)(?:,\s*)?San Diego\s*(9\d{4})", txt)
            ph = C.norm_phone(txt)
            web = next((a["href"] for a in c.find_all("a", href=True)), None)
            if not web:
                mw = re.search(r"(www\.[a-z0-9.\-]+)", txt)
                web = f"https://{mw.group(1)}" if mw else None
            street = ad.group(1).replace("Newman Center Catholic Community,", "").replace("Newman Center,", "").strip(" ,|") if ad else None
            out.append({"type": "campus_ministry", "subtype": None, "name": ("Newman Center Catholic Community (UC San Diego)" if "California" in t else
                                                                             "Newman Center (San Diego State University)" if "State" in t else "University of San Diego Campus Ministry"),
                        "organization": t, "address": street, "city": "San Diego", "zip": ad.group(2) if ad else None, "website": C.norm_url(web), "phone": ph,
                        "office_email": None, "conducted_by": None, "source_url": x["link"], "fetched_at": C.now_iso()})
        elif t == "Oblates of Prince of Peace Abbey":
            out.append({"type": "religious_community", "subtype": "abbey", "name": "Prince of Peace Abbey (Oblates)", "address": None, "city": "Oceanside", "zip": None,
                        "website": "https://www.princeofpeaceabbey.org", "phone": C.norm_phone(txt), "office_email": None, "conducted_by": None,
                        "note": "Listed under Catholic Organizations; the page gives a phone and website only. City is the abbey's known location (Oceanside, Benedictine monastery), not stated on the diocese page.",
                        "source_url": x["link"], "fetched_at": C.now_iso()})
    # religious communities (women religious): congregation names only; the sisters' names are not stored
    p = pages["religious"]
    s = soup(p["content"]["rendered"])
    names = []
    for el in s.find_all(string=re.compile(r"\([A-Z]{2,6}\)\s*$")):
        names.append(el.strip())
    seen = set()
    for nm in names:
        if nm in seen:
            continue
        seen.add(nm)
        el = s.find(string=nm)
        site = None
        for nxt in el.parent.find_all_next("a", href=True, limit=3):
            if "Learn" in nxt.get_text():
                site = nxt["href"]
                break
        out.append({"type": "religious_community", "subtype": "women_religious_congregation", "name": re.sub(r"\s+", " ", nm), "address": None, "city": None, "zip": None,
                    "website": C.norm_url(site), "phone": None, "office_email": None, "conducted_by": None,
                    "note": "Congregation serving in the diocese, from the diocese's Religious Communities page (it counts 28 congregations in its header). The sisters' names are not stored.",
                    "source_url": f"{BASE}/wp-json/wp/v2/pages/{PAGES['religious']}", "page_url": f"{BASE}/women-religious/religious-communities/", "fetched_at": p["_fetched_at"]})
    # Pastoral Center
    cp = pages["contact"]
    t = page_text(cp)
    assert "3888 Paducah Drive" in t
    out.append({"type": "diocesan_office", "subtype": "chancery", "name": "Diocesan Pastoral Center", "address": "3888 Paducah Drive", "city": "San Diego", "zip": "92117",
                "website": BASE, "phone": "(858) 490-8200", "office_email": None, "conducted_by": None,
                "note": "Mailing address P.O. Box 85728, San Diego 92186-5728; office hours Mon-Fri 8:30-4:30.",
                "source_url": f"{BASE}/wp-json/wp/v2/pages/{PAGES['contact']}", "page_url": f"{BASE}/contact/", "fetched_at": cp["_fetched_at"]})
    # Catholic Charities Diocese of San Diego
    try:
        r = fx.get("https://ccdsd.org/")
        tt = soup(r.text).get_text(" ", strip=True)
        if "3888 Paducah Drive" in tt:
            out.append({"type": "catholic_charities", "subtype": "agency", "name": "Catholic Charities, Diocese of San Diego", "address": "3888 Paducah Drive", "city": "San Diego",
                        "zip": "92117", "website": "https://ccdsd.org/", "phone": "(619) 323-2841", "office_email": None, "conducted_by": None,
                        "note": "Administration offices at the Pastoral Center (no direct services there); direct services are at sites across San Diego and Imperial Counties, not listed on the home page.",
                        "source_url": r.url, "fetched_at": r.fetched_at})
    except C.Blocked:
        pass
    return out


def build_events(fx):
    try:
        evs, raw_n = C.tribe_events(fx, BASE)
    except C.Blocked as e:
        return [], 0, Counter(), str(e)
    kept, dropped = C.screen_events(evs)
    return kept, raw_n, dropped, None


# ------------------------------------------------------------------------------------------------ main
def main() -> None:
    fx = C.Fetcher(SLUG)
    pages = {k: get_page(fx, pid) for k, pid in PAGES.items()}

    def coll(route, extra=None):
        items, _ = fx.wp_all(BASE, "wp/v2/" + route, {"per_page": 100, **(extra or {})})
        return items
    maps = {x["id"]: x for x in coll("maps")}
    masses = coll("masses")
    orgs = coll("catholic-org")
    terms = {t: {x["id"]: htmllib.unescape(x["name"]) for x in coll(t)} for t in ("masses-language", "masses-city")}
    parishes, markers, matched, unmatched_masses, rest_src = build_parishes(fx, pages, maps, masses, terms)
    C.dedupe_ids(parishes, "name")
    # mission/other rows that appear only in the Masses directory (no address or coordinates published)
    extra = []
    for t, city, langs, link, ncand in unmatched_masses:
        nm = title_case(t)
        if re.search(r"\bmission\b|center", t, re.I):
            extra.append({"name": nm, "listed_name": t, "base_name": re.sub(r"\s+", " ", t).strip(), "locality": city, "type": "mission" if re.search("mission", t, re.I) else "chapel", "address": None, "city": city,
                          "zip": None, "county": None, "lat": None, "lng": None, "phone": None, "website": None, "office_email": None, "languages": sorted(langs),
                          "pastor": None, "region": None, "region_name": None, "deanery": None, "deanery_number": None, "vicariate": None, "rite": "Latin",
                          "designation": None, "status": "active",
                          "status_note": "Appears only in the diocese's Masses directory (a Mass-times listing); no address is published there.",
                          "source_url": f"{BASE}/wp-json/wp/v2/masses", "page_url": link, "fetched_at": C.now_iso(), "directory_url": link})
    C.dedupe_ids(extra, "name")
    for r in extra:
        r["id"] = r["id"] + "-masses-listing"
    parishes_only = parishes
    parishes = parishes + extra
    structure, assigned, unmatched_groups = build_structure(pages, parishes, fx)
    allp = parishes
    parishes = parishes_only
    schools, school_regions = school_entities(pages, maps)
    ents = schools + org_entities(orgs, pages, fx)
    for e in ents:
        for k in ("conducted_by", "note"):
            if e.get(k):
                e[k] = C.screen_text(e[k])
    events, raw_events, dropped, ev_block = build_events(fx)
    fetched = C.now_iso()
    cnt = C.counts(allp, ents, events)
    et = Counter((e["type"], e.get("subtype")) for e in ents)
    nogeo = sum(1 for p in allp if p["lat"] is None)
    nodean = [p["name"] for p in allp if not p["deanery"]]
    print("masses unmatched:", unmatched_masses)
    print("groups unmatched:", unmatched_groups)
    print("no deanery:", nodean)
    stmt = re.search(r"The Diocese of San Diego has [^.]*\.", re.sub(r"\s+", " ", page_text(pages["find-a-parish"])))
    stmt = stmt.group(0) if stmt else ""
    readme = f"""# Diocese of San Diego: official directory data

Collected {fetched[:10]} from https://sdcatholic.org (Roman Catholic Diocese of San Diego). Script: `ingest/california/directories/san-diego.py`.
Re-run: `python3 -I ingest/california/directories/san-diego.py` (responses cached in `data/raw/california/dioceses/san-diego/`, gitignored).

## Sources and method

- **Access.** robots.txt (Yoast defaults; only `/events/*` query forms and `/venue/*?` are disallowed, no Crawl-delay; the module waits >= 1 s between requests). Plain default `python-requests` User-Agent.
  HTML pages (home, sitemaps, `/maps/...`, `/masses/...`) answer a bare 403 to the sandbox and were not fetched. `/wp-json/` is open, so everything is from REST responses, as for Oakland:
  `wp/v2/pages/<id>` (rendered content), `wp/v2/maps`, `wp/v2/masses`, `wp/v2/catholic-org`, the `masses-language` / `masses-city` terms, and `tribe/events/v1/events`.
  The site also exposes JetEngine / MCP admin routes; none was called.
- **Parishes and missions: the Find a Parish page** (`/find-a-parish/`, REST page {PAGES['find-a-parish']}). Its list view is a JetEngine grid with {len(parishes)} rows: name, locality, address, phone, website. The grid's map markers
  give each row's coordinates (matched on the record id, {len(markers)} markers). The page states "{stmt}"
- **Languages and online Mass:** the Masses directory (`wp/v2/masses`, {len(masses)} records, each tagged with Mass-language terms, a city, and categories "Online Mass" / "Mass in Language"), matched to parish rows by name and city:
  {matched} of {len(masses)} matched; the rest are listed below. `languages` are the non-English Mass languages the diocese lists; English is not listed.
- **Structure:** the Deans (Vicar Forane) page gives seven deaneries, their deans and their member parishes. {len(assigned)} of {len(allp)} rows (the {len(parishes)} Find a Parish rows plus {len(extra)} Masses-directory-only rows) are placed in a deanery (name + city match). No regions or vicariates exist.
- **Schools:** the Find a School map: {len(schools)} records (names, city, map coordinates; preschools included), grouped by region on the page. Street addresses are not in the page content.
- **Other entities:** three campus ministries and Prince of Peace Abbey from the Catholic Organizations records; women religious congregations from the Religious Communities page (names only); the Pastoral Center from the Contact page;
  Catholic Charities from ccdsd.org (administration at the Pastoral Center).
- **Events:** {"collected: " + str(len(events)) + " kept of " + str(raw_events) + " (dropped by the privacy screen: " + str(dict(dropped) or "none") + ")" if not ev_block else "NOT collected: " + ev_block}

## Counts

| parishes.json rows | {len(allp)} |
|---|---|
{C.md_table(dict(Counter(p['type'] for p in allp)))}| of which Find a Parish rows | {len(parishes)} |
| of which Masses-directory-only mission/center rows (no address) | {len(extra)} |
| rite other than Latin | {sum(1 for p in allp if p['rite'] != 'Latin')} |
| with coordinates | {len(allp) - nogeo} |
| with a deanery | {sum(1 for p in allp if p['deanery'])} |
| in Imperial County (by city/ZIP) | {sum(1 for p in allp if p.get('county') == 'Imperial')} |

| entities by type (subtype) | |
|---|---|
{C.md_table({f'{t} ({s})' if s else t: n for (t, s), n in et.items()})}| events | {len(events)} |
| deaneries in structure.json | 7 |

## Notes and gaps

- **Status.** Every row is `active`: the list carries no closure or merger flags. The diocese is in Chapter 11 reorganization (a page on its site says so); nothing there names parish closures. Check before use.
- **Pastors** are not on the Find a Parish page; `pastor` is null. The monthly priest-assignment announcements (`wp/v2/priest-assignments`) name moves, not a standing roster, and were not parsed.
- **Eastern Catholic churches** ({', '.join(p['name'] for p in allp if p['rite'] != 'Latin')}) are in the list with `rite` set from the label in the title; they have their own eparchies.
- **Missions.** `type: mission` is set from the word in the name (Indian missions, Polish and Melkite/Armenian missions). The three historic missions (San Diego de Alcalá, San Luis Rey, San Antonio de Pala) are active parishes and keep `type: parish` with `designation`.
- **Masses directory entries with no parish row:** {[(t, c) for t, c, _l, _u, n in unmatched_masses]}.
- **Deanery groupings that matched no row:** {unmatched_groups} (listed as `other_listed_sites` on the deanery).
- **Not collected:** school street addresses and websites; chaplaincy directory (50 records of hospital, prison and military chaplains; chaplains are named priests); Catholic cemeteries (the diocese lists them only as an outside link);
  lay movements in Catholic Organizations (about 50, not requested); Mass times; priest assignments.
"""
    c = C.write_all(SLUG, allp, structure, ents, events, readme)
    print(json.dumps(c, indent=2))


if __name__ == "__main__":
    main()
