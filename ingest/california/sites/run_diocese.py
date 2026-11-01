#!/usr/bin/env python3
"""One diocese pass of the California parish-website crawl.

    python3 -I ingest/california/sites/run_diocese.py prepare <slug>            # fresh input list from DiscoverMass + directory
    python3 -I ingest/california/sites/run_diocese.py fetch <slug>              # sandbox fetch (8 hosts at once, 1 req/s/host)
    python3 -I ingest/california/sites/run_diocese.py laptop-list <slug>        # sites to retry from the home connection
    python3 -I ingest/california/sites/run_diocese.py laptop-merge <slug> <dir> # bring the laptop's raw cache back
    python3 -I ingest/california/sites/run_diocese.py sidedoors <slug>          # machine-readable doors for refused sites
    python3 -I ingest/california/sites/run_diocese.py parse <slug>              # parse + feeds + events -> data/real/california/sites/<slug>/
    python3 -I ingest/california/sites/run_diocese.py parse <slug> --offline    # re-parse the cache with no network: calendar
                                                                                # feeds and events carried from the last parse
    python3 -I ingest/california/sites/run_diocese.py readme                    # statewide README.md from the summary.json files

Outputs (data/real/california/sites/<slug>/): <domain>.json per site, summary.json. See README.md there.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
RAW = ROOT / "data" / "raw" / "california" / "sites"
OUT = ROOT / "data" / "real" / "california" / "sites"
CA = ROOT / "data" / "real" / "california"
TODAY = "2026-10-31"          # the run date (fixes the 90-day window across sandbox and laptop passes)

DEAD = {"dns", "not_found", "parked", "ssl_error", "conn_error", "timeout", "unreachable_via_proxy", "http_4xx", "http_5xx",
        "proxy_denied", "not_html", "https_only"}
REFUSED = {"challenge", "blocked_403", "blocked_429", "robots_disallowed", "http_202"}
# Statuses the laptop pass never retries (same set as laptop_list.NO_RETRY): a site not in this set, not yet asked from the
# home connection, and not read is `laptop_pending`.
NO_RETRY = {"ok", "not_found", "robots_disallowed", "parked", "social_only"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_input(slug: str) -> dict:
    return json.loads((RAW / "_inputs" / f"{slug}.json").read_text("utf-8"))


def cmd_prepare(slug: str) -> None:
    import build_input
    res = build_input.build(slug)
    p = RAW / "_inputs" / f"{slug}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", "utf-8")
    fl = [{"key": s["key"], "home": s["home"]} for s in res["sites"] if not s["social_only"]]
    (RAW / "_inputs" / f"{slug}.fetch.json").write_text(json.dumps(fl))
    print(f"{slug}: {res['directory_records']} directory records, {res['discovermass_records']} DiscoverMass records -> "
          f"{len(res['sites'])} sites ({len(fl)} to crawl)")


def cmd_fetch(slug: str, workers: int, max_seconds: float) -> None:
    (RAW / "_logs").mkdir(parents=True, exist_ok=True)
    log = RAW / "_logs" / f"{slug}.sandbox.log"
    args = [sys.executable, "-I", str(HERE / "fetch_site.py"), "--input", str(RAW / "_inputs" / f"{slug}.fetch.json"),
            "--raw-root", str(RAW), "--workers", str(workers), "--env", "sandbox", "--window-start", TODAY]
    if max_seconds:
        args += ["--max-seconds", str(max_seconds)]
    with open(log, "a") as f:
        subprocess.run(args, stdout=f, stderr=subprocess.STDOUT, check=False)
    print("fetch done; log:", log)


def cmd_laptop_list(slug: str) -> None:
    subprocess.run([sys.executable, "-I", str(HERE / "laptop_list.py"), slug], check=True)


def cmd_laptop_merge(slug: str, src: str) -> None:
    """Copy the laptop's per-site raw dirs into the raw root where the laptop got further than the sandbox."""
    src_p = Path(src)
    mine = {x["key"].replace("/", "__") for x in load_input(slug)["sites"]}
    status_file = RAW / "_laptop" / f"{slug}.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(status_file.read_text()) if status_file.exists() else {}
    n_ok = 0
    for d in sorted(src_p.iterdir()):
        mp = d / "manifest.json"
        if not mp.exists() or d.name not in mine:
            continue
        lm = json.loads(mp.read_text())
        key = lm["key"]
        prev[key] = {"status": lm.get("status"), "complete": lm.get("complete"), "pages": len(lm.get("pages", []))}
        tgt = RAW / d.name
        sandbox_status = json.loads((tgt / "manifest.json").read_text()).get("status") if (tgt / "manifest.json").exists() else None
        if lm.get("status") == "ok" and sandbox_status != "ok":
            if tgt.exists():
                shutil.rmtree(tgt)
            shutil.copytree(d, tgt)
            n_ok += 1
    status_file.write_text(json.dumps(prev, indent=1))
    print(f"laptop results merged: {len(prev)} sites attempted, {n_ok} now ok, statuses {dict(Counter(v['status'] for v in prev.values()))}")


