"""Pull every parish/mission profile from oakdiocese.org into data/real/parishes_directory.json.

Strategy:
  1. Try the WordPress REST API (looks for a parish/bio/location post type in /wp/v2/types,
     plus a direct probe of the `bbwp_bios` CPT the site uses).
  2. Fall back to crawling https://www.oakdiocese.org/parishes/ (paginated cards) and each
     /bios/<slug>/ profile page for region/deanery, clergy and Mass times.
  3. Geocode with the Census one-line geocoder (cached in data/raw/geocode_cache.json).

Usage: python ingest/public/oakdiocese_directory.py [--no-geocode]
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REAL, census_geocode, get, now_iso, save_json, slug  # noqa: E402

BASE = "https://www.oakdiocese.org"
LIST_URL = BASE + "/parishes/"
OUT = REAL / "parishes_directory.json"
DELAY = 0.6

CLERGY_RE = re.compile(r"^(Father|Fr\.?|Rev\.?|Reverend|Msgr\.?|Monsignor|Deacon|Dcn\.?|Bishop|Sister|Sr\.?|Brother|Br\.?|Mr\.?|Mrs\.?|Ms\.?|Very Rev)\b", re.I)
LANGS = ["English", "Spanish", "Vietnamese", "Tagalog", "Filipino", "Portuguese", "Chinese", "Cantonese",
         "Mandarin", "Korean", "Polish", "Latin", "Italian", "Tongan", "Samoan", "Eritrean", "Ge'ez",
         "Igbo", "French", "Croatian", "Hungarian", "Indonesian", "Japanese", "Malayalam", "Syro-Malabar",
         "Ilocano", "Arabic", "Burmese", "Haitian", "Creole", "Fijian", "ASL", "Sign Language", "Swahili",
         "Lithuanian", "German", "Ukrainian"]


def get_retry(url: str, tries: int = 5):
    """The site's WAF intermittently answers 403; back off and retry."""
    r = None
    for attempt in range(tries):
        r = get(url, timeout=30)
        if r is not None and (r.ok or r.status_code == 404):
            return r
        time.sleep(4 * (attempt + 1))
    return r


def try_rest() -> list[dict] | None:
    """Return raw REST items if a parish-ish post type is exposed, else None."""
    r = get(BASE + "/wp-json/wp/v2/types")
    bases = []
    if r is not None and r.ok:
        try:
            for k, v in r.json().items():
                if re.search(r"parish|bio|location|church|directory", k + " " + str(v.get("name")), re.I):
                    bases.append(v.get("rest_base") or k)
        except ValueError:
            pass
    bases.append("bbwp_bios")
    for b in dict.fromkeys(bases):
        items, page = [], 1
        while True:
            rr = get(f"{BASE}/wp-json/wp/v2/{b}?per_page=100&page={page}")
            if rr is None or not rr.ok:
                break
            try:
                batch = rr.json()
            except ValueError:
                break
            if not isinstance(batch, list) or not batch:
                break
            items += batch
            if page >= int(rr.headers.get("X-WP-TotalPages", 1)):
                break
            page += 1
        if items:
            print(f"REST: {len(items)} items from /wp/v2/{b}")
            return items
    print("REST: no parish post type exposed (bbwp_bios not in REST) -> HTML fallback")
    return None


def norm_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    if not u or u.startswith(("mailto:", "tel:", "javascript:")):
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    return u


def parse_cards(soup: BeautifulSoup, page_url: str) -> list[dict]:
    out = []
    for post in soup.select("div.pp-content-post"):
        classes = " ".join(post.get("class", []))
        if "bios_departments-parishes" not in classes and "bbwp_bios" not in classes:
            continue
        h3 = post.select_one(".listing h3")
        if not h3:
            continue
        prof = post.select_one("a.bio-popup")
        des = post.select_one("h4.designation")
        add = post.select_one("h5.addtional-info, h5.additional-info")
        tel = post.select_one(".meta.phone a")
        mail = post.select_one(".meta.email a")
        web = post.select_one(".meta.website a")
        out.append({
            "name": html.unescape(h3.get_text(" ", strip=True)),
            "designation": des.get_text(" ", strip=True) if des else None,
            "additional_info": add.get_text(" ", strip=True) if add else None,
            "phone": tel.get("href", "")[4:].strip() if tel else None,
            "email": mail.get("href", "")[7:].strip() if mail else None,
            "website": norm_url(web.get("href")) if web else None,
            "profile_url": prof.get("href") if prof else None,
            "wp_post_id": post.get("data-id"),
            "listing_url": page_url,
        })
    return out


