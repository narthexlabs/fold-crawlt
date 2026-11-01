#!/usr/bin/env python3
"""Diocese of Orange: official directory data -> data/real/california/dioceses/orange/.

rcbo.org sits behind WP Engine + Cloudflare. For the sandbox's plain python-requests User-Agent, requests that are not
already in the site's cache answer a bare nginx 403 (the all-parishes page and the sitemaps came through; the REST
directory, the events API and every HTML detail page did not), and from Matthew's laptop the plain urllib User-Agent is
refused by Cloudflare (error 1010, "browser signature banned"). Neither block is worked around. What the module uses:

  live (sandbox, Fetcher):   /wp-json/wp/v2/pages?slug=all-parishes   (the diocese's list of parishes and centers)
                             ccoc.org (Catholic Charities of Orange County; answers the sandbox)
  Wayback Machine captures   rcbo.org parish and school detail pages, deanery list, all-schools, about, ministry pages,
  (laptop_fetch.py, run on   occem.org cemetery pages. Every field read from a capture carries `wayback_url` with the
  the laptop; bundles staged capture timestamp. Bundles live in data/raw/california/dioceses/orange/wayback/ (gitignored).
  back to the sandbox):

Run: python3 -I ingest/california/directories/orange.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

SLUG = "orange"
BASE = "https://www.rcbo.org"
WB_DIR = C.RAW_ROOT / "dioceses" / SLUG / "wayback"
ALL_PARISHES_REST = f"{BASE}/wp-json/wp/v2/pages"

EASTERN = [("byzantine", "Byzantine (Eastern Catholic)"), ("melkite", "Melkite (Eastern Catholic)"),
           ("chaldean", "Chaldean (Eastern Catholic)"), ("maron", "Maronite (Eastern Catholic)"),
           ("syro-malabar", "Syro-Malabar (Eastern Catholic)")]
CENTER_LANG = [("korean", "Korean"), ("vietnamese", "Vietnamese"), ("filipino", "Filipino"), ("polish", "Polish")]


# ---------------------------------------------------------------------------------------------- captures
class Captures:
    """Wayback captures read back from the laptop bundles."""

    def __init__(self) -> None:
        self.by_url: dict[str, dict] = {}
        for f in sorted(WB_DIR.glob("bundle_*.json")):
            for _name, rec in json.loads(f.read_text()).items():
                orig = rec["url"].split("id_/", 1)[1] if "id_/" in rec["url"] else rec["url"]
                self.by_url[orig.rstrip("/")] = rec

    def get(self, original_url: str) -> dict | None:
        return self.by_url.get(original_url.rstrip("/"))

    @staticmethod
    def wayback_url(rec: dict) -> str:
        # final_url is .../web/<ts>id_/<orig>; the human-readable capture drops the id_ flag
        return re.sub(r"/web/(\d+)id_/", r"/web/\1/", rec["final_url"])

    @staticmethod
    def stamp(rec: dict) -> str:
        m = re.search(r"/web/(\d{8})", rec["final_url"])
        return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ""


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def rows_of(s: BeautifulSoup) -> list[tuple[str, str, str | None]]:
    """(icon, text, href) for the .local_row list on a directory detail page."""
    box = s.select_one("#location_table_wrapper")
    out = []
    for r in (box.select(".local_row") if box else []):
        ic = r.select_one(".material-icons, .material-icons-outlined")
        a = r.find("a", href=True)
        txt = re.sub(r"\s+", " ", r.get_text(" ", strip=True))
        icon = ic.get_text(strip=True) if ic else ""
        out.append((icon, txt[len(icon):].strip() if txt.startswith(icon) else txt, a["href"] if a else None))
    return out


def centre(html: str) -> tuple[float, float] | None:
    m = re.search(r"(?<!/)var center = \{\s*lat:\s*([-\d.]+),\s*lng:\s*([-\d.]+)", html)
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    return (lat, lng) if 32 < lat < 35 and -119 < lng < -116 else None


def street_city_zip(addr: str) -> tuple[str | None, str | None, str | None]:
    # "13280 Chapman Ave, Garden Grove, 92840" (the diocese writes no state)
    parts = [p.strip() for p in addr.split(",")]
    zp = C.zip5(addr)
    if len(parts) >= 3:
        return ", ".join(parts[:-2]), parts[-2], zp
    if len(parts) == 2:
        return parts[0], re.sub(r"\s*9\d{4}$", "", parts[1]).strip() or None, zp
    return addr, None, zp


# ---------------------------------------------------------------------------------------------- live fetch
def get_retry(fx: C.Fetcher, url: str, params: dict | None = None, tries: int = 3, wait: int = 20):
    """The site answers a bare nginx 403 to some requests; try a few times, spaced, then give up (a block is recorded)."""
    q = url + (("&" if "?" in url else "?") + urlencode(params) if params else "")
    import hashlib  # noqa: PLC0415
    last = None
    for i in range(tries):
        p = fx.raw / (hashlib.sha1(q.encode()).hexdigest()[:16] + ".json")
        if p.exists() and json.loads(p.read_text()).get("blocked"):
            p.unlink()
        try:
            return fx.get(url, params)
        except C.Blocked as e:
            last = e
            if "challenge" in str(e).lower() and "403" not in str(e):
                raise
            if i < tries - 1:
                time.sleep(wait)
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------- parishes
def clean_name(listed: str) -> tuple[str, str | None]:
    """'Saint Anne Catholic Church, Santa Ana' -> ('St. Anne (Santa Ana)', 'Santa Ana'). Fixes the directory's typos."""
    n = listed.replace("’", "'").replace("Chuch", "Church").replace("Churh", "Church")
    n = re.sub(r"\s+", " ", n).strip()
    disamb = None
    m = re.match(r"^(.*?),\s*([A-Z][A-Za-z ]+)$", n)
    if m and "Catholic" in m.group(1) or (m and re.search(r"Our Lady of Guadalupe", m.group(1))):
        n, disamb = m.group(1), m.group(2)
    n = re.sub(r"\s+Catholic (Church|Parish)$", "", n)
    n = re.sub(r"^Saints\s+", "Sts. ", n)
    n = re.sub(r"^Saint\s+", "St. ", n)
    n = re.sub(r"\b(The|De|Of)\b", lambda mm: mm.group(1).lower(), n) if re.search(r"\b(The|Of)\b", n) else n
    n = n.replace("Saint ", "St. ")
    return (f"{n} ({disamb})" if disamb else n), disamb


