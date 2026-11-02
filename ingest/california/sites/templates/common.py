"""The strict schedule reader shared by every template (see README.md in this folder).

It reads Mass, confession and adoration times from the main content of a parish page, keeping the page's structure:
headings (h1-h6 and bold-only lines) set the section's kind, day, language, location or "holy day" context; tables are
read by their column and row headers; each time takes the day, kind and language its own clause states before any
context. The rules that came out of the independent check (docs/review/ca-check-2026-10-31.md):

  * "Daily" never means Sunday (Monday to Saturday, or Monday to Friday when the same section gives Saturday a time of
    its own); "Weekdays" is Monday to Friday; a range of days is read literally ("M-F", "Monday - Saturday").
  * A time range is read literally (start and end), and a range is a Mass only when its clause says Mass.
  * Lines about something else are refused: choir and rehearsals, rosary / novena / holy hour of a devotion / stations /
    chaplet, Communion services, the Liturgy of the Hours, office, shop and coffee hours, classes and meetings.
  * Section headings decide the kind (Confession / Reconciliation, Adoration, Holy Days); a heading naming another
    church (not one of the parish's own names) drops the rows under it; holy-day and feast blocks are kind "other".
  * A table's column headers give the days (or languages) of its cells.

Every row carries `confidence` (high / medium / low, from how much of it was inferred) and `template`.
No network, no writes: pure functions over cached pages (parse_site.Page objects or anything with .url, .kind,
.fetched_at, .text and .soup).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DAYN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]   # 0 = Monday (parse_site order)
ORDN = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", -1: "Last"}


# typographic punctuation folded to ASCII before matching (a curly apostrophe in "St. John’s" must read as "St. John's",
# an en dash in "May – Aug" as "-"); without this NFKD + ASCII drops them and "John’s" became the word "johns"
PUNCT_ASCII = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u2032": "'", "\u00b4": "'", "\u0060": "'",
                             "\u201c": '"', "\u201d": '"', "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                             "\u2014": "-", "\u2015": "-", "\u2212": "-"})


def fold(s: str) -> str:
    """Lower case, accents removed (so "Sábado" and "Sabado" read alike), typographic quotes and dashes as ASCII."""
    return unicodedata.normalize("NFKD", (s or "").translate(PUNCT_ASCII)).encode("ascii", "ignore").decode().lower()


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\u200b\u200c\u200d\ufeff]", "", (s or "").replace("\xa0", " "))).strip()


# ------------------------------------------------------------------------------------------------ times
# "8:00 AM", "8am", "8 a.m.", "8:00a", "17:30", "noon"; a bare "8:00" (no AM/PM) is kept and resolved from its neighbours.
TIME_RX = re.compile(
    r"(?<![\d:/.$#])(?:"
    r"(?P<h1>[01]?\d|2[0-3])[:.](?P<m1>[0-5]\d)(?![\d])(?:\s*(?P<ap1>[ap])\.?\s?m\b\.?|(?P<ap1b>[ap])(?![a-z]))?"
    r"|(?P<h2>1[0-2]|0?[1-9])\s*(?P<ap2>[ap])\.?\s?m\b\.?"
    r"|(?P<noon>\bnoon\b|\bmediod[ií]a\b|\bmidday\b)"
    r")", re.I)
# a range: "3:30 - 4:30 PM", "3-4pm", "9:00 AM to 6:00 PM", "de 3 a 4 pm", "7:00 PM until 8:00 PM"
RANGE_RX = re.compile(
    r"(?<![\d:/.$#])(?P<a>(?:[01]?\d|2[0-3])(?:[:.][0-5]\d)?)\s*(?P<aap>[ap]\.?\s?m\b\.?)?\s*"
    r"(?:-|–|—|to|until|till|til|hasta|thru|through|a|and|y)\s*"
    r"(?P<b>(?:[01]?\d|2[0-3])(?:[:.][0-5]\d)?)\s*(?P<bap>[ap]\.?\s?m\b\.?|noon)?", re.I)
REF_BEFORE = re.compile(r"(?:after|following|before|prior to|ends? (?:at|by)|until|till|by|después de|despues de|antes de|"
                        r"follows?|preceding)\s+(?:the\s+|each\s+|all\s+|our\s+|la\s+|las\s+)?$", re.I)


@dataclass
class TimeTok:
    start: int
    end: int
    hh: int | None          # hour 0-23 once resolved
    mm: int
    ap: str | None          # 'a' / 'p' when stated
    h12: int
    end_hhmm: str | None = None
    meridiem: str = "explicit"   # explicit / propagated / guessed / 24h
    ref: bool = False             # "after the 8 AM Mass": a reference, not a start

    @property
    def hhmm(self) -> str:
        return f"{self.hh:02d}:{self.mm:02d}"

    def minutes(self) -> int:
        return (self.hh or 0) * 60 + self.mm


def _to24(h: int, ap: str | None) -> int:
    if ap is None:
        return h
    h = h % 12
    return h + 12 if ap == "p" else h


def times_in(text: str) -> list[TimeTok]:
    """Every time in a line, ranges first; bare times take AM/PM from their neighbours (see resolve_bare)."""
    toks: list[TimeTok] = []
    taken: list[tuple[int, int]] = []
    for m in RANGE_RX.finditer(text):
        a, b = m.group("a"), m.group("b")
        aap = (m.group("aap") or "")[:1].lower() or None
        bap_raw = (m.group("bap") or "").lower()
        bap = "p" if bap_raw == "noon" else (bap_raw[:1] or None)
        conn = text[(m.end("aap") if m.group("aap") else m.end("a")):m.start("b")]
        if re.search(r"\ba\b", conn, re.I) and not re.search(r"\bde\s*$", text[:m.start()], re.I):
            continue                              # Spanish "a" only in "de 3 a 4"
        if re.search(r"\b(?:and|y)\b", conn, re.I) and not re.search(r"\b(?:between|entre)\s+(?:the\s+hours\s+of\s+)?$", text[:m.start()], re.I):
            continue                              # "and" only in "between 6 and 10"
        if not (bap or aap or (":" in a and ":" in b)):
            continue                              # "1-2" alone is not a time range
        if not bap and not aap and not re.search(r"\d[:.]\d\d", a + b):
            continue
        ha, ma = (a.replace(".", ":").split(":") + ["00"])[:2]
        hb, mb = (b.replace(".", ":").split(":") + ["00"])[:2]
        ha, hb = int(ha), int(hb)
        if ha > 23 or hb > 23 or (bap and hb > 12) or (aap and ha > 12):
            continue
        if bap_raw == "noon":
            hb24 = 12
        else:
            hb24 = _to24(hb, bap) if bap else hb
        if aap:
            ha24 = _to24(ha, aap)
        elif bap:
            # "3:00 - 4:00 PM" -> 3 PM; "11:00 - 1:00 PM" -> 11 AM; "10 - 11 am" -> 10 AM
            cand = _to24(ha, bap)
            ha24 = cand if cand * 60 + int(ma) <= hb24 * 60 + int(mb) else _to24(ha, "a" if bap == "p" else "p")
        else:
            ha24 = ha
        meridiem = "explicit" if (aap or bap or ha > 12 or hb > 12) else "bare"
        t = TimeTok(m.start(), m.end(), ha24 if meridiem != "bare" else None, int(ma), aap or (bap if meridiem != "bare" else None),
                    ha if ha <= 12 else ha % 12, f"{hb24:02d}:{int(mb):02d}" if meridiem != "bare" else None,
                    "explicit" if meridiem != "bare" else "bare")
        if meridiem == "bare":
            t.hh = None
            t.end_hhmm = f"{hb}:{mb}"      # resolved later with the start
        toks.append(t)
        taken.append((m.start(), m.end()))
    for m in TIME_RX.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        if m.group("noon"):
            toks.append(TimeTok(m.start(), m.end(), 12, 0, "p", 12, None, "explicit"))
            continue
        if m.group("h2"):
            h, mm, ap = int(m.group("h2")), 0, m.group("ap2").lower()
            toks.append(TimeTok(m.start(), m.end(), _to24(h, ap), mm, ap, h, None, "explicit"))
            continue
        h, mm = int(m.group("h1")), int(m.group("m1"))
        ap = (m.group("ap1") or m.group("ap1b") or "").lower() or None
        if m.group(0)[len(m.group("h1"))] == "." and not ap:
            continue                               # "8.30" without am/pm is not a time (prices, scripture)
        if ap and h > 12:
            continue
        if ap:
            toks.append(TimeTok(m.start(), m.end(), _to24(h, ap), mm, ap, h, None, "explicit"))
        elif h > 12 or h == 0:
            toks.append(TimeTok(m.start(), m.end(), h, mm, None, h % 12, None, "24h"))
        else:
            toks.append(TimeTok(m.start(), m.end(), None, mm, None, h, None, "bare"))
    toks.sort(key=lambda t: t.start)
    for t in toks:
        if REF_BEFORE.search(text[max(0, t.start - 30):t.start]):
            t.ref = True
    resolve_bare(toks)
    return toks


def resolve_bare(toks: list[TimeTok]) -> None:
    """"7:30, 9:00, 11:00 a.m." -> all AM; "8:00 AM, 10:00, 12:00" -> 10 AM, 12 PM; "11:30, 1:00 PM" -> 11:30 AM.
    A line with no AM/PM at all is guessed (6-11 AM, 12-5 PM) and marked `guessed`."""
    for i, t in enumerate(toks):
        if t.meridiem != "bare":
            continue
        nxt = next((u for u in toks[i + 1:] if u.meridiem in ("explicit", "24h") and u.hh is not None), None)
        prv = next((u for u in reversed(toks[:i]) if u.meridiem in ("explicit", "24h", "propagated") and u.hh is not None), None)
        if nxt is not None and nxt.ap:
            h = _to24(t.h12, nxt.ap)
            if h * 60 + t.mm > nxt.minutes():
                h = _to24(t.h12, "a" if nxt.ap == "p" else "p")
            t.hh, t.meridiem = h, "propagated"
        elif prv is not None:
            ap = "p" if prv.hh >= 12 else "a"
            h = _to24(t.h12, ap)
            if h * 60 + t.mm < prv.minutes():
                h = _to24(t.h12, "p")
            t.hh, t.meridiem = h, "propagated"
        else:
            t.hh = t.h12 if 6 <= t.h12 <= 11 else (12 if t.h12 == 12 else t.h12 + 12)
            t.meridiem = "guessed"
        if t.end_hhmm and ":" in t.end_hhmm and t.end_hhmm.count(":") == 1 and len(t.end_hhmm) <= 5 and not re.match(r"\d\d:\d\d$", t.end_hhmm):
            hb, mb = t.end_hhmm.split(":")
            hb = int(hb)
            e = _to24(hb, "p" if t.hh >= 12 else "a")
            if e * 60 + int(mb) < t.minutes():
                e = _to24(hb, "p")
            t.end_hhmm = f"{e:02d}:{int(mb):02d}"


def split_lists(text: str, toks: list[TimeTok], ctx) -> list[TimeTok]:
    """A dash between two times that each state AM/PM is a list, not a range, in a Mass line ("7:00 am – 4:00 pm
    (Vigil)", "7:30 am – 9:00 am – 10:30 am"): split it. Confession and adoration lines keep their ranges."""
    ks = {k for _, _, k in kinds_in(text)}
    ctx_kind = ctx.kind[0] if ctx is not None and ctx.kind else None
    mass_line = ("mass" in ks or (not ks & {"confession", "adoration"} and ctx_kind == "mass")) and not ks & {"confession", "adoration"}
    out = []
    for i, t in enumerate(toks):
        seg = text[t.start:t.end]
        both = t.end_hhmm and len(re.findall(r"[ap]\.?\s?m\b", seg, re.I)) >= 2
        chained = t.end_hhmm and i + 1 < len(toks) and re.match(r"\s*[-–—]\s*$", text[t.end:toks[i + 1].start] or "x")
        if t.end_hhmm and (chained or (both and mass_line)) and not re.search(r"\b(?:to|until|till|hasta|and|y)\b", seg, re.I):
            e = TimeTok(t.start, t.end, int(t.end_hhmm[:2]), int(t.end_hhmm[3:5]), None, int(t.end_hhmm[:2]) % 12 or 12, None,
                        t.meridiem, False)
            m = RANGE_RX.match(text, t.start)
            if m:
                e.start = m.start("b")
                t.end = m.end("a") + len(m.group("aap") or "")
            t.end_hhmm = None
            out += [t, e]
        else:
            out.append(t)
    return out


# ------------------------------------------------------------------------------------------------ days
_DW = (r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday|s)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
       r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|ch[uú]a nh[aậ]t|ch[uủ] nh[aậ]t|th[uứ] (?:hai|ba|t[uư]|n[aă]m|s[aá]u|b[aả]y)")
_DWP = rf"(?:{_DW})(?:'?s)?\.?(?![a-z])"
_ORD = r"(?:first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th|primer|primero|segundo|tercer|tercero|cuarto|quinto|[uú]ltimo)"
DAY_WORD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6, "lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4,
            "sab": 5, "dom": 6}
