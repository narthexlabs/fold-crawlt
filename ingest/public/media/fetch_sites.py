"""Fetch home pages (plus a livestream/contact page when linked) for organizations whose HTML is not cached yet,
so extract_links.py can read their social / video / newsletter links.

Covers: Catholic schools (schools_directory.json), ecosystem orgs with a website (orgs.json), the diocese's own
sites (oakdiocese.org, The Catholic Voice), the bishop's pages and context bodies (USCCB, California Catholic
Conference). Parish sites are NOT fetched here (parish_sites.py / the home re-crawl own data/raw/parish_sites/).

Polite: honest project User-Agent (common.UA), 1.5 s between requests, no retries past one, no bot-check evasion.
Raw HTML -> data/raw/media/<owner_id>/NN_<slug>.html (gitignored) plus index.json per owner.

Usage:  python3 ingest/public/media/fetch_sites.py [--only owner_id ...] [--force]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, get, now_iso  # noqa: E402

OUT = RAW / "media"

# Diocesan / context sites (owner_id, owner_type, name, url)
EXTRA = [
    ("diocese-oakland", "diocese", "Roman Catholic Diocese of Oakland", "https://oakdiocese.org/"),
    ("diocese-oakland", "diocese", "Roman Catholic Diocese of Oakland", "https://oakdiocese.org/bishop-barber/"),
    ("diocese-oakland", "diocese", "Roman Catholic Diocese of Oakland", "https://oakdiocese.org/livestream/"),
    ("med-catholic-voice", "media", "The Catholic Voice", "https://catholicvoiceoakland.org/"),
    ("christ-light-oakland", "parish", "Cathedral of Christ the Light", "https://ctlcathedral.org/"),
    ("ctx-usccb", "context", "United States Conference of Catholic Bishops", "https://www.usccb.org/"),
    ("ctx-cacatholic", "context", "California Catholic Conference", "https://cacatholic.org/"),
    ("dm-cyo", "diocesan_ministry", "CYO Oakland", "https://www.cyooakland.org/"),
    ("dm-cem", "diocesan_ministry", "Catholic Funeral & Cemetery Services (Oakland)", "https://www.cfcsoakland.org/"),
]

LIVE_RX = re.compile(r"href=[\"']([^\"']*(?:livestream|live-stream|watch-mass|online-mass|streaming|connect|follow|social)[^\"']*)[\"']", re.I)


def targets() -> list[tuple[str, str, str, str]]:
    out = list(EXTRA)
    cached_charity_domains = set()
    for idx in (RAW / "charities").glob("*/index.json"):
        cached_charity_domains.add(urlparse(json.loads(idx.read_text())["home"]).netloc.replace("www.", ""))
    web_idx = RAW / "ecosystem" / "web" / "index.json"
    if web_idx.exists():
        for v in json.loads(web_idx.read_text()).values():
            if v.get("status") == 200:
                cached_charity_domains.add(urlparse(v["url"]).netloc.replace("www.", ""))
    for s in json.loads((REAL / "schools_directory.json").read_text())["records"]:
        if s.get("website"):
            sid = "school-" + re.sub(r"[^a-z0-9]+", "-", (s["name"] + " " + (s.get("city") or "")).lower()).strip("-")
            out.append((sid, "school", s["name"], s["website"]))
    for o in json.loads((REAL / "ecosystem" / "orgs.json").read_text())["records"]:
        w = o.get("website")
        if not w or o["type"] in ("kofc_council", "kofc_assembly"):
            continue
        if urlparse(w).netloc.replace("www.", "") in cached_charity_domains:
            continue
        out.append((o["id"], o["type"], o["name"], w))
    return out


def fetch_owner(owner, otype, name, urls, force=False):
    d = OUT / owner
    idx_path = d / "index.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {"owner_id": owner, "owner_type": otype,
                                                                       "name": name, "pages": []}
    done = {p["url"] for p in idx["pages"]}
    d.mkdir(parents=True, exist_ok=True)
    queue = [u for u in urls if force or u not in done]
    extra_added = False
    while queue:
        u = queue.pop(0)
        r = get(u, timeout=20)
        time.sleep(1.5)
        rec = {"url": u, "fetched_at": now_iso(), "status": r.status_code if r is not None else None,
               "final_url": r.url if r is not None else None}
        if r is not None and r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            fn = f"{len(idx['pages']):02d}_{re.sub(r'[^a-z0-9]+', '-', urlparse(u).path.lower()).strip('-') or 'home'}.html"
            (d / fn).write_text(r.text, encoding="utf-8")
            rec["raw_file"] = str((d / fn).relative_to(RAW.parent.parent))
            if not extra_added:  # follow at most 2 livestream/social pages on the same host
                host = urlparse(r.url).netloc
                more = []
                for h in LIVE_RX.findall(r.text):
                    full = urljoin(r.url, h)
                    if urlparse(full).netloc == host and full not in done and full not in more and full != r.url:
                        more.append(full)
                queue.extend(more[:2])
                extra_added = True
        idx["pages"].append(rec)
        done.add(u)
        print(owner, rec["status"], u, flush=True)
    idx_path.write_text(json.dumps(idx, indent=2) + "\n")


def main():
    only = set(a for a in sys.argv[1:] if not a.startswith("--"))
    force = "--force" in sys.argv
    by_owner: dict[str, list] = {}
    meta = {}
    for owner, otype, name, url in targets():
        if only and owner not in only:
            continue
        by_owner.setdefault(owner, []).append(url)
        meta[owner] = (otype, name)
    for owner, urls in by_owner.items():
        fetch_owner(owner, *meta[owner], urls, force=force)


if __name__ == "__main__":
    main()