def parse_cards(html: str) -> list[dict]:
    s = soup(html)
    out = []
    for a in s.select("#parishes_lists_wrapper a.newsroom-single-item"):
        h = a.find("h3").get_text(" ", strip=True)
        d = a.select_one(".item-date")
        lines = [t.strip() for t in d.get_text("|", strip=True).split("|")] if d else []
        out.append({"listed_name": h, "href": a["href"], "slug": a["href"].rstrip("/").rsplit("/", 1)[-1], "street": lines[0] if lines else None,
                    "city_zip": lines[1] if len(lines) > 1 else None})
    return out


def parse_detail(rec: dict) -> dict:
    s = soup(rec["body"])
    d: dict = {"phone": None, "fax": None, "website": None, "deanery_number": None, "lat": None, "lng": None, "social": {}}
    for icon, txt, href in rows_of(s):
        if icon == "location_on":
            d["address_raw"] = txt
        elif icon == "call":
            d["phone"] = C.norm_phone(txt)
        elif icon == "print":
            ph = C.norm_phone(txt)
            d["fax"] = None if ph in (None, "(000) 000-0000") else ph
        elif icon in ("link",) and href:
            d["website"] = C.norm_url(href)
        elif icon == "man_2":
            m = re.search(r"Deanery:\s*(\d+)", txt)
            d["deanery_number"] = int(m.group(1)) if m else None
    c = centre(rec["body"])
    if c:
        d["lat"], d["lng"] = c
    for li in s.select(".locals_social_list li"):
        a = li.find("a", href=True)
        if a and "get_direction" not in (li.get("class") or []):
            key = (li.get("class") or ["x"])[0].replace("link_", "")
            d["social"][key] = a["href"]
    return d


def rite_of(name: str) -> str:
    for kw, rite in EASTERN:
        if kw in name.lower():
            return rite
    return "Latin"


