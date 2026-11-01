#!/usr/bin/env python3
"""Score the parish-website schedule readers against truth and a referee, per site and per platform.

    python3 -I scripts/california/eval_templates.py                  # baseline (current generic reader) + templates
    python3 -I scripts/california/eval_templates.py --only ecatholic  # one platform
    python3 -I scripts/california/eval_templates.py --out /tmp/eval_templates.json --show-site shparish.net

Offline: reads cached pages only (data/raw/california/sites/<domain>/, data/raw/parish_sites/<id>/), never the network,
never Supabase (the Oakland truth rows are a committed snapshot, ingest/california/sites/templates/truth/).

Readers compared
  baseline   ingest/california/sites/parse_site.schedule_rows (the generic reader as of this run), rows flagged
             needs_review dropped, then scripts/california/merge.py sanity() as the build applies it.
  templates  ingest/california/sites/templates (platform detected per site), same sanity() pass.

Sets (reported separately)
  oakland    truth: the 44 Oakland parishes with hand-checked rows (mass_schedule source 'curated-2026-10-26'), scored on
             the parishes whose site pages are cached (data/raw/parish_sites/, crawl of 2026-10-25/26).
  review15   truth: the independent check's 15 parishes (seed 20261031, docs/review/ca-check-2026-10-31.md), Mass only,
             scored where a parish website is cached; language as the check compared it (none = English).
  referee    DiscoverMass rows of every California parish with a cached site (merged/<slug>.json, comments applied).
             A referee, not truth: disagreement can be DiscoverMass being stale. Slot agreement only.

A slot is (kind, weekday, start time), plus the language when both sides state one, as in the check: weekly rows are
counted over one ordinary week, monthly rows ("First Friday") once as (weekday, week, time); seasonal rows (summer, Lent,
Advent) are neutral. Kinds: Mass, confession, adoration; a Mass row that gives only a time range without the word Mass is
not a Mass (lib/schedule.ts kindOf). Holy-day and other rows are not scored.

Measures per site and per platform (pooled):
  precision      shown slots that are right / shown slots
  recall         true slots shown right / true slots
  sunday_recall  the same for Sunday Masses
  kind_err       confession / adoration slots shown under the wrong kind (a Mass shown as confession, adoration shown as
                 Mass, ...) / shown slots of those kinds at a true slot's day and time
  error_rate     (wrong + missing + mislabelled) / union, the check's Mass measure (review15 and oakland)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITES = ROOT / "ingest" / "california" / "sites"
sys.path.insert(0, str(SITES))
sys.path.insert(0, str(ROOT / "scripts" / "california"))

import parse_site as P  # noqa: E402
import templates as T  # noqa: E402
from templates import common as C  # noqa: E402

try:
    import merge as M  # noqa: E402  (read-only use of sanity())
    SANITY = M.sanity
except Exception as e:  # noqa: BLE001 - the harness still runs without it
    print(f"(merge.sanity unavailable: {e!r}; rows scored without it)", file=sys.stderr)
    SANITY = None

TODAY = "2026-10-31"
RAW = ROOT / "data" / "raw" / "california" / "sites"
REAL = ROOT / "data" / "real" / "california"
TRUTH = SITES / "templates" / "truth"
OAK_SITES = ROOT / "data" / "real" / "parish_sites.json"
CACHE = Path(os.environ.get("EVAL_CACHE", "/tmp/eval_templates_cache"))

# ------------------------------------------------------------------------------------------------ days (lib/find.ts readDay)
WD = {"sunday": 6, "sun": 6, "domingo": 6, "monday": 0, "mon": 0, "lunes": 0, "tuesday": 1, "tue": 1, "tues": 1, "martes": 1,
      "wednesday": 2, "wed": 2, "miercoles": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "jueves": 3, "friday": 4, "fri": 4,
      "viernes": 4, "saturday": 5, "sat": 5, "sabado": 5}
NTH = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
RANGE_TXT = re.compile(r"\d(?::\d\d)?\s*(?:a\.?m\.?|p\.?m\.?)?\s*[-–]\s*\d{1,2}(?::\d\d|\s*[ap]\.?\s?m\b)", re.I)


def read_day(day: str | None, raw: str = "", kind: str = "mass", time: str | None = None):
    """(days 0=Mon, nth list|None, seasonal) or None; "Daily" as lib/find.ts dailyDays()."""
    if not day:
        return None
    quals = re.findall(r"\(([^)]*)\)", day)
    seasonal = any(re.search(r"\b(lent|advent|easter|summer|winter|daylight|christmas)\b", q, re.I) for q in quals)
    s = re.sub(r"\s+", " ", C.fold(re.sub(r"[\u2013\u2014]", "-", re.sub(r"\([^)]*\)", " ", day)))).strip()
    if re.match(r"^(daily|every day)\b", s):
        if kind == "adoration":
            return list(range(7)), None, seasonal
        r = P.day_range(raw or "")
        if r:
            return [P.DAYN.index(x) for x in r[0]], None, seasonal
        sat = re.search(r"\bsat(?:urday)?s?\b|s[aá]bados?", raw or "", re.I)
        return (list(range(5)) if sat else list(range(6))), None, seasonal
    if re.match(r"^weekdays?\b", s):
        return list(range(5)), None, seasonal
    if re.match(r"^(saturday )?vigil\b", s):
        return [5], None, seasonal
    if re.match(r"^\d{1,2}(?:st|nd|rd|th) of (?:each|the) month", s) or "holy" in s:
        return None
    ordx = r"(?:first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)"
    m = re.match(rf"^({ordx}(?:(?:\s*,\s*|\s+(?:and|or|&)\s+|\s*&\s*){ordx})*) (\w+?)s? of (?:the|each|every) month", s)
    if m:
        d = WD.get(m.group(2))
        if d is None:
            return None
        nth = sorted({NTH[x] for x in re.split(r"\s*,\s*|\s+(?:and|or)\s+|\s*&\s*", m.group(1)) if x in NTH})
        return [d], nth, seasonal
    days = set()
    for part in re.split(r",| and ", s):
        p = part.strip()
        r = re.match(r"^(\w+)\.?\s*(?:–|—|-|through|to)\s*(\w+)", p)
        if r and WD.get(r.group(1)) is not None and WD.get(r.group(2)) is not None:
            a, b = WD[r.group(1)], WD[r.group(2)]
            i = a
            while True:
                days.add(i)
                if i == b:
                    break
                i = (i + 1) % 7
            continue
        w = re.match(r"^(\w+)", p)
        if w and WD.get(w.group(1)) is not None:
            days.add(WD[w.group(1)])
    return (sorted(days), None, seasonal) if days else None


def shown_kind(r: dict, curated: bool = False) -> str:
    k = r.get("kind") or "other"
    if k not in ("mass", "confession", "adoration"):
        return "other"
    if not curated and k == "mass" and RANGE_TXT.search(r.get("raw") or "") and not re.search(r"\b(mass|misa)\b", r.get("raw") or "", re.I):
        return "other"
    return k


LANG_CANON = {"espanol": "Spanish", "español": "Spanish", "spanish": "Spanish", "english": "English", "ingles": "English",
              "vietnamese": "Vietnamese", "tieng viet": "Vietnamese", "filipino": "Tagalog", "tagalog": "Tagalog",
              "portuguese": "Portuguese", "portugues": "Portuguese", "latin": "Latin", "korean": "Korean", "chinese": "Chinese",
              "mandarin": "Chinese", "cantonese": "Chinese"}


def canon_lang(x: str | None) -> str | None:
    if not x:
        return None
    f = C.fold(x).strip()
    if re.search(r"bilingual|/|&| and |biling", f):
        return "Bilingual"
    return LANG_CANON.get(f, x.strip().title())


def slots(rows: list[dict], curated: bool = False, kinds=("mass", "confession", "adoration")):
    """{('w', kind, dow, HH:MM) or ('m', kind, dow, nth, HH:MM): language} and the neutral (seasonal) keys."""
    out, neutral = {}, set()
    for r in rows:
        k = shown_kind(r, curated)
        if k not in kinds or not r.get("time") or not re.match(r"\d\d:\d\d", r["time"]):
            continue
        rule = read_day(r.get("day"), r.get("raw") or "", k, r.get("time"))
        if rule is None:
            continue
        days, nth, seasonal = rule
        t = r["time"][:5]
        for d in days:
            keys = [("m", k, d, n, t) for n in nth] if nth else [("w", k, d, t)]
            for key in keys:
                if seasonal:
                    neutral.add(key)
                    continue
                lang = canon_lang(r.get("language"))
                if key not in out or (out[key] is None and lang):
                    out[key] = lang
    return out, neutral


def lang_ok(a, b, review: bool) -> bool:
    if review:
        a, b = a or "English", b or "English"
    if not a or not b or "Bilingual" in (a, b):
        return True
    return a == b


def score(shown: dict, truth: dict, neutral: set, review: bool = False) -> dict:
    c = mis = wrong = miss = 0
    sun_t = sun_c = 0
    kconf_n = kconf_err = 0
    pos_truth = defaultdict(set)
    for key in truth:
        pos_truth[key[2:]].add(key[1])
    notes = []
    for key, lang in shown.items():
        if key in neutral:
            continue
        if key in truth:
            if lang_ok(lang, truth[key], review):
                c += 1
            else:
                mis += 1
                notes.append(f"LANG {fmt(key)} shown {lang} vs {truth[key]}")
        else:
            wrong += 1
            notes.append(f"WRONG {fmt(key)}{' ' + lang if lang else ''}")
        tk = pos_truth.get(key[2:], set())
        if tk and (key[1] in ("confession", "adoration") or tk & {"confession", "adoration"}):
            kconf_n += 1
            if key[1] not in tk:
                kconf_err += 1
    for key, lang in truth.items():
        if key[0] == "w" and key[1] == "mass" and key[2] == 6:
            sun_t += 1
            if key in shown and lang_ok(shown[key], lang, review):
                sun_c += 1
        if key not in shown and key not in neutral:
            miss += 1
            notes.append(f"MISSING {fmt(key)}{' ' + lang if lang else ''}")
    return {"correct": c, "mislabel": mis, "wrong": wrong, "missing": miss, "shown": c + mis + wrong, "truth": c + mis + miss,
            "sun_truth": sun_t, "sun_correct": sun_c, "kind_n": kconf_n, "kind_err": kconf_err, "notes": notes}


DAYS3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fmt(key) -> str:
    if key[0] == "w":
        return f"{key[1]} {DAYS3[key[2]]} {key[3]}"
    return f"{key[1]} {DAYS3[key[2]]}#{key[3]} {key[4]}"


def pooled(rs: list[dict]) -> dict:
    s = defaultdict(int)
    for r in rs:
        for k, v in r.items():
            if isinstance(v, int):
                s[k] += v
    p = s["correct"] / s["shown"] if s["shown"] else None
    rc = s["correct"] / s["truth"] if s["truth"] else None
    union = s["correct"] + s["mislabel"] + s["wrong"] + s["missing"]
    return {"n": len(rs), "shown": s["shown"], "truth": s["truth"], "precision": p, "recall": rc,
            "sunday_recall": s["sun_correct"] / s["sun_truth"] if s["sun_truth"] else None,
            "kind_err": s["kind_err"] / s["kind_n"] if s["kind_n"] else None, "kind_n": s["kind_n"],
            "error_rate": (s["wrong"] + s["missing"] + s["mislabel"]) / union if union else None, "union": union,
            "wrong": s["wrong"], "missing": s["missing"], "mislabel": s["mislabel"]}


# ------------------------------------------------------------------------------------------------ readers
def sanity(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("needs_review"):
            continue
        x = dict(r, source=f"crawled-{TODAY}")
        if SANITY is not None:
            x = SANITY(x)
            if x.get("needs_review"):
                continue
        out.append(x)
    return out


def baseline(pages, key, home, names) -> list[dict]:
    rows, _ = P.schedule_rows(pages, key, home, TODAY, names, {})
    return sanity(rows)


def templated(pages, names, template=None) -> tuple[list[dict], dict]:
    res = T.parse_schedule(pages, names, template)
    return sanity(res["rows"]), res


# ------------------------------------------------------------------------------------------------ sets
class OakPage(P.Page):
    pass


def oak_pages(rec: dict) -> list:
    pages = []
    for pg in rec.get("pages") or []:
        f = pg.get("raw_file") or ""
        fp = Path(f) if f.startswith("/") else ROOT / "data" / f
        if not fp.exists():
            continue
        r = {"kind": pg["category"], "url": pg["url"], "final_url": pg["url"], "fetched_at": rec.get("crawled_at")}
        pages.append(OakPage(r, fp.read_bytes()))
    # the same page fetched twice (a second category): read once
    seen, out = set(), []
    for p in pages:
        if p.url in seen:
            continue
        seen.add(p.url)
        out.append(p)
    return out


def site_inputs() -> dict:
    info = {}
    for f in (RAW / "_inputs").glob("*.json"):
        if f.name.endswith((".fetch.json", ".laptop.json")):
            continue
        for s in json.loads(f.read_text())["sites"]:
            info[s["key"]] = s
    return info


def site_pages(domain: str) -> list:
    d = RAW / domain.replace("/", "__")
    mp = d / "manifest.json"
    if not mp.exists():
        return []
    m = json.loads(mp.read_text())
    if m.get("status") != "ok":
        return []
    return P.load_pages(d, m)


def ca_units() -> list[dict]:
    """One unit per merged California parish with a readable cached site: its sites and DiscoverMass rows."""
    units = []
    for f in sorted((REAL / "merged").glob("*.json")):
        if f.stem in ("geocode_cache", "oakland"):
            continue
        data = json.loads(f.read_text())
        for r in data.get("records", []):
            sites = [s["domain"] for s in (r.get("match") or {}).get("sites", []) if s.get("crawl_status") == "ok"]
            if not sites:
                continue
            dm = [x for x in r.get("rows", []) if (x.get("source") or "").startswith("secondary-") and x.get("hidden") != "needs_review"]
            units.append({"id": r["id"], "diocese": f.stem, "sites": sites, "dm": dm, "name": r.get("name")})
    return units


def site_result(domain: str, info: dict, template: str | None = None):
    """(platform, cms_guess, baseline rows, template rows, template meta) for one site, cached on disk by page mtimes."""
    pages = site_pages(domain)
    if not pages:
        return None
    names = (info.get(domain) or {}).get("names")
    home = (info.get(domain) or {}).get("home") or pages[0].url
    plat = T.platform_of(pages)
    cms = P.cms_guess(pages, domain)["cms"]
    stamp = f"{int((SITES / 'parse_site.py').stat().st_mtime)}-{int((RAW / domain.replace('/', '__') / 'manifest.json').stat().st_mtime)}"
    cf = CACHE / f"{domain.replace('/', '__')}.{stamp}.json"
    if cf.exists():
        b = json.loads(cf.read_text())
    else:
        b = baseline(pages, domain, home, names)
        CACHE.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(b))
    t, res = templated(pages, names, template)
    return {"domain": domain, "platform": plat, "cms_guess": cms, "baseline": b, "templates": t,
            "template": res["template"], "parse_quality": res["parse_quality"], "dropped": res["dropped"]}


def _work(args):
    domain, info, template = args
    try:
        return domain, site_result(domain, info, template)
    except Exception as e:  # noqa: BLE001
        return domain, {"error": repr(e)[:300]}


def run(only: str | None, workers: int, template: str | None, limit: int | None, want=None, sets=None, review_ids=()):
    info = site_inputs()
    units = ca_units()
    if want:
        units = [u for u in units if u["id"] in want or set(u["sites"]) & want]
    if sets is not None and "referee" not in sets:
        units = [u for u in units if u["id"] in review_ids] if "review15" in sets else []
    domains = sorted({d for u in units for d in u["sites"]})
    if limit:
        domains = domains[:limit]
    args = [(d, {d: info.get(d)}, template) for d in domains]
    results = {}
    if workers > 1:
        with Pool(workers) as pool:
            for d, r in pool.imap_unordered(_work, args, chunksize=4):
                results[d] = r
    else:
        for a in args:
            d, r = _work(a)
            results[d] = r
    return units, results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="report one platform (template name)")
    ap.add_argument("--template", help="force one template for every site (e.g. generic_strict)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default="/tmp/eval_templates.json")
    ap.add_argument("--show-site", action="append", default=[], help="print rows and notes for a domain or unit id")
    ap.add_argument("--no-oakland", action="store_true")
    ap.add_argument("--ids", help="comma-separated unit ids or domains to score (default: all)")
    ap.add_argument("--set", choices=["oakland", "review15", "referee"], action="append", help="score only these sets")
    ap.add_argument("--write-grades", action="store_true",
                    help="write ingest/california/sites/templates/grades.json (per-template scores and passed/failed against templates.BAR)")
    a = ap.parse_args()
    want = {x for x in (a.ids or "").split(",") if x}
    sets = set(a.set or ["oakland", "review15", "referee"])
    if want:
        a.show_site = a.show_site or sorted(want)

    # ---- Oakland truth
    oak_units = []
    if not a.no_oakland and "oakland" in sets:
        cur = json.loads((TRUTH / "oakland-curated-2026-10-26.json").read_text())["rows"]
        by = defaultdict(list)
        for x in cur:
            by[x["entity_id"]].append(x)
        recs = {r["id"]: r for r in json.loads(OAK_SITES.read_text())["records"]}
        for eid, rows in sorted(by.items()):
            if want and eid not in want:
                continue
            rec = recs.get(eid)
            if not rec:
                continue
            pages = oak_pages(rec)
            if not pages:
                continue
            names = [rec.get("name")] if rec.get("name") else None
            b = baseline(pages, eid, rec.get("website") or pages[0].url, names)
            t, res = templated(pages, names, a.template)
            oak_units.append({"id": eid, "platform": T.platform_of(pages), "cms_guess": (rec.get("cms_guess") or {}).get("cms"),
                              "truth": rows, "baseline": b, "templates": t, "template": res["template"],
                              "parse_quality": res["parse_quality"]})

    review = json.loads((TRUTH / "review15-seed20261031.json").read_text())["parishes"]
    units, results = run(a.only, a.workers, a.template, a.limit, want, sets, set(review))

    def rows_of(u, which):
        out = []
        for d in u["sites"]:
            r = results.get(d)
            if r and "error" not in r:
                out += T.rows_for_church(r[which], u.get("name")) if which == "templates" else r[which]
        return out

    def plat_of(u):
        ps = [results[d]["platform"] for d in u["sites"] if results.get(d) and "error" not in results[d]]
        return ps[0] if ps else None

    report = {"sets": {}, "per_site": {}}
    for which in ("baseline", "templates"):
        # ---- referee (DiscoverMass), all kinds
        per = defaultdict(list)
        for u in units:
            if not u["dm"] or plat_of(u) is None:
                continue
            if not any(shown_kind(x) == "mass" for x in u["dm"]):
                continue
            sh, nsh = slots(rows_of(u, which))
            tr, ntr = slots(u["dm"])
            sc = score(sh, tr, nsh | ntr)
            sc["id"] = u["id"]
            per[plat_of(u)].append(sc)
            report["per_site"].setdefault(u["id"], {})[f"referee:{which}"] = {k: v for k, v in sc.items()}
        report["sets"].setdefault("referee", {})[which] = {p: pooled(v) for p, v in per.items()}
        report["sets"]["referee"][which]["ALL"] = pooled([x for v in per.values() for x in v])
        # ---- review15 (Mass only, the check's language rule)
        per = defaultdict(list)
        ids = {u["id"]: u for u in units}
        for pid, tr in review.items():
            u = ids.get(pid)
            if not u or plat_of(u) is None:
                continue
            rows = rows_of(u, which)
            if not rows:
                pass
            sh, nsh = slots(rows, kinds=("mass",))
            truth = {("w", "mass", (d + 6) % 7, t): l for d, t, l in tr["w"]}
            truth.update({("m", "mass", (d + 6) % 7, n, t): None for d, n, t in tr["m"]})
            neutral = {("m", "mass", (d + 6) % 7, n, t) for d, n, t in tr.get("neutral_m", [])}
            sc = score(sh, truth, nsh | neutral, review=True)
            sc["id"] = pid
            per[plat_of(u)].append(sc)
            report["per_site"].setdefault(pid, {})[f"review15:{which}"] = sc
        report["sets"].setdefault("review15", {})[which] = {p: pooled(v) for p, v in per.items()}
        report["sets"]["review15"][which]["ALL"] = pooled([x for v in per.values() for x in v])
        # ---- Oakland truth
        per = defaultdict(list)
        for u in oak_units:
            sh, nsh = slots(u[which])
            tr, ntr = slots(u["truth"], curated=True)
            sc = score(sh, tr, nsh | ntr)
            sc["id"] = u["id"]
            per[u["platform"]].append(sc)
            report["per_site"].setdefault(u["id"], {})[f"oakland:{which}"] = sc
        report["sets"].setdefault("oakland", {})[which] = {p: pooled(v) for p, v in per.items()}
        report["sets"]["oakland"][which]["ALL"] = pooled([x for v in per.values() for x in v])

    # ---- tables
    def pct(x):
        return "  -  " if x is None else f"{100 * x:5.1f}"

    for setname in ("oakland", "review15", "referee"):
        S = report["sets"].get(setname) or {}
        plats = sorted(set(S.get("baseline", {})) | set(S.get("templates", {})), key=lambda p: (p == "ALL", p))
        print(f"\n== {setname}: baseline -> templates   (n units; slot precision / recall / Sunday recall / kind error; "
              f"{'error rate' if setname != 'referee' else 'disagreement'})")
        print(f"{'platform':16} {'n':>4} | {'prec':>5} {'rec':>5} {'sun':>5} {'kind':>5} {'err':>5} | {'prec':>5} {'rec':>5} {'sun':>5} {'kind':>5} {'err':>5}")
        for p in plats:
            if a.only and p not in (a.only, "ALL"):
                continue
            b, t = S.get("baseline", {}).get(p) or {}, S.get("templates", {}).get(p) or {}
            print(f"{p:16} {t.get('n', b.get('n', 0)):>4} | {pct(b.get('precision'))} {pct(b.get('recall'))} {pct(b.get('sunday_recall'))} "
                  f"{pct(b.get('kind_err'))} {pct(b.get('error_rate'))} | {pct(t.get('precision'))} {pct(t.get('recall'))} "
                  f"{pct(t.get('sunday_recall'))} {pct(t.get('kind_err'))} {pct(t.get('error_rate'))}")
    # confidence calibration on the referee
    conf = defaultdict(lambda: [0, 0])
    for u in units:
        if not u["dm"] or plat_of(u) is None:
            continue
        tr, _ = slots(u["dm"])
        for r in rows_of(u, "templates"):
            s1, _ = slots([r])
            for key in s1:
                conf[r.get("confidence")][1] += 1
                conf[r.get("confidence")][0] += key in tr
    print("\nreferee agreement by row confidence:", {k: f"{v[0]}/{v[1]} = {100 * v[0] / v[1]:.0f}%" for k, v in conf.items() if v[1]})
    q = defaultdict(int)
    for d, r in results.items():
        if r and "error" not in r:
            q[(r["template"], r["parse_quality"]["score"])] += 1
    print("parse_quality by template:", dict(sorted(q.items())))
    errs = [d for d, r in results.items() if r and "error" in r]
    if errs:
        print(f"{len(errs)} sites raised errors, e.g. {errs[:3]}: {results[errs[0]]['error']}")
    for s in a.show_site:
        for u in units + oak_units:
            if u["id"] == s or s in u.get("sites", []):
                print(f"\n--- {u['id']} ({plat_of(u) if 'sites' in u else u['platform']})")
                for which in ("baseline", "templates"):
                    rows = rows_of(u, which) if "sites" in u else u[which]
                    print(f"  [{which}] {len(rows)} rows")
                    for r in rows:
                        print(f"    {r['kind']:10} {r.get('day')!s:32} {r.get('time')!s:12} {r.get('language')!s:10} "
                              f"{r.get('confidence', '')!s:6} {(r.get('raw') or '')[:70]}")
                for k, v in report["per_site"].get(u["id"], {}).items():
                    print(f"  {k}: correct {v['correct']} wrong {v['wrong']} missing {v['missing']} lang {v['mislabel']}  {'; '.join(v['notes'][:30])}")
    report["sites"] = {d: {k: v for k, v in (r or {}).items() if k not in ("baseline", "templates")} for d, r in results.items()}
    Path(a.out).write_text(json.dumps(report, indent=1, default=str))
    if a.write_grades:
        if a.only or a.ids or a.limit or a.set:
            print("not writing grades from a partial run (--only/--ids/--limit/--set)")
        else:
            S = report["sets"]
            keep = ("n", "precision", "recall", "sunday_recall", "error_rate")
            grades = {"measured_at": TODAY, "measured_by": "scripts/california/eval_templates.py --write-grades", "bar": T.BAR, "templates": {},
                      "all": {k: {x: (S.get(k, {}).get("templates", {}).get("ALL") or {}).get(x) for x in keep} for k in ("oakland", "review15", "referee")}}
            for name in T.BY_NAME:
                tr = {x: (S.get(T.BAR["truth_set"], {}).get("templates", {}).get(name) or {}).get(x) for x in keep}
                rf = {x: (S.get("referee", {}).get("templates", {}).get(name) or {}).get(x) for x in keep}
                grades["templates"][name] = {T.BAR["truth_set"]: tr if tr.get("n") else None, "referee": rf if rf.get("n") else None,
                                             "passed": T.passes(tr, rf)}
            T.GRADES_FILE.write_text(json.dumps(grades, indent=1) + "\n")
            print("grades:", {k: v["passed"] for k, v in grades["templates"].items()}, "->", T.GRADES_FILE)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
