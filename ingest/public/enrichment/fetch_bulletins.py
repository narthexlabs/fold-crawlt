"""Download the latest weekly bulletins per parish and extract their text, page by page.

Reads data/real/enrichment/bulletin_sources.json (from bulletin_sources.py).
For each parish, takes the newest N distinct bulletin dates (default 6) and downloads each PDF once into
data/raw/bulletins/<id>/ (gitignored). Text is extracted with poppler's pdftotext in two modes:
reading order (<stem>.txt) and column layout (<stem>.layout.txt); pages are separated by form feeds,
so page numbers are kept. Pages with almost no text layer are OCR'd with tesseract (<stem>.ocr.txt).
Bulletins posted as JPG pages are OCR'd directly.

Writes data/real/enrichment/bulletin_manifest.json: one entry per downloaded bulletin with url,
found_on, fetched_at, http status, page count, sha1 and local text paths. Failed URLs are kept with
their status so the gaps are visible.

Usage: python ingest/public/enrichment/fetch_bulletins.py [--per-parish 6] [--ids a,b] [--delay 1.5]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, ROOT, SESSION, load_json, now_iso, save_json  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bulletin_sources import parse_date  # noqa: E402

SOURCES = REAL / "enrichment" / "bulletin_sources.json"
MANIFEST = REAL / "enrichment" / "bulletin_manifest.json"
BDIR = RAW / "bulletins"
MAX_BYTES = 40_000_000
_last_hit: dict[str, float] = defaultdict(float)


def polite_get(url: str, delay: float, timeout: int = 60):
    host = urlparse(url).netloc
    wait = _last_hit[host] + delay - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        _last_hit[host] = time.time()
        return None, f"error:{type(e).__name__}"
    _last_hit[host] = time.time()
    return r, r.status_code


def pdftotext(pdf: Path, layout: bool) -> str:
    cmd = ["pdftotext", "-enc", "UTF-8"] + (["-layout"] if layout else []) + [str(pdf), "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=180).stdout.decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        return ""


def ocr_pdf(pdf: Path, pages: list[int], workdir: Path) -> dict[int, str]:
    out = {}
    workdir.mkdir(parents=True, exist_ok=True)
    for p in pages:
        stem = workdir / f"p{p}"
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-f", str(p), "-l", str(p), "-png", str(pdf), str(stem)],
                       capture_output=True, timeout=180)
        imgs = sorted(workdir.glob(f"p{p}*.png"))
        if not imgs:
            continue
        res = subprocess.run(["tesseract", str(imgs[0]), "-", "--psm", "3"], capture_output=True, timeout=300)
        out[p] = res.stdout.decode("utf-8", "ignore")
        for i in imgs:
            i.unlink()
    return out


def ocr_image(img: Path) -> str:
    res = subprocess.run(["tesseract", str(img), "-", "--psm", "3"], capture_output=True, timeout=300)
    return res.stdout.decode("utf-8", "ignore")


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def select(cands: list[dict], n: int) -> list[dict]:
    """Newest n distinct dates; within a date prefer a link the parish published over a pattern URL."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for c in cands:
        if c.get("date"):
            by_date[c["date"]].append(c)
    order = {"lpi_container": 0, "pdf": 1, "docman": 1, "wix_pdf": 1, "ecatholic": 1, "image_page": 2,
             "lpi_container_pattern": 3, "ecatholic_pattern": 3}
    picked = []
    for d in sorted(by_date, reverse=True):
        group = sorted(by_date[d], key=lambda c: order.get(c["how"], 5))
        picked.append(group)
    return picked[: n + 4]  # spare dates in case some URLs fail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-parish", type=int, default=6)
    ap.add_argument("--ids", default="")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--max-undated", type=int, default=4)
    args = ap.parse_args()
    ids = set(filter(None, args.ids.split(",")))
    sources = load_json(SOURCES)["records"]
    manifest = load_json(MANIFEST, {"records": {}})
    recs = manifest.setdefault("records", {})
    for src in sources:
        pid = src["id"]
        if ids and pid not in ids:
            continue
        if not src["candidates"]:
            continue
        pdir = BDIR / pid
        pdir.mkdir(parents=True, exist_ok=True)
        entries = {e["url"]: e for e in recs.get(pid, [])}
        got_dates = {e["date"] for e in entries.values() if e.get("ok")}
        groups = select(src["candidates"], args.per_parish)
        undated = [c for c in src["candidates"] if not c.get("date")][: args.max_undated]
        # image-page bulletins: group JPG pages by date into one "bulletin"
        img_groups = defaultdict(list)
        for g in groups:
            for c in g:
                if c["how"] == "image_page":
                    img_groups[c["date"]].append(c)
        n_ok = len(got_dates)
        for g in groups:
            if n_ok >= args.per_parish:
                break
            d = g[0]["date"]
            if d in got_dates:
                continue
            if all(c["how"] == "image_page" for c in g):
                pages = sorted(img_groups[d], key=lambda c: c["url"])
                texts, ok_pages = [], 0
                for c in pages:
                    fn = pdir / Path(urlparse(c["url"]).path).name
                    if not fn.exists():
                        r, st = polite_get(c["url"], args.delay)
                        if r is None or not r.ok:
                            continue
                        fn.write_bytes(r.content)
                    texts.append(ocr_image(fn))
                    ok_pages += 1
                if ok_pages:
                    txt = pdir / f"{d}.ocr.txt"
                    txt.write_text("\f".join(texts), encoding="utf-8")
                    entries[pages[0]["url"]] = {"date": d, "url": pages[0]["url"], "page_urls": [c["url"] for c in pages],
                                                "found_on": pages[0].get("found_on"), "how": "image_page",
                                                "fetched_at": now_iso(), "status": 200, "ok": True, "pages": ok_pages,
                                                "text_path": rel(txt), "layout_path": None, "ocr": True}
                    got_dates.add(d)
                    n_ok += 1
                continue
            for c in g:
                if c["how"] == "image_page":
                    continue
                if c["url"] in entries and not entries[c["url"]].get("ok"):
                    continue  # failed before; don't hammer
                ok = fetch_one(pid, pdir, c, d, entries, args.delay)
                if ok:
                    got_dates.add(d)
                    n_ok += 1
                    break
        for c in undated:
            if c["url"] in entries:
                continue
            fetch_one(pid, pdir, c, None, entries, args.delay)
        recs[pid] = sorted(entries.values(), key=lambda e: (e.get("date") or ""), reverse=True)
        oks = [e for e in recs[pid] if e.get("ok")]
        print(f"{pid:48s} ok={len(oks)} dates={[e['date'] for e in oks][:8]}", flush=True)
        manifest["generated_at"] = now_iso()
        save_json(MANIFEST, manifest)