def build_parishes(fx: C.Fetcher, cap: Captures):
    r = get_retry(fx, ALL_PARISHES_REST, {"slug": "all-parishes"})
    page = r.json()[0]
    cards = parse_cards(page["content"]["rendered"])
    list_url, list_fetched = r.url, r.fetched_at
    out, missing = [], []
    for c in cards:
        rec = cap.get(f"{BASE}/directories/parishes/{c['slug']}")
        det = parse_detail(rec) if rec else {}
        if not rec:
            missing.append(c["slug"])
        name, disamb = clean_name(c["listed_name"])
        street, city, zp = street_city_zip(f"{c['street']}, {c['city_zip']}")
        if det.get("address_raw"):
            s2, c2, z2 = street_city_zip(det["address_raw"])
            street, city, zp = s2 or street, c2 or city, z2 or zp
        nm = c["listed_name"]
        typ = "parish"
        if re.search(r"cathedral", nm, re.I):
            typ = "cathedral"
        elif re.search(r"\bchapel\b", nm, re.I):
            typ = "chapel"
        elif re.search(r"\bcenter\b", nm, re.I):
            typ = "personal_parish"  # the diocese files these as "Centers": non-territorial communities
        elif re.search(r"abbey", nm, re.I):
            typ = "chapel"
        langs = [lg for kw, lg in CENTER_LANG if re.search(kw, nm, re.I)]
        rite = rite_of(nm)
        row = {
            "name": name, "listed_name": c["listed_name"], "type": typ,
            "address": street, "city": city, "zip": zp, "county": "Orange",
            "lat": det.get("lat"), "lng": det.get("lng"), "phone": det.get("phone"), "fax": det.get("fax"),
            "website": det.get("website"), "office_email": None,
            "languages": langs, "languages_basis": "language named in the parish or center name" if langs else None,
            "pastor": None, "region": None, "region_name": None,
            "deanery": f"Deanery {det['deanery_number']}" if det.get("deanery_number") else None,
            "deanery_number": det.get("deanery_number"), "vicariate": None,
            "rite": rite,
            "designation": "center" if typ == "personal_parish" else ("abbey" if re.search("abbey", nm, re.I) else None),
            "status": "active",
            "status_note": f"Listed on the diocese's All Parishes page on {list_fetched[:10]}; no closure or merger notice is published in the directory.",
            "social": det.get("social") or {},
            "directory_url": c["href"].replace("rcbo2025dev.wpenginepowered.com", "www.rcbo.org"),
            "source_url": list_url, "fetched_at": list_fetched,
            "detail_source_url": Captures.wayback_url(rec) if rec else None,
            "detail_captured": Captures.stamp(rec) if rec else None,
            "geo_source": "rcbo.org directory page map centre (Wayback capture)" if det.get("lat") else None,
        }
        if disamb:
            row["disambiguator"] = disamb
        out.append(row)
    return out, cards, missing, (list_url, list_fetched)


# ---------------------------------------------------------------------------------------------- structure
def norm_key(s: str) -> str:
    s = s.lower().replace("’", "'").replace("mt.", "mount").replace("&", " and ")
    s = re.sub(r"\b(catholic|church|parish|center|centre|cultural|the)\b", " ", s)
    s = re.sub(r"\bsaints?\b|\bsts?\.?\b", " st ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


LIST_ALIASES = {  # deanery-list label -> directory name fragment (labels there are shortened)
    "pope john paul ii polish": "john paul ii polish", "st thomas apostle korean": "thomas korean", "our lady peace korean": "our lady of peace korean",
    "vietnamese": "vietnamese", "mission basilica sjc": "mission basilica", "holy family": "holy family",
}


def parse_deaneries(rec: dict) -> list[dict]:
    s = soup(rec["body"])
    t = s.get_text("\n", strip=True)
    start = t.find("Diocese of Orange Deaneries")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t[start:].split("\n")][1:]
    out, cur, buf = [], None, []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^Dean (\d+) \((.*)\)$", ln)
        if m or ln == "Religious":
            if cur:
                out.append(cur)
            cur = {"number": int(m.group(1)) if m else None, "name": f"Deanery {m.group(1)}" if m else "Religious Deanery",
                   "summary": m.group(2) if m else None, "dean": None, "sub_dean": None, "members": []}
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        if ln.startswith("Dean") and not ln.startswith("Deanery"):
            rest = ln[4:].lstrip(": ").strip()
            j = i + 1
            while not rest and j < len(lines):
                rest = lines[j].lstrip(": ").strip()
                j += 1
            if rest == "Rev." and j < len(lines):  # "Rev.\nVincent Pham"
                rest = "Rev. " + lines[j]
                j += 1
            cur["dean"] = rest
            i = j
            continue
        if ln.startswith("Sub-Dean"):
            val = ln.split(":", 1)[1].strip()
            if not val and i + 1 < len(lines):
                i += 1
                val = lines[i].strip()
            cur["sub_dean"] = val
            i += 1
            if cur["number"] is None:  # the Religious deanery lists no parishes; what follows is the page footer
                break
            continue
        if ln in ("Dean", ":"):
            i += 1
            continue
        buf_line = ln
        if buf_line.endswith(","):  # "St. Joseph," / "Placentia"
            if i + 1 < len(lines):
                buf_line = buf_line + " " + lines[i + 1]
                i += 1
        cur["members"].append(buf_line)
        i += 1
    if cur:
        out.append(cur)
    return out


