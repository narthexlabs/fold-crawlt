#!/usr/bin/env python3
"""Diocese of San Bernardino: official directory data -> data/real/california/dioceses/san-bernardino/.

sbdcatholics.org sits behind Imperva/Incapsula. From the sandbox, /wp-json/ and the HTML pages answer a JavaScript
challenge, and the module does not work around it. Matthew's laptop (a home connection, plain urllib User-Agent, 1.2 s
between requests) was used once, as the task rules allow, and got ordinary 200 responses for the whole site. That
crawl is staged in data/raw/california/dioceses/san-bernardino/laptop/ (gitignored):

  sb_bundle.json        104 parish/mission/chapel pages (sbp_<entity number>), 27 school pages (sbs_<id>), and 9 About-type pages
  sb_lists.json         WP REST lists: sbd-parishes, sbd-schools, sbd-ministries, ministry-type
  sb_events_raw.json    The Events Calendar REST response (tribe/events/v1)
  sb_root.json          /wp-json/ (site description)

Live from the sandbox (these hosts answer it): sites.sbdcatholics.org (the two religious-communities iframes), ccsbriv.org
(Catholic Charities) and the US Census Geocoder.

Run: python3 -I ingest/california/directories/san-bernardino.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

SLUG = "san-bernardino"
BASE = "https://www.sbdcatholics.org"
RAW = C.RAW_ROOT / "dioceses" / SLUG / "laptop"
VICARIATES = ["High Desert", "San Bernardino", "Riverside", "West End", "Low Desert", "Hemet"]
STAFF_TITLES = {"Pastor", "Administrator", "Parochial Vicar", "Deacon", "Resident Priest", "Rector"}
CLERGY_PREFIX = re.compile(r"^(Very Rev\.|Rev\.|Rt\. Rev\.|Msgr\.|Fr\.|Father|Br\.|Sr\.|Dr\.)")
PARISH_LABELS = ["Address", "Mailing Address", "Phone", "Email", "Office Hours", "Visit Website", "Demographics"]
SCHOOL_LABELS = ["Details", "Grades", "Administration", "Demographics", "Office Hours", "Entity Number", "Contact", "Phone",
                 "Fax", "Email", "Address", "Mailing Address", "Visit Website", "School History"]
CENSUS = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


# ---------------------------------------------------------------------------------------------- reading the laptop crawl
def load():
    bundle = json.loads((RAW / "sb_bundle.json").read_text())
    lists = {k: json.loads(v["body"]) for k, v in json.loads((RAW / "sb_lists.json").read_text()).items()}
    list_meta = {k: {"url": v["url"], "fetched_at": v["fetched_at"]} for k, v in json.loads((RAW / "sb_lists.json").read_text()).items()}
    events = json.loads((RAW / "sb_events_raw.json").read_text())
    root = json.loads((RAW / "sb_root.json").read_text())
    return bundle, lists, list_meta, events, root


def lines_of(rec: dict) -> list[str]:
    s = BeautifulSoup(rec["html"], "html.parser")
    return [re.sub(r"\s+", " ", x.replace("​", "").replace("﻿", "")).strip() for x in s.get_text("\n", strip=True).split("\n") if x.strip()]


def sections(L: list[str], labels: list[str], start: int = 0, stop: int | None = None) -> dict[str, list[str]]:
    """label -> lines up to the next label; the first occurrence of each label wins."""
    out: dict[str, list[str]] = {}
    cur = None
    for x in L[start:stop]:
        if x in labels:
            cur = x if x not in out else None
            if cur:
                out[cur] = []
            continue
        if cur is not None:
            out[cur].append(x)
    return out


def website_href(rec: dict) -> str | None:
    s = BeautifulSoup(rec["html"], "html.parser")
    for a in s.find_all("a"):
        if a.get_text(" ", strip=True) == "Visit Website" and a.get("href"):
            return C.norm_url(a["href"])
    return None


def split_street(lines: list[str]):
    """['73441 Fred Waring Dr', 'Palm Desert, CA,', '92260'] -> (street, city, zip)"""
    if not lines:
        return None, None, None
    zp = None
    rest = list(lines)
    if re.fullmatch(r"9\d{4}(-\d{4})?", rest[-1]):
        zp = rest.pop()[:5]
    city = None
    if rest and re.search(r",\s*CA,?$", rest[-1]):
        city = re.sub(r",\s*CA,?$", "", rest.pop()).strip()
    street = ", ".join(rest) or None
    return street, city, zp


def mass_groups(lines: list[str]) -> tuple[dict, bool]:
    heads = ["Weekend Mass", "Weekday Mass", "Confessions", "Adoration", "Holy Days", "Contact Office"]
    out, cur, by_office = {}, None, False
    for x in lines:
        if x in heads:
            if x == "Contact Office":
                by_office = True
                cur = None
            else:
                cur = x
                out[cur] = []
            continue
        if cur:
            out[cur].append(x)
    return {k: v for k, v in out.items() if v}, by_office


def staff_of(lines: list[str]) -> list[dict]:
    items: list[list] = []
    for x in lines:
        if x in STAFF_TITLES:
            items.append([x, []])
        elif items:
            items[-1][1].append(x)
    out = []
    for title, vals in items:
        if title == "Deacon":
            nm = " ".join(vals[:2]) if len(vals) >= 2 and not CLERGY_PREFIX.match(vals[0]) and not vals[0].startswith("Founded") else None
            names = [("Deacon " + nm)] if nm else []
        else:
            names = [v for v in vals if CLERGY_PREFIX.match(v) and len(v) < 90]
        for n in names:
            out.append({"title": title, "name": n})
    return out


def parse_parish(key: str, rec: dict) -> dict:
    L = lines_of(rec)
    assert L[1] in VICARIATES, (key, L[1])
    name = L[2]
    i_mass, i_staff = L.index("Mass Times"), L.index("Ecclesial Staff")
    i_contact = L.index("Contact", i_staff)
    desc = " ".join(L[3:i_mass])
    mass, by_office = mass_groups(L[i_mass + 1:i_staff])
    staff = staff_of(L[i_staff + 1:i_contact])
    sec = sections(L, PARISH_LABELS, i_contact + 1)
    street, city, zp = split_street(sec.get("Address", []))
    mstreet, mcity, mzip = split_street(sec.get("Mailing Address", []))
    demo = sec.get("Demographics", [])
    num = fam = None
    for j, x in enumerate(demo):
        if x.startswith("Entity Number") and j + 1 < len(demo):
            num = demo[j + 1]
        if x.startswith("Registered Families") and j + 1 < len(demo):
            fam = demo[j + 1]
    mt = re.search(r"Founded (?:as a parish )?in (\d{4})", desc)
    rel = None
    full = "\n".join(L)
    r = re.search(r"(?:^|\n)(An? (?:chapel|mission) of [^\n]{0,100})", full)
    if r:
        rel = r.group(1).strip()
    email_raw = " ".join(sec.get("Email", [])).strip() or None
    return {
        "entity_number": int(num) if num and num.isdigit() else int(key.split("_")[1]),
        "name": name, "vicariate_name": L[1], "page_title": L[0],
        "founded": int(mt.group(1)) if mt else None,
        "related_to": rel,
        "street": street, "city": city, "zip": zp,
        "mailing": {"address": mstreet, "city": mcity, "zip": mzip} if (mstreet or mcity) else None,
        "phone": C.norm_phone(" ".join(sec.get("Phone", []))),
        "email_raw": email_raw,
        "office_email": C.generic_email(email_raw),
        "office_hours": " ".join(sec.get("Office Hours", [])) or None,
        "website": website_href(rec),
        "mass_times": mass, "mass_times_by_contact_office": by_office,
        "staff": staff,
        "registered_families": int(fam.replace(",", "")) if fam and fam.replace(",", "").isdigit() else None,
        "page_url": rec.get("final_url") or rec["url"], "fetched_at": rec["fetched_at"],
    }


def ptype(name: str, related: str | None) -> str:
    if re.search(r"cathedral", name, re.I):
        return "cathedral"
    if re.search(r"\bchapel\b", name, re.I):
        return "chapel"
    if re.search(r"\bshrine\b", name, re.I):
        return "shrine"
    if re.search(r"newman", name, re.I):
        return "campus_ministry"
    if re.search(r"\bmission\b", name, re.I) or (related and related.lower().startswith("a mission")):
        return "mission"
    if related and related.lower().startswith("a chapel"):
        return "chapel"
    return "parish"


# ---------------------------------------------------------------------------------------------- geocoding
def _geo_queries(street, city, zp):
    """The address as published, then plain normalisations (no guessing): quotes removed, Coachella-valley house numbers
    written without the hyphen ('66-700' -> '66700', the form the Census file uses), then without the ZIP."""
    s1 = re.sub(r"[\u201c\u201d\"]", "", street).strip()
    s2 = re.sub(r"^(\d+)-(\d+)\b", r"\1\2", s1)
    out = []
    for st in (street, s1, s2):
        for z in (zp, None):
            q = f"{st}, {city}, CA {z or ''}".strip()
            if q not in out:
                out.append(q)
    return out


def geocode(fx: C.Fetcher, street, city, zp):
    if not (street and city):
        return None
    for q in _geo_queries(street, city, zp):
        try:
            d, r = fx.get_json(CENSUS, {"address": q, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json"})
        except C.Blocked:
            return None
        m = ((d or {}).get("result") or {}).get("addressMatches") or []
        if not m:
            continue
        g = m[0].get("geographies") or {}
        county = (g.get("Counties") or [{}])[0].get("NAME")
        return {"lat": round(m[0]["coordinates"]["y"], 6), "lng": round(m[0]["coordinates"]["x"], 6), "matched": m[0]["matchedAddress"],
                "county": county.replace(" County", "") if county else None}
    return None


# ---------------------------------------------------------------------------------------------- parishes
def build_parishes(fx, bundle):
    cards = {}
    s = BeautifulSoup(bundle["sbm_parishes"]["html"], "html.parser")
    for el in s.select('[href*="/sbd-parishes/"]'):
        m = re.search(r"/sbd-parishes/(\d+)/", el["href"])
        if not m:
            continue
        nm = el.select_one(".parishname")
        hs = [h.get_text(" ", strip=True) for h in el.select(".elementor-heading-title")]
        if nm and len(hs) >= 2:
            cards[int(m.group(1))] = {"name": nm.get_text(" ", strip=True), "city": hs[0], "vicariate": hs[1]}
    rows, diag = [], {"card_mismatch": [], "no_card": []}
    for key in sorted(k for k in bundle if k.startswith("sbp_")):
        rec = bundle[key]
        d = parse_parish(key, rec)
        card = cards.get(d["entity_number"])
        if not card:
            diag["no_card"].append(d["name"])
        else:
            if card["vicariate"] != d["vicariate_name"] + " Vicariate" or card["city"] != d["city"]:
                diag["card_mismatch"].append((d["name"], card, d["vicariate_name"], d["city"]))
        typ = ptype(d["name"], d["related_to"])
        langs = [x for x in C.detect_languages(" ".join(sum(d["mass_times"].values(), []))) if x != "English"]
        heads = [x for x in d["staff"] if x["title"] in ("Pastor", "Administrator", "Rector")]
        pastor = C.pastor_record(heads[0]["name"], heads[0]["title"]) if heads else None
        note = "Listed on the diocese's Parishes and Missions finder; the finder and the parish page carry no closure or merger notice."
        if typ == "campus_ministry":
            note += " The page says it was founded as a parish in 1971 and serves UC Riverside and Riverside City College students."
        row = {
            "name": d["name"], "type": typ, "address": d["street"], "city": d["city"], "zip": d["zip"], "county": None, "lat": None, "lng": None,
            "phone": d["phone"], "website": d["website"], "office_email": d["office_email"], "languages": langs,
            "pastor": pastor, "clergy": d["staff"],
            "region": None, "region_name": None, "deanery": None, "deanery_number": None,
            "vicariate": d["vicariate_name"] + " Vicariate", "rite": "Latin",
            "status": "active", "status_note": note,
            "entity_number": d["entity_number"], "founded": d["founded"], "related_to": d["related_to"],
            "mailing_address": d["mailing"], "office_hours": d["office_hours"],
            "mass_times": d["mass_times"], "mass_times_by_contact_office": d["mass_times_by_contact_office"],
            "registered_families": d["registered_families"],
            "registered_families_note": "As published on the parish page (diocesan database, page generated 2026-10-29); the diocese does not say how it counts.",
            "geo_source": None,
            "source_url": d["page_url"], "page_url": d["page_url"], "fetched_at": d["fetched_at"],
            "fetched_via": "laptop (plain urllib, 1.2 s spacing); the sandbox is challenged by Imperva",
        }
        rows.append(row)
    return rows, cards, diag


def geocode_rows(fx, rows):
    n = 0
    for r in rows:
        g = geocode(fx, r["address"], r["city"], r["zip"]) if r.get("address") and not re.search(r"reservation", r["address"], re.I) else None
        if g:
            r["lat"], r["lng"], r["county"] = g["lat"], g["lng"], g["county"]
            r["geo_source"] = "US Census Geocoder (Public_AR_Current); matched: " + g["matched"]
            n += 1
    # county for rows the geocoder could not place: the county of the other rows with the same ZIP, else the same city
    for r in rows:
        if r["county"] is None and (r.get("zip") or r.get("city")):
            pool = [x["county"] for x in rows if x["county"] and ((r.get("zip") and x.get("zip") == r["zip"]) or (not r.get("zip") and x.get("city") == r.get("city")))]
            if pool:
                r["county"] = Counter(pool).most_common(1)[0][0]
                r["county_source"] = "inferred from other diocesan rows with the same ZIP" if r.get("zip") else "inferred from other rows in the same city"
    return n


# ---------------------------------------------------------------------------------------------- Eastern and other communities
EAST_HEADS = [
    ("Byzantine Eparchy of Van Nuys Parish - Fontana", "jurisdiction", "Byzantine (Eastern Catholic)"),
    ("Diocese of Newton Mission (Melkite-Greek Catholic) San Bernardino", "jurisdiction", "Melkite (Eastern Catholic)"),
    ("St. Anthony the Great Melkite Catholic Mission", "community", "Melkite (Eastern Catholic)"),
    ("St. Jude Syro Malabar Catholic Mission", "community", "Syro-Malabar (Eastern Catholic)"),
    ("Arab American Ministry", "community", None),
    ("The Ordinariate of the Chair of Saint Peter", "jurisdiction", "Latin"),
    ("The Apostolic Maronite Church", "jurisdiction", "Maronite (Eastern Catholic)"),
]


def parse_eastern(rec: dict):
    L = lines_of(rec)
    start = L.index("Eastern Catholic & Other Catholic Communities", 1) + 1
    end = L.index("How can we help you?")
    L = L[start:end]
    idx = [L.index(h) for h, _, _ in EAST_HEADS]
    assert idx == sorted(idx), idx
    out = []
    for n, (h, kind, rite) in enumerate(EAST_HEADS):
        blk = L[idx[n] + 1:idx[n + 1] if n + 1 < len(EAST_HEADS) else None]
        first = blk[0]
        addr = next((x for x in blk if re.search(r"\b9\d{4}$", x)), None)
        phone = C.norm_phone(next((x for x in blk if re.match(r"^\(\d{3}\)", x)), None))
        kv = {}
        for j, x in enumerate(blk):
            if j + 1 < len(blk) and blk[j + 1].startswith(":"):
                kv[x] = blk[j + 1].lstrip(": ").strip()
        em = None
        for j, x in enumerate(blk):
            if x.lower().startswith("email") and j + 1 < len(blk):
                em = blk[j + 1]
        street, city, zp = (None, None, None)
        if addr:
            m = re.match(r"^(.*?),\s*([A-Za-z .]+?)\s+(9\d{4})$", addr)
            street, city, zp = (m.group(1), m.group(2), m.group(3)) if m else (addr, None, C.zip5(addr))
        out.append({"heading": h, "kind": kind, "rite": rite, "first_line": first, "address": street, "city": city, "zip": zp, "phone": phone,
                    "email_raw": em, "kv": kv})
    return out


def eastern_rows(rec, parishes):
    rows, ents = [], []
    for e in parse_eastern(rec):
        host = find_host(parishes, e["address"], e["city"])
        if e["kind"] == "community":
            name, listing_name = e["heading"], e["first_line"]
            meets = listing_name
        else:
            name, listing_name = re.sub(r"-\d{4}$", "", e["first_line"]).strip(), e["first_line"]
            meets = None
        pastor = None
        if e["kv"].get("Pastor"):
            pastor = C.pastor_record(e["kv"]["Pastor"], "Pastor")
        mass = {k: v for k, v in e["kv"].items() if k not in ("Pastor",)}
        note = ("Listed on the diocese's 'Eastern Catholic & Other Catholic Communities' page, not in the Parishes and Missions finder. "
                "The page gives a heading and one or two lines per community; how the two lines relate is read from the layout.")
        base = {"address": e["address"], "city": e["city"], "zip": e["zip"], "county": None, "lat": None, "lng": None, "phone": e["phone"], "website": None,
                "office_email": C.generic_email(e["email_raw"]), "languages": [], "pastor": pastor, "clergy": [{"title": "Pastor", "name": pastor["name"]}] if pastor else [],
                "region": None, "region_name": None, "deanery": None, "deanery_number": None, "vicariate": None,
                "status": "active", "status_note": note, "source_url": rec.get("final_url") or rec["url"], "page_url": rec.get("final_url") or rec["url"],
                "fetched_at": rec["fetched_at"], "fetched_via": "laptop (plain urllib, 1.2 s spacing); the sandbox is challenged by Imperva",
                "listing_heading": e["heading"], "listing_first_line": e["first_line"], "mass_times": mass, "geo_source": None}
        if e["heading"] == "Arab American Ministry":
            ents.append({"type": "other", "subtype": "ethnic_ministry", "name": "Arab American Ministry", "hosted_at": listing_name,
                         "host_parish_id": None, "host_address_matches_finder_row": host["name"] if host else None,
                         **{k: base[k] for k in ("address", "city", "zip", "phone", "website", "office_email", "lat", "lng", "source_url", "fetched_at")},
                         "note": "Listed with the Eastern and other communities; it names The Holy Name of Jesus (Redlands) and its address, so it is read as a ministry hosted there, not a separate church.",
                         "_east": True})
            continue
        row = {"name": name, "type": "mission" if e["heading"] != "Byzantine Eparchy of Van Nuys Parish - Fontana" else "parish",
               "rite": e["rite"], "jurisdiction": e["heading"] if e["kind"] == "jurisdiction" else None, "meets_at": meets,
               "address_is_host_parish": bool(meets and host), **base}
        if host and meets:
            row["status_note"] += f" The listed address is that of {host['name']} (finder row), where the community meets."
        rows.append(row)
    return rows, ents


# ---------------------------------------------------------------------------------------------- schools and other entities
def parse_school(key: str, rec: dict) -> dict:
    L = lines_of(rec)
    i = 1
    name, city = L[1], L[2]
    sec = sections(L, SCHOOL_LABELS, 3)
    street, scity, zp = split_street(sec.get("Address", []))
    m, mcity, mzip = split_street(sec.get("Mailing Address", []))
    prin = next((x.split(":", 1)[1].strip() for x in sec.get("Administration", []) if x.startswith("Principal:")), None)
    enr = next((x.split(":", 1)[1].strip() for x in sec.get("Demographics", []) if x.startswith("Registered Students")), None)
    grades = " ".join(sec.get("Grades", [])) or None
    em = " ".join(sec.get("Email", [])) or None
    g = (grades or "").lower()
    if re.search(r"9|12", g) and not re.search(r"preschool|tk|k\b|kindergarten|1st|8th", g):
        sub = "high_school"
    elif re.fullmatch(r"\s*(pre-?school|pre-?k|tk)[^-–]*", g):
        sub = "preschool"
    else:
        sub = "elementary_middle"
    return {"type": "school", "subtype": sub, "name": name, "entity_number": int(" ".join(sec.get("Entity Number", [])) or 0) or None, "grades": grades,
            "address": street, "city": scity or city, "zip": zp, "county": None, "lat": None, "lng": None,
            "mailing_address": {"address": m, "city": mcity, "zip": mzip} if m else None,
            "phone": C.norm_phone(" ".join(sec.get("Phone", []))), "fax": C.norm_phone(" ".join(sec.get("Fax", []))),
            "website": website_href(rec), "office_email": C.generic_email(em), "principal": prin,
            "enrollment": int(enr.replace(",", "")) if enr and enr.replace(",", "").isdigit() else None,
            "enrollment_note": "'Registered Students' as published on the school page (page fetched on the date below); not an audited figure.",
            "office_hours": " ".join(sec.get("Office Hours", [])) or None,
            "source_url": rec.get("final_url") or rec["url"], "fetched_at": rec["fetched_at"]}


STREET_NOISE = {"s", "n", "e", "w", "south", "north", "east", "west", "st", "street", "ave", "avenue", "rd", "road", "dr", "drive", "blvd", "boulevard"}


def find_host(parishes, address, city):
    """Finder row at the same place: same city, same house number, and a shared street word (South/S, Ave./Avenue ignored)."""
    if not address:
        return None
    num = re.match(r"\s*(\d+)", address)
    words = set(re.findall(r"[a-z]+", address.lower())) - STREET_NOISE
    for p in parishes:
        a = p.get("address") or ""
        n2 = re.match(r"\s*(\d+)", a)
        if num and n2 and num.group(1) == n2.group(1) and (p.get("city") or "").lower() == (city or "").lower() \
                and words & (set(re.findall(r"[a-z]+", a.lower())) - STREET_NOISE):
            return p
    return None


def clean_mojibake(s: str) -> str:
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def religious_entities(fx):
    ents, errs = [], []
    for kind, fn in (("men", "menreligious.php"), ("women", "womenreligious.php")):
        url = f"https://sites.sbdcatholics.org/clergyreligious/{fn}"
        try:
            r = fx.get(url)
        except C.Blocked as e:
            errs.append(str(e))
            continue
        for td in re.findall(r"<td>(.*?)</td>", r.text, flags=re.S):
            nm = re.sub(r"\s+", " ", clean_mojibake(BeautifulSoup(td, "html.parser").get_text(" ", strip=True)))
            ents.append({"type": "religious_community", "subtype": kind, "name": nm, "address": None, "city": None, "zip": None, "website": None,
                         "phone": None, "office_email": None,
                         "note": f"Name only, from the diocese's 'Religious Communities for {'Men' if kind == 'men' else 'Women'}' list (a communities list; the page gives no addresses).",
                         "source_url": url, "page_url": f"{BASE}/about-the-diocese-of-san-bernardino/clergy-and-religious/", "fetched_at": r.fetched_at})
    return ents, errs


def charities_entity(fx):
    try:
        r = fx.get("https://ccsbriv.org/")
    except C.Blocked as e:
        return None, str(e)
    d = None
    for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, flags=re.S):
        try:
            j = json.loads(blob)
        except ValueError:
            continue
        if j.get("@type") == "LocalBusiness":
            d = j
    if not d:
        return None, "no LocalBusiness block"
    a = d["address"]
    return {"type": "catholic_charities", "name": "Catholic Charities San Bernardino & Riverside Counties", "address": a["streetAddress"], "city": a["addressLocality"],
            "zip": a["postalCode"], "county": None, "lat": None, "lng": None, "website": "https://ccsbriv.org", "phone": C.norm_phone(d.get("telephone")),
            "office_email": C.generic_email(d.get("email")), "areas_served": [x["name"] for x in d.get("areaServed", [])],
            "hours": "Mon-Fri 08:30-17:00 (site's structured data)",
            "note": "From the agency's own site (JSON-LD on the home page). The diocese's pages fetched here describe the Pastoral Center as housing all departments 'except for Catholic Charities' but do not link to it.",
            "source_url": "https://ccsbriv.org/", "fetched_at": r.fetched_at}, None


def other_entities(bundle):
    fac = bundle["sbm_facilities"]
    con = bundle["sbm_contact"]
    fu, ft = fac.get("final_url") or fac["url"], fac["fetched_at"]
    cu, ct = con.get("final_url") or con["url"], con["fetched_at"]
    ents = [
        {"type": "diocesan_office", "name": "Diocesan Pastoral Center", "address": "1201 E. Highland Ave.", "city": "San Bernardino", "zip": "92404", "county": None,
         "lat": None, "lng": None, "website": BASE, "phone": "(909) 475-5300", "office_email": C.generic_email("info@sbdiocese.org"),
         "hours": "Mon-Fri 8:30 AM - 4:30 PM", "note": "Houses the Bishop's office and the diocesan ministries; the Facilities page describes a 55,000 sq ft building opened in 1995.",
         "source_url": cu, "fetched_at": ct},
        {"type": "other", "subtype": "hospital", "name": "St. Bernardine Medical Center", "address": "2101 N. Waterman Ave.", "city": "San Bernardino", "zip": "92404", "county": None,
         "lat": None, "lng": None, "website": None, "phone": "(909) 883-8711", "office_email": None, "sponsor": "Sisters of Charity of the Incarnate Word (founded by, per the diocese)",
         "note": "Catholic hospital listed on the diocese's Facilities page (administrator names there are not copied).", "source_url": fu, "fetched_at": ft},
        {"type": "other", "subtype": "hospital", "name": "St. Mary Regional Medical Center", "address": "18300 Highway 18", "city": "Apple Valley", "zip": "92307", "county": None,
         "lat": None, "lng": None, "website": None, "phone": "(760) 242-2311", "office_email": None, "sponsor": "Sisters of St. Joseph of Orange (operated by, per the diocese)",
         "note": "Catholic hospital listed on the diocese's Facilities page (administrator names there are not copied).", "source_url": fu, "fetched_at": ft},
        {"type": "cemetery", "name": "Our Lady Queen of Peace Catholic Cemetery", "address": "3510 E. Washington St.", "city": "Colton", "zip": None, "county": None, "lat": None, "lng": None,
         "website": None, "phone": None, "office_email": None, "note": "Opened 2005 on 30 acres; the Facilities page calls it the Inland Empire's first consecrated Catholic burial site.",
         "source_url": fu, "fetched_at": ft},
        {"type": "campus_ministry", "name": "Bishop Barnes Newman Center at California State University, San Bernardino", "organization": "California State University, San Bernardino",
         "address": None, "city": "San Bernardino", "zip": None, "county": None, "lat": None, "lng": None, "website": None, "phone": None, "office_email": None,
         "note": "About a mile from the CSUSB campus; the Facilities page gives no street address.", "source_url": fu, "fetched_at": ft},
        {"type": "retreat_center", "name": "Benedict House", "address": None, "city": "Grand Terrace", "zip": None, "county": None, "lat": None, "lng": None, "website": None,
         "phone": None, "office_email": None, "note": "Retreat and renewal house with a monastery chapel; the Facilities page gives no street address.", "source_url": fu, "fetched_at": ft},
        {"type": "seminary", "name": "St. Junipero Serra House of Formation", "address": None, "city": "Grand Terrace", "zip": None, "county": None, "lat": None, "lng": None,
         "website": None, "phone": None, "office_email": None,
         "note": "House of formation for diocesan seminarians on a 1.5-acre campus opened in 2005; the page's introduction calls it 'Blessed Junipero Serra House of Formation'. No street address is given.",
         "source_url": fu, "fetched_at": ft},
    ]
    return ents


# ---------------------------------------------------------------------------------------------- structure
def clergy_leaders(rec):
    L = lines_of(rec)
    ep = {}
    vf = {}
    for j, x in enumerate(L):
        if x.endswith(", E.V.") and j + 1 < len(L):
            ep[L[j + 1]] = x
        if x.endswith(", V.F.") and j + 1 < len(L):
            vf[L[j + 1].replace(" Vicariate", "")] = x
    return ep, vf


def build_structure(bundle, parishes, root, cards, diag):
    clergy = bundle["sbm_clergy"]
    about = bundle["sbm_about"]
    ep, vf = clergy_leaders(clergy)
    about_txt = "\n".join(lines_of(about))
    q1 = root["description"]
    q2 = re.search(r"covering [\d,]+ square miles in San Bernardino and Riverside counties", about_txt)
    members = defaultdict(list)
    ids = defaultdict(list)
    names = Counter(p["name"] for p in parishes)
    for p in parishes:
        if p.get("vicariate"):
            lab = p["name"] if names[p["name"]] == 1 else f"{p['name']} ({p['city']})"
            members[p["vicariate"]].append(lab)
            ids[p["vicariate"]].append(p["id"])
    # cross-check each Vicar Forane against his own parish page
    vcheck = []
    for v, nm in vf.items():
        sur = re.sub(r"[,]?\s*V\.F\.|Very Rev\.|\s*E\.V\.", "", nm).split()[-1]
        mine = [p for p in parishes if any(sur in (c["name"]) and c["title"] in ("Pastor", "Administrator") for c in p["clergy"])]
        vcheck.append((v, nm, [(p["name"], p["vicariate"]) for p in mine]))
    units = []
    for v in VICARIATES:
        vn = v + " Vicariate"
        units.append({"kind": "vicariate", "name": vn, "parent": None,
                      "leader": {"name": vf[v].replace(", V.F.", ""), "title": "Vicar Forane (V.F.)"} if v in vf else None,
                      "member_parishes": sorted(members[vn]), "member_parish_ids": sorted(ids[vn]),
                      "counties_of_members": sorted({p["county"] for p in parishes if p.get("vicariate") == vn and p.get("county")}),
                      "source_url": f"{BASE}/about-the-diocese-of-san-bernardino/clergy-and-religious/",
                      "membership_source": "each parish page's vicariate line (agrees with the finder's cards)"})
    for region, who in ep.items():
        units.append({"kind": "region", "name": region, "parent": None, "leader": {"name": who.replace(", E.V.", ""), "title": "Episcopal Vicar (E.V.)"},
                      "member_parishes": [], "member_vicariates": None,
                      "note": "The About page calls the Episcopal Vicars 'delegates of Bishop Rojas to Riverside and San Bernardino counties, respectively'; no page lists which vicariates belong to which region, so none is assigned here.",
                      "source_url": f"{BASE}/about-the-diocese-of-san-bernardino/clergy-and-religious/"})
    cathedral = next(p for p in parishes if p["type"] == "cathedral")
    bishops = [
        {"name": "Most Rev. Alberto Rojas", "title": "Bishop of San Bernardino (Coadjutor appointed 2019-12-02; Ordinary since 2020-12-28)", "status": "ordinary", "source_section": "FAQ: What is the leadership structure of the Diocese of San Bernardino?",
         "source_url": f"{BASE}/about-the-diocese-of-san-bernardino/"},
        {"name": "Most Rev. Gerald R. Barnes", "title": "Bishop Emeritus; second Bishop of San Bernardino, retired December 2020", "status": "emeritus", "source_section": "FAQ: What is the leadership structure of the Diocese of San Bernardino?",
         "source_url": f"{BASE}/about-the-diocese-of-san-bernardino/"},
        {"name": "Most Rev. Rutilio del Riego", "title": "Auxiliary Bishop Emeritus; appointed 2005, retired December 2015", "status": "emeritus", "source_section": "FAQ: What is the leadership structure of the Diocese of San Bernardino?",
         "source_url": f"{BASE}/about-the-diocese-of-san-bernardino/"},
    ]
    officers = [
        {"name": "Very Rev. Gerard Lopez", "title": "Vicar General"},
        {"name": "Sister Leticia Salazar, O.D.N.", "title": "Chancellor"},
        {"name": "John Andrews", "title": "Vice Chancellor"},
    ]
    for o in officers:
        assert o["name"].split(",")[0].split()[-1] in about_txt, o
        o["source_url"] = f"{BASE}/about-the-diocese-of-san-bernardino/"
    deps = []
    types = {t["id"]: re.sub(r"&amp;", "&", t["name"]) for t in bundle["_lists"]["sb_mintype"]}
    for m in bundle["_lists"]["sb_ministries"]:
        deps.append({"name": re.sub(r"\s+", " ", re.sub(r"&amp;", "&", m["title"]["rendered"])).strip(), "category": types.get((m.get("ministry-type") or [None])[0]), "url": m["link"]})
    return {
        "diocese": SLUG, "name": "Diocese of San Bernardino", "website": BASE,
        "counties": ["San Bernardino", "Riverside"],
        "counties_source_url": f"{BASE}/about-the-diocese-of-san-bernardino/",
        "counties_source_says": f"About page FAQ: \"covering 27,293 square miles in San Bernardino and Riverside counties\"; site description (wp-json): \"{q1}\".",
        "counties_verified_from_diocese_site": bool(q2) and "San Bernardino and Riverside Counties" in q1,
        "bishops": bishops, "diocesan_officers": officers,
        "cathedral": {"name": "Our Lady of the Rosary Cathedral", "address": cathedral["address"], "city": cathedral["city"], "zip": cathedral["zip"],
                      "lat": cathedral["lat"], "lng": cathedral["lng"], "parish": cathedral["name"], "source_url": cathedral["page_url"]},
        "units": units,
        "departments": deps,
        "notes": [
            "The diocese has six vicariates (each led by a Vicar Forane) and two Episcopal Vicars (Riverside and San Bernardino); there are no deaneries.",
            f"Vicariate membership comes from each parish page and agrees with the finder's cards for all but {len(diag['card_mismatch'])} rows; "
            f"Vicar Forane names come from the Clergy & Religious page, where each name is followed by its vicariate.",
            f"Vicar Forane pairing checked against the parish pages where a man with that surname is pastor or administrator: consistent for {sum(1 for v, _n, ps in vcheck if any(vic == v + ' Vicariate' for _p, vic in ps))} of {len(vcheck)} vicariates (surname match only).",
            "The diocese's About page gives its founding as July 17, 1978 in the text and November 6, 1978 in the FAQ; both are quoted from the page.",
            "The Facilities page still says the Pastoral Center 'houses the office of Bishop Gerald Barnes'; the About FAQ and the Rojas page say Bishop Rojas became Ordinary in December 2020. Rojas is used.",
            "The About FAQ counts 93 parishes, 6 missions and 5 chapels (104); the Parishes and Missions finder lists 104 pages.",
            "The Clergy & Religious page also links priest and deacon ordination chronologies and necrologies; they were not collected.",
        ],
        "source_urls": [f"{BASE}/about-the-diocese-of-san-bernardino/", f"{BASE}/about-the-diocese-of-san-bernardino/clergy-and-religious/",
                        f"{BASE}/parishes/", f"{BASE}/bishops/bishop-alberto-rojas/", f"{BASE}/wp-json/"],
        "fetched_at": C.now_iso(),
        "_diag": {"vicar_forane_check": vcheck},
    }


# ---------------------------------------------------------------------------------------------- events
def build_events(raw):
    evs, n = [], 0
    for page in raw:
        for e in page["data"].get("events", []):
            n += 1
            v = e.get("venue") or {}
            if isinstance(v, list):
                v = v[0] if v else {}
            loc = ", ".join(x for x in [C.clean_text(v.get("venue")), C.clean_text(v.get("address")), C.clean_text(v.get("city"))] if x) or None
            evs.append({"title": C.clean_text(e.get("title")), "start": (e.get("start_date") or "").replace(" ", "T") or None,
                        "end": (e.get("end_date") or "").replace(" ", "T") or None, "all_day": bool(e.get("all_day")), "timezone": e.get("timezone"),
                        "location": loc, "url": e.get("url"), "categories": [C.clean_text(c.get("name")) for c in (e.get("categories") or [])],
                        "source_url": page["url"], "fetched_at": page["fetched_at"]})
    kept, dropped = C.screen_events(evs)
    return kept, n, dropped


# ---------------------------------------------------------------------------------------------- main
def main() -> None:
    fx = C.Fetcher(SLUG)
    bundle, lists, list_meta, events_raw, root = load()
    bundle["_lists"] = lists
    parishes, cards, diag = build_parishes(fx, bundle)
    east_rows, east_ents = eastern_rows(bundle["sbm_eastern"], parishes)
    allp = parishes + east_rows
    schools = []
    for key in sorted(k for k in bundle if k.startswith("sbs_")):
        schools.append(parse_school(key, bundle[key]))
    rel_ents, rel_errs = religious_entities(fx)
    cc, cc_err = charities_entity(fx)
    others = other_entities(bundle)
    ents = schools + others + east_ents + rel_ents + ([cc] if cc else [])
    # coordinates and counties
    g_p = geocode_rows(fx, allp)
    g_e = geocode_rows(fx, [e for e in ents if e.get("address") and e["type"] != "religious_community"])
    C.dedupe_ids(allp, "name")
    # host parish ids for communities that meet at a finder row
    byname = {p["name"]: p for p in parishes}
    for r in east_rows:
        if r.get("address_is_host_parish"):
            h = find_host(parishes, r["address"], r["city"])
            r["host_parish_id"] = h["id"] if h else None
    for e in east_ents:
        h = find_host(parishes, e["address"], e["city"])
        e["host_parish_id"] = h["id"] if h else None
    structure = build_structure(bundle, allp[: len(parishes)], root, cards, diag)
    # regions: only if every vicariate sits inside one county
    events, ev_raw, dropped = build_events(events_raw)
    for e in ents:
        e.pop("_east", None)
    fetched = C.now_iso()
    cnt = C.counts(allp, ents, events)
    et = Counter((e["type"], e.get("subtype")) for e in ents)
    nogeo = sum(1 for p in allp if p["lat"] is None)
    nogeo_e = [e["name"] for e in ents if e["type"] != "religious_community" and e.get("lat") is None]
    sdesc = structure.pop("_diag")
    enroll = sum(s["enrollment"] or 0 for s in schools)
    by_vic = Counter(p.get("vicariate") for p in allp)
    typ = Counter(p["type"] for p in parishes)
    print("cards:", len(cards), "mismatch:", diag["card_mismatch"], "no_card:", diag["no_card"])
    print("types:", typ, "east rows:", [(r["name"], r["type"]) for r in east_rows], "east ents:", [e["name"] for e in east_ents])
    print("geocoded:", g_p, "of", len(allp), "| entities geocoded:", g_e, "no coords:", nogeo, nogeo_e)
    print("vicar check:", sdesc["vicar_forane_check"])
    print("schools:", len(schools), Counter(s["subtype"] for s in schools), "enrollment sum:", enroll)
    print("entities:", sorted(et.items(), key=str))
    print("events:", len(events), "of", ev_raw, dict(dropped))
    print("rel errs:", rel_errs, "cc err:", cc_err)
    readme = make_readme(fetched, allp, parishes, east_rows, schools, ents, events, ev_raw, dropped, structure, g_p, nogeo, nogeo_e, enroll, typ, et,
                         by_vic, rel_errs, cc_err, diag, list_meta)
    done = C.write_all(SLUG, allp, structure, ents, events, readme)
    print(done)


def fmt(d):
    return ", ".join(f"{k if k is not None else 'none'} {v}" for k, v in sorted(dict(d).items(), key=lambda kv: (-kv[1], str(kv[0]))))


def make_readme(fetched, allp, parishes, east_rows, schools, ents, events, ev_raw, dropped, structure, g_p, nogeo, nogeo_e, enroll, typ, et, by_vic, rel_errs, cc_err, diag, list_meta):
    cn = Counter(p.get("county") for p in allp)
    langs = Counter(l for p in allp for l in p["languages"])
    reg = sum(1 for p in allp if p.get("registered_families") is not None)
    clergy_n = sum(len(p["clergy"]) for p in allp)
    pas = sum(1 for p in allp if p.get("pastor"))
    return f"""# Diocese of San Bernardino: official directory data