def cmd_sidedoors(slug: str, workers: int) -> None:
    import sidedoor_site
    inp = load_input(slug)["sites"]
    items = []
    for s in inp:
        mp = RAW / s["key"].replace("/", "__") / "manifest.json"
        if s.get("social_only") or not mp.exists():
            continue
        m = json.loads(mp.read_text())
        if m.get("status") in ("challenge", "blocked_403", "blocked_429", "http_202") and not (RAW / s["key"].replace("/", "__") / "sidedoors.json").exists():
            items.append({"key": s["key"], "home": m.get("final_home") or s["home"]})
    print(f"{len(items)} refused sites to ask for side doors")
    res = sidedoor_site.run_many(items, workers=workers, log=lambda x: None)
    print("side doors opened:", sum(1 for r in res.values() if r.get("opened")), "of", len(items))


def social_only_record(info: dict) -> dict:
    import parse_site as P
    plat = {}
    r = P.social_of(info["home"])
    if r and r[0] in P.SOCIAL_KEYS:
        plat[r[0]] = [r[1]]
    return {"domain": info["key"], "diocese": info["slug"], "matched_church_ids": sorted(set(info["directory_ids"] + info["discovermass_ids"])),
            "matched": {"directory": info["directory_ids"], "discovermass": info["discovermass_ids"]}, "names": info["names"],
            "cities": info["cities"], "home_url": info["home"], "final_url": info["home"], "crawl_status": "social_only",
            "crawl_env": None, "pages": [], "cms_guess": {"cms": "social_only", "evidence": [info["home"]]}, "template": "none",
            "groups": [], "calendar_feeds": [], "events": [], "social": plat, "bulletin": None, "schedule_rows": [],
            "source_url": info["home"], "fetched_at": now_iso(),
            "note": "the listed website is a social-media page; not crawled (login walls), link recorded"}


def build_sidedoor_pages(info: dict, home: str):
    import parse_site as P
    import sidedoor_site
    pages = []
    for kind, url, title, html, fetched in sidedoor_site.pseudo_pages(info["key"], home):
        rec = {"kind": kind, "url": url, "final_url": url, "fetched_at": fetched, "class": "ok"}
        pages.append(P.Page(rec, html.encode("utf-8")))
    return pages


def carry_over_feeds(rec: dict, old: dict | None) -> list[str]:
    """A re-parse reads third-party calendar feeds live. When Google answers a re-read with 429 (or any non-200) for a feed the
    previous parse read fine, keep that feed's earlier events and record (same fetch day, same 90-day window) instead of
    replacing them with nothing. Nothing is retried. Returns the feed ids carried over."""
    if not old:
        return []
    done = []
    old_feeds = {f.get("feed_id"): f for f in old.get("calendar_feeds", [])}
    for i, f in enumerate(rec.get("calendar_feeds", [])):
        lf, fo = f.get("last_fetch") or {}, old_feeds.get(f.get("feed_id"))
        olf = (fo or {}).get("last_fetch") or {}
        if f.get("status") != "active" or lf.get("status") in (None, 200) or olf.get("status") != 200:
            continue
        kept = dict(fo)
        kept["last_fetch"] = dict(olf, carried_over={"reason": f"re-read answered {lf.get('status')}", "re_read_at": lf.get("fetched_at")})
        rec["calendar_feeds"][i] = kept
        have = {e.get("id") for e in rec.get("events", [])}
        rec.setdefault("events", []).extend(e for e in old.get("events", []) if e.get("feed_id") == f["feed_id"] and e.get("id") not in have)
        done.append(f["feed_id"])
    if done:
        ec = dict(old.get("event_counts") or {})
        ec["stored"] = len(rec["events"])
        ec["carried_over_feeds"] = done
        rec["event_counts"] = ec
    return done