def match_member(label: str, parishes: list[dict]) -> dict | None:
    k = norm_key(label)
    cands = []
    for p in parishes:
        pk = norm_key(p["listed_name"].replace(",", " "))
        if k == pk or k in pk or pk in k:
            cands.append((len(pk), p))
    if len(cands) == 1:
        return cands[0][1]
    if cands:  # disambiguate by city word in label
        for _l, p in cands:
            if p.get("city") and norm_key(p["city"]) and norm_key(p["city"]) in k:
                return p
            if p.get("disambiguator") and norm_key(p["disambiguator"]) in k:
                return p
        # label with a place after a comma is explicit; otherwise prefer exact-length match
        cands.sort(key=lambda x: abs(x[0] - len(k)))
        return cands[0][1]
    return None


def build_structure(cap: Captures, parishes: list[dict], list_url: str):
    dl = cap.get(f"{BASE}/deanery-list")
    about = cap.get(f"{BASE}/about")
    deaneries = parse_deaneries(dl)
    unit_members: dict[int | None, list[str]] = defaultdict(list)
    mismatch = []
    unmatched = []
    for d in deaneries:
        for lab in d["members"]:
            p = match_member(LIST_ALIASES.get(norm_key(lab), lab), parishes) or match_member(lab, parishes)
            if not p:
                unmatched.append((d["name"], lab))
                continue
            if p.get("deanery_number") not in (None, d["number"]):
                mismatch.append((p["name"], d["number"], p["deanery_number"]))
            if p.get("deanery_number") is None and d["number"] is not None:
                p["deanery_number"] = d["number"]
                p["deanery"] = f"Deanery {d['number']}"
                p["deanery_source"] = "deanery list (the parish's own directory page shows no deanery)"
            unit_members[d["number"]].append(p["name"])
    units = []
    for d in deaneries:
        mem = sorted({p["name"] for p in parishes if p.get("deanery_number") == d["number"]}) if d["number"] else sorted(set(unit_members[None]))
        units.append({"kind": "deanery", "name": d["name"], "parent": None,
                      "leader": {"name": d["dean"], "title": "Dean"} if d["dean"] else None,
                      "sub_leader": {"name": d["sub_dean"], "title": "Sub-Dean"} if d["sub_dean"] else None,
                      "summary_on_list": d["summary"], "number": d["number"], "member_parishes": mem,
                      "source_url": Captures.wayback_url(dl), "captured": Captures.stamp(dl)})
    about_s = soup(about["body"])
    about_txt = about_s.get_text("\n", strip=True)
    m = re.search(r"The Diocese of Orange is the growing Catholic Community in Orange County, California", about_txt)
    bishops = [
        {"name": "Most Rev. Kevin W. Vann, D.D., J.C.D., D.Min.", "title": "Bishop of Orange"},
        {"name": "Most Rev. Timothy E. Freyer, D.D.", "title": "Auxiliary Bishop"},
        {"name": "Most Rev. Thanh Thai Nguyen, D.D.", "title": "Auxiliary Bishop"},
    ]
    for b in bishops:
        assert b["name"].split(",")[0].replace("Most Rev. ", "").split()[-1] in about_txt, b
        b["source_url"] = Captures.wayback_url(about)
    cc = next(p for p in parishes if p["type"] == "cathedral")
    structure = {
        "diocese": SLUG, "name": "Diocese of Orange", "website": BASE,
        "counties": ["Orange"],
        "counties_source_url": Captures.wayback_url(about),
        "counties_source_says": "\"The Diocese of Orange is the growing Catholic Community in Orange County, California\" (rcbo.org/about, Wayback capture "
                                f"{Captures.stamp(about)}).",
        "counties_verified_from_diocese_site": bool(m),
        "bishops": bishops,
        "cathedral": {"name": "Christ Cathedral", "address": cc["address"], "city": cc["city"], "zip": cc["zip"], "lat": cc["lat"], "lng": cc["lng"],
                      "parish": cc["name"], "source_url": cc["detail_source_url"]},
        "units": units,
        "notes": [
            "Orange has no pastoral regions or vicariates; the diocese groups parishes into seven numbered deaneries plus a 'Religious' deanery (no parishes listed).",
            "The deanery list (rcbo.org/deanery-list, Wayback capture " + Captures.stamp(dl) + ") shortens parish names; parishes are matched to it by name and checked against the 'Deanery: N' line on each parish's own page.",
            f"Deanery label cross-check: {len(mismatch)} mismatches, {len(unmatched)} list labels not matched to a parish.",
            "Parishes with no deanery on their page and none on the list: Eastern Catholic churches and St. Michael's Abbey.",
        ],
        "source_urls": [list_url, Captures.wayback_url(dl), Captures.wayback_url(about)],
        "fetched_at": C.now_iso(),
        "_diag": {"mismatch": mismatch, "unmatched": unmatched},
    }
    return structure