VI_DAY = {"hai": 0, "ba": 1, "tu": 2, "nam": 3, "sau": 4, "bay": 5}
ORD_N = {"first": 1, "1st": 1, "primer": 1, "primero": 1, "second": 2, "2nd": 2, "segundo": 2, "third": 3, "3rd": 3, "tercer": 3,
         "tercero": 3, "fourth": 4, "4th": 4, "cuarto": 4, "fifth": 5, "5th": 5, "quinto": 5, "last": -1, "ultimo": -1}
RANGE_CONN = r"\s*(?:-|–|—|/-|to|through|thru|until|a|al|hasta|đến|den)\s*"
LIST_CONN = r"\s*(?:,|&|\+|/|and|y|or|o)\s*"

ORD_RX = re.compile(rf"\b(?:every\s+|each\s+|cada\s+)?(?P<ord>{_ORD}(?:{LIST_CONN}{_ORD})*)\s+(?P<d>{_DWP})"
                    rf"(?:\s+(?:of\s+(?:the|each|every)\s+month|del\s+mes|de\s+cada\s+mes|of\s+every\s+month))?", re.I)
PAREN_ORD_RX = re.compile(rf"\(\s*(?P<ord>{_ORD}(?:{LIST_CONN}{_ORD})*)\s*\)", re.I)   # "(1st & 3rd)", no day word
RANGE_DAY_RX = re.compile(rf"\b(?:from\s+|de\s+)?(?P<a>{_DWP}){RANGE_CONN}(?P<b>{_DWP})", re.I)
LIST_DAY_RX = re.compile(rf"\b(?P<l>{_DWP}(?:{LIST_CONN}{_DWP})+)", re.I)
ONE_DAY_RX = re.compile(rf"\b(?P<d>{_DWP})", re.I)
GEN_RX = re.compile(r"\b(?P<g>week\s?days?|daily|every\s?day|each day|diariamente|diaria(?:s|mente)?|entre semana|de lunes a viernes|"
                    r"weekends?|fin(?:es)? de semana|anticipated|vigil(?:ia)?|holy\s*days?(?:\s+of\s+obligation)?|"
                    r"d[ií]as?\s+(?:santos|de\s+(?:precepto|obligaci[oó]n))|solemnit(?:y|ies))\b", re.I)
_LT = r"(?:Su|Sa|Th|Tu|M|T|W|R|F)"
LETTER_RX = re.compile(rf"(?<![A-Za-z]){_LT}\.?(?:\s*(?:-|–|—|/|,|&)\s*{_LT}\.?)+(?![A-Za-z])|(?<![A-Za-z])(?:MWF|TTh|TTH|MTWThF|MTWRF)(?![A-Za-z])")
LETTER_DAY = {"M": 0, "T": 1, "Tu": 1, "W": 2, "Th": 3, "R": 3, "F": 4, "Sa": 5, "Su": 6}


def day_of_word(w: str) -> int | None:
    f = re.sub(r"(?:'?s)?\.?$", "", fold(w).strip(" ."))
    if f.startswith(("chua nhat", "chu nhat")):
        return 6
    m = re.match(r"thu (hai|bay|ba|tu|nam|sau)\b", f)
    if m:
        return VI_DAY[m.group(1)]
    if f.startswith("sun"):
        return 6
    return DAY_WORD.get(f[:3])


def _span(a: int, b: int) -> list[int]:
    out, i = [], a
    while True:
        out.append(i)
        if i == b:
            return out
        i = (i + 1) % 7


@dataclass
class DayTok:
    start: int
    end: int
    days: list[int]                 # 0 = Monday
    nth: list[int] | None = None    # monthly rule ("First Friday")
    generic: str | None = None      # daily / weekday / holy / weekend / vigil
    text: str = ""


