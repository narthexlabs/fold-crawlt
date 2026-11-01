#!/usr/bin/env python3
"""Generic parish-directory page reader for sees with no parish REST type (lane G4b).

    python3 -I ingest/us/dioceses/directory_html.py [--status html,html_unknown,rest-pages] [--only slug,slug]
                                                    [--minutes 100] [--workers 16] [--refresh] [--force]

For each see whose directory_probe status is in --status (default html,html_unknown): find the directory page (probe's
directory_page, the REST search hits, /parishes/ etc., and home-page links whose text says parish / find a parish /
directory), read entries (external website link + the block naming a parish), follow pagination, and, when the list
has fewer websites than parish pages, read each parish detail page (from the list's own links or a parish-like
sitemap). Engine: dirlib.read_see. Output data/real/us/dioceses/<slug>/parishes.json (only when it has more websites
than an existing file) and a row in data/real/us/dioceses/reader_status.json. Resumable: every response is cached
in data/raw/us/dioceses/<slug>/; a see already `written` is skipped unless --force.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dirlib as DL  # noqa: E402
import runner as RN  # noqa: E402
import vendors as VD  # noqa: E402
import common_fetch as CF  # noqa: E402

SCRIPT = "directory_html.py"

# JS finders whose data request could not be made from page HTML alone (2026-11-01 pass; see vendors.py JS finders).
JS_NOTES = {
    "diocese-of-erie": ("needs_post", "find-parish-school.php -> js/doelocator.js POSTs a form body to doegetjson.php "
                        "(a GET answers 'No latitude parameter passed!'); our fetcher is GET-only, so not read"),
    "diocese-of-evansville": ("challenge", "parishes-map.html embeds batchgeo.com/map/ecdparishes, which answers 403 challenge"),
    "diocese-of-pueblo": ("needs_js", "Wix dynamic page: repeater data comes from Wix's data API with a per-visit "
                          "instance token; warmup data in the HTML holds only menu items"),
    "maronite-eparchy-of-our-lady-of-lebanon": ("needs_js", "Wix dynamic page (store_locator app); data needs a Wix instance token"),
    "ukrainian-catholic-eparchy-of-stamford": ("needs_js", "mywebsitebuilder.com site: /parishes/ page structure JS holds "
                                               "no parish list (content rendered from the builder's API)"),
    "eparchy-of-st-maron-of-brooklyn": ("no_list_on_page", "Squarespace page only links per-state sub-pages; "
                                        "?format=json is robots-disallowed"),
    "archdiocese-of-detroit": ("vendor_search_only", "aod.org/parishes uses Algolia search (index 'aod'); the existing "
                               "file came from parish pages; Algolia not queried"),
    "diocese-of-manchester": ("no_api", "directory.catholicnh.org/churchmap prints h-cards (name, address, lat/lng) in HTML, "
                              "no website field; existing generic read kept"),
    "archdiocese-of-miami": ("unreachable_via_proxy", "miamiarch.org not reachable from the sandbox proxy"),
    "diocese-of-rochester": ("unreachable_via_proxy", "dor.org conn_error from the sandbox"),
    "diocese-of-bridgeport": ("unreachable_via_proxy", "bridgeportdiocese.com dns failure from the sandbox"),
    "diocese-of-reno": ("unreachable_via_proxy", "conn_error"),
    "diocese-of-nashville": ("unreachable_via_proxy", "conn_error"),
    "diocese-of-green-bay": ("unreachable_via_proxy", "conn_error"),
    "diocese-of-lansing": ("unreachable_via_proxy", "timeout"),
    "diocese-of-great-falls-billings": ("unreachable_via_proxy", "conn_error"),
    "byzantine-catholic-archeparchy-of-pittsburgh": ("unreachable_via_proxy", "conn_error"),
    "ukrainian-catholic-archeparchy-of-philadelphia": ("unreachable_via_proxy", "ukrcatholic.org conn_error"),
    "diocese-of-san-diego": ("challenge", "sdcatholic.org answers 403 challenge"),
}


def note_reader(slug: str, res: dict | None, log: dict | None = None, state: str | None = None, why: str | None = None):
    """Per-see `reader` entry in directory_status.json: what the JS-finder pass found (endpoint or the reason it stops)."""
    import fcntl
    import json
    with DL.STATUS_LOCK, open(str(DL.RAW / "reader_status.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        st = json.loads(DL.STATUS.read_text())
        rec = st["sees"].get(slug)
        if rec is None:
            return
        if res and str(res.get("reader", "")).startswith("api:"):
            lst = (log or {}).get("lists") or [{}]
            note = {"state": "api", "vendor": res["reader"][4:], "endpoint": lst[0].get("url"),
                    "finder_page": (log or {}).get("finder_page"), "result": res.get("result"),
                    "records": res.get("records"), "with_website": res.get("with_website")}
        else:
            s, w = JS_NOTES.get(slug, (state or "no_api_found", why or "no data request found in the finder page HTML"))
            note = {"state": s, "why": w, "result": (res or {}).get("result")}
        note["checked_at"] = DL.now_iso()
        note["by"] = "ingest/us/dioceses/directory_html.py --js-finders (vendors.py JS finders)"
        rec["reader"] = note
        DL.STATUS.write_text(json.dumps(st, indent=1, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="html,html_unknown")
    ap.add_argument("--only", default="")
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--per-see-minutes", type=float, default=30)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--js-finders", action="store_true",
                    help="only the sees whose finder is JavaScript (vendors.JS_FINDER_PAGES + JS_NOTES); forces a reread")
    a = ap.parse_args()
    if a.js_finders:
        # sees whose finder data request was found are read; the others only get their note (no request is made)
        prev = DL.load_reader_status()["sees"]
        for slug in sorted(set(JS_NOTES) - set(VD.JS_FINDER_PAGES)):
            note_reader(slug, prev.get(slug))
        a.only = a.only or ",".join(sorted(VD.JS_FINDER_PAGES))
        a.force = True
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
        if not a.force and done.get(slug, {}).get("result") in ("written", "kept-existing", "no-list") \
                and done[slug].get("reader") and not only:
            continue
        todo.append((slug, sees[slug], p))
    ok, _ = CF.egress_open()
    if not ok:
        print("EGRESS CLOSED; nothing read")
        return 3
    print(f"{len(todo)} sees to read")
    pol = CF.Polite()

    def one(slug, see, probe, deadline=None):
        import time
        mins = a.per_see_minutes
        if deadline:
            mins = max(1, min(mins, (deadline - time.time()) / 60))
        v = VD.try_vendor(see, probe, pol, a.refresh)
        out, reader = RN.vendor_or_generic(v, lambda: DL.read_see(see, probe, pol, a.refresh, minutes=mins))
        api = str(reader).startswith("api:")
        res = RN.save(slug, see, out["base"], out["records"], out["log"], SCRIPT,
                      VD.API_LABEL if api else "diocese-directory-html", f"ingest/us/dioceses/{SCRIPT}", reader,
                      probe.get("status"))
        if api or slug in VD.JS_FINDER_PAGES or slug in JS_NOTES:
            note_reader(slug, res, out["log"])
        return res

    RN.run(todo, one, a.workers, a.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