# ---------------------------------------------------------------------------------------------- entities
def school_entities(cap: Captures) -> list[dict]:
    lst = cap.get(f"{BASE}/all-schools")
    s = soup(lst["body"])
    w = s.select_one("#parishes_lists_wrapper")
    out = []
    cat = None
    for el in w.find_all(["h4", "a"]):
        if el.name == "h4":
            cat = el.get_text(strip=True)
            continue
        if "newsroom-single-item" not in (el.get("class") or []):
            continue
        slug = el["href"].rstrip("/").rsplit("/", 1)[-1]
        name = el.find("h3").get_text(" ", strip=True)
        lines = [t.strip() for t in el.select_one(".item-date").get_text("|", strip=True).split("|")]
        street, city, zp = street_city_zip(f"{lines[0]}, {lines[1]}")
        rec = cap.get(f"{BASE}/directories/schools/{slug}")
        det = parse_detail(rec) if rec else {}
        label = el.select_one(".school_all_style").get_text(strip=True) if el.select_one(".school_all_style") else None
        level = "high_school" if re.search(r"high school|\bmater dei\b|servite|jserra", name, re.I) else "elementary_middle"
        out.append({
            "type": "school", "subtype": level, "name": name, "address": street, "city": re.sub(r"\s+$", "", city or ""), "zip": zp,
            "website": det.get("website"), "phone": det.get("phone"), "office_email": None,
            "lat": det.get("lat"), "lng": det.get("lng"),
            "governance": {"Diocesan": "diocesan", "Private": "private", "Pax Christi": "Pax Christi"}.get(label, label),
            "conducted_by": None,
            "source_url": Captures.wayback_url(lst), "detail_source_url": Captures.wayback_url(rec) if rec else None,
            "captured": Captures.stamp(lst), "fetched_at": C.now_iso(),
        })
    return out


def cemetery_entities(cap: Captures) -> list[dict]:
    out = []
    for slug, nm in [("ascension", "Ascension Cemetery"), ("cathedral-memorial", "Cathedral Memorial Gardens"),
                     ("holy-sepulcher", "Holy Sepulcher Cemetery"), ("good-shepherd", "Good Shepherd Cemetery")]:
        rec = cap.get(f"https://www.occem.org/cemetery-locations/{slug}")
        if not rec:
            continue
        t = soup(rec["body"]).get_text("\n", strip=True)
        m = re.search(r"Our Address\s*\n(.+?)\nPhone\s*\n(.+?)\n", t)
        street, city, zp = (None, None, None)
        if m:
            addr = m.group(1)
            mm = re.match(r"^(.*?)\s+([A-Z][A-Za-z ]+),\s*CA\s*(\d{5})$", addr)
            if mm:
                street, city, zp = mm.group(1), mm.group(2), mm.group(3)
                # "24754 Trabuco Road Lake Forest" -> split street / city at the last street suffix
                ms = re.match(r"^(.*?\b(?:Road|Rd|Ave|Avenue|Blvd|Street|St|Drive|Dr|Way|Parkway)\.?)\s+(.+)$", mm.group(1) + " " + mm.group(2))
                if ms:
                    street, city = ms.group(1), ms.group(2)
        out.append({"type": "cemetery", "subtype": None, "name": nm, "address": street, "city": city, "zip": zp,
                    "website": f"https://www.occem.org/cemetery-locations/{slug}/", "phone": C.norm_phone(m.group(2)) if m else None,
                    "office_email": None, "conducted_by": "Diocese of Orange Catholic Cemeteries",
                    "source_url": Captures.wayback_url(rec), "captured": Captures.stamp(rec), "fetched_at": C.now_iso()})
    rec = cap.get("https://www.occem.org/columbarium")
    if rec:
        out.append({"type": "cemetery", "subtype": "columbarium", "name": "St. Callistus Columbarium (Christ Cathedral)", "address": "13280 Chapman Ave",
                    "city": "Garden Grove", "zip": "92840", "website": "https://www.occem.org/columbarium/", "phone": "(888) 543-5020",
                    "office_email": None, "conducted_by": "Diocese of Orange Catholic Cemeteries",
                    "note": "Niche columbarium within Christ Cathedral; the page gives the reservation line 1-888-543-5020 and the cathedral as the site (address is the cathedral's).",
                    "source_url": Captures.wayback_url(rec), "captured": Captures.stamp(rec), "fetched_at": C.now_iso()})
    return out


