"""Find and download the latest bulletins for every parish from the two central hosts (LPi, DiscoverMass/Diocesan).

    python3 -I ingest/public/bulletins/fetch.py [--diocese oakland] [--limit N] [--ids a,b] [--per-parish 2]
                                                [--state ca] [--refresh]

Parishes: data/seed/entities.json, type parish or mission, all statuses (closed/merged parishes are searched too:
a listing or a fresh bulletin is evidence the place is still active). --diocese keeps entities whose
attrs.diocese (default "oakland" for this seed) matches; the DiscoverMass page's own "Diocese of X" line is
recorded and a mismatch is logged and the match dropped.

Per parish:
  1. DiscoverMass: confirmed slug (earlier transcription or dm_overrides.json) else conservative name+city match
     (hosts.dm_match). The page is parsed for schedule rows (used by scripts/crosscheck_mass_times.py) and for its
     Bulletins tab (Diocesan-printed bulletins); the newest --per-parish PDFs are downloaded.
  2. LPi: every container code *seen* for this parish (crawl JSON, cached parish HTML, earlier bulletin text,
     bulletin_sources.json, bulletin_manual.json, the DiscoverMass page) is probed for the last 5 Sundays.

Outputs
  data/raw/bulletins/<entity_id>/<host>_<YYYY-MM-DD>.pdf (+ .txt)   gitignored
  data/real/bulletins/index.json        entity_id, host, url, date, fetched_at, found_via, local paths
  data/real/bulletins/match_log.json    per parish: DiscoverMass decision + candidates, LPi codes and probes
  data/real/bulletins/discovermass_pages.json   parsed DiscoverMass pages (schedule rows, bulletin links, diocese)
Resumable and idempotent: state is written after every parish; HTTP responses are cached in data/raw/hosts/.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402
from hosts import RAW, REAL, ROOT, Polite, now_iso  # noqa: E402

OUT = REAL / "bulletins"
INDEX, MATCHLOG, DMPAGES = OUT / "index.json", OUT / "match_log.json", OUT / "discovermass_pages.json"
OVERRIDES = OUT / "dm_overrides.json"
BDIR = RAW / "bulletins"


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def save(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def known_dm() -> dict[str, list[str]]:
    """Slugs confirmed by hand on 2026-10-30 (mass_times_fill) + dm_overrides.json ({id: [urls] | []})."""
    out = {}
    fill = load(REAL / "parish_sites/mass_times_fill_2026-10-30.json", {})
    for p in fill.get("parishes", []):
        u = (p.get("secondary_source") or {}).get("url")
        if u:
            out[p["entity_id"]] = [x.strip() for x in u.split(";")]
    for k, v in load(OVERRIDES, {}).get("parishes", {}).items():
        out[k] = v["urls"]
    return out


_CRAWL: dict[str, dict[str, set[str]]] = {}


def _crawl_codes() -> dict[str, dict[str, set[str]]]:
    """Codes per parish in the crawl JSON files, read once (they are large)."""
    if not _CRAWL:
        for f in ("parish_sites.json", "parish_sites/residential.json"):
            d = load(REAL / f, {})
            recs = d.get("records", d) if isinstance(d, dict) else d
            for r in (recs if isinstance(recs, list) else recs.values()):
                if isinstance(r, dict) and r.get("id"):
                    _CRAWL.setdefault(f, {})[r["id"]] = hosts.lpi_codes_in(json.dumps(r))
        _CRAWL["src"] = {r["id"]: set((r.get("lpi_codes") or {}).keys())
                         for r in load(REAL / "enrichment/bulletin_sources.json", {}).get("records", [])}
        _CRAWL["man"] = {k: set((v.get("lpi_codes") or {}).keys()) for k, v in
                         load(ROOT / "ingest/public/enrichment/bulletin_manual.json", {}).get("parishes", {}).items()}
    return _CRAWL


def lpi_seen_codes(pid: str) -> dict[str, str]:
    """code -> where it was seen."""
    seen: dict[str, str] = {}

    def add(codes, where):
        for c in codes:
            seen.setdefault(c, where)
    cc = _crawl_codes()
    for f in ("parish_sites.json", "parish_sites/residential.json"):
        add(cc.get(f, {}).get(pid, ()), f"data/real/{f}")
    add(cc["src"].get(pid, ()), "data/real/enrichment/bulletin_sources.json")
    add(cc["man"].get(pid, ()), "ingest/public/enrichment/bulletin_manual.json")
    for d in (RAW / "parish_sites" / pid, BDIR / pid):
        if d.exists():
            for fp in sorted(d.iterdir()):
                if fp.suffix in (".html", ".txt") and not fp.name.endswith(".layout.txt"):
                    add(hosts.lpi_codes_in(fp.read_text(errors="ignore")[:400000]), str(fp.relative_to(ROOT)))
    return seen


def pdf_text(pdf: Path) -> Path | None:
    txt = pdf.with_suffix(".txt")
    if not txt.exists():
        try:
            r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, timeout=180)
            txt.write_bytes(r.stdout)
        except subprocess.TimeoutExpired:
            return None
    return txt


def download(f: Polite, pid: str, host: str, b: dict) -> dict | None:
    m, body = f.get(b["url"], max_age_days=None, timeout=120)
    if m.get("status") != 200 or body[:4] != b"%PDF":
        return {"ok": False, "status": m.get("status"), **b}
    d = BDIR / pid
    d.mkdir(parents=True, exist_ok=True)
    pdf = d / f"{host}_{b.get('date') or 'undated'}.pdf"
    if not pdf.exists():
        pdf.write_bytes(body)
    txt = pdf_text(pdf)
    return {"ok": True, **b, "fetched_at": m["fetched_at"], "pdf_path": str(pdf.relative_to(ROOT)),
            "text_path": str(txt.relative_to(ROOT)) if txt else None, "bytes": len(body)}


def parishes(diocese: str | None, ids: set[str]) -> list[dict]:
    ents = load(ROOT / "data/seed/entities.json", {})["entities"]
    out = []
    for e in ents:
        if e["type"] not in ("parish", "mission"):
            continue
        dio = (e.get("attrs") or {}).get("diocese") or "oakland"
        if diocese and dio != diocese.lower():
            continue
        if ids and e["id"] not in ids:
            continue
        out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diocese", default="oakland")
    ap.add_argument("--state", default="ca")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="")
    ap.add_argument("--per-parish", type=int, default=2)
    ap.add_argument("--refresh", action="store_true", help="redo parishes already in match_log")
    a = ap.parse_args()
    f = Polite()
    index = load(INDEX, {"records": []})
    mlog = load(MATCHLOG, {"parishes": {}})
    pages = load(DMPAGES, {"pages": {}})
    dm_ix = hosts.dm_index(f, a.state)
    known = known_dm()
    todo = parishes(a.diocese, set(filter(None, a.ids.split(","))))
    if a.limit:
        todo = todo[: a.limit]
    done = 0
    for e in todo:
        pid = e["id"]
        if pid in mlog["parishes"] and not a.refresh:
            continue
        log = {"name": e["name"], "city": e.get("city"), "status": e.get("status"), "checked_at": now_iso()}
        recs = []
        # ---- DiscoverMass
        if pid in known:
            urls, why, cands = known[pid], "confirmed earlier (mass_times_fill / dm_overrides)", []
        else:
            u, why, cands = hosts.dm_match(e["name"], e.get("city") or "", dm_ix)
            urls = [u] if u else []
            top = [c for c in cands if cands and c["score"] == cands[0]["score"] == 2 and not c["alias"]]
            want = hosts.name_tokens(e["name"])
            if not u and 2 <= len(top) <= 3 and all(hosts.name_tokens(c["slug"][: -len("-" + hosts.slugify(c["city_used"]) + "-" + a.state)].replace("-", " ")) <= want
                                                    for c in top):
                urls, why = [c["url"] for c in top], "multi-church parish: every candidate's name is part of the parish name"
        log["discovermass"] = {"decision": why, "urls": urls,
                               "candidates": [{k: c[k] for k in ("slug", "score", "city_used")} for c in cands[:6]]}
        dm_bulls = []
        for u in urls:
            m, body = f.get(u, max_age_days=7)
            if m.get("status") != 200:
                log["discovermass"].setdefault("errors", []).append({"url": u, "status": m.get("status")})
                continue
            pg = hosts.dm_parse(body, u)
            pg["fetched_at"] = m["fetched_at"]
            pg["entity_id"] = pid
            if pg["diocese"] and a.diocese and a.diocese.lower() not in pg["diocese"].lower():
                log["discovermass"].setdefault("dropped", []).append({"url": u, "reason": f"page says {pg['diocese']}"})
                continue
            pages["pages"][u] = pg
            dm_bulls += [dict(b, page=u) for b in pg["bulletins"]]
            for c in hosts.lpi_codes_in(body.decode("utf-8", "ignore")):
                log.setdefault("lpi_codes_from_dm", []).append(c)
        dm_bulls.sort(key=lambda b: b.get("date") or "", reverse=True)
        for b in dm_bulls[: a.per_parish]:
            r = download(f, pid, "discovermass", {"url": b["url"], "date": b.get("date"), "found_on": b["page"]})
            if r and r["ok"]:
                recs.append(dict(r, entity_id=pid, host="discovermass", found_via="DiscoverMass Bulletins tab"))
            else:
                log["discovermass"].setdefault("download_failed", []).append(r)
        log["discovermass"]["bulletins_listed"] = len(dm_bulls)
        # ---- LPi
        codes = lpi_seen_codes(pid)
        for c in log.get("lpi_codes_from_dm", []):
            codes.setdefault(c, "discovermass page")
        log["lpi"] = {"codes": codes, "probes": []}
        for code, where in sorted(codes.items()):
            found, tried = hosts.lpi_latest(f, code, want=a.per_parish)
            log["lpi"]["probes"].append({"code": code, "tried": len(tried), "found": [x["date"] for x in found]})
            for b in found:
                r = download(f, pid, "lpi", {"url": b["url"], "date": b["date"], "found_on": where})
                if r and r["ok"]:
                    recs.append(dict(r, entity_id=pid, host="lpi", lpi_code=code,
                                     found_via=f"LPi container, code seen in {where}"))
        index["records"] = [x for x in index["records"] if x["entity_id"] != pid] + [
            {k: r.get(k) for k in ("entity_id", "host", "url", "date", "fetched_at", "found_via", "found_on", "lpi_code",
                                   "pdf_path", "text_path", "bytes")} for r in recs]
        log["bulletins"] = len(recs)
        mlog["parishes"][pid] = log
        index["generated_at"] = mlog["generated_at"] = pages["generated_at"] = now_iso()
        mlog["robots"] = f.robots_notes()
        save(INDEX, index)
        save(MATCHLOG, mlog)
        save(DMPAGES, pages)
        done += 1
        print(f"{pid:48s} dm={len(urls)} ({why[:40]}) dm_bulletins={len(dm_bulls)} lpi_codes={len(codes)} -> {len(recs)}",
              flush=True)
    print(f"done {done} parishes; {f.requests} HTTP requests; index has {len(index['records'])} bulletins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
