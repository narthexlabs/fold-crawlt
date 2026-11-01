"""Build data/real/formation/offerings.json: per-parish "offerings" that make spiritual growth possible.

What it measures (never people, only what a parish offers):
  - Masses per week, by language and form (weekend vs weekday), plus monthly Masses
  - Confession: hours per week when it is scheduled, open-ended sessions, by-appointment flag
  - Eucharistic adoration: hours per week (overlaps counted once), monthly extra hours
  - Formation-type offerings mentioned on the parish website (Bible study, OCIA, youth, young adults, ...)
  - MAP 2022 baseline (weekend Masses and non-English Masses) for every parish, as a cross-check

Inputs
  data/real/parish_sites.json                      crawl output (37 of 70 sites OK on 2026-10-25/26)
  ingest/public/formation/schedules_curated.json   regular schedules transcribed from those pages, each
                                                   entry with source URL and a <=15-word evidence snippet
  data/raw/parish_sites/<id>/*.html                cached pages (gitignored). Optional: used to re-check every
                                                   evidence snippet and to scan for formation offerings.
                                                   Without the cache the script falls back to the committed
                                                   text in parish_sites.json and marks the checks accordingly.
  data/real/parishes_directory.json, data/real/map_2022_attendance.json

Usage
  python3 ingest/public/formation/build_offerings.py
  python3 ingest/public/formation/build_offerings.py --out /tmp/offerings.json
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "public"))
from common import canon_key  # noqa: E402
from privacy import RULES, screen  # noqa: E402

REAL = ROOT / "data" / "real"
CURATED = Path(__file__).resolve().parent / "schedules_curated.json"
OUT_DEFAULT = REAL / "formation" / "offerings.json"

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEK_MIN = 7 * 24 * 60
MONTH_TO_WEEK = 12 / 52  # a monthly event, expressed per average week
EXCLUDED_SEASONS = {"lent", "advent_lent", "advent", "seasonal", "summer"}
VIGIL_FROM = 14 * 60  # a Saturday Mass at/after 2 PM is counted as a weekend (vigil) Mass

# Same MAP 2022 -> directory matching as scripts/build_real_seed.py (kept in sync by hand).
MANUAL_MAP = {
    "queen all|concord": "queen-all-concord",
    "all|hayward": "all-hayward",
    "philip albert|alameda": "philip-neri-albert-great-alameda",
    "ignatius|antioch": "ignatius-antioch",
    "our lady grace|castro-valley": "our-lady-grace-castro-valley",
    "rose patrick|crockett": "rose-lima-patrick-crockett",
    "christ king|pleasant-hill": "christ-king-stephen-pleasant-hill",
    "pleasanton augustine|pleasanton": "pleasanton-augustine-elizabeth-seton-pleasanton",
}
MERGED_INTO = {"stephen|walnut-creek": "christ-king-stephen-pleasant-hill"}

LANG_NAMES = {
    "en": "English", "es": "Spanish", "vi": "Vietnamese", "zh": "Chinese (dialect not stated)",
    "yue": "Cantonese", "tl": "Tagalog/Filipino", "pt": "Portuguese", "pl": "Polish", "la": "Latin",
    "kjg": "Khmu (Kmhmu)", "sw": "Swahili", "und": "language not clear on site",
}

# ---------------------------------------------------------------- formation-type offerings (website mentions)
# Each category is a kind of opportunity to grow, never a measure of anyone's growth.
FORMATION_CATEGORIES = {
    "bible_study": r"\bbible (study|studies|class|reflections?|sharing)\b|\bscripture (study|class|sharing)\b|"
                   r"estudio b[ií]blico|c[ií]rculo b[ií]blico|lectio divina|little rock scripture",
    "ocia": r"\bO\.?C\.?I\.?A\b|\bR\.?C\.?I\.?A\b|christian initiation (of|for) adults|iniciaci[oó]n cristiana|becoming catholic",
    "adult_faith_formation": r"adult (faith )?formation|adult (religious )?education|formaci[oó]n (de |para )?adultos|"
                             r"adult confirmation|faith[- ]shar(e|ing)|small (faith |christian )?groups?|book club|"
                             r"\balpha (course|program|series)\b|that man is you|walking with purpose|theology on tap|"
                             r"school (for|of) pastoral ministry|serra catechetical",
    "youth_ministry": r"\byouth (group|ministry|ministries|nights?|core team)\b|life ?teen|\bteens? (ministry|group|nights?)\b|"
                      r"middle school ministry|high school (youth )?ministry|j[oó]venes para cristo|grupo juvenil|"
                      r"pastoral juvenil|youth group",
    "young_adults": r"young adults?|j[oó]venes adultos|adultos j[oó]venes",
    "mens_group": r"\bmen['’]?s (group|ministry|ministries|fellowship|faith share|faith sharing|bible study|club|retreat|cursillo)\b|"
                  r"grupo de hombres|that man is you",
    "womens_group": r"\bwomen['’]?s (group|ministry|ministries|fellowship|faith share|faith sharing|bible study|club|retreat|cursillo)\b|"
                    r"grupo de mujeres|walking with purpose",
    "prayer_groups": r"prayer (group|meeting|ministry|team)s?|grupo de oraci[oó]n|charismatic|carism[aá]tic|"
                     r"legion of mary|legi[oó]n de mar[ií]a|\bcenacle\b|cen[aá]culo|apostleship of prayer|"
                     r"rosary (group|makers|society|ministry)|group rosary|el.?shaddai|couples for christ|abba father",
    "retreats": r"\bretreats?\b|\bretiros?\b|cursillo|christ renews his parish|day of (renewal|recollection)|emma[uú]s retreat",
    "marriage_family": r"marriage (encounter|enrichment|ministry|preparation|prep)\b|pre-?cana|grupo matrimonial|"
                       r"movimiento familiar|couples for christ|natural family planning|retrouvaille|family (life )?ministry",
    "spiritual_direction": r"spiritual direct(ion|or)s?|direcci[oó]n espiritual|acompa[nñ]amiento espiritual",
    "disability_catechesis": r"\bSPRED\b|special religious ed|adaptive (catechesis|faith formation)",
    "children_faith_formation": r"faith formation|religious education|\bCCD\b|catechesis|catequesis|first (holy )?communion|"
                                r"primera comuni[oó]n|vacation bible school|children['’]?s liturgy|catechesis of the good shepherd",
    "confirmation_prep": r"\bconfirmation\b|confirmaci[oó]n",
    "catechists_recruited_or_trained": r"\bcatechists?\b|catequistas?",
    "grief_and_bereavement": r"griefshare|grief share|bereavement|grief support|\bduelo\b",
}
# Not counted as formation, but useful context for the parish working file.
OTHER_CATEGORIES = {
    "service_charity": r"st\.? vincent de paul|svdp|san vicente de pa[uú]l|food pantry|despensa",
    "fraternal": r"knights of columbus|caballeros de col[oó]n|columbiettes|young ladies['’]? institute|catholic daughters|"
                 r"italian catholic federation",
    "homebound_sick_ministry": r"homebound|ministers? (of|to) the sick|ministry to the sick|pastoral care (of|to) the sick",
}
_FC = {k: re.compile(v, re.I) for k, v in FORMATION_CATEGORIES.items()}
_FC["ocia"] = re.compile(FORMATION_CATEGORIES["ocia"])  # case-sensitive acronyms; phrases written in mixed case below
_OCIA_PHRASES = re.compile(r"christian initiation (of|for) adults|iniciaci[oó]n cristiana|becoming catholic", re.I)
_OC = {k: re.compile(v, re.I) for k, v in OTHER_CATEGORIES.items()}


# ---------------------------------------------------------------- helpers
def load(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def tmin(s: str | None) -> int | None:
    if s is None:
        return None
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def expand_days(spec: str) -> list[int]:
    spec = spec.strip()
    if spec.lower() == "daily":
        return list(range(7))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (DAYS.index(x.strip()) for x in part.split("-"))
            i = a
            while True:
                out.append(i)
                if i == b:
                    break
                i = (i + 1) % 7
        else:
            out.append(DAYS.index(part))
    return sorted(set(out))


def segments(day: int, start: int, end: int) -> list[tuple[int, int]]:
    """Week-timeline segments [a, b) in minutes from Sunday 00:00; overnight spans wrap."""
    a = day * 1440 + start
    b = day * 1440 + end if end > start else (day + 1) * 1440 + end
    if b <= WEEK_MIN:
        return [(a, b)]
    return [(a, WEEK_MIN), (0, b - WEEK_MIN)]


def merge(segs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(segs):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def length(segs) -> int:
    return sum(b - a for a, b in segs)


def minus(segs, cover) -> int:
    """Minutes of segs not covered by cover (both merged)."""
    total = 0
    for a, b in segs:
        cur = a
        for c, d in cover:
            if d <= cur or c >= b:
                continue
            if c > cur:
                total += c - cur
            cur = max(cur, d)
            if cur >= b:
                break
        if cur < b:
            total += b - cur
    return total


def norm_text(s: str) -> str:
    s = s.replace("​", "").replace("﻿", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().casefold()


def short(s: str, words: int = 12) -> str:
    w = s.split()
    return " ".join(w[:words]) + (" …" if len(w) > words else "")


def around(line: str, m: re.Match, words: int = 6) -> str:
    """At most ~13 words of the line, centred on the regex match, so the evidence shows why it matched."""
    before = line[:m.start()].split()[-words:]
    after = line[m.end():].split()[:words]
    head = "… " if len(line[:m.start()].split()) > words else ""
    tail = " …" if len(line[m.end():].split()) > words else ""
    return head + " ".join(before + [m.group(0)] + after) + tail


def r1(x: float) -> float:
    return round(x + 1e-9, 1)


# ---------------------------------------------------------------- cached page text
def page_lines(html: str) -> list[str]:
    from bs4 import BeautifulSoup  # imported lazily: only needed when the cache exists
    s = BeautifulSoup(html, "html.parser")
    for t in s(["script", "style", "noscript", "svg"]):
        t.decompose()
    for br in s.find_all("br"):
        br.replace_with("\n")
    txt = s.get_text("\n")
    return [re.sub(r"\s+", " ", ln).strip() for ln in txt.split("\n") if ln.strip()]


def cached_pages(rec: dict) -> dict[str, list[str]]:
    out = {}
    for p in rec.get("pages", []):
        f = Path(p.get("raw_file") or "")
        if not f.is_absolute():
            f = ROOT / f
        if not f.exists():  # raw_file paths are absolute on the crawling machine; try the repo-relative cache too
            alt = ROOT / "data" / "raw" / "parish_sites" / rec["id"] / f.name
            f = alt if alt.exists() else f
        if f.exists():
            out[p["url"]] = page_lines(f.read_text(encoding="utf-8", errors="replace"))
    return out


def url_key(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", u or "").rstrip("/").lower()


# ---------------------------------------------------------------- per-parish computations
def masses_block(cur: dict) -> dict:
    entries = cur.get("masses")
    if entries is None:
        return {"known": False}
    by_lang: Counter = Counter()
    by_lang_weekend: Counter = Counter()
    weekend = weekday = unstated_en = ef = school_year = 0
    sites = set()
    for e in entries:
        if e.get("season") in EXCLUDED_SEASONS:
            continue
        for d in expand_days(e["days"]):
            for t in e["times"]:
                lang = e.get("lang") or "und"
                by_lang[lang] += 1
                is_weekend = d == 0 or (d == 6 and tmin(t) >= VIGIL_FROM)
                if is_weekend:
                    weekend += 1
                    by_lang_weekend[lang] += 1
                else:
                    weekday += 1
                if lang == "en" and not e.get("lang_stated", True):
                    unstated_en += 1
                if e.get("form") == "extraordinary":
                    ef += 1
                if e.get("season") == "school_year":
                    school_year += 1
        if e.get("site"):
            sites.add(e["site"])
    monthly = [{k: m[k] for k in ("which", "time", "lang", "note") if m.get(k)} for m in cur.get("masses_monthly", [])]
    langs = sorted({l2 for l in by_lang for l2 in l.split("+")})
    return {
        "known": True,
        "per_week": weekend + weekday,
        "weekend": weekend,
        "weekday": weekday,
        "by_language": dict(sorted(by_lang.items(), key=lambda kv: -kv[1])),
        "weekend_by_language": dict(sorted(by_lang_weekend.items(), key=lambda kv: -kv[1])),
        "languages": langs,
        "non_english_per_week": sum(v for k, v in by_lang.items() if k not in ("en", "und") and not k.startswith("en+")),
        "bilingual_per_week": sum(v for k, v in by_lang.items() if "+" in k),
        "language_unstated_assumed_english": unstated_en,
        "extraordinary_form_per_week": ef,
        "school_year_only_per_week": school_year,
        "monthly": monthly,
        "monthly_per_month": len(monthly),
        "sites": sorted(sites),
    }


def sessions_block(entries: list[dict], median_session_h: float | None = None) -> dict:
    weekly_segs, monthly_items = [], []
    open_week = open_month = 0
    seasonal = school_year = False
    days_with = set()
    langs = set()
    for e in entries:
        season = e.get("season", "regular")
        if season in EXCLUDED_SEASONS:
            seasonal = True
            continue
        if season == "school_year":
            school_year = True
        if e.get("lang"):
            langs.update(e["lang"].split("+"))
        days = expand_days(e["days"])
        s, en = tmin(e.get("start")), tmin(e.get("end"))
        monthly = e.get("freq") == "monthly"
        if s is None or en is None:
            if monthly:
                open_month += 1
            else:
                open_week += len(days)
                days_with.update(days)
            continue
        segs = [sg for d in days for sg in segments(d, s, en)]
        if monthly:
            monthly_items.append(segs)
        else:
            weekly_segs += segs
            days_with.update(days)
    weekly = merge(weekly_segs)
    weekly_h = length(weekly) / 60
    monthly_extra_h = sum(minus(merge(m), weekly) for m in monthly_items) / 60
    out = {
        "hours_week_stated": r1(weekly_h),
        "days_per_week": len(days_with),
        "open_ended_sessions_week": open_week,
        "monthly_extra_hours": r1(monthly_extra_h),
        "monthly_open_ended_sessions": open_month,
        "hours_week_incl_monthly": r1(weekly_h + monthly_extra_h * MONTH_TO_WEEK),
        "seasonal_extra_not_counted": seasonal,
        "includes_school_year_only": school_year,
    }
    if median_session_h is not None:
        out["hours_week_estimated"] = r1(weekly_h + open_week * median_session_h)
    if langs:
        out["languages_stated"] = sorted(langs)
    return out


def scan_formation(pages: dict[str, list[str]], fallback_texts: list[tuple[str, str]]) -> dict:
    found: dict[str, list[dict]] = defaultdict(list)
    other: dict[str, list[dict]] = defaultdict(list)
    seen_lines = set()
    sources = [(u, ln) for u, L in pages.items() for ln in L] or fallback_texts
    for url, ln in sources:
        if len(ln) > 600 or ln in seen_lines:
            continue
        seen_lines.add(ln)
        for k, rx in _FC.items():
            hit = rx.search(ln) or (k == "ocia" and _OCIA_PHRASES.search(ln))
            if hit and len(found[k]) < 3:
                found[k].append({"text": around(ln, hit), "url": url})
        for k, rx in _OC.items():
            hit = rx.search(ln)
            if hit and len(other[k]) < 2:
                other[k].append({"text": around(ln, hit), "url": url})
    return {"formation": dict(found), "other": dict(other)}


def verify(cur: dict, pages: dict[str, list[str]], raw_fallback: str) -> dict:
    joined = {url_key(u): (norm_text(" ".join(L)), norm_text("".join(L))) for u, L in pages.items()}
    all_space = " ".join(v[0] for v in joined.values())
    all_nospace = " ".join(v[1] for v in joined.values())
    fb = norm_text(raw_fallback or "")
    total = ok = 0
    failed = []
    method = "cached_html" if pages else ("parish_sites_raw_text" if fb else "none")
    for sect in ("masses", "masses_monthly", "confession", "adoration"):
        for e in cur.get(sect) or []:
            ev = e.get("ev")
            if not ev:
                continue
            total += 1
            n = norm_text(ev)
            nn = n.replace(" ", "")
            hay = joined.get(url_key(e.get("src", "")))
            hit = False
            if hay:
                hit = n in hay[0] or nn in hay[1].replace(" ", "")
            if not hit and pages:
                hit = n in all_space or nn in all_nospace.replace(" ", "")
            if not hit and not pages and fb:
                hit = n in fb
            if hit:
                ok += 1
            else:
                failed.append({"section": sect, "ev": ev, "src": e.get("src")})
    return {"method": method, "entries": total, "verified": ok, "unverified": failed}


def confidence_ministries(rec: dict, n_pages: int) -> str:
    has_min_page = any(p["category"] == "ministries" for p in rec.get("pages", []))
    if has_min_page and n_pages >= 5:
        return "high"
    if has_min_page or n_pages >= 4:
        return "medium"
    return "low"


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    a = ap.parse_args()

    sites = load(REAL / "parish_sites.json")
    directory = load(REAL / "parishes_directory.json")
    mapatt = load(REAL / "map_2022_attendance.json")
    curated = load(CURATED)
    cur_by_id = curated["parishes"]
    dir_by_id = {r["id"]: r for r in directory["records"]}

    # MAP 2022 baseline per directory parish
    dir_key = {canon_key(r["name"], r["city"]): r["id"] for r in directory["records"]}
    map_by_id: dict[str, dict] = {}
    for row in mapatt["rows"]:
        k = row["canon_key"]
        pid = MANUAL_MAP.get(k) or dir_key.get(k) or MERGED_INTO.get(k)
        if not pid:
            continue
        m = map_by_id.setdefault(pid, {"weekend_masses": 0, "non_english_masses": 0, "rows": []})
        m["weekend_masses"] += row.get("masses") or 0
        m["non_english_masses"] += row.get("lang_masses") or 0
        m["rows"].append(row["parish_as_printed"])

    # global median of stated weekly confession session length (used only for the labelled estimate)
    durs = []
    for cur in cur_by_id.values():
        for e in cur.get("confession") or []:
            if e.get("freq") == "monthly" or e.get("season") in EXCLUDED_SEASONS:
                continue
            s, en = tmin(e.get("start")), tmin(e.get("end"))
            if s is not None and en is not None:
                durs.append(((en - s) % 1440) / 60)
    median_conf = statistics.median(durs) if durs else None

    records = []
    cache_hits = 0
    for rec in sites["records"]:
        if rec.get("crawl_status") != "ok":
            continue
        pid = rec["id"]
        cur = cur_by_id.get(pid)
        if cur is None:
            print(f"warning: {pid} has no curated schedule", file=sys.stderr)
            continue
        pages = cached_pages(rec)
        cache_hits += bool(pages)
        raw = (rec.get("mass_schedule") or {}).get("raw") or ""
        fallback = [(rec.get("website"), ln) for ln in raw.split("\n")] + \
                   [((rec.get("ministries") or {}).get("source_url"), it) for it in (rec.get("ministries") or {}).get("items", [])]
        scan = scan_formation(pages, fallback)
        n_pages = len(rec.get("pages", []))
        d = dir_by_id.get(pid, {})
        conf = dict(cur.get("confidence", {}))
        conf["ministries"] = confidence_ministries(rec, n_pages)
        confession = sessions_block(cur.get("confession") or [], median_conf)
        confession["by_appointment"] = cur.get("confession_by_appointment")
        confession["also_before_masses"] = bool(cur.get("confession_before_masses"))
        confession["known"] = conf.get("confession") not in ("none",) and bool(cur.get("confession"))
        adoration = sessions_block(cur.get("adoration") or [])
        adoration["perpetual_public_hours"] = adoration["hours_week_stated"] >= 167.9
        adoration["known"] = conf.get("adoration") not in ("none",)
        formation_found = sorted(scan["formation"])
        records.append({
            "id": pid,
            "name": rec.get("name"),
            "city": d.get("city"),
            "region": d.get("region"),
            "deanery": d.get("deanery"),
            "website": rec.get("website"),
            "crawled_at": rec.get("crawled_at"),
            "pages_crawled": n_pages,
            "masses": masses_block(cur),
            "confession": confession,
            "adoration": adoration,
            "devotions": cur.get("devotions", []),
            "formation_offerings": {
                "categories_found": formation_found,
                "breadth": len(formation_found),
                "breadth_out_of": len(FORMATION_CATEGORIES),
                "evidence": scan["formation"],
                "other_found": sorted(scan["other"]),
                "formed_link_on_site": "formed" in (rec.get("tech_signals") or {}),
                "ministry_items_crawled": len((rec.get("ministries") or {}).get("items") or []),
            },
            "map_2022": {k: v for k, v in map_by_id.get(pid, {}).items() if k != "rows"} or None,
            "flags": cur.get("flags", []),
            "notes": cur.get("notes", ""),
            "confidence": conf,
            "evidence_check": verify(cur, pages, raw),
        })

    # ---- diocesan roll-up over covered parishes (counts and medians only; no ranking)
    known_m = [r for r in records if r["masses"]["known"]]
    lang_tot: Counter = Counter()
    lang_parishes: Counter = Counter()
    for r in known_m:
        for k, v in r["masses"]["by_language"].items():
            lang_tot[k] += v
        for l in r["masses"]["languages"]:
            lang_parishes[l] += 1
    conf_known = [r for r in records if r["confession"]["known"]]
    ador_any = [r for r in records if r["adoration"]["hours_week_incl_monthly"] > 0
                or r["adoration"]["open_ended_sessions_week"] or r["adoration"]["monthly_open_ended_sessions"]]
    ador_weekly = [r for r in records if r["adoration"]["hours_week_stated"] > 0 or r["adoration"]["open_ended_sessions_week"]]
    ador_5plus = [r for r in records if r["adoration"]["days_per_week"] >= 5]
    weekday_conf = [r for r in conf_known if any(
        set(expand_days(e["days"])) - {6, 0} for e in (cur_by_id[r["id"]].get("confession") or [])
        if e.get("freq") != "monthly" and e.get("season") not in EXCLUDED_SEASONS)]
    cat_counts = Counter(c for r in records for c in r["formation_offerings"]["categories_found"])

    def med(xs):
        xs = [x for x in xs if x is not None]
        return r1(statistics.median(xs)) if xs else None

    total_dir = len(directory["records"])
    summary = {
        "coverage": {
            "parishes_in_directory": total_dir,
            "with_website": sum(1 for r in directory["records"] if r.get("website")),
            "crawled_ok": len(records),
            "with_known_mass_schedule": len(known_m),
            "with_known_confession_schedule": len(conf_known),
            "with_cached_html_for_checks": cache_hits,
            "with_map_2022_baseline": len(map_by_id),
        },
        "masses": {
            "per_week_total": sum(r["masses"]["per_week"] for r in known_m),
            "weekend_total": sum(r["masses"]["weekend"] for r in known_m),
            "per_week_by_language": dict(lang_tot.most_common()),
            "parishes_offering_language": dict(lang_parishes.most_common()),
            "parishes_with_any_non_english_mass": sum(1 for r in known_m if r["masses"]["non_english_per_week"] or r["masses"]["bilingual_per_week"]),
            "median_per_week": med([r["masses"]["per_week"] for r in known_m]),
        },
        "confession": {
            "parishes_with_scheduled_confession": len(conf_known),
            "median_hours_week_stated": med([r["confession"]["hours_week_stated"] for r in conf_known]),
            "total_hours_week_stated": r1(sum(r["confession"]["hours_week_stated"] for r in conf_known)),
            "parishes_with_weekday_confession": len(weekday_conf),
            "parishes_also_before_masses": sum(1 for r in records if r["confession"]["also_before_masses"]),
            "parishes_mentioning_by_appointment": sum(1 for r in records if r["confession"]["by_appointment"]),
            "median_stated_session_hours_used_for_estimates": r1(median_conf) if median_conf else None,
        },
        "adoration": {
            "parishes_with_any_scheduled_adoration": len(ador_any),
            "parishes_with_weekly_adoration": len(ador_weekly),
            "parishes_with_adoration_5plus_days_week": len(ador_5plus),
            "median_hours_week_incl_monthly_among_offering": med([r["adoration"]["hours_week_incl_monthly"] for r in ador_any]),
        },
        "formation_offerings": {
            "parishes_mentioning_category": dict(cat_counts.most_common()),
            "note": "Website mentions only; absence on a crawled page is not absence in the parish.",
        },
    }

    baseline = []
    for pid, d in sorted(dir_by_id.items()):
        m = map_by_id.get(pid)
        baseline.append({
            "id": pid, "name": d.get("name"), "city": d.get("city"),
            "weekend_masses_2022": m["weekend_masses"] if m else None,
            "non_english_or_cultural_masses_2022": m["non_english_masses"] if m else None,
            "map_rows": m["rows"] if m else [],
            "crawled_ok": pid in {r["id"] for r in records},
        })

    out = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "period": "Parish websites as crawled 2026-10-25/26 (regular weekly schedule); MAP baseline is 2022.",
        "sources": {
            "parish_sites": "data/real/parish_sites.json (crawl of each parish's own website)",
            "curated_schedules": "ingest/public/formation/schedules_curated.json",
            "map_2022": "data/real/map_2022_attendance.json (MAP Interim Report, Sept 2022, Appendix 2)",
            "directory": "data/real/parishes_directory.json",
        },
        "method": {
            "unit": "Offerings (what a parish makes available), never measures of any person's faith.",
            "masses": "Each listed regular Mass time x days per week. Weekend = Sunday plus Saturday from 2 PM (vigil). "
                      "Language as stated on the site; unlabeled Masses are counted as English and tallied in "
                      "language_unstated_assumed_english. Monthly Masses are listed separately and not in per_week.",
            "confession": "hours_week_stated = hours per week covered by scheduled sessions that give start and end times "
                          "(overlapping sessions counted once). Sessions with only a start time are counted in "
                          "open_ended_sessions_week; hours_week_estimated adds the median stated session length for "
                          "each of them. Advent/Lent extras are excluded. Monthly sessions add monthly_extra_hours, "
                          "folded into hours_week_incl_monthly at 12/52.",
            "adoration": "Same rules as confession. Overlapping hours are counted once; a monthly event counts only the hours "
                         "not already covered weekly.",
            "formation_offerings": "Regex scan of the visible text of every crawled page (menus included) for 16 kinds of "
                                   "formation offering; up to 3 evidence lines each. A website mention shows that something "
                                   "is offered or advertised; it says nothing about size, quality or anyone's growth.",
            "confidence": "high = explicit times on a dedicated schedule page, consistent across pages; medium = some "
                          "inference, pages disagree, or a source page is dated; low = key page not fetched or times "
                          "missing; none = no schedule found (unknown, not zero).",
            "evidence_check": "Every curated entry carries a <=15-word snippet; the script re-finds it in the cached page text "
                              "(or the committed crawl text when the cache is absent).",
        },
        "categories": {"formation": list(FORMATION_CATEGORIES), "other": list(OTHER_CATEGORIES)},
        "language_names": LANG_NAMES,
        "summary": summary,
        "records": records,
        "map_2022_baseline_all_parishes": baseline,
    }
    screen(out, RULES["offerings"])  # private names and personal contact details out of the evidence snippets
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    unv = sum(len(r["evidence_check"]["unverified"]) for r in records)
    tot = sum(r["evidence_check"]["entries"] for r in records)
    print(f"wrote {len(records)} parish records -> {a.out}")
    print(f"evidence snippets verified: {tot - unv}/{tot}; cached html for {cache_hits} parishes")


if __name__ == "__main__":
    main()