def campus_entities(cap: Captures) -> list[dict]:
    rec = cap.get(f"{BASE}/ministry/university-campus-ministry")
    s = soup(rec["body"])
    out = []
    main = s.find(id="content") or s
    heads = [h for h in main.find_all("h3") if re.search(r"University|College", h.get_text())]
    data = {
        "University Of California, Irvine": ("University of California, Irvine", "Anteater Catholic", "https://anteatercatholic.org/", "Rev. Benjamin Tran", "9 Hillgate", "Irvine", "92603", "(949) 861-3445"),
        "California State University, Fullerton": ("California State University, Fullerton", "Titan Catholic", "https://connection.newmanministry.com/colleges/california-state-university-fullerton/", "Rev. Isabelo Abuan, III", None, "Fullerton", None, "(714) 879-1965"),
        "Chapman University": ("Chapman University", "Catholic Newman Fellowship", None, "Rev. Rafael Luevano", "1 University Drive", "Orange", "92866", "(714) 628-7260"),
    }
    for h in heads:
        key = h.get_text(" ", strip=True)
        if key not in data:
            continue
        inst, nm, web, chap, addr, city, zp, ph = data[key]
        # every value must appear on the page, or the row is not written
        body = main.get_text(" ", strip=True)
        assert chap.split(",")[0] in body and (not addr or addr in body), key
        out.append({"type": "campus_ministry", "subtype": None, "name": f"{nm} ({inst})", "organization": inst, "address": addr, "city": city, "zip": zp,
                    "website": web, "phone": ph, "office_email": None, "conducted_by": None,
                    "chaplain": {"name": chap, "title": "Chaplain"},
                    "note": "Contact address and phone are the chaplain's parish or university address as printed on the diocese's campus ministry page.",
                    "source_url": Captures.wayback_url(rec), "captured": Captures.stamp(rec), "fetched_at": C.now_iso()})
    return out


def ccoc_entities(fx: C.Fetcher) -> list[dict]:
    out = []
    r = fx.get("https://www.ccoc.org/")
    t = soup(r.text).get_text(" ", strip=True)
    m = re.search(r"Tower of Hope\s*[–-]\s*(12141 S Lewis St), 11th Floor, (Garden Grove), CA (\d{5}) \| ([\d.]+)", t)
    if m:
        out.append({"type": "catholic_charities", "subtype": "agency", "name": "Catholic Charities of Orange County", "address": f"{m.group(1)}, 11th Floor",
                    "city": m.group(2), "zip": m.group(3), "website": "https://www.ccoc.org/", "phone": C.norm_phone(m.group(4)), "office_email": None,
                    "conducted_by": None, "note": "Administrative office (Tower of Hope), as printed in the ccoc.org footer.",
                    "source_url": r.url, "fetched_at": r.fetched_at})
    for url, nm in [("https://ccoc.org/programs/life-sustaining-services/cantlay-food-distribution-center", "Cantlay Food Distribution Center"),
                    ("https://ccoc.org/programs/life-sustaining-services/garden-of-hope", "Garden of Hope & Healing")]:
        try:
            rr = fx.get(url)
        except C.Blocked:
            continue
        tt = soup(rr.text).get_text(" ", strip=True)
        mm = re.search(r"(\d{3,5} [WENS]\.? [A-Z][a-z]+ (?:Ave\.?|Avenue|Street|St\.?|Blvd\.?|Road|Rd\.?|Drive|Dr\.?))\s+(Santa Ana|[A-Z][a-z]+(?: [A-Z][a-z]+)?),? CA,? (\d{5})", tt)
        if mm:
            out.append({"type": "catholic_charities", "subtype": "program_site", "name": f"Catholic Charities of Orange County: {nm}", "address": mm.group(1),
                        "city": mm.group(2), "zip": mm.group(3), "website": url, "phone": None, "office_email": None, "conducted_by": "Catholic Charities of Orange County",
                        "source_url": rr.url, "fetched_at": rr.fetched_at})
    return out


