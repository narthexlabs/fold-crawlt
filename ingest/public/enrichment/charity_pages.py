"""Fetch public pages of Catholic charities in Alameda and Contra Costa counties for profile building.

For each organization: fetch the home page, find internal links about programs/services, locations and
hours, volunteering, donations and current needs, impact and annual reports, then fetch up to --max
pages (honest User-Agent, polite per-host delay). Raw HTML and visible text go to
data/raw/charities/<org>/ (gitignored). An index of fetched pages (url, category, status, fetched_at,
text file) is written to data/raw/charities/<org>/index.json.

The structured profiles in data/real/enrichment/charity_profiles.json are curated from these pages
(every field keeps its source_url); see build_charity_profiles.py.

Usage: python ingest/public/enrichment/charity_pages.py [--orgs cceb,svdp-cc] [--max 14]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, SESSION, now_iso, save_json  # noqa: E402

OUT = RAW / "charities"
ORGS = {
    "cceb": "https://www.cceb.org/",
    "svdp-alameda": "https://www.svdp-alameda.org/",
    "svdp-cc": "https://www.svdp-cc.org/",
    "st-marys-center": "https://stmaryscenter.org/",
}
CATS = {
    "programs": r"program|service|what[-_ ]we[-_ ]do|our[-_ ]work|ministr|dining|pantry|housing|immigration|"
                r"refugee|counsel|senior|preschool|shelter|clinic|food|thrift|store|reentry|jail|prison",
    "locations": r"location|contact|hours|visit|find[-_ ]us|directions|where",
    "volunteer": r"volunteer|get[-_ ]involved|serve|join",
    "needs": r"donat|give|giving|wish[-_ ]?list|needs|in[-_ ]kind|drive|support|amazon",
    "impact": r"impact|annual[-_ ]report|report|about|history|mission|financ|990|stories|news",
}
SKIP = re.compile(r"\.(pdf|jpe?g|png|gif|zip|docx?|mp4|mp3)(\?|$)|/wp-json/|/feed/?$|/tag/|/author/|"
                  r"/category/|\?share=|facebook|twitter|instagram|linkedin|youtube|mailto:|tel:", re.I)


def visible_text(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "noscript", "svg", "form"]):
        t.decompose()
    for br in s.find_all("br"):
        br.replace_with("\n")
    txt = s.get_text("\n")
    return "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in txt.split("\n") if ln.strip())


def links(html: str, base: str) -> list[tuple[str, str]]:
    s = BeautifulSoup(html, "lxml")
    out = []
    host = urlparse(base).netloc.removeprefix("www.")
    for a in s.find_all("a", href=True):
        u = urljoin(base, a["href"].strip()).split("#")[0]
        if not u.startswith("http") or urlparse(u).netloc.removeprefix("www.") != host or SKIP.search(u):
            continue
        out.append((u, a.get_text(" ", strip=True)[:80]))
    return out


def crawl(org: str, home: str, maxp: int, delay: float) -> dict:
    d = OUT / org
    d.mkdir(parents=True, exist_ok=True)
    pages, seen, queue = [], set(), [(home, "home")]
    while queue and len(pages) < maxp:
        url, cat = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        time.sleep(delay)
        try:
            r = SESSION.get(url, timeout=25)
            status = r.status_code
        except Exception as e:  # noqa: BLE001
            pages.append({"url": url, "category": cat, "status": f"error:{type(e).__name__}", "fetched_at": now_iso()})
            continue
        rec = {"url": url, "final_url": r.url, "category": cat, "status": status, "fetched_at": now_iso()}
        if r.ok and "html" in r.headers.get("content-type", ""):
            stem = f"{len(pages):02d}_" + (re.sub(r"[^A-Za-z0-9]+", "_", urlparse(r.url).path).strip("_")[:60] or "home")
            (d / f"{stem}.html").write_text(r.text, encoding="utf-8")
            (d / f"{stem}.txt").write_text(visible_text(r.text), encoding="utf-8")
            rec["text_file"] = str((d / f"{stem}.txt").relative_to(RAW.parent.parent))
            if cat == "home":
                found: dict[str, list[str]] = {k: [] for k in CATS}
                for u, txt in links(r.text, r.url):
                    hay = (txt + " " + urlparse(u).path).lower()
                    for c, rx in CATS.items():
                        if re.search(rx, hay) and u not in found[c]:
                            found[c].append(u)
                rec["discovered"] = found
                # breadth: interleave categories so each gets pages
                for i in range(6):
                    for c in CATS:
                        if i < len(found[c]):
                            queue.append((found[c][i], c))
        pages.append(rec)
        print(f"  {org} {status} {cat:10s} {url}", flush=True)
        if cat == "home" and not (r.ok):
            break
    idx = {"org": org, "home": home, "fetched_at": now_iso(), "pages": pages}
    save_json(d / "index.json", idx)
    return idx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orgs", default="")
    ap.add_argument("--max", type=int, default=14)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--extra", default="", help="org=url,org2=url2 to add organizations")
    args = ap.parse_args()
    orgs = dict(ORGS)
    for kv in filter(None, args.extra.split(",")):
        k, v = kv.split("=", 1)
        orgs[k] = v
    want = set(filter(None, args.orgs.split(","))) or set(orgs)
    for org, home in orgs.items():
        if org in want:
            print(org, home, flush=True)
            crawl(org, home, args.max, args.delay)


if __name__ == "__main__":
    main()