def days_in(text: str) -> list[DayTok]:
    """Day expressions in a line: ordinals ("First Fridays"), ranges ("Mon - Fri", "lunes a viernes"), lists ("Tues &
    Thurs"), single days, letter forms ("M-F", "T/Th"), and generic words (daily, weekdays, vigil, holy days)."""
    out: list[DayTok] = []

    def free(a: int, b: int) -> bool:
        return not any(x.start < b and a < x.end for x in out)

    for m in ORD_RX.finditer(text):
        d = day_of_word(m.group("d"))
        if d is None:
            continue
        nth = sorted({ORD_N.get(fold(x).strip(), 0) for x in re.split(LIST_CONN, m.group("ord")) if fold(x).strip() in ORD_N})
        if nth:
            out.append(DayTok(m.start(), m.end(), [d], nth, None, m.group(0)))
    for m in RANGE_DAY_RX.finditer(text):
        if not free(m.start(), m.end()):
            continue
        conn = text[m.end("a"):m.start("b")]
        a, b = day_of_word(m.group("a")), day_of_word(m.group("b"))
        if a is None or b is None or a == b:
            continue
        if re.fullmatch(r"\s*(?:a|al)\s*", conn, re.I) and not re.match(r"lun|mar|mi|jue|vie|s[aá]b|dom", fold(m.group("a"))):
            continue
        out.append(DayTok(m.start(), m.end(), _span(a, b), None, None, m.group(0)))
    for m in LIST_DAY_RX.finditer(text):
        if not free(m.start(), m.end()):
            continue
        ds = [day_of_word(x) for x in re.findall(_DWP, m.group("l"), re.I)]
        ds = [x for x in ds if x is not None]
        if len(ds) >= 2:
            out.append(DayTok(m.start(), m.end(), sorted(set(ds)), None, None, m.group(0)))
    for m in ONE_DAY_RX.finditer(text):
        if not free(m.start(), m.end()):
            continue
        d = day_of_word(m.group("d"))
        if d is None:
            continue
        w = fold(m.group("d"))
        if w in ("sun", "sat", "wed", "mar") and not re.search(r"\d|:|day|\b(?:mass|misa)", text[m.end():m.end() + 25], re.I) \
                and not re.match(r"\s*[:.,-]", text[m.end():]):
            if w == "mar" or not text[m.start()].isupper():
                continue                           # "the sun", "mar" (sea), lower-case "sat"
        out.append(DayTok(m.start(), m.end(), [d], None, None, m.group(0)))
    for m in LETTER_RX.finditer(text):
        if not free(m.start(), m.end()):
            continue
        s = m.group(0)
        if s in ("MWF",):
            ds = [0, 2, 4]
        elif s in ("TTh", "TTH"):
            ds = [1, 3]
        elif s in ("MTWThF", "MTWRF"):
            ds = [0, 1, 2, 3, 4]
        else:
            parts = re.findall(rf"{_LT}", s)
            conns = re.findall(r"[-–—/,&]", s)
            vals = [LETTER_DAY.get(p) for p in parts]
            if None in vals or len(vals) < 2:
                continue
            if len(vals) == 2 and conns and conns[0] in "-–—":
                ds = _span(vals[0], vals[1])
            else:
                ds = sorted(set(vals))
        out.append(DayTok(m.start(), m.end(), ds, None, None, s))
    for m in GEN_RX.finditer(text):
        if not free(m.start(), m.end()):
            continue
        g = fold(m.group("g"))
        if g.startswith(("weekend", "fin")):     # before "week": "Weekend" is not "Weekday" (it named Mon-Fri until 2026-11-01)
            out.append(DayTok(m.start(), m.end(), [], None, "weekend", m.group(0)))
        elif g.startswith(("week", "entre", "de lunes")):
            out.append(DayTok(m.start(), m.end(), [0, 1, 2, 3, 4], None, "weekday", m.group(0)))
        elif g.startswith(("daily", "every", "each", "diaria")):
            out.append(DayTok(m.start(), m.end(), [0, 1, 2, 3, 4, 5], None, "daily", m.group(0)))
        elif g.startswith(("weekend", "fin")):
            out.append(DayTok(m.start(), m.end(), [], None, "weekend", m.group(0)))
        elif g.startswith(("anticip", "vigil")):
            out.append(DayTok(m.start(), m.end(), [5], None, "vigil", m.group(0)))
        else:
            out.append(DayTok(m.start(), m.end(), [], None, "holy", m.group(0)))
    out.sort(key=lambda x: x.start)
    # "Saturday Vigil": one expression
    merged: list[DayTok] = []
    for x in out:
        if merged and x.generic == "vigil" and merged[-1].days == [5] and not merged[-1].nth and x.start - merged[-1].end <= 3:
            merged[-1].end = x.end
            continue
        merged.append(x)
    return merged


# ------------------------------------------------------------------------------------------------ kinds, languages
KIND_RX = [
    ("confession", re.compile(r"confess\w*|reconcil\w*|\bpenance\b|confesi\w*|x[ưu]ng t[ộo]i|gi[ảa]i t[ộo]i", re.I)),
    ("adoration", re.compile(r"adorat\w*|adoraci\w*|exposition|\bholy hour\b|hora santa|ch[ầa]u th[áa]nh", re.I)),
    ("benediction", re.compile(r"\bbenediction\b|\bbendici[oó]n\b", re.I)),
    ("mass", re.compile(r"\bmass(?:es)?\b|\bmisas?\b|\bvigil(?:ia)?\b|\beucharist\b|divine liturgy|th[áa]nh l[ễe]|"
                        r"\bliturg(?:y|ies|ia)\b(?! of the)|\bmesse\b|\bmsza\b", re.I)),
    # devotions are offerings too (Matthew, 2026-11-01): kept as kind "devotion" (merge.py / lib/schedule.ts show it as
    # "other"), never dropped; "Stations of the Cross, followed by Holy Mass" is a devotion row, the Mass after it unstated
    ("devotion", re.compile(r"stations of the cross|via crucis|\bchaplet\b|\bnovena\b|\brosary\b|\brosario\b|"
                            r"divine mercy (?:hour|devotion|chaplet)|gorzkie [żz]ale|miraculous medal|perpetual help devotion|"
                            r"\bdevotions?\b(?! (?:schedule|times))|\bmorning prayer\b|\bevening prayer\b|\bvespers\b|\blauds\b", re.I)),
    ("not", re.compile(
        r"rehears\w*|\bpractice\b|\bchoir\b|\bcoro\b|\bschola\b|communion service|eucharistic service|liturgy service|"
        r"word and communion|celebration of the word|liturgy of the (?:word|hours)|children'?s liturgy|"
        r"night prayer|divine office|office hours?|horario de oficina|"
        r"office (?:is )?(?:open|closed)|parish office|\boficina\b|\bcoffee\b|\bcaf[eé]\b|gift shop|book ?store|\bthrift\b|"
        r"\bshop\b|\bbingo\b|"
        r"perpetual help|\bknights\b|caballeros|\bmeeting\b|\bmeets?\b|\bse reúnen?\b|\bjunta\b|\bclass(?:es)?\b|\bclases?\b|bible study|"
        r"estudio b[ií]blico|church (?:is )?open|open daily|doors open|food (?:pantry|bank|distribution)|"
        r"religious education|faith formation|catechism|catequesis|\bdoctrina\b|\bocia\b|\brcia\b|youth group|"
        r"\bschool office\b|\blunch\b|\bclosed\b|cerrad[oa]|\bwedding|\bboda|quincea\w*|\bbaptism\w*|\bbautismo\w*|"
        r"anointing|mass intentions?|intenciones|prayer group|grupo de oraci[oó]n|\bcenacle\b|\bcursillo\b|"
        r"legion of mary|\bultreya\b|charismatic|renovaci[oó]n carism|\bregistration\b|\binscripci|\bpotluck\b|"
        r"\bdinner\b|\bbreakfast\b|\bdonuts?\b|\bfestival\b|\bfiesta\b|\bconcert\b|\bdance\b|\bmovie\b|\bwebinar\b|"
        r"\bzoom\b|\bretreat\b|\bseminar\b|\bworkshop\b|\bspeaker\b|\btalk\b|\bpilgrimage\b", re.I)),
]
HOLY_RX = re.compile(r"\bholy\s*days?\b|\bholidays?\b|d[ií]as? (?:feriados?|festivos?)|\bferiados?\b|\bd[ií]as?\s+(?:santos|de\s+(?:precepto|obligaci[oó]n))|\bsolemnit|\ball\s+souls|\ball\s+saints|"
                     r"fieles\s+difuntos|todos\s+los\s+santos|d[ií]a\s+de\s+(?:los\s+)?muertos|\bthanksgiving\b|\bchristmas\b|"
                     r"\bnavidad\b|nochebuena|new\s+year|ash\s+wednesday|mi[eé]rcoles\s+de\s+ceniza|holy\s+(?:thursday|week|saturday)|"
                     r"good\s+friday|viernes\s+santo|semana\s+santa|triduum|\beaster\b|\bpascua\b|immaculate\s+conception|"
                     r"\binmaculada\b|simbang\s+gabi|\bassumption of\b|our lady of guadalupe feast|feast of", re.I)
_MON = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_RANGE_TO = r"\s*(?:-|\u2013|\u2014|to|through|thru|until|till|\u2019?til)\s*"
# a seasonal block: "Summer", "Lent", "from Memorial Day to Labor Day", "May - Aug", "June through August", "(Sept-May)"
SEASON_RX = re.compile(r"\b(?P<s>summer|lent(?:en)?|advent|winter|during the school year|school year|while school is in session|"
                       r"school days?|cuaresma|adviento|verano|"
                       rf"memorial\s+day{_RANGE_TO}labou?r\s+day|"
                       rf"(?:{_MON})\.?{_RANGE_TO}(?:{_MON})\b\.?)(?:\b|(?<=\.))", re.I)
_MON_N = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def season_in(text: str) -> str | None:
    """The season a line or heading limits its rows to (season_label), or None. "Except from Memorial Day thru Labor Day",
    "no 5:30 PM Mass in summer" name the season the row is NOT held in: the row is the regular schedule."""
    for m in SEASON_RX.finditer(text or ""):
        pre = (text or "")[max(0, m.start() - 30):m.start()]
        if re.search(r"\b(?:except|excluding|exc\.|not|no|other than|but|outside|excepto|menos)\b[^.;:]*$", pre, re.I):
            continue
        return season_label(m.group("s"))
    return None


def season_label(s: str | None) -> str | None:
    """The qualifier written after a seasonal row's day ("Sunday (summer)"): a month range reads as summer / winter / the
    school year when it is one ("May - Aug" -> "summer, may-aug"), so merge.py and lib/find.ts keep it off the weekly list."""
    if not s:
        return None
    f = fold(s).strip(" .")
    if re.search(r"school", f):
        return "during the school year"
    if re.search(r"memorial", f):
        return "summer"
    ms = re.findall(r"[a-z]+", f)
    if len(ms) >= 2 and ms[0][:3] in _MON_N and ms[-1][:3] in _MON_N:
        a, b = _MON_N[ms[0][:3]], _MON_N[ms[-1][:3]]
        span = list(range(a, b + 1)) if a <= b else list(range(a, 13)) + list(range(1, b + 1))
        tag = f"{ms[0][:3]}-{ms[-1][:3]}"
        if a in (8, 9) and b in (5, 6):
            return f"during the school year, {tag}"
        if 7 in span and 1 not in span:
            return f"summer, {tag}"
        if 1 in span and 7 not in span:
            return f"winter, {tag}"
        return f"seasonal, {tag}"
    return f.replace("lenten", "lent")