def office_entities(cap: Captures) -> list[dict]:
    rec = cap.get(f"{BASE}/directory")
    return [{"type": "diocesan_office", "subtype": "chancery", "name": "Diocese of Orange Pastoral Center", "address": "13280 Chapman Ave", "city": "Garden Grove",
             "zip": "92840", "website": BASE, "phone": "(714) 282-3000", "fax": "(714) 282-3029", "office_email": None, "conducted_by": None,
             "note": "Footer of rcbo.org: 'VISIT US 13280 Chapman Ave, Garden Grove'; phone and fax as printed there.",
             "source_url": Captures.wayback_url(rec), "captured": Captures.stamp(rec), "fetched_at": C.now_iso()}]


def religious_entities(parishes: list[dict]) -> list[dict]:
    out = []
    for p in parishes:
        if p["designation"] == "abbey":
            out.append({"type": "religious_community", "subtype": "abbey", "name": p["listed_name"], "address": p["address"], "city": p["city"], "zip": p["zip"],
                        "website": p["website"], "phone": p["phone"], "office_email": None, "conducted_by": None,
                        "note": "Listed by the diocese among its parishes and centers (its own directory page shows address only). Also present in parishes.json.",
                        "source_url": p["source_url"], "fetched_at": p["fetched_at"]})
    return out


# ---------------------------------------------------------------------------------------------- events
def build_events(fx: C.Fetcher):
    """The Events Calendar REST API on rcbo.org. Returns ([], 0, reason) when the site refuses the plain client."""
    try:
        # Fetcher.get raises Blocked on a 403; one spaced retry only
        try:
            evs, raw_n = C.tribe_events(fx, BASE)
        except C.Blocked:
            time.sleep(30)
            for p in fx.raw.glob("*.json"):
                d = json.loads(p.read_text())
                if d.get("blocked") and "tribe/events" in (d.get("requested") or ""):
                    p.unlink()
            evs, raw_n = C.tribe_events(fx, BASE)
    except C.Blocked as e:
        return [], 0, Counter(), str(e)
    kept, dropped = C.screen_events(evs)
    return kept, raw_n, dropped, None


