"""Diocese of Sacramento (scd.org, Drupal): parishes, schools, structure, entities and events from the public site.

No REST API (Drupal, /wp-json is 404). Routes used, all plain HTML, robots.txt allowed:
  /parishes?page=N          paged directory (10 per page) with address, phone, website and map coordinates
  /parish/<slug>            detail: deanery line, mailing address, email, pastor, parent parish, services
  /diocese/deaneries-and-deans, /bishops, /schools-list?page=N + /school/<slug>, /newman,
  /catholic-charities-and-social-concerns/catholic-charities, /catholic-funeral-and-cemetery-services/our-services,
  /staff-directory (office names only), /events?page=N (+ /event/<slug> for the location)

    python -I ingest/california/directories/sacramento.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

SLUG = "sacramento"
BASE = "https://www.scd.org"
CLERGY_TITLE = re.compile(r"^(Very Rev\.|Rev\. Msgr\.|Rev\.|Msgr\.|Most Rev\.|Deacon|Dcn\.|Fr\.|Father)\s", re.I)


def soup(r) -> BeautifulSoup:
    return BeautifulSoup(r.text, "html.parser")


def txt(el) -> str | None:
    return C.clean_text(el.get_text(" ", strip=True)) if el else None


def drupal_points(html: str) -> dict[str, tuple[float, float]]:
    """entity_id -> (lat, lng) from the leaflet block in drupalSettings."""
    m = re.search(r'<script type="application/json" data-drupal-selector="drupal-settings-json">(.*?)</script>', html, re.S)
    out = {}
    if not m:
        return out
    for k, v in (json.loads(m.group(1)).get("leaflet") or {}).items():
        for f in v.get("features", []):
            if f.get("lat") and f.get("lon") and f.get("entity_id"):
                out[str(f["entity_id"])] = (round(float(f["lat"]), 6), round(float(f["lon"]), 6))
    return out


def walk_list(fx: C.Fetcher, path: str, name_sel: str) -> list[tuple[BeautifulSoup, dict, str, str]]:
    """Every page of a paged Drupal view. Yields (row, points, page_url, fetched_at)."""
    rows, n = [], 0
    while n < 60:
        r = fx.get(f"{BASE}{path}" + (f"?page={n}" if n else ""))
        b = soup(r)
        pts = drupal_points(r.text)
        found = [row for row in b.select(".views-row") if row.select_one(name_sel)]
        if not found:
            break
        rows += [(row, pts, r.url, r.fetched_at) for row in found]
        if not any("Next page" in a.get_text() for a in b.select("a")):
            break
        n += 1
    return rows


def row_geo(row, pts) -> tuple[float | None, float | None]:
    m = row.select_one('[id^="leaflet-map-"]')
    if m:
        eid = re.search(r"-(\d+)-field-geofield", m["id"])
        if eid and eid.group(1) in pts:
            return pts[eid.group(1)]
    return None, None


# ------------------------------------------------------------------------------------------- parishes
def parse_parish_detail(fx: C.Fetcher, href: str) -> dict:
    out = {}
    try:
        r = fx.get(BASE + href)
    except C.Blocked as e:
        return {"detail_error": str(e)}
    b = soup(r)
    out["detail_url"], out["detail_fetched_at"] = r.url, r.fetched_at

    def field(name):
        el = b.select_one(f".views-field-field-{name}")
        return el

    dv = txt(field("vicariate"))
    if dv:
        m = re.match(r"(.+?) County,\s*(.+?) Deanery$", dv)
        out["county"], out["deanery"] = (m.group(1), m.group(2)) if m else (None, dv)
    pastor = field("pastor") or field("parochial-admin")
    if pastor:
        t = txt(pastor) or ""
        role = "Pastor" if field("pastor") is not None else "Parochial Administrator"
        nm = re.sub(r"^(Pastor|Parochial Administrator|Administrator)\s+", "", t).strip()
        if nm:
            out["pastor"] = C.pastor_record(nm, role)
    adm = field("parochial-admin")
    if adm and not out.get("pastor"):
        nm = re.sub(r"^Parochial Administrator\s+", "", txt(adm) or "").strip()
        if nm:
            out["pastor"] = C.pastor_record(nm, "Parochial Administrator")
    em = field("email")
    if em:
        a = em.find("a", href=True)
        out["office_email"] = C.generic_email(a["href"]) if a else None
        out["_email_seen"] = bool(a)
    wb = field("website")
    if wb:
        a = wb.find("a", href=True)
        out["website"] = C.norm_url(a["href"]) if a else None
    par = field("parent-org")
    if par:
        out["parent_parish"] = txt(par)
    ye = txt(field("year-established"))
    if ye:
        out["established"] = re.sub(r"^Established\s+", "", ye)
    a1, pc = txt(field("primary-address-address-line1")), txt(field("primary-address-postal-code"))
    out["address"], out["city_zip"] = a1, pc
    ma = txt(field("mailing-address-address-line1"))
    if ma:
        out["mailing_address"] = ma
    services = " ".join(txt(e) or "" for e in b.select(".views-field-field-service-start-time"))
    out["languages"] = C.detect_languages(services)
    return out


def build_parishes(fx: C.Fetcher) -> list[dict]:
    rows = walk_list(fx, "/parishes", ".parish-name")
    parishes = []
    for row, pts, page_url, page_ts in rows:
        a = row.select_one(".parish-name a")
        name = txt(a)
        href = a["href"]
        loc = row.select_one(".parish-address .locality")
        st = row.select_one(".parish-address .postal-code")
        lat, lng = row_geo(row, pts)
        web = row.select_one(".parish-website a")
        d = parse_parish_detail(fx, href)
        city = txt(loc)
        z = C.zip5(txt(st))
        ptype = C.parish_type(name)
        if re.search(r"\bmission\b", name, re.I):
            ptype = "mission"
        if re.search(r"chaplain|chaplaincy|hospital|medical|prison|correctional|state prison", name, re.I):
            ptype = "chapel"
        if re.search(r"newman", name, re.I):
            ptype = "campus_ministry"
        rec = {
            "name": name,
            "type": ptype,
            "address": txt(row.select_one(".parish-address .address-line1")),
            "city": city,
            "zip": z,
            "lat": lat, "lng": lng,
            "phone": C.norm_phone(txt(row.select_one(".parish-phone"))),
            "website": C.norm_url(web["href"]) if web else d.get("website"),
            "office_email": d.get("office_email"),
            "languages": d.get("languages") or [],
            "pastor": d.get("pastor"),
            "deanery": d.get("deanery"),
            "county": d.get("county"),
            "parent_parish": d.get("parent_parish"),
            "established": d.get("established"),
            "mailing_address": d.get("mailing_address"),
            "status": "active",
            "status_note": "Listed in the diocese's online parish directory on the fetch date; no merger or closure decree is published on the page.",
            "source_url": BASE + href,
            "list_source_url": page_url,
            "fetched_at": d.get("detail_fetched_at") or page_ts,
        }
        if d.get("detail_error"):
            rec["detail_error"] = d["detail_error"]
        if d.get("_email_seen") and not rec["office_email"]:
            rec["email_withheld"] = "page lists a non-generic or personal address"
        parishes.append(rec)
    C.dedupe_ids(parishes)
    return parishes


# ------------------------------------------------------------------------------------------- structure
def build_structure(fx: C.Fetcher, parishes: list[dict]) -> dict:
    units, sources = [], []
    # deaneries and deans
    r = fx.get(BASE + "/diocese/deaneries-and-deans")
    sources.append(r.url)
    b = soup(r)
    body = b.select_one("main") or b.select_one("#main-content") or b.body
    for h in body.find_all("h3"):
        nm = txt(h) or ""
        if not nm.endswith(" Deanery"):
            continue
        p = h.find_next_sibling("p")
        if p is None:
            continue
        for br in p.find_all("br"):
            br.replace_with("\n")
        lines = [C.clean_text(x) for x in p.get_text().split("\n")]
        lines = [x for x in lines if x]
        cur = {"kind": "deanery", "name": nm.removesuffix(" Deanery"), "parent": None, "leader": None,
               "member_parishes": [], "source_url": r.url}
        for t in lines:
            if re.search(r"\bDean\s*$", t) and CLERGY_TITLE.match(t):
                cur["leader"] = {"name": re.sub(r",?\s*Dean\b.*$", "", t).strip(" ,"), "title": "Dean"}
            else:
                cur["member_parishes"].append(t)
        units.append(cur)
    # members as listed by the page ("City, Parish name"); also the directory's own deanery field
    by_dean = {}
    for p in parishes:
        by_dean.setdefault(p.get("deanery"), []).append(p["name"])
    for u in units:
        u["member_parishes_directory"] = by_dean.get(u["name"], [])
    # bishops
    r2 = fx.get(BASE + "/bishops")
    sources.append(r2.url)
    t = re.sub(r"\s+", " ", soup(r2).get_text(" ", strip=True))
    bishops = []
    for m in re.finditer(r"(Most Reverend|Most Rev\.) ([A-Z][A-Za-z.\- ]+?) (Bishop of the Diocese of Sacramento|Auxiliary Bishop of Sacramento|Bishop Emeritus of Sacramento)", t):
        bishops.append({"name": f"Most Rev. {m.group(2).strip()}", "title": m.group(3).replace("Bishop of the Diocese of Sacramento", "Bishop of Sacramento"),
                        "source_url": r2.url})
    # counties: the diocese's About page states the number; each parish page names its county
    r3 = fx.get(BASE + "/about")
    sources.append(r3.url)
    t3 = re.sub(r"\s+", " ", soup(r3).get_text(" ", strip=True))
    says = re.search(r"The Diocese of Sacramento, comprised of (\d+) counties", t3)
    counties = sorted({p["county"] for p in parishes if p.get("county")})
    cath = next((p for p in parishes if p["type"] == "cathedral"), None)
    cathedral = None
    if cath:
        cathedral = {"name": cath["name"], "address": cath["address"], "city": cath["city"], "zip": cath["zip"],
                     "website": cath["website"], "source_url": cath["source_url"]}
    return {
        "diocese": SLUG, "name": "Diocese of Sacramento", "website": BASE, "bishops": bishops, "cathedral": cathedral,
        "counties": counties,
        "counties_source_url": r3.url,
        "counties_says": says.group(0) if says else None,
        "counties_basis": "County names read from each parish page's own 'County, Deanery' line (" + BASE + "/parish/...); the About page gives the count only (the list is on an image map).",
        "units": units, "notes": [
            "The diocese is organised into deaneries (no regions or vicariates are published). The directory field called "
            "'vicariate' on each parish page holds the county and deanery."],
        "source_urls": sources, "fetched_at": r.fetched_at,
    }


# ------------------------------------------------------------------------------------------- entities
def build_entities(fx: C.Fetcher, parishes: list[dict]) -> list[dict]:
    ents = []
    # schools
    rows = walk_list(fx, "/schools-list", ".school-name")
    for row, pts, page_url, ts in rows:
        a = row.select_one(".school-name a")
        href = a["href"]
        web = row.select_one(".school-website a")
        lat, lng = row_geo(row, pts)
        e = {"type": "school", "name": txt(a), "address": None, "city": None, "zip": None,
             "website": C.norm_url(web["href"]) if web else None,
             "phone": C.norm_phone(txt(row.select_one(".school-phone"))),
             "grades": re.sub(r"^Grades:\s*", "", txt(row.select_one(".school-details")) or "") or None,
             "lat": lat, "lng": lng, "source_url": BASE + href, "fetched_at": ts}
        try:
            r = fx.get(BASE + href)
            b = soup(r)
            e["fetched_at"] = r.fetched_at
            a1 = txt(b.select_one(".views-field-field-primary-address-address-line1"))
            pc = txt(b.select_one(".views-field-field-primary-address-postal-code"))
            e["address"] = a1
            m = re.match(r"(.+?),\s*(?:California|CA)\s+(9\d{4})", pc or "")
            if m:
                e["city"], e["zip"] = m.group(1).strip(), m.group(2)
            pr = b.select_one(".views-field-field-parish, .views-field-field-parent-org")
            if pr:
                e["parish_affiliation"] = txt(pr)
        except C.Blocked as ex:
            e["detail_error"] = str(ex)
        ents.append(e)
    # Newman centers
    r = fx.get(BASE + "/newman")
    t = soup(r).get_text("\n", strip=True)
    for m in re.finditer(r"^(Chico|Davis|Sacramento) Newman\n(.+?)\n(.+?), CA (9\d{4})\n([\d\-]+)\n(\S+\.org)", t, re.M):
        ents.append({"type": "campus_ministry", "name": f"{m.group(1)} Newman Catholic Center", "address": m.group(2), "city": m.group(3),
                     "zip": m.group(4), "website": C.norm_url(m.group(6)), "phone": C.norm_phone(m.group(5)),
                     "source_url": r.url, "fetched_at": r.fetched_at})
    # Catholic Charities
    r = fx.get(BASE + "/catholic-charities-and-social-concerns/catholic-charities")
    b = soup(r)
    text = b.get_text("\n", strip=True)
    ents.append({"type": "catholic_charities", "name": "Catholic Charities of Sacramento, Inc.", "address": "2110 Broadway", "city": "Sacramento",
                 "zip": "95818", "website": r.url, "phone": "(916) 733-0100",
                 "note": "Umbrella corporation; member agencies below. Address and phone are the Pastoral Center's, as listed on the site footer.",
                 "source_url": r.url, "fetched_at": r.fetched_at})
    for m in re.finditer(r"^((?:Northern Valley Catholic Social Service|Catholic Charities of [A-Za-z\- ]+)[^\n]*?)\n(?:Contact:\s*)?([\d\-\(\) ]{10,14})?", text, re.M):
        nm = m.group(1).strip()
        if len(nm) < 90 and not any(x["name"] == nm for x in ents):
            ents.append({"type": "catholic_charities", "name": nm, "address": None, "city": None, "zip": None, "website": None,
                         "phone": C.norm_phone(m.group(2)), "note": "Member agency / program listed on the diocese's Catholic Charities page",
                         "source_url": r.url, "fetched_at": r.fetched_at})
    # Cemeteries (CFCS)
    r = fx.get(BASE + "/catholic-funeral-and-cemetery-services/our-services")
    t = re.sub(r"\s+", " ", soup(r).get_text(" ", strip=True))
    i = t.find("Fax: 916-840-2199")
    seg = t[i:i + 900] if i >= 0 else ""
    for m in re.finditer(r"([A-Z][A-Za-z.' ]+? Cemetery(?: & Funeral Center)?)(?: \(FD \d+\))? ([A-Z][A-Za-z ]+), CA (?:(9\d{4}) )?(\d{3}-\d{3}-\d{4})", seg):
        ents.append({"type": "cemetery", "name": m.group(1).strip(), "address": None, "city": m.group(2).strip(), "zip": m.group(3),
                     "website": r.url, "phone": C.norm_phone(m.group(4)), "operator": "Catholic Funeral & Cemetery Services (diocesan)",
                     "source_url": r.url, "fetched_at": r.fetched_at})
    # diocesan offices (section headings of the Pastoral Center directory; no staff names, no personal mail)
    r = fx.get(BASE + "/staff-directory")
    b = soup(r)
    for h in b.find_all("h3"):
        nm = txt(h)
        if nm:
            ents.append({"type": "diocesan_office", "name": nm, "address": "2110 Broadway", "city": "Sacramento", "zip": "95818",
                         "website": BASE + "/staff-directory", "phone": "(916) 733-0100",
                         "note": "Pastoral Center office; main number from the site footer",
                         "source_url": r.url, "fetched_at": r.fetched_at})
    # retreat centers / camps: Christ the King Retreat Center is listed in the West Placer deanery
    ctk = next((u for u in parishes if re.search(r"retreat", u["name"], re.I)), None)
    ents.append({"type": "retreat_center", "name": "Christ the King Retreat Center", "address": None, "city": "Loomis", "zip": None,
                 "website": None, "phone": None,
                 "note": "Named in the diocese's deanery list (West Placer); address not on the pages read.",
                 "source_url": BASE + "/diocese/deaneries-and-deans", "fetched_at": r.fetched_at})
    C.dedupe_ids(ents)
    return ents


# ------------------------------------------------------------------------------------------- events
MONTHS = "January February March April May June July August September October November December".split()


def parse_dt(date_s: str, time_s: str | None):
    m = re.search(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})", date_s or "")
    if not m or m.group(1) not in MONTHS:
        return None, None
    d = f"{m.group(3)}-{MONTHS.index(m.group(1)) + 1:02d}-{int(m.group(2)):02d}"

    def hm(s):
        mm = re.search(r"(\d{1,2}):(\d{2})\s*([ap]m)", s or "", re.I)
        if not mm:
            return None
        h = int(mm.group(1)) % 12 + (12 if mm.group(3).lower() == "pm" else 0)
        return f"{h:02d}:{mm.group(2)}:00"
    times = re.findall(r"\d{1,2}:\d{2}\s*[ap]m", time_s or "", re.I)
    st = hm(times[0]) if times else None
    en = hm(times[1]) if len(times) > 1 else None
    return (f"{d}T{st}" if st else d), (f"{d}T{en}" if en else None)


def build_events(fx: C.Fetcher) -> tuple[list[dict], int, dict]:
    evs, n, raw = [], 0, 0
    while n < 30:
        try:
            r = fx.get(BASE + "/events" + (f"?page={n}" if n else ""))
        except C.Blocked:
            break
        b = soup(r)
        rows = [x for x in b.select(".views-row") if x.select_one(".views-field-title")]
        if not rows:
            break
        for row in rows:
            raw += 1
            a = row.select_one(".views-field-title a")
            dt = txt(row.select_one(".datetime"))
            body = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
            tm = re.search(r"(\d{1,2}:\d{2} [ap]m(?: - \d{1,2}:\d{2} [ap]m)?)", body, re.I)
            start, end = parse_dt(dt, tm.group(1) if tm else None)
            loc = txt(row.select_one(".views-field-field-location-name"))
            evs.append({"title": txt(a), "start": start, "end": end, "location": loc, "url": BASE + a["href"],
                        "categories": [txt(x) for x in row.select(".views-field-field-event-type .field__item")],
                        "source_url": r.url, "fetched_at": r.fetched_at, "_description": txt(row.select_one(".field--name-field-description"))})
        if not any("Next page" in a.get_text() for a in b.select("a")):
            break
        n += 1
    evs = [e for e in evs if C.in_window(e["start"], e["end"])]
    kept, dropped = C.screen_events(evs)
    return kept, raw, dict(dropped)


def main() -> None:
    fx = C.Fetcher(SLUG)
    parishes = build_parishes(fx)
    structure = build_structure(fx, parishes)
    entities = build_entities(fx, parishes)
    events, raw_events, dropped = build_events(fx)
    nsch = sum(1 for e in entities if e["type"] == "school")
    c0 = C.counts(parishes, entities, events)
    readme = C.readme("Diocese of Sacramento", BASE, "sacramento.py", f"""- robots.txt (scd.org): no Crawl-delay; `/search/`, `/admin/` and similar are disallowed and none are used. {len(list(fx.raw.glob('*.json')))} distinct responses cached.