SKIP_HEAD = re.compile(r"mass intentions?|intenciones|week at a glance|this week|upcoming|calendar|calendario|\bnews\b|noticias|"
                       r"bulletin|bolet[ií]n|\breadings?\b|lecturas|office hours?|horario de oficina|parish office|\bstaff\b|"
                       r"\bevents?\b|eventos|announcements?|anuncios|ministry schedule|minister(?:s)? schedule|lector|usher|"
                       r"altar server|eucharistic minister|sacristan|\bdonat|\bgiving\b|\bstewardship\b|livestream schedule", re.I)
SCHED_HEAD = re.compile(r"\bmass(?:es)?\b|\bmisas?\b|schedule|horarios?|\btimes\b|confess|reconcil|adorat|adoraci|\bworship\b|"
                        r"\bliturg|th[áa]nh l[ễe]|\bvigil\b|holy hour", re.I)
LANGS = [
    ("Spanish", r"spanish|espa[nñ]ol|espanol|\(esp\.?\)|\bespa\b|castellano|en español"),
    ("English", r"english|ingl[eé]s"),
    ("Vietnamese", r"vietnamese|ti[eế]ng vi[eệ]t|vietnamita|\bvi[eệ]t\b|vi[eệ]t ng[uữ]"),
    ("Tagalog", r"tagalog|filipino"),
    ("Portuguese", r"portuguese|portugu[eê]s"),
    ("Korean", r"korean|hangul|coreano"),
    ("Chinese", r"chinese|mandarin|cantonese|chino"),
    ("Latin", r"\blatin\b|tridentine|extraordinary form|traditional latin|\btlm\b|lat[ií]n"),
    ("Polish", r"polish|polski"),
    ("Italian", r"italian|italiano"),
    ("French", r"french|fran[cç]ais"),
    ("Tongan", r"tongan"), ("Samoan", r"samoan"), ("Igbo", r"\bigbo\b"), ("Haitian Creole", r"haitian|kreyol"),
    ("Arabic", r"arabic"), ("Chaldean", r"chaldean|aramaic"), ("Syro-Malabar", r"malayalam|syro-?malabar"),
    ("Indonesian", r"indonesian"), ("Japanese", r"japanese"), ("Croatian", r"croatian"), ("Hungarian", r"hungarian"),
    ("Lithuanian", r"lithuanian"), ("Eritrean", r"eritrean|ge'?ez|tigrinya"), ("Burmese", r"burmese"),
    ("German", r"german|deutsch"), ("American Sign Language", r"\basl\b|sign language|interpreted for the deaf"),
    ("Bilingual", r"bilingual|biling[uü]e"),
]
LANG_RX = re.compile("|".join(f"(?P<L{i}>{rx})" for i, (_, rx) in enumerate(LANGS)), re.I)


def langs_in(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in LANG_RX.finditer(text):
        for i, (name, _) in enumerate(LANGS):
            if m.group(f"L{i}"):
                out.append((m.start(), m.end(), name))
                break
    return out


def kinds_in(text: str) -> list[tuple[int, int, str]]:
    out = []
    for kind, rx in sorted(KIND_RX, key=lambda kr: kr[0] != "not"):
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b, _ in out):
                continue
            out.append((m.start(), m.end(), kind))
    out.sort()
    return out


MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|" \
         r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"
DATE_RX = re.compile(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}\s+(?:de\s+)?(?:{MONTHS})\b|"
                     r"(?<![\d:])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\d:])", re.I)
NEG_RX = re.compile(r"\bno\s+(?:mass|misa|masses|confessions?|adoration)\b|\bno hay\b|\bcancel+ed\b|\bsuspended\b", re.I)
CHANGE_RX = re.compile(r"\b(effective|starting|beginning|begins|as of|until further notice|a partir|comenzando)\b", re.I)
# An exception qualifier on a standing time: "(except: Ash Wednesday & Holy Week)", "(except Holy Saturday)", "except
# January 1", "(no confessions on holy days)". The row stands; the qualifier is kept as the row's `note` and removed from
# the text the reader judges, so its feast / holy / date / church words never turn the row into a holy-day row, a dated
# line or another place's schedule (ihmatlanta.org, 2026-11-01: Confession and Adoration rows were dropped as
# other_location because "Holy Saturday" reads as a church name). A clause holding a time, or one naming a plain weekday
# ("Mon-Sat 8 am (except Wed)") is left alone: it changes which days the row covers.
EXCEPT_RX = re.compile(
    r"[(\[]\s*(?:except(?:ing)?|excepto|excluding|no\s+(?:mass(?:es)?|confessions?|adoration|misas?)\s+(?:on|during)|"
    r"not\s+(?:on|during)|closed\s+(?:on|during))\b[^()\[\]]*[)\]]"
    r"|(?:^|[,;]\s*|\s[-–—]\s*|\s)(?:except(?:ing)?|excepto|excluding)\b\s*:?[^;|()]*$", re.I)


def split_except(text: str) -> tuple[str, str | None]:
    """(text without its exception qualifiers, the qualifiers joined) -- see EXCEPT_RX."""
    notes, out = [], text or ""
    for m in reversed(list(EXCEPT_RX.finditer(out))):
        seg = m.group(0)
        if times_in(seg):
            continue
        plain_day = [d for d in days_in(seg) if d.days and d.generic != "holy"]
        if plain_day and not HOLY_RX.search(seg) and not DATE_RX.search(seg):
            continue
        notes.insert(0, norm_space(seg.strip(" ,;-–—()")))
        out = out[:m.start()] + out[m.end():]
    return (norm_space(out), "; ".join(notes)) if notes else (text, None)


# ------------------------------------------------------------------------------------------------ page items
BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "section", "article", "header", "footer", "main", "aside", "nav", "table", "tr", "td",
              "th", "tbody", "thead", "blockquote", "dd", "dt", "dl", "figure", "figcaption", "form", "center", "pre", "address",
              "h1", "h2", "h3", "h4", "h5", "h6", "hr", "body", "html", "fieldset", "details", "summary", "caption"}
HEAD_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
DROP_TAGS = ["script", "style", "noscript", "svg", "template", "iframe", "form", "button", "select", "input", "textarea", "nav",
             "footer"]


@dataclass
class Item:
    kind: str                 # 'h' (heading) / 'line' / 'table'
    text: str = ""
    level: int = 9            # 1-6 headings, 7 bold-only line, 9 plain
    rows: list = field(default_factory=list)   # table: list of rows, each a list of cell texts