_DIRECTORY: dict[str, dict] = {}


def name_match(pid: str, text: str) -> bool:
    """Does the bulletin text mention the parish's distinctive name word(s)? Guards against a wrong LPi code."""
    if not _DIRECTORY:
        _DIRECTORY.update({r["id"]: r for r in load_json(REAL / "parishes_directory.json")["records"]})
    name = _DIRECTORY.get(pid, {}).get("name", "")
    words = [w for w in re.findall(r"[A-Za-z]{4,}", name)
             if w.lower() not in {"parish", "church", "saint", "catholic", "community", "mission", "cathedral", "lady"}]
    low = text[:60000].lower()
    return any(w.lower() in low for w in words) if words else True


def fetch_one(pid: str, pdir: Path, c: dict, d: str | None, entries: dict, delay: float) -> bool:
    url = c["url"]
    stem = d or ("undated-" + hashlib.sha1(url.encode()).hexdigest()[:10])
    pdf = pdir / f"{stem}.pdf"
    status = 200
    if not pdf.exists():
        r, status = polite_get(url, delay)
        body = r.content if (r is not None and r.ok) else b""
        if r is None or not r.ok or not body.startswith(b"%PDF") or len(body) > MAX_BYTES:
            entries[url] = {"date": d, "url": url, "found_on": c.get("found_on"), "how": c["how"],
                            "fetched_at": now_iso(), "status": status, "ok": False,
                            "reason": "not a pdf" if (r is not None and r.ok) else "http"}
            return False
        pdf.write_bytes(body)
    data = pdf.read_bytes()
    raw = pdftotext(pdf, layout=False)
    lay = pdftotext(pdf, layout=True)
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    thin = [i + 1 for i, t in enumerate(pages) if len(t.strip()) < 150]
    ocr_used = False
    if pages and len(thin) >= max(1, len(pages) // 2):
        ocr = ocr_pdf(pdf, thin[:16], pdir / "_ocr")
        if ocr:
            ocr_used = True
            for p, t in ocr.items():
                pages[p - 1] = t
            raw = "\f".join(pages)
    if d is None:
        first = "\n".join(pages[:2])[:4000]
        dd = parse_date(first)
        if dd:
            d = dd.isoformat()
            newpdf = pdir / f"{d}.pdf"
            if not newpdf.exists():
                pdf.rename(newpdf)
                pdf = newpdf
            stem = d
    txt = pdir / f"{stem}.txt"
    txt.write_text(raw, encoding="utf-8")
    layp = pdir / f"{stem}.layout.txt"
    layp.write_text(lay, encoding="utf-8")
    entries[url] = {"date": d, "url": url, "found_on": c.get("found_on"), "how": c["how"],
                    "fetched_at": now_iso(), "status": status, "ok": True, "pages": len(pages),
                    "name_match": name_match(pid, raw),
                    "chars": len(raw), "sha1": hashlib.sha1(data).hexdigest(), "pdf_path": rel(pdf),
                    "text_path": rel(txt), "layout_path": rel(layp), "ocr": ocr_used}
    return True


if __name__ == "__main__":
    main()
