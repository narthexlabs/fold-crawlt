"""Per-platform schedule templates for parish websites (Mass, confession, adoration). See README.md here.

    from templates import parse_schedule
    res = parse_schedule(pages, names)      # pages: parse_site.Page objects of one site; names: the parish's names
    res["rows"], res["template"], res["platform"], res["parse_quality"], res["dropped"]

Platform order: eCatholic (a fixed Catholic builder with a structured schedule widget), Wix, Squarespace, Weebly,
GoDaddy, WordPress, ParishSoft (served by ParishSoft itself; none in the cache), then generic_strict. Giving links
(giving.parishsoft.com) never decide the platform.
"""
from __future__ import annotations

import re

from . import common, ecatholic, generic_strict, godaddy, parishsoft, squarespace, weebly, wix, wordpress

TEMPLATES = [ecatholic, wix, squarespace, weebly, godaddy, wordpress, parishsoft]
BY_NAME = {t.NAME: t for t in TEMPLATES + [generic_strict]}
# ------------------------------------------------------------------------------------------------ the bar
# A template is trusted (its parses may outrank DiscoverMass) when, in scripts/california/eval_templates.py's last run:
#   - on the hand-checked Oakland truth set, over at least BAR["min_units"] parishes: slot precision >= 90% and Sunday
#     Mass recall >= 85% (the share of true Sunday Masses it shows correctly);
#   - on the DiscoverMass referee (every California parish with a cached site): slot precision >= 75%, i.e. at parity
#     with DiscoverMass, which itself scores 78% against the Oakland hand-checked rows (a perfect parser cannot beat it).
# A site's own parse is then trusted only when its parse_quality is "high" and it agrees with DiscoverMass on Sunday
# where DiscoverMass lists Sunday Masses (trusted() below; the Sunday check runs in scripts/california/merge.py, which
# holds the DiscoverMass rows). The grades live in grades.json next to this file, written by
# `python3 -I scripts/california/eval_templates.py --write-grades`; PASSED is read from it.
BAR = {"truth_set": "oakland", "min_units": 10, "precision": 0.90, "sunday_recall": 0.85, "referee_precision": 0.75}
GRADES_FILE = __import__("pathlib").Path(__file__).resolve().parent / "grades.json"


# A template with fewer than BAR["min_units"] Oakland truth parishes (eCatholic: none) may pass on a LIVE truth set instead:
# sites drawn at random from the national crawl, read by a person off the live page, with a held-out part the parser fixes
# never saw (docs/review/ecatholic-check-2026-11-01.md). Same thresholds: >= 10 parishes with weekend Masses, weekend slot
# precision >= 90% on the held-out sites AND on all graded sites, Sunday recall >= 85% (held-out), referee >= 75%.
# Per kind (Matthew 2026-11-01 19:02: the ledger tracks weekend Mass, weekday Mass, confession and adoration separately), a
# template's rows of one kind are trusted only when that kind passes on its own: >= 10 parishes with rows of that kind on
# the page (dev + held-out pooled), pooled slot precision >= 90% and recall >= 85%, and held-out precision >= 90%.
# grades.json `kinds`; trusted_kinds() below. A template graded only on the Oakland set (no `kinds`) is trusted for every
# kind its Oakland grade covered (Mass, confession and adoration slots pooled), as before.
KINDS = ("weekend", "weekday", "confession", "adoration")
KIND_BAR = {"min_units": 10, "precision": 0.90, "recall": 0.85, "heldout_precision": 0.90}


def passes_live(live: dict | None, referee: dict | None) -> bool:
    """A live truth grade ({n, heldout: {precision, sunday_recall}, all: {precision}}) against BAR."""
    lv, r = live or {}, referee or {}
    ho, al = lv.get("heldout") or {}, lv.get("all") or {}
    return bool(lv.get("n", 0) >= BAR["min_units"] and (ho.get("precision") or 0) >= BAR["precision"]
                and (al.get("precision") or 0) >= BAR["precision"] and (ho.get("sunday_recall") or 0) >= BAR["sunday_recall"]
                and (r.get("precision") or 0) >= BAR["referee_precision"])


def kind_passes(k: dict | None) -> bool:
    k = k or {}
    return bool(k.get("n", 0) >= KIND_BAR["min_units"] and (k.get("precision") or 0) >= KIND_BAR["precision"]
                and (k.get("recall") or 0) >= KIND_BAR["recall"] and (k.get("heldout_precision") or 0) >= KIND_BAR["heldout_precision"])


def passes(truth: dict | None, referee: dict | None) -> bool:
    """One template's pooled scores ({n, precision, sunday_recall}) against BAR."""
    t, r = truth or {}, referee or {}
    return bool(t.get("n", 0) >= BAR["min_units"] and (t.get("precision") or 0) >= BAR["precision"]
                and (t.get("sunday_recall") or 0) >= BAR["sunday_recall"] and (r.get("precision") or 0) >= BAR["referee_precision"])