def carry_events(rec: dict, old: dict | None) -> None:
    """Offline re-parse: keep the last parse's calendar feeds and events (third-party feeds are read live, so they are
    not re-read here), then re-apply the privacy screens to the events (rescreen.py)."""
    import rescreen
    old = old or {}
    rec["calendar_feeds"] = old.get("calendar_feeds", [])
    rec["events"] = old.get("events", [])
    if old.get("event_counts") is not None:
        rec["event_counts"] = dict(old["event_counts"], carried_offline=True)
    rescreen.rescreen_events(rec)


def cmd_parse(slug: str, only: set[str] | None = None, offline: bool = False) -> None:
    import requests
    import parse_site as P
    import events as E
    import adapters
    inp = load_input(slug)
    if offline:  # nothing may reach the network: any request raises
        def _no_network(*a, **k):
            raise RuntimeError("offline parse: no network")
        requests.sessions.Session.request = _no_network
        import urllib.request
        urllib.request.urlopen = _no_network
    shared = adapters.Fetcher(delay=1.0)
    shared.s.headers["User-Agent"] = requests.utils.default_user_agent()     # plain default, as for the crawl
    laptop = {}
    lp = RAW / "_laptop" / f"{slug}.json"
    if lp.exists():
        laptop = json.loads(lp.read_text())
    out_dir = OUT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.fromisoformat(TODAY)
    n = 0
    for info in inp["sites"]:
        key = info["key"]
        if only and key not in only:
            continue
        d = RAW / key.replace("/", "__")
        mp = d / "manifest.json"
        fn_old = out_dir / (key.replace("/", "__") + ".json")
        if mp.exists() and fn_old.exists() and not info.get("social_only"):
            # Keep a good earlier read (e.g. from the laptop, whose raw cache is not here) when this pass could not read the
            # site (challenge / blocked / http-only from the sandbox): a refusal today does not erase yesterday's page.
            try:
                prev_rec = json.loads(fn_old.read_text("utf-8"))
                if prev_rec.get("crawl_status") == "ok" and json.loads(mp.read_text()).get("status") != "ok":
                    prev_rec["refetch_attempt"] = {"status": json.loads(mp.read_text()).get("status"), "at": TODAY, "env": "sandbox"}
                    fn_old.write_text(json.dumps(prev_rec, indent=1, ensure_ascii=False) + "\n", "utf-8")
                    n += 1
                    continue
            except (ValueError, OSError):
                pass
        if info.get("social_only"):
            rec = social_only_record(info)
        elif not mp.exists():
            rec = {"domain": key, "diocese": slug, "crawl_status": "not_fetched", "matched_church_ids": sorted(set(info["directory_ids"] + info["discovermass_ids"])),
                   "home_url": info["home"], "groups": [], "calendar_feeds": [], "events": [], "social": {}, "bulletin": None, "schedule_rows": [],
                   "pages": [], "source_url": info["home"], "fetched_at": now_iso()}
        else:
            m = json.loads(mp.read_text())
            try:
                rec = P.parse_site(d, m, info, TODAY)
            except Exception as e:  # noqa: BLE001 - one odd page must not stop the pass; the error is recorded
                print(f"  PARSE ERROR {key}: {e!r}", flush=True)
                rec = {"domain": key, "diocese": slug, "matched_church_ids": sorted(set(info["directory_ids"] + info["discovermass_ids"])),
                       "crawl_status": m.get("status"), "crawl_env": m.get("env"), "home_url": info["home"], "groups": [],
                       "calendar_feeds": [], "events": [], "social": {}, "bulletin": None, "schedule_rows": [], "pages": [],
                       "parse_error": repr(e)[:200], "source_url": info["home"], "fetched_at": now_iso()}
            pages = rec.pop("_pages", None)
            if m.get("status") == "ok" and m.get("env") == "laptop":
                rec["crawl_env"] = "laptop"
            lstat = laptop.get(key)
            if lstat and m.get("env") != "laptop":
                rec["laptop_attempt"] = lstat["status"]
            if m.get("env") != "laptop" and m.get("status") not in NO_RETRY and key not in laptop:
                rec["laptop_pending"] = True      # the laptop pass has not run for this site (laptop offline); see README
            if pages is None and (d / "sidedoors.json").exists():
                sp = build_sidedoor_pages(info, m.get("final_home") or info["home"])
                if sp:
                    pages = sp
                    rec["crawl_env"] = "sidedoor"
                    rec["template"] = "sidedoor-wp-json"
                    rec["cms_guess"] = {"cms": "wordpress", "evidence": ["/wp-json/wp/v2/pages answered"]}
                    soc, _ = P.extract_social(sp, info["home"])
                    rec["social"] = soc
                    rec["bulletin"] = P.find_bulletin(sp)
                    sd_dropped: dict = {}
                    rec["schedule_rows"], imgs = P.schedule_rows_templated(rec, sp, key, info["home"], TODAY, info.get("names"), sd_dropped)
                    if sd_dropped:
                        rec["schedule_dropped"] = sd_dropped
                    rec["groups"], rec["groups_dropped"] = P.groups_for(sp, key, None, TODAY)
                    rec["pages"] = [{"category": p.kind, "url": p.url, "status": 200, "class": "sidedoor"} for p in sp]
                sd = json.loads((d / "sidedoors.json").read_text())
                rec["sidedoors"] = {"opened": sd.get("opened", []), "checked_at": sd.get("checked_at")}
                if not rec["bulletin"] and sd.get("bulletin_urls_listed"):
                    u = sd["bulletin_urls_listed"][0]
                    rec["bulletin"] = {"url": u, "date": P.date_of(u), "kind": "sitemap-listed", "text": "", "source_url": f"{rec['final_url'].rstrip('/')}/sitemap.xml"}
            if pages and offline:
                fn0 = out_dir / (key.replace("/", "__") + ".json")
                carry_events(rec, json.loads(fn0.read_text("utf-8")) if fn0.exists() else None)
            elif pages:
                try:
                    rows = E.feed_rows(pages, m, d, key, shared)
                    f = E.CachedFetcher(shared, d, m, E.host_of(m.get("final_home") or info["home"]))
                    rec["events"], rec["event_counts"] = E.read_events(rows, key, f, today)
                    rec["calendar_feeds"] = rows
                except Exception as e:  # noqa: BLE001 - one bad feed must not stop the pass
                    rec["calendar_feeds"] = []
                    rec["event_counts"] = {"error": repr(e)[:160]}
        fn = out_dir / (key.replace("/", "__") + ".json")
        if fn.exists() and rec.get("calendar_feeds") and not offline:
            try:
                carry_over_feeds(rec, json.loads(fn.read_text("utf-8")))
            except (ValueError, OSError):
                pass
        P.screen_record(rec)
        fn.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", "utf-8")
        n += 1
        if n % 25 == 0:
            print(f"  parsed {n}", flush=True)
    finalize(slug)


