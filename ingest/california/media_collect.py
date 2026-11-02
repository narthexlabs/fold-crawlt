"""Step 1 of the California YouTube catalog: collect every candidate YouTube link outside Oakland. No network.

Sources, all already on disk:
  * the parish index of the /ca pages (scripts/california/merge.py build_diocese, run in memory with geocode off, nothing
    written): each parish's crawled site files (data/real/california/sites/<slug>/*.json: social.youtube, social.livestream),
    the diocese's own directory row (dioceses/<slug>/parishes.json) and the DiscoverMass row (discovermass/churches.json);
  * the crawl's cached HTML (data/raw/california/sites/<domain>/*.html.gz): every YouTube link in the page, including
    iframes, the links beyond the three the crawl keeps per platform, and single videos (resolved later with oEmbed);
  * the diocese raw caches (data/raw/california/dioceses/<slug>/*.json: WordPress REST pages such as San Diego's
    "Online Mass" list, directory pages, entity sites) and cached diocese home pages (sites/<diocese domain>, dsj-laptop);
  * data/real/california/wayback/*.json when it exists (another agent writes it; re-run this script to pick it up).

The 139 links that the 2026-10-31 social clean-up (scripts/california/merge.py social_is_own, commit 14b7efc) left off the
parish cards are reproduced exactly by running that check over the same inputs: every call that answers "not the parish's own"
for a YouTube link is recorded (`cleanup: dropped`). Links it kept are marked `kept`.

Output (local, gitignored): data/raw/california/youtube/candidates.json, parish_index.json
    python3 -I ingest/california/media_collect.py            # builds the index (about 2 minutes) unless it is cached
    python3 -I ingest/california/media_collect.py --reindex  # rebuild the index
"""
from __future__ import annotations

import collections
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from media_common import CA, EXTRA_DIOCESE_HOSTS, RAW, ROOT, extract_yt, now_iso, parse_yt_url, reg_domain  # noqa: E402

DIOCESES = [d["slug"] for d in json.loads((CA / "dioceses.json").read_text())["dioceses"]]
PARISH_DIOCESES = [s for s in DIOCESES if s != "oakland"]
RAW_SITES = ROOT / "data" / "raw" / "california" / "sites"
RAW_DIO = ROOT / "data" / "raw" / "california" / "dioceses"
INDEX = RAW / "parish_index.json"


def build_index() -> dict:
    """One row per parish record of the 11 dioceses, with the links each structured source holds."""
    sys.path.insert(0, str(ROOT / "scripts" / "california"))
    import merge  # noqa: PLC0415

    evaluated: list[dict] = []
    orig = merge.social_is_own

    def spy(plat, url, r):  # records every ownership decision the clean-up made for a YouTube link
        ok = orig(plat, url, r)
        if plat == "youtube":
            evaluated.append({"parish_id": r["id"], "url": url, "kept": bool(ok)})
        return ok

    merge.social_is_own = spy
    parishes = []
    for slug in PARISH_DIOCESES:
        b = merge.build_diocese(slug, geocode=False)
        for r in b["recs"]:
            sites = []
            for s in r.get("sites") or []:
                sites.append({"domain": s.get("domain"), "file": s.get("_file"), "school": bool(merge.school_site(s, r)),
                              "home_url": s.get("home_url"), "crawl_status": s.get("crawl_status"),
                              "diocesan_host": (s.get("domain") or "") in merge.DIOCESAN_HOSTS,
                              "yt_links": [x for x in extract_yt(json.dumps({"social": s.get("social")}, ensure_ascii=False))]})
            d_dir, d_dm = r.get("dir") or {}, r.get("dm") or {}
            parishes.append({
                "id": r["id"], "diocese": slug, "name": r["name"], "city": r.get("city"), "zip": r.get("zip"), "county": r.get("county"),
                "website": r.get("website"), "kind": r.get("kind"), "type": r.get("type"), "status": r.get("status"),
                "listed_by": r.get("listed_by"), "sites": sites,
                "dir_yt": extract_yt(json.dumps(d_dir, ensure_ascii=False)) if d_dir else [],
                "dm_yt": extract_yt(json.dumps(d_dm, ensure_ascii=False)) if d_dm else [],
                "dm_url": d_dm.get("source_url") if d_dm else None,
            })
    merge.social_is_own = orig
    idx = {"built_at": now_iso(), "parishes": parishes, "cleanup_evaluations": evaluated,
           "social_stats": dict(merge.SOCIAL_STATS)}
    INDEX.write_text(json.dumps(idx, ensure_ascii=False) + "\n")
    return idx


def scan_html_gz(path: Path) -> str:
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8", "ignore")
    except (OSError, EOFError):
        return ""


def page_urls(site_dir: Path) -> dict[str, str]:
    mf = site_dir / "manifest.json"
    if not mf.exists():
        return {}
    try:
        m = json.loads(mf.read_text())
    except json.JSONDecodeError:
        return {}
    return {p.get("file"): p.get("final_url") or p.get("url") for p in m.get("pages", []) if p.get("file")}


