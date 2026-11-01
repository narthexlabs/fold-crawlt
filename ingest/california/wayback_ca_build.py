#!/usr/bin/env python3
"""Turn the Wayback captures fetched by wayback_ca_run.py into data/real/california/wayback/<diocese>.json.

    python3 -I ingest/california/wayback_ca_build.py            # reads data/raw/california/wayback/<slug>/<site>/ (gitignored)

Per site: captures used (original URL, timestamp, archive URL), groups (existing groups extraction, ingest/california/
sites/parse_site.groups_for), bulletin page + third-party bulletin links (LPi container.parishesonline.com,
files.ecatholic.com, discovermass; PDFs are NOT fetched), social links, Mass-times rows (existing parser, every row
needs_review, source wayback-<date>), capture age in days. All text goes through ingest/public/privacy.py.
"""
from __future__ import annotations

import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
import parse_site as ps  # noqa: E402

RAWD = ROOT / "data" / "raw" / "california" / "wayback"
OUT = ROOT / "data" / "real" / "california" / "wayback"
TODAY = date(2026, 10, 31)
TARGETS = ROOT / "data" / "raw" / "california" / "wayback" / "_targets.json"
THIRD = re.compile(r"container\.parishesonline\.com|files\.ecatholic\.com|discovermass\.com|parishesonline\.com|4lpi\.com|jspaluch|"
                   r"issuu\.com|calameo|flipsnack|yumpu|diocesan\.com|church-bulletins|churchbulletins|myparishapp", re.I)
PRIMARY = re.compile(r"container\.parishesonline\.com|files\.ecatholic\.com|discovermass\.com", re.I)
CHALLENGE = re.compile(r"just a moment|cf-browser-verification|attention required|enable javascript and cookies|sucuri website firewall", re.I)
PARISH_WORDS = re.compile(r"parish|mass\b|catholic|church|diocese|ministr|misa|iglesia|parroquia", re.I)


def load_html(path: Path) -> str:
    b = gzip.open(path, "rb").read()
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', b[:4000])
    enc = (m.group(1).decode() if m else "utf-8")
    try:
        return b.decode(enc, "replace")
    except LookupError:
        return b.decode("utf-8", "replace")