def crawl_listing() -> list[dict]:
    cards, seen, page = [], set(), 1
    while page < 40:
        url = LIST_URL if page == 1 else f"{LIST_URL}page/{page}/"
        r = get_retry(url)
        if r is None or not r.ok:
            print(f"listing page {page}: HTTP {getattr(r, 'status_code', 'fail')} -> stop")
            break
        new = [c for c in parse_cards(BeautifulSoup(r.text, "html.parser"), url)
               if (c["profile_url"] or c["name"]) not in seen]
        if not new:
            break
        for c in new:
            seen.add(c["profile_url"] or c["name"])
        cards += new
        print(f"listing page {page}: +{len(new)} (total {len(cards)})")
        page += 1
        time.sleep(DELAY)
    return cards


def split_address(designation: str | None) -> tuple[str | None, str | None, str | None]:
    """'1145 Gilman St., Berkeley' -> (street, city, zip)."""
    if not designation:
        return None, None, None
    d = re.sub(r"\s+", " ", designation).strip()
    zm = re.search(r"\b(9\d{4})(?:-\d{4})?\b", d)
    zp = zm.group(1) if zm else None
    d2 = re.sub(r",?\s*(CA|California)?\s*9\d{4}(-\d{4})?\s*$", "", d).strip(" ,")
    d2 = re.sub(r",\s*(CA|California)\s*$", "", d2).strip(" ,")
    if "," in d2:
        street, city = d2.rsplit(",", 1)
        return street.strip(), city.strip(), zp
    return d2, None, zp