- The site is Drupal (no WordPress REST API; `/wp-json/` is 404). Routes read are plain HTML views.
- `/parishes?page=N` (10 per page, {len(parishes)} entries): name, address, phone, website, map coordinates (the page's own Leaflet data).
  `/parish/<slug>` for each entry: county and deanery line ("<County> County, <X> Deanery"), pastor or parochial administrator as listed,
  parent parish (missions), generic office email (non-generic and personal addresses are dropped by `common.generic_email`), established year,
  Mass languages (from the listed service lines).
- `/diocese/deaneries-and-deans`: {len(structure['units'])} deaneries, each with its dean and the parishes the page lists. `/bishops`: bishops.
  `/about`: "comprised of 20 counties"; the county names come from the parish pages' own county line (the About page's county list is an image map).
- Entities: schools from `/schools-list?page=N` + `/school/<slug>` ({nsch}); Newman centers from `/newman`; Catholic Charities member agencies from
  `/catholic-charities-and-social-concerns/catholic-charities`; cemeteries from `/catholic-funeral-and-cemetery-services/our-services`; diocesan office names
  from `/staff-directory` (office headings only; staff names and mailboxes are not stored).
- Events: `/events?page=N` (Drupal view; no iCal or REST feed): {raw_events} listed, {len(events)} kept after the date window (today to +120 days)
  and the privacy screen (dropped: {dropped or 'none'}).""",
        f"""- `status` is `active` for every entry: the directory lists no closed or merged parishes and publishes no decrees. It means "listed on the fetch date".
  Missions carry `parent_parish`; hospital, prison and air-base chaplaincies are typed `chapel`.
- `/catholic-funeral-and-cemetery-services` (the section landing page) answers HTTP 403 (a Drupal access-denied page, not a bot challenge); the `/our-services` sub-page works.
- Cemetery street addresses, Christ the King Retreat Center's address, Camp Pendola, religious communities and seminaries are not published as lists on scd.org.
  The 2025-26 diocesan directory is a purchasable PDF (`/directory`), not read here.
- Regions and vicariates do not exist in this diocese's published structure; deaneries only (the site's field is named "vicariate" but holds the deanery).
- Mass languages come only from the service lines on each parish page; {sum(1 for p in parishes if p['office_email'])} of {len(parishes)} entries have a generic office email.""", c0)
    c = C.write_all(SLUG, parishes, structure, entities, events, readme)
    print(json.dumps(c, indent=2))


if __name__ == "__main__":
    main()
