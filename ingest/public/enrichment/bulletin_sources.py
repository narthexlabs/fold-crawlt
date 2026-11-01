"""Find each parish's weekly-bulletin source and list dated bulletin PDF candidates.

Inputs
  data/real/parishes_directory.json   (75 parishes)
  data/real/parish_sites.json         (crawl results; raw HTML in data/raw/parish_sites/<id>/)
  ingest/public/enrichment/bulletin_manual.json  (hand-found sources: LPi codes, index pages, PDFs,
                                                  each with the public page where it was found)
Extra bulletin index pages listed in the manual file are fetched once (honest User-Agent, 2 s delay)
and cached in data/raw/bulletins/_index/<id>/.

Output
  data/real/enrichment/bulletin_sources.json
    one record per parish: source_type, index_url(s), lpi_code, candidates[{url, date, found_on, how}]

Source types: lpi (LPi / ParishesOnline container PDFs), parish_pdf (PDFs hosted by the parish site or
its CMS/CDN), docman (Joomla file links), ecatholic, image_pages (bulletin posted as JPG pages),
flocknote, none.

Usage: python ingest/public/enrichment/bulletin_sources.py [--weeks 8] [--no-fetch]
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, ROOT, SESSION, load_json, now_iso, save_json  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = REAL / "enrichment" / "bulletin_sources.json"
MANUAL = HERE / "bulletin_manual.json"
INDEX_CACHE = RAW / "bulletins" / "_index"
TODAY = date(2026, 10, 26)

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}
MON3 = {k[:3]: v for k, v in MONTHS.items()}
MON3["sept"] = 9

LPI_CONTAINER = re.compile(r"container\.parishesonline\.com/bulletins/(\d{2})/(\d{4})/(?:tn_)?(\d{8})B\.(?:pdf|jpg)", re.I)
LPI_WIDGET = re.compile(r"publicationWidget\?[^\"'<>\s]*?id=(0018[0-9A-Za-z]{11,14})", re.I)
LPI_ORG = re.compile(r"parishesonline\.com/(?:organization|find|publication-page)/([a-z0-9-]+)", re.I)
LPI_FILECODE = re.compile(r"^05[-_]?(\d{4})[_-]")  # LPi job number at the start of a file name
ECATH = re.compile(r"files\.ecatholic\.com/(\d+)/bulletins/(\d{8})\.pdf", re.I)
BULLETIN_WORD = re.compile(r"bulletin|bolet[ií]n|bolletin|weekly|semanal|sunday|domingo|ordinary|advent|lent|easter|"
                           r"christmas|pentecost|SPNSA|Bull_|\bB(?:online)?\.pdf|/bulletins?/", re.I)
NOT_BULLETIN = re.compile(r"registration|calendar|insert|flyer|form|application|policy|guideline|letter|"
                          r"schedule[-_ ]?by|lector|minister[-_ ]?schedule|menu|handbook|annual[-_ ]?report|"
                          r"usccb\.org|Bulletin-Insert", re.I)


def sundays(n: int, end: date = TODAY) -> list[date]:
    d = end - timedelta(days=(end.weekday() + 1) % 7)  # most recent Sunday on/before end
    return [d - timedelta(weeks=i) for i in range(n)]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        x = date(y, m, d)
    except ValueError:
        return None
    if date(2015, 1, 1) <= x <= TODAY + timedelta(days=14):
        return x
    return None


def parse_date(s: str, year_hint: int | None = None) -> date | None:
    """Pull a bulletin date out of a URL or link text."""
    s = unquote(htmllib.unescape(s or ""))
    low = s.lower()
    # 20261025 / 20261025B
    for m in re.finditer(r"(?<!\d)(20[12]\d)(\d{2})(\d{2})(?!\d)", s):
        x = _mk(int(m[1]), int(m[2]), int(m[3]))
        if x:
            return x
    # 2026-10-25 / 2026_10_25 / 2026.10.25
    for m in re.finditer(r"(?<!\d)(20[12]\d)[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)", s):
        x = _mk(int(m[1]), int(m[2]), int(m[3]))
        if x:
            return x
    # Month name + day (+ optional year)
    mon_re = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    for m in re.finditer(mon_re + r"[-_ .+]*(\d{1,2})(?:st|nd|rd|th)?(?:\s*[-\u2013&/]\s*(\d{1,2})(?:st|nd|rd|th)?)?"
                         r"(?:[-_ ,.+]*(20[12]\d))?(?!\d)", low):
        mon = m[1]
        mi = MONTHS.get(mon) or MON3.get(mon[:4] if mon.startswith("sept") else mon[:3])
        day = int(m[2])
        if m[3] and 0 < int(m[3]) - day <= 2:  # "October 24-25" -> the later (Sunday) date
            day = int(m[3])
        years = [int(m[4])] if m[4] else ([year_hint] if year_hint else [TODAY.year, TODAY.year - 1])
        for y in years:
            x = _mk(y, mi, day) if mi else None
            if x:
                return x
    # 10-25-2026 / 10.25.2026
    for m in re.finditer(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](20[12]\d)(?!\d)", s):
        x = _mk(int(m[3]), int(m[1]), int(m[2]))
        if x:
            return x
    # 10.25.26 / 10-25-26 / 09.06.26
    for m in re.finditer(r"(?<![\d])(\d{1,2})[-_.](\d{1,2})[-_.](\d{2})(?![\d])", s):
        x = _mk(2000 + int(m[3]), int(m[1]), int(m[2]))
        if x:
            return x
    # bare MMDDYY file name: 102526.pdf
    m = re.search(r"/(\d{2})(\d{2})(\d{2})\.pdf", s)
    if m:
        x = _mk(2000 + int(m[3]), int(m[1]), int(m[2]))
        if x:
            return x
    return None


def year_hint_from(url: str) -> int | None:
    m = re.search(r"/(20[12]\d)/\d{2}/", url) or re.search(r"(20[12]\d)", url)
    return int(m[1]) if m else None


def extract_links(html: str, base: str) -> list[tuple[str, str]]:
    out = []
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["a", "iframe", "embed", "object", "img", "source"]):
        href = tag.get("href") or tag.get("src") or tag.get("data") or tag.get("data-src")
        if not href:
            continue
        text = tag.get_text(" ", strip=True) if tag.name == "a" else (tag.get("title") or tag.get("alt") or "")
        out.append((urljoin(base, htmllib.unescape(href.strip())), text[:200]))
    # URLs inside scripts / JSON attributes (escaped slashes)
    for m in re.finditer(r"https?:\\?/\\?/[^\s\"'<>()]+?\.pdf(?:\?[^\s\"'<>]*)?", html, re.I):
        out.append((m[0].replace("\\/", "/"), ""))
    for m in re.finditer(r"https?://container\.parishesonline\.com/bulletins/[^\s\"'<>&]+", html, re.I):
        out.append((m[0], ""))
    return out


def classify(url: str, text: str, year_hint: int | None, on_index: bool = False) -> dict | None:
    u = url.split("#")[0]
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    low = u.lower()
    m = LPI_CONTAINER.search(u)
    if m:
        d = parse_date(m[3])
        return {"url": f"https://container.parishesonline.com/bulletins/{m[1]}/{m[2]}/{m[3]}B.pdf",
                "date": d.isoformat() if d else None, "how": "lpi_container", "lpi_code": f"{m[1]}-{m[2]}"}
    m = ECATH.search(u)
    if m:
        d = parse_date(m[2])
        return {"url": u.split("?")[0], "date": d.isoformat() if d else None, "how": "ecatholic"}
    is_pdf = ".pdf" in low
    is_docman = bool(re.search(r"/(?:bulletin|weekly-bulletin)/\d+-[^/]+/file$", low))
    is_wix = "usrfiles.com/ugd/" in low or "/_files/ugd/" in low
    is_img_page = bool(re.search(r"bulletin[^/]*page\d*\.jpe?g$|bulletin[^/]*-\d\.jpe?g$", low))
    if not (is_pdf or is_docman or is_img_page):
        return None
    if "usccb.org" in low:
        return None
    hay = unquote(f"{u} {text}")
    has_word = bool(re.search(r"bulletin|bolet[ií]n|bolletin", hay, re.I))
    if NOT_BULLETIN.search(hay) and not has_word:
        return None
    d = parse_date(u, year_hint) or parse_date(text, year_hint)
    if not (has_word or d or (on_index and is_pdf) or BULLETIN_WORD.search(hay)):
        return None
    how = "docman" if is_docman else ("image_page" if is_img_page else ("wix_pdf" if is_wix else "pdf"))
    short = " ".join((text or "").split()[:12])
    return {"url": u, "date": d.isoformat() if d else None, "how": how, "text": short or None}


def fetch_index(pid: str, url: str, delay: float) -> tuple[str | None, int | None]:
    INDEX_CACHE.joinpath(pid).mkdir(parents=True, exist_ok=True)
    f = INDEX_CACHE / pid / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".html")
    if f.exists():
        return f.read_text(encoding="utf-8", errors="ignore"), 200
    time.sleep(delay)
    try:
        r = SESSION.get(url, timeout=20)
    except Exception:
        return None, None
    if r.ok and "html" in r.headers.get("content-type", ""):
        f.write_text(r.text, encoding="utf-8")
        return r.text, r.status_code
    return None, r.status_code


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    directory = {r["id"]: r for r in load_json(REAL / "parishes_directory.json")["records"]}
    sites = {r["id"]: r for r in load_json(REAL / "parish_sites.json")["records"]}
    manual = load_json(MANUAL, {}).get("parishes", {})
    recent = sundays(args.weeks)
    manifest = load_json(REAL / "enrichment" / "bulletin_manifest.json", {}).get("records", {})
    records = []
    for pid, prow in directory.items():
        site = sites.get(pid, {})
        man = manual.get(pid, {})
        cands: dict[str, dict] = {}
        lpi_codes: dict[str, str] = {}
        widget_ids, lpi_orgs, index_pages, notes = set(), set(), [], []

        def take(url: str, text: str, found_on: str, on_index: bool = False):
            for m in LPI_WIDGET.finditer(url):
                widget_ids.add(m[1])
            for m in LPI_ORG.finditer(url):
                lpi_orgs.add(m[1])
            c = classify(url, text, year_hint_from(url) or year_hint_from(found_on), on_index)
            if not c:
                return
            if c.get("lpi_code"):
                lpi_codes.setdefault(c["lpi_code"], found_on)
            c["found_on"] = found_on
            key = c["url"]
            if key not in cands or (c.get("date") and not cands[key].get("date")):
                cands[key] = c

        # 1. crawl pages already on disk
        for p in site.get("pages", []) or []:
            raw = Path(p.get("raw_file") or "")
            if not raw.exists():
                continue
            html = raw.read_text(encoding="utf-8", errors="ignore")
            for url, text in extract_links(html, p["url"]):
                take(url, text, p["url"], p.get("category") == "bulletin")
            for m in LPI_WIDGET.finditer(html):
                widget_ids.add(m[1])
            if p.get("category") == "bulletin":
                index_pages.append(p["url"])
        b = site.get("bulletin") or {}
        if b.get("url"):
            take(b["url"], b.get("text") or "", b.get("source_url") or site.get("website") or "")
        # 2. hand-found sources
        for code, where in (man.get("lpi_codes") or {}).items():
            lpi_codes.setdefault(code, where)
        for item in man.get("pdfs", []):
            c = classify(item["url"], item.get("text", ""), year_hint_from(item["url"]), True) or {
                "url": item["url"], "date": None, "how": "pdf"}
            if item.get("date"):
                c["date"] = item["date"]
            c["found_on"] = item.get("found_on")
            cands[c["url"]] = c
        for idx in man.get("index_pages", []):
            url, found_on = (idx["url"], idx.get("found_on")) if isinstance(idx, dict) else (idx, None)
            if url in index_pages:
                continue
            index_pages.append(url)
            if args.no_fetch:
                continue
            html, status = fetch_index(pid, url, args.delay)
            if html is None:
                notes.append(f"index page {url} not fetched (status {status})")
                continue
            for u2, text in extract_links(html, url):
                take(u2, text, url, True)
        # 3. file-name LPi codes (e.g. 05-1934_10-25-26.pdf, 051933-October-25...pdf)
        for c in list(cands.values()):
            fn = unquote(c["url"].rsplit("/", 1)[-1])
            m = LPI_FILECODE.search(fn)
            if m and c["how"] != "lpi_container":
                lpi_codes.setdefault(f"05-{m[1]}", c["url"])
        # 3b. LPi job number printed in an already-downloaded bulletin (e.g. "05-4158" on the ad page)
        if not lpi_codes:
            from collections import Counter
            cnt, where = Counter(), {}
            for e in manifest.get(pid, []):
                tp = e.get("text_path") and (ROOT / e["text_path"])
                if e.get("ok") and tp and tp.exists():
                    for m in re.finditer(r"(?<![\d-])05-(\d{4})(?![\d-])", tp.read_text(encoding="utf-8", errors="ignore")):
                        cnt[m[1]] += 1
                        where.setdefault(m[1], e["url"])
            if cnt:
                code = cnt.most_common(1)[0][0]
                lpi_codes[f"05-{code}"] = where[code]
                notes.append(f"LPi code 05-{code} read from the printed bulletin {where[code]}")
        # 4a. eCatholic sites: same file pattern the parish bulletin pages link to
        ecat_sites = {}
        for c in cands.values():
            m = ECATH.search(c["url"])
            if m:
                ecat_sites.setdefault(m[1], c.get("found_on"))
        if man.get("ecatholic_site"):
            ecat_sites.setdefault(man["ecatholic_site"], (man.get("pdfs") or [{}])[0].get("found_on"))
        for site_id, where in ecat_sites.items():
            for d in recent:
                u = f"https://files.ecatholic.com/{site_id}/bulletins/{d:%Y%m%d}.pdf"
                cands.setdefault(u, {"url": u, "date": d.isoformat(), "how": "ecatholic_pattern", "found_on": where})
        # 4. LPi container URLs for recent Sundays (same URL pattern the parish pages link to)
        for code in lpi_codes:
            region, num = code.split("-")
            for d in recent:
                u = f"https://container.parishesonline.com/bulletins/{region}/{num}/{d:%Y%m%d}B.pdf"
                cands.setdefault(u, {"url": u, "date": d.isoformat(), "how": "lpi_container_pattern",
                                     "found_on": lpi_codes[code]})
        dated = sorted([c for c in cands.values() if c.get("date")], key=lambda c: c["date"], reverse=True)
        undated = [c for c in cands.values() if not c.get("date")]
        hows = {c["how"] for c in cands.values()}
        if lpi_codes:
            stype = "lpi"
        elif hows & {"pdf", "wix_pdf"}:
            stype = "parish_pdf"
        elif "docman" in hows:
            stype = "docman"
        elif "ecatholic" in hows:
            stype = "ecatholic"
        elif "image_page" in hows:
            stype = "image_pages"
        elif widget_ids or lpi_orgs:
            stype = "lpi_widget_only"
        elif any("flocknote" in (x or "") for x in index_pages):
            stype = "flocknote"
        else:
            stype = "none_found"
        records.append({
            "id": pid,
            "name": prow["name"],
            "city": prow["city"],
            "crawl_status": site.get("crawl_status"),
            "source_type": stype,
            "lpi_codes": lpi_codes,
            "ecatholic_sites": sorted(ecat_sites),
            "lpi_widget_ids": sorted(widget_ids),
            "lpi_org_slugs": sorted(lpi_orgs),
            "index_pages": index_pages,
            "candidates": dated + undated,
            "n_dated": len(dated),
            "latest_date": dated[0]["date"] if dated else None,
            "manual_note": man.get("note"),
            "notes": notes,
        })
        print(f"{pid:48s} {stype:16s} dated={len(dated):3d} latest={dated[0]['date'] if dated else '-'}"
              f" lpi={','.join(lpi_codes) or '-'}", flush=True)
    save_json(OUT, {"generated_at": now_iso(), "weeks": args.weeks,
                    "method": "links found in crawled parish pages + hand-found sources (bulletin_manual.json)",
                    "count": len(records), "records": records})
    print("wrote", OUT)


if __name__ == "__main__":
    main()
