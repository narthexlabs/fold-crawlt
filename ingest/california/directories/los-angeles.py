"""Archdiocese of Los Angeles (lacatholics.org, WordPress): parishes, pastoral regions/deaneries, entities, events.

Sources (all public, robots.txt allowed; /wp-admin/ is the only Disallow):
  * 2025 Archdiocese of Los Angeles Catholic Directory (official PDF on lacatholics.org, rev. 2025-07-08): the parish
    listing by city (name, Region/Deanery code, address, phone, website, office e-mail, pastor), parochial missions,
    Eastern Catholic churches, Directory of Ethnic Liturgies (Mass languages), regional deans, and the schools,
    cemeteries, retreat centers, formation houses, hospitals, campus ministry and Catholic Charities sections.
  * /wp-json/tribe/events/v1/venues (The Events Calendar): lat/lng, website and phone for ~720 venues (parishes, schools...).
  * /wp-json/tribe/events/v1/events: the public diocesan calendar (today..+120 days).
  * /<region>-pastoral-region/ pages: regional bishop and area (counties).

The parish "finder" on /find/ posts to admin-ajax.php with a nonce; it is not needed (the PDF and the venue REST API
carry the same fields) so it is not used.

    python -I ingest/california/directories/los-angeles.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import la_pdf as P  # noqa: E402

SLUG = "los-angeles"
BASE = "https://lacatholics.org"
PDF_URL = f"{BASE}/wp-content/uploads/2025/07/2025_ArchdioceseOfLA_Catholic_Directory-REV7.8.25.pdf"
REGION_SLUG = {"Our Lady of the Angels": "our-lady-of-the-angels-pastoral-region", "San Fernando": "san-fernando-pastoral-region",
               "San Gabriel": "san-gabriel-pastoral-region", "San Pedro": "san-pedro-pastoral-region",
               "Santa Barbara": "santa-barbara-pastoral-region"}
DEANERY_REGION = {**{d: "Santa Barbara" for d in (1, 2, 3, 4)}, **{d: "San Fernando" for d in (5, 6, 7, 8)},
                  **{d: "San Gabriel" for d in (9, 10, 11, 12)}, **{d: "Our Lady of the Angels" for d in (13, 14, 15, 16)},
                  **{d: "San Pedro" for d in (17, 18, 19, 20)}}
COUNTY_BY_CITY_HINT = {"Ventura": ["Oxnard", "Ventura", "Camarillo", "Moorpark", "Simi Valley", "Thousand Oaks", "Westlake Village",
                                   "Newbury Park", "Fillmore", "Santa Paula", "Ojai", "Piru"]}


# ---------------------------------------------------------------------------------------------- fetch helpers
def fetch_binary(fx: C.Fetcher, url: str, name: str) -> Path:
    """Download a binary once (shared robots/throttle logic), cache under data/raw."""
    path = fx.raw / name
    if path.exists() and path.stat().st_size > 1000:
        return path
    host = urlparse(url).netloc
    rp, why = fx._robots_for(url)
    if why or (rp is not None and not rp.can_fetch("*", url)):
        raise C.Blocked(url, why or "disallowed by robots.txt")
    fx._wait(host, fx.crawl_delay(url))
    r = fx.session.get(url, timeout=180)
    fx._stamp(host)
    fx.requests_made += 1
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise C.Blocked(url, f"HTTP {r.status_code}", r.status_code)
    path.write_bytes(r.content)
    fx.log.append({"url": url, "status": 200, "blocked": None, "at": C.now_iso()})
    return path


def venues(fx: C.Fetcher) -> list[dict]:
    out, page = [], 1
    while page <= 30:
        d, r = fx.get_json(f"{BASE}/wp-json/tribe/events/v1/venues", {"per_page": 50, "page": page})
        if not isinstance(d, dict) or not d.get("venues"):
            break
        out += d["venues"]
        if page >= int(d.get("total_pages") or 1):
            break
        page += 1
    for v in out:
        v["_name"] = C.clean_text(v.get("venue") or v.get("slug"))
    return out


# ---------------------------------------------------------------------------------------------- parishes
def street_of(addr: str | None, city_hint: str | None):
    if not addr:
        return None, None, None
    # "2201 Laguna St. Santa Barbara CA 93105 | Parish Office: (805) 682-4151": the address is the part before "|"
    d = re.split(r"\s*\|\s*", re.sub(r"\s+", " ", addr).strip())[0]
    zp = C.zip5(d)
    rest = re.sub(r",?\s*(CA|California)\.?,?\s*9\d{4}(-\d{4})?\s*$", "", d).strip(" ,")
    if city_hint:
        m = re.search(r"[ ,]*" + re.escape(city_hint) + r"\s*$", rest, re.I)
        if m:
            return rest[:m.start()].strip(" ,"), city_hint, zp
    if "," in rest:
        s, c = rest.rsplit(",", 1)
        return s.strip(), c.strip(), zp
    # last resort: street ends at the last street-type word
    m = re.search(r"^(.*?\b(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Way|Pl|Place|Ln|Lane|Ct|Court|Hwy|Highway|Cir|Circle|Pkwy|Sq|Terrace|Ter)\.?)\s+(.+)$", rest)
    if m:
        return m.group(1).strip(), m.group(2).strip(), zp
    return rest, None, zp


def best_site(sites: list[str]) -> str | None:
    for s in sites:
        s = s.strip().strip("()")
        if re.search(r"\.(org|com|net|us|edu|church|catholic)\b|^www\.", s, re.I) and "@" not in s:
            return C.norm_url(s.split()[0])
    return None


def vkey(v: dict) -> tuple:
    return (C.norm_phone(v.get("phone")), re.sub(r"\D", "", (v.get("address") or "").split()[0] if v.get("address") else ""), v.get("zip"))


def index_venues(vs: list[dict]):
    by_phone, by_addr = defaultdict(list), defaultdict(list)
    for v in vs:
        ph = C.norm_phone(v.get("phone"))
        if ph:
            by_phone[ph].append(v)
        a = (v.get("address") or "").strip()
        num = re.match(r"^(\d+)", a)
        zp = C.zip5(v.get("zip"))
        if num and zp:
            by_addr[(num.group(1), zp)].append(v)
    return by_phone, by_addr


def parish_name_key(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", s or "").lower()
    s = re.sub(r"\b(catholic|church|parish|community|mission)\b", " ", s)
    s = re.sub(r"\bst\.?\b|\bsaint\b|\bsaints\b|\bsts\.?\b", "st", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def match_venue(rec: dict, idx) -> dict | None:
    by_phone, by_addr = idx
    nk = parish_name_key(rec["name"])
    cands = []
    for ph in rec.get("_phones", []):
        cands += by_phone.get(ph, [])
    m = re.match(r"^(\d+)", rec.get("address") or "")
    if m and rec.get("zip"):
        cands += by_addr.get((m.group(1), rec["zip"]), [])
    if not cands:
        return None
    seen, uniq = set(), []
    for v in cands:
        if v["id"] not in seen:
            seen.add(v["id"])
            uniq.append(v)
    # prefer one whose name resembles the parish name; else the sole candidate
    named = [v for v in uniq if nk and (nk in parish_name_key(v["_name"]) or parish_name_key(v["_name"]) in nk)]
    if len(named) >= 1:
        return named[0]
    return uniq[0] if len(uniq) == 1 else None


def lead_to_pastor(lead: list[tuple[str, str]]):
    for title, val in lead:
        m = re.match(r"^(.*?)(?:\s*\(.*)?$", val)
        c = re.sub(r"\s+", " ", val).strip()
        c = re.sub(r"\(?[\w.+%-]+@[\w.-]+\)?", "", c).strip(" ,;()")
        if re.match(r"^(Fr\.?|Father|Msgr\.?|Monsignor|Rev\.?|Very Rev\.?|Rt\. Rev\.?|Deacon|Dcn\.?|Sr\.?|Sister|Br\.?|Brother|Bishop)\b", c, re.I):
            c = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", c) if re.match(r"^Fr\. ?\w+[A-Z]", c) else c
            return C.pastor_record(c, title if title.lower() != "pastors" else "Pastor")
    return None


def build_parishes(fx, pdf: P.Pdf, vs: list[dict], pdf_src: str):
    idx = index_venues(vs)
    pages = list(range(53, 91))
    skip = None
    raw = P.parse_city_listing(pdf.lines(pages, skip=skip, bounds=[0, 301, 603]))
    recs = []
    fetched = C.now_iso()
    for blk in raw:
        f = P.parse_block(blk)
        name = P.title_case(f["name_raw"])
        main = f["addresses"].get("unlabeled") or f["addresses"].get("church & rectory") or f["addresses"].get("church") \
            or f["addresses"].get("church/rectory") or f["addresses"].get("rectory") or f["addresses"].get("office")
        city_hint = P.title_case(f["city_header"]) if f.get("city_header") else None
        if city_hint and "(" in city_hint:
            city_hint = re.sub(r"\s*\(.*$", "", city_hint)
        street, city, zp = street_of(main, city_hint)
        if not city and main:
            street, city, zp = street_of(main, None)
        county = f.get("county")
        phones = [C.norm_phone(x) for x in f["phones"] if C.norm_phone(x)]
        office = re.search(r"parish office:?\s*(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4})", main or "", re.I)
        if office and C.norm_phone(office.group(1)) not in phones:
            phones.insert(0, C.norm_phone(office.group(1)))   # the office phone printed after the address
        website = best_site(f["sites"])
        emails = []
        for e in f["emails"]:
            g = C.generic_email(e)
            if g and g not in emails:
                emails.append(g)
        # prefer a non-alias parish office address when present
        office_email = next((e for e in emails if not e.endswith("@la-archdiocese.org")), emails[0] if emails else None)
        rec = {
            "id": None, "name": name,
            "type": "cathedral" if re.match(r"^Cathedral of Our Lady", name) else "parish",
            "address": street, "city": city, "zip": zp, "county": county or ("Los Angeles" if city == "Los Angeles" else None),
            "lat": None, "lng": None,
            "phone": phones[0] if phones else None, "website": website, "office_email": office_email,
            "languages": [],
            "pastor": lead_to_pastor(f["leaders"]),
            "region": f"{f['region']} Pastoral Region", "region_name": f["region"],
            "deanery": f"Deanery {f['deanery']}", "deanery_number": f["deanery"], "vicariate": None,
            "status": "active", "status_note": None,
            "established": f.get("established"),
            "staffed_by": f.get("staffed_by"),
            "mailing_address": f["addresses"].get("mail"),
            "parochial_missions": f.get("parochial_missions"),
            "rite": "Latin",
            "source_url": f"{pdf_src}#page={f['pdf_page']}", "fetched_at": fetched,
            "_phones": phones,
        }
        if not rec["county"]:
            rec["county"] = "Los Angeles"
        v = match_venue(rec, idx)
        if v:
            rec["venue_url"] = v.get("url")
            if v.get("geo_lat") and v.get("geo_lng"):
                rec["lat"], rec["lng"] = round(float(v["geo_lat"]), 6), round(float(v["geo_lng"]), 6)
                rec["geo_source"] = "lacatholics.org tribe venue record"
            if not rec["website"] and v.get("website"):
                rec["website"] = C.norm_url(v["website"])
                rec["website_source"] = "lacatholics.org venue"
            rec["_venue_id"] = v["id"]
        recs.append(rec)
    return recs, raw


def finish_ids(recs):
    C.dedupe_ids(recs, "name")
    for r in recs:
        r.pop("_phones", None)
        r.pop("_venue_id", None)


# ---------------------------------------------------------------------------------------------- shared record helpers
SECTION_SKIP = re.compile(r"^(The 2025 Archdiocese|\d+ \| The 2025|\(Continued\)|Retreat Centers$|Catholic Cemeteries|Catholic High Schools|"
                          r"Catholic Universities|Religious Houses|Campus Ministry|Parochial Elementary|Non-Parochial|Pre-Schools|"
                          r"Catholic Health Services|Hospitals$|\(Alphabetical Listing\))")
COLS3 = [0, 211, 394, 603]


def section_lines(pdf: P.Pdf, pages) -> list[tuple[int, str]]:
    out = []
    for pn, ln in pdf.lines(list(pages), skip=SECTION_SKIP, bounds=COLS3):
        out.append((pn, "" if re.match(r"^_{5,}$", ln) else ln))
    return out


def contact_from_body(body: list[str]) -> dict:
    phones, web, emails = [], None, []
    cond = None
    for ln in body[:14]:
        for ph in P.PHONE_RE.findall(ln):
            phones.append(C.norm_phone(ph))
        for em in re.findall(r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}", ln):
            g = C.generic_email(em)
            if g and g not in emails:
                emails.append(g)
        for tok in re.split(r"[|\s]+", ln):
            if not web and re.match(r"^(https?://|www\.)\S+", tok, re.I) or (not web and re.match(r"^[\w-]+(\.[\w-]+)*\.(org|com|net|edu|us)(/\S*)?$", tok, re.I) and "@" not in ln.split(tok)[0][-1:]):
                web = tok
        m = re.match(r"^(Conducted by|Staffed by|Founded by|Sponsored by)\s*:?\s*(.*)$", ln)
        if m and not cond:
            cond = m.group(2)
    return {"phone": next((p for p in phones if p), None), "website": C.norm_url(web), "emails": emails, "conducted_by": cond}


def entity(type_: str, name: str, addr_line: str | None, body: list[str], page: int, src: str, fetched: str, subtype=None, city_hint=None, **extra):
    street, city, zp = street_of(re.sub(r"^(Office|Mail|Newman Club)\s*:?\s*,?\s*", "", addr_line or ""), city_hint)
    c = contact_from_body(body)
    rec = {"type": type_, "subtype": subtype, "name": C.clean_text(name), "address": street, "city": city, "zip": zp,
           "website": c["website"], "phone": c["phone"], "office_email": next(iter(c["emails"]), None),
           "conducted_by": C.clean_text(c["conducted_by"]), "source_url": f"{src}#page={page}", "fetched_at": fetched}
    rec.update({k: v for k, v in extra.items() if v is not None})
    return rec


def clean_entity_name(lines: list[str]) -> str:
    n = " ".join(lines)
    n = re.sub(r"\s*\((?:Co-ed|Girls|Boys)\)\s*$", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return P.title_case(n) if n.upper() == n or re.match(r"^[A-Z][A-Z .’'&-]{3,}\b", n) else n


def loose_name(lines, entry) -> list[str]:
    return entry["name_lines"]


# ---------------------------------------------------------------------------------------------- entities
def venue_by_phone(vs):
    d = defaultdict(list)
    for v in vs:
        ph = C.norm_phone(v.get("phone"))
        if ph:
            d[ph].append(v)
    return d


def school_level(name: str, sect: str) -> str:
    return {"hs": "high_school", "elem": "elementary_middle", "nonpar": "non_parochial_elementary_middle"}[sect]


def build_entities(pdf: P.Pdf, vs: list[dict], parishes: list[dict], src: str):
    fetched = C.now_iso()
    vph = venue_by_phone(vs)
    cities = {re.sub(r"[^a-z]", "", (p["city"] or "").lower()) for p in parishes}
    ents, stats = [], defaultdict(Counter)

    def add(rec, section, ok=True):
        stats[section]["parsed" if ok else "dropped_no_name"] += 1
        if ok:
            ents.append(rec)

    # --- schools: high schools, parochial + non-parochial elementary/middle
    for sect, pages in (("hs", range(168, 173)), ("elem", range(173, 190)), ("nonpar", [190])):
        lines = section_lines(pdf, pages)
        if sect == "elem":
            raw = P.elementary_entries(lines)
            items = [{"name_lines": e["name_lines"], "addr": next((b for b in e["body"] if P.ADDR_RE.search(b)), None), "body": e["body"], "pdf_page": e["pdf_page"],
                      "city_header": e["city_header"]} for e in raw]
            for it in items:   # street on one line, 'City, CA zip' on the next
                if it["addr"] is None:
                    for k, b in enumerate(it["body"]):
                        if re.match(r"^\d", b) and k + 1 < len(it["body"]) and P.ADDR_RE.search(it["body"][k + 1]):
                            it["addr"] = b + " " + it["body"][k + 1]
                            break
                elif not re.match(r"^\d", it["addr"]):
                    k = it["body"].index(it["addr"])
                    if k and re.match(r"^\d", it["body"][k - 1]):
                        it["addr"] = it["body"][k - 1] + " " + it["addr"]
        else:
            items = P.entries_by_address(lines, cities)
        for it in items:
            name = clean_entity_name(it["name_lines"]) if it["name_lines"] else None
            c = contact_from_body(it["body"])
            if not name and c["phone"] and c["phone"] in vph:
                name = vph[c["phone"]][0]["_name"]
                name_src = "venue"
            else:
                name_src = "pdf"
            if not name or not it["addr"]:
                add(None, sect, False)
                continue
            body = it["body"]
            grades = next((re.search(r"\((.*?)\)", b).group(1) for b in body if b.startswith("Enrollment:") and re.search(r"\((.*?)\)", b)), None)
            enroll = next((int(re.search(r"Enrollment:\s*([\d,]+)", b).group(1).replace(",", "")) for b in body if b.startswith("Enrollment:") and re.search(r"Enrollment:\s*([\d,]+)", b)), None)
            reg = next((b.split(":", 1)[1].strip() for b in body if b.startswith("Region:")), None)
            rec = entity("school", name, it["addr"], body, it["pdf_page"], src, fetched, subtype=school_level(name, sect),
                         city_hint=P.title_case(it["city_header"]) if it.get("city_header") else None,
                         grades=grades, enrollment_listed=enroll, region=(reg + " Pastoral Region") if reg else None)
            if name_src == "venue":
                rec["name_source"] = "lacatholics.org venue (name missing in parsed PDF block)"
            add(rec, sect)

    # --- cemeteries (Catholic Cemeteries & Mortuaries)
    for it in P.entries_by_address(section_lines(pdf, [202, 203]), cities):
        nm = clean_entity_name(it["name_lines"]) if it["name_lines"] else ""
        if not re.search(r"cemetery|mausoleum|mortuary", nm, re.I):
            continue
        add(entity("cemetery", nm, it["addr"], it["body"], it["pdf_page"], src, fetched, city_hint=P.title_case(it["city_header"]) if it["city_header"] else None), "cemetery")

    # --- retreat centers
    for it in P.entries_by_address(section_lines(pdf, [216, 217, 218]), cities):
        nm = clean_entity_name(it["name_lines"]) if it["name_lines"] else None
        if not nm:
            add(None, "retreat", False)
            continue
        add(entity("retreat_center", nm, it["addr"], it["body"], it["pdf_page"], src, fetched, city_hint=P.title_case(it["city_header"]) if it["city_header"] else None), "retreat")

    # --- hospitals (Catholic Health Services)
    for it in P.entries_by_address(section_lines(pdf, [204]), cities):
        nm = clean_entity_name([x for x in it["name_lines"] if x not in ("DIGNITY HEALTH",)]) if it["name_lines"] else None
        if not nm or not re.search(r"hospital|medical|health|center", nm, re.I):
            add(None, "hospital", False)
            continue
        add(entity("other", nm, it["addr"], it["body"], it["pdf_page"], src, fetched, subtype="hospital",
                   city_hint=P.title_case(it["city_header"]) if it["city_header"] else None), "hospital")

    # --- religious houses of formation / seminaries (organisation heading, house name, address)
    def heading_section(pages, skip_re):
        sk = re.compile(skip_re)
        lines = []
        for pn, ln in pdf.lines(list(pages), skip=sk, bounds=COLS3):
            lines.append((pn, "" if re.match(r"^_{5,}$", ln) else ln))
        return P.heading_entries(lines)

    def heading_record(e):
        org = P.title_case(" ".join(e["org_lines"]))
        mixed = [m for m in e["mixed"] if not re.match(r"^(Religious|The 2025|Formation|Associate Directors|Director,|Pre-Novitiate)", m) and not P.PHONE_RE.search(m)]
        addr = e["addr"]
        street_idx = [k for k, m in enumerate(mixed) if re.match(r"^\d", m) or re.search(r",\s*\d+ ", m)]
        if street_idx and not re.match(r"^\d", addr):
            k = street_idx[-1]
            st = mixed.pop(k)
            addr = re.sub(r"^.*?,\s*(?=\d)", "", st) + " " + addr
            mixed = [m for m in mixed if not re.match(r"^(Newman Club)", m)]
        names = [m for m in mixed if not m.endswith(":")]
        return org, names, addr

    for e in heading_section([195, 196], r"^(The 2025 Archdiocese|\d+ \| The 2025|\(Continued\)|Religious Houses)"):
        org, names, addr = heading_record(e)
        if org.startswith(("Queen Of Angels", "Piarist")) or not names and org in ("For Priestly Formation",):
            continue   # column bleed on this block; the two houses are added by hand below
        nm = P.title_case(" ".join(names)) if names and not names[0].startswith("Hospitaller Order") else (names[-1] if names else org)
        nm = re.sub(r"\s+", " ", names[-1] if names and re.search(r"seminary|house|novitiate|priory", names[-1], re.I) else (names[0] if names else org))
        is_sem = bool(re.search(r"seminary|priestly formation", nm, re.I))
        rec = entity("seminary" if is_sem else "religious_community", nm if len(nm) > 3 else org, addr, e["body"], e["pdf_page"], src, fetched,
                     subtype=None if is_sem else "house_of_formation", organization=org)
        add(rec, "formation")
    for nm, org, street, city, zp, ph, web in (
            ("Queen of Angels Center for Priestly Formation", "Archdiocese of Los Angeles", "5434 Torrance Blvd.", "Torrance", "90503", "(310) 516-6671", "http://www.qofacenter.org"),
            ("Casa Calasanz (House of Formation and Vocation Center of the Piarist Fathers)", "Piarist Fathers", "3951 Rogers St.", "Los Angeles", "90063", "(323) 269-0883", "http://www.epiacalifornias.org")):
        add({"type": "seminary" if "Queen" in nm else "religious_community", "subtype": None if "Queen" in nm else "house_of_formation", "name": nm,
             "organization": org, "address": street, "city": city, "zip": zp, "website": web, "phone": ph, "office_email": None, "conducted_by": None,
             "source_url": f"{src}#page=195", "fetched_at": fetched}, "formation")
    ents[:] = [e for e in ents if not re.match(r"^\d", e.get("name") or "")]
    ents.append({"type": "seminary", "subtype": "archdiocesan_seminary", "name": "St. John's Seminary", "address": "5012 Seminary Rd.", "city": "Camarillo",
                 "zip": "93012", "website": "https://www.stjohnsem.edu", "phone": "(805) 482-2755", "office_email": None, "conducted_by": None,
                 "source_url": f"{src}#page=194", "fetched_at": fetched})

    # --- campus ministry / Newman
    for e in heading_section([220], r"^(The 2025 Archdiocese|\d+ \| The 2025|\(Continued\)|Campus$|Ministry / Newman|Directory$|Centers$|Campus Ministry)"):
        org, names, addr = heading_record(e)
        center = names[0] if names else None
        nm = f"{org} - {center}" if center and center.lower() not in org.lower() else org
        add(entity("campus_ministry", nm, addr, e["body"], e["pdf_page"], src, fetched, organization=org), "campus")

    # --- Catholic Charities of Los Angeles (agency)
    ents.append({"type": "catholic_charities", "subtype": "agency", "name": "Catholic Charities of Los Angeles, Inc.", "address": "1531 James M. Wood Blvd.",
                 "city": "Los Angeles", "zip": "90015", "website": "https://catholiccharitiesla.org", "phone": None, "office_email": None, "conducted_by": None,
                 "note": "Central Administration Office as printed in the directory; hours Mon-Fri 8:30 am-5 pm. Program sites are listed in the same section "
                         "(three-column layout with staff names), not extracted here.",
                 "source_url": f"{src}#page=203", "fetched_at": fetched})

    # --- Archdiocesan offices (names + the main switchboard only)
    ents.append({"type": "diocesan_office", "subtype": "chancery", "name": "Archdiocesan Catholic Center (ACC)", "address": "3424 Wilshire Blvd.",
                 "city": "Los Angeles", "zip": "90010", "website": "https://lacatholics.org", "phone": "(213) 637-7000", "office_email": None,
                 "conducted_by": None, "source_url": f"{src}#page=7", "fetched_at": fetched})
    return ents, stats


# ---------------------------------------------------------------------------------------------- missions, Eastern churches, languages, geocoding
LANG_MAP = {"Spanish": "Spanish", "Filipino (Tagalog)": "Tagalog", "Filipino": "Filipino", "Korean": "Korean", "Chinese (Cantonese)": "Cantonese",
            "Chinese (Mandarin)": "Mandarin", "Italian": "Italian", "Samoan": "Samoan", "Tongan": "Tongan", "Cambodian": "Khmer", "Croatian": "Croatian",
            "Polish": "Polish", "American Sign Language": "Sign Language", "French": "French", "Guamanian (Chamorro)": "Chamorro", "Hebrew": "Hebrew",
            "Hungarian": "Hungarian", "Igbo (Nigerian)": "Igbo", "Indonesian": "Indonesian", "Japanese": "Japanese", "Lithuanian": "Lithuanian",
            "Portuguese": "Portuguese", "Thai": "Thai", "Vietnamese": "Vietnamese", "German": "German", "Fijian": "Fijian", "Maya": "Mayan",
            "Mayan": "Mayan", "Kidane-Mihret (Geez Rite)": "Geez", "Eritrean Geez Rite": "Geez"}


def attach_languages(parishes: list[dict], eth: list[dict]) -> tuple[int, int]:
    by_phone = defaultdict(list)
    by_name = defaultdict(list)
    for p in parishes:
        for ph in {p.get("phone")} | set(p.get("_phones", [])):
            if ph:
                by_phone[ph].append(p)
        by_name[(parish_name_key(p["name"]), C.zip5(p.get("zip")) and re.sub(r"[^a-z]", "", (p.get("city") or "").lower()))].append(p)
    matched = unmatched = 0
    for e in eth:
        hit = by_phone.get(e["phone"], [])
        if not hit:
            nk = parish_name_key(e["name"])
            ck = re.sub(r"[^a-z]", "", (e.get("city") or "").lower())
            hit = [p for p in parishes if nk and parish_name_key(p["name"]) == nk and re.sub(r"[^a-z]", "", (p.get("city") or "").lower()) == ck]
        if len(hit) != 1:
            unmatched += 1
            continue
        matched += 1
        p = hit[0]
        lab = e["language"]
        p.setdefault("ethnic_liturgies", [])
        if lab not in p["ethnic_liturgies"]:
            p["ethnic_liturgies"].append(lab)
        if lab in LANG_MAP and LANG_MAP[lab] not in p["languages"]:
            p["languages"].append(LANG_MAP[lab])
    return matched, unmatched


def geocode(fx: C.Fetcher, street, city, zp):
    if not (street and city):
        return None
    q = f"{street}, {city}, CA {zp or ''}".strip()
    try:
        d, r = fx.get_json("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                           {"address": q, "benchmark": "Public_AR_Current", "format": "json"})
    except C.Blocked:
        return None
    m = ((d or {}).get("result") or {}).get("addressMatches") or []
    if not m:
        return None
    return {"lat": round(m[0]["coordinates"]["y"], 6), "lng": round(m[0]["coordinates"]["x"], 6), "matched": m[0]["matchedAddress"], "url": r.url}


def extra_parish_records(fx, pdf, vs, parishes, pdf_src):
    """Eastern Catholic churches in the LA regions, and parochial missions/chapels/centers (p. 87-88 of the directory)."""
    idx = index_venues(vs)
    fetched = C.now_iso()
    out, other_dioceses = [], []
    for e in P.parse_eastern(pdf, [92, 93, 94]):
        main = e["addresses"].get("unlabeled")
        main = main.split("|")[0].strip() if main else None
        m = re.search(r"\(([^()]*)\)\s*$", e["name_raw"])
        name = P.title_case(e["name_raw"])
        city_hint = None
        mm = re.search(r"\(([A-Z][A-Za-z .]+)\)", e["name_raw"])
        street, city, zp = street_of(main, None)
        phones = [C.norm_phone(x) for x in e["phones"] if C.norm_phone(x)]
        rec = {"id": None, "name": name, "type": "mission" if re.search(r"\bMISSION\b", e["name_raw"]) else "personal_parish",
               "address": street, "city": city, "zip": zp, "county": None, "lat": None, "lng": None,
               "phone": phones[0] if phones else None, "website": best_site(e["sites"]),
               "office_email": next((g for g in (C.generic_email(x) for x in e["emails"]) if g), None),
               "languages": [], "pastor": lead_to_pastor(e["leaders"]),
               "status": "active", "status_note": None, "established": e.get("established"), "staffed_by": None,
               "rite": (e.get("rite") or "").title() + " (Eastern Catholic)", "eparchy_note": None,
               "source_url": f"{pdf_src}#page={e['pdf_page']}", "fetched_at": fetched, "_phones": phones}
        if e["region"]:
            rec.update(region=f"{e['region']} Pastoral Region", region_name=e["region"], deanery=f"Deanery {e['deanery']}", deanery_number=e["deanery"], vicariate=None)
            v = match_venue(rec, idx)
            if v and v.get("geo_lat"):
                rec["lat"], rec["lng"] = round(float(v["geo_lat"]), 6), round(float(v["geo_lng"]), 6)
                rec["geo_source"] = "lacatholics.org tribe venue record"
                rec["venue_url"] = v.get("url")
            out.append(rec)
        else:
            rec.update(region=None, region_name=None, deanery=None, deanery_number=None, vicariate=None,
                       listed_in_diocese=e["tail"].replace("Diocese of ", ""))
            other_dioceses.append(rec)
    # parochial missions, chapels, centers
    by_nk = {}
    for p in parishes:
        by_nk.setdefault(parish_name_key(p["name"]), []).append(p)
    for m in P.parse_missions(pdf, 91):
        parent_txt = m.get("parent") or ""
        parent_name = re.sub(r"\s*Church\b", "", parent_txt.split(",")[0]).strip()
        pcity = parent_txt.split(",")[1].strip() if "," in parent_txt else None
        parent_name = {"St. Odilla": "St. Odilia"}.get(parent_name, parent_name)
        pk = parish_name_key(parent_name)
        cands = by_nk.get(pk, []) or [x for k, v in by_nk.items() if pk and k.startswith(pk) for x in v]
        if pcity:
            cands = [c for c in cands if re.sub(r"[^a-z]", "", (c["city"] or "").lower()) == re.sub(r"[^a-z]", "", pcity.lower())] or cands
        if len(cands) > 1 and m.get("city_header"):
            near = [c for c in cands if re.sub(r"[^a-z]", "", (c["city"] or "").lower()) in re.sub(r"[^a-z]", "", m["city_header"].lower())]
            cands = near or cands
        parent = cands[0] if len(cands) >= 1 else None
        street, city, zp = street_of(m["address_line"], None)
        typ = "chapel" if re.search(r"\bChapel\b", m["name"]) else "mission"
        rec = {"id": None, "name": P.title_case(m["name"]) if m["name"].isupper() else m["name"], "type": typ, "address": street, "city": city, "zip": zp,
               "county": parent["county"] if parent else None, "lat": None, "lng": None, "phone": C.norm_phone(m.get("phone")), "website": None,
               "office_email": None, "languages": [], "pastor": None,
               "region": parent["region"] if parent else None, "region_name": parent["region_name"] if parent else None,
               "deanery": parent["deanery"] if parent else None, "deanery_number": parent["deanery_number"] if parent else None, "vicariate": None,
               "status": "active", "status_note": None, "parent_parish": parent["name"] if parent else parent_txt or None,
               "parent_parish_note": None if parent else f"parent '{parent_txt}' not matched to a listed parish",
               "rite": "Latin", "source_url": f"{pdf_src}#page={m['pdf_page']}", "fetched_at": fetched, "_phones": [C.norm_phone(m.get('phone'))]}
        if re.search(r"Korean (Center|Community|Apostolate)|Evangelization Center|Cambodian Center", m["name"]):
            rec["subtype"] = "ethnic_center"
        v = match_venue(rec, idx)
        if v and v.get("geo_lat"):
            rec["lat"], rec["lng"] = round(float(v["geo_lat"]), 6), round(float(v["geo_lng"]), 6)
            rec["geo_source"] = "lacatholics.org tribe venue record"
        out.append(rec)
    return out, other_dioceses


# ---------------------------------------------------------------------------------------------- structure
REGION_OFFICES = {
    "Santa Barbara": ("3240 Calle Pinon", "Santa Barbara", "93105", "(805) 682-0442", "Slawomir S. Szkredka"),
    "San Fernando": ("15101 San Fernando Mission Blvd.", "Mission Hills", "91345", "(818) 361-6009", "Albert M. Bahhuth"),
    "San Gabriel": ("16009 Cypress Ave.", "Irwindale", "91706", "(626) 960-9344", "Brian Nunes"),
    "Our Lady of the Angels": ("5835 W. Slauson Ave.", "Culver City", "90230", "(310) 215-0703", "Matthew G. Elshoff, O.F.M. Cap."),
    "San Pedro": ("3555 Saint Pancratius Pl.", "Lakewood", "90712", "(562) 634-0456", "Marc V. Trudeau"),
}
REGION_AREA = {"Santa Barbara": "Santa Barbara and Ventura Counties", "San Fernando": "San Fernando, Santa Clarita and Antelope Valleys",
               "San Gabriel": "East Los Angeles through San Gabriel and Pomona Valleys", "Our Lady of the Angels": "Downtown central Los Angeles, west to Malibu, south to LAX",
               "San Pedro": "Long Beach and Southern Los Angeles County"}


def deans(pdf: P.Pdf) -> dict[int, tuple[str, int]]:
    out: dict[int, tuple[str, int]] = {}
    for pn in range(30, 48):
        t = pdf.page_text(pn)
        if "Regional Deans" not in t and "Deanery" not in t:
            continue
        for m in re.finditer(r"Deanery (\d+):\s*((?:Fr\.|Msgr\.|Rev\.|Very Rev\.)[^\n]*?V\.\s?F\.)", t):
            n = int(m.group(1))
            out.setdefault(n, (re.sub(r"\s+", " ", m.group(2)).replace("V. F.", "V.F."), pn))
    return out


def find_pdf_page(pdf: P.Pdf, needle: str, first=20, last=45) -> int | None:
    for pn in range(first, last):
        if needle in pdf.page_text(pn):
            return pn
    return None


def build_structure(fx, pdf, parishes, pdf_src):
    fetched = C.now_iso()
    dn = deans(pdf)
    by_region, by_dean = defaultdict(list), defaultdict(list)
    for p in parishes:
        if p.get("region_name"):
            by_region[p["region_name"]].append(p)
        if p.get("deanery_number"):
            by_dean[p["deanery_number"]].append(p)
    units = []
    for rn in P.REGIONS:
        addr, city, zp, phone, bishop = REGION_OFFICES[rn]
        units.append({"kind": "region", "name": f"{rn} Pastoral Region", "parent": None,
                      "leader": {"name": f"Most Rev. {bishop}", "title": "Auxiliary Bishop; Regional Bishop"},
                      "area": REGION_AREA[rn], "deaneries": [n for n, r in DEANERY_REGION.items() if r == rn],
                      "office": {"name": f"{rn} Pastoral Region office", "address": addr, "city": city, "zip": zp, "phone": phone},
                      "member_parishes": [p["name"] for p in by_region[rn]], "member_parish_ids": [p["id"] for p in by_region[rn]],
                      "parish_count": len(by_region[rn]),
                      "source_url": f"{BASE}/{REGION_SLUG[rn]}/", "also_source_url": f"{pdf_src}#page=33"})
    for n in range(1, 21):
        rn = DEANERY_REGION[n]
        d = dn.get(n)
        units.append({"kind": "deanery", "name": f"Deanery {n}", "parent": f"{rn} Pastoral Region",
                      "leader": {"name": d[0], "title": "Dean (Vicar Forane)"} if d else None,
                      "member_parishes": [p["name"] for p in by_dean[n]], "member_parish_ids": [p["id"] for p in by_dean[n]],
                      "parish_count": len(by_dean[n]), "source_url": f"{pdf_src}#page={d[1] if d else 33}"})
    cathedral_p = next(p for p in parishes if p["type"] == "cathedral")
    cnt_page = find_pdf_page(pdf, "comprises the Counties", 20, 45) or 28
    return {
        "diocese": SLUG, "name": "Archdiocese of Los Angeles", "website": BASE,
        "counties": ["Los Angeles", "Santa Barbara", "Ventura"],
        "counties_source_url": f"{pdf_src}#page={cnt_page}",
        "counties_source_says": "The Archdiocese of Los Angeles comprises the Counties of Los Angeles, Santa Barbara, and Ventura (2025 Catholic Directory, About the Archdiocese). "
                                "Region pages on lacatholics.org agree: Santa Barbara Region 'Santa Barbara and Ventura Counties'; San Pedro 'Long Beach and Southern Los Angeles County'.",
        "bishops": [
            {"name": "Most Rev. José H. Gomez", "title": "Archbishop of Los Angeles", "source_url": f"{pdf_src}#page=29"},
            {"name": "Most Rev. Slawomir S. Szkredka", "title": "Auxiliary Bishop; Regional Bishop, Santa Barbara Pastoral Region", "source_url": f"{BASE}/santa-barbara-pastoral-region/"},
            {"name": "Most Rev. Albert M. Bahhuth", "title": "Auxiliary Bishop; Regional Bishop, San Fernando Pastoral Region", "source_url": f"{BASE}/san-fernando-pastoral-region/"},
            {"name": "Most Rev. Brian A. Nunes", "title": "Auxiliary Bishop; Regional Bishop, San Gabriel Pastoral Region", "source_url": f"{BASE}/san-gabriel-pastoral-region/"},
            {"name": "Most Rev. Matthew G. Elshoff, O.F.M. Cap.", "title": "Auxiliary Bishop; Regional Bishop, Our Lady of the Angels Pastoral Region", "source_url": f"{BASE}/our-lady-of-the-angels-pastoral-region/"},
            {"name": "Most Rev. Marc V. Trudeau", "title": "Auxiliary Bishop; Regional Bishop, San Pedro Pastoral Region", "source_url": f"{BASE}/san-pedro-pastoral-region/"},
        ],
        "cathedral": {"name": "Cathedral of Our Lady of the Angels", "address": cathedral_p["address"], "city": cathedral_p["city"], "zip": cathedral_p["zip"],
                      "lat": cathedral_p.get("lat"), "lng": cathedral_p.get("lng"), "source_url": cathedral_p["source_url"]},
        "units": units,
        "notes": [
            "Five pastoral regions, each led by an auxiliary bishop, group 20 deaneries (1-4 Santa Barbara, 5-8 San Fernando, 9-12 San Gabriel, 13-16 Our Lady of the Angels, 17-20 San Pedro). "
            "Each deanery has a dean (Vicar Forane, V.F.).",
            "Every parish's region and deanery comes from its header line in the directory's 'Listing of Parishes in the Archdiocese by City' (e.g. 'San Gabriel Region D10').",
            "Dean names are those printed in the 2025 directory (rev. 2025-07-08); deans and pastors change each summer, so re-check before operational use.",
            "Eastern Catholic churches and parochial missions/chapels/centers carry the region/deanery of the directory header or of their parent parish.",
        ],
        "source_urls": [pdf_src, f"{BASE}/{{region}}-pastoral-region/"],
        "fetched_at": fetched,
    }


# ---------------------------------------------------------------------------------------------- events
def build_events(fx):
    evs, raw_n = C.tribe_events(fx, BASE)
    before = len(evs)
    kept, dropped = C.screen_events([dict(e) for e in evs])
    safe = []
    for e in kept:
        if "[name withheld]" in (e.get("title") or "") or "[name withheld]" in (e.get("location") or ""):
            dropped["title_or_location_names_person"] += 1
            continue
        safe.append(e)
    return safe, raw_n, dropped


# ---------------------------------------------------------------------------------------------- main
def main() -> None:
    fx = C.Fetcher(SLUG)
    pdf_path = fetch_binary(fx, PDF_URL, "catholic_directory_2025.pdf")
    pdf = P.Pdf(pdf_path)
    vs = venues(fx)
    parishes, raw_blocks = build_parishes(fx, pdf, vs, PDF_URL)
    extra, other_dioceses = extra_parish_records(fx, pdf, vs, parishes, PDF_URL)
    allp = parishes + extra
    cities = {re.sub(r"[^a-z]", "", (p["city"] or "").lower()) for p in parishes}
    eth = P.parse_ethnic_liturgies(pdf, [94, 95, 96, 97, 98, 99, 100], cities)
    matched, unmatched = attach_languages(allp, eth)
    # coordinates: the Census geocoder for addresses with no venue record
    geocoded = 0
    for p in allp:
        if p["lat"] is None and p["address"] and p["city"]:
            g = geocode(fx, p["address"], p["city"], p["zip"])
            if g:
                p["lat"], p["lng"] = g["lat"], g["lng"]
                p["geo_source"] = "US Census Geocoder (Public_AR_Current)"
                p["geo_source_url"] = g["url"]
                geocoded += 1
    for p in allp:
        for k in ("ethnic_liturgies",):
            p.setdefault(k, [])
        if p.get("county") is None and p.get("city") in ("Oxnard", "Ventura", "Camarillo", "Fillmore", "Piru", "Simi Valley", "Thousand Oaks", "Santa Paula", "Ojai", "Moorpark", "Westlake Village", "Newbury Park"):
            p["county"] = "Ventura"
    # status: the directory lists no closures; check the archdiocese's own posts for merger/closure notices
    status_note = "Listed in the 2025 Catholic Directory (rev. 2025-07-08); the directory carries no closure or merger decrees."
    for p in allp:
        p["status_note"] = p["status_note"] or status_note
    C.dedupe_ids(allp, "name")
    for p in allp:
        for k in ("_phones", "_venue_id"):
            p.pop(k, None)
    for p in other_dioceses:
        for k in ("_phones",):
            p.pop(k, None)
    allp.sort(key=lambda p: (p.get("deanery_number") or 99, p["name"]))
    # entities
    ents, estats = build_entities(pdf, vs, parishes, PDF_URL)
    # screen free text on entities (conducted_by, names)
    for e in ents:
        for k in ("conducted_by", "organization", "note"):
            if e.get(k):
                e[k] = C.screen_text(e[k])
    # events
    events, raw_events, dropped = build_events(fx)
    structure = build_structure(fx, pdf, allp, PDF_URL)
    structure["eastern_churches_listed_for_other_dioceses"] = [{"name": p["name"], "diocese": p["listed_in_diocese"], "address": p["address"], "city": p["city"],
                                                                "source_url": p["source_url"]} for p in other_dioceses]
    # sanity: every LA parish has region + deanery
    bad = [p["name"] for p in allp if not (p["region"] and p["deanery"])]
    print("rows without region/deanery:", bad)
    assert not [p for p in allp if not (p["region"] and p["deanery"]) and p["type"] in ("parish", "cathedral", "personal_parish")], "parish without region/deanery"
    fetched = C.now_iso()
    cnt = C.counts(allp, ents, events)
    by_type = Counter(p["type"] for p in allp)
    et = Counter(e["type"] for e in ents)
    st_sub = Counter((e["type"], e.get("subtype")) for e in ents)
    nogeo = sum(1 for p in allp if p["lat"] is None)
    nopastor = sum(1 for p in allp if not p.get("pastor"))
    readme = f"""# Archdiocese of Los Angeles: official directory data