def parse_profile(url: str) -> dict:
    r = get_retry(url)
    if r is None or not r.ok:
        return {"profile_error": f"HTTP {getattr(r, 'status_code', 'fail')}"}
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict = {}
    blocks = soup.select(".fl-module-rich-text")
    # the profile body is the rich-text module inside the page content, before the footer
    body = None
    for b in blocks:
        if b.find_parent("footer") or b.find_parent("header"):
            continue
        body = b
        break
    lat = re.search(r'"?lat(?:itude)?"?\s*[:=]\s*"?(-?\d{1,2}\.\d{3,})', r.text)
    lng = re.search(r'"?(?:lng|lon|longitude)"?\s*[:=]\s*"?(-?\d{2,3}\.\d{3,})', r.text)
    if lat and lng:
        out["lat"], out["lng"] = float(lat.group(1)), float(lng.group(1))
    if not body:
        return out
    paras = []
    for p in body.find_all(["p", "div", "li"], recursive=True):
        if p.name == "div" and p.find("p"):
            continue
        for br in p.find_all("br"):
            br.replace_with("\n")
        t = p.get_text("", strip=False).strip()
        if t:
            paras.append([ln.strip() for ln in t.split("\n") if ln.strip()])
    out["profile_text"] = "\n\n".join("\n".join(p) for p in paras)
    clergy, mass, region_line, other = [], [], None, []
    for lines in paras:
        head = lines[0].lower()
        if head.startswith("mass") or any(re.search(r"\b(sunday|saturday|weekday|daily|vigil)\b.*\d", ln, re.I) for ln in lines[:2]):
            mass += [ln for ln in lines if not ln.lower().rstrip(":") in ("masses", "mass times", "mass schedule")]
        elif re.search(r"region|deanery", lines[0], re.I):
            region_line = lines[0]
            other += lines[1:]
        elif sum(bool(CLERGY_RE.match(ln)) for ln in lines) >= max(1, len(lines) // 2):
            clergy += lines
        else:
            other += lines
    if region_line:
        region_line = re.sub(r"\s+", " ", region_line).strip()
    out["region_line"] = region_line
    if region_line:
        rm = re.search(r"([A-Za-z]+(?: [A-Za-z]+)?) Region(?: \((\d+)\))?", region_line)
        dm = re.search(r"Deanery\s*(\d+)", region_line, re.I)
        cm = re.search(r"clustered with (.+)$", region_line, re.I)
        out["region"] = rm.group(1).strip() + " Region" if rm else None
        out["region_number"] = int(rm.group(2)) if rm and rm.group(2) else None
        out["deanery"] = int(dm.group(1)) if dm else None
        out["clustered_with"] = cm.group(1).strip() if cm else None
    out["clergy"] = clergy
    pastor = None
    for ln in clergy:
        m = re.search(r"(pastor|administrator|rector|parochial administrator|moderator)", ln, re.I)
        if m:
            pastor = {"name": re.sub(r",?\s*\(?(pastor|administrator|rector|parochial administrator|moderator)\)?.*$", "", ln, flags=re.I).strip(" ,-"),
                      "title": m.group(1).title()}
            break
    if not pastor and clergy and re.match(r"^(Father|Fr|Rev|Msgr|Monsignor|Very Rev)", clergy[0], re.I):
        pastor = {"name": clergy[0], "title": "Pastor/Administrator (inferred: first listed priest)"}
    out["pastor"] = pastor
    out["mass_times_text"] = "\n".join(mass) or None
    langs = []
    for ln in mass + other:
        for L in LANGS:
            if re.search(r"\b" + re.escape(L) + r"\b", ln, re.I) and L not in langs:
                langs.append(L)
    out["languages"] = langs
    out["other_notes"] = other or None
    return out


def build(cards: list[dict], geocode: bool) -> list[dict]:
    recs, ids = [], {}
    for i, c in enumerate(cards, 1):
        street, city, zp = split_address(c["designation"])
        prof = parse_profile(c["profile_url"]) if c["profile_url"] else {}
        time.sleep(DELAY)
        name = c["name"]
        kind = "mission" if re.search(r"\bmission\b", name, re.I) else (
            "campus_ministry" if re.search(r"newman", name, re.I) else "parish")
        rid = re.sub(r"\b(\w+)-\1\b", r"\1", slug(name, city))  # e.g. our-our-lady -> our-lady
        if rid in ids:
            ids[rid] += 1
            rid = f"{rid}-{ids[rid]}"
        else:
            ids[rid] = 1
        fetched = now_iso()
        rec = {
            "id": rid,
            "name": name,
            "kind": kind,
            "address": street,
            "city": city,
            "state": "CA",
            "zip": zp,
            "address_raw": c["designation"],
            "phone": c["phone"],
            "email": c["email"],
            "website": c["website"],
            "pastor": prof.get("pastor"),
            "clergy": prof.get("clergy") or [],
            "region": prof.get("region"),
            "region_number": prof.get("region_number"),
            "deanery": prof.get("deanery"),
            "clustered_with": prof.get("clustered_with"),
            "region_line": prof.get("region_line"),
            "languages": prof.get("languages") or [],
            "mass_times_text": prof.get("mass_times_text"),
            "other_notes": prof.get("other_notes"),
            "additional_info": c["additional_info"],
            "profile_text": prof.get("profile_text"),
            "lat": prof.get("lat"),
            "lng": prof.get("lng"),
            "geo_source": "oakdiocese.org" if prof.get("lat") else None,
            "wp_post_id": c["wp_post_id"],
            "source_url": c["profile_url"] or c["listing_url"],
            "listing_url": c["listing_url"],
            "fetched_at": fetched,
        }
        if prof.get("profile_error"):
            rec["profile_error"] = prof["profile_error"]
        if geocode and rec["lat"] is None and street and city:
            q = f"{street}, {city}, CA {zp or ''}".strip()
            g = census_geocode(q)
            if g:
                rec.update(lat=g["lat"], lng=g["lng"], geo_source="census",
                           geo_matched_address=g["matched_address"], geo_source_url=g["source_url"])
                zm = re.search(r"\b(9\d{4})\s*$", g["matched_address"] or "")
                if not rec["zip"] and zm:
                    rec["zip"], rec["zip_source"] = zm.group(1), "census_geocoder"
        recs.append(rec)
        print(f"[{i}/{len(cards)}] {rid}  deanery={rec['deanery']} pastor={(rec['pastor'] or {}).get('name')} geo={rec['geo_source']}")
    return recs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-geocode", action="store_true")
    a = ap.parse_args()
    rest = try_rest()
    if rest:
        # REST items carry title/link only for this CPT; still resolve via profile pages.
        cards = [{"name": html.unescape(it.get("title", {}).get("rendered", "")), "designation": None,
                  "additional_info": None, "phone": None, "email": None, "website": None,
                  "profile_url": it.get("link"), "wp_post_id": str(it.get("id")), "listing_url": LIST_URL}
                 for it in rest]
        # merge with listing cards for contact fields
        listing = {c["profile_url"]: c for c in crawl_listing()}
        cards = [listing.get(c["profile_url"], c) for c in cards]
    else:
        cards = crawl_listing()
    if not cards:
        sys.exit("no cards scraped (blocked?) - not overwriting output")
    recs = build(cards, geocode=not a.no_geocode)
    recs.sort(key=lambda r: (r["city"] or "", r["name"]))
    save_json(OUT, {
        "source": LIST_URL,
        "method": "rest" if rest else "html",
        "generated_at": now_iso(),
        "count": len(recs),
        "records": recs,
    })
    print(f"wrote {len(recs)} records -> {OUT}")


if __name__ == "__main__":
    main()
