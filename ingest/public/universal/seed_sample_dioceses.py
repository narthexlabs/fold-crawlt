"""Seeding test: build a parish list for other California dioceses from openly licensed sources only.

Why these sources (see docs/research/08-universal-platform.md, section 3):
  * Wikidata (CC0) gives the diocese layer: label, Catholic-Hierarchy ID (P1866), GCatholic ID (P8389),
    official website (P856), inception (P571).
  * English Wikipedia "List of churches in the ..." articles (CC BY-SA 4.0) give a parish/church list
    grouped by deanery, usually citing the diocese's own parish finder. Reuse is allowed with attribution
    and share-alike, so the output file carries the article, revision id and licence.
  * Not used: GCatholic, catholic-hierarchy.org (no reuse terms published; permission needed),
    MassTimes.org (data copyrighted; written permission required), the Official Catholic Directory
    (licensed), diocesan websites (terms not verified in this session).

Optional calibration: if the Oakland article can be fetched, its list is matched against the diocese's
own directory (data/real/parishes_directory.json) to measure how far a Wikipedia seed can be trusted.

Writes data/real/universal/sample_dioceses.json. Raw API responses are cached in data/raw/universal/
(gitignored). Run:  python3 -I ingest/public/universal/seed_sample_dioceses.py [--no-download]
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "universal"
OUT = ROOT / "data" / "real" / "universal" / "sample_dioceses.json"
OAK_DIRECTORY = ROOT / "data" / "real" / "parishes_directory.json"
UA = "OaklandDioceseResearch/0.1 (public-data research; github.com/FrenchFry123/oakland-diocese-dashboard)"
WP_API = "https://en.wikipedia.org/w/api.php"
WD_SPARQL = "https://query.wikidata.org/sparql"

DIOCESES = [
    {"id": "dio-san-francisco", "ch_code": "snfr", "counties": ["San Francisco", "Marin", "San Mateo"],
     "wp_title": "List of churches in the Archdiocese of San Francisco"},
    {"id": "dio-san-jose", "ch_code": "snjo", "counties": ["Santa Clara"],
     "wp_title": "List of churches in the Diocese of San Jose in California"},
    {"id": "dio-sacramento", "ch_code": "sacr", "counties": None,  # 20 northern counties; not listed here
     "wp_title": "List of churches in the Diocese of Sacramento"},
    {"id": "dio-oakland", "ch_code": "oakl", "counties": ["Alameda", "Contra Costa"],
     "wp_title": "List of churches in the Diocese of Oakland", "calibration": True},
]

FORMER_WORDS = re.compile(r"\b(closed|merged|demolish|suppress|razed|destroyed|sold|deconsecrat|consolidat|"
                          r"no longer|former(ly)?|defunct|relocated|replaced by|torn down)\b", re.I)
OTHER_CENTRE = re.compile(r"\b(chapel|oratory|shrine|newman|campus ministry|catholic community at|"
                          r"center|centre|mission(?! (san|santa|dolores|de)\b))", re.I)


def http_get(url: str, params: dict, accept: str, cache: Path, tries: int = 3) -> str:
    if "--no-download" in sys.argv and cache.exists():
        return cache.read_text()
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read().decode("utf-8")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(body)
            return body
        except urllib.error.HTTPError as e:  # 429 / 5xx: back off politely, honour Retry-After
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 20 * (attempt + 1))
                time.sleep(min(wait, 90))
                continue
            raise
    raise RuntimeError("unreachable")


def wikipedia(title: str) -> dict:
    cache = RAW / ("wp_" + re.sub(r"\W+", "_", title) + ".json")
    body = http_get(WP_API, {"action": "parse", "page": title, "prop": "wikitext|revid", "format": "json",
                             "redirects": 1, "maxlag": 5}, "application/json", cache)
    return json.loads(body)["parse"]


def wikidata(codes: list[str]) -> dict[str, dict]:
    values = " ".join(f'"{c}"' for c in codes)
    q = f"""SELECT ?d ?dLabel ?ch ?gc ?web ?inception ?typeLabel WHERE {{
      VALUES ?ch {{ {values} }} ?d wdt:P1866 ?ch .
      OPTIONAL {{ ?d wdt:P8389 ?gc }} OPTIONAL {{ ?d wdt:P856 ?web }} OPTIONAL {{ ?d wdt:P571 ?inception }}
      OPTIONAL {{ ?d wdt:P31 ?type }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
    body = http_get(WD_SPARQL, {"query": q}, "application/sparql-results+json", RAW / "wikidata_dioceses.json")
    out: dict[str, dict] = {}
    for b in json.loads(body)["results"]["bindings"]:
        code = b["ch"]["value"]
        rec = out.setdefault(code, {"qid": b["d"]["value"].rsplit("/", 1)[-1], "label": b["dLabel"]["value"],
                                    "catholic_hierarchy_id": code, "gcatholic_id": None, "website": None,
                                    "inception": None, "instance_of": []})
        if "gc" in b:
            rec["gcatholic_id"] = b["gc"]["value"]
        if "web" in b:
            rec["website"] = b["web"]["value"]
        if "inception" in b:
            rec["inception"] = b["inception"]["value"][:10]
        t = b.get("typeLabel", {}).get("value")
        if t and t not in rec["instance_of"]:
            rec["instance_of"].append(t)
    return out


# ------------------------------------------------------------------ wikitext parsing
def clean(s: str) -> str:
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", s, flags=re.I)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"<br\s*/?>", "; ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", s).strip(" ;")


def link_target(s: str) -> str | None:
    m = re.search(r"\[\[([^\]|#]+)", s)
    if m and not m.group(1).lower().startswith(("file:", "image:")):
        return m.group(1).strip()
    return None


def header_name(cell: str) -> str:
    return clean(cell.split("|")[-1]).lower()


def parse_tables(wikitext: str) -> list[dict]:
    rows: list[dict] = []
    h2 = h3 = None
    lines = wikitext.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(={2,3})\s*(.*?)\s*\1\s*$", line)
        if m:
            if len(m.group(1)) == 2:
                h2, h3 = clean(m.group(2)), None
            else:
                h3 = clean(m.group(2))
        if line.startswith("{|"):
            headers: list[str] = []
            cur: list[str] | None = None
            i += 1
            while i < len(lines) and not lines[i].startswith("|}"):
                ln = lines[i]
                if ln.startswith("!"):
                    for c in re.split(r"!!", ln[1:]):
                        headers.append(header_name(c))
                elif ln.startswith("|-"):
                    if cur:
                        rows.append(make_row(headers, cur, h2, h3))
                    cur = []
                elif ln.startswith("|"):
                    if cur is None:
                        cur = []
                    cur.extend(re.split(r"\|\|", ln[1:]))
                elif cur:
                    cur[-1] += " " + ln  # continuation line
                i += 1
            if cur:
                rows.append(make_row(headers, cur, h2, h3))
        i += 1
    return [r for r in rows if r and r.get("name")]


def make_row(headers: list[str], cells: list[str], h2, h3) -> dict:
    rec: dict = {"section": h2, "subsection": h3}
    for h, raw in zip(headers, cells):
        key = ("name" if ("name" in h or h in ("church", "parish")) else
               "location" if ("location" in h or "address" in h) else
               "established" if ("est" in h or "found" in h or "built" in h) else
               "notes" if ("note" in h or "description" in h) else None)
        if not key:
            continue
        rec[key] = clean(raw)
        if key == "name":
            rec["wikipedia_article"] = link_target(raw)
    return rec


def classify(rec: dict) -> dict:
    text = " ".join(str(rec.get(k) or "") for k in ("name", "notes", "section", "subsection"))
    former = bool(FORMER_WORDS.search(text)) or bool(re.search(r"former|closed", str(rec.get("section")), re.I))
    kind = "other_pastoral_centre" if OTHER_CENTRE.search(rec.get("name") or "") else "parish_or_church"
    rec["status_guess"] = "former_or_changed" if former else "listed_current"
    rec["kind_guess"] = kind
    rec["needs_review"] = former or kind != "parish_or_church"
    return rec


def split_location(loc: str | None) -> tuple[str | None, str | None]:
    if not loc:
        return None, None
    parts = [p.strip() for p in loc.split(",")]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    if not re.search(r"\d", loc):  # a bare place name, e.g. "Tomales"
        return None, loc
    return loc, None


# ------------------------------------------------------------------ Oakland calibration
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(saint|st|sts|parish|church|catholic|the|of|and|community|cathedral|basilica|mission)\b", " ", s)
    return re.sub(r"[^a-z]+", " ", s).strip()


def calibrate(wp_rows: list[dict]) -> dict:
    official = json.loads(OAK_DIRECTORY.read_text())["records"]
    off = [(norm(r["name"]), (r.get("city") or "").lower(), r["name"]) for r in official]
    matched_off: set[int] = set()
    wp_current = [r for r in wp_rows if r["status_guess"] == "listed_current"]
    hits = 0
    unmatched_wp = []
    for r in wp_current:
        n, city = norm(r["name"]), (r.get("city") or "").lower()
        found = None
        for j, (on, oc, _) in enumerate(off):
            if j in matched_off:
                continue
            same_city = bool(city) and (city in oc or oc in city)
            if on and n and (on == n or on in n or n in on) and (same_city or not city):
                found = j
                break
        if found is not None:
            matched_off.add(found)
            hits += 1
        else:
            unmatched_wp.append(f'{r["name"]} ({r.get("city")})')
    missing = [off[j][2] + f" ({official[j].get('city')})" for j in range(len(off)) if j not in matched_off]
    return {
        "official_source": "https://www.oakdiocese.org/parishes/ (data/real/parishes_directory.json)",
        "official_parishes": len(official),
        "wikipedia_rows_listed_current": len(wp_current),
        "matched": hits,
        "recall_vs_official": round(hits / len(official), 3) if official else None,
        "precision_vs_official": round(hits / len(wp_current), 3) if wp_current else None,
        "wikipedia_only": unmatched_wp,
        "official_only": missing,
        "method": "normalized name containment + same city; Wikipedia rows flagged former/closed excluded",
    }


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    wd = wikidata([d["ch_code"] for d in DIOCESES])
    result = {
        "dataset": "Seeding test: parish lists for California dioceses from openly licensed sources",
        "generated_at": now,
        "generator": "ingest/public/universal/seed_sample_dioceses.py",
        "licences": {
            "wikidata": "CC0 1.0 (https://www.wikidata.org/wiki/Wikidata:Licensing)",
            "wikipedia": "CC BY-SA 4.0; this file's parish rows are derived from the cited article revisions and "
                         "must keep attribution and the same licence (https://en.wikipedia.org/wiki/"
                         "Wikipedia:Reusing_Wikipedia_content)",
        },
        "caveats": [
            "Wikipedia lists mix current parishes, missions, chapels, campus ministries and some former churches. "
            "status_guess/kind_guess are keyword heuristics; needs_review=true rows must be checked by the diocese.",
            "Rows are a seed for the diocese to confirm, never a substitute for the diocese's own directory or its "
            "Official Catholic Directory listing.",
            "No people, Mass times or statistics are taken; only names, addresses, deanery and founding year.",
        ],
        "dioceses": [],
    }
    for cfg in DIOCESES:
        meta = wd.get(cfg["ch_code"], {})
        entry = {"id": cfg["id"], "wikidata": meta, "counties": cfg["counties"], "parishes": [],
                 "source": {"title": cfg["wp_title"]}}
        try:
            page = wikipedia(cfg["wp_title"])
        except Exception as e:  # rate limits are common from shared datacenter IPs
            entry["source"]["error"] = f"{type(e).__name__}: {e}"
            result["dioceses"].append(entry)
            continue
        revid = page.get("revid")
        url = "https://en.wikipedia.org/w/index.php?" + urllib.parse.urlencode({"title": page["title"], "oldid": revid})
        entry["source"].update({"url": url, "revid": revid, "fetched_at": now, "licence": "CC BY-SA 4.0"})
        rows = [classify(r) for r in parse_tables(page["wikitext"]["*"])]
        for r in rows:
            r["address"], r["city"] = split_location(r.get("location"))
            if not r["city"] and r.get("section") == "San Francisco County":  # city implied by the section
                r["city"] = "San Francisco"
            r["deanery"] = r["subsection"] if (r.get("subsection") and "deanery" in r["subsection"].lower()) else (
                r["section"] if r.get("section") and "deanery" in r["section"].lower() else None)
            r["county_or_region"] = r["section"] if r.get("deanery") == r.get("subsection") else None
            r["source_url"] = url
        entry["parishes"] = rows
        entry["counts"] = {
            "rows": len(rows),
            "listed_current": sum(r["status_guess"] == "listed_current" for r in rows),
            "listed_current_parish_or_church": sum(r["status_guess"] == "listed_current"
                                                   and r["kind_guess"] == "parish_or_church" for r in rows),
            "needs_review": sum(r["needs_review"] for r in rows),
            "deaneries": sorted({r["deanery"] for r in rows if r.get("deanery")}),
        }
        if cfg.get("calibration"):
            entry["calibration"] = calibrate(rows)
        result["dioceses"].append(entry)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    for d in result["dioceses"]:
        print(d["id"], d.get("counts"), d["source"].get("error", ""))
        if d.get("calibration"):
            c = d["calibration"]
            print("  calibration: recall", c["recall_vs_official"], "precision", c["precision_vs_official"])


if __name__ == "__main__":
    main()