def _is_data_table(t: Tag) -> bool:
    if t.find("table"):
        return False
    rows = t.find_all("tr")
    if len(rows) < 2:
        return False
    cells = [r.find_all(["td", "th"]) for r in rows]
    multi = sum(1 for c in cells if len(c) >= 2)
    if multi < max(2, len(rows) // 2):
        return False
    lens = [len(norm_space(c.get_text(" "))) for r in cells for c in r]
    return bool(lens) and sum(lens) / len(lens) < 90


def _bold_only(el: Tag) -> bool:
    txt = norm_space(el.get_text(" "))
    if not txt or len(txt) > 90:
        return False
    b = "".join(norm_space(x.get_text(" ")) for x in el.find_all(["strong", "b"]))
    if not b:
        st = [x for x in el.find_all("span", style=True) if re.search(r"font-weight:\s*(?:bold|[6-9]00)", x["style"])]
        b = "".join(norm_space(x.get_text(" ")) for x in st)
    return bool(b) and len(re.sub(r"\W", "", b)) >= 0.9 * len(re.sub(r"\W", "", txt))


def items_of(root: Tag) -> list[Item]:
    """The content under `root` as headings, text lines and tables, in document order."""
    out: list[Item] = []
    buf: list[str] = []

    def flush():
        if buf:
            for ln in "".join(buf).split("\n"):
                ln = norm_space(ln)
                if ln:
                    out.append(Item("line", ln))
            buf.clear()

    def walk(el):
        for c in el.children:
            if isinstance(c, Comment):
                continue
            if isinstance(c, NavigableString):
                buf.append(str(c))
                continue
            if not isinstance(c, Tag):
                continue
            name = c.name.lower()
            if name in DROP_TAGS:
                continue
            if name == "br":
                buf.append("\n")
                continue
            if name in HEAD_TAGS:
                flush()
                for br in c.find_all("br"):
                    br.replace_with("\n")
                for ln in c.get_text("").split("\n"):
                    ln = norm_space(ln)
                    if ln:
                        out.append(Item("h", ln, HEAD_TAGS[name]))
                continue
            if name == "table" and _is_data_table(c):
                flush()
                rows = []
                for tr in c.find_all("tr"):
                    row = []
                    for cell in tr.find_all(["td", "th"]):
                        row.append("\n".join(x.text for x in items_of(cell) if x.kind != "table"))
                    rows.append(row)
                out.append(Item("table", rows=rows))
                continue
            if name in BLOCK_TAGS:
                flush()
                if name in ("p", "div", "li", "td", "th", "dt") and not c.find(list(BLOCK_TAGS - {"p"}) + ["br"]) and _bold_only(c):
                    txt = norm_space(c.get_text(" "))
                    if not TIME_RX.search(txt):
                        out.append(Item("h", txt, 7))
                        continue
                walk(c)
                flush()
                continue
            walk(c)

    walk(root)
    flush()
    return out


def clean_soup(html_or_soup) -> BeautifulSoup:
    s = BeautifulSoup(str(html_or_soup), "lxml") if not isinstance(html_or_soup, str) else BeautifulSoup(html_or_soup, "lxml")
    for t in s(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    for t in s.select("[hidden], [style*='display:none'], [style*='display: none'], [style*='visibility:hidden'], .sr-only, .screen-reader-text, .visually-hidden"):
        t.decompose()
    return s


def main_of(soup: BeautifulSoup, selectors: list[str] = (), drop: list[str] = ()) -> Tag:
    """The page's main content: the first selector that matches with some text, else <main>/<article>/#content, else
    <body>; site headers, navigation, footers, sidebars and `drop` selectors are removed."""
    for sel in list(drop) + ["header#header", "#header", "#masthead", ".site-header", "[role=banner]", "[role=navigation]",
                             "#sidebar", ".sidebar", "aside", ".widget-area", "#secondary", ".site-footer", "#footer",
                             "[data-elementor-type=header]", "[data-elementor-type=footer]", ".menu", "#menu", ".nav", ".navbar",
                             ".breadcrumb", ".breadcrumbs", "#cookie-law-info-bar", ".cookie-notice"]:
        for t in soup.select(sel):
            t.decompose()
    body = soup.body or soup
    n_body = len(TIME_RX.findall(body.get_text(" ")))
    for sel in list(selectors) + ["main", "[role=main]", "#main", "#content", "article", ".entry-content", ".content", "#page"]:
        el = soup.select_one(sel)
        if el is None or len(norm_space(el.get_text(" "))) <= 40:
            continue
        # a container that holds most of the page's times (a wrong pick, e.g. the first of many ".content" boxes,
        # holds few of them)
        if n_body and len(TIME_RX.findall(el.get_text(" "))) < 0.5 * n_body:
            continue
        return el
    return body


# ------------------------------------------------------------------------------------------------ names (location test)
NAME_STOP = {"st", "saint", "sts", "of", "the", "church", "parish", "catholic", "and", "de", "la", "el", "los", "las", "our", "lady",
             "san", "santa", "mission", "community", "chapel", "center", "centre", "shrine", "cathedral", "holy", "ca", "giao", "xu",
             "duc", "me", "roman", "basilica", "iglesia", "parroquia", "capilla", "del", "in", "at", "nuestra", "senora"}


def name_words(s: str) -> set[str]:
    t = fold(s)
    t = re.sub(r"\([^)]*\)", " ", t)
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", t).split() if w not in NAME_STOP and len(w) > 2 and not w.isdigit()}


PLACE_RX = re.compile(r"\b(?:st\.?|saint|sts\.?|san|santa|santo|our lady|nuestra se[nñ]ora|holy|sacred|blessed|mission|chapel|capilla|"
                      r"church|iglesia|parroquia|cathedral|shrine|oratory|campus|center|centre|hall)\b", re.I)
ADDR_RX = re.compile(r"\b\d{2,6}\s+(?:[NSEW]\.?\s+)?[A-Z][\w.]*(?:\s+[A-Z][\w.]*)*\s+(?:st|street|rd|road|ave|avenue|blvd|boulevard|dr|"
                     r"drive|way|ln|lane|ct|court|pl|place|hwy|highway|pkwy|parkway)\b\.?", re.I)


def other_place(text: str, own: set[str]) -> bool:
    """True when a heading or line prefix names a church or place whose name shares no word with the parish's own names."""
    if not own:
        return False
    t = re.sub(r"\b(?:mass(?:es)?|misas?|schedule|horario|times?|confessions?|adoration|weekend|weekday|daily|sunday|saturday|"
               r"english|spanish|vigil)\b", " ", text, flags=re.I)
    if not (PLACE_RX.search(t) or ADDR_RX.search(t)):
        return False
    if re.match(r"\s*(?:welcome to|bienvenidos? a|about)\b", t, re.I):
        return False                                   # "Welcome to St. Raphael RC Church": the site's own banner
    words = name_words(t)
    if not words:
        return False
    if words & own:
        return False
    # a misspelt name in the input list ("St. Raphel") still names the parish's own church
    import difflib
    return not any(len(w) >= 5 and len(o) >= 5 and difflib.SequenceMatcher(None, w, o).ratio() >= 0.8 for w in words for o in own)


# ------------------------------------------------------------------------------------------------ the reader
@dataclass
class Ctx:
    kind: tuple | None = None        # (value, level, source)
    day: tuple | None = None         # (DayTok, level)
    lang: tuple | None = None
    holy: tuple | None = None
    skip: tuple | None = None
    other_loc: tuple | None = None
    loc: tuple | None = None         # (church name, level): one of the site's own churches
    season: tuple | None = None
    season_days: set = field(default_factory=set)   # weekdays already listed under the current seasonal heading
    dated: bool = False
    sticky_kind: str | None = None
    last_line_day: DayTok | None = None

    def clear_from(self, level: int):
        for f in ("kind", "day", "lang", "holy", "skip", "other_loc", "season", "loc"):
            v = getattr(self, f)
            if v is not None and v[1] >= level:
                setattr(self, f, None)
        self.dated = False
        self.sticky_kind = None
        self.last_line_day = None


def day_text(d: int, nth: list[int] | None) -> str:
    if nth:
        names = [ORDN.get(n, str(n)) for n in nth]
        lead = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"{lead} {DAYN[d]} of the month"
    return DAYN[d]


class Reader:
    """Reads items (from items_of) into schedule rows. One Reader per page."""

    def __init__(self, page_url: str, fetched_at: str | None, own: set[str], template: str, mode: str = "full",
                 names: list[str] | None = None):
        self.url, self.fetched_at, self.own, self.template, self.mode = page_url, fetched_at, own, template, mode
        # a site serving several churches: rows under one church's heading are tagged "[<church>] ..." (lib/schedule.ts
        # splitSite), so each church's record keeps its own
        self.names = [(n, name_words(n)) for n in (names or []) if name_words(n)]
        self.multi = len(self.names) >= 2
        self.rows: list[dict] = []
        self.dropped: dict[str, int] = {}
        self.section = 0
        self._note: str | None = None

    def drop(self, why: str, n: int = 1):
        self.dropped[why] = self.dropped.get(why, 0) + n

    def which_church(self, text: str) -> str | None:
        """The one of the site's churches a heading or suffix names (most name words in common), for multi-church sites."""
        if not self.multi or not text:
            return None
        w = name_words(text)
        best = sorted(((len(w & nw), n) for n, nw in self.names if w & nw), reverse=True)
        if not best or (len(best) > 1 and best[0][0] == best[1][0]):
            return None
        return best[0][1]

    # -- context lines (no times)
    def context(self, ctx: Ctx, text: str, level: int):
        frag = len(re.sub(r"[^A-Za-zÀ-ỹ]", "", text)) < 3 or bool(re.match(r"^[a-z)(]", text)) or (
            len(text) <= 12 and bool(re.fullmatch(r"\(?[\w\s/&,.-]*\)?", text)) and bool(langs_in(text)))
        sub = frag or (len(text) <= 40 and not kinds_in(text) and not HOLY_RX.search(text) and bool(
            [d for d in days_in(text) if d.generic != "holy"] or langs_in(text)))
        if level <= 7 and not sub:
            ctx.clear_from(level)
            self.section += 1
        elif level <= 7:
            level = 9                                  # "Saturday" / "Spanish" as a sub-heading: a day or language only
        f = text
        if len(f) > 160:
            return
        if DATE_RX.search(f) and len(f) <= 40 and not CHANGE_RX.search(f):
            ctx.dated = True
            return
        if len(re.sub(r"[^A-Za-zÀ-ỹ]", "", f)) < 3 and not days_in(f):
            return            # "|", "-", "18": a separator in an events list keeps the date above it in force
        ctx.dated = False
        holy = HOLY_RX.search(f)
        ks = [k for _, _, k in kinds_in(f)]
        dts = days_in(f)
        if SKIP_HEAD.search(f) and not (re.search(r"\bmass times\b|confess|reconcil|adorat", f, re.I) and not re.search(
                r"intention|lector|usher|server|minister|sacristan", f, re.I)):
            ctx.skip = (True, level)
            return
        if (level <= 7 and not sub) or [k for k in ks if k in ("mass", "confession", "adoration")] or holy:
            ctx.skip = None
        if holy and not any(d.generic not in ("holy", None) for d in dts if d.generic) and not re.search(r"\bweekday|\bdaily|\bsunday\b", f, re.I):
            ctx.holy = (True, level)
        elif level <= 7 or (ks and "not" not in ks) or [d for d in dts if d.generic != "holy"]:
            ctx.holy = None
        loc_text = re.split(r"[:\-–—]\s", f)[0]
        holy_head = bool(holy) and not re.search(r"\b(?:church|chapel|parish|cathedral|basilica|shrine|oratory|mission|iglesia|"
                                                 r"parroquia|capilla)\b", f, re.I) and not ADDR_RX.search(f)
        if other_place(loc_text, self.own) and not ks and not holy_head:   # "Holy Days of Obligation:" names no church
            ctx.other_loc = (loc_text, level)
        elif (level <= 7 and (ctx.other_loc is None or level <= ctx.other_loc[1])) or (PLACE_RX.search(f) and not ks):
            ctx.other_loc = None      # a sub-heading under another church's heading ("MASS TIMES", its address) keeps it
        own_loc = self.which_church(loc_text) if (PLACE_RX.search(loc_text) or ADDR_RX.search(loc_text)) else None
        if own_loc:
            ctx.loc = (own_loc, level)
        # "Novena with Mass" / "Mass, then Rosary": a service named beside a devotion is the section's kind
        for k in sorted(ks, key=lambda k: k == "devotion"):
            if k in ("confession", "adoration", "mass", "devotion", "not"):
                ctx.kind = (k, level, "heading" if level <= 7 else "line", text[:60])
                ctx.sticky_kind = None
                break
        ls = langs_in(f)
        if ls and len(f) < 70:
            ctx.lang = (ls[0][2], level)
        se = season_in(f)
        if se:
            ctx.season = (se, level)
            ctx.season_days = set()
        elif level <= 7 and not dts:
            ctx.season = None
        elif ctx.season and dts and {x for d in dts for x in d.days} & ctx.season_days:
            # "Weekday Masses (July-August)" Mon 8:00 ... Fri 8:00, then "Monday - Friday" 8 am / 6 pm: a second list of the same
            # days after a seasonal one is the regular schedule, not more of the season
            ctx.season = None
            ctx.season_days = set()
        if dts:
            d = next((x for x in dts if x.generic != "holy"), None)
            if d is not None:
                ctx.day = (d, level)
                ctx.last_line_day = None

    # -- the church heading above a home-page schedule block (schedule_blocks): sets only the place, never the kind or day
    def place(self, ctx: Ctx, text: str, level: int):
        loc_text = re.split(r"[:\-–—]\s", text)[0]
        if other_place(loc_text, self.own):
            ctx.other_loc = (loc_text, level)
        else:
            ctx.other_loc = None
            own_loc = self.which_church(loc_text)
            if own_loc:
                ctx.loc = (own_loc, level)

    # -- one line with times
    def line(self, ctx: Ctx, text: str, cell_day: DayTok | None = None, cell_lang: str | None = None,
             cell_kind: str | None = None, from_table: bool = False):
        text, self._note = split_except(text)
        toks = [t for t in times_in(text)]
        if not toks:
            return
        toks = split_lists(text, toks, ctx)
        # "3:00 pm — Confessions (until about 3:30 pm)": the second time is the first one's end, not a start of its own
        merged = []
        for t in toks:
            if merged and not merged[-1].end_hhmm and not merged[-1].ref and t.hh is not None and re.search(
                    r"\b(?:until|till|to|hasta)\s+(?:about|approximately|approx\.?|around|roughly|aprox\w*)\s*$|\bends?\s+(?:at\s+)?(?:about|around)\s*$",
                    text[max(0, t.start - 30):t.start], re.I) and t.minutes() > merged[-1].minutes():
                merged[-1].end_hhmm = t.hhmm
                continue
            merged.append(t)
        toks = merged
        if ctx.skip:
            self.drop("skipped_section", len(toks))
            return
        if ctx.other_loc:
            self.drop("other_location", len(toks))
            return
        if DATE_RX.search(text) and not HOLY_RX.search(text):
            self.drop("dated_line", len(toks))
            self._dated_line = True           # an events list: "October 18 | 9:00 am", then its title "Mass 9:00AM - English"
            return
        if getattr(self, "_dated_line", False) and not [d for d in days_in(text) if d.days]:
            self._dated_line = False
            self.drop("dated_block", len(toks))
            return
        self._dated_line = False
        if ctx.dated:
            self.drop("dated_block", len(toks))
            return
        prefix = re.split(r"\s*[:–—]\s+|\s+-\s+", text, maxsplit=1)[0] if re.search(r"[:–—]\s|\s-\s", text) else ""
        if prefix and len(prefix) < 70 and not TIME_RX.search(prefix) and other_place(prefix, self.own):
            self.drop("other_location", len(toks))
            return
        at = re.search(r"\b(?:at|en)\s+((?:the\s+)?(?:St\.?|Saint|Sts\.?|San|Santa|Our Lady|Holy|Sacred|Blessed|Christ|Mission|"
                       r"[A-Z][a-z]+\s+(?:Cathedral|Basilica|Shrine|Parish))\b[^,;.()|]{0,60})", text)
        if at and not self.multi and other_place(at.group(1), self.own):
            self.drop("other_location", len(toks))      # "8:15am - Mass at Christ Cathedral with Bishop Vann": another church's
            return
        line_loc = self.which_church(prefix) if prefix and len(prefix) < 70 and not TIME_RX.search(prefix) else None
        holy_line = bool(HOLY_RX.search(text)) or bool(ctx.holy)
        dts = [d for d in days_in(text) if d.generic != "holy"]
        ks = kinds_in(text)
        ls = langs_in(text)
        starts_with_time = toks[0].start <= min([d.start for d in dts] + [len(text)])
        season = season_in(text) or (ctx.season[0] if ctx.season else None)
        consumed_k, consumed_l = set(), set()
        last_day_used = None
        for i, t in enumerate(toks):
            # "Friday after the 8:30 am Mass", "Saturday following 9:00am Mass", "before the 5 pm Mass": a confession or
            # adoration time stated against a Mass. Kept (2026-11-01, Matthew: the whole posted schedule) as a row at the
            # Mass's time with `relative` = after / before, the line as its `note`, and needs_review (merge never shows a
            # needs_review row), so a person or a later pass can state the real start. A reference beside a stated start
            # ("4:50 - 5:20 pm (before the 5:30 Mass)") or an end ("until 4 pm") stays dropped.
            self._rel = None
            if t.ref:
                lead = text[max(0, t.start - 30):t.start]
                if any(not x.ref for x in toks) or re.search(r"(?:ends? (?:at|by)|until|till|\bby)\s+(?:the\s+)?$", lead, re.I) \
                        or any(x.ref and x.start < t.start for x in toks):
                    self.drop("relative_time")
                    continue
                self._rel = "before" if re.search(r"before|prior to|antes de|preceding", lead, re.I) else "after"
            nxt_start = toks[i + 1].start if i + 1 < len(toks) else len(text)
            prv_end = toks[i - 1].end if i else 0
            # ---- kind
            kind, ksrc = None, None
            for (a, b, k) in ([] if t.ref else ks):     # "after the 8 am Mass": that Mass is the reference, not the kind
                if a >= t.end and a < nxt_start:
                    gap = text[t.end:a]
                    if re.fullmatch(r"[\s\-–—:()@|]*(?:[A-Za-zÀ-ỹ'’.]+\s*){0,2}[\s\-–—:()@]*", gap) and not re.search(r"[,;|]", gap) \
                            and (not gap.strip(" -–—:()@") or langs_in(gap) or re.fullmatch(r"\s*(?:(?:the|la|el|de|eucharistic|blessed|holy|daily|weekday|weekend|sunday|anticipated|"
                                                                          r"sacrament(?:al)?|of|individual|private|communal|perpetual)\s*)*", gap.strip(" -–—:()@"), re.I)):
                        kind, ksrc = k, "line"
                        consumed_k.add(a)
                        break
            if kind is None:
                prev = [(a, b, k) for (a, b, k) in ks if b <= t.start and a not in consumed_k]
                if prev:
                    kind, ksrc = prev[-1][2], "line"
            if kind is None and cell_kind:
                kind, ksrc = cell_kind, "table"
            if kind is None and ctx.kind:
                kind, ksrc = ctx.kind[0], ("section" if ctx.kind[2] == "heading" else "context")
            if kind is None and ctx.sticky_kind == "mass":     # a Mass list continues; confession / adoration lines do not
                kind, ksrc = ctx.sticky_kind, "sticky"
            if t.ref and kind not in ("confession", "adoration"):
                self.drop("relative_time")
                continue
            if kind is None and self.mode == "full" and (dts or ctx.day or cell_day):
                kind, ksrc = "mass", "default"
            if kind is None:
                self.drop("no_kind")
                continue
            if kind == "not":
                self.drop("not_a_service")
                continue
            if NEG_RX.search(text[max(prv_end, t.start - 40):t.start]):
                self.drop("negated")                      # "There is no Mass at 12:10 PM on those days"
                continue
            if kind == "benediction":
                self.drop("benediction_end")
                continue
            # ---- day
            day, dsrc = None, None
            if dts:
                if starts_with_time:
                    after = [d for d in dts if d.start >= t.end and d.start < nxt_start]
                    before = [d for d in dts if d.end <= t.start]
                    day = after[0] if after else (before[-1] if before else (last_day_used or dts[0]))
                else:
                    before = [d for d in dts if d.end <= t.start]
                    day = before[-1] if before else dts[0]
                dsrc = "line"
                last_day_used = day
            elif cell_day is not None:
                day, dsrc = cell_day, "table"
            elif ctx.last_line_day is not None and starts_with_time and (not ctx.day or (ctx.day[0].generic == "weekend"
                                                                                       and not ctx.day[0].days)):
                # a bare time under a "Weekend" heading continues the line above ("Sunday: 9:00 AM" / "11:00 AM"); the
                # weekend word names no single day of its own
                day, dsrc = ctx.last_line_day, "carry"
            elif ctx.day:
                day, dsrc = ctx.day[0], "context"
            elif ctx.last_line_day is not None and starts_with_time:
                day, dsrc = ctx.last_line_day, "carry"
            # ---- language
            lang = None
            for j, (a, b, L) in enumerate(ls):
                if a >= t.end and a < nxt_start and not re.search(r"[;|]", text[t.end:a]) and a not in consumed_l:
                    lang = L
                    consumed_l.add(a)
                    # "(English/Việt)", "English & Spanish": both languages
                    if j + 1 < len(ls) and ls[j + 1][0] < nxt_start and re.fullmatch(r"\s*(?:/|&|and|y|-)\s*", text[b:ls[j + 1][0]], re.I):
                        lang = f"{L}/{ls[j + 1][2]}" if ls[j + 1][2] != L else L
                        consumed_l.add(ls[j + 1][0])
                    break
            if lang is None:
                lo = max([d.end for d in dts if d.end <= t.start] + [0])
                prev = [(a, b, L) for (a, b, L) in ls if lo <= a and b <= t.start and a not in consumed_l]
                if prev:
                    lang = prev[-1][2]
            if lang is None:
                # "11:30 am and 1:00 pm in Spanish": a list of times shares the language written after its last time
                j = i
                while j + 1 < len(toks) and re.fullmatch(r"\s*,?\s*(?:and|&|y)\s*", text[toks[j].end:toks[j + 1].start], re.I):
                    j += 1
                if j > i:
                    lim = toks[j + 1].start if j + 1 < len(toks) else len(text)
                    after = [L for (a, b, L) in ls if toks[j].end <= a < lim and not re.search(r"[;|]", text[toks[j].end:a])]
                    if after:
                        lang = after[0]
            lang = lang or cell_lang or (ctx.lang[0] if ctx.lang else None)
            # ---- church (multi-church sites): the time's own suffix ("8:30 a.m., St. Philip, Occidental"), the line's
            # prefix, else the heading above
            church = None
            if self.multi:
                tail = text[t.end:nxt_start]
                church = self.which_church(tail) if PLACE_RX.search(tail) else None
                church = church or line_loc or (ctx.loc[0] if ctx.loc else None)
                if church is None and PLACE_RX.search(tail) and other_place(tail, self.own):
                    self.drop("other_location")
                    continue
            # a row whose kind came from its heading carries the heading, so the line read alone still says what it is
            # (lib/schedule.ts kindOf reads a Mass row with a time range and no "Mass" as not a Mass; merge.sanity holds
            # a line that names a livestream without a service word)
            line_txt = text
            if ksrc not in ("line",) and kind in ("mass", "confession", "adoration") and not re.search(
                    {"mass": r"\b(?:mass|misa)", "confession": r"confess|reconcil|confesi", "adoration": r"adora"}[kind], text, re.I):
                head = ctx.kind[3] if (ctx.kind and ctx.kind[0] == kind and len(ctx.kind) > 3) else {"mass": "Mass", "confession": "Confession",
                                                                                                       "adoration": "Adoration"}[kind]
                line_txt = f"{head}: {text}" if head.strip(" :") else text
            raw_out = f"[{church}] {line_txt}" if church else line_txt
            # ---- build
            hh = t.hhmm
            if holy_line or (day is not None and day.generic == "holy"):
                self.emit("other", "Holy days", hh, lang, raw_out, t, ksrc, "holy", 0, kind_parsed=kind)
                continue
            if day is None or (not day.days and day.generic != "holy"):
                self.drop("no_day")
                continue
            if kind == "mass" and t.end_hhmm and not re.search(r"\bmass|\bmisa|\bvigil|th[áa]nh l[ễe]|liturg", text, re.I):
                self.drop("range_not_a_mass")
                continue
            if kind == "mass" and not (5 * 60 <= t.minutes() <= 21 * 60 + 30):
                self.drop("implausible_hour")
                continue
            if kind == "confession" and not (6 * 60 <= t.minutes() <= 22 * 60):
                self.drop("implausible_hour")
                continue
            if len(day.days) == 1 and not day.nth:
                # "English (1st & 3rd) 09:00 AM" under a "Sundays" heading: an ordinal list with no day word of its own
                po = PAREN_ORD_RX.search(text)
                if po:
                    nth = sorted({ORD_N.get(fold(x).strip(), 0) for x in re.split(LIST_CONN, po.group("ord")) if fold(x).strip() in ORD_N})
                    if nth:
                        day = replace(day, nth=nth)
            days = day.days
            if day.generic == "daily" and kind == "adoration":
                days = list(range(7))
            tstr = hh if kind == "mass" or not t.end_hhmm else f"{hh}-{t.end_hhmm}"
            for d in days:
                self.emit(kind, day_text(d, day.nth), tstr, lang, raw_out, t, ksrc, dsrc, d, daily=day.generic == "daily",
                          season=season, from_table=from_table, multi=len(days) > 1, daytxt=day.text)
            if season and ctx.season and season == ctx.season[0]:
                ctx.season_days.update(days)
            if kind in ("mass", "confession", "adoration"):
                ctx.sticky_kind = kind
        if dts:
            ctx.last_line_day = dts[-1]

    def emit(self, kind, day, time, lang, raw, t: TimeTok, ksrc, dsrc, dnum, kind_parsed=None, daily=False, season=None,
             from_table=False, multi=False, daytxt=""):
        pen = {"line": 0, "table": 0, "section": 0.5, "context": 0.5, "sticky": 1, "default": 1.5}.get(ksrc, 1)
        pen += {"line": 0, "table": 0, "context": 0.5, "carry": 1, "holy": 0}.get(dsrc, 1)
        pen += {"explicit": 0, "24h": 0, "propagated": 0.5, "guessed": 1.5}.get(t.meridiem, 1)
        conf = "high" if pen == 0 else "medium" if pen <= 1 else "low"
        if season and day != "Holy days":
            day = f"{day} ({season_label(season)})"
        row = {"kind": kind, "day": day, "time": time, "language": lang, "raw": raw[:200], "source_url": self.url,
               "fetched_at": self.fetched_at, "needs_review": False, "review_reason": None, "confidence": conf,
               "template": self.template, "_why": {"kind": ksrc, "day": dsrc, "meridiem": t.meridiem}, "_dnum": dnum,
               "_daily": daily, "_section": self.section}
        if kind_parsed and kind_parsed != kind:
            row["kind_parsed"] = kind_parsed
        if getattr(self, "_note", None):
            row["note"] = self._note[:120]
        if getattr(self, "_rel", None):
            row.update(relative=self._rel, needs_review=True, review_reason="relative_time", note=norm_space(raw)[:120],
                       confidence="low")
        if multi and daytxt:
            row["day_expanded_from"] = daytxt[:40]
        self.rows.append(row)

    # -- tables
    def table(self, ctx: Ctx, rows: list[list[str]]):
        if not rows:
            return
        if ctx.skip or ctx.other_loc:
            self.drop("skipped_section" if ctx.skip else "other_location", sum(len(times_in(c)) for r in rows for c in r))
            return
        width = max(len(r) for r in rows)
        head = rows[0]
        col_day: dict[int, DayTok] = {}
        col_lang: dict[int, str] = {}
        col_kind: dict[int, str] = {}
        has_head = False
        if not any(times_in(c) for c in head):
            for j, c in enumerate(head):
                ds = [d for d in days_in(c) if d.days]
                ls, ks = langs_in(c), kinds_in(c)
                if ds and len(c) < 40:
                    col_day[j] = ds[0]
                if ls and len(c) < 40:
                    col_lang[j] = ls[0][2]
                if ks and len(c) < 50:
                    col_kind[j] = ks[0][2]
            has_head = bool(col_day or col_lang or col_kind)
        body = rows[1:] if has_head or not any(times_in(c) for c in head) else rows
        if not has_head and not any(times_in(c) for c in head):
            self.context(ctx, " ".join(head), 9)
        for r in body:
            if not r:
                continue
            rh = r[0]
            rh_days = [d for d in days_in(rh) if d.days or d.generic == "holy"] if not times_in(rh) else []
            rh_lang = langs_in(rh)[0][2] if (langs_in(rh) and not times_in(rh)) else None
            rh_kind = kinds_in(rh)[0][2] if (kinds_in(rh) and not times_in(rh)) else None
            if not any(times_in(c) for c in r):
                self.context(ctx, " ".join(r), 9)
                continue
            if not (has_head or rh_days or rh_lang or rh_kind):
                self.line(ctx, " ".join(c.replace("\n", " ") for c in r), from_table=True)
                continue
            for j, c in enumerate(r):
                if not times_in(c):
                    continue
                dday = rh_days[0] if rh_days else col_day.get(j)
                if rh_days and rh_days[0].generic == "holy":
                    dday = rh_days[0]
                rlabel = rh.replace("\n", " ") if (rh_days or rh_kind) and j > 0 and len(rh) < 60 else None
                clabel = head[j].replace("\n", " ") if has_head and j < len(head) and len(head[j]) < 40 and head[j] else None
                for ln in c.split("\n"):
                    if dday is not None and dday.generic == "holy":
                        self.line(ctx, f"Holy days {ln}", None, col_lang.get(j) or rh_lang, col_kind.get(j) or rh_kind, True)
                        continue
                    if days_in(ln):
                        txt = ln
                    else:
                        # the row header (and column header) the cell sits under: "Weekday Mass: 6:30am", "Sunday (Spanish): 12:00 PM"
                        lab = " ".join(x for x in (rlabel or (dday.text if dday is not None else None), clabel if clabel and clabel != rlabel else None) if x)
                        txt = f"{lab}: {ln}" if lab else ln
                    self.line(ctx, txt, None, col_lang.get(j) or rh_lang, col_kind.get(j) or rh_kind, True)

    def run(self, items: list[Item], ctx: Ctx | None = None):
        ctx = ctx or Ctx()
        for it in items:
            if it.kind == "place":
                self.place(ctx, it.text, it.level)
                continue
            if it.kind == "table":
                self.table(ctx, it.rows)
            elif not times_in(it.text):
                self.context(ctx, it.text, it.level if it.kind == "h" else 9)
            elif len(it.text) > 200:
                # prose: one sentence at a time, so a time takes the kind and days of its own sentence
                for sent in re.split(r"(?<=[a-z0-9)][.!?])\s+(?=[A-Z])", it.text):
                    if times_in(sent):
                        self.line(ctx, sent)
            else:
                self.line(ctx, it.text)
        return self.finish()

    def finish(self) -> list[dict]:
        rows = self.rows
        # "Daily" is Monday to Friday where the same section gives Saturday a time of its own
        sat_explicit = {(r["_section"], r["kind"]) for r in rows if r["_dnum"] == 5 and not r["_daily"]}
        rows = [r for r in rows if not (r["_daily"] and r["_dnum"] == 5 and (r["_section"], r["kind"]) in sat_explicit and r["kind"] != "adoration")]
        return dedupe(rows)       # same rules (labelled slot kept; a schedule line beats a sentence for one slot)


# ------------------------------------------------------------------------------------------------ pages
SCHED_URL = re.compile(r"mass|misa|schedule|horario|worship|liturg|confess|reconcil|adorat|sacrament|times|thanh-le|gio-le", re.I)
NOT_SCHED_URL = re.compile(r"christmas|navidad|easter|pascua|holy-?week|semana-?santa|lent|cuaresma|advent|triduum|intention|"
                           r"minister|lector|usher|server|funeral|wedding|marriage|quince|baptis|bautis|calendar|/events?/|"
                           r"/news|bulletin|livestream|readings|first-?communion|confirmation|ocia|rcia|school|/blog/|"
                           r"facility|scheduler|copy-of|music|choir", re.I)


def page_title(p) -> str:
    try:
        t = p.soup.title.get_text(" ", strip=True) if p.soup.title else ""
    except Exception:  # noqa: BLE001
        t = ""
    return t


def page_mode(p) -> str | None:
    """'full' for a page about the schedule (its URL or title), 'block' for the home page and other pages (read only
    inside a schedule heading), None for pages never read (seasonal or event pages)."""
    path = re.sub(r"^https?://[^/]+", "", p.url or "")
    segs = [x for x in re.split(r"[/?#&=]", path) if x]
    last = segs[-1] if segs else ""
    title = page_title(p)
    if NOT_SCHED_URL.search(last) or (NOT_SCHED_URL.search(path) and not SCHED_URL.search(last)):
        return None
    if getattr(p, "kind", "") == "home":
        return "block"
    if page_kind_default(p):
        return "full"
    if getattr(p, "kind", "") in ("confession", "adoration", "liturgy"):
        return "block"           # a "Sacraments" hub or "Worship" page: only under its schedule headings
    if SCHED_URL.search(path) or SCHED_HEAD.search(title):
        return "full"
    if getattr(p, "kind", "") == "mass_times":
        return "block"
    return None


PAGE_KIND_URL = {"confession": re.compile(r"confess|reconcil|penance|confesi|spowied|giai-?toi|xung-?toi", re.I),
                 "adoration": re.compile(r"adorat|adoraci|adoracj|holy-?hours?|hora-?santa|exposition|chau-?thanh", re.I)}


def page_kind_default(p) -> str | None:
    """A page fetched as the confession / adoration page whose own URL or title names that service (".../reconciliation",
    "Eucharistic Adoration"): its times are that service unless a heading on it says otherwise (2026-11-01)."""
    k = getattr(p, "kind", "")
    if k not in PAGE_KIND_URL:
        return None
    path = re.sub(r"^https?://[^/]+", "", p.url or "")
    title = page_title(p)
    if PAGE_KIND_URL[k].search(path) or PAGE_KIND_URL[k].search(fold(title)):
        return k
    return None


def schedule_blocks(items: list[Item]) -> list[Item]:
    """For a 'block' page: only the items under a schedule heading (Mass Times, Horario de Misas, Confessions, ...),
    up to the next heading of the same or a higher level that is not about the schedule."""
    out: list[Item] = []
    on, lvl, n = False, 9, 0
    # The church heading a schedule block sits under ("HOLY GHOST CHURCH" / address / phone / "MASS TIMES"): carried into the
    # block, so a two-church home page keeps each church's times apart (holyghost-stbernadette.org, 2026-11-01). Address,
    # city and phone lines between the two do not break the link; any other heading or more than 8 items does.
    place, since = None, 0
    for it in items:
        is_head = it.kind == "h" or (it.kind == "line" and len(it.text) < 50 and not times_in(it.text) and SCHED_HEAD.search(it.text)
                                     and not SKIP_HEAD.search(it.text))
        if is_head and SCHED_HEAD.search(it.text) and not SKIP_HEAD.search(it.text) and not HOLY_RX.search(it.text) \
                and not (not on and times_in(it.text) and DATE_RX.search(" ".join(x.text for x in items[max(0, items.index(it) - 2):items.index(it) + 3]))):
            # (an events list's entry title, "Mass 9:00AM - English" beside "October 18 | 9:00 am", starts no block)
            if place is not None and since <= 8:
                out.append(Item("place", place.text, place.level))
            place = None
            on, lvl, n = True, (it.level if it.kind == "h" else 8), 0
            out.append(Item("h", it.text, min(it.level, 7)))
            continue
        if on and it.kind == "h" and it.level <= lvl and not SCHED_HEAD.search(it.text) and not days_in(it.text) and not langs_in(it.text):
            on = False
            out.append(Item("h", "—", it.level))
            since = 0
        if not on:
            since += 1
            if it.kind == "h" and len(it.text) < 80 and PLACE_RX.search(it.text) and not times_in(it.text) and not ADDR_RX.search(it.text):
                place, since = it, 0
            elif it.kind == "h" and not (ADDR_RX.search(it.text) or (re.search(r"\d", it.text) and len(it.text) < 60)):
                place = None
        if on:
            n += 1
            if n > 60:
                on = False
                continue
            out.append(it)
    return out


def read_pages(pages, own: set[str], template: str, main_selectors=(), drop=(), extra_items=None,
               names: list[str] | None = None) -> tuple[list[dict], dict]:
    """Rows from every schedule page (full) and from schedule blocks of the home page; home-page rows are kept only
    for kinds the schedule pages did not give."""
    full_rows, block_rows, dropped = [], [], {}
    for p in pages:
        mode = page_mode(p)
        if mode is None:
            continue
        try:
            soup = clean_soup(p.soup)
        except Exception:  # noqa: BLE001
            continue
        root = main_of(soup, list(main_selectors), list(drop))
        items = extra_items(p, root) if extra_items else items_of(root)
        if mode == "block":
            items = schedule_blocks(items)
        rd = Reader(p.url, p.fetched_at, own, template, mode, names)
        pk = page_kind_default(p)
        rows = rd.run(items, Ctx(kind=(pk, 0, "heading", pk.capitalize())) if pk else None)
        for k, v in rd.dropped.items():
            dropped[k] = dropped.get(k, 0) + v
        (full_rows if mode == "full" else block_rows).extend(rows)
    # The home page is always a schedule source (2026-11-01, Matthew: the home page often carries the whole weekly schedule
    # and sub-pages only part of it): its block rows are added wherever a schedule page did not already give that kind, day
    # and start time (listed first, so a home row and a sub-page row for one slot keep the home row). Other block pages (a
    # Mass page read only under its headings) still fill only kinds the full pages lack.
    have = {r["kind"] for r in full_rows}
    home_urls = {p.url for p in pages if getattr(p, "kind", "") == "home"}
    home_rows = [r for r in block_rows if r["source_url"] in home_urls]
    rows = home_rows + full_rows + [r for r in block_rows if r["source_url"] not in home_urls and r["kind"] not in have]
    return dedupe(rows), dropped


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per (kind, day, start, language); of two readings of one slot the schedule line wins over a sentence
    ("Our 11am & 12:30pm Sunday Masses are live streamed", which row hygiene later drops as prose)."""
    keep, seen = set(), set()
    labelled = {(r["kind"], r["day"], r["time"][:5]) for r in rows if r.get("language")}
    for i, r in sorted(enumerate(rows), key=lambda x: len(x[1].get("raw") or "") > 110):
        k = (r["kind"], r["day"], r["time"][:5], r.get("language"))
        if k in seen or (not r.get("language") and (r["kind"], r["day"], r["time"][:5]) in labelled):
            continue
        seen.add(k)
        keep.add(i)
    return [r for i, r in enumerate(rows) if i in keep]


def public(rows: list[dict]) -> list[dict]:
    """Rows in the stored shape (internal fields removed)."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
