"""Archdiocese of San Francisco (sfarch.org, WordPress + Elementor): parishes, schools, offices, events, structure.

The archdiocese's current site is sfarch.org (sfarchdiocese.org, the older name, answers a Cloudflare challenge to
our client and is not used). sfarch.org/robots.txt allows the routes below; no Crawl-delay.

  /wp-json/wp/v2/facility            custom post type: one row per parish / school / chancery office / hospital / other
  /wp-json/wp/v2/facility-type       the type terms
  /facility/<slug>/                  HTML detail page: street, city, zip, phone, website (the REST `acf` field is empty)
  /resources/priests-directory/?e-page-...=N   public clergy assignments (role + parish) -> pastors
  /wp-json/tribe/events/v1/events    The Events Calendar API
  /about/our-archdiocese/            counties; bishop pages; /resources/staff-directory-new/ (office names)

    python -I ingest/california/directories/san-francisco.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

SLUG = "san-francisco"
BASE = "https://sfarch.org"
TYPES = {669: "chancery", 670: "parish", 671: "school", 677: "other", 679: "hospital", 680: "cemetery"}


def soup(r) -> BeautifulSoup:
    b = BeautifulSoup(r.text, "html.parser")
    for s in b(["script", "style"]):
        s.decompose()
    return b


def fkey(name: str) -> str:
    n = C.clean_text(name) or ""
    n = re.sub(r"\b(saint|st\.?|san|santa)\b", "st", n.lower())
    n = re.sub(r"\b(parish|church|the|of|and|de|del)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def parse_facility(fx: C.Fetcher, link: str) -> dict:
    try:
        r = fx.get(link)
    except C.Blocked as e:
        return {"detail_error": str(e)}
    b = soup(r)
    out = {"detail_url": r.url, "detail_fetched_at": r.fetched_at}
    h = b.select_one("h2.elementor-heading-title")
    title = C.clean_text(h.get_text()) if h else None
    t = re.sub(r"\s+", " ", (b.body or b).get_text(" ", strip=True))
    k = t.find("Back to ")
    i = t.find(title, k if k >= 0 else 0) if title else -1
    seg = t[i + len(title):i + len(title) + 400] if i >= 0 else t[:600]
    m = re.search(r"^\s*(.+?),\s+([A-Za-z.' \-]+?),?\s+CA,?\s*(9\d{4})?\s*(.*)$", seg)
    if m:
        out["address"], out["city"], out["zip"] = m.group(1).strip(" ,"), m.group(2).strip(), m.group(3) or None
        out["phone"] = C.norm_phone(m.group(4)[:30])
    else:
        out["address_raw"] = seg[:120]
    for a in b.select("a.elementor-icon[href]"):
        if a.find("i", class_=re.compile("globe")):
            out["website"] = C.norm_url(a["href"])
            break
    return out


def clergy_assignments(fx: C.Fetcher) -> tuple[dict, list[str]]:
    """facility key -> {'pastor': {...}, ...} from the public priests directory (role as listed). No emails stored."""
    by_fac: dict[str, dict] = {}
    urls, n = [], 1
    FAC_ROLES = ("Pastor", "Administrator", "Parochial Administrator", "Rector", "Pastor Emeritus", "Parochial Vicar",
                 "Resident", "Chaplain", "Supply Priest", "Faculty", "Primary Location")
    while n <= 40:
        u = f"{BASE}/resources/priests-directory/" + (f"?e-page-c8c8e89={n}" if n > 1 else "")
        try:
            r = fx.get(u)
        except C.Blocked:
            break
        urls.append(r.url)
        b = soup(r)
        items = b.select("div.e-loop-item.clergy")
        if not items:
            break
        for it in items:
            L = [x for x in (C.clean_text(s) for s in it.get_text("\n").split("\n")) if x]
            name = L[0]
            if "@" in name or not re.match(r"^(Fr\.|Rev\.|Msgr\.|Very Rev\.|Most Rev\.|Bishop|Archbishop|Fr )", name):
                continue
            j = 0
            while j < len(L):
                if L[j] in FAC_ROLES and j + 1 < len(L):
                    role, fac = L[j], L[j + 1]
                    info = by_fac.setdefault(fkey(fac), {})
                    if "addr" not in info and j + 3 < len(L) and re.search(r"\b9\d{4}\b|, CA\b", L[j + 3]):
                        cz = re.match(r"(.+?),\s*CA,?\s*(9\d{4})?", L[j + 3])
                        info["addr"] = {"address": L[j + 2], "city": cz.group(1).strip() if cz else None,
                                        "zip": cz.group(2) if cz else None, "source_url": r.url, "name": fac}
                        ph = next((L[k + 1] for k in range(j + 4, min(j + 8, len(L) - 1)) if L[k] == "Phone:"), None)
                        info["addr"]["phone"] = C.norm_phone(ph)
                    if role in ("Pastor", "Administrator", "Parochial Administrator", "Rector"):
                        by_fac.setdefault(fkey(fac), {}).setdefault("pastor", {"name": name, "title": role,
                                                                                 "source_url": r.url})
                    j += 2
                else:
                    j += 1
        if not any(a.get_text(strip=True).startswith("Next") and a.get("href") for a in b.select(".elementor-pagination a")):
            break
        n += 1
    return by_fac, urls


def external_entities(fx: C.Fetcher) -> list[dict]:
    out = []
    try:
        r = fx.get("https://sfcathcems.org/our-locations/")
        t = re.sub(r"\s+", " ", soup(r).body.get_text(" ", strip=True))
        names = ["Holy Cross Catholic Cemetery", "Holy Cross Menlo Park", "Mt. Olivet San Rafael", "Our Lady of the Pillar",
                 "St. Anthony’s Cemetery", "St. Mary Magdalene", "Tomales Catholic Cemetery"]
        i = t.find("Holy Cross Catholic Cemetery 1500")
        seg = t[i:] if i >= 0 else t
        for nm in names:
            m = re.search(re.escape(nm) + r" (.+?) (Colma|Menlo Park|San Rafael|Half Moon Bay|Pescadero|Bolinas|Tomales), CA (9\d{4}) \((\d{3})\) (\d{3}-\d{4})", seg)
            if m:
                out.append({"type": "cemetery", "name": nm, "address": m.group(1), "city": m.group(2), "zip": m.group(3),
                            "website": "https://sfcathcems.org/", "phone": f"({m.group(4)}) {m.group(5)}",
                            "operator": "Archdiocese of San Francisco Catholic Cemeteries",
                            "source_url": r.url, "fetched_at": r.fetched_at})
    except C.Blocked as e:
        out.append({"type": "cemetery", "name": "Archdiocese of San Francisco Catholic Cemeteries", "website": "https://sfcathcems.org/",
                    "source_url": "https://sfarch.org/about/our-archdiocese/", "fetched_at": C.now_iso(), "parse_note": str(e)})
    try:
        r = fx.get("https://www.vallombrosa.org/")
        out.append({"type": "retreat_center", "name": "Vallombrosa Retreat Center", "address": None, "city": "Menlo Park", "zip": None,
                    "website": "https://www.vallombrosa.org/", "phone": "(650) 325-5614",
                    "note": "Linked from sfarch.org; street address not on the home page",
                    "source_url": r.url, "fetched_at": r.fetched_at})
    except C.Blocked as e:
        pass
    out.append({"type": "catholic_charities", "name": "Catholic Charities (San Francisco, San Mateo and Marin)", "address": None, "city": None,
                "zip": None, "website": "https://catholiccharitiessf.org/", "phone": None,
                "note": "Named and linked on sfarch.org; catholiccharitiessf.org answers HTTP 403 to our client, so its program sites are not collected.",
                "source_url": "https://sfarch.org/about/our-archdiocese/", "fetched_at": C.now_iso()})
    out.append({"type": "other", "name": "Catholic Schools (Archdiocesan school office site)", "address": None, "city": None, "zip": None,
                "website": "https://schools.sfarch.org/", "phone": None,
                "note": "schools.sfarch.org (school finder) answers a Cloudflare challenge; school names come from the sfarch.org facility list",
                "source_url": "https://sfarch.org/about/our-archdiocese/", "fetched_at": C.now_iso()})
    return out


def main() -> None:
    fx = C.Fetcher(SLUG)
    facs, resps = fx.wp_all(BASE, "wp/v2/facility", {"per_page": 100, "_fields": "id,slug,link,title,facility-type,modified"})
    list_url, list_ts = resps[0].url, resps[0].fetched_at
    pastors, clergy_urls = clergy_assignments(fx)
    parishes, ents = [], []
    for f in facs:
        types = [TYPES.get(t) for t in f.get("facility-type", [])]
        kind = next((t for t in types if t), "other")
        name = C.clean_text(f["title"]["rendered"])
        d = parse_facility(fx, f["link"])
        base = {
            "name": name, "address": d.get("address"), "city": d.get("city"), "zip": d.get("zip"),
            "phone": d.get("phone"), "website": d.get("website"), "source_url": f["link"],
            "fetched_at": d.get("detail_fetched_at") or list_ts,
        }
        if d.get("detail_error") or d.get("address_raw"):
            base["parse_note"] = d.get("detail_error") or f"address not parsed: {d.get('address_raw')}"
        if kind == "parish":
            ptype = C.parish_type(name)
            if re.search(r"cathedral", name, re.I):
                ptype = "cathedral"
            if re.search(r"\bshrine\b", name, re.I):
                ptype = "shrine"
            pr = pastors.get(fkey(name))
            pr = pr if pr and pr.get("pastor") else None
            parishes.append({**base, "type": ptype, "lat": None, "lng": None, "office_email": None, "languages": [],
                             "pastor": ({k: v for k, v in pr["pastor"].items() if k != "source_url"} if pr else None),
                             "pastor_source_url": pr["pastor"]["source_url"] if pr else None,
                             "region": None, "vicariate": None, "deanery": None,
                             "status": "active",
                             "status_note": "Listed as a Parish facility on sfarch.org on the fetch date; no merger or closure decree is published there.",
                             "wp_id": f["id"], "list_source_url": list_url})
        else:
            etype = {"school": "school", "chancery": "diocesan_office", "hospital": "other", "cemetery": "cemetery"}.get(kind, "other")
            if re.search(r"seminary", name, re.I):
                etype = "seminary"
            elif etype == "other" and re.search(r"retreat", name, re.I):
                etype = "retreat_center"
            elif etype == "other" and re.search(r"newman|campus|university|college|USF|UCSF|Stanford", name, re.I) and kind != "hospital":
                etype = "campus_ministry"
            ents.append({**base, "type": etype, "facility_type": kind, "wp_id": f["id"], "list_source_url": list_url})
    # addresses for entities whose own page has none: the public clergy directory prints the site's address
    for e in ents:
        a = (pastors.get(fkey(e["name"])) or {}).get("addr")
        if a and not e["address"]:
            e.update(address=a["address"], city=a["city"], zip=a["zip"], phone=e["phone"] or a["phone"],
                     address_source_url=a["source_url"])
    for pr in parishes:
        a = (pastors.get(fkey(pr["name"])) or {}).get("addr")
        if a and not pr["address"]:
            pr.update(address=a["address"], city=a["city"], zip=a["zip"], phone=pr["phone"] or a["phone"],
                      address_source_url=a["source_url"])
    for pr in parishes:
        pr.pop("parse_note", None) if pr.get("address") else None
    for e in ents:
        if not e["address"]:
            e["parse_note"] = "no address on the facility page or in the clergy directory"
        else:
            e.pop("parse_note", None)
    # other official hosts the archdiocese links from sfarch.org
    ents += external_entities(fx)
    C.dedupe_ids(parishes)
    C.dedupe_ids(ents)

    # structure: counties, bishops, cathedral
    r = fx.get(f"{BASE}/about/our-archdiocese/")
    t = re.sub(r"\s+", " ", soup(r).get_text(" ", strip=True))
    m = re.search(r"Counties: (\d) \(([^)]+)\)", t)
    counties = [c.strip() for c in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(2)) if c.strip()] if m else []
    r2 = fx.get(f"{BASE}/resources/staff-directory-new/")
    bishops = [{"name": "Most Rev. Salvatore J. Cordileone", "title": "Archbishop of San Francisco",
                "source_url": f"{BASE}/about/our-archbishop/"}]
    r4 = fx.get(f"{BASE}/auxiliary-bishops-emeriti/")
    t4 = re.sub(r"\s+", " ", soup(r4).get_text(" ", strip=True))
    for nm4 in re.findall(r"Bishop ((?:[A-Z][A-Za-z.]*\s){1,3}?[A-Z][a-z]+)(?= Auxiliary Bishops Emeriti)", t4):
        bishops.append({"name": f"Most Rev. {nm4}", "title": "Auxiliary Bishop Emeritus", "source_url": r4.url})
    st = re.sub(r"\s+", " ", soup(r2).get_text(" ", strip=True))
    r3 = fx.get(f"{BASE}/our-archbishop-page/")
    bt = re.sub(r"\s+", " ", soup(r3).get_text(" ", strip=True))
    cath = next((p for p in parishes if p["type"] == "cathedral"), None)
    events, raw_n = C.tribe_events(fx, BASE)
    events = [e for e in events if C.in_window(e["start"], e["end"])]
    events, dropped = C.screen_events(events)
    structure = {
        "diocese": SLUG, "name": "Archdiocese of San Francisco", "website": BASE, "bishops": bishops,
        "cathedral": ({k: cath[k] for k in ("name", "address", "city", "zip", "website", "source_url")} if cath else None),
        "counties": counties, "counties_source_url": r.url, "counties_says": m.group(0) if m else None,
        "units": [], "notes": [],
        "source_urls": [r.url, r2.url, r3.url] + clergy_urls[:1], "fetched_at": r.fetched_at,
    }
    c = C.counts(parishes, ents, events)
    readme = C.readme("Archdiocese of San Francisco", BASE, "san-francisco.py", f"""- robots.txt (sfarch.org): only `/wp-admin/` and two export paths are disallowed; no Crawl-delay. {len(list(fx.raw.glob('*.json')))} distinct responses cached.
