#!/usr/bin/env python3
"""Parish pages behind a WordPress REST door (lane G4b): the 49 `rest-pages` sees and the 12 `open-rest` sees.

    python3 -I ingest/us/dioceses/directory_pages.py [--status rest-pages,open-rest] [--only slug,slug]
                                                     [--minutes 100] [--workers 16] [--refresh] [--force]

open-rest  The probe's REST records rarely carry the parish website (Charlotte 0/95): it is printed on each parish's
           rendered page. So: the REST collection (cached by the probe) gives every record's `link`; each page is read
           (<=1 req/s per host) for the website, address and coordinates; the REST record keeps its name and rest_id.
           Fields that the REST record already had (address, zip, website from an ACF field) are kept.
rest-pages The REST search and the sitemap's parish-like sub-sitemaps (crb_parish-sitemap.xml, wp-sitemap-posts-parishes-1.xml
           ...) enumerate parish pages; the probe's search hits name the directory page. Then dirlib.read_see: list page
           entries, pagination, detail pages.
Every see gets a `cms_guess`. Output: data/real/us/dioceses/<slug>/parishes.json (kept only when it has more websites
than the file already there) and a row in reader_status.json. Resumable from the raw cache.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dirlib as DL  # noqa: E402
import runner as RN  # noqa: E402
import vendors as VD  # noqa: E402
import common_fetch as CF  # noqa: E402
from directory_probe import Probe  # noqa: E402

SCRIPT = "directory_pages.py"


def rest_records(see: dict, probe: dict, pol, refresh: bool) -> tuple[list[dict], str | None]:
    """Re-read the probe's REST collection (from cache) with the improved Probe.record()."""
    ptype = probe.get("parish_type")
    if not ptype:
        return [], None
    pr = Probe(see, pol, refresh)
    pr.base = probe.get("base") or pr.base
    coll = next((c for c in (probe.get("doors") or {}).get("collections", []) if c.get("type") == ptype), {})
    rest_base = coll.get("rest_base") or ptype
    items, _ = pr.collection(rest_base)
    recs = [r for r in (pr.record(it, ptype) for it in items) if r]
    return recs, rest_base


def read_open_rest(see, probe, pol, refresh, minutes):
    t0 = time.time()
    recs, rest_base = rest_records(see, probe, pol, refresh)
    base = probe.get("base")
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=700)
    own = {CF.host_of(base)}
    hs, hr = F.soup(base + "/")
    chrome = DL.ext_hosts(hs, own) if hs is not None else set()
    cms = DL.cms_guess(hr["body"]) if hs is not None else "wordpress"
    raw, idx = [], []
    for i, r in enumerate(recs):
        if time.time() - t0 > minutes * 60:
            break
        u = r.get("source_url") or ""
        if not u.startswith("http") or "/wp-json/" in u:
            continue
        s, rr = F.soup(u)
        if s is None:
            continue
        own.add(CF.host_of(rr.get("final_url") or u))
        d = DL.detail_record(s, rr.get("final_url") or u, own, see.get("name", ""))
        if d is None:   # title did not look like a parish name; keep the REST name
            s, rr = F.soup(u)
            if s is None:
                continue
            DL.strip_chrome(s)
            area = DL.main_area(s)
            links = []
            for a in area.find_all("a", href=True):
                uu = DL.clean_href(DL.urljoin(u, a["href"]))
                if DL.website_ok(uu, own):
                    links.append({"url": uu, "host": CF.host_of(uu), "text": DL.clean(a.get_text(" ")), "label": "", "prev": ""})
            oph, oem = DL.office_contacts(area)
            d = {"name": r["name"], "links": links, "lat": None, "lng": None, **DL.address_from(area.get_text(", ")),
                 "source_url": u, "office_phone": oph, "office_email": oem}
        raw.append(d)
        idx.append(i)
    dets = DL.finish_details(raw, chrome)
    for i, d in zip(idx, dets):
        r = recs[i]
        if d.get("website") and (not r.get("website") or r["website"].rstrip("/") == d["website"].rstrip("/")):
            r["website"], r["website_basis"] = d["website"], "page-" + (d.get("website_basis") or "")
        elif r.get("website"):
            r["website_basis"] = "rest-field"
        for k in ("address", "city", "zip", "lat", "lng", "office_phone", "office_email"):
            r[k] = r.get(k) or d.get(k)
        r["state"] = d.get("state")
        r["_conf"] = "page" if d.get("website") else "rest"
    for r in recs:
        r.setdefault("_conf", "rest")
        if r.get("website") and not r.get("website_basis"):
            r["website_basis"] = "rest-content"
    log = {"cms_guess": cms, "rest_base": rest_base, "rest_records": len(recs), "detail_pages": len(raw),
           "requests_net": F.n_net, "requests_cached": F.n_cache, "fetch_classes": dict(F.classes),
           "seconds": round(time.time() - t0), "lists": [{"url": f"{base}/wp-json/wp/v2/{rest_base}"}]}
    return {"records": recs, "log": log, "base": base}