def finalize(slug: str) -> None:
    """Drop social links shared by many sites (the diocese's own pages), then write summary.json."""
    out_dir = OUT / slug
    recs = [json.loads(p.read_text("utf-8")) for p in sorted(out_dir.glob("*.json")) if p.name != "summary.json"]
    cnt = Counter()
    for r in recs:
        for plat, urls in (r.get("social") or {}).items():
            if plat == "livestream":
                continue
            for u in urls:
                cnt[(plat, u)] += 1
    shared = {k for k, v in cnt.items() if v >= max(5, int(0.03 * len(recs)))}
    shared_list = sorted(f"{p} {u} ({cnt[(p, u)]} sites)" for p, u in shared)
    for r in recs:
        soc = r.get("social") or {}
        dropped = []
        for plat in list(soc):
            if plat == "livestream":
                continue
            keep = [u for u in soc[plat] if (plat, u) not in shared]
            dropped += [u for u in soc[plat] if (plat, u) in shared]
            if keep:
                soc[plat] = keep
            else:
                del soc[plat]
        if dropped:
            r["social_shared_links_removed"] = dropped
        (out_dir / (r["domain"].replace("/", "__") + ".json")).write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n", "utf-8")
    inp = load_input(slug)
    st = Counter(r["crawl_status"] for r in recs)
    ok = [r for r in recs if r["crawl_status"] == "ok"]
    ok_laptop = [r for r in ok if r.get("crawl_env") == "laptop"]
    sidedoor_ok = [r for r in recs if r.get("crawl_env") == "sidedoor"]
    plat_sites = Counter()
    for r in recs:
        for plat in (r.get("social") or {}):
            plat_sites[plat] += 1
    feeds_by_platform = Counter()
    for r in recs:
        for fd in r.get("calendar_feeds", []):
            if fd.get("status") == "active":
                feeds_by_platform[fd.get("platform")] += 1
    groups_by_cat = Counter(g["category"] for r in recs for g in r["groups"])
    summary = {
        "slug": slug, "generated_at": now_iso(), "run_date": TODAY,
        "input": {"directory_records": inp["directory_records"], "discovermass_records": inp["discovermass_records"],
                  "directory_without_website": inp["directory_without_website"], "discovermass_without_website": inp["discovermass_without_website"]},
        "sites_in_list": len(recs), "social_only": st.get("social_only", 0),
        "crawl": {
            "ok_total": len(ok), "ok_via_sandbox": len(ok) - len(ok_laptop), "ok_via_laptop": len(ok_laptop),
            "partial_via_side_doors": len(sidedoor_ok),
            "challenge": st.get("challenge", 0),
            "blocked_403_429": st.get("blocked_403", 0) + st.get("blocked_429", 0) + st.get("http_202", 0),
            "robots_disallowed": st.get("robots_disallowed", 0),
            "laptop_pending": sum(1 for r in recs if r.get("laptop_pending")),
            "dead": sum(v for k, v in st.items() if k in DEAD),
            "not_fetched": st.get("not_fetched", 0),
            "by_status": dict(st.most_common()),
        },
        "laptop_attempted": sum(1 for r in recs if r.get("laptop_attempt") or r.get("crawl_env") == "laptop"),
        "groups": {"total": sum(len(r["groups"]) for r in recs), "sites_with_groups": sum(1 for r in recs if r["groups"]),
                   "by_category": dict(groups_by_cat.most_common()), "with_meeting_time": sum(1 for r in recs for g in r["groups"] if g.get("meets"))},
        "calendar": {"sites_with_active_feed": sum(1 for r in recs if any(f.get("status") == "active" for f in r.get("calendar_feeds", []))),
                     "active_feeds": sum(feeds_by_platform.values()), "feeds_by_platform": dict(feeds_by_platform.most_common()),
                     "sites_with_events": sum(1 for r in recs if r["events"]), "events_stored": sum(len(r["events"]) for r in recs),
                     "events_in_window_before_privacy": sum((r.get("event_counts") or {}).get("in_window", 0) for r in recs),
                     "window": f"{TODAY} + 90 days"},
        "social": {"sites_by_platform": dict(plat_sites.most_common()), "sites_with_any_social": sum(1 for r in recs if any(k != "livestream" for k in (r.get("social") or {}))),
                   "shared_links_removed_as_diocesan": shared_list},
        "bulletin": {"sites_with_bulletin_url": sum(1 for r in recs if r.get("bulletin")),
                     "by_kind": dict(Counter(r["bulletin"]["kind"] for r in recs if r.get("bulletin")).most_common()),
                     "with_date": sum(1 for r in recs if r.get("bulletin") and r["bulletin"].get("date"))},
        "schedule": {"sites_with_own_site_rows": sum(1 for r in recs if r["schedule_rows"]), "rows_total": sum(len(r["schedule_rows"]) for r in recs),
                     "rows_needs_review": sum(1 for r in recs for x in r["schedule_rows"] if x.get("needs_review")),
                     "sites_with_schedule_image_only": sum(1 for r in recs if r.get("schedule_images")),
                     "by_kind": dict(Counter(x["kind"] for r in recs for x in r["schedule_rows"]).most_common())},
        "cms_guess": dict(Counter((r.get("cms_guess") or {}).get("cms", "unknown") for r in ok + sidedoor_ok).most_common()),
        "template": dict(Counter(r.get("template") for r in ok + sidedoor_ok).most_common()),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({k: summary[k] for k in ("sites_in_list", "crawl", "groups", "calendar", "schedule")}, indent=1))