def main() -> None:
    idx = json.loads(INDEX.read_text()) if INDEX.exists() and "--reindex" not in sys.argv else build_index()
    parishes = idx["parishes"]
    ev = idx["cleanup_evaluations"]
    dropped = {(e["parish_id"], parse_yt_url(e["url"])["key"] if parse_yt_url(e["url"]) else e["url"]) for e in ev if not e["kept"]}
    kept = {(e["parish_id"], parse_yt_url(e["url"])["key"] if parse_yt_url(e["url"]) else e["url"]) for e in ev if e["kept"]}
    dropped_calls = sum(1 for e in ev if not e["kept"])

    cands: dict[tuple, dict] = {}

    def add(owner_type, owner_id, diocese, link, source, **extra):
        k = (owner_type, owner_id, link["type"], link["key"])
        c = cands.setdefault(k, {"owner_type": owner_type, "owner_id": owner_id, "diocese": diocese, "link_type": link["type"],
                                 "key": link["key"], "url": link["url"], "live_hint": False, "sources": [], "site_is_school": False})
        c["live_hint"] = c["live_hint"] or bool(link.get("live_hint"))
        c["site_is_school"] = c["site_is_school"] or bool(extra.get("site_is_school"))
        if source not in c["sources"]:
            c["sources"].append(source)
        return c

    # ---- structured sources, per parish
    site_domains: dict[str, list[dict]] = collections.defaultdict(list)   # domain -> parishes using it
    for p in parishes:
        for s in p["sites"]:
            if s.get("domain"):
                site_domains[s["domain"]].append(p)
            for l in s["yt_links"]:
                add("parish", p["id"], p["diocese"], l, {"source": "site_json", "ref": s.get("file"), "domain": s["domain"]},
                    site_is_school=s["school"] or s["diocesan_host"])
        for l in p["dir_yt"]:
            add("parish", p["id"], p["diocese"], l, {"source": "directory", "ref": f"dioceses/{p['diocese']}/parishes.json"})
        for l in p["dm_yt"]:
            add("parish", p["id"], p["diocese"], l, {"source": "discovermass", "ref": p.get("dm_url")})

    # ---- the crawl's cached HTML, per site
    html_sites = html_links = 0
    for dom, plist in sorted(site_domains.items()):
        sd = RAW_SITES / dom
        if not sd.is_dir():
            continue
        school = any(s["school"] or s["diocesan_host"] for p in plist for s in p["sites"] if s.get("domain") == dom)
        urls = page_urls(sd)
        found = False
        for f in sorted(sd.glob("*.html.gz")):
            links = extract_yt(scan_html_gz(f))
            if links:
                found = True
            for l in links:
                html_links += 1
                for p in plist:
                    add("parish", p["id"], p["diocese"], l, {"source": "site_html", "ref": urls.get(f.name) or f"{dom}/{f.name}", "domain": dom},
                        site_is_school=school)
        html_sites += 1

    # ---- diocese raw caches: unattributed to a parish (owner resolved later by name), except diocese-domain pages
    dio_hosts = {h: s for s, hs in EXTRA_DIOCESE_HOSTS.items() for h in hs}
    own_domain = {reg_domain(p["website"]): p for p in parishes if p.get("website")}
    for slug in DIOCESES:
        d = RAW_DIO / slug
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                doc = json.loads(f.read_text(errors="ignore"))
            except (json.JSONDecodeError, OSError):
                continue
            body = doc.get("body") if isinstance(doc, dict) else None
            if not isinstance(body, str):
                continue
            links = extract_yt(body)
            if not links:
                continue
            url = doc.get("url") or ""
            host = reg_domain(url)
            for l in links:
                if host in dio_hosts:
                    add("diocese", dio_hosts[host], dio_hosts[host], l, {"source": "diocese_page", "ref": url})
                else:
                    add("unattributed", None, slug, l, {"source": "diocese_cache", "ref": url})
        wb = d / "wayback"
        if wb.is_dir():
            for f in sorted(wb.glob("*.json")):
                try:
                    doc = json.loads(f.read_text(errors="ignore"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(doc, dict):
                    for pid, page in doc.items():
                        txt = json.dumps(page, ensure_ascii=False) if not isinstance(page, str) else page
                        for l in extract_yt(txt):
                            u = page.get("url") if isinstance(page, dict) else None
                            kind = "diocese" if "rcbo.org" in txt[:3000] and "sameAs" in txt else "unattributed"
                            if kind == "diocese":
                                add("diocese", slug, slug, l, {"source": "diocese_wayback", "ref": f"{f.name}:{pid}"})
                            else:
                                add("unattributed", None, slug, l, {"source": "diocese_wayback", "ref": f"{f.name}:{pid}", "page": u})
    # cached home pages of the dioceses (the crawl's site folders and the San Jose laptop pull)
    for slug, hosts in EXTRA_DIOCESE_HOSTS.items():
        for h in hosts:
            sd = RAW_SITES / h
            if sd.is_dir():
                urls = page_urls(sd)
                for f in sorted(sd.glob("*.html.gz")):
                    for l in extract_yt(scan_html_gz(f)):
                        add("diocese", slug, slug, l, {"source": "diocese_site_html", "ref": urls.get(f.name) or f"{h}/{f.name}"})
    for f in sorted((ROOT / "data" / "raw" / "california" / "dsj-laptop").glob("*.html")):
        for l in extract_yt(f.read_text(errors="ignore")):
            add("diocese", "san-jose", "san-jose", l, {"source": "diocese_site_html", "ref": f"dsj.org/{f.name}"})

    # ---- Wayback captures, if the other agent has written them
    wb_dir = CA / "wayback"
    wb_files = sorted(wb_dir.glob("*.json")) if wb_dir.is_dir() else []
    wb_n = 0
    for f in wb_files:
        try:
            doc = json.loads(f.read_text(errors="ignore"))
        except (json.JSONDecodeError, OSError):
            continue
        txt = json.dumps(doc, ensure_ascii=False)
        pid = doc.get("parish_id") or doc.get("id") if isinstance(doc, dict) else None
        pdi = next((p for p in parishes if p["id"] == pid), None)
        for l in extract_yt(txt):
            wb_n += 1
            if pdi:
                add("parish", pdi["id"], pdi["diocese"], l, {"source": "wayback", "ref": f"california/wayback/{f.name}"})
            else:
                add("unattributed", None, doc.get("diocese") if isinstance(doc, dict) else None, l,
                    {"source": "wayback", "ref": f"california/wayback/{f.name}"})

    # ---- the diocese pass (ingest/us/media/diocese_pages.py): each diocese's home page and its cathedral's site
    dp_f = ROOT / "data" / "real" / "us" / "media" / "_inputs" / "diocese_pages.json"
    dp_n = 0
    if dp_f.exists():
        for t in json.loads(dp_f.read_text())["targets"]:
            cs = t.get("ca_slug")
            if not cs or cs == "oakland" or not t.get("yt_links"):
                continue
            for l in t["yt_links"]:
                link = {"type": l["type"], "key": l["key"], "url": l["url"], "live_hint": l.get("live_hint")}
                if t["kind"] == "cathedral" and t.get("ca_parish_id"):
                    add("parish", t["ca_parish_id"], cs, link, {"source": "site_html", "ref": l.get("found_on"), "domain": reg_domain(t["url"])})
                else:
                    add("diocese", cs, cs, link, {"source": "diocese_page" if t["kind"] == "diocese_home" else "cathedral_page", "ref": l.get("found_on")})
                dp_n += 1

    # ---- tag the clean-up status
    out = []
    for c in cands.values():
        if c["owner_type"] == "parish":
            kk = (c["owner_id"], c["key"])
            c["cleanup"] = "dropped" if kk in dropped else "kept" if kk in kept else None
        out.append(c)
    out.sort(key=lambda c: (c["diocese"] or "", c["owner_id"] or "", c["link_type"], c["key"]))
    by_type = collections.Counter(c["link_type"] for c in out)
    summary = {
        "built_at": now_iso(), "parishes_indexed": len(parishes), "candidates": len(out), "by_link_type": dict(by_type),
        "by_owner_type": dict(collections.Counter(c["owner_type"] for c in out)),
        "cleanup_dropped_calls": dropped_calls, "cleanup_dropped_unique_parish_link": len(dropped), "cleanup_kept": len(kept),
        "html_sites_scanned": html_sites, "html_youtube_links_found": html_links,
        "wayback_files": len(wb_files), "wayback_links": wb_n, "diocese_pass_links": dp_n, "merge_social_stats": idx.get("social_stats"),
    }
    (RAW / "candidates.json").write_text(json.dumps({"summary": summary, "candidates": out}, ensure_ascii=False, indent=1) + "\n")
    # inputs for the GitHub runner job `media` (which has no California site data): candidates + a trimmed parish index
    inp = CA / "media" / "_inputs"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "candidates.json").write_text(json.dumps({"summary": summary, "candidates": out}, ensure_ascii=False, separators=(",", ":")))
    keep = ("id", "diocese", "name", "city", "website", "kind", "type", "status")
    trimmed = [{**{k: p.get(k) for k in keep}, "sites": [{k: x.get(k) for k in ("domain", "school", "diocesan_host")} for x in p.get("sites") or []]}
               for p in parishes]
    (inp / "parish_index.json").write_text(json.dumps({
        "built_at": idx["built_at"], "note": "Trimmed copy of data/raw/california/youtube/parish_index.json (media_collect.py): only the fields "
        "media_resolve.py verify and media_report.py read. Institutional facts only (parish name, city, website).",
        "parishes": trimmed, "cleanup_evaluations": [], "social_stats": idx.get("social_stats")}, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