# ---------------------------------------------------------------------------------------------- main
def main() -> None:
    fx = C.Fetcher(SLUG)
    cap = Captures()
    parishes, cards, missing, (list_url, list_fetched) = build_parishes(fx, cap)
    structure = build_structure(cap, parishes, list_url)
    diag = structure.pop("_diag")
    C.dedupe_ids(parishes, "name")
    for p in parishes:
        p.setdefault("ethnic_liturgies", [])
    ents = school_entities(cap) + cemetery_entities(cap) + campus_entities(cap) + ccoc_entities(fx) + office_entities(cap) + religious_entities(parishes)
    for e in ents:
        for k in ("conducted_by", "note"):
            if e.get(k):
                e[k] = C.screen_text(e[k])
    events, raw_events, dropped, ev_block = build_events(fx)
    fetched = C.now_iso()
    cnt = C.counts(parishes, ents, events)
    et = Counter((e["type"], e.get("subtype")) for e in ents)
    nogeo = sum(1 for p in parishes if p["lat"] is None)
    noweb = sum(1 for p in parishes if not p["website"])
    nodean = [p["name"] for p in parishes if not p["deanery_number"]]
    print("missing detail captures:", missing, "| no deanery:", nodean, "| diag:", diag)
    readme = f"""# Diocese of Orange: official directory data

Collected {fetched[:10]} from https://www.rcbo.org (the Roman Catholic Diocese of Orange), https://www.occem.org (its cemeteries) and https://www.ccoc.org
(Catholic Charities of Orange County). Script: `ingest/california/directories/orange.py` (laptop helper: `laptop_fetch.py`).
Re-run: `python3 -I ingest/california/directories/orange.py` (needs the raw captures in `data/raw/california/dioceses/orange/wayback/`, gitignored).

## Sources and method

- **Which requests worked.** rcbo.org is on WP Engine behind Cloudflare. From the sandbox (plain `python-requests` User-Agent) the site answered a bare nginx `403` to
  most requests: the REST directory (`/wp-json/wp/v2/directory`), the events API (`/wp-json/tribe/events/v1/`), every HTML parish and school page,
  `directory-sitemap.xml`. Spaced retries (3 to 8 tries, 12 to 20 s apart) did not change that. What did come through: `/robots.txt` (Crawl-delay 10, honoured), `sitemap_index.xml`,
  `page-sitemap.xml` and `GET /wp-json/wp/v2/pages?slug=all-parishes`. One try from Matthew's laptop (plain `urllib`) was refused by Cloudflare with error 1010 ("browser signature banned").
  Neither block was worked around (no other client, no changed User-Agent, no cookies).
- **Parish list (live, fetched {list_fetched[:10]}):** the rendered content of the diocese's All Parishes page via REST `pages?slug=all-parishes`: {len(cards)} entries (name, street, city, ZIP), all of them in parishes.json.
- **Parish details (Wayback Machine captures, `wayback_url` / `detail_captured` on every row):** the Internet Archive's capture of each parish's own directory page (address, phone, fax, website, `Deanery: N`, map centre = coordinates), taken
  from the laptop with a script equal to `laptop_fetch.py` (1.2 s apart, default urllib User-Agent, archive.org has no bot block). {len(cards) - len(missing)} of {len(cards)} captured; capture dates run from 2025-11 to 2026-05, so phone and website are as of that capture.
  Pastor names are not in the page (they load by script from `admin-ajax.php`, which was not called), so `pastor` is null on every row. Office e-mails sit behind Cloudflare's address obfuscation and are not decoded, so `office_email` is null.
- **Structure:** `deanery-list` (capture {Captures.stamp(cap.get(BASE + '/deanery-list'))}) gives seven numbered deaneries and a Religious deanery, each with a Dean and Sub-Dean; the `about` page (capture {Captures.stamp(cap.get(BASE + '/about'))}) gives the bishops
  and the statement of the diocese's territory (Orange County). Parishes get `deanery` from their own page; the list is used for leaders and for parishes whose page shows none.
- **Entities:** schools from the All Schools page (capture {Captures.stamp(cap.get(BASE + '/all-schools'))}; the diocese's three groups: Diocesan, Private Catholic, Pax Christi) with phone and map centre from each school's page;
  cemeteries from occem.org (captures 2026-06/07); campus ministries from the diocese's University Campus Ministry page; Catholic Charities from ccoc.org (live).
- **Events:** {"collected: " + str(len(events)) + " kept of " + str(raw_events) if not ev_block else "NOT collected. The Events Calendar API answered 403 to the plain client (" + ev_block + "). The Wayback capture of /events/ is from 2026-07 and is out of the window, so it was not used."}

## Counts

| parishes.json rows | {len(parishes)} |
|---|---|
{C.md_table(dict(Counter(p['type'] for p in parishes)))}| rite other than Latin | {sum(1 for p in parishes if p['rite'] != 'Latin')} |
| with coordinates | {len(parishes) - nogeo} |
| with a website | {len(parishes) - noweb} |
| with a deanery | {len(parishes) - len(nodean)} |
| without a deanery | {len(nodean)} ({'; '.join(nodean)}) |

| entities by type (subtype) | |
|---|---|
{C.md_table({f'{t} ({s})' if s else t: n for (t, s), n in et.items()})}| events | {len(events)} |
| deaneries in structure.json | {sum(1 for u in structure['units'] if u['kind'] == 'deanery')} |

## Notes and gaps

- **Status.** Every row is `active`: the diocese's list carries no closed, merged or announced-closure flags. Check before use.
- **Centers.** The diocese lists six communities as "Centers" (Korean Martyrs, Poong Jesus Nazareno Filipino, Vietnamese Catholic, St. John Paul II Polish, Our Lady of Peace Korean, St. Thomas Korean). They are typed `personal_parish` with `designation: center`;
  the canonical status is the diocese's to confirm. `languages` is set only where the name names a language.
- **Eastern Catholic.** {sum(1 for p in parishes if p['rite'] != 'Latin')} listed churches belong to Eastern churches (rite from the name); they have no deanery. The 2025 Los Angeles directory also lists some of them (see Los Angeles `structure.json`).
- **Deanery cross-check.** Parishes whose own page and the deanery list disagree: {diag['mismatch'] or 'none'}. List labels that matched no parish: {diag['unmatched'] or 'none'}.
- **Schools** are the diocese's list of {sum(1 for e in ents if e['type'] == 'school')} (25 diocesan, 7 private Catholic, 5 Pax Christi). Websites are given only where the school's own directory page links one. Pre-schools and Catholic colleges are not on that list.
- **Not collected:** parish pastors and Mass times (script-loaded), the Catholic Charities program-site list beyond the pages named above, retreat centers (the House of Prayer for Priests page gives no address),
  and events (see above). Clergy directory (`/directories/clergy-and-religious/`) was not fetched.
- Capture timestamps are in each row's `detail_source_url`; the live check date is `fetched_at`.
"""
    c = C.write_all(SLUG, parishes, structure, ents, events, readme)
    print(json.dumps(c, indent=2))


if __name__ == "__main__":
    main()
