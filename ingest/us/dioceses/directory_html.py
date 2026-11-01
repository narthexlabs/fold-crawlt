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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="html,html_unknown")
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
        return RN.save(slug, see, out["base"], out["records"], out["log"], SCRIPT, "diocese-directory-html",
                       f"ingest/us/dioceses/{SCRIPT}", reader, probe.get("status"))

    RN.run(todo, one, a.workers, a.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
