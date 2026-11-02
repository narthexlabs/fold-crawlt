"""eCatholic: read the builder's own Mass / Confession / Adoration Times widgets structurally.

eCatholic pages are a list of modules (`ul.region > li[id^=module_]`). The schedule widgets are
`div.sacramentTimesModule` (`massTimesModule`, `confessionTimesModule`, `adorationTimesModule`); each holds
`ul.sacramentTimes.{massTime|confessionTime|adorationTime}` with one `li.sacramentDay` per day:

    <span class="day SATURDAY_VIGIL">Saturday</span>            day classes: SUNDAY ... SATURDAY, SATURDAY_VIGIL,
    <span class="sacramentTime">                                 FIRST_FRIDAY, FIRST_SATURDAY, HOLY_DAY, HOLY_DAY_VIGIL,
      <span class="startTime">5:00 <span class="timePeriod">PM</span></span>     MULTIDAY ("m tu w th f" classes, or
      <span class="endTime">...</span>                                            multiMonday addDash + multiFriday)
      <span class="language SPANISH">Spanish</span>
      <span class="notes">Mass</span>

A widget may sit under a category ("Sunday Mass Schedule", "Upcoming Holy Days"). Notes can name another church of a
multi-church parish ("- St. Boniface, Nicolaus"), a monthly rule ("Every Third Saturday ONLY", "Swahili Mass - First
Sunday") or a non-Mass service ("Communion Service", "Novena"); those are applied. Free-text modules (customModule)
are read with generic_strict for kinds the widgets do not give.
"""
from __future__ import annotations

import re

from . import common as C
from . import generic_strict

NAME = "ecatholic"
# the builder's own markup (a link to ecatholic.com, e.g. a bulletin or giving page, does not make a site eCatholic)
DETECT = re.compile(r"sacramentTimesModule|class=\"moduleInner|<li id=\"module_\d+|powered by\s*(?:<[^>]+>\s*)*ecatholic|ecatholiccdn\.com", re.I)
DAYCLS = {"MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3, "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6, "SATURDAY_VIGIL": 5}
MULTI_CLS = {"m": 0, "tu": 1, "w": 2, "th": 3, "f": 4, "sa": 5, "su": 6}
MULTI_SPAN = {"multiMonday": 0, "multiTuesday": 1, "multiWednesday": 2, "multiThursday": 3, "multiFriday": 4, "multiSaturday": 5,
              "multiSunday": 6}
NTH_CLS = re.compile(r"^(FIRST|SECOND|THIRD|FOURTH|FIFTH|LAST)_(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)$")
NTH_N = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "LAST": -1}
LANG_CLS = {"ENGLISH": "English", "SPANISH": "Spanish", "VIETNAMESE": "Vietnamese", "LATIN": "Latin", "TAGALOG": "Tagalog",
            "FILIPINO": "Tagalog", "KOREAN": "Korean", "PORTUGUESE": "Portuguese", "CHINESE": "Chinese", "MANDARIN": "Chinese",
            "CANTONESE": "Chinese", "POLISH": "Polish", "ITALIAN": "Italian", "FRENCH": "French", "BILINGUAL": "Bilingual",
            "SWAHILI": "Swahili", "IGBO": "Igbo", "TONGAN": "Tongan", "SAMOAN": "Samoan", "ARABIC": "Arabic", "GERMAN": "German",
            "AMERICAN_SIGN_LANGUAGE": "American Sign Language", "ASL": "American Sign Language", "HMONG": "Hmong"}
DEDIC_RX = re.compile(r"\b(?:st\.?|saint|sts\.?|san|santa|santo|our lady|nuestra se[nñ]ora|holy|sacred|blessed|immaculate|"
                      r"cathedral|basilica|shrine|mission)\b", re.I)
NOT_NOTE = re.compile(r"communion service|liturgy of the word|novena|rosary|rosario|chaplet|stations|morning prayer|"
                      r"evening prayer|choir rehearsal|no mass|cancel", re.I)


# a WordPress site is not eCatholic even when its theme reuses a "moduleInner" class (stgall.org, htparish.org moved to
# WordPress and kept being read as eCatholic, 2026-11-01); eCatholic's own widget or CDN still wins
WORDPRESS = re.compile(r"/wp-content/|/wp-includes/|<meta[^>]+name=[\"']generator[\"'][^>]+content=[\"']WordPress|/wp-json/", re.I)
OWN_MARKUP = re.compile(r"sacramentTimesModule|ecatholiccdn\.com|powered by\s*(?:<[^>]+>\s*)*ecatholic", re.I)