Collected {fetched[:10]} from https://www.sbdcatholics.org (Diocese of San Bernardino). Script: `ingest/california/directories/san-bernardino.py`.
Re-run: `python3 -I ingest/california/directories/san-bernardino.py` (reads the laptop crawl in `data/raw/california/dioceses/san-bernardino/laptop/`, gitignored, and fetches the live sources below).

## Access: what the sandbox could and could not do

- **sbdcatholics.org is behind Imperva/Incapsula.** From the sandbox, `/wp-json/` and HTML pages answer a JavaScript challenge. `robots.txt` is readable (it disallows `/wp-admin/` except `admin-ajax.php`; no Crawl-delay). The challenge was **not** worked around: no browser automation, no header changes.
- **One attempt from Matthew's laptop**, as the task rules allow (a Linux VM on his home connection, python3 `urllib` default User-Agent, 1.2 s between requests, `robots.txt` respected). It received ordinary 200 responses for the whole site, so nothing else was needed. Pages were trimmed of script, style, svg and navigation before staging. Each record carries `fetched_at` (2026-10-31) and `fetched_via`.
- **Live from the sandbox** (these hosts answer plain requests): `sites.sbdcatholics.org` (the two religious-communities lists, served as iframes of the Clergy & Religious page), `ccsbriv.org` (Catholic Charities), and the US Census Geocoder.

