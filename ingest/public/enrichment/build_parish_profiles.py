"""Merge everything known about each parish into one sourced profile.

Inputs (all in data/real/):
  parishes_directory.json                 diocesan directory (address, clergy, Mass times)
  parish_sites.json                       parish website crawl (schedule, staff, ministries, giving)
  enrichment/bulletin_sources.json        where each parish publishes its bulletin
  enrichment/bulletins/<id>.json          parsed bulletins (parse_bulletins.py)
  enrichment/parish_alt_sources.json      facts read from alternate sources for blocked sites

Output: data/real/enrichment/parish_profiles.json, one record per parish. Every section carries
source_url + fetched_at (lists carry them per item). The inputs are already screened for private names
(ingest/public/privacy.py); save_json screens the output again, and staff_withheld lists the people the
screen withheld (neutral role text and source only, never a name). `data_completeness` counts which kinds of public
information we hold for the parish; it describes our data coverage, not the parish, and must not be
used to rank parishes.

Usage: python ingest/public/enrichment/build_parish_profiles.py
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import REAL, load_json, now_iso, save_json  # noqa: E402

ENR = REAL / "enrichment"
OUT = ENR / "parish_profiles.json"
TODAY = date(2026, 10, 26)

CHECKS = [
    ("address", "street address"),
    ("phone_or_email", "phone or email"),
    ("website", "website"),
    ("mass_times", "Mass times from any source"),
    ("confession_times", "confession times"),
    ("staff", "at least one staff/clergy name with title beyond the directory clergy list"),
    ("ministries", "at least 3 ministries or groups"),
    ("recent_bulletin", "a bulletin dated within 60 days"),
    ("offertory_figures", "printed offertory/collection figures"),
    ("appeal_figures", "printed Bishop's Ministries Appeal figures"),
    ("upcoming_events", "dated upcoming events"),
    ("volunteer_or_donation_needs", "volunteer or donation requests"),
    ("giving_channel", "online giving provider or link"),
    ("geocode", "latitude/longitude"),
]


def norm_name(n: str) -> str:
    n = re.sub(r"(?i)\b(rev|fr|father|msgr|monsignor|deacon|dcn|sr|sister|br|brother|mr|mrs|ms|dr|very|most)\b\.?", " ", n)
    n = re.sub(r"[^a-z ]", " ", n.lower())
    parts = [p for p in n.split() if len(p) > 1]
    return " ".join(parts[:1] + parts[-1:]) if len(parts) >= 2 else " ".join(parts)


def add_staff(bucket: dict, name: str, title: str, src: dict) -> None:
    if not name or len(name) < 4:
        return
    k = norm_name(name)
    if not k or " " not in k:
        return
    rec = bucket.setdefault(k, {"name": name, "titles": [], "sources": []})
    title = " ".join((title or "").split()[:12])
    if title and title not in rec["titles"]:
        rec["titles"].append(title)
    if src["source_url"] not in [s["source_url"] for s in rec["sources"]]:
        rec["sources"].append(src)
    # prefer the longer, more formal rendering of the name
    if len(name) > len(rec["name"]) and len(name) < 45:
        rec["name"] = name


def min_key(n: str) -> str:
    n = re.sub(r"(?i)\b(st\.?|saint|the|of|and|&|ministry|ministries|society|group|committee|council)\b", " ", n)
    return re.sub(r"[^a-z]", "", n.lower())[:30]


def main() -> None:
    directory = load_json(REAL / "parishes_directory.json")
    sites = {r["id"]: r for r in load_json(REAL / "parish_sites.json")["records"]}
    sources = {r["id"]: r for r in load_json(ENR / "bulletin_sources.json")["records"]}
    alt = load_json(ENR / "parish_alt_sources.json", {"records": {}})["records"]
    dir_fetched = directory.get("generated_at")
    charities = load_json(ENR / "charity_profiles.json", {"records": []})["records"]
    links_by_parish: dict[str, list] = {}
    for o in charities:
        for c in o.get("parish_conferences") or []:
            if c.get("parish_id"):
                links_by_parish.setdefault(c["parish_id"], []).append(
                    {"organization": o["name"], "organization_id": o["id"], "relation": "parish conference (branch)",
                     "detail": c["conference"], "source_url": c["source_url"], "fetched_at": c["fetched_at"]})
        for e in o.get("events") or []:
            if e.get("parish_id"):
                links_by_parish.setdefault(e["parish_id"], []).append(
                    {"organization": o["name"], "organization_id": o["id"], "relation": "hosts program/event",
                     "detail": f"{e['title']} {e['date']} {e.get('time', '')}".strip(), "source_url": e["source_url"],
                     "fetched_at": e["fetched_at"]})
    out = []
    for d in directory["records"]:
        pid = d["id"]
        site = sites.get(pid, {})
        src = sources.get(pid, {})
        bfile = ENR / "bulletins" / f"{pid}.json"
        bl = load_json(bfile, {}).get("bulletins", []) if bfile.exists() else []
        alts = alt.get(pid, [])
        dsrc = {"source_url": d["source_url"], "fetched_at": d.get("fetched_at") or dir_fetched, "method": "diocesan directory"}
        csrc = lambda url: {"source_url": url, "fetched_at": site.get("crawled_at"), "method": "parish website crawl"}  # noqa: E731
        bsrc = lambda b, page=None: {"source_url": b["source_url"], "fetched_at": b["fetched_at"],  # noqa: E731
                                     "method": "parsed bulletin", "bulletin_date": b["date"], "page": page}
        asrc = lambda a: {"source_url": a["source_url"], "fetched_at": a["fetched_at"], "method": a["method"]}  # noqa: E731

        p: dict = {
            "id": pid, "name": d["name"], "city": d["city"],
            "address": {"street": d.get("address"), "city": d.get("city"), "state": d.get("state"), "zip": d.get("zip"),
                        **dsrc},
            "contact": {"phone": d.get("phone"), "email": d.get("email"), **dsrc},
            "website": {"url": d.get("website"), **dsrc} if d.get("website") else None,
            "geo": {"lat": d.get("lat"), "lng": d.get("lng"), "source_url": d.get("geo_source_url"),
                    "method": d.get("geo_source")} if d.get("lat") else None,
            "structure": {"region": d.get("region"), "deanery": d.get("deanery"), "clustered_with": d.get("clustered_with"),
                          "languages": d.get("languages"), **dsrc},
            "clergy_directory": {"names": d.get("clergy") or [], "pastor": d.get("pastor"), **dsrc},
        }
        # ------------------------------------------------ Mass / confession / adoration
        mass = {}
        if d.get("mass_times_text"):
            mass["directory"] = {"text": d["mass_times_text"], **dsrc}
        ms = site.get("mass_schedule") or {}
        if ms.get("parsed"):
            mass["website"] = {"entries": [{k: e.get(k) for k in ("day", "time", "language", "kind")} for e in ms["parsed"]],
                               **csrc(ms.get("source_url"))}
        if bl and bl[0]["schedules"]["mass"]:
            b = bl[0]
            mass["bulletin"] = {"entries": [{k: e[k] for k in ("day", "time", "language", "page")} for e in b["schedules"]["mass"]],
                                **{k: v for k, v in bsrc(b).items() if k != "page"},
                                "note": "heuristic extraction; joint cluster bulletins list both parishes' times"}
        for a in alts:
            if a.get("mass_schedule"):
                mass["alternate"] = {"entries": a["mass_schedule"], **asrc(a)}
        p["mass_times"] = mass or None
        conf, ador = [], []
        for b in bl[:1]:
            conf += [{**{k: e[k] for k in ("day", "time")}, **bsrc(b, e["page"])} for e in b["schedules"]["confession"]]
            ador += [{**{k: e[k] for k in ("day", "time")}, **bsrc(b, e["page"])} for e in b["schedules"]["adoration"]]
        for e in (ms.get("parsed") or []):
            line = (e.get("line") or "").lower()
            if e.get("kind") == "other" and re.search(r"confess|reconcil|penance|confesi", line):
                conf.append({"day": e.get("day"), "time": e.get("time"), **csrc(ms.get("source_url"))})
            if e.get("kind") == "other" and re.search(r"adoration|adoraci|holy hour", line):
                ador.append({"day": e.get("day"), "time": e.get("time"), **csrc(ms.get("source_url"))})
        for a in alts:
            for e in a.get("confession") or []:
                conf.append({**e, **asrc(a)})
            for e in a.get("adoration") or []:
                ador.append({**e, **asrc(a)})
        p["confession"] = conf or None
        p["adoration"] = ador or None
        # ------------------------------------------------ staff (merged, per-source)
        staff: dict = {}
        for s in ((site.get("staff") or {}).get("items") or []):
            add_staff(staff, s["name"], s.get("title"), csrc((site.get("staff") or {}).get("source_url")))
        for b in bl[:2]:
            for s in b["staff"]:
                add_staff(staff, s["name"], s.get("title"), bsrc(b, s.get("page")))
        for a in alts:
            for s in a.get("staff") or []:
                add_staff(staff, s["name"], s.get("title"), asrc(a))
        p["staff"] = sorted(staff.values(), key=lambda r: (-len(r["sources"]), r["name"]))[:50] or None
        # people the privacy screen withheld upstream (ministry contacts, committee members): role text and source only
        held = [{**h, **csrc((site.get("staff") or {}).get("source_url"))} for h in ((site.get("staff") or {}).get("items_withheld") or [])]
        for b in bl[:2]:
            held += [{"withheld": h["withheld"], **bsrc(b, h.get("page"))} for h in (b.get("staff_withheld") or [])]
        for a in alts:
            held += [{"withheld": h["withheld"], **asrc(a)} for h in (a.get("staff_withheld") or [])]
        p["staff_withheld"] = held or None
        # ------------------------------------------------ ministries (merged)
        mins: dict = {}
        for m in ((site.get("ministries") or {}).get("items") or []):
            nm = m if isinstance(m, str) else m.get("name")
            if nm:
                mins.setdefault(min_key(nm), {"name": nm, "sources": []})["sources"].append(
                    csrc((site.get("ministries") or {}).get("source_url")))
        for b in bl[:2]:
            for m in b["ministries"]:
                r = mins.setdefault(min_key(m["name"]), {"name": m["name"], "sources": []})
                if b["source_url"] not in [x["source_url"] for x in r["sources"]]:
                    r["sources"].append(bsrc(b, m["page"]))
        for a in alts:
            for nm in a.get("ministries") or []:
                r = mins.setdefault(min_key(nm), {"name": nm, "sources": []})
                if a["source_url"] not in [x["source_url"] for x in r["sources"]]:
                    r["sources"].append(asrc(a))
        mins.pop("", None)
        p["ministries"] = list(mins.values())[:120] or None
        # ------------------------------------------------ bulletin source + series
        shared_from = next((b.get("shared_from") for b in bl if b.get("shared_from")), None)
        p["bulletin"] = {
            "source_type": f"shared cluster bulletin ({shared_from})" if shared_from else src.get("source_type"),
            "index_pages": src.get("index_pages") or [],
            "lpi_codes": src.get("lpi_codes") or {},
            "ecatholic_sites": src.get("ecatholic_sites") or [],
            "parsed_dates": [b["date"] for b in bl],
            "latest_date": bl[0]["date"] if bl else None,
            "latest_url": bl[0]["source_url"] if bl else None,
            "extract_file": f"data/real/enrichment/bulletins/{pid}.json" if bl else None,
            "note": src.get("manual_note"),
            "shared_from": next((b.get("shared_from") for b in bl if b.get("shared_from")), None),
        }
        off, bma, sec, sac = [], [], [], []
        for b in bl:
            for f in b["offertory"]:
                off.append({**{k: f.get(k) for k in ("label", "category", "amounts", "values", "headers", "page")},
                            "bulletin_date": b["date"], "source_url": b["source_url"], "fetched_at": b["fetched_at"]})
            for f in b["bma"]:
                bma.append({**f, "bulletin_date": b["date"], "source_url": b["source_url"], "fetched_at": b["fetched_at"]})
            for f in b.get("second_collections", []):
                sec.append({**f, "bulletin_date": b["date"], "source_url": b["source_url"], "fetched_at": b["fetched_at"]})
            for f in b["sacramental_counts"]:
                sac.append({**f, "bulletin_date": b["date"], "source_url": b["source_url"], "fetched_at": b["fetched_at"]})
        p["offertory"] = {"rows": off, "note": "Figures as printed in the bulletin; a bulletin usually reports the "
                          "previous weekend. Labels are the printed row labels; category is a keyword guess."} if off else None
        p["bishops_ministries_appeal"] = bma or None
        seen = set()
        p["second_collections"] = [x for x in sec if not ((x["date"], x["purpose"]) in seen or seen.add((x["date"], x["purpose"])))] or None
        p["sacramental_counts"] = sac or None
        # upcoming events & needs from the two newest bulletins + alternates
        ev, needs = [], []
        seen_ev = set()
        for b in bl[:2]:
            for e in b["events"]:
                if e["date"] >= TODAY.isoformat():
                    k = (e["date"], e["title"][:25].lower())
                    if k not in seen_ev:
                        seen_ev.add(k)
                        ev.append({**e, **bsrc(b, e["page"])})
            for v in b["volunteer_needs"]:
                needs.append({**v, **bsrc(b, v["page"])})
        for a in alts:
            for v in a.get("volunteer_needs") or []:
                needs.append({"kind": "general", "text": v, **asrc(a)})
            for e in a.get("events") or []:
                ev.append({**e, **asrc(a)})
        p["upcoming_events"] = sorted(ev, key=lambda e: e.get("date") or "")[:40] or None
        p["volunteer_and_donation_needs"] = needs[:30] or None
        # giving
        giving = {}
        gp = site.get("giving_provider")
        if gp:
            giving["provider"] = gp if isinstance(gp, dict) else {"provider": gp}
            giving["provider"].setdefault("method", "regex over crawled HTML")
        links = [{"url": u, **asrc(a)} for a in alts for u in (a.get("giving_links") or [])]
        if links:
            giving["links"] = links
        p["giving"] = giving or None
        fl = next((a.get("flocknote_url") for a in alts if a.get("flocknote_url")), None)
        p["flocknote_url"] = fl
        cl = list(links_by_parish.get(pid, []))
        svdp_min = next((m for k, m in mins.items() if "vincent" in k or "svdp" in k), None)
        if svdp_min:
            cl.append({"organization": "Society of St. Vincent de Paul", "relation": "parish ministry listed",
                       "detail": svdp_min["name"], **svdp_min["sources"][0]})
        p["charity_links"] = cl or None
        p["notes"] = [{"text": t, **asrc(a)} for a in alts for t in (a.get("notes") or [])] or None
        p["transition"] = next(({**a["transition"], **asrc(a)} for a in alts if a.get("transition")), None)
        p["crawl_status"] = site.get("crawl_status")
        p["alternate_sources"] = [{"source_url": a["source_url"], "fetched_at": a["fetched_at"], "method": a["method"]}
                                  for a in alts] or None
        # ------------------------------------------------ completeness (data coverage only)
        recent_cut = (TODAY - timedelta(days=60)).isoformat()
        dir_clergy = {norm_name(c) for c in (d.get("clergy") or [])}
        have = {
            "address": bool(d.get("address")),
            "phone_or_email": bool(d.get("phone") or d.get("email")),
            "website": bool(d.get("website")),
            "mass_times": bool(mass),
            "confession_times": bool(conf),
            "staff": any(k not in dir_clergy for k in staff),
            "ministries": len(mins) >= 3,
            "recent_bulletin": bool(bl and bl[0]["date"] >= recent_cut),
            "offertory_figures": bool(off),
            "appeal_figures": bool(bma),
            "upcoming_events": bool(ev),
            "volunteer_or_donation_needs": bool(needs),
            "giving_channel": bool(giving),
            "geocode": bool(d.get("lat")),
        }
        p["data_completeness"] = {"score": round(100 * sum(have.values()) / len(have)), "have": have,
                                  "note": "Share of tracked public-data fields we hold for this parish. Measures our "
                                          "data coverage, not the parish."}
        out.append(p)
    save_json(OUT, {"generated_at": now_iso(), "count": len(out), "checks": dict(CHECKS),
                    "inputs": ["data/real/parishes_directory.json", "data/real/parish_sites.json",
                               "data/real/enrichment/bulletin_sources.json", "data/real/enrichment/bulletins/",
                               "data/real/enrichment/parish_alt_sources.json"],
                    "records": out})
    cov = {k: sum(r["data_completeness"]["have"][k] for r in out) for k, _ in CHECKS}
    print("parishes:", len(out))
    for k, v in cov.items():
        print(f"  {k:28s} {v:3d}")
    print("  mean completeness", round(sum(r["data_completeness"]["score"] for r in out) / len(out)))


if __name__ == "__main__":
    main()