def build_site(sdir: Path) -> dict:
    st = json.loads((sdir / "site.json").read_text("utf-8"))
    slug, key = st["slug"], st["key"]
    caps, pages = [], []
    for c in st["captures"]:
        if c.get("status") != 200 or not c.get("path") or not (sdir / c["path"]).exists():
            caps.append({k: c.get(k) for k in ("roles", "original", "capture_date", "archive_url", "status", "error")} | {"used": False})
            continue
        text = load_html(sdir / c["path"])
        bad = "challenge_page" if CHALLENGE.search(text[:6000]) else None
        if not bad and not PARISH_WORDS.search(text):
            bad = "not_parish_content"
        cap_date = c["capture_date"]
        age = (TODAY - date.fromisoformat(cap_date)).days if cap_date else None
        caps.append({"roles": c["roles"], "original": c["original"], "capture_timestamp": c["capture_timestamp"], "capture_date": cap_date,
                     "archive_url": c["archive_url"], "status": 200, "bytes": c["bytes"], "age_days": age,
                     "used": bad is None, "unusable_reason": bad})
        if bad:
            continue
        kind = {"home": "home", "mass": "mass_times", "ministries": "ministries", "bulletin": "bulletin"}.get(c["roles"][0], "other")
        rec = {"kind": kind, "url": c["original"], "final_url": c["original"], "fetched_at": cap_date}
        pg = ps.Page(rec, text.encode("utf-8"))
        pg.cap = caps[-1]
        pages.append(pg)
    used = [c for c in caps if c.get("used")]
    home_cap = next((c for c in used if "home" in c["roles"]), None)
    newest = max(used, key=lambda c: c["capture_date"], default=None)
    ref = home_cap or newest
    out = {"domain": key, "diocese": slug, "crawl_status": st.get("crawl_status"), "home_url": st["home"],
           "wayback": {"cdx_urls_since_2024": (st.get("cdx") or {}).get("rows"), "cdx_urls_2025plus": (st.get("cdx") or {}).get("rows_2025plus"),
                       "note": st.get("note"), "home_missing": bool(st.get("home_missing")),
                       "any_2025_capture": any((c["capture_date"] or "") >= "2025-01-01" for c in used),
                       "newest_capture_date": newest["capture_date"] if newest else None,
                       "capture_age_days": ref["age_days"] if ref else None, "captures": caps},
           "groups": [], "groups_dropped": {}, "bulletin": None, "bulletin_links": [], "social": {}, "schedule_rows": []}
    if not pages:
        return out

    def archive_of(pg):
        return pg.cap["archive_url"]

    def label(pg):
        return f"wayback-{pg.cap['capture_date']}"

    # groups (ministries pages first, existing extraction)
    groups, dropped = ps.groups_for(pages, key, st["home"], TODAY.isoformat())
    for g in groups:
        pg = next((p for p in pages if p.url == g["source_url"]), pages[0])
        g.update(source=label(pg), source_url=archive_of(pg), source_kind="website", fetched_at=pg.cap["capture_date"], needs_review=True)
    out["groups"], out["groups_dropped"] = groups, dropped
    # schedule rows from the Mass-times page (home page when no Mass page was captured)
    mass = [p for p in pages if p.kind == "mass_times"] or [p for p in pages if p.kind == "home"]
    if mass:
        rows, _ = ps.schedule_rows(mass[:1], key, st["home"], TODAY.isoformat(), None, {})
        for r in rows:
            pg = mass[0]
            reasons = [x for x in (r.get("review_reason"), "wayback", None if pg.kind == "mass_times" else "from_home_page") if x]
            r.update(source=label(pg), source_url=archive_of(pg), fetched_at=pg.cap["capture_date"], needs_review=True,
                     review_reason=",".join(reasons), page_role=pg.kind)
        out["schedule_rows"] = rows
    # bulletin page + third-party links
    links, seen = [], set()
    for pg in pages:
        for a in pg.soup.find_all("a", href=True):
            href = urljoin(pg.url, a["href"].strip()).split("#")[0]
            if (href.startswith("http") and THIRD.search(urlparse(href).netloc + urlparse(href).path) and href not in seen
                    and (re.search(r"\.pdf(?:\?|$)|bulletin|boletin", href, re.I) or re.search(r"discovermass|parishesonline|4lpi", href, re.I))
                    and not re.search(r"\.(?:jpe?g|png|gif|webp|svg)(?:\?|$)", href, re.I)):
                seen.add(href)
                links.append({"url": href, "host": urlparse(href).netloc.lower(), "primary_host": bool(PRIMARY.search(href)),
                              "date": ps.date_of(href), "found_on": pg.url, "capture_date": pg.cap["capture_date"]})
        for m in ps.ECAT_RE.finditer(pg.text):
            if m.group(0) not in seen:
                seen.add(m.group(0))
                links.append({"url": m.group(0), "host": "files.ecatholic.com", "primary_host": True, "date": ps.date_of(m.group(2)),
                              "found_on": pg.url, "capture_date": pg.cap["capture_date"]})
        for m in re.finditer(r"https?://container\.parishesonline\.com/bulletins/\d+/\d+/\d{8}B?\.pdf", pg.text):
            if m.group(0) not in seen:
                seen.add(m.group(0))
                links.append({"url": m.group(0), "host": "container.parishesonline.com", "primary_host": True, "date": ps.date_of(m.group(0)),
                              "found_on": pg.url, "capture_date": pg.cap["capture_date"]})
    out["bulletin_links"] = links[:40]
    bpage = next((p for p in pages if p.kind == "bulletin"), None)
    best = ps.find_bulletin(pages)
    out["bulletin"] = {"page_url": bpage.url if bpage else (best or {}).get("source_url"), "page_archive_url": archive_of(bpage) if bpage else None,
                       "best_link": best}
    soc, _ = ps.extract_social(pages, st["home"])
    out["social"] = soc
    return ps.screen_record(out)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    per = defaultdict(list)
    for sj in sorted(RAWD.glob("*/*/site.json")):
        st = json.loads(sj.read_text("utf-8"))
        if not st.get("done"):
            continue
        per[st["slug"]].append(build_site(sj.parent))
    stats = {}
    for slug, sites in per.items():
        # social links shared by >= 5 sites of a diocese are the diocese's own
        cnt = Counter((p, u) for s in sites for p, us in s["social"].items() if p != "livestream" for u in us)
        shared = {k for k, v in cnt.items() if v >= 5}
        for s in sites:
            for p in list(s["social"]):
                if p != "livestream":
                    s["social"][p] = [u for u in s["social"][p] if (p, u) not in shared]
                    if not s["social"][p]:
                        del s["social"][p]
        ages = [s["wayback"]["capture_age_days"] for s in sites if s["wayback"]["capture_age_days"] is not None]
        stats[slug] = {
            "sites_done": len(sites),
            "with_any_2025_capture": sum(1 for s in sites if s["wayback"]["any_2025_capture"]),
            "with_ministries_page": sum(1 for s in sites if any("ministries" in c.get("roles", []) and c.get("used") for c in s["wayback"]["captures"])),
            "groups_found": sum(len(s["groups"]) for s in sites),
            "sites_with_bulletin_links": sum(1 for s in sites if any(l["primary_host"] for l in s["bulletin_links"])),
            "bulletin_links_primary_hosts": sum(1 for s in sites for l in s["bulletin_links"] if l["primary_host"]),
            "schedule_rows": sum(len(s["schedule_rows"]) for s in sites),
            "median_capture_age_days": statistics.median(ages) if ages else None}
        doc = {"diocese": slug, "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "counts": stats[slug],
               "source": "Wayback Machine (CDX API, statuscode:200, newest per URL since 2024-01-01; id_ raw captures)", "sites": sites}
        (OUT / f"{slug}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", "utf-8")
    (OUT / "_counts.json").write_text(json.dumps(stats, indent=1) + "\n", "utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