Collected {fetched[:10]} from https://lacatholics.org (WordPress) and the archdiocese's own PDF directory. Script:
`ingest/california/directories/los-angeles.py` (PDF helpers: `la_pdf.py`; needs `pdfplumber`). Re-run: `python3 -I ingest/california/directories/los-angeles.py`
(responses cached under `data/raw/california/dioceses/los-angeles/`, gitignored).

## Sources and method

- robots.txt: only `/wp-admin/` is disallowed (admin-ajax allowed); no Crawl-delay. Plain default `python-requests` User-Agent, >= 1 s between requests, {len(list(fx.raw.glob('*.json')))} responses cached from this and earlier runs (a rerun makes only the missing requests).
- **Parishes: the 2025 Archdiocese of Los Angeles Catholic Directory** (official PDF on lacatholics.org, rev. 2025-07-08, 292 pages),
  `{PDF_URL}`. The WordPress site has no parish post type in `/wp-json/wp/v2/` (the `/find/` parish finder posts to admin-ajax with a nonce and is
  backed by the same venue records), so the PDF is the complete official list. Parsed column-aware (pdfplumber): "Listing of Parishes in the Archdiocese by City"
  gives, per parish, the **Pastoral Region and Deanery code in its header** (e.g. `San Gabriel Region D10`), address, phone, website, office e-mail, pastor, year established.
  Eastern Catholic churches (directory p. 88-90), parochial missions/chapels/centers (p. 87) and the Directory of Ethnic Liturgies (p. 90-94, Mass languages) are parsed the same way.