## Sources and method

- **Parishes, missions and chapels:** the WordPress REST list `wp/v2/sbd-parishes` (104 posts; slug = entity number) gave the page list, and each parish page `/sbd-parishes/<entity number>/` gave name, vicariate, address, mailing address, phone, office email, website link, office hours, Mass times, clergy staff, founding year (from the page's one-line description) and "Registered Families". The finder page `/parishes/` (name / city / vicariate cards) is used as a cross-check: {len(diag['card_mismatch'])} mismatches with the pages, {len(diag['no_card'])} pages without a card.
  Type is read from the name or from the page's "A chapel/mission of ..." line. Status is `active` for every finder row: the finder and the pages carry no closure or merger notice (that is an absence of notice, not a confirmation).
- **Eastern Catholic and other communities:** the page "Eastern Catholic & Other Catholic Communities" lists seven communities (St. Nicholas Byzantine, Fontana, is the one the Los Angeles directory PDF also lists for this diocese). Six are added to `parishes.json` ({len(east_rows)} rows, `rite` and `jurisdiction` as the page's headings give them); the Arab American Ministry names The Holy Name of Jesus (Redlands) and its address, so it is read as a ministry hosted there and goes to `entities.json`. Where a community's listed address is a finder row's address, the row says so (`address_is_host_parish`, `host_parish_id`).
- **Coordinates and counties:** no coordinates are published, so addresses were geocoded with the US Census Geocoder (`geographies/onelineaddress`, benchmark Public_AR_Current; `geo_source` shows the matched address): {g_p} of {len(allp)} parish rows placed; {nogeo} not placed (reservation names instead of street addresses, or no Census match). Where the geocoder could not give a county, it is inferred from rows with the same ZIP and labelled `county_source`.
- **Structure:** six vicariates with their Vicar Forane (Clergy & Religious page) and members (from the parish pages); two Episcopal Vicars (Riverside and San Bernardino). The two regions' member vicariates are not published and are not assigned. Bishops and diocesan officers come from the About page FAQ ("leadership structure"); Bishop Rojas's page confirms his appointment. The 48 diocesan ministries and departments (`wp/v2/sbd-ministries`, names and categories only) are in `structure.json` under `departments`.
- **Counties:** San Bernardino and Riverside, verified from the diocese's own site: the About FAQ ("covering 27,293 square miles in San Bernardino and Riverside counties") and the site description in `/wp-json/` ("Serving the Catholic Community of San Bernardino and Riverside Counties").
- **Schools:** the 27 school pages (`wp/v2/sbd-schools`, `/sbd-schools/<id>/`): name, grades, address, phone, website, principal (a public staff role; the principal's email is not stored), "Registered Students" (sum {enroll}; the About page says approximately 8,599). Subtype is read from the grade range.
- **Other entities:** Diocesan Pastoral Center (Contact page); two Catholic hospitals, the cemetery, the Bishop Barnes Newman Center (CSUSB), Benedict House and the Serra House of Formation (Facilities page; none but the hospitals and cemetery give a street address); {sum(1 for e in ents if e['type']=='religious_community')} religious communities (names only, from the two iframe lists on `sites.sbdcatholics.org`); Catholic Charities (JSON-LD on ccsbriv.org: main office address, phone, hours).
- **Events:** The Events Calendar REST API, today to +120 days: {len(events)} kept of {ev_raw} (privacy-screen drops: {fmt(dropped) or 'none'}). The shared screen dropped 'Diaconate Convocation' (flag `names_person`, which looks like a false positive on a public diocesan event); it is not kept here.
- **Clergy:** `clergy` lists the staff the parish page gives (pastor, administrator, parochial vicars, deacons, resident priest, rector) as public roles; `pastor` is the first pastor/administrator/rector. {clergy_n} entries across {pas} rows with a head.

## Counts

| Item | Count |
|---|---|
| parishes.json rows | {len(allp)} |
{C.md_table(dict(Counter(p['type'] for p in allp)))}| of which finder pages | {len(parishes)} |
| of which Eastern/other community rows | {len(east_rows)} |
| rite other than Latin | {sum(1 for p in allp if p['rite'] != 'Latin')} |
| with coordinates | {len(allp) - nogeo} |
| with a vicariate | {sum(1 for p in allp if p.get('vicariate'))} |
| counties (rows) | {fmt(cn)} |
| with a non-English Mass language listed | {sum(1 for p in allp if p['languages'])} |
| with Registered Families | {reg} |

The About FAQ counts 93 parishes, 6 missions and 5 chapels (104); the finder has 104 pages. By name and page text this module finds {fmt(typ)} among the finder pages.

Rows per vicariate: {fmt(by_vic)}

| entities.json | Count |
|---|---|
{C.md_table({f"{a}{'/' + b if b else ''}": n for (a, b), n in sorted(et.items(), key=str)})}
| events | {len(events)} |

Mass languages listed (non-English, from the Mass-time lines; rows per language): {fmt(langs)}

## Gaps and cautions

- Registered Families on each page is the diocese's own count with no stated date other than the page's generation stamp; it is institutional (not people) and is kept with that note. The Diocesan Development Fund campaign figures printed on parish pages (goal, donors, pledges, gifts) were **not** collected.
- Parish histories, bulletins and photos on the pages were not copied.
- The diocese has no deaneries, so `deanery` is empty; `region` is empty because the two regions' vicariates are not published.
- Not placed on the map (no street address, or no Census match): {', '.join(nogeo_e) or 'none'}. Parish rows without coordinates: {nogeo} (five are Indian-reservation chapels whose address is the reservation name; the rest had no Census match).
- Hospital administrator names on the Facilities page look out of date and are not copied. That page also still names Bishop Barnes at the Pastoral Center, which the About page contradicts.
- The 27 schools' "Registered Students" add up to {enroll}; the About page says "approximately 8,599" students. They are not reconciled (different dates or definitions are likely).
- Priest and deacon ordination chronologies, necrologies and the Catholic Schools office pages were not collected.
- Catholic Charities (ccsbriv.org) was not linked from the diocese's pages fetched here; the entity is from the agency's own site. {('Not collected: ' + cc_err) if cc_err else ''}
- Religious-community lists: {('errors ' + str(rel_errs)) if rel_errs else 'collected for men and women'}.
"""


if __name__ == "__main__":
    main()
