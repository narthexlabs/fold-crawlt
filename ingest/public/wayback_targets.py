"""targets.json for ingest/public/wayback_home.py, built from data/real/recovery_2026-10-31.json.

    python3 -I ingest/public/wayback_targets.py <out targets.json>

The 13 parishes with no own-source times or bulletin after the side-door pass: per parish the hosts tried, the
Availability-API captures recorded earlier, and the CDX listings to request (domain listing from a year that covers
the newest captures; for three sites a second, collapsed listing of 2021-2024 pages, used for groups only). The
2026-10-31 run added one query by hand (the Google Site that stanthonyoakley.org framed, found in its 2016-2018
captures); the file actually used is data/real/wayback/targets_2026-10-31.json.
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path
REC = Path(__file__).resolve().parents[2] / "data/real/recovery_2026-10-31.json"
OUT = sys.argv[1]
rep = json.load(open(REC))["report"]
IDS = ["christ-light-oakland", "leo-great-oakland", "mark-richmond", "perpetua-lafayette",
       "anthony-mary-help-christians-oakland", "good-shepherd-pittsburg", "divine-mercy-oakland", "raymond-dublin",
       "anthony-oakley", "cornelius-richmond", "our-lady-lourdes-oakland", "rose-lima-patrick-crockett", "bernard-oakland"]
DEAD = {"our-lady-lourdes-oakland", "anthony-oakley", "cornelius-richmond", "divine-mercy-oakland", "good-shepherd-pittsburg"}
# entity -> (CDX key, url, matchType, from, path filter on that listing)
Q = {
    "christ-light-oakland": [("ctlcathedral.org", "ctlcathedral.org", "domain", "2025", None)],
    "leo-great-oakland": [("ctlcathedral.org", "ctlcathedral.org", "domain", "2025", r"st-leo|stleo"),
                          ("stleothegreat.org", "stleothegreat.org", "domain", "2025", None)],
    "mark-richmond": [("stmarkrichmond.org", "stmarkrichmond.org", "domain", "2025", None)],
    "perpetua-lafayette": [("stperpetua.org", "stperpetua.org", "domain", "2025", None)],
    "anthony-mary-help-christians-oakland": [("stanthonymaryhelp.org@2020", "stanthonymaryhelp.org", "domain", "2020", None),
                                             ("stanthony-maryhelp.org@2020", "stanthony-maryhelp.org", "domain", "2020", None)],
    "good-shepherd-pittsburg": [("goodshepherdpittsburg.org@2018", "goodshepherdpittsburg.org", "domain", "2018", None)],
    "divine-mercy-oakland": [("divinemercyoak.org@2023", "divinemercyoak.org", "domain", "2023", None)],
    "raymond-dublin": [("st-raymond-dublin.org@2023", "st-raymond-dublin.org", "domain", "2023", None)],
    "anthony-oakley": [("stanthonyoakley.org@2018", "stanthonyoakley.org", "domain", "2018", None)],
    "cornelius-richmond": [("stcorneliusrichmond.org@2020", "stcorneliusrichmond.org", "domain", "2020", None)],
    "our-lady-lourdes-oakland": [("lourdesoakland.com@2018", "lourdesoakland.com", "domain", "2018", None)],
    "rose-lima-patrick-crockett": [("strosecrockett.org@2023", "strosecrockett.org", "domain", "2023", None)],
    "bernard-oakland": [("oakdiocese.org/bios/st-bernard-parish", "oakdiocese.org/bios/st-bernard-parish", "prefix", "2023", None)],
}
# older pages for groups (older captures are fine for groups): one row per URL (collapse=urlkey) to learn the URLs
OLDER = [("stmarkrichmond.org<2025", "stmarkrichmond.org", "domain", "2021", "2024", "urlkey"),
         ("ctlcathedral.org<2025", "ctlcathedral.org", "domain", "2022", "2024", "urlkey"),
         ("stperpetua.org<2025", "stperpetua.org", "domain", "2022", "2024", "urlkey")]
queries, seen = [], set()
par = {}
for eid in IDS:
    r = rep[eid]
    w = r["wayback"]
    par[eid] = {"name": r["name"], "hosts_tried": r.get("hosts"), "dead_or_hijacked": eid in DEAD,
                "side_door_note": r.get("note"),
                "availability_api": {"latest_capture": w.get("latest_capture"), "captures_found": w.get("captures_found"),
                                     "history": w.get("history")},
                "cdx": [{"key": k, "path_filter": pf} for k, _, _, _, pf in Q[eid]]}
    for k, u, mt, frm, pf in Q[eid]:
        if k not in seen:
            seen.add(k)
            queries.append({"key": k, "url": u, "matchType": mt, "from": frm})
for k, u, mt, frm, to, col in OLDER:
    queries.append({"key": k, "url": u, "matchType": mt, "from": frm, "to": to, "collapse": col})
par["mark-richmond"]["cdx"].append({"key": "stmarkrichmond.org<2025", "path_filter": None, "groups_only": True})
par["christ-light-oakland"]["cdx"].append({"key": "ctlcathedral.org<2025", "path_filter": None, "groups_only": True})
par["perpetua-lafayette"]["cdx"].append({"key": "stperpetua.org<2025", "path_filter": None, "groups_only": True})
json.dump({"generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           "built_from": "data/real/recovery_2026-10-31.json (13 parishes with no own-source times or bulletin after the side-door pass)",
           "rules": "CDX filter=statuscode:200, read newest first; captures fetched as id_ raw content; 1 request per 1.1 s",
           "parishes": par, "cdx_queries": queries}, open(OUT, "w"), indent=1, ensure_ascii=False)
print(len(queries), "cdx queries")