- **Coordinates and websites**: `/wp-json/tribe/events/v1/venues` (The Events Calendar venue records, {len(vs)} venues) matched to PDF parishes by phone or street number + ZIP.
  Parishes with no venue record ({geocoded} rows) were geocoded with the US Census Geocoder (`geo_source` says which). {nogeo} rows still have no coordinates.
- **Structure**: regions, deaneries and deans (Vicars Forane) from the directory's region pages; regional bishops confirmed on the five `/<region>-pastoral-region/` pages.
- **Events**: `/wp-json/tribe/events/v1/events` (today to +120 days). {raw_events} listed, {len(events)} kept; dropped by the privacy screen: {dict(dropped) or 'none'}.
  Titles are screened with `ingest/connectors/calendars/privacy.py` then `ingest/public/privacy.py`; any title still containing a withheld name is dropped.
- **Languages**: `languages` = the non-English Mass languages the Directory of Ethnic Liturgies lists for the parish ({matched} liturgy lines matched to a parish by phone or name+city, {unmatched} unmatched);
  `ethnic_liturgies` keeps the directory's own labels (e.g. "African American", "Arab American"). English is not listed in that directory, so it is not in `languages`.

## Counts

| parishes.json rows | {len(allp)} |
|---|---|
{C.md_table(dict(by_type))}| of which Latin-rite parishes (header line in the by-city listing) | {len(parishes)} |
| Eastern Catholic churches in the LA regions | {sum(1 for p in extra if str(p.get('rite','')).endswith('(Eastern Catholic)'))} |
| status active / other | {cnt['parishes_by_status'].get('active', 0)} / {len(allp) - cnt['parishes_by_status'].get('active', 0)} |
| with coordinates | {len(allp) - nogeo} |
| with a pastor/administrator listed | {len(allp) - nopastor} |

