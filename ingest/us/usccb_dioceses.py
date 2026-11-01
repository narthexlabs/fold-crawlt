"""US diocese spine: USCCB "Bishops and Dioceses by State" -> data/real/us/dioceses.json.

Source page (public HTML, no login): https://www.usccb.org/about/bishops-and-dioceses/all-dioceses
Method: read robots.txt (refuse the page if it is disallowed), then fetch the one page (>= 1 s after robots.txt),
parse each <article class="node--type-da"> under its state heading. Nothing else is requested.
Raw HTML is cached in data/raw/us/usccb/ (git-ignored). Re-run with --refresh to fetch again.

    python3 -I ingest/us/usccb_dioceses.py [--refresh]
"""
import html
import json
import re
import sys
import time
import urllib.robotparser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "us" / "usccb"
OUT = ROOT / "data" / "real" / "us" / "dioceses.json"
UA = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"
HOST = "https://www.usccb.org"
URL = HOST + "/about/bishops-and-dioceses/all-dioceses"
GAP = 1.5  # seconds between requests (rule: >= 1 s)

# Eastern Catholic church named in the title, where the title says it (otherwise null; nothing is guessed).
CHURCH_WORDS = [("Ukrainian", "Ukrainian"), ("Byzantine", "Byzantine (Ruthenian)"), ("Chaldean", "Chaldean"),
                ("Syro Malabar", "Syro-Malabar"), ("Syro-Malabar", "Syro-Malabar"),
                ("Syro-Malankara", "Syro-Malankara"), ("Syriac", "Syriac"), ("Maronite", "Maronite"),
                ("Armenian", "Armenian"), ("Romanian", "Romanian"), ("Melkite", "Melkite")]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(refresh: bool) -> tuple[str, str, str]:
    """Return (html, fetched_at, robots_note). Cached copy unless --refresh."""
    RAW.mkdir(parents=True, exist_ok=True)
    page, meta = RAW / "all-dioceses.html", RAW / "all-dioceses.json"
    if page.exists() and meta.exists() and not refresh:
        m = json.loads(meta.read_text())
        return page.read_text(encoding="utf-8"), m["fetched_at"], m["robots_note"]
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(HOST + "/robots.txt", timeout=(15, 60))
    if r.status_code != 200:
        sys.exit(f"BLOCKED: robots.txt HTTP {r.status_code}; stopping")
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    if not rp.can_fetch(UA, URL):
        sys.exit("robots.txt disallows the page; stopping")
    note = f"robots.txt HTTP 200; {URL.replace(HOST, '')} allowed; crawl-delay {rp.crawl_delay('*') or 'none'}"
    time.sleep(GAP)
    r = s.get(URL, timeout=(15, 90))
    if r.status_code != 200:
        sys.exit(f"BLOCKED: page HTTP {r.status_code}; stopping")
    fetched = now_iso()
    page.write_text(r.text, encoding="utf-8")
    meta.write_text(json.dumps({"url": URL, "status": 200, "fetched_at": fetched, "robots_note": note,
                                "bytes": len(r.content)}))
    return r.text, fetched, note


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def classify(title: str) -> tuple[str, str]:
    """(rank, rite). Rank: archdiocese | diocese | archeparchy | eparchy | ordinariate | military. Rite: Latin | Eastern."""
    t = title.lower()
    if "military services" in t:
        return "military", "Latin"
    if "personal ordinariate" in t:
        return "ordinariate", "Latin"
    if "archeparchy" in t:
        return "archeparchy", "Eastern"
    if "eparchy" in t or "for ukrainians" in t or "for the romanians" in t:
        return "eparchy", "Eastern"
    # Styled "Diocese" on the USCCB list but canonically eparchies of Eastern churches.
    if re.search(r"syro.malabar|syriac catholic", t):
        return "eparchy", "Eastern"
    if t.startswith("archdiocese"):
        return "archdiocese", "Latin"
    return "diocese", "Latin"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse(h: str) -> list[dict]:
    rows = []
    for grp in h.split('<div class="views-group">')[1:]:
        heading = clean(re.search(r"<h3>([^<]+)</h3>", grp).group(1))
        for art in re.findall(r'<article class="node node--type-da.*?</article>', grp, flags=re.S):
            def f(cls):
                m = re.search(r'field--name-%s[^>]*>(.*?)</div>' % cls, art, flags=re.S)
                return clean(re.sub(r"<[^>]+>", "", m.group(1))) if m else None
            title = clean(re.search(r"field--name-title[^>]*>([^<]+)<", art).group(1))
            site = re.search(r'field--name-field-da-site[^>]*><a href="([^"]+)"', art)
            staff = [{"name": clean(n), "position": clean(p)} for n, p in re.findall(
                r'<div class="name">(.*?)</div>\s*<div class="position">(.*?)</div>', art, flags=re.S)]
            rank, rite = classify(title)
            church = next((v for k, v in CHURCH_WORDS if k.lower() in title.lower()), None) if rite == "Eastern" else None
            rows.append({
                "slug": slugify(title), "name": title, "rank": rank, "rite": rite, "eastern_church": church,
                "see_city": f("field-da-city"), "see_state": f("field-da-state-abbreviation"),
                "usccb_state_heading": heading,
                "states_served": None,  # not on the USCCB list; derive from county shapes (HANDOFF s.4 step 1)
                "province": None,       # not shown on the USCCB list
                "website": site.group(1) if site else None,
                "chancery_address": ", ".join(x for x in [f("field-da-address-1")] if x) or None,
                "chancery_zip": f("field-da-zip-code"),
                "leadership_as_listed": staff,  # public clergy roles exactly as the page lists them
            })
    return rows


def main():
    h, fetched, note = fetch("--refresh" in sys.argv)
    rows = parse(h)
    slugs = Counter(r["slug"] for r in rows)
    for r in rows:  # make slugs unique if two titles collide
        if slugs[r["slug"]] > 1:
            r["slug"] += "-" + (r["see_state"] or "").lower()
    for r in rows:
        r["source_url"] = URL
        r["fetched_at"] = fetched
    rows.sort(key=lambda r: (r["usccb_state_heading"], r["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "USCCB, Bishops and Dioceses by State", "source_url": URL, "fetched_at": fetched,
        "user_agent": UA, "robots": note, "count": len(rows),
        "by_rank": dict(Counter(r["rank"] for r in rows)), "by_rite": dict(Counter(r["rite"] for r in rows)),
        "dioceses": rows}, indent=1, ensure_ascii=False))
    print(len(rows), dict(Counter(r["rank"] for r in rows)), dict(Counter(r["rite"] for r in rows)))
    dup = [s for s, n in Counter(r["slug"] for r in rows).items() if n > 1]
    print("duplicate slugs:", dup)


if __name__ == "__main__":
    main()