def detect(html: str, url: str) -> bool:
    h = html or ""
    if not DETECT.search(h):
        return False
    return not WORDPRESS.search(h) or bool(OWN_MARKUP.search(h))


def _hhmm(span) -> str | None:
    if span is None:
        return None
    txt = C.norm_space(span.get_text(" "))
    ts = C.times_in(txt)
    return ts[0].hhmm if ts and ts[0].hh is not None else None


def _days_of(dspan) -> tuple[list[int], list[int] | None, str | None]:
    cls = [c for c in (dspan.get("class") or []) if c not in ("day", "massModeSacramentDay", "multiShort", "multiAbbr")]
    for c in cls:
        if c in DAYCLS:
            return [DAYCLS[c]], None, None
        m = NTH_CLS.match(c)
        if m:
            return [DAYCLS[m.group(2)]], [NTH_N[m.group(1)]], None
        if c in ("HOLY_DAY", "HOLY_DAY_VIGIL"):
            return [], None, "holy"
    if "DAILY" in cls and "MULTIDAY" not in cls:
        ds = [d for d in C.days_in(C.norm_space(dspan.get_text(" ")).rstrip(" :")) if d.days]
        if ds:
            return sorted({x for d in ds for x in d.days}), None, None
    if "MULTIDAY" in cls:
        listed = [MULTI_CLS[c] for c in cls if c in MULTI_CLS]
        if listed:
            return sorted(set(listed)), None, None
        spans = [(MULTI_SPAN.get(next((c for c in (s.get("class") or []) if c in MULTI_SPAN), ""), None), s.get("class") or [])
                 for s in dspan.find_all("span")]
        spans = [(d, c) for d, c in spans if d is not None]
        if len(spans) >= 2 and "addDash" in spans[0][1]:
            return C._span(spans[0][0], spans[-1][0]), None, None
        if spans:
            return sorted({d for d, _ in spans}), None, None
        ds = [d for d in C.days_in(C.norm_space(dspan.get_text(" "))) if d.days]
        if ds:
            return sorted({x for d in ds for x in d.days}), None, None
    return [], None, None


