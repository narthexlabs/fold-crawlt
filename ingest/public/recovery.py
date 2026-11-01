"""Recovery pass for parishes whose own site is unreadable: bulletins, times and groups from side doors + Wayback.

    python3 -I ingest/public/sidedoors.py          # 1. probe the side doors     -> data/raw/sidedoors/doors.json
    python3 -I ingest/public/wayback.py            # 2. Wayback captures          -> data/raw/wayback/captures.json
    python3 -I ingest/public/recovery.py bulletins # 3. download the latest bulletin each open door lists; append to
                                                   #    data/real/bulletins/index.json
    python3 -I ingest/public/bulletins/extract.py --ids <ids>          # 4. parse (rule-based, privacy-screened)
    python3 -I ingest/public/recovery.py build --have-json <db export> # 5. mass rows, groups, report
        -> data/real/recovery_2026-10-31.json, /tmp/recovery_mass.sql, /tmp/recovery_groups.sql (INSERT only)

Times: WordPress REST pages (content.rendered) go through parish_sites.mass_block/parse_mass and the
crawled_mass_schedule filters (rows_from_record); bulletin schedules come from parse_one. Mass rows are inserted only
for parishes whose existing rows of that kind are all DiscoverMass (secondary); other parishes get the comparison
in the JSON only. Groups: WordPress ministry pages through parish_sites.parse_ministries + build_groups.classify, and
bulletin ministries through bulletins/load.py (same taxonomy, privacy screen and duplicate check).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "groups"))
sys.path.insert(0, str(HERE / "bulletins"))
from common import RAW, REAL, ROOT, now_iso, save_json  # noqa: E402

DOORS = RAW / "sidedoors" / "doors.json"
WAYBACK = RAW / "wayback" / "captures.json"
BDIR = RAW / "bulletins"
INDEX = REAL / "bulletins" / "index.json"
OUT = REAL / "recovery_2026-10-31.json"
SESSION = requests.Session()  # default python-requests User-Agent
# Times read off each bulletin's schedule box by hand (2026-10-31), checked against the parser candidates.
REVIEWED = {
    "benedict-oakland": [  # 2026-10-25 p.2 box: "SATURDAY ... 4:00 pm VIGIL MASS", "SUNDAY ... 10:00 am GOSPEL MASS";
        # confession "BY APPOINTMENT" (no row)
        {"date": "2026-10-25", "kind": "mass", "day": "Saturday", "time": "16:00", "raw": "Saturday 4:00 pm Vigil Mass"},
        {"date": "2026-10-25", "kind": "mass", "day": "Sunday", "time": "10:00", "raw": "Sunday 10:00 am Gospel Mass"}],
    "columba-oakland": [  # 2026-11-01 p.1 "Liturgy": Mon/Fri/Sat 8:00 and Tue/Thu 7:00 are prayer on Zoom, not Mass
        {"date": "2026-11-01", "kind": "mass", "day": "Tuesday, Wednesday, Thursday", "time": "08:00",
         "raw": "Mass: Tuesday, Wednesday and Thursday 8:00 AM"},
        {"date": "2026-11-01", "kind": "mass", "day": "Saturday", "time": "16:00", "raw": "Saturday Mass: 4:00 PM"},
        {"date": "2026-11-01", "kind": "mass", "day": "Sunday", "time": "08:00", "raw": "Sunday Masses: 8:00 AM and 10:30 AM"},
        {"date": "2026-11-01", "kind": "mass", "day": "Sunday", "time": "10:30", "raw": "Sunday Masses: 8:00 AM and 10:30 AM"},
        {"date": "2026-11-01", "kind": "confession", "day": "Saturday", "time": "15:30",
         "raw": "Reconciliation 3:30 PM - 4:00 PM Saturday or by appt."}],
    "most-holy-rosary-antioch": [  # 2026-10-25 p.1 box (two OCR columns): confessions Fri-Sat 4-5 pm; chapel open for
        # Eucharistic Adoration Mon-Fri 9 am-5 pm (First Friday 24 hours)
        {"date": "2026-10-25", "kind": "confession", "day": "Friday", "time": "16:00", "raw": "Confessions Friday-Saturday 4pm to 5pm"},
        {"date": "2026-10-25", "kind": "confession", "day": "Saturday", "time": "16:00", "raw": "Confessions Friday-Saturday 4pm to 5pm"},
        {"date": "2026-10-25", "kind": "adoration", "day": "Monday–Friday", "time": "09:00",
         "raw": "Eucharistic Adoration: Monday to Friday our chapel is open from 9am to 5pm"}],
    "anne-union-city": [  # 2026-11-01 p.2 "Hours of Operation"
        {"date": "2026-11-01", "kind": "confession", "day": "Saturday", "time": "15:30", "raw": "Reconciliation Saturdays at 3:30 PM"},
        {"date": "2026-11-01", "kind": "adoration", "day": "First Friday of the month", "time": "09:00",
         "raw": "First Friday of the month in the church for adoration and prayer from 9:00am to 6:45pm"}],
}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def doors() -> dict:
    return json.loads(DOORS.read_text())["parishes"]


def wp_json(eid: str, kind: str) -> list[dict]:
    for x in doors().get(eid, {}).get("doors", []):
        if x["result"] == "ok-json" and f"/wp/v2/{kind}" in x["url"] and x.get("path"):
            j = json.loads((ROOT / x["path"]).read_text())
            if isinstance(j, list):
                return j
    return []


# ------------------------------------------------------------------------------------------- bulletin candidates
def date_from_name(u: str) -> str | None:
    f = urlparse(u).path.rsplit("/", 1)[-1]
    m = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})"
                  r"(?:st|nd|rd|th)?-(20\d\d)", f, re.I)
    if m:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"for-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})-(20\d\d)", f, re.I)
    if m:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d\d)(\d\d)(\d\d)\.pdf", f)  # Peter Martyr: MMDDYY.pdf
    if m:
        return f"20{m.group(3)}-{m.group(1)}-{m.group(2)}"
    m = re.search(r"/bulletins/(20\d\d)(\d\d)(\d\d)\.pdf", u)  # eCatholic files host
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def candidates(eid: str) -> list[dict]:
    """Bulletin PDFs (or page images) the open doors list, newest first, with how each was found."""
    out = []
    for x in doors().get(eid, {}).get("doors", []):
        if x["result"] == "ok-pdf":
            out.append({"url": x["url"], "date": date_from_name(x["url"]), "found_via": f"side door: {x['door']}",
                        "path": x["path"], "fetched_at": x["fetched_at"]})
    for p in wp_json(eid, "pages"):
        c = p.get("content", {}).get("rendered", "")
        if not re.search(r"bul|bolet", p.get("slug", ""), re.I):
            continue
        for u in sorted(set(re.findall(r"https?://[^\"' ]+\.pdf", c))):
            if urlparse(u).netloc.removeprefix("www.") not in urlparse(p.get("link", "")).netloc:
                continue
            d = date_from_name(u)
            if d:
                out.append({"url": u, "date": d, "found_via": f"side door: WordPress REST page '{p['slug']}'",
                            "found_on": p.get("link")})
    for p in wp_json(eid, "posts"):  # image bulletins posted as WordPress posts (St. Jarlath)
        if not re.match(r"bulletin-", p.get("slug", "")):
            continue
        imgs = re.findall(r'src="(https?://[^"]+_page-\d{4}-791x1024\.jpg)"', p.get("content", {}).get("rendered", ""))
        if imgs:
            m = re.match(r"bulletin-(\d{1,2})-(\d{1,2})-(\d\d)$", p["slug"])
            pd = f"20{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else p.get("date", "")[:10]
            out.append({"url": p.get("link"), "date": pd, "images": [
                re.sub(r"-791x1024\.jpg$", "-scaled.jpg", i) for i in imgs],
                "found_via": f"side door: WordPress REST post '{p['slug']}' (bulletin as page images)",
                "found_on": p.get("link")})
            break
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: c.get("date") or "", reverse=True):
        if c["url"].split("?")[0] in seen:
            continue
        seen.add(c["url"].split("?")[0])
        uniq.append(c)
    return uniq


def pdftext(pdf: Path) -> Path:
    txt = pdf.with_suffix(".txt")
    if not txt.exists():
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, timeout=180)
        txt.write_bytes(r.stdout)
    return txt


def polite_get(url: str, last: dict, delay: float = 3.0):
    h = urlparse(url).netloc
    w = last.get(h, 0) + delay - time.time()
    if w > 0:
        time.sleep(w)
    last[h] = time.time()
    try:
        return SESSION.get(url, timeout=120)
    except requests.RequestException:
        return None


def cmd_bulletins(ids: list[str], per_parish: int) -> int:
    from PIL import Image  # noqa: PLC0415
    index = json.loads(INDEX.read_text())
    last: dict = {}
    added = []
    for eid in ids:
        got = 0
        for c in candidates(eid):
            if got >= per_parish or not c.get("date"):
                continue
            host = "ecatholic" if "files.ecatholic.com" in c["url"] else "parish-site"
            d = BDIR / eid
            d.mkdir(parents=True, exist_ok=True)
            pdf = d / f"{host}_{c['date']}.pdf"
            fetched = c.get("fetched_at") or now_iso()
            if not pdf.exists():
                if c.get("path"):
                    pdf.write_bytes((ROOT / c["path"]).read_bytes())
                elif c.get("images"):
                    pages = []
                    for u in c["images"]:
                        r = polite_get(u, last)
                        if r is None or r.status_code != 200 or "image" not in r.headers.get("content-type", ""):
                            pages = []
                            break
                        ip = d / "_img" / urlparse(u).path.rsplit("/", 1)[-1]
                        ip.parent.mkdir(parents=True, exist_ok=True)
                        ip.write_bytes(r.content)
                        pages.append(Image.open(ip).convert("RGB"))
                    if not pages:
                        print(f"  {eid}: page images not readable, skipped")
                        continue
                    pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=200)
                else:
                    r = polite_get(c["url"], last)
                    if r is None or r.status_code != 200 or r.content[:4] != b"%PDF":
                        print(f"  {eid}: {c['url']} -> {getattr(r, 'status_code', 'error')}")
                        continue
                    pdf.write_bytes(r.content)
                fetched = now_iso()
            txt = pdftext(pdf)
            rec = {"entity_id": eid, "host": host, "url": c["url"], "date": c["date"], "fetched_at": fetched,
                   "found_via": c["found_via"], "found_on": c.get("found_on"), "lpi_code": None,
                   "pdf_path": str(pdf.relative_to(ROOT)), "text_path": str(txt.relative_to(ROOT)),
                   "bytes": pdf.stat().st_size}
            if c.get("images"):
                rec["page_images"] = c["images"]
            index["records"] = [x for x in index["records"] if not (x["entity_id"] == eid and x["url"] == c["url"])]
            index["records"].append(rec)
            added.append(rec)
            got += 1
            print(f"{eid:40s} {host:12s} {c['date']} {c['found_via']}")
    index["generated_at"] = now_iso()
    INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    print(len(added), "bulletins indexed; ids:", ",".join(sorted({r["entity_id"] for r in added})))
    return 0


# ------------------------------------------------------------------------------------------- times + groups
def wp_mass_rows(eid: str) -> list[dict]:
    import parish_sites as ps  # noqa: PLC0415
    from crawled_mass_schedule import rows_from_record  # noqa: PLC0415
    out = []
    for p in wp_json(eid, "pages"):
        if not re.search(r"mass|misa|horario|schedule|confess|sacrament", p.get("slug", ""), re.I):
            continue
        soup = BeautifulSoup(p["content"]["rendered"], "html.parser")
        lines = ps.visible_lines(soup)
        block = ps.mass_block(lines)
        rec = {"id": eid, "website": p.get("link"),
               "mass_schedule": {"raw": block or "", "parsed": ps.parse_mass(block), "source_url": p.get("link")}}
        out += rows_from_record(rec, f"sidedoor-wpjson-{p.get('modified', '')[:10] or 'undated'}")
    return out


def bulletin_mass_rows(eid: str, ext: list[dict]) -> tuple[list[dict], dict | None]:
    from privacy import scrub_schedule_line  # noqa: PLC0415
    from crawled_mass_schedule import hhmm, norm_day  # noqa: PLC0415
    bl = sorted([b for b in ext if b["entity_id"] == eid and not b.get("error")], key=lambda b: b.get("date") or "")
    if not bl:
        return [], None
    b = bl[-1]
    rows = []
    for kind in ("mass", "confession", "adoration"):
        for s in (b.get("schedules") or {}).get(kind, []):
            t = s.get("time")
            t = t if t and re.fullmatch(r"\d\d:\d\d", t) else (hhmm(t) if t else None)
            if not t:
                continue
            clean, _ = scrub_schedule_line(s.get("line") or s.get("raw") or "")
            rows.append({"entity_id": eid, "kind": kind, "day": norm_day(s.get("day")), "time": t,
                         "language": s.get("language"), "raw": clean[:200],
                         "source": f"bulletin-{b['host']}-{b['date']}", "source_url": b["source_url"]})
    seen, uniq = set(), []
    for r in rows:
        k = (r["kind"], r["day"], r["time"], r["language"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq, b


def wp_groups(eid: str) -> list[dict]:
    import parish_sites as ps  # noqa: PLC0415
    out = []
    for p in wp_json(eid, "pages"):
        if not re.search(r"ministr|parish-life|groups|organizations|social-justice|vincent|faith-formation|liturgical",
                         p.get("slug", ""), re.I):
            continue
        soup = BeautifulSoup(p["content"]["rendered"], "html.parser")
        for n in ps.parse_ministries(soup, ps.visible_lines(soup), True):
            out.append({"name": n, "source_url": p.get("link"), "modified": (p.get("modified") or "")[:10]})
    return out


def cmd_build(ids: list[str], have_json: str, groups_existing: str) -> int:
    import build_groups as bg  # noqa: PLC0415
    from load import display, nkey, stem  # noqa: PLC0415
    from load_sql import COLS, lit  # noqa: PLC0415
    have = json.loads(Path(have_json).read_text())  # [{entity_id, source, kind, day, time}]
    by = defaultdict(list)
    for r in have:
        by[r["entity_id"]].append(r)
    gex = json.loads(Path(groups_existing).read_text()) if groups_existing else []
    have_gid = {stem(x["group_id"]) for x in gex if x.get("group_id")}
    have_name = {(x["entity_id"], nkey(x["name"])) for x in gex}
    ents = {e["id"]: e for e in json.loads((ROOT / "data/seed/entities.json").read_text())["entities"]}
    ext = json.loads((REAL / "bulletins/extracted.json").read_text())["records"]
    idx = json.loads(INDEX.read_text())["records"]
    dr = doors()
    wb = json.loads(WAYBACK.read_text())
    report, mass_rows, grp_rows = {}, [], {}
    for eid in ids:
        d = dr.get(eid, {"doors": [], "hosts": []})
        res = Counter(x["result"] for x in d["doors"])
        off = sorted({x["redirected_off_host"] for x in d["doors"] if x.get("redirected_off_host")})
        parked = any("/lander" in (Path(ROOT / x["path"]).read_text(errors="ignore")[:300] if x.get("path") else "")
                     for x in d["doors"])
        opened = [] if off or parked else sorted({x["door"] for x in d["doors"] if x["result"].startswith("ok")})
        w = wb["parishes"].get(eid, {})
        caps = [x for x in w.get("latest", []) + w.get("history", []) if x.get("capture_date")]
        latest = max(caps, key=lambda x: x["capture"]) if caps else None
        # ---- times
        cur = by.get(eid, [])
        wp_rows = wp_mass_rows(eid) if not (off or parked) else []
        b_rows, b = bulletin_mass_rows(eid, ext)
        new_rows, comparison = [], {}
        for cand, label in ((wp_rows, "wpjson"), (b_rows, "bulletin")):
            if not cand:
                continue
            for kind in ("mass", "confession", "adoration"):
                ck = [r for r in cand if r["kind"] == kind]
                if not ck:
                    continue
                existing = [r for r in cur if r["kind"] == kind]
                parish_dm = all(r["source"].startswith("secondary-discovermass") for r in cur)
                dm_only = parish_dm or (bool(existing) and all(r["source"].startswith("secondary-discovermass")
                                                                for r in existing))
                ex_keys = {(r["day"], r["time"]) for r in existing}
                ck_keys = {(r["day"], r["time"]) for r in ck}
                comparison[f"{label}:{kind}"] = {
                    "existing_sources": sorted({r["source"] for r in existing}),
                    "existing_all_discovermass": dm_only, "candidate_rows": len(ck),
                    "agree": sorted(f"{a} {t}" for a, t in ck_keys & ex_keys),
                    "only_in_new_source": sorted(f"{a} {t}" for a, t in ck_keys - ex_keys),
                    "only_in_existing": sorted(f"{a} {t}" for a, t in ex_keys - ck_keys),
                    "inserted": False}
                comparison[f"{label}:{kind}"]["eligible_for_insert"] = dm_only
        # The rule-based parsers are candidates only: bulletin OCR mixes columns (St. Columba's Zoom prayer times read as
        # Masses, Most Holy Rosary's Mass list read as adoration). What is inserted is REVIEWED: rows read off the
        # bulletin's own schedule box, for kinds whose existing rows are all DiscoverMass (or a DiscoverMass-only parish).
        for r in REVIEWED.get(eid, []):
            existing = [x for x in cur if x["kind"] == r["kind"]]
            parish_dm = all(x["source"].startswith("secondary-discovermass") for x in cur)
            if parish_dm or (existing and all(x["source"].startswith("secondary-discovermass") for x in existing)):
                b_src = next((x for x in idx if x["entity_id"] == eid and x["date"] == r["date"]), None)
                new_rows.append({"entity_id": eid, "kind": r["kind"], "day": r["day"], "time": r["time"],
                                 "language": r.get("language"), "raw": r["raw"],
                                 "source": f"bulletin-{b_src['host']}-{r['date']}", "source_url": b_src["url"]})
        for k in comparison.values():
            k["inserted"] = False
        for r in new_rows:
            comparison.setdefault(f"bulletin:{r['kind']}", {})["inserted"] = True
        mass_rows += new_rows
        # ---- groups (WordPress ministry pages; bulletin groups go through bulletins/load.py)
        gnew = []
        for g in (wp_groups(eid) if not (off or parked) else []):
            raw = bg.clean_name(bg.fix_mojibake(g["name"]))
            r, why = bg.classify(raw)
            if r is None or bg.private_name_risk(raw):
                continue
            gid = f"{eid}:{r[0] if r[2] != 'knights_of_columbus' else 'kofc'}"
            name = display(raw, r)
            if gid in grp_rows or stem(gid) in have_gid or (eid, nkey(name)) in have_name:
                continue
            _, cat, sub, aud, lang, _, strength = r
            ent = ents.get(eid) or {}
            grp_rows[gid] = {"group_id": gid, "entity_id": eid, "name": name, "source_url": g["source_url"],
                             "scope": "parish", "category": cat, "subcategory": sub, "audience": aud,
                             "language": lang or "en", "language_assumed": not lang, "meets": None,
                             "location": ", ".join(x for x in [ent.get("name"), ent.get("city")] if x),
                             "contact_role": "parish office", "description": None,
                             "join_how": "Contact the parish office to ask when it meets.",
                             "source_kind": "website", "confidence": "low" if strength != "specific" else "medium",
                             "last_seen": g["modified"] or None, "fetched_at": next(
                                 (x["fetched_at"] for x in d["doors"] if "/wp/v2/pages" in x["url"]), now_iso()),
                             "flags": ["source:sidedoor-wpjson"], "raw_name": raw}
            gnew.append(gid)
        bull = sorted([x for x in idx if x["entity_id"] == eid and x.get("found_via", "").startswith("side door")],
                      key=lambda x: x["date"], reverse=True)
        report[eid] = {
            "name": (ents.get(eid) or {}).get("name"), "hosts": d.get("hosts"),
            "side_doors": {"tried": len(d["doors"]), "results": dict(res), "opened": opened,
                           "redirected_off_host": off, "parked_domain": parked,
                           "detail": [{k: x.get(k) for k in ("door", "url", "result", "status", "fetched_at")}
                                      for x in d["doors"]]},
            "wayback": {"latest_capture": latest and {k: latest[k] for k in ("capture_date", "archive_url", "raw_url")},
                        "captures_found": [{k: x.get(k) for k in ("url", "capture_date", "archive_url")}
                                           for x in w.get("latest", []) if x.get("capture_date")],
                        "history": [x["capture_date"] for x in w.get("history", [])],
                        "content_read": False},
            "bulletins": [{k: x[k] for k in ("date", "url", "host", "found_via")} for x in bull],
            "times": {"wpjson_rows": len(wp_rows), "bulletin_rows": len(b_rows),
                      "bulletin_used": b and {"date": b["date"], "url": b["source_url"]},
                      "comparison": comparison, "rows_added": len(new_rows)},
            "groups_added_from_website": gnew,
        }
    save_json(OUT, {"generated_at": now_iso(), "report": report})
    # SQL
    def q(v):
        return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
    if mass_rows:
        vals = ",\n".join("(" + ",".join(q(r[c]) for c in ("entity_id", "day", "time", "language", "kind", "raw",
                                                            "source", "source_url")) + ")" for r in mass_rows)
        Path("/tmp/recovery_mass.sql").write_text(f"""with ins as (
insert into mass_schedule (entity_id, day, time, language, kind, raw, source, source_url)
select v.* from (values
{vals}) as v(entity_id, day, time, language, kind, raw, source, source_url)
where not exists (select 1 from mass_schedule m where m.entity_id = v.entity_id and m.source = v.source
                  and m.kind = v.kind and m.day is not distinct from v.day and m.time = v.time)
returning entity_id, kind)
select count(*) rows_inserted, count(distinct entity_id) parishes from ins;
""")
    recs = list(grp_rows.values())
    if recs:
        cols = ",".join(COLS)
        vals = ",\n".join("(" + ",".join(lit(r[c], c, i == 0) for c in COLS) + ")" for i, r in enumerate(recs))
        Path("/tmp/recovery_groups.sql").write_text(f"""with v({cols}) as (values
{vals})
, ins as (
  insert into ministry ({cols}) select {cols} from v
  where not exists (select 1 from ministry x where x.entity_id = v.entity_id and x.group_id = v.group_id)
    and not exists (select 1 from ministry x where x.entity_id = v.entity_id
                    and regexp_replace(lower(x.name), '[^a-z]', '', 'g') = regexp_replace(lower(v.name), '[^a-z]', '', 'g'))
  returning entity_id)
select count(*) inserted, count(distinct entity_id) parishes from ins;
""")
    save_json(REAL / "recovery_groups_2026-10-31.json", {"count": len(recs), "records": recs})
    print(f"mass rows {len(mass_rows)} ({Counter(r['entity_id'] for r in mass_rows)}); website groups {len(recs)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["bulletins", "build"])
    ap.add_argument("--ids", default="")
    ap.add_argument("--per-parish", type=int, default=1)
    ap.add_argument("--have-json", default="")
    ap.add_argument("--groups-existing", default="")
    a = ap.parse_args()
    ids = [x for x in a.ids.split(",") if x] or list(doors())
    if a.step == "bulletins":
        return cmd_bulletins(ids, a.per_parish)
    return cmd_build(ids, a.have_json, a.groups_existing)


if __name__ == "__main__":
    raise SystemExit(main())