def rest_page_hints(see, probe, pol, refresh) -> list[str]:
    """Extra parish-list page URLs from WordPress REST search (pages only)."""
    base = probe.get("base")
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=10)
    out = []
    for q in ("parish directory", "find a parish", "parishes"):
        r = F.get(f"{base}/wp-json/wp/v2/search?" + urlencode({"search": q, "subtype": "page", "per_page": 20}),
                  accept="application/json")
        try:
            js = json.loads(r["body"]) if r["class"] == "ok" else []
        except ValueError:
            js = []
        for h in js if isinstance(js, list) else []:
            t = DL.clean(h.get("title"))
            u = h.get("url") or ""
            if re.search(r"parish|church|director|find", t + " " + u, re.I) and not re.search(
                    r"school|staff|clergy|closed|former|support|resource|portal|bulletin|employ", t + u, re.I):
                out.append(u)
    return list(dict.fromkeys(out))[:6]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="rest-pages,open-rest")
    ap.add_argument("--only", default="")
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--per-see-minutes", type=float, default=30)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sees, st = RN.load()
    want = set(a.status.split(","))
    only = {x for x in a.only.split(",") if x}
    done = DL.load_reader_status()["sees"]
    todo = []
    for slug, p in st.items():
        if only and slug not in only:
            continue
        if not only and p.get("status") not in want:
            continue
        if not a.force and not only and done.get(slug, {}).get("result") in ("written", "kept-existing", "no-list"):
            continue
        todo.append((slug, sees[slug], p))
    ok, _ = CF.egress_open()
    if not ok:
        print("EGRESS CLOSED; nothing read")
        return 3
    print(f"{len(todo)} sees to read")
    pol = CF.Polite()

    def one(slug, see, probe, deadline=None):
        mins = a.per_see_minutes
        if deadline:
            mins = max(1, min(mins, (deadline - time.time()) / 60))
        if probe.get("status") == "open-rest":
            out = read_open_rest(see, probe, pol, a.refresh, mins)
            return RN.save(slug, see, out["base"], out["records"], out["log"], SCRIPT, "diocese-directory-rest",
                           f"ingest/us/dioceses/{SCRIPT}", "rest+page", probe.get("status"))
        v = VD.try_vendor(see, probe, pol, a.refresh)

        def generic():
            p2 = dict(probe)
            hints = rest_page_hints(see, probe, pol, a.refresh)
            doors = dict(p2.get("doors") or {})
            doors["wp_search_parish"] = list(doors.get("wp_search_parish") or []) + [{"url": u} for u in hints]
            p2["doors"] = doors
            return DL.read_see(see, p2, pol, a.refresh, minutes=mins)
        out, reader = RN.vendor_or_generic(v, generic)
        return RN.save(slug, see, out["base"], out["records"], out["log"], SCRIPT, "diocese-directory-pages",
                       f"ingest/us/dioceses/{SCRIPT}", reader, probe.get("status"))

    RN.run(todo, one, a.workers, a.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
