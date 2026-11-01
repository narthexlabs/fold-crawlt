"""Run loop shared by directory_pages.py and directory_html.py: worker pool across sees (one host per worker, so the
per-host 1 req/s limit holds), time limit, per-see result written to reader_status.json and parishes.json."""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
import traceback

import dirlib as DL
import common_fetch as CF

CONF = {"rest": "rest", "page": "page", "list": "html-generic", "vendor": "html-vendor"}


def load():
    sees = {s["slug"]: s for s in json.loads((DL.US / "dioceses.json").read_text())["dioceses"]}
    st = json.loads(DL.STATUS.read_text())["sees"]
    return sees, st


def better(new: dict, old: dict | None) -> bool:
    if not old:
        return True
    # ties go to the newer read (it carries office contacts and the page-checked websites)
    return (new["with_website"], new["count"]) >= (old.get("with_website", 0), old.get("count", 0))


def save(slug, see, base, recs, log, script, label, method, reader, prev_status):
    recs_out = []
    for conf in ("vendor", "list", "page", "rest"):
        part = [r for r in recs if r.get("_conf") == conf]
        recs_out += DL.finalize_records(part, CONF[conf], f"{script} ({reader})", filter_names=(reader != "rest+page"))
    # drop list entries that are obviously not parishes when the list is mostly websites of other things
    res = {"result": None, "reader": reader, "cms_guess": log.get("cms_guess"), "probe_status": prev_status,
           "checked_at": DL.now_iso(), "log": log}
    old = DL.existing(slug)
    n, w = len(recs_out), sum(1 for r in recs_out if r.get("website"))
    res.update(records=n, with_website=w)
    raw_n = len({(DL.clean(r.get("name")).lower(), CF.host_of(r.get("website") or "") or r.get("source_url")) for r in recs})
    res["records_before_filter"] = raw_n
    # quality gate: a list where fewer than 60% of the entries survive the parish-name filter is not a parish directory
    if n >= 5 and n >= 0.6 * raw_n:
        meta_new = {"count": n, "with_website": w}
        # a file this lane's own readers wrote earlier is simply replaced (a rerun applies the newer filters);
        # the probe's REST files are replaced only by a list with more websites
        mine = bool(old and str(old.get("method", "")).startswith("ingest/us/dioceses/directory_"))
        if mine or better(meta_new, old):
            src = (log.get("lists") or [{}])[0].get("url") if any(r.get("_conf") in ("list", "vendor") for r in recs) else base
            DL.write_parishes(slug, see["name"], src or base, label, method, log.get("cms_guess") or "unknown", recs_out,
                              "Read by a generic directory reader: name, address, city, zip and coordinates from the "
                              "diocese's own pages; website = the parish's own domain linked from its entry (website_basis "
                              "says how it was chosen). No phones, e-mails or personal names kept. Spot-check before "
                              "treating as the official list.")
            res["result"] = "written"
        else:
            res["result"] = "kept-existing"
            res["existing"] = {"count": old.get("count"), "with_website": old.get("with_website")}
    else:
        res["result"] = "no-list" if raw_n < 5 else "rejected-not-a-parish-list"
        if old and str(old.get("method", "")).startswith("ingest/us/dioceses/directory_"):
            (DL.OUT / slug / "parishes.json").unlink()      # an earlier, looser pass of ours; withdrawn
            res["withdrew_earlier_file"] = True
    DL.save_reader_status(slug, res)
    return res


def run(todo: list, one, workers: int, minutes: float):
    t0 = time.time()
    out = {}

    def wrap(item):
        slug = item[0]
        if minutes and time.time() - t0 > minutes * 60:
            return slug, None
        cd = ((item[2].get("robots") or {}).get("crawl_delay") or 0) if len(item) > 2 else 0
        if cd and float(cd) > 30:
            # common_fetch.Polite caps waits at 30 s; a longer Crawl-delay (dsj.org: 600) is honoured by not crawling
            r = {"result": "skipped-crawl-delay", "crawl_delay": cd, "checked_at": DL.now_iso()}
            DL.save_reader_status(slug, r)
            return slug, r
        try:
            return slug, one(*item, deadline=t0 + minutes * 60 if minutes else None)
        except Exception as e:  # noqa: BLE001
            r = {"result": "error", "error": repr(e)[:300], "trace": traceback.format_exc()[-800:], "checked_at": DL.now_iso()}
            DL.save_reader_status(slug, r)
            return slug, r

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for slug, r in ex.map(wrap, todo):
            if r:
                out[slug] = r
                print(f"  {slug:58s} {r.get('result'):14s} {r.get('records', 0):4} recs {r.get('with_website', 0):4} web  "
                      f"{r.get('cms_guess') or ''} {r.get('reader') or ''} {r.get('error', '')}", flush=True)
    return out


def webs(out) -> int:
    return sum(1 for r in (out or {}).get("records", []) if r.get("website"))


def vendor_or_generic(v, generic_fn):
    """A vendor read is used alone when it has >= 5 records and half of them carry a website; otherwise the generic
    engine runs too and the result with more websites wins (the vendor's records are kept when they tie)."""
    if v and len(v["records"]) >= 5 and webs(v) >= 0.5 * len(v["records"]):
        return v, v["reader"]
    g = generic_fn()
    if v and len(v["records"]) >= 5 and webs(v) >= webs(g):
        v["log"]["generic_tried"] = {"records": len(g["records"]), "with_website": webs(g)}
        return v, v["reader"]
    if v:
        g["log"]["vendor_tried"] = {"reader": v.get("reader"), "records": len(v["records"]), "with_website": webs(v)}
    return g, "generic"