def cmd_readme() -> None:
    names = {d["slug"]: d["name"] for d in json.loads((CA / "dioceses.json").read_text())["dioceses"]} if (CA / "dioceses.json").exists() else {}
    rows = []
    for p in sorted(OUT.glob("*/summary.json")):
        s = json.loads(p.read_text())
        rows.append(s)
    order = ["los-angeles", "san-francisco", "san-diego", "orange", "san-jose", "sacramento", "san-bernardino", "fresno", "monterey",
             "santa-rosa", "stockton"]
    rows.sort(key=lambda s: order.index(s["slug"]) if s["slug"] in order else 99)
    L = ["# California parish websites (`data/real/california/sites/`)", "",
         "One pass over every parish website of each diocese, built from the DiscoverMass index and the diocese's own directory "
         "(matched by website domain). Files only: nothing here is loaded into Supabase. Built by `ingest/california/sites/` "
         "(`run_diocese.py`, `fetch_site.py`, `parse_site.py`, `events.py`, `sidedoor_site.py`).", "",
         "## Files", "",
         "- `<slug>/<domain>.json`: one site. Fields: `domain`, `matched_church_ids` (directory and DiscoverMass ids), `crawl_status`, "
         "`crawl_env` (sandbox / laptop / sidedoor), `cms_guess`, `template` (which parser path read it), `pages`, `groups[]`, "
         "`calendar_feeds[]`, `events[]` (next 90 days, privacy-screened), `social{}`, `bulletin{url, date, kind}`, `schedule_rows[]` "
         "(Mass / confession / adoration from the parish's own pages), `source_url`, `fetched_at`.",
         "- `<slug>/summary.json`: counts for the diocese (this table is built from them).", "",
         "## Rules kept", "",
         "- robots.txt read per site (Disallow and Crawl-delay honoured); at most 1 request per second per host, up to 8 hosts at once; "
         "the HTTP library's plain default User-Agent; no cookies, no JavaScript, TLS always verified.",
         "- A challenge page (Cloudflare \"Just a moment...\", Sucuri, captcha wall) is recorded as `challenge` and never solved, retried or "
         "worked around; the same for 403 and 429. Those sites were tried once more from the owner's home connection (laptop), and then "
         "their public machine-readable doors (WordPress REST pages, feed, sitemaps) were asked once each; three refusals in a row stop it.",
         "- No private individuals: no names, phones or e-mails are stored. Free text goes through `ingest/public/privacy.py` (`screen`); "
         "events through the calendar connector's `screen_event` plus the shared detector (a name it would withhold drops the event); "
         "funerals, Mass intentions, prayer and sick lists are dropped. A Google Calendar id that is an e-mail address is not stored. "
         "Contact is only a generic contact-page link.",
         "- Schedule rows read from tables, images, PDFs or OCR mix columns: they carry `needs_review: true` and a `review_reason`; "
         "implausible hours and rows without a day are flagged the same way. Schedules posted only as images are listed under `schedule_images`.",
         "- Social links that appear on at least 5 sites (and 3 % of a diocese) are the diocese's own and are removed from the parish record "
         "(`social_shared_links_removed`, listed in `summary.json`).", "",
         "## Counts per diocese", "",
         "| diocese | sites in list | ok (sandbox) | ok via laptop | side doors only | challenge | blocked 403/429 | dead | social-only | laptop_pending | groups | sites w/ groups | sites w/ calendar feed | events | sites w/ any social | sites w/ bulletin URL | sites w/ own-site schedule |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot = Counter()
    for s in rows:
        c = s["crawl"]
        vals = [s["sites_in_list"], c["ok_via_sandbox"], c["ok_via_laptop"], c["partial_via_side_doors"], c["challenge"], c["blocked_403_429"],
                c["dead"], s["social_only"], c.get("laptop_pending", 0), s["groups"]["total"], s["groups"]["sites_with_groups"], s["calendar"]["sites_with_active_feed"],
                s["calendar"]["events_stored"], s["social"]["sites_with_any_social"], s["bulletin"]["sites_with_bulletin_url"],
                s["schedule"]["sites_with_own_site_rows"]]
        for i, v in enumerate(vals):
            tot[i] += v
        L.append(f"| {s['slug']} | " + " | ".join(str(v) for v in vals) + " |")
    L.append("| **all** | " + " | ".join(f"**{tot[i]}**" for i in range(16)) + " |")
    pending = sorted(d.name for d in (CA / "dioceses").iterdir() if d.is_dir() and not (OUT / d.name / "summary.json").exists()
                     and d.name != "oakland")
    L += ["", "Not crawled yet: " + (", ".join(pending) or "none") + ". The Fresno, Monterey, Santa Rosa and Stockton directories are "
          "blocked by a bot challenge (see each diocese's README), so their lists come from DiscoverMass alone; San Jose's directory "
          "lists a website for only 3 of 57 parishes, the rest come from DiscoverMass. Run `prepare <slug>` then `fetch <slug>` "
          "again when DiscoverMass adds websites (cached sites are not fetched again).", "",
          "`laptop_pending`: sites the sandbox could not read (challenge, 403/429, dead or unreachable) that have not yet been asked "
          "once from the owner's home connection. The laptop was offline for the 2026-10-31 pass, so they are recorded (`laptop_pending: true` "
          "in the site file) and not retried; `laptop-list <slug>` lists them for the next laptop pass. They are a subset of the "
          "challenge, blocked and dead columns, not an extra category.", "",
          "Social links by platform (sites with at least one link), per diocese:", ""]
    plats = ["facebook", "instagram", "youtube", "x", "tiktok", "flocknote", "livestream"]
    L.append("| diocese | " + " | ".join(plats) + " |")
    L.append("|---|" + "---:|" * len(plats))
    for s in rows:
        sp = s["social"]["sites_by_platform"]
        L.append(f"| {s['slug']} | " + " | ".join(str(sp.get(p, 0)) for p in plats) + " |")
    L.append("")
    (OUT / "README.md").write_text("\n".join(L), "utf-8")
    print("README.md written")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "fetch", "laptop-list", "laptop-merge", "sidedoors", "parse", "readme", "finalize"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-seconds", type=float, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--offline", action="store_true", help="parse: no network; carry calendar feeds and events over")
    a = ap.parse_args()
    if a.cmd == "prepare":
        cmd_prepare(a.slug)
    elif a.cmd == "fetch":
        cmd_fetch(a.slug, a.workers, a.max_seconds)
    elif a.cmd == "laptop-list":
        cmd_laptop_list(a.slug)
    elif a.cmd == "laptop-merge":
        cmd_laptop_merge(a.slug, a.arg)
    elif a.cmd == "sidedoors":
        cmd_sidedoors(a.slug, a.workers)
    elif a.cmd == "parse":
        cmd_parse(a.slug, {x for x in a.only.split(",") if x} or None, a.offline)
    elif a.cmd == "finalize":
        finalize(a.slug)
    elif a.cmd == "readme":
        cmd_readme()
    return 0


if __name__ == "__main__":
    sys.exit(main())