def widget_rows(p, own: set[str], names: list[str] | None = None) -> tuple[list[dict], dict]:
    rows, dropped = [], {}
    which = C.Reader(p.url, None, own, NAME, names=names).which_church

    def drop(k):
        dropped[k] = dropped.get(k, 0) + 1

    # current markup: div.sacramentTimesModule; older sites (2014-era theme) carry only div.massTimesModule /
    # confessionTimesModule / adorationTimesModule around the same ul.sacramentTimes lists
    for mod in p.soup.select("div.sacramentTimesModule, div.massTimesModule, div.confessionTimesModule, div.adorationTimesModule"):
        if mod.find_parent("div", class_=re.compile(r"sacramentTimesModule|massTimesModule|confessionTimesModule|adorationTimesModule")):
            continue
        for ul in mod.select("ul.sacramentTimes"):
            ucls = ul.get("class") or []
            kind = "confession" if "confessionTime" in ucls else "adoration" if "adorationTime" in ucls else "mass"
            cat_el = ul.find_previous_sibling("div", class_="categoryName")
            cat = C.norm_space(cat_el.get_text(" ")) if cat_el else ""
            for li in ul.select("li.sacramentDay"):
                dspan = li.find("span", class_="day")
                if dspan is None:
                    continue
                days, nth, gen = _days_of(dspan)
                daytxt = C.norm_space(dspan.get_text(" ")).rstrip(" :")
                if len(days) > 1:   # "Mon Fri" as the widget prints it: write the range out, as merge.sanity reads it
                    run = days == C._span(days[0], days[-1])
                    daytxt = f"{C.DAYN[days[0]]} - {C.DAYN[days[-1]]}" if run and len(days) > 2 else ", ".join(C.DAYN[d] for d in days)
                holy = gen == "holy" or bool(C.HOLY_RX.search(cat))
                for st in li.select("span.sacramentTime"):
                    start = _hhmm(st.find("span", class_="startTime"))
                    end = _hhmm(st.find("span", class_="endTime"))
                    if not start:
                        drop("no_time")
                        continue
                    lsp = st.find("span", class_="language")
                    lang = None
                    if lsp is not None:
                        lc = next((c for c in (lsp.get("class") or []) if c != "language"), None)
                        lang = LANG_CLS.get(lc or "", C.norm_space(lsp.get_text(" ")) or None)
                    nsp = st.find("span", class_="notes")
                    notes = C.norm_space(nsp.get_text(" ")) if nsp else ""
                    raw = C.norm_space(f"{daytxt}: {C.norm_space(st.find('span', class_='startTime').get_text(' '))}"
                                       f"{' - ' + C.norm_space(st.find('span', class_='endTime').get_text(' ')) if end else ''}"
                                       f" {lang or ''} {notes}")
                    k = kind
                    if NOT_NOTE.search(notes):
                        drop("not_a_service")
                        continue
                    if kind == "mass" and re.search(r"\bexposition\b|\badoration\b", notes, re.I) and not re.search(r"\bmass\b", notes, re.I):
                        k = "adoration"
                    loc = re.sub(r"^[\s\-–(]+|[)\s]+$", "", notes)
                    # a widget lists the parish's own Masses: a note naming a building without a dedication ("at Oak Street
                    # Church", "Pierson Street Church", "Parish Center") is one of its own worship sites, not another parish
                    if loc and C.other_place(loc, own) and DEDIC_RX.search(loc) and not re.search(r"\bmass\b|\bmisa\b", loc, re.I) or (
                            loc and own and re.match(r"^[-–]\s*st\.?\s", notes, re.I) and not (C.name_words(loc) & own)):
                        drop("other_location")
                        continue
                    church = which(loc) if loc else None
                    if church:
                        raw = f"[{church}] {raw}"
                    nn = [d for d in C.days_in(notes) if d.nth]
                    row_nth = nth or (nn[0].nth if nn else None)
                    row_days = days
                    if nn and not nth:
                        row_days = nn[0].days if nn[0].days[0] in days or not days else days
                    seas = C.season_in(notes) or C.season_in(cat)
                    tstr = start if (k == "mass" or not end) else f"{start}-{end}"
                    if holy:
                        rows.append(_row("other", "Holy days", tstr, lang, raw, p, "high", kind_parsed=k))
                        continue
                    if not row_days:
                        drop("no_day")
                        continue
                    for d in row_days:
                        day = C.day_text(d, row_nth)
                        if seas:
                            day += f" ({seas})"
                        r = _row(k, day, tstr, lang, raw, p, "high")
                        if len(row_days) > 1:
                            r["day_expanded_from"] = daytxt[:40]
                        rows.append(r)
    return rows, dropped


def _row(kind, day, time, lang, raw, p, conf, kind_parsed=None) -> dict:
    r = {"kind": kind, "day": day, "time": time, "language": lang, "raw": raw[:200], "source_url": p.url, "fetched_at": p.fetched_at,
         "needs_review": False, "review_reason": None, "confidence": conf, "template": NAME}
    if kind_parsed and kind_parsed != kind:
        r["kind_parsed"] = kind_parsed
    return r


def parse_full(pages, names=None):
    own = set().union(*[C.name_words(n) for n in names]) if names else set()
    wrows, dropped = [], {}
    for p in pages:
        if C.page_mode(p) is None and p.kind != "home":
            continue
        try:
            r, d = widget_rows(p, own, names)
        except Exception:  # noqa: BLE001
            continue
        wrows += r
        for k, v in d.items():
            dropped[k] = dropped.get(k, 0) + v
    have = {r["kind"] for r in wrows if r["kind"] in ("mass", "confession", "adoration")}
    # free text in the content modules, without the widgets, for kinds the widgets do not give
    grows, gd = generic_strict.parse_full(pages, names, NAME + "+generic_strict", ["#core", "#content1", "ul.region"],
                                          ["div.sacramentTimesModule", "div.massTimesModule", "div.confessionTimesModule", "div.adorationTimesModule", "#content2", ".sideNavModule", "#quickLinks", "#siteName"])
    for k, v in gd.items():
        dropped["text:" + k] = dropped.get("text:" + k, 0) + v
    rows = C.dedupe(wrows + [r for r in grows if r["kind"] not in have])
    return rows, dropped


def parse(pages, names=None) -> list[dict]:
    return C.public(parse_full(pages, names)[0])
