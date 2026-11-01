"""National parish-bulletin groups (lane G9): fetch the latest bulletin of every US parish site that links one, extract the
informal groups (men's/women's, Bible study, young adults, prayer groups...) and write data/real/us/bulletins/<diocese>/<domain>.json.

    python3 -I ingest/us/bulletins/run.py [--minutes 100] [--workers 8] [--dioceses a,b] [--limit N] [--refresh] [--extract-only]

Reuses California's code at run time, never copies or edits it:
  * ingest/california/bulletins/fetch.py   link resolution (PDF / bulletins page / LPi container code seen on the page /
    eCatholic file pattern / Flocknote note), polite Net (robots.txt, >= 1 s per host, one request per host, challenge /
    403 / 429 remembered), pdftotext, tesseract OCR for image-only pages. Its path constants are re-pointed at data/raw/us/.
  * ingest/california/bulletins/extract_groups.py   rule-based group extraction + privacy screens (extract_issue,
    merge_site, final_screen, site_groups_norm).
Differences from California: input = data/real/us/sites/<diocese>/<domain>.json (crawl_status ok and a `bulletin` link
with a url); no discovermass.com request is ever made (policy: its Crawl-delay and the proxy); a `discovermass` bulletin
link is only used when the parish's own page it was found on links a PDF. Only links/codes already seen are used.

Priority: sites with Mass times but no groups first, then times + groups, then the rest; DiscoverMass-only links last.
Resumable: a site with a state file in data/raw/us/bulletins/_state/<diocese>/ is skipped (--refresh redoes).
Exit 3: egress closed (nothing recorded about any site).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib.util
import json
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "us"))
import common_fetch  # noqa: E402  (egress_open, https_only)


def _load(name: str, rel: str):
    p = ROOT / rel
    sys.path.insert(0, str(p.parent))
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


F = _load("ca_bulletin_fetch", "ingest/california/bulletins/fetch.py")
X = _load("ca_bulletin_extract", "ingest/california/bulletins/extract_groups.py")

SITES = ROOT / "data" / "real" / "us" / "sites"
OUT = ROOT / "data" / "real" / "us" / "bulletins"
RAW = ROOT / "data" / "raw" / "us" / "bulletins"
STATE = RAW / "_state"
# re-point California's fetcher at the national paths
F.RAW, F.STATE, F.HTTP_CACHE = RAW, STATE, RAW / "_http"
F.SITES_RAW = ROOT / "data" / "raw" / "us" / "sites"
F.DM_RAW = ROOT / "data" / "raw" / "us" / "discovermass" / "pages"

_orig_get = F.Net.get


def _get_no_dm(self, url, *a, **k):
    if "discovermass.com" in url.lower():
        return {"status": None, "cls": "skipped_policy_no_discovermass", "body": b"", "final_url": url,
                "fetched_at": F.now_iso(), "from_cache": False, "err": None, "last_modified": None, "truncated": False}
    return _orig_get(self, common_fetch.https_only(url), *a, **k)


F.Net.get = _get_no_dm
NOW = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # noqa: E731


def work_items(dioceses: list[str] | None) -> list[dict]:
    items = []
    for f in sorted(SITES.glob("*/*.json")):
        if f.name == "progress.json":
            continue
        dio = f.parent.name
        if dioceses and dio not in dioceses:
            continue
        d = F.load(f, {}) or {}
        b = d.get("bulletin")
        if d.get("crawl_status") != "ok" or not isinstance(b, dict) or not b.get("url"):
            continue
        times, groups = bool(d.get("schedule_rows")), bool(d.get("groups"))
        kind = b.get("kind")
        prio = (3 if kind == "discovermass" else 0) + (0 if times and not groups else 1 if times else 2)
        names, cities = d.get("names") or [], d.get("cities") or []
        items.append({
            "slug": dio, "site_key": f.stem, "site_file": str(f.relative_to(ROOT)), "domain": d.get("domain") or f.stem,
            "home_url": d.get("final_url") or d.get("home_url"), "crawl_status": d.get("crawl_status"),
            "crawl_env": d.get("crawl_env"), "bulletin": b, "prio": prio, "times": times, "groups_before": len(d.get("groups") or []),
            "parishes": [{"id": cid, "name": names[i] if i < len(names) else None, "city": cities[i] if i < len(cities) else None,
                          "dm_url": None} for i, cid in enumerate(d.get("matched_church_ids") or [f.stem])],
            "names": names})
    items.sort(key=lambda x: (x["prio"], x["slug"], x["site_key"]))
    return items


def extract_site(it: dict, st: dict) -> dict:
    ok = [x for x in st.get("issues") or [] if x.get("ok") and x.get("text_path")]
    found = []
    pnames = it["names"] or [p.get("name") or "" for p in it["parishes"]]
    for iss in ok:
        tp = ROOT / iss["text_path"]
        if tp.exists():
            found += X.extract_issue(tp.read_text(errors="ignore"), iss, pnames, None)
    kept, dropped = [], 0
    for g in X.merge_site(found, it["site_key"]):
        if X.final_screen(g):
            dropped += 1
            continue
        kept.append(g)
    sg = X.site_groups_norm(it["site_file"])
    for g in kept:
        g["also_on_parish_website"] = X.norm_name(g["name"]) in sg or f"sub:{g['subcategory']}:{g['language']}" in sg
    gained = it["groups_before"] == 0 and bool(kept)
    return {
        "diocese": it["slug"], "domain": it["domain"], "site_key": it["site_key"], "site_file": it["site_file"],
        "parishes": [{"church_id": p["id"], "name": p.get("name"), "city": p.get("city")} for p in it["parishes"]],
        "bulletin_link": st.get("bulletin_link"), "status": st.get("status"),
        "had_mass_times": it["times"], "site_groups_before": it["groups_before"],
        "gained_first_groups": gained,
        "issues": [{k: x.get(k) for k in ("url", "date", "date_source", "pages", "ocr", "ocr_pages", "found_via", "fetched_at")}
                   for x in ok],
        "notes": st.get("notes") or [],
        "tried": [{"url": x.get("url"), "cls": x.get("cls"), "status": x.get("status")}
                  for x in st.get("issues") or [] if not x.get("ok") and x.get("kept") is not False][:6],
        "checked_at": st.get("checked_at"), "built_at": NOW(),
        "built_by": "ingest/us/bulletins/run.py (California's fetch.py / extract_groups.py)",
        "privacy": "no lay names, phones or e-mails; contact is 'ask the parish office'; names and places pass "
                   "ingest/public/privacy.py and ingest/connectors/calendars/privacy.py",
        "privacy_dropped": dropped,
        "groups": kept,
    }


def write_site(it: dict, st: dict) -> dict:
    rec = extract_site(it, st)
    p = OUT / it["slug"] / f"{it['site_key']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n")
    return rec


def process(net, it, extract_only=False):
    sp = STATE / it["slug"] / f"{it['site_key']}.json"
    if extract_only:
        st = F.load(sp)
        if not st:
            return None
    else:
        try:
            st = F.resolve_site(net, it)
        except Exception as e:  # noqa: BLE001
            st = {"slug": it["slug"], "site_key": it["site_key"], "status": f"error:{type(e).__name__}", "issues": [],
                  "bulletin_link": it["bulletin"], "checked_at": NOW(), "notes": [str(e)[:200]]}
            F.save(sp, st)
            return st, None
    return st, write_site(it, st)


def write_readme() -> dict:
    recs = [json.loads(p.read_text()) for p in sorted(OUT.glob("*/*.json"))]
    by = {}
    for r in recs:
        c = by.setdefault(r["diocese"], Counter())
        c["sites"] += 1
        c["pdf_ok"] += r["status"] == "ok"
        c["issues"] += len(r["issues"])
        c["sites_with_groups"] += bool(r["groups"])
        c["gained_first_groups"] += bool(r["gained_first_groups"])
        c["groups"] += len(r["groups"])
        c["informal"] += sum(1 for g in r["groups"] if g["informal"])
        c["times_no_groups_sites"] += bool(r["had_mass_times"] and not r["site_groups_before"])
    tot = Counter()
    for c in by.values():
        tot.update(c)
    status = Counter(r["status"] for r in recs)
    kinds = Counter()
    for r in recs:
        for g in r["groups"]:
            if g["informal"]:
                kinds[g["informal_kind"]] += 1
    cols = ["sites", "pdf_ok", "issues", "times_no_groups_sites", "sites_with_groups", "gained_first_groups", "groups", "informal"]
    rows = "\n".join(f"| {d} | " + " | ".join(str(by[d][c]) for c in cols) + " |" for d in sorted(by))
    rows += "\n| **all** | " + " | ".join(f"**{tot[c]}**" for c in cols) + " |"
    txt = (ROOT / "ingest/us/bulletins/README.template.md").read_text()
    txt = (txt.replace("{{ROWS}}", rows).replace("{{STATUS}}", ", ".join(f"{k} {v}" for k, v in status.most_common()))
           .replace("{{KINDS}}", ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
           .replace("{{DATE}}", NOW()[:10]))
    (OUT / "README.md").write_text(txt)
    return {"sites": tot["sites"], "pdf_ok": tot["pdf_ok"], "gained": tot["gained_first_groups"], "groups": tot["groups"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dioceses", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--extract-only", action="store_true", help="re-run extraction from cached text, no network")
    a = ap.parse_args()
    items = work_items([d for d in a.dioceses.split(",") if d] or None)
    n_all = len(items)
    if not a.refresh and not a.extract_only:
        items = [x for x in items if not (STATE / x["slug"] / f"{x['site_key']}.json").exists()]
    if a.limit:
        items = items[: a.limit]
    if not a.extract_only:
        ok, probes = common_fetch.egress_open()
        if not ok:
            print("egress closed; nothing recorded", probes)
            return 3
    seed = {}
    net = F.Net(seed)
    deadline = time.time() + a.minutes * 60
    print(f"{n_all} sites with a bulletin link; {len(items)} to do (prio 0 = times but no groups: "
          f"{sum(1 for x in items if x['prio'] == 0)})", flush=True)
    touched, done, lock = set(), [0], threading.Lock()
    stats = Counter()

    def job(it):
        if time.time() > deadline:
            return None
        return it, process(net, it, a.extract_only)

    RAW.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for fut in cf.as_completed([ex.submit(job, it) for it in items]):
            r = fut.result()
            if not r or not r[1]:
                continue
            it, (st, rec) = r[0], r[1] if isinstance(r[1], tuple) else (r[1], None)
            if rec is None:
                continue
            touched.add(it["slug"])
            stats["sites"] += 1
            stats["pdf_ok"] += rec["status"] == "ok"
            stats["gained"] += bool(rec["gained_first_groups"])
            stats["groups"] += len(rec["groups"])
            done[0] += 1
            print(f"[{done[0]}] {it['slug'][:28]:28s} {it['site_key'][:30]:30s} {str(rec['status']):20s} issues={len(rec['issues'])} "
                  f"groups={len(rec['groups'])}{' GAINED' if rec['gained_first_groups'] else ''}", flush=True)
    s = write_readme()
    F.save(RAW / "_last_touched.json", sorted(touched))
    print("chunk:", dict(stats), "| all:", s, f"| {net.requests} robots/other requests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