- The official site is `sfarch.org`. `sfarchdiocese.org` / `www.sfarchdiocese.org` answer a Cloudflare challenge (from the sandbox and from the owner's
  laptop connection), so they were not used. `schools.sfarch.org` (school finder) is also challenged; `catholiccharitiessf.org` answers 403.
- `/wp-json/wp/v2/facility` (custom post type, {len(facs)} rows: Parish 91, School 93, Chancery 43, Hospital 12, Other 18) + `/wp-json/wp/v2/facility-type`.
  The REST `acf` field is empty, so each `/facility/<slug>/` page is read for street, city, zip, phone and website (parish pages have them; school and
  chancery pages do not). Missing addresses are filled from the public priests directory's assignment listings where the same site appears.
- Pastors: `/resources/priests-directory/?e-page-...=N` (15 pages) - the assignment shown as Pastor / Administrator / Rector is used, as the directory names it.
  Priests' emails and phone numbers on that page are not stored.
- Structure: `/about/our-archdiocese/` (counties), `/about/our-archbishop/`, `/auxiliary-bishops-emeriti/`. The sfarch.org pages read publish no deaneries,
  regions or vicariates, so `units` is empty.
- Events: The Events Calendar API (`/wp-json/tribe/events/v1/events`), start today to +120 days: {raw_n} listed, {len(events)} kept; the privacy screen dropped {dict(dropped) or 'none'}.
- Cemeteries (sfcathcems.org/our-locations/) and Vallombrosa (vallombrosa.org) are linked from sfarch.org's menu and were read.""",
        f"""- Deaneries are not published on the pages found; `region`, `vicariate`, `deanery` are null on every parish.
- School and chancery facility pages carry no address; school addresses are therefore mostly null here (the CDE/NCES file in `data/real/california/schools_cde.json` has them).
- `schools.sfarch.org` (Cloudflare challenge) and `catholiccharitiessf.org` (403) could not be read; Catholic Charities program sites are not collected.
- `status` is `active` for every parish because the directory lists no closed or merged parishes; it means "listed on the fetch date", not a verified status.
- Mass languages are not in the directory; `languages` is empty.""", c)
    c = C.write_all(SLUG, parishes, structure, ents, events, readme)
    print(json.dumps(c, indent=2), "raw events", raw_n, dropped, "requests", fx.requests_made)


if __name__ == "__main__":
    main()