def _load_passed() -> set[str]:
    try:
        g = __import__("json").loads(GRADES_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return set()
    return {name for name, v in (g.get("templates") or {}).items() if v.get("passed")}


PASSED: set[str] = _load_passed()


def _load_kinds() -> dict[str, set[str]]:
    try:
        g = __import__("json").loads(GRADES_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, v in (g.get("templates") or {}).items():
        if not v.get("passed"):
            continue
        ks = v.get("kinds")
        out[name] = set(KINDS) if not ks else {k for k in KINDS if (ks.get(k) or {}).get("trusted")}
    return out


TRUSTED_KINDS: dict[str, set[str]] = _load_kinds()


def trusted_kinds(template: str) -> list[str]:
    """The kinds (weekend / weekday / confession / adoration) whose rows a passed template is trusted for."""
    return [k for k in KINDS if k in TRUSTED_KINDS.get(template, set())]


def platform_of(pages) -> str:
    home = next((p for p in pages if getattr(p, "kind", "") == "home"), pages[0] if pages else None)
    if home is None:
        return generic_strict.NAME
    html = home.text[:1_500_000]
    for t in TEMPLATES:
        if t.detect(html, home.url):
            return t.NAME
    return generic_strict.NAME


def parse_schedule(pages, names=None, template: str | None = None) -> dict:
    name = template or platform_of(pages)
    mod = BY_NAME[name]
    rows, dropped = mod.parse_full(pages, names)
    q = quality(rows)
    out = common.public(rows)
    for r in out:
        r.setdefault("template", name)
    return {"rows": out, "template": name, "platform": name, "dropped": dropped, "parse_quality": q}


# ------------------------------------------------------------------------------------------------ parse quality
def _min(t: str) -> int:
    m = re.match(r"(\d\d):(\d\d)", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else -1


def quality(rows: list[dict], dm_rows: list[dict] | None = None) -> dict:
    """Internal sanity of one site's parse, plus agreement with DiscoverMass where its rows are given:
    has a Sunday (or vigil) Mass; no impossible hours; plausible counts per day; no day that copies another's times
    exactly with 4+ Masses (a list read under the wrong heading); most rows high confidence.
    score: high / medium / low."""
    mass = [r for r in rows if r["kind"] == "mass" and r["day"] in common.DAYN]
    by_day: dict[str, set[int]] = {}
    for r in mass:
        by_day.setdefault(r["day"], set()).add(_min(r["time"]))
    checks = {}
    checks["has_sunday"] = bool(by_day.get("Sunday")) or any(t >= 14 * 60 for t in by_day.get("Saturday", ()))
    checks["hours_ok"] = all(5 * 60 <= _min(r["time"]) <= 21 * 60 + 30 for r in mass)
    checks["counts_ok"] = len(by_day.get("Sunday", ())) <= 12 and all(len(v) <= 6 for d, v in by_day.items() if d != "Sunday") \
        and len(rows) <= 90
    sun = by_day.get("Sunday", set())
    checks["sunday_not_copied"] = not (len(sun) >= 4 and any(v == sun for d, v in by_day.items() if d not in ("Sunday",)))
    weekday_same = [frozenset(by_day.get(d, ())) for d in common.DAYN[:5]]
    checks["no_daily_on_sunday"] = not (sun and all(w and w <= sun for w in weekday_same) and len(sun) <= 2 and len(by_day) >= 6)
    conf = [r.get("confidence") for r in rows if r["kind"] in ("mass", "confession", "adoration")]
    checks["mostly_high"] = bool(conf) and sum(c == "high" for c in conf) >= 0.6 * len(conf)
    out = {"checks": checks}
    ok = all(v for k, v in checks.items())
    if dm_rows is not None:
        agree = dm_agreement(rows, dm_rows)
        out["discovermass"] = agree
        if agree.get("sunday") is False:
            ok = False
    out["score"] = "high" if ok else "medium" if checks["has_sunday"] and checks["hours_ok"] and checks["counts_ok"] else "low"
    return out


def sunday_times(rows: list[dict]) -> set[str]:
    return {r["time"][:5] for r in rows if r.get("kind") == "mass" and (r.get("day") or "").strip() in ("Sunday",)
            and not r.get("needs_review") and not r.get("hidden")}


def dm_agreement(rows: list[dict], dm_rows: list[dict]) -> dict:
    """Sunday Mass start times compared with DiscoverMass's (None when DiscoverMass lists no Sunday Mass)."""
    a, b = sunday_times(rows), sunday_times(dm_rows)
    if not b:
        return {"sunday": None}
    inter = len(a & b)
    jac = inter / len(a | b) if a | b else 1.0
    return {"sunday": a == b or (len(b) >= 5 and jac >= 0.8), "sunday_jaccard": round(jac, 3), "site_sunday": sorted(a),
            "discovermass_sunday": sorted(b)}


def rows_for_church(rows: list[dict], church_name: str | None) -> list[dict]:
    """A multi-church site's rows for one of its churches: rows tagged "[<church>]" for another church are left out."""
    if not church_name:
        return rows
    mine = common.name_words(church_name)
    out = []
    for r in rows:
        m = re.match(r"^\[([^\]]+)\]", r.get("raw") or "")
        if m and mine and common.PLACE_RX.search(m.group(1)) and not (common.name_words(m.group(1)) & mine):
            continue
        out.append(r)
    return out


def trusted(template: str, parse_quality: dict) -> bool:
    """Trusted: the template passed the bar, the site's parse_quality is high, and it agrees with DiscoverMass on Sunday
    where DiscoverMass lists Sunday Masses."""
    if template not in PASSED or (parse_quality or {}).get("score") != "high":
        return False
    return (parse_quality.get("discovermass") or {}).get("sunday") is not False
