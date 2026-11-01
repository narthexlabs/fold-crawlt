"""Parse downloaded parish bulletins into structured, page-referenced facts.

Reads data/real/enrichment/bulletin_manifest.json (from fetch_bulletins.py) and the text/PDF files in
data/raw/bulletins/<id>/. Writes one file per parish: data/real/enrichment/bulletins/<id>.json, with one
record per bulletin date:

  offertory        printed collection figures: label, amount(s), column headers, page
  bma              Bishop's Ministries Appeal goal / pledged / paid / percent / participants, page
  schedules        Mass, confession and adoration lines (day, time, language), page
  ministries       ministry and group names (no personal names), page
  staff            public staff and clergy (name, title) from staff directories, page
  events           upcoming dated events (date, short title), page
  volunteer_needs  calls for volunteers, donations or supplies (short text), page
  sacramental_counts  printed counts only (e.g. "32 children received First Communion"), page

Privacy: prayer lists, the sick, the deceased, Mass intentions, banns, newly baptized and donor lists are
skipped by section and by line. Short texts are cut at contact words ("contact", "call", "email") and
emails/phones are removed. No text longer than 15 words is stored from any source. Every record then goes
through the shared screen in ingest/public/privacy.py (screen_bulletin_record): Mass-intention lines keep only
their day and time ("[Mass intention withheld]"), other private names become "[name withheld]", and staff rows
that are not public staff or clergy (ministry contacts, committee members, advertisers, crawl junk) are dropped
or listed under staff_withheld with their page only.

Financial rows are rebuilt from word positions with pdfplumber (label on the same line to the left, or
just above in the same column), only on pages whose text mentions money keywords.

Usage: python ingest/public/enrichment/parse_bulletins.py [--ids a,b]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import REAL, ROOT, load_json, now_iso, save_json  # noqa: E402
from parish_sites import LANGS, TIME_RE, parse_staff  # noqa: E402
from privacy import screen_bulletin_record  # noqa: E402

logging.getLogger("pdfminer").setLevel(logging.ERROR)

MANIFEST = REAL / "enrichment" / "bulletin_manifest.json"
OUTDIR = REAL / "enrichment" / "bulletins"
MAXW = 15

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MON_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
          r"septiembre|octubre|noviembre|diciembre)")
ES_MON = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
          "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
DAYS_RE = re.compile(r"\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday|weekdays?|daily|"
                     r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|domingo|lunes|martes|mi[eé]rcoles|jueves|"
                     r"viernes|s[aá]bado|first friday|first saturday)s?\b\.?", re.I)
PRIVATE_RE = re.compile(
    r"pray(?:er)? (?:for|list|chain|requests?)|please pray|let us pray for|our sick|the sick|enfermos|deceased|"
    r"difuntos|rest in peace|eternal rest|in memoriam|in memory of|in loving memory|mass intentions?|"
    r"intenciones|banns|amonestaciones|newly baptized|recently baptized|were baptized|bautizad|"
    r"recently (?:departed|deceased)|who (?:have )?died|passed away|sanctuary lamp|altar (?:candles?|flowers?)|"
    r"healing of|recovery of|homebound|remember in|our beloved|r\.?i\.?p\.?\b|†|donor list|honor roll|"
    r"thank you to (?:the following|our donors)|special thanks to|birthday|thanksgiving\s*[:/]|/\s*thanksgiving|"
    r"special intentions?|for the souls?|souls? of|req(?:uested)? by|anniversary\b|cumplea[ñn]os|acci[oó]n de gracias", re.I)
NAMES_LIST = re.compile(r"(?:\b[A-Z][a-z]+\s+[A-Z][a-z]+,\s*){2,}|^[A-Z][a-z]+\s+[A-Z][a-z]+,\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b")
CONTACT_CUT = re.compile(r"\b(?:contact(?:ing)?|call(?:ing)?|text|e-?mail|email|rsvp|reach out to|speak (?:to|with)|"
                         r"see|ask|led by|coordinated by|chaired by|llame|llamar|comun[ií]quese|contacte|"
                         r"informaci[oó]n|for (?:more )?info(?:rmation)?|questions\??)\b.*", re.I)
EMAIL_RE = re.compile(r"\S+@\S+")
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b(?:\s*(?:x|ext\.?)\s*\d+)?")
URL_RE = re.compile(r"https?://\S+|www\.\S+")

MINISTRY_TERMS = re.compile(
    r"\b(ministr(?:y|ies)|ministerio|society|sociedad|council|consejo|guild|legion of mary|knights of columbus|"
    r"knights|caballeros|club|choir|coro|youth|j[oó]venes|juvenil|young adults?|ocia|rcia|catechesis|catequesis|"
    r"faith formation|religious education|confirmation|st\.? vincent de paul|svdp|vincentian|bible study|"
    r"estudio b[ií]blico|prayer group|grupo de oraci[oó]n|rosary group|cursillo|couples for christ|"
    r"marriage encounter|engaged encounter|altar servers?|monaguillos|lectors?|lectores|ushers?|ujieres|"
    r"eucharistic ministers?|extraordinary ministers?|sacristans?|hospitality|greeters|bereavement|grief|"
    r"funeral|respect life|pro-?life|gabriel project|walking with moms|food pantry|pantry|"
    r"social justice|outreach|scouts?|cub scouts|girl scouts|ladies|women[’']?s group|men[’']?s group|"
    r"mothers?[’']? group|moms|catholic daughters|altar society|filipino|vietnamese community|"
    r"charismatic|renewal|small christian communit|small faith|alpha|that man is you|walking with purpose|"
    r"senior|golden|young at heart|cyo|sports|stewardship committee|finance council|pastoral council|"
    r"legion|sodality|secular franciscans?|lay dominicans?|third order|oblates?|ccd|vbs|"
    r"vacation bible|homeless|shelter|meals?|soup kitchen|prison|detention|jail|hospital|"
    r"health ministry|parish nurse|mental health|music ministry|liturgy committee|art & environment|"
    r"environment committee|welcome|evangeliz|adoration|perpetual adoration|holy hour|divine mercy)\b", re.I)
EVENT_TERMS = re.compile(
    r"\b(join us|will (?:be held|take place|meet)|is (?:coming|on)|retreat|dinner|breakfast|lunch|potluck|"
    r"festival|fiesta|bazaar|sale|fundraiser|gala|auction|concert|workshop|class(?:es)?|session|talk|"
    r"lecture|mission|novena|pilgrimage|celebration|celebrate|feast|procession|blessing|meeting|reunion|"
    r"registration|open house|drive|walk|run|picnic|movie|trivia|bingo|crab feed|pancake|social|"
    r"vigil|service|mass for|misa|evento|convivio|kermes|venta|retiro|charla|taller|bendici[oó]n|"
    r"all souls|all saints|d[ií]a de (?:los )?muertos|thanksgiving|advent|christmas|halloween|trunk)\b", re.I)
VOLUNTEER_RE = re.compile(
    r"\b(volunteers? (?:are |is )?(?:needed|wanted)|need(?:s|ed)? (?:volunteers|help|more)|looking for "
    r"(?:volunteers|help|people)|help (?:is )?(?:needed|wanted)|we need|in need of|sign[- ]?up|signup|"
    r"volunteer(?:ing)? opportunit|be a volunteer|become a volunteer|can you help|please help|"
    r"donations? (?:of|are|is|needed|welcome|accepted)|please (?:donate|bring|consider donating)|"
    r"donate|drive\b|collecting|giving tree|angel tree|toy drive|coat drive|food drive|canned|"
    r"non-?perishable|diapers|school supplies|se necesitan|necesitamos|voluntari[oa]s|donaciones|"
    r"donativos|ay[uú]danos)\b", re.I)
SACRAMENT_RE = re.compile(
    r"(?P<n>\b\d{1,4}\b|\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty)\b)\s+"
    r"(?P<who>(?:\w+\s+){0,3}?)(?P<what>children|kids|students|youth|teens|young people|candidates|"
    r"confirmandi|catechumens|elect|adults|infants|babies|couples|people|parishioners|families|ni[ñn]os|"
    r"j[oó]venes|adultos|parejas)\b[^.\n]{0,60}?\b(?P<sac>baptized|baptisms?|bautizos?|bautizad[oa]s|"
    r"(?:first|1st) (?:holy )?communion|primera comuni[oó]n|confirmed|confirmation|confirmaci[oó]n|"
    r"married|marriages?|weddings?|matrimonios?|entered the church|received into|full communion|ocia|rcia|"
    r"funerals?|anointed|anointing)", re.I)
WORDNUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
           "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}

# ---------------------------------------------------------------- money
AMT_RE = re.compile(r"^\(?\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\)?$|^\(?\$\d+(?:\.\d{1,2})?\)?$")
PCT_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%")
MONEY_PAGE_RE = re.compile(r"offertory|collection|colecta|ofrenda|stewardship|giving|budget|presupuesto|"
                           r"appeal|bma|bishop[’']?s|campa[ñn]a|ministerios del obispo|pledge|weekly|semanal|"
                           r"year[- ]to[- ]date|ytd|financial|finanzas|donations?", re.I)
STRONG_FIN = re.compile(r"offertory|collection|colecta|ofrenda|stewardship|sacrificial|giving|budget|presupuesto|"
                        r"weekly (?:goal|need|update|report|income)|year[- ]to[- ]date|\bytd\b|financial|finanzas|"
                        r"plate|envelopes?|sobres|online|eft|electronic|weshare|osv|pushpay|faith direct|givelify|"
                        r"vanco|appeal|bma|bishop[’']?s (?:ministr|appeal|annual)|campa[ñn]a|pledge|promesa|"
                        r"second collection|segunda|primera|1st|2nd|over\s*/?\s*\(?under|variance|shortfall|"
                        r"deficit|surplus|maintenance|building fund|capital|debt|assessment|cathedraticum|"
                        r"mass donation|loose|cash|checks?|donors?|households|total|goal|meta|needed|raised", re.I)
LABEL_JUNK = re.compile(r"tickets?|prizes?|winners?|drawing|cost per|price|\bfee\b|per person|admission|registration fee|tuition|"
                        r"\$\d+ (?:per|each)|raffle|scrip|gift cards?|candle|sponsor|advertis|ad info", re.I)


def words15(s: str, n: int = MAXW) -> str:
    w = re.sub(r"\s+", " ", s or "").strip().split(" ")
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def scrub(s: str) -> str:
    s = EMAIL_RE.sub("", s)
    s = PHONE_RE.sub("", s)
    s = URL_RE.sub("", s)
    s = CONTACT_CUT.sub("", s)
    return re.sub(r"\s+", " ", s).strip(" -–—:|,;•·")


def money_value(t: str) -> float | None:
    neg = t.strip().startswith("(") and t.strip().endswith(")")
    v = re.sub(r"[^\d.]", "", t)
    if not v or v.count(".") > 1:
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return -x if neg else x


def fin_category(label: str) -> str:
    L = label.lower()
    if re.search(r"bma|bishop[’']?s (?:ministr|appeal|annual)|campa[ñn]a del obispo|ministerios del obispo|appeal", L):
        return "bma"
    if re.search(r"second|2nd|segunda|special collection|colecta especial", L):
        return "second_collection"
    if re.search(r"goal|budget|presupuesto|needed|need\b|meta|target|required", L):
        return "goal_or_budget"
    if re.search(r"over\s*/?\s*\(?under|variance|short|deficit|surplus|difference|diferencia", L):
        return "variance"
    if re.search(r"year[- ]to[- ]date|\bytd\b|fiscal year|to date|a la fecha", L):
        return "year_to_date"
    if re.search(r"online|eft|electronic|weshare|osv|pushpay|faith ?direct|givelify|vanco|e-?giving|"
                 r"digital|text to give|en l[ií]nea", L):
        return "online_giving"
    if re.search(r"plate|loose|cash|envelopes?|sobres|checks?|mass donation", L):
        return "plate_or_envelope"
    if re.search(r"building|capital|maintenance|mantenimiento|repair|roof|debt|deuda|loan|renovation", L):
        return "building_or_debt"
    if re.search(r"total|offertory|collection|colecta|ofrenda|stewardship|sacrificial|weekly|semanal|"
                 r"1st|first|primera|sunday|domingo|\bmass\b|misa", L):
        return "offertory"
    return "other"


def finance_rows(pdf_path: Path, pages: list[int]) -> list[dict]:
    rows = []
    try:
        doc = pdfplumber.open(str(pdf_path))
    except Exception:  # noqa: BLE001
        return rows
    with doc:
        for pno in pages:
            if pno - 1 >= len(doc.pages):
                continue
            try:
                words = doc.pages[pno - 1].extract_words(keep_blank_chars=False)
            except Exception:  # noqa: BLE001
                continue
            ws = []
            for w in words:  # join "$" + "9,716.00"
                if (ws and ws[-1]["text"] == "$" and abs(ws[-1]["top"] - w["top"]) < 3
                        and re.match(r"^\(?\d", w["text"])):
                    ws[-1] = dict(ws[-1], text="$" + w["text"], x1=w["x1"])
                else:
                    ws.append(dict(w))
            lines: dict[int, list[dict]] = {}
            for w in ws:
                lines.setdefault(round(w["top"] / 2.5), []).append(w)
            amounts = [w for w in ws if AMT_RE.match(w["text"].rstrip(".,;:*").replace(" ", ""))
                       and "$" in w["text"]]
            used = set()
            for a in amounts:
                if id(a) in used:
                    continue
                # same visual row: amounts to the right are extra columns of the same row
                row_amts = sorted([b for b in amounts if abs(b["top"] - a["top"]) < 3 and b["x0"] >= a["x0"]
                                   and b["x0"] - a["x1"] < 160], key=lambda b: b["x0"])
                chain = [a]
                for b in row_amts[1:]:
                    if b["x0"] - chain[-1]["x1"] < 160:
                        chain.append(b)
                for b in chain:
                    used.add(id(b))
                # label: same line to the left (contiguous words), else line(s) just above in the same column
                left = sorted([v for v in ws if abs(v["top"] - a["top"]) < 3.5 and v["x1"] <= a["x0"] + 1
                               and v["x0"] > a["x0"] - 300 and not AMT_RE.match(v["text"].replace(" ", ""))],
                              key=lambda v: v["x0"])
                lab, prev = [], a["x0"]
                for v in reversed(left):
                    if prev - v["x1"] > 70:
                        break
                    lab.insert(0, v["text"])
                    prev = v["x0"]
                label = " ".join(lab)
                above_lab = ""
                if not STRONG_FIN.search(label):
                    cands = [v for v in ws if 0 < a["top"] - v["top"] < 34 and v["x1"] > a["x0"] - 200
                             and v["x0"] < a["x1"] + 120 and "$" not in v["text"]]
                    by_top: dict[int, list[dict]] = {}
                    for v in cands:
                        by_top.setdefault(round(v["top"] / 2.5), []).append(v)
                    for k in sorted(by_top, reverse=True):  # nearest line first
                        line = sorted(by_top[k], key=lambda v: v["x0"])
                        txt = " ".join(v["text"] for v in line)
                        if STRONG_FIN.search(txt) and not PRIVATE_RE.search(txt):
                            above_lab = txt
                            break
                # headers for multi-amount rows ("This Week", "YTD", "Budget")
                headers = []
                if len(chain) > 1:
                    for b in chain:
                        hs = [v for v in ws if 0 < b["top"] - v["top"] < 80 and v["x0"] < b["x1"] + 10
                              and v["x1"] > b["x0"] - 25 and re.search(r"[A-Za-z]", v["text"])
                              and "$" not in v["text"]]
                        if hs:
                            top = max(v["top"] for v in hs)
                            headers.append(" ".join(v["text"] for v in sorted(hs, key=lambda v: v["x0"])
                                                    if abs(v["top"] - top) < 3))
                        else:
                            headers.append(None)
                label_full = re.sub(r"[.…_·]{2,}", " ", label).strip(" :|-–")
                above_full = re.sub(r"[.…_·]{2,}", " ", above_lab).strip(" :|-–")
                rows.append({"page": pno, "label": label_full, "label_above": above_full or None,
                             "amounts": [w["text"].rstrip(".,;:*") for w in chain],
                             "values": [money_value(w["text"]) for w in chain],
                             "headers": headers or None, "y": round(a["top"]), "x": round(a["x0"])})
    return rows


MASS_LABEL = re.compile(r"^(?:(?:sat(?:urday)?|sun(?:day)?|vigil)\.?\s*)?\(?\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?\)?\s*"
                        r"(?:mass|misa)?(?:\s*\((?:sat|sun|vigil|sp|span|eng)[a-z.]*\))?$|^(?:mass|misa)\s+\d", re.I)


def trim_label(lab: str) -> str:
    """Cut words from a neighbouring column: start at most 3 words before the first finance keyword."""
    m = STRONG_FIN.search(lab)
    if not m:
        return lab
    before = lab[:m.start()].split()
    return " ".join(before[-3:] + [lab[m.start():]]).strip()


def choose_label(r: dict, zone_pages: dict[int, list[tuple[float, float]]]) -> tuple[str, str] | None:
    """Return (label, category) for a money row worth keeping, else None. Never returns non-finance text."""
    in_zone = any(0 <= r["y"] - y <= 160 and abs(r["x"] - x) < 260 for (y, x) in zone_pages.get(r["page"], []))
    lab = (r["label"] or "").strip()
    if lab and (LABEL_JUNK.search(lab) or PRIVATE_RE.search(lab)):
        return None
    if lab and STRONG_FIN.search(lab):
        t = trim_label(lab)
        return t, fin_category(t)
    tail = " ".join(lab.split()[-4:])
    if in_zone and MASS_LABEL.search(tail):
        return tail, "offertory_by_mass"
    above = (r["label_above"] or "").strip()
    if above and not LABEL_JUNK.search(above):
        t = trim_label(above)
        return t, fin_category(t)
    return None


# ---------------------------------------------------------------- text helpers
def lines_of(page_text: str) -> list[str]:
    return [re.sub(r"\s+", " ", ln).strip() for ln in page_text.split("\n") if ln.strip()]


def private_mask(lines: list[str]) -> list[bool]:
    """True for lines inside a prayer-list / deceased / intentions block (heading + following lines)."""
    mask = [False] * len(lines)
    i = 0
    while i < len(lines):
        if PRIVATE_RE.search(lines[i]):
            j = i
            mask[j] = True
            # extend while lines look like name lists or until a blank heading-like break (max 25 lines)
            k = j + 1
            while k < len(lines) and k - j <= 25:
                ln = lines[k]
                if MINISTRY_TERMS.search(ln) and len(ln) < 60 and not re.search(r",.*,", ln):
                    break
                if re.match(r"^(?:mass schedule|horario|parish staff|staff|ministries|upcoming|events|"
                            r"collection|offertory|stewardship)\b", ln, re.I):
                    break
                mask[k] = True
                k += 1
            i = k
        else:
            i += 1
    return mask


DAY_NORM = {"domingo": "Sunday", "lunes": "Monday", "martes": "Tuesday", "miercoles": "Wednesday",
            "miércoles": "Wednesday", "jueves": "Thursday", "viernes": "Friday", "sabado": "Saturday",
            "sábado": "Saturday", "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
            "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "fri": "Friday", "sat": "Saturday",
            "sun": "Sunday", "weekdays": "Weekday", "weekday": "Weekday", "daily": "Daily",
            "first friday": "First Friday", "first saturday": "First Saturday"}


def parse_time(tm: re.Match) -> str | None:
    if tm.group(0).lower() == "noon":
        return "12:00"
    h, m, ap = int(tm.group(1)), tm.group(2) or "00", tm.group(3).lower()
    if h > 12 or int(m) > 59:
        return None
    return f"{(h % 12) + (12 if ap == 'p' else 0):02d}:{m}"


def schedules(pages: list[str]) -> dict[str, list[dict]]:
    out = {"mass": [], "confession": [], "adoration": []}
    seen = set()
    for pno, ptxt in enumerate(pages[:3], 1):  # schedules sit on the cover/second page
        lines = lines_of(ptxt)
        block, day = None, None
        for i, ln in enumerate(lines):
            if len(ln) > 160:
                block = None
                continue
            low = re.sub(r"(?:before|after|following|prior to|antes de|despu[eé]s de)\s+(?:the\s+|each\s+|la\s+|las\s+)?"
                         r"(?:\w+\s+)?(?:mass(?:es)?|misas?)", " ", ln.lower())
            explicit = None
            if re.search(r"confession|reconciliation|penance|confesi[oó]n|confesiones|reconciliaci[oó]n", low):
                explicit = "confession"
            elif re.search(r"adoration|adoraci[oó]n|holy hour|hora santa|exposition", low):
                explicit = "adoration"
            elif re.search(r"\bmass(?:es)?\b|\bmisas?\b|\bvigil\b|liturgy|weekend|weekday|daily|horario", low):
                explicit = "mass"
            other = re.search(r"office hours|horas de oficina|rosary|rosario|novena|baptism|bautismo|wedding|"
                              r"matrimony|quincea|funeral|anointing|meeting|class|choir|practice|bingo|dinner", low)
            is_heading = len(ln) <= 45 and not TIME_RE.search(ln)
            if is_heading:
                if explicit:
                    block = explicit
                elif other:
                    block = None
                dm = DAYS_RE.search(ln)
                if dm:
                    day = DAY_NORM.get(dm.group(1).lower().rstrip("."), dm.group(1).title())
                continue
            if other and not explicit:
                continue
            kind = explicit or block
            dm = DAYS_RE.search(ln)
            if dm:
                day = DAY_NORM.get(dm.group(1).lower().rstrip("."), dm.group(1).title())
            if not kind:
                continue
            ms = list(TIME_RE.finditer(ln))
            skip = set()
            times = []
            for j, tm in enumerate(ms):  # "6pm-7pm" / "3 to 4pm" -> one range
                if j in skip:
                    continue
                t = parse_time(tm)
                if not t:
                    continue
                if j + 1 < len(ms) and re.fullmatch(r"\s*(?:-|–|—|to|a|until|hasta)\s*", ln[tm.end():ms[j + 1].start()], re.I):
                    t2 = parse_time(ms[j + 1])
                    if t2:
                        t = f"{t}-{t2}"
                        skip.add(j + 1)
                times.append(t)
            for t in times:
                lang = next((L for L in LANGS if re.search(r"\b" + re.escape(L) + r"\b", ln, re.I)), None)
                key = (kind, day, t, lang)
                if key in seen:
                    continue
                seen.add(key)
                out[kind].append({"day": day, "time": t, "language": lang, "page": pno,
                                  "line": words15(scrub(ln) or ln, 12)})
    return out


PERSON_RE = re.compile(r"^(?:(?:Mr|Mrs|Ms|Dr|Fr|Rev|Deacon|Dcn|Sr|Br)\.?\s+)?[A-Z][a-z'\-]+(?:\s+[A-Z]\.?)?"
                       r"(?:\s+(?:de |del |de la |van |von )?[A-Z][a-zA-Z'\-]+){1,2}$")
GENERIC_MIN = re.compile(r"(?i)^(?:come )?(?:welcome(?: those)?|ministries(?: & .*| and .*)?|our ministries|"
                         r"parish ministries|church ministries|ministerios|get involved|organizations?)$")
PERSON_START_RE = re.compile(r"^[A-Z][a-z'\-]+\s+[A-Z][a-z'\-]+,\s")


def ministries(pages: list[str]) -> list[dict]:
    out, seen = [], set()
    for pno, ptxt in enumerate(pages, 1):
        lines = lines_of(ptxt)
        mask = private_mask(lines)
        for ln, priv in zip(lines, mask):
            if priv or len(ln) > 90 or not MINISTRY_TERMS.search(ln):
                continue
            seg = re.split(r"\s[|•·–—]\s|:\s|\s{2,}|\(|\d{3}[-.)]|@|https?://|www\.", ln)[0]
            # "Jane Doe, Chair, Parish Council" -> keep only comma parts that name the ministry
            seg = re.sub(r"[.…_·]{2,}.*$", "", seg)  # dot leaders run into phone numbers
            parts = [x.strip() for x in re.split(r",|\s/\s|\s-\s", seg)]
            if len(parts) > 1:
                parts = [x for x in parts if MINISTRY_TERMS.search(x) and not PERSON_RE.match(x)]
                seg = parts[0] if parts else ""
            if PERSON_START_RE.match(seg) or re.search(r"=|\bI am\b|\bI'm\b|\bwho\b|\bwould\b|looking for", seg):
                continue
            if re.search(r"\.\s+[A-Z]", seg) or GENERIC_MIN.match(seg.strip()) or \
                    len(re.findall(r"\b(?:the|that|and|to|for|of|our|your|a|an|is|are|go|supports?|sustains?)\b",
                                   seg, re.I)) > 2:
                continue
            seg = scrub(seg)
            if not seg or not MINISTRY_TERMS.search(seg) or len(seg) < 4 or len(seg.split()) > 9:
                continue
            if re.search(r"\b(?:please|join us|will|we |our |you |this |if |is |are |was |were |has |have )", seg, re.I):
                continue
            if re.match(r"^[a-z]", seg) or seg.endswith((",", " of", " the", " and", " for")):
                continue
            key = re.sub(r"[^a-z]", "", seg.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": seg, "page": pno})
    return out[:80]


def find_event_date(ln: str, bdate: date) -> date | None:
    low = ln.lower()
    for m in re.finditer(MON_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)", low):
        mon = m.group(1)
        mi = ES_MON.get(mon) or next((i for i, M in enumerate(MONTHS, 1) if M.startswith(mon[:3])), None)
        if not mi:
            continue
        for y in (bdate.year, bdate.year + 1):
            try:
                d = date(y, mi, int(m.group(2)))
            except ValueError:
                continue
            if bdate - timedelta(days=1) <= d <= bdate + timedelta(days=150):
                return d
    m = re.search(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])", ln)
    if m:
        y = int(m.group(3)) if m.group(3) else bdate.year
        y = y + 2000 if y < 100 else y
        for yy in (y, y + 1):
            try:
                d = date(yy, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
            if bdate - timedelta(days=1) <= d <= bdate + timedelta(days=150):
                return d
    return None


def events(pages: list[str], bdate: date) -> list[dict]:
    out, seen = [], set()
    for pno, ptxt in enumerate(pages, 1):
        lines = lines_of(ptxt)
        mask = private_mask(lines)
        for i, (ln, priv) in enumerate(zip(lines, mask)):
            if priv or len(ln) > 200:
                continue
            d = find_event_date(ln, bdate)
            if not d:
                continue
            ctx = ln if EVENT_TERMS.search(ln) else " ".join(lines[max(0, i - 1):i + 2])
            if not EVENT_TERMS.search(ctx) or PRIVATE_RE.search(ctx) or NAMES_LIST.search(ctx):
                continue
            title = scrub(ctx)
            if len(title) < 8:
                continue
            key = (d.isoformat(), re.sub(r"[^a-z]", "", title.lower())[:40])
            if key in seen:
                continue
            seen.add(key)
            out.append({"date": d.isoformat(), "title": words15(title, 14), "page": pno})
    return out[:60]


def volunteer_needs(pages: list[str]) -> list[dict]:
    out, seen = [], set()
    for pno, ptxt in enumerate(pages, 1):
        lines = lines_of(ptxt)
        mask = private_mask(lines)
        for i, (ln, priv) in enumerate(zip(lines, mask)):
            if priv or not VOLUNTEER_RE.search(ln):
                continue
            ctx = ln if len(ln) > 40 else " ".join(lines[i:i + 2])
            if PRIVATE_RE.search(ctx) or NAMES_LIST.search(ctx) or re.search(
                    r"online giving|give online|text to give|scan the qr|donate (?:to|through) the (?:church|parish)|"
                    r"regularly donate|thank you for (?:your )?(?:giving|generosity|donations?)|e-?giving|"
                    r"second collection|segunda colecta|parish directory|to donate now|donate online|"
                    r"weshare|osv|givelify|faith direct|pushpay|vanco|venmo|paypal|zelle", ctx, re.I):
                continue
            txt = scrub(ctx)
            if len(txt) < 12:
                continue
            key = re.sub(r"[^a-z]", "", txt.lower())[:50]
            if key in seen:
                continue
            seen.add(key)
            kind = ("volunteers" if re.search(r"volunt|help|sign[- ]?up|need(?:s|ed)? (?:help|more)", txt, re.I)
                    else "donations")
            out.append({"kind": kind, "text": words15(txt), "page": pno})
    return out[:40]


def sacramental_counts(pages: list[str]) -> list[dict]:
    out = []
    for pno, ptxt in enumerate(pages, 1):
        text = re.sub(r"\s+", " ", ptxt)
        for m in SACRAMENT_RE.finditer(text):
            n = m.group("n").lower()
            num = int(n) if n.isdigit() else WORDNUM.get(n)
            if not num or num > 2000:
                continue
            pre = text[max(0, m.start() - 6):m.start()]
            if "$" in pre or re.search(r"\d[:/]$", pre.strip()):
                continue
            sac = m.group("sac").lower()
            norm = ("baptism" if "bapti" in sac or "bauti" in sac else
                    "first_communion" if "communion" in sac or "comuni" in sac else
                    "confirmation" if "confirm" in sac else
                    "marriage" if re.search(r"marri|wedding|matrimon", sac) else
                    "funeral" if "funeral" in sac else
                    "anointing" if "anoint" in sac else "christian_initiation")
            out.append({"sacrament": norm, "count": num, "who": m.group("what").lower(),
                        "context": words15(m.group(0), 12), "page": pno})
    return out[:20]


PAREN_STAFF = re.compile(r"^((?:(?:Rev|Fr|Msgr|Mr|Mrs|Ms|Dr|Deacon|Dcn|Sr|Br)\.?\s+){0,3}[A-Z][\w'\-.]+(?:\s+[A-Z][\w'\-.]+){1,4})"
                         r"(?:\s+(?:OFM|O\.?F\.?M|OFC|OP|O\.P\.|SJ|S\.J\.|CSC|SDB)\.?\s*(?:CONV|Conv)?\.?)?\s*\(([^)]{3,50})\)$")


def staff_from(pages: list[str]) -> list[dict]:
    from parish_sites import TITLE_RE
    out, seen = [], set()
    for pno, ptxt in enumerate(pages, 1):
        lines = lines_of(ptxt)
        mask = private_mask(lines)
        clean = [scrub(ln) if not priv else "" for ln, priv in zip(lines, mask)]
        clean = [c for c in clean if c]
        extra = []
        for ln in clean:  # "Rev. Fr. Lazaro Sandoval OFM. CONV (Pastor)"
            m = PAREN_STAFF.match(ln)
            if m and TITLE_RE.search(m.group(2)):
                nm = re.sub(r"(?i)\s+(?:OFM|OFC|O\.?P|S\.?J|CSC|SDB)\.?(?:\s*Conv\.?)?$", "", m.group(1).strip())
                extra.append({"name": nm, "title": m.group(2).strip()})
        for s in extra + parse_staff(clean):
            k = s["name"].lower()
            if k in seen or len(s["name"].split()) > 5:
                continue
            if re.search(r"\b(?:Rev|Fr|Msgr|Father|Deacon|Dcn)\.?\s+[A-Z]", s["title"]) or len(s["title"]) > 70:
                continue
            if s["name"].isupper() or not re.search(r"[a-z]", s["name"]) or \
                    re.search(r"(?i)letter|message|from our|made easy|\d{5,}", s["title"] + " " + s["name"]):
                continue
            seen.add(k)
            title = re.sub(r"[.…_·]{2,}.*$", "", s["title"])
            title = PHONE_RE.sub("", title).split("|")[-1].strip(" .,-–|")
            title = re.sub(r"\s*\d{3}[-.]\d{4}\b", "", title).strip()
            if not title:
                continue
            out.append({"name": s["name"], "title": words15(title, 8), "page": pno})
    return out[:40]


def bma_text(pages: list[str]) -> list[dict]:
    """Percent-of-goal and participant counts printed near Bishop's Ministries Appeal mentions."""
    out = []
    for pno, ptxt in enumerate(pages, 1):
        lines = lines_of(ptxt)
        for i, ln in enumerate(lines):
            if not re.search(r"bishop[’']?s ministr|\bbma\b|bishop[’']?s appeal|campa[ñn]a (?:anual )?del obispo|"
                             r"ministerios del obispo", ln, re.I):
                continue
            win = " ".join(lines[i:i + 8])
            for m in PCT_RE.finditer(win):
                out.append({"metric": "percent", "value": float(m.group(1)), "page": pno,
                            "context": words15(win[max(0, m.start() - 60):m.end() + 30], 12)})
            for m in re.finditer(r"(\d{1,4})\s+(families|households|donors|parishioners|participants|"
                                 r"pledges|gifts|familias|donantes)", win, re.I):
                out.append({"metric": m.group(2).lower(), "value": int(m.group(1)), "page": pno,
                            "context": words15(win[max(0, m.start() - 40):m.end() + 30], 12)})
            for m in re.finditer(r"(goal|meta|pledged|promised|paid|received|raised|collected|remaining|"
                                 r"balance|assessment|to go)\D{0,25}(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
                                 win, re.I):
                v = money_value(m.group(2))
                if v is None or v < 50:
                    continue
                out.append({"metric": m.group(1).lower(), "value": v, "page": pno,
                            "context": words15(m.group(0), 10)})
            for m in re.finditer(r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s{0,2}(pledged|paid|raised|"
                                 r"collected|remaining|to go)\b", win, re.I):
                out.append({"metric": m.group(2).lower(), "value": money_value(m.group(1)), "page": pno,
                            "context": words15(m.group(0), 10)})
    # dedupe
    seen, ded = set(), []
    for x in out:
        k = (x["metric"], x["value"], x["page"])
        if k not in seen:
            seen.add(k)
            ded.append(x)
    return ded[:20]


TEXT_AMT = re.compile(r"\(?\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\)?")


def text_money_rows(pages: list[str]) -> list[dict]:
    """Fallback for OCR'd or text-only bulletins: 'label ... $amount' on one line."""
    out = []
    for pno, ptxt in enumerate(pages, 1):
        if not MONEY_PAGE_RE.search(ptxt):
            continue
        for ln in lines_of(ptxt):
            ms = list(TEXT_AMT.finditer(ln))
            if not ms or len(ln) > 140 or PRIVATE_RE.search(ln) or LABEL_JUNK.search(ln):
                continue
            label = re.sub(r"[.…_·|\]\[]{1,}", " ", ln[:ms[0].start()]).strip(" :-–")
            if not STRONG_FIN.search(label):
                continue
            label = trim_label(re.sub(r"\s+", " ", label))
            amts = [m.group(0).replace(" ", "") for m in ms[:4]]
            if max((abs(money_value(a) or 0) for a in amts), default=0) < 50:
                continue
            out.append({"label": words15(label, 12), "category": fin_category(label), "amounts": amts,
                        "values": [money_value(a) for a in amts], "headers": None, "page": pno,
                        "method": "text_line"})
    return out[:40]


def parse_one(entry: dict, pid: str) -> dict:
    tp = ROOT / entry["text_path"]
    text = tp.read_text(encoding="utf-8", errors="ignore")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    bdate = date.fromisoformat(entry["date"]) if entry.get("date") else None
    rec = {"date": entry.get("date"), "source_url": entry["url"], "found_on": entry.get("found_on"),
           "shared_from": entry.get("shared_from"),
           "fetched_at": entry.get("fetched_at"), "pages": len(pages), "ocr": entry.get("ocr", False),
           "name_match": entry.get("name_match")}
    # money
    fin = []
    if entry.get("pdf_path") and (ROOT / entry["pdf_path"]).exists():
        money_pages = [i for i, p in enumerate(pages, 1) if MONEY_PAGE_RE.search(p) and "$" in p]
        rows = finance_rows(ROOT / entry["pdf_path"], money_pages[:6])
        zones: dict[int, list[tuple[float, float]]] = {}
        # finance headings: rows don't carry them, so look for heading words on those pages
        try:
            with pdfplumber.open(str(ROOT / entry["pdf_path"])) as doc:
                for pno in money_pages[:6]:
                    for w in doc.pages[pno - 1].extract_words():
                        if re.match(r"(?i)^(offertory|stewardship|collections?|colectas?|ofrendas?|weekly|financial|"
                                    r"giving|update|sacrificial|corresponsabilidad)\b", w["text"]):
                            zones.setdefault(pno, []).append((w["top"], w["x0"]))
        except Exception:  # noqa: BLE001
            pass
        for r in rows:
            ch = choose_label(r, zones)
            if not ch:
                continue
            lab, cat = ch
            vals = [v for v in r["values"] if v is not None]
            if not vals or max(abs(v) for v in vals) < 50:  # $5 forms, "$24" cut off by a line wrap, etc.
                continue
            ctx = " ".join([r["label_above"] or ""] + [h or "" for h in (r["headers"] or [])])
            if cat in ("other", "goal_or_budget", "offertory") and re.search(
                    r"bishop[’']?s (?:ministr|appeal|annual)|\bbma\b|campa[ñn]a del obispo", ctx, re.I):
                cat = "bma"
            lab = re.sub(r"\s+", " ", PHONE_RE.sub("", EMAIL_RE.sub("", lab))).strip(" :|-–")
            heads = [words15(h, 6) if h else None for h in (r["headers"] or [])] or None
            fin.append({"label": words15(lab, 12), "category": cat, "amounts": r["amounts"],
                        "values": r["values"], "headers": heads, "page": r["page"]})
    if not fin:
        fin = text_money_rows(pages)
    rec["offertory"] = [f for f in fin if f["category"] != "bma"]
    rec["bma"] = [f for f in fin if f["category"] == "bma"] + bma_text(pages)
    rec["schedules"] = schedules(pages)
    rec["ministries"] = ministries(pages)
    rec["staff"] = staff_from(pages)
    rec["events"] = events(pages, bdate) if bdate else []
    rec["volunteer_needs"] = volunteer_needs(pages)
    rec["sacramental_counts"] = sacramental_counts(pages)
    rec["second_collections"] = second_collections(pages, bdate) if bdate else []
    screen_bulletin_record(rec)
    return rec


def second_collections(pages: list[str], bdate: date) -> list[dict]:
    """Scheduled second/special collections: date + purpose (e.g. 'Oct. 18: Home and World Missions')."""
    out, seen = [], set()
    for pno, ptxt in enumerate(pages, 1):
        lines = lines_of(ptxt)
        for i, ln in enumerate(lines):
            if not re.search(r"second (?:and special )?collections?|special collections?|segundas? colectas?|"
                             r"colectas? especial", ln, re.I):
                continue
            for nxt in [ln] + lines[i + 1:i + 8]:
                d = find_event_date(nxt, bdate - timedelta(days=14))
                if not d:
                    continue
                purpose = re.sub(MON_RE + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?[:,.\-–]?", "", nxt, flags=re.I)
                purpose = re.sub(r"^(?:second|special|2nd)?\s*collections?\s*[:\-–]?", "", purpose.strip(), flags=re.I)
                purpose = scrub(purpose).strip(" :-–")
                if len(purpose) < 4 or PRIVATE_RE.search(purpose):
                    continue
                k = (d.isoformat(), purpose.lower()[:30])
                if k in seen:
                    continue
                seen.add(k)
                out.append({"date": d.isoformat(), "purpose": words15(purpose, 10), "page": pno})
    return out[:12]


def is_bulletin(entry: dict) -> bool:
    if not entry.get("ok") or not entry.get("date"):
        return False
    d = date.fromisoformat(entry["date"])
    return d >= date(2026, 1, 1) and (entry.get("pages") or 0) >= 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    ids = set(filter(None, args.ids.split(",")))
    manifest = load_json(MANIFEST)["records"]
    directory = {r["id"]: r for r in load_json(REAL / "parishes_directory.json")["records"]}
    manual = load_json(Path(__file__).resolve().parent / "bulletin_manual.json", {}).get("parishes", {})
    shared = {pid: m["shared_bulletin_of"] for pid, m in manual.items() if m.get("shared_bulletin_of")}
    for pid, other in shared.items():  # clustered parishes that publish one joint bulletin
        if not any(e.get("ok") for e in manifest.get(pid, [])) and other in manifest:
            manifest[pid] = [dict(e, shared_from=other) for e in manifest[other]]
    OUTDIR.mkdir(parents=True, exist_ok=True)
    for pid, entries in sorted(manifest.items()):
        if ids and pid not in ids:
            continue
        bl = []
        skipped = []
        for e in entries:
            if not is_bulletin(e):
                if e.get("ok"):
                    skipped.append({"url": e["url"], "date": e.get("date"), "reason": "undated, pre-2026 or not a bulletin"})
                continue
            try:
                bl.append(parse_one(e, pid))
            except Exception as ex:  # noqa: BLE001
                skipped.append({"url": e["url"], "date": e.get("date"), "reason": f"parse error {type(ex).__name__}"})
        bl.sort(key=lambda r: r["date"] or "", reverse=True)
        out = {"id": pid, "name": directory.get(pid, {}).get("name"), "generated_at": now_iso(),
               "method": "pdftotext + pdfplumber word positions; see ingest/public/enrichment/parse_bulletins.py",
               "bulletins": bl, "skipped": skipped}
        save_json(OUTDIR / f"{pid}.json", out)
        n_off = sum(bool(b["offertory"]) for b in bl)
        print(f"{pid:48s} bulletins={len(bl)} with_offertory={n_off} bma={sum(bool(b['bma']) for b in bl)} "
              f"mass={sum(len(b['schedules']['mass']) for b in bl[:1])} min={len(bl[0]['ministries']) if bl else 0} "
              f"staff={len(bl[0]['staff']) if bl else 0} ev={len(bl[0]['events']) if bl else 0}", flush=True)


if __name__ == "__main__":
    main()