| entities by type (subtype) | |
|---|---|
{C.md_table({f'{t} ({s})' if s else t: n for (t, s), n in st_sub.items()})}| events | {len(events)} |
| regions / deaneries in structure.json | 5 / 20 |

## Notes and gaps

- **Status.** The directory is a snapshot with no closure or merger decrees, so every row is `active` ("listed on the fetch date"). It predates any 2026 announcement; check before use.
- **Regional counts.** {len(parishes)} Latin-rite parish headers were parsed, matching the directory's own table (290: Santa Barbara 37, San Fernando 49, San Gabriel 65, Our Lady of the Angels 74, San Pedro 65;
  per region here: {dict(Counter(p['region_name'] for p in parishes))}). The lacatholics.org region pages quote larger figures (e.g. 76 for Our Lady of the Angels) because they also count Eastern Catholic churches.
- **Eastern Catholic.** {len(other_dioceses)} Eastern churches in that section are in the dioceses of Orange and San Bernardino; they are in `structure.json` (`eastern_churches_listed_for_other_dioceses`), not in parishes.json.
  Eastern churches are typed `personal_parish` (or `mission`) with `rite`; their own eparchy governs them.
- **Entities from the PDF** are layout-parsed and carry `source_url` with `#page=` of the PDF. Schools: 47 high schools, 196 parochial elementary/middle and 7 non-parochial parsed
  (the directory counts 48 / 205 / ~10; the rest failed to parse and are not guessed). Pre-schools (65 listed) and Catholic universities are not extracted.
  Retreat centers include ones the directory lists as "geographically close" (Palomar Mountain, Idyllwild, San Diego County...). Hospitals are typed `other`/`hospital` (system in `conducted_by`).
  Catholic Charities: only the agency record; its program sites are in a multi-column block mixed with staff names and are not extracted. Mortuaries are part of the cemetery entries.
  Religious institutes of men/women and the diocesan-office staff pages (names of staff) are not stored.
- **lacatholicschools.org** (Office of Catholic Schools) is a Finalsite site with Crawl-delay 5; its school locator is a JavaScript map, so it was not scraped (no JavaScript execution). The directory's school sections are used instead.
- Pastors are as printed in the directory (summer 2025); only clergy/religious names with a title are kept. Lay business managers, deacons' and priests' personal e-mails are not stored.
  `office_email` is kept only for generic office mailboxes (`parish-NNNN@la-archdiocese.org` aliases and office-style addresses).
- Blockers: none. lacatholics.org answered every request.
"""
    c = C.write_all(SLUG, allp, structure, ents, events, readme)
    print(json.dumps(c, indent=2))


if __name__ == "__main__":
    main()
