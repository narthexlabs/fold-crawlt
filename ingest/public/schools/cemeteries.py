"""Catholic Funeral & Cemetery Services (cfcsoakland.org) locations -> data/real/cemeteries.json

Each /location/<slug>/ page carries a schema.org LocalBusiness JSON-LD block (name, street,
city, zip, phone). Its "geo" coordinates are copy-pasted across pages (Holy Sepulchre in Hayward
is placed in Dublin), so they are kept as site_geo but not used; geocode.py adds Census coordinates.

Run: python -I ingest/public/schools/cemeteries.py
"""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schools import REAL, SESSION, now_iso, save_json  # noqa: E402

HOME = "https://www.cfcsoakland.org/"


def kind_of(name: str, slug: str) -> str:
    if "mausoleum" in name.lower():
        return "mausoleum"
    if "chapel" in name.lower():
        return "funeral chapel"
    if "cemetery" in name.lower() or "mission san jose" in name.lower():
        return "cemetery"
    return "other"


def parse_locations_page(html: str) -> list[dict]:
    """The /locations/ page lists every site as: name, [FDnnnn], street, City, CA zip, phone."""
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html)
    lines = [unescape(x).strip() for x in re.sub(r"<[^>]+>", "\n", s).split("\n")]
    lines = [x for x in lines if x]
    i = next(k for k, x in enumerate(lines) if x.startswith("For more information regarding each"))
    out, k = [], i + 1
    while k < len(lines) - 3:
        m_city = None
        for j in range(k + 1, min(k + 4, len(lines))):
            m_city = re.match(r"^(.+?), CA (\d{5})$", lines[j])
            if m_city:
                break
        if not m_city or not re.match(r"^\(\d{3}\) \d{3}-\d{4}$", lines[j + 1]):
            break
        head = lines[k:j - 1]
        name = head[0]
        lic = next((re.sub(r"\s", "", x) for x in head[1:] + [name] if re.search(r"FD ?\d+", x)), None)
        lic = re.search(r"FD ?\d+", lic).group(0).replace(" ", "") if lic else None
        name = re.sub(r"\s*FD ?\d+$", "", name).strip()
        out.append({"name": name, "license": lic, "address": lines[j - 1].rstrip(" ."),
                    "city": m_city.group(1), "zip": m_city.group(2), "phone": lines[j + 1]})
        k = j + 2
    return out


def main() -> None:
    fetched_at = now_iso()
    locs_url = HOME + "locations/"
    locs = parse_locations_page(SESSION.get(locs_url, timeout=30).text)
    home = SESSION.get(HOME, timeout=30).text
    links = sorted(set(re.findall(r'href="(https://www\.cfcsoakland\.org/location/[^"#?]+/)"', home)))
    recs = []
    for loc in locs:
        key = re.sub(r"[^a-z]", "", loc["name"].lower().split(",")[0])
        url = next((u for u in links if key[:10] in re.sub(r"[^a-z]", "", u.lower())), None)
        biz = None
        if url:
            h = SESSION.get(url, timeout=30).text
            for blob in re.findall(r'<script type="application/ld\+json"[^>]*>([\s\S]*?)</script>', h):
                try:
                    d = json.loads(blob)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("@type") == "LocalBusiness":
                    biz = d
                    break
        name = re.sub(r",\s*[A-Za-z ]+, CA$", "", loc["name"]).strip()
        recs.append({
            "name": name,
            "kind": kind_of(name, url or ""),
            "funeral_center": bool(loc["license"]),
            "ca_funeral_license": loc["license"],
            "address": loc["address"], "city": loc["city"], "state": "CA", "zip": loc["zip"],
            "county": "Contra Costa" if loc["city"] in ("Antioch", "Lafayette", "San Pablo") else "Alameda",
            "phone": loc["phone"],
            "hours": (biz or {}).get("openingHoursSpecification"),
            "site_geo": (biz or {}).get("geo"),
            "detail_url": url,
            "operator": "Catholic Funeral & Cemetery Services (Diocese of Oakland)",
            "source_url": locs_url, "fetched_at": fetched_at,
        })
    save_json(REAL / "cemeteries.json", {
        "source": "Catholic Funeral & Cemetery Services of the Diocese of Oakland", "source_page": locs_url,
        "site_claim": "eight funeral centers and nine cemeteries",
        "notes": ["site_geo comes from per-location JSON-LD and is unreliable (coordinates repeated across "
                  "locations); use lat/lng from the Census geocoder.",
                  "kind=funeral chapel rows (Cooper, Sorensen) are funeral homes, not cemeteries.",
                  "The site claims nine cemeteries but lists 8 burial sites (7 cemeteries + the Cathedral mausoleum)."],
        "counts": {k: sum(1 for r in recs if r["kind"] == k) for k in {r["kind"] for r in recs}},
        "fetched_at": fetched_at, "records": recs,
    })
    for r in recs:
        print(f"{r['name']:45} {r['kind']:15} {r['address']}, {r['city']} {r['zip']} {r['phone']} {r['ca_funeral_license']}")


if __name__ == "__main__":
    main()
