"""Build data/real/ecosystem/orgs.json and relationships.json.

Inputs (run these first; all write under data/real/ecosystem/ or data/raw/ecosystem/):
    oakdiocese_wp.py      diocesan pages/posts/venues via the WordPress REST API (raw, gitignored)
    web.py                cache of other pages (raw, gitignored; index.json holds fetch times)
    kofc_councils.py      -> kofc_councils.json
    parish_signals.py     -> parish_signals.json
    propublica_990.py     -> form990.json
    registry.py           hand-curated orgs + relationships (each with source_url)

    python3 -I ingest/public/ecosystem/build_ecosystem.py [--no-geocode]

Every record leaves with source_url + fetched_at. Ids that are not in orgs.json must exist in
data/seed/entities.json (parishes, schools, cceb, cemeteries, chancery offices, dio-oakland); the build fails otherwise.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, REAL, ROOT, census_geocode, load_json, now_iso, save_json  # noqa: E402
import registry  # noqa: E402
from registry import S  # noqa: E402

ECO = REAL / "ecosystem"
ALAMEDA = {"alameda", "albany", "berkeley", "castro valley", "dublin", "emeryville", "fremont", "hayward", "livermore",
           "newark", "oakland", "piedmont", "pleasanton", "san leandro", "san lorenzo", "union city", "sunol"}
CONTRA_COSTA = {"antioch", "bay point", "brentwood", "byron", "clayton", "concord", "crockett", "danville", "el cerrito",
                "el sobrante", "hercules", "kensington", "lafayette", "martinez", "moraga", "oakley", "orinda", "pinole",
                "pittsburg", "pleasant hill", "point richmond", "port costa", "richmond", "rodeo", "san pablo",
                "san ramon", "walnut creek", "discovery bay", "pacheco", "alamo", "diablo"}

# St. Vincent de Paul of Contra Costa: conference names exactly as on https://www.svdp-cc.org/branches/ -> parish id
SVDP_CC = [
    ("Most Holy Rosary", "most-holy-rosary-antioch"), ("Our Lady Queen of the World", "our-lady-queen-world-bay-point"),
    ("Immaculate Heart", "immaculate-heart-mary-brentwood"), ("St. Anne", "anne-byron"), ("St. Agnes", "agnes-concord"),
    ("Queen of All Saints", "queen-all-concord"), ("St. Bonaventure", "bonaventure-concord"),
    ("St. Francis of Assisi", "francis-assisi-concord"), ("St. Isidore", "isidore-danville"), ("St. Jerome", "jerome-el-cerrito"),
    ("St. John the Baptist", "john-baptist-el-cerrito"), ("St. Callistus", "callistus-el-sobrante"),
    ("St. Catherine of Siena", "catherine-siena-martinez"), ("St. Anthony", "anthony-oakley"), ("St. Joseph", "joseph-pinole"),
    ("Good Shepherd", "good-shepherd-pittsburg"), ("St. Peter Martyr", "peter-martyr-pittsburg"),
    ("Christ the King – St. Stephen", "christ-king-stephen-pleasant-hill"), ("St. David of Wales", "david-richmond"),
    ("Our Lady of Mercy", "our-lady-mercy-richmond"), ("St. Patrick", "patrick-rodeo"), ("St. Paul", "paul-san-pablo"),
    ("St. Joan of Arc", "joan-arc-san-ramon"), ("St. John Vianney", "john-vianney-walnut-creek"), ("St. Mary", "mary-walnut-creek"),
]


def county_of(city: str | None) -> str | None:
    c = (city or "").lower().strip()
    return "Alameda" if c in ALAMEDA else "Contra Costa" if c in CONTRA_COSTA else None


class Fetched:
    """source_url -> fetched_at, from the fetch logs."""

    def __init__(self):
        self.m: dict[str, str] = {}
        for f in ("oakdiocese_pages.json", "oakdiocese_posts.json"):
            d = load_json(RAW / "ecosystem" / f, {})
            for it in d.get("items", []):
                self.m[it["link"].rstrip("/") + "/"] = d["fetched_at"]
        idx = load_json(RAW / "ecosystem" / "web" / "index.json", {})
        for meta in idx.values():
            if meta.get("status") == 200:
                self.m[meta["url"]] = meta["fetched_at"]
                if meta.get("final_url"):
                    self.m.setdefault(meta["final_url"], meta["fetched_at"])
        self.events = load_json(REAL / "diocese_events.json", {}).get("retrieved")
        self.staff = load_json(REAL / "chancery_staff.json", {}).get("retrieved")
        self.m.update(registry.WEBFETCHED)
        sites = load_json(REAL / "parish_sites.json", {"records": []})
        for s in sites["records"]:
            for p in s.get("pages") or []:
                if p.get("url"):
                    self.m.setdefault(p["url"], s.get("crawled_at"))
        k = load_json(ECO / "kofc_councils.json", {})
        for c in k.get("councils", []):
            self.m[c["source_url"]] = c["fetched_at"]
        self.kofc = k.get("fetched_at")

    def __call__(self, url: str) -> str | None:
        if url in self.m:
            return self.m[url]
        if url.rstrip("/") + "/" in self.m:
            return self.m[url.rstrip("/") + "/"]
        if url.startswith(registry.OAK + "/event/"):
            return self.events
        if url == S["staff"]:
            return self.staff
        if "kofc.org" in url:
            return self.kofc
        return None


def main() -> None:
    geocode = "--no-geocode" not in sys.argv
    fa = Fetched()
    seed = load_json(ROOT / "data" / "seed" / "entities.json")["entities"]
    seed_by_id = {e["id"]: e for e in seed}
    signals = {r["parish_id"]: r for r in load_json(ECO / "parish_signals.json")["records"]}
    kofc = load_json(ECO / "kofc_councils.json")
    f990 = load_json(ECO / "form990.json", {"records": {}, "unmatched": []})

    orgs: dict[str, dict] = {}
    rels: list[dict] = []
    problems: list[str] = []

    def signal_url(parish_id: str, key: str | None = None) -> str | None:
        r = signals.get(parish_id) or {}
        for c in r.get("religious_clergy", []):
            if key is None or c["institute_key"] == key:
                return c["source_url"]
        return None

    def add_org(o: dict) -> None:
        o = dict(o)
        for k in ("pp_query", "pp_city"):
            o.pop(k, None)
        o.setdefault("state", "CA" if county_of(o.get("city")) else None)
        o.setdefault("county", county_of(o.get("city")))
        assert o["id"] not in orgs and o["id"] not in seed_by_id, o["id"]
        orgs[o["id"]] = o

    def add_rel(f, t, rel, evidence, src, basis="stated", **kw):
        rels.append({"from_id": f, "to_id": t, "relation": rel, "evidence": evidence, "source_url": src,
                     "basis": basis, **kw})

    # ------------------------------------------------------------- curated
    for o in registry.ORGS:
        add_org(o)
    for r in registry.RELS:
        r = dict(r)
        src = r["source_url"]
        if src.startswith("PARISH_SIGNAL:"):
            _, pid, key = src.split(":")
            src = signal_url(pid, key)
            if not src:
                problems.append(f"no parish-signal URL for {r}")
                continue
        f, t, rel, ev = r.pop("from_id"), r.pop("to_id"), r.pop("relation"), r.pop("evidence")
        r.pop("source_url")
        add_rel(f, t, rel, ev, src, **r)

    # ------------------------------------------------------------- Knights of Columbus
    k_src = {}
    for c in kofc["councils"]:
        num = c["council_number"]
        is_asm = c["type_code"] == "A"
        oid = f"kofc-{'a' if is_asm else 'c'}{num}"
        k_src[num] = oid
        add_org({"id": oid, "name": f"Knights of Columbus {'Assembly' if is_asm else 'Council'} {num} ({c['council_name']})",
                 "type": "kofc_assembly" if is_asm else "kofc_council", "city": c["city"], "zip": c.get("zip") or None,
                 "website": ("http://" + c["website"]) if c.get("website") and not c["website"].startswith("http") else c.get("website"),
                 "parent_body": "Knights of Columbus" + (" (Fourth Degree)" if is_asm else ""),
                 "kofc_number": num, "kofc_assembly": (c.get("assembly") or "").replace(".0", "") or None,
                 "description": "Listed by the Knights of Columbus council finder; no meeting place given there.",
                 "source_url": [c["source_url"], c["finder_url"]]})
    for num, (pid, basis) in registry.KOFC_PARISH.items():
        oid = k_src.get(num)
        if not oid:
            problems.append(f"KofC {num} not in finder results")
            continue
        c = next(x for x in kofc["councils"] if x["council_number"] == num)
        if basis == "website":
            add_rel(oid, pid, "meets_at", f"Council website is hosted on the parish site ({c['website']}).", c["source_url"])
        else:
            add_rel(oid, pid, "meets_at", f"Council is named for {seed_by_id[pid]['name']} and listed in the same city.",
                    c["source_url"], basis="inferred")
    for num, (pid, city, label) in registry.KOFC_EXTRA.items():
        oid = f"kofc-c{num}"
        url = next((k["source_url"] for k in signals[pid]["kofc_councils"] if k["council_number"] == num), None)
        add_org({"id": oid, "name": f"Knights of Columbus Council {num}" + (f" ({label.split(' Council')[0]})" if "Kolbe" in label else ""),
                 "type": "kofc_council", "city": city, "parent_body": "Knights of Columbus", "kofc_number": num,
                 "description": "Named on the parish website; not returned by the KofC finder for these ZIP codes.",
                 "source_url": [url]})
    for pid, r in signals.items():
        for k in r["kofc_councils"]:
            num = k["council_number"]
            oid = k_src.get(num) or (f"kofc-c{num}" if f"kofc-c{num}" in orgs else None)
            if not oid:
                continue
            if pid == "david-richmond":
                add_rel(oid, pid, "serves", "Parish has a KofC Round Table whose members belong to a nearby council.", k["source_url"])
            else:
                add_rel(oid, pid, "meets_at", f"Parish website names Knights of Columbus Council {num}.", k["source_url"])

    # ------------------------------------------------------------- St. Vincent de Paul conferences
    cc_url = S["svdp_cc"]
    for name, pid in SVDP_CC:
        oid = f"svdp-{pid}"
        pname = seed_by_id[pid]["name"]
        add_org({"id": oid, "name": f"St. Vincent de Paul Conference, {pname} ({seed_by_id[pid]['city']})", "type": "svdp_conference",
                 "city": seed_by_id[pid]["city"], "parent_id": "chr-svdp-cc", "parent_body": "Society of St. Vincent de Paul",
                 "description": f"Parish-based conference listed by SVdP Contra Costa as '{name}'.", "source_url": [cc_url]})
        add_rel(oid, pid, "based_at", "Listed among SVdP Contra Costa's parish-based branch locations.", cc_url)
    add_org({"id": "svdp-vincentians-of-the-arc", "name": "Vincentians of the Arc (SVdP youth & young adult conference)",
             "type": "svdp_conference", "city": "San Ramon", "parent_id": "chr-svdp-cc", "parent_body": "Society of St. Vincent de Paul",
             "description": "Youth and young-adult conference at the St. Joan of Arc address.", "source_url": [cc_url]})
    add_rel("svdp-vincentians-of-the-arc", "joan-arc-san-ramon", "based_at", "Listed at St. Joan of Arc's address (2601 San Ramon Valley Blvd).", cc_url)
    add_org({"id": "svdp-hope-cc", "name": "Hope Conference (SVdP Contra Costa, by referral only)", "type": "svdp_conference",
             "parent_id": "chr-svdp-cc", "parent_body": "Society of St. Vincent de Paul",
             "description": "Non-parish conference listed as 'by referral only'.", "source_url": [cc_url]})
    # Alameda County: the council lists 37 parish conferences but its locations page refused this environment (429),
    # so conferences are taken from parish websites that mention a St. Vincent de Paul conference/ministry.
    for pid, r in signals.items():
        e = seed_by_id.get(pid)
        if not e or not r.get("svdp") or (e.get("county") or county_of(e.get("city"))) != "Alameda":
            continue
        oid = f"svdp-{pid}"
        add_org({"id": oid, "name": f"St. Vincent de Paul Conference, {e['name']} ({e['city']})", "type": "svdp_conference",
                 "city": e["city"], "parent_id": "chr-svdp-alameda", "parent_body": "Society of St. Vincent de Paul",
                 "description": "Seen on the parish website (St. Vincent de Paul ministry or conference).",
                 "source_url": [r["svdp"]["source_url"]]})
        add_rel(oid, pid, "based_at", "Parish website lists a St. Vincent de Paul conference or ministry.", r["svdp"]["source_url"])

    # ------------------------------------------------------------- parish-website group signals -> meets_at
    group_seen = defaultdict(list)
    for pid, r in signals.items():
        for g, hit in r["groups"].items():
            oid = registry.GROUP_ORG.get(g)
            if not oid:
                continue
            group_seen[oid].append(hit["source_url"])
            add_rel(oid, pid, "meets_at", f"Parish website lists {g} among its ministries or groups.", hit["source_url"])
    for oid, urls in group_seen.items():
        src = orgs[oid]["source_url"]
        orgs[oid]["source_url"] = [u for u in src if u != "PARISH_SIGNALS"] + sorted(set(urls))[:5]
        if "PARISH_SIGNALS" in src:
            orgs[oid]["description"] += f" Seen on {len(set(urls))} crawled parish site(s)."
    for o in orgs.values():
        if "PARISH_SIGNALS" in o["source_url"]:
            o["source_url"] = [u for u in o["source_url"] if u != "PARISH_SIGNALS"]

    # ------------------------------------------------------------- de-duplicate relationships
    merged: dict[tuple, dict] = {}
    for r in rels:
        key = (r["from_id"], r["to_id"], r["relation"], r.get("status", "current"))
        r["fetched_at"] = fa(r["source_url"])
        if key in merged:
            m = merged[key]
            if r["source_url"] != m["source_url"] or r["evidence"] != m["evidence"]:
                m.setdefault("more_evidence", []).append({k: r[k] for k in ("evidence", "source_url", "fetched_at")})
            if r["basis"] == "stated":
                m["basis"] = "stated"
            continue
        merged[key] = r
    rels = list(merged.values())

    # ------------------------------------------------------------- 990s, provenance, geocode
    for oid, s in f990["records"].items():
        if oid in orgs:
            lt = s.get("latest") or {}
            orgs[oid]["ein"] = s["ein"]
            orgs[oid]["form990"] = {
                "irs_name": s["irs_name"], "tax_period": lt.get("tax_period"), "period_begin": lt.get("period_begin"),
                "period_end": lt.get("period_end"), "period_months": lt.get("period_months"), "return_type": lt.get("return_type"),
                "prior_year_revenue": lt.get("prior_year_revenue"), "note": lt.get("note"),
                "total_revenue": lt.get("total_revenue"), "total_expenses": lt.get("total_expenses"),
                "total_assets": lt.get("total_assets"), "total_liabilities": lt.get("total_liabilities"),
                "employees": lt.get("employees"), "volunteers": lt.get("volunteers"),
                "match_basis": s.get("match_basis"), "figures_source": lt.get("source"), "source_url": s["source_url"],
                "propublica_page": s["propublica_page"], "xml_url": lt.get("xml_url"), "fetched_at": s["fetched_at"]}
    for o in orgs.values():
        o["sources"] = [{"url": u, "fetched_at": fa(u)} for u in o["source_url"]]
        times = [x["fetched_at"] for x in o["sources"] if x["fetched_at"]]
        o["fetched_at"] = max(times) if times else None
        o["source_url"] = o["source_url"][0] if o["source_url"] else None
        for x in o["sources"]:
            if not x["fetched_at"]:
                problems.append(f"org {o['id']}: no fetched_at for {x['url']}")
        if geocode and o.get("address") and not re.match(r"(?i)p\.?\s*o\.?\s*box", o["address"]) and o.get("county") in ("Alameda", "Contra Costa"):
            g = census_geocode(f"{o['address']}, {o['city']}, CA")
            if g:
                o.update({"lat": g["lat"], "lng": g["lng"], "geo_source": "census_geocoder", "geo_source_url": g["source_url"]})
    for r in rels:
        if not r["fetched_at"]:
            problems.append(f"rel {r['from_id']}->{r['to_id']}: no fetched_at for {r['source_url']}")

    # ------------------------------------------------------------- validate ids
    def name_of(i):
        return (orgs.get(i) or seed_by_id.get(i) or {}).get("name")
    for o in orgs.values():
        if o.get("parent_id") and not name_of(o["parent_id"]):
            problems.append(f"org {o['id']}: unknown parent_id {o['parent_id']}")
    for r in rels:
        for k in ("from_id", "to_id"):
            if not name_of(r[k]):
                problems.append(f"rel unknown id {r[k]} ({r['from_id']}->{r['to_id']})")
        r["from_name"], r["to_name"] = name_of(r["from_id"]), name_of(r["to_id"])
    if problems:
        print("\n".join(problems))
        raise SystemExit(f"{len(problems)} problems")

    # ------------------------------------------------------------- write
    order = ["religious_institute", "religious_house", "seminary", "higher_education", "formation_program",
             "diocesan_ministry", "chaplaincy", "ethnic_apostolate", "charity", "healthcare", "svdp_council", "svdp_conference",
             "retreat_center", "media", "lay_movement", "fraternal_order", "kofc_council", "kofc_assembly", "community_partner",
             "served_facility"]
    recs = sorted(orgs.values(), key=lambda o: (order.index(o["type"]) if o["type"] in order else 99, o["id"]))
    keys = ["id", "name", "type", "postnominal", "gender", "status", "address", "city", "county", "state", "zip", "lat", "lng",
            "website", "ein", "parent_id", "parent_body", "description", "operator", "population_note", "kofc_number",
            "kofc_assembly", "form990", "geo_source", "geo_source_url", "source_url", "fetched_at", "sources"]
    out = [{k: o.get(k) for k in keys if k in o or k in ("address", "city", "county", "website", "ein", "parent_id", "parent_body", "form990")}
           for o in recs]
    now = now_iso()
    type_counts = Counter(o["type"] for o in recs)
    save_json(ECO / "orgs.json", {
        "generated_at": now, "generated_by": "ingest/public/ecosystem/build_ecosystem.py",
        "count": len(out), "count_by_type": dict(type_counts.most_common()),
        "notes": ["Existing ids (parishes, schools, cceb, cemeteries, chancery offices, dio-oakland) live in data/seed/entities.json and are only referenced here.",
                  "form990 is null for church bodies, which do not file 990s, and for orgs ProPublica could not match.",
                  "status: absent = active as of the source; departed_2024 / moved_2013 mark former presences."],
        "records": out})
    rel_order = ["staffs", "sponsors", "operates", "based_at", "forms_seminarians_for", "serves", "meets_at", "partners_with"]
    rels.sort(key=lambda r: (rel_order.index(r["relation"]), r["from_id"], r["to_id"]))
    rkeys = ["from_id", "from_name", "relation", "to_id", "to_name", "evidence", "basis", "status", "since", "until",
             "source_url", "fetched_at", "more_evidence"]
    rout = [{k: r[k] for k in rkeys if k in r} for r in rels]
    for r in rout:
        r.setdefault("status", "current")
    save_json(ECO / "relationships.json", {
        "generated_at": now, "generated_by": "ingest/public/ecosystem/build_ecosystem.py",
        "count": len(rout), "count_by_relation": dict(Counter(r["relation"] for r in rout).most_common()),
        "count_by_basis": dict(Counter(r["basis"] for r in rout).most_common()),
        "schema": {"from_id": "org or entity id", "to_id": "org or entity id",
                   "relation": "staffs | sponsors | based_at | serves | forms_seminarians_for | meets_at | partners_with | operates",
                   "evidence": "paraphrase or <=15-word quote", "basis": "stated (source says so) | inferred (name + city match)",
                   "status": "current | ended", "source_url": "page", "fetched_at": "UTC fetch time"},
        "records": rout})
    print(f"orgs: {len(out)} {dict(type_counts.most_common())}")
    print(f"relationships: {len(rout)} {dict(Counter(r['relation'] for r in rout).most_common())}")


if __name__ == "__main__":
    main()
