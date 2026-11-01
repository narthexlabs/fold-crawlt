"""Groups from California parish bulletins, informal groups above all (rule-based; no LLM).

    python3 -I ingest/california/bulletins/extract_groups.py [--slugs a,b] [--evidence]

Reads the issues ingest/california/bulletins/fetch.py downloaded (data/raw/california/bulletins/_state/<slug>/<site>.json
and the .txt / .ocr.txt next to each PDF) and writes data/real/california/bulletins/<slug>.json, one entry per parish.

Why bulletins: parish websites mostly list formal ministries (lectors, ushers, choir). The groups a newcomer looks for
(a men's group, moms, young adults, a Bible study, a prayer group) are announced week by week in the bulletin.

How a group is found
  1. Text: pdftotext pages ("\\f"); image-only pages were OCR'd by fetch.py (tesseract, flagged `ocr`).
  2. Skipped before anything is read: pages that are mostly advertising (LPi back pages: dentists, insurance, mortuaries,
     realtors...), and every private section, from its heading until the next heading or 40 lines: Mass intentions,
     prayer lists, the sick, the deceased, banns, baptisms, sanctuary lamp / memorial offerings, donor lists, birthdays,
     military lists, in Spanish/Vietnamese/Korean/Tagalog too. Single lines are dropped too when either privacy screen
     calls them a Mass intention ("Mass for <Name>"), a list of names, or a line about the dead or the sick.
  3. Informal-group vocabulary (VOCAB below: men's, women's, moms, young adults, youth, seniors, Bible study, small
     groups, prayer / charismatic, rosary, Legion of Mary, Cursillo, Knights of Columbus council #, Columbiettes,
     Catholic Daughters, SVdP, grief, divorced & separated, Alpha, OCIA inquiry, couples; English, Spanish, Vietnamese,
     Korean, Tagalog). A match on a line about another place (the diocese, a deanery) or an ad is not a parish group.
  4. Formal ministries: a short heading-like line that the groups taxonomy (ingest/public/groups/build_groups.classify)
     maps to a category with a specific rule (Lectors, Altar Society, Ministry to the Sick...).
  5. Meeting day / time / frequency only from the announcement line (text after the group's name) and the next two lines
     of the same block (stopping at another group or a heading). Nothing is filled in when the bulletin does not say it.
     A one-off date becomes `next_meeting`, not a recurring time.
  6. Place in the parish (hall, room, chapel, library, salón parroquial...) from the same window.
  7. Language: the vocabulary term's language, else the language of the announcement, else English (flagged assumed).

Privacy (hard rule; CLAUDE.md): no lay names, phones or e-mails are stored. A group's contact is "ask the parish office".
Group names are cut before the first name the shared screen (ingest/public/privacy.py name_spans) sees, then must pass
privacy.redact (0 names), build_groups.private_name_risk, and the calendar connector's names_private_person and
mass_for_person (ingest/connectors/calendars/privacy.py); a name that fails falls back to the taxonomy's generic name.
Locations pass redact too or are dropped. No text snippet is written to data/real; `--evidence` writes the source lines
(for spot checks) under data/raw/california/bulletins/_evidence/ (gitignored).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "public"))
sys.path.insert(0, str(ROOT / "ingest" / "public" / "groups"))
import privacy  # noqa: E402  (ingest/public/privacy.py: redact, name_spans, is_intention_line, strip_contacts)
import build_groups as bg  # noqa: E402  (taxonomy: classify, CATEGORIES, clean_name, smart_title)

_spec = importlib.util.spec_from_file_location("cal_privacy", ROOT / "ingest" / "connectors" / "calendars" / "privacy.py")
cal_privacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal_privacy)

RAW = ROOT / "data" / "raw" / "california" / "bulletins"
STATE = RAW / "_state"
EVID = RAW / "_evidence"
OUT = ROOT / "data" / "real" / "california" / "bulletins"
MERGED = ROOT / "data" / "real" / "california" / "merged"
SLUGS = ["los-angeles", "san-francisco", "fresno", "monterey", "orange", "sacramento", "san-bernardino", "san-diego",
         "san-jose", "santa-rosa", "stockton"]
CONTACT = "ask the parish office"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold(s: str) -> str:
    """Lowercase, accents off (Spanish/Vietnamese), curly quotes straight. Hangul is kept as is (NFC)."""
    s = (s or "").replace("’", "'").replace("‘", "'").replace("đ", "d").replace("Đ", "D")
    out = []
    for ch in s:
        if "가" <= ch <= "힣" or "㄰" <= ch <= "㆏":
            out.append(ch)
        else:
            out.append("".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)))
    return "".join(out).lower()


# ------------------------------------------------------------------------------------------------ vocabulary
# (pattern on folded text, key, category, subcategory, audience, language or None, display, informal kind)
# Patterns written CASE: are matched on the original text, case-sensitive (acronyms).
V = [
    # men
    (r"\bthat man is you\b|CASE:\bTMIY\b", "tmiy", "men", "that_man_is_you", "men", None, "That Man Is You", "mens"),
    (r"\bexodus ?90\b|CASE:\bE90\b", "exodus90", "men", "exodus_90", "men", None, "Exodus 90", "mens"),
    (r"\bmen'?s (?:group|fellowship|ministry|bible study|bible|prayer group|prayer breakfast|breakfast group|club|"
     r"faith (?:group|sharing)|spirituality|study|small groups?|schola|bible group|rosary|prayer|morning prayer)\b|"
     r"\bcatholic men'?s? (?:fellowship|group|ministry)\b|\bmen of (?:st\.? |saint )joseph\b|\bmen of (?:faith|christ|god)\b|"
     r"\bgrupo de hombres\b|\bhombres (?:de fe|catolicos|de dios|de valor|en cristo|de san jose)\b|"
     r"\bministerio de (?:hombres|caballeros)\b|\bservants of the lord\b|\b형제회\b|\bkalalakihan\b",
     "mens_group", "men", "mens_group", "men", None, "Men's Group", "mens"),
    # women
    (r"\bwalking with purpose\b", "wwp", "scripture_study", "bible_study", "women", None, "Walking with Purpose", "womens"),
    (r"\bwomen'?s (?:group|fellowship|ministry|bible study|bible|prayer group|club|guild|faith (?:group|sharing)|"
     r"spirituality|study|small groups?|circle|council|society|cenacle|rosary|prayer|morning prayer|book club)\b|"
     r"\bcatholic women'?s (?:group|club|fellowship|league|council)\b|\bcouncil of catholic women\b|\bccw\b|"
     r"\bladies'? (?:guild|club|aid|auxiliary|society|group|fellowship|ministry)\b|\bladies of charity\b|"
     r"\bblessed is she\b|\bendow (?:study|group)\b|\bwomen of (?:grace|faith)\b|\bhandmaids of the lord\b|"
     r"\bgrupo de (?:mujeres|damas|senoras)\b|\bmujeres (?:de fe|catolicas|en cristo|de dios|de la parroquia)\b|"
     r"\bministerio de (?:mujeres|damas)\b|\bdamas (?:de la caridad|catolicas)\b|\b자매회\b|\bkababaihan\b",
     "womens_group", "women", "womens_group", "women", None, "Women's Group", "womens"),
    (r"\bchrist renews his parish\b|CASE:\bCRHP\b|\bcristo renueva su parroquia\b", "crhp", "movements",
     "christ_renews_his_parish", "all", None, "Christ Renews His Parish", "crhp"),
    # moms
    (r"CASE:\bMOPS\b|\bmothers of preschoolers\b|\bmoms?'? (?:group|ministry|club|fellowship|bible study|prayer group|"
     r"night out|morning out|connect|circle|in prayer)\b|\bmothers'? (?:group|club|ministry|prayer group|circle|"
     r"in prayer)\b|\bmothers prayers\b|\bcatholic moms?\b|\bmoms? (?:&|and|n) (?:tots|toddlers|me|babies)\b|"
     r"\bmommy (?:&|and) me\b|\bgrupo de (?:mamas|madres)\b|\bmadres (?:catolicas|en oracion)\b|\bmamas catolicas\b|"
     r"\bhoi (?:cac )?ba me (?:cong giao|ctvn)\b|\b자모회\b",
     "moms_group", "families", "moms_group", "families", None, "Moms' Group", "moms"),
    # young adults
    (r"\byoung adults?'? (?:group|ministry|ministries|fellowship|community|night|nights|bible study|faith|prayer|social|"
     r"gathering|council|catholics?|of)\b|\byoung adult\b(?= ministry)|\bjovenes adultos\b|"
     r"\b(?:20|twenty)s (?:&|and|y) (?:30|thirty)s\b|\byoung (?:catholic )?professionals\b|\bsingles for christ\b|"
     r"\b청년회\b", "young_adults", "young_adults", "young_adult_group", "young_adult", None, "Young Adult Group",
     "young_adults"),
    # youth
    (r"\byouth (?:group|ministry|ministries|night|fellowship|minister|program|nights)\b|\blife ?teen\b|"
     r"\bedge (?:middle school|youth|night|ministry)\b|\b(?:high|middle) school (?:youth )?(?:group|ministry)\b|"
     r"\bjr\.? high (?:group|ministry)\b|\bteen (?:group|ministry|night|bible study)\b|\bgrupo (?:de )?jovenes\b|"
     r"\bgrupo juvenil\b|\bpastoral juvenil\b|\bministerio (?:de )?(?:jovenes|juvenil)\b|\bjovenes para cristo\b|"
     r"\bthieu nhi thanh the\b|\bgioi tre\b|\byouth for christ\b|\bkabataan\b|\b중고등부\b",
     "youth_ministry", "youth", "youth_ministry", "youth", None, "Youth Ministry", "youth"),
    # seniors
    (r"(?<!school )\bseniors? (?:group|club|ministry|fellowship|citizens|social|connection|circle|lunch)\b|"
     r"\bsenior adults? (?:group|ministry)\b|\bgolden (?:agers|friends|circle)\b|\bkeenagers\b|\bprime ?timers\b|"
     r"\byoung at heart\b|\btercera edad\b|\badultos mayores\b|\bhoi cao nien\b|\b노인대학\b|\b시니어\b",
     "seniors", "seniors", "seniors_group", "seniors", None, "Seniors Group", "seniors"),
    # scripture
    (r"\bbible (?:study|studies|class|classes|timeline|discussion|sharing|group)\b|\bscripture (?:study|studies|group|"
     r"class|sharing)\b|\bgreat adventure bible\b|\bascension(?:'s)? (?:presents|bible)\b|\bbible in a year (?:group|study)\b|"
     r"\bformed (?:study|group|bible)\b|\bestudios? biblicos?\b|\bcirculo biblico\b|\bgrupo biblico\b|"
     r"\bclases? de biblia\b|\bhoc hoi kinh thanh\b|\b성경 ?공부\b|\b성서 ?(?:공부|모임)\b|\bpag-?aaral ng bibliya\b",
     "bible_study", "scripture_study", "bible_study", "all", None, "Bible Study", "bible_study"),
    (r"\bsmall (?:faith |christian |prayer |church |discipleship )?groups?\b|\bsmall christian communit(?:y|ies)\b|"
     r"\bfaith[- ]sharing(?: group| groups| community)?\b|\bpequenas comunidades\b|\bcomunidades eclesiales de base\b|"
     r"\bgrupos pequenos\b|\bgrupos? de reflexion\b|\bdiscipleship (?:groups?|quads?)\b|\bemmaus (?:group|ministry)\b",
     "small_groups", "scripture_study", "small_faith_groups", "all", None, "Small Faith Groups", "small_groups"),
    # prayer
    (r"\bcharismatic (?:prayer|renewal|group|community)\b|\bcatholic charismatic\b|\brenovacion carismatica\b|"
     r"\bgrupo (?:de oracion )?carismatico\b|\bholy spirit prayer group\b|\blife in the spirit seminar\b|"
     r"\bseminario de vida en el espiritu\b|\bpraise (?:and|&) worship (?:group|night|prayer)\b|\bel shaddai\b|"
     r"\bcanh tan dac sung\b|\b성령 ?기도회\b",
     "charismatic", "movements", "charismatic_renewal", "all", None, "Charismatic Prayer Group", "charismatic"),
    (r"\bprayer (?:group|groups|circle|meeting|cenacle|warriors|partners|community|chain|line)\b|"
     r"\bintercessory prayer (?:group|ministry|team)\b|\bgrupos? de oracion\b|\breunion de oracion\b|\bcenaculo\b|"
     r"\bcenacle\b|\bdivine mercy (?:group|cenacle|prayer group)\b|\bcentering prayer (?:group)?\b|"
     r"\bcontemplative prayer (?:group)?\b|\blien minh thanh tam\b|\bnhom cau nguyen\b|\b기도회\b|\bgrupo ng panalangin\b",
     "prayer_group", "prayer_devotion", "prayer_group", "all", None, "Prayer Group", "prayer_group"),
    (r"\brosary (?:group|society|ministry|makers|making|club|circle|guild|cenacle|team|apostolate|crusade)\b|"
     r"\bliving rosary\b|\b(?:men|women)'?s rosary\b|\bfamily rosary\b|\brosario (?:de hombres|de mujeres|viviente)\b|"
     r"\bgrupo del? (?:santo )?rosario\b|\bhoi man coi\b|\bpraying the rosary together\b|"
     r"\brosary (?:is prayed|will be prayed|is recited|prayed|recited) (?:every|each|daily|before|after|on)\b|"
     r"\b(?:pray|recite|join us for|join us in praying) the (?:holy )?rosary (?:every|each|on (?:mon|tue|wed|thu|fri|sat|sun))\b|"
     r"\brosary (?:every|each|daily|weekly) (?:mon|tue|wed|thu|fri|sat|sun|day|morning|evening)\b|\brosario (?:todos|cada)\b",
     "rosary_group", "prayer_devotion", "rosary", "all", None, "Rosary Group", "rosary_group"),
    (r"\blegion of mary\b|\blegion de maria\b|\blegio mariae\b|\bpraesidium\b|\bdao binh duc me\b|\b레지오\b|"
     r"\b쁘레시디움\b", "legion", "prayer_devotion", "legion_of_mary", "all", None, "Legion of Mary", "legion_of_mary"),
    (r"\bcursillos?\b|\bcursillistas?\b|\bultreyas?\b|\b꾸르실료\b|\b울뜨레야\b", "cursillo", "movements", "cursillo", "all",
     None, "Cursillo", "cursillo"),
    # fraternal
    (r"\bknights of columbus\b|CASE:\bK ?of ?C\b|CASE:\bKofC\b|\bcaballeros de colon\b|\bhiep si columbus\b|"
     r"\b콜럼버스 기사단\b", "kofc", "fraternal_orders", "knights_of_columbus", "men", None, "Knights of Columbus", "kofc"),
    (r"\bknights of peter claver\b", "kpc", "fraternal_orders", "knights_of_peter_claver", "men", None,
     "Knights of Peter Claver", "fraternal_other"),
    (r"\bcolumbiettes\b", "columbiettes", "fraternal_orders", "columbiettes", "women", None, "Columbiettes",
     "columbiettes"),
    (r"\bcatholic daughters\b|CASE:\bCDA\b(?= court| Court)|\bhijas catolicas\b", "cda", "fraternal_orders",
     "catholic_daughters", "women", None, "Catholic Daughters of the Americas", "catholic_daughters"),
    (r"\byoung ladies'? institute\b|CASE:\bYLI\b", "yli", "fraternal_orders", "young_ladies_institute", "women", None,
     "Young Ladies' Institute", "fraternal_other"),
    (r"\bsociety of (?:st\.?|saint) vincent de paul\b|\b(?:st\.?|saint) vincent de paul (?:society|conference|ministry|"
     r"group|food pantry|pantry|outreach)\b|CASE:\bSVdP\b|CASE:\bSVDP\b|CASE:\bSSVdP\b|\bvincentians?\b|"
     r"\bsociedad de san vicente de paul\b|\bconferencia de san vicente\b|\bsan vicente de paul\b|"
     r"VDP:\b(?:st\.?|saint) vincent de paul\b|\b빈첸시오\b",
     "svdp", "service_mercy", "st_vincent_de_paul", "all", None, "St. Vincent de Paul Society", "svdp"),
    # support
    (r"\bgrief (?:support|share|ministry|group|recovery|sessions?|workshop)\b|\bgriefshare\b|\bbereavement\b|"
     r"\bgrupo de duelo\b|\bministerio de (?:duelo|consolacion)\b|\bapoyo (?:en el|para el) duelo\b|"
     r"\bconsolation ministry\b|\bseasons of hope\b|\bjourney through grief\b|\bwidow(?:s|ers)'? (?:group|support|ministry)\b",
     "bereavement", "grief_support", "bereavement", "all", None, "Grief Support / Bereavement", "grief"),
    (r"\bdivorced (?:&|and|or|/) separated\b|\bseparated (?:&|and|or|/) divorced\b|\bdivorce ?care\b|\bsurviving divorce\b|"
     r"\bdivorce (?:support|recovery|ministry)\b|\bcatholic divorce\b|\bseparados y divorciados\b|"
     r"\bdivorciados y separados\b|\bbeginning experience\b",
     "divorced", "grief_support", "divorced_separated", "all", None, "Divorced & Separated Ministry",
     "divorced_separated"),
    (r"\balpha (?:course|program|series|night|nights|dinner|session|sessions|film|youth|team|weekend|en espanol|"
     r"is back|is coming|starts|begins|returns|will)\b|\b(?:curso|programa) alpha\b|\bjoin us for alpha\b|HEAD:^alpha$",
     "alpha", "formation", "alpha", "all", None, "Alpha", "alpha"),
    (r"CASE:\bOCIA\b|CASE:\bRCIA\b|CASE:\bRICA\b|CASE:\bOICA\b|\border of christian initiation\b|"
     r"\brite of christian initiation\b|\binquiry (?:sessions?|class|classes|night|group|period)\b|"
     r"\binterested in becoming (?:a )?catholic\b|\bthinking (?:about|of) becoming (?:a )?catholic\b|"
     r"\bbecoming catholic\b|\bcatecumenado\b",
     "ocia", "formation", "ocia", "all", None, "OCIA (becoming Catholic)", "ocia_inquiry"),
    # couples
    (r"\bcouples for christ\b|CASE:\bCFC\b|\bmarried couples (?:group|ministry)\b|\bcouples (?:group|ministry|bible study|"
     r"night|prayer group|club)\b|\bmarriage (?:ministry|enrichment)\b|\bteams of our lady\b|\bgrupo de matrimonios\b|"
     r"\bmatrimonios (?:para cristo|unidos|en cristo)\b|\bmovimiento familiar cristiano\b|\bmarriage encounter\b|"
     r"\bencuentro matrimonial\b|\bpareja(?:s)? (?:para|en) cristo\b",
     "couples", "marriage_family", "couples_group", "all", None, "Married Couples Group", "couples"),
]
def split_top(pat: str) -> list[str]:
    """Split a regex on its top-level '|' (not inside a group or a character class)."""
    parts, depth, cur, i, cls = [], 0, [], 0, False
    while i < len(pat):
        c = pat[i]
        if c == "\\":
            cur.append(pat[i:i + 2])
            i += 2
            continue
        if cls:
            cls = c != "]"
        elif c == "[":
            cls = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


VOCAB = []
for pat, *rest in V:
    # plain parts match the folded text; CASE: the original text, case-sensitive (acronyms); VDP: only when the parish
    # is not itself named for St. Vincent de Paul; HEAD: the whole line, when it is a heading
    plain, case, vdp, head = [], [], [], []
    for part in split_top(pat):
        if part.startswith("CASE:"):
            case.append(part[5:])
        elif part.startswith("VDP:"):
            vdp.append(part[4:])
        elif part.startswith("HEAD:"):
            head.append(part[5:])
        else:
            plain.append(part)
    VOCAB.append({"plain": re.compile("|".join(plain)) if plain else None,
                  "case": re.compile("|".join(case)) if case else None,
                  "vdp": re.compile("|".join(vdp)) if vdp else None,
                  "head": re.compile("|".join(head)) if head else None,
                  "key": rest[0], "category": rest[1], "subcategory": rest[2], "audience": rest[3], "lang": rest[4],
                  "display": rest[5], "kind": rest[6]})

TERM_LANG = [   # language of the matched term itself
    ("es", re.compile(r"grupo|hombres|mujeres|damas|senoras|ministerio|jovenes|juvenil|oracion|carismatic|renovacion|"
                      r"biblic|biblia|rosario|legion de maria|caballeros|hijas|vicente|duelo|consolacion|separados|"
                      r"divorciados|matrimonio|pareja|curso|programa|catecumenado|cursillistas|tercera edad|"
                      r"adultos mayores|madres|mamas|pequenas comunidades|comunidades eclesiales|reflexion|cenaculo|"
                      r"escuela|seminario de vida|movimiento|encuentro|cristo renueva|RICA|OICA")),
    ("vi", re.compile(r"\bhoi\b|thieu nhi|gioi tre|kinh thanh|dao binh|man coi|cao nien|lien minh|nhom cau|canh tan|hiep si")),
    ("ko", re.compile(r"[가-힣]")),
    ("tl", re.compile(r"kalalakihan|kababaihan|kabataan|panalangin|bibliya")),
]
ES_WORDS = set("el la los las de del que y en para con por una un es se su sus al nuestra nuestro todos cada martes "
               "miercoles jueves viernes sabado domingo lunes semana mes invita invitamos reunion reuniones salon "
               "despues antes hora horas parroquia iglesia".split())
EN_WORDS = set("the and of to for with is are will our your all every each this that join us at in on from week month "
               "meets meeting monday tuesday wednesday thursday friday saturday sunday parish hall church".split())

# ------------------------------------------------------------------------------------------------ what to skip
PRIV_HEAD = re.compile(
    r"mass intentions?|intentions? (?:for|of) (?:the )?(?:week|mass)|intenciones|intenci[oó]n(?:es)? de (?:la )?misa|"
    r"misas? (?:de la semana|por)|\bý lễ|미사 ?지향|연미사|위령|선종|"
    r"pray(?:ers?)? for (?:the |our )?(?:sick|ill|homebound|deceased|departed|dead|faithful departed|following|those who)|"
    r"please pray for|let us pray for|keep in (?:your|our) prayers|remember in (?:your|our) prayers|"
    r"(?:pray|prayer) for our (?:sick|military|troops|deceased|parishioners)|prayer (?:list|requests?)|sick list|our sick|"
    r"the sick of|for the sick|healing prayer list|oremos por|recemos por|oraciones por (?:los|nuestros)|enfermos|"
    r"recuerden en sus oraciones|c[aầ]u nguy[eệ]n cho|xin c[aầ]u|ipanalangin|"
    r"recently (?:deceased|departed)|rest in peace|eternal rest|in memoriam|in (?:loving )?memory|necrology|"
    r"book of the (?:dead|deceased)|our (?:beloved )?(?:dead|deceased)|difuntos|fallecid[oa]s|descanse en paz|"
    r"q\.?e\.?p\.?d|que en paz descanse|linh h[oồ]n|obituar|funerals? (?:this week|of the week)|"
    r"banns(?: of marriage)?|amonestaciones|proclamas|newly baptized|recently baptized|welcome to (?:our )?(?:newly|the newly)|"
    r"baptisms? (?:this week|celebrated)|bautizos|bautismos (?:de la semana|celebrados)|"
    r"sanctuary (?:lamp|candle)|altar (?:bread|wine|flowers|candles?)|this week'?s (?:candles?|flowers)|lampara del santuario|"
    r"memorial (?:gifts?|donations?|offerings?)|in honor of|donors?\b|honor roll|benefactors|thank you to (?:the following|our)|"
    r"special thanks to|our (?:generous )?(?:donors|sponsors)|"
    r"birthday|anniversar(?:y|ies) (?:this week|blessing)|cumplea[ñn]os|wedding anniversar|"
    r"military (?:prayer )?list|serving (?:in|our) (?:the )?military|our (?:men and women|service members) in", re.I)
PRIV_LINE = re.compile(
    r"[†✟✝]|\bR\.?I\.?P\.?\b|\bdeceased\b|\bpassed away\b|\bdied\b|\bfuneral of\b|\bq\.?e\.?p\.?d\b|\bdifunt|"
    r"\bfallecid|\bin memory of\b|\ben memoria de\b|\bint\.? of\b|\bspecial intention|\bfor the repose\b|"
    r"\bsalud de\b|\bhealth of\b|\brecovery of\b|\bhealing of\b|\bprayers? for the sick\b", re.I)
NAMES_LIST = re.compile(r"(?:\b[A-Z][a-z'’\-]+\s+[A-Z][a-z'’\-]+\s*[,;]\s*){2,}")
AD_RX = re.compile(
    r"\b(?:d\.?d\.?s|dmd|dentist\w*|dental|orthodont\w*|insurance|realt(?:y|or)|real estate|mortgage|plumbing|roofing|"
    r"heating|air conditioning|hvac|mortuary|funeral (?:home|chapel|services?)|attorney|law (?:office|group|firm)s?|cpa|"
    r"tax (?:service|preparation)|auto (?:repair|body|sales)|collision|pharmacy|pizza|restaurant|grill|bakery|catering|"
    r"landscap\w*|construction|chiropract\w*|physical therapy|veterinar\w*|optometr\w*|hearing aids?|senior living|"
    r"assisted living|memory care|home care|caregivers?|advertis\w*|ad space|se habla espa[nñ]ol|lic\.? ?#|state farm|"
    r"edward jones|financial advisor|lpi|parishesonline|smog|tire|towing|salon|barber|spa|flooring|"
    r"painting|remodel\w*|cleaning|movers|storage|notary|immigration law|personal injury)\b", re.I)
AD_LINE = re.compile(r"\b(?:insurance|field agent|general agent|agency|annuit\w*|financial|car donation|"
                     r"donate (?:your|a) (?:car|vehicle)|thrift store|vehicle|realtor|mortuary|dds|dental|lpi)\b", re.I)
ELSEWHERE = re.compile(r"\b(?:diocese of|diocesan|archdiocese|archdiocesan|arquidi[oó]cesis|di[oó]cesis|deanery|"
                       r"decanato|cathedral of|catedral de|regional|region-wide|catholic charities|other parishes|"
                       r"neighboring parish|at (?:st\.?|saint|our lady|holy|sacred|immaculate|san|santa|nuestra)\b)", re.I)
HEADING = re.compile(r"^[^a-z]{3,60}$")      # ALL CAPS / no lowercase: a heading


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 70:
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.8 or (len(s.split()) <= 5 and s.istitle())


# ------------------------------------------------------------------------------------------------ meeting info
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_RX = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day)?s?\b\.?|\b(monday|tuesday|wednesday|thursday|"
                    r"friday|saturday|sunday)s?\b|\b(lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)\b", re.I)
ES_DAY = {"lunes": "monday", "martes": "tuesday", "miercoles": "wednesday", "jueves": "thursday", "viernes": "friday",
          "sabado": "saturday", "sabados": "saturday", "domingo": "sunday", "domingos": "sunday"}
AB_DAY = {"mon": "monday", "tue": "tuesday", "tues": "tuesday", "wed": "wednesday", "thu": "thursday", "thur": "thursday",
          "thurs": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
TIME_RX = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(?:-|–|to|a)?\s*(?:(1[0-2]|0?[1-9])(?::([0-5]\d))?)?\s*"
                     r"(a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])|\b(noon|mediod[ií]a)\b", re.I)
ORD_RX = re.compile(r"\b(1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth|last|primer|segundo|tercer|cuarto|ultimo)\s+"
                    r"(?:(?:and|&|y)\s+(1st|2nd|3rd|4th|first|second|third|fourth|last|tercer|cuarto)\s+)?"
                    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|lunes|martes|miercoles|jueves|viernes|"
                    r"sabado|domingo)s?\b", re.I)
ORD_N = {"1st": "1st", "first": "1st", "primer": "1st", "2nd": "2nd", "second": "2nd", "segundo": "2nd", "3rd": "3rd",
         "third": "3rd", "tercer": "3rd", "4th": "4th", "fourth": "4th", "cuarto": "4th", "5th": "5th", "fifth": "5th",
         "last": "last", "ultimo": "last"}
WEEKLY_RX = re.compile(r"\b(every|each|weekly|todos los|todas las|cada)\b|\b(mon|tues|wednes|thurs|fri|satur|sun)days\b|"
                       r"\b(lunes|martes|miercoles|jueves|viernes|sabados|domingos)\b(?= (?:a las|de))?", re.I)
MONTHLY_RX = re.compile(r"\b(monthly|of (?:the|each|every) month|each month|every month|al mes|del mes|cada mes|"
                        r"mensual)\b", re.I)
BIWEEKLY_RX = re.compile(r"\b(every other|bi-?weekly|every two weeks|cada dos semanas|cada quince dias)\b", re.I)
ONE_DATE_RX = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b|"
                         r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b|\b(\d{1,2}) de (enero|febrero|marzo|abril|mayo|junio|julio|"
                         r"agosto|septiembre|octubre|noviembre|diciembre)\b", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
ES_MON = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
          "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def hhmm(h: str, m: str | None, ap: str) -> str:
    hh = int(h) % 12 + (12 if ap.lower().startswith("p") else 0)
    return f"{hh:02d}:{int(m or 0):02d}"


def nice_time(t: str) -> str:
    h, m = int(t[:2]), int(t[3:])
    return f"{(h % 12) or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


OFFICE_CUT = re.compile(r"\b(?:call|calling|llame|llamar|office|oficina|contact|hours|horario|e-?mail|text|rsvp|"
                        r"register|registration|inscripci\w+)\b", re.I)


def meeting(window: str, issue_date: str | None, lang: str | None = None, nxt: list[str] | None = None
            ) -> tuple[dict | None, dict | None]:
    """(meets, next_meeting) from the text after the group's name; the next lines are read only when that line says
    nothing. Office / contact hours are not meeting times. Only what is written; nothing inferred."""
    windows = [window] + ([" ".join(nxt)] if nxt else [])
    for i, w in enumerate(windows):
        w = OFFICE_CUT.split(w)[0]
        got = _meeting(w, issue_date, lang)
        if got != (None, None):
            m = got[0]
            if m and not m.get("time") and i == 0 and nxt and TIME_RX.match(nxt[0].strip()):
                # "every Thursday at" / "7:00pm in the hall": the time runs on to the next line
                both = _meeting(OFFICE_CUT.split(window + " " + nxt[0])[0], issue_date, lang)[0]
                if both and both.get("days") == m.get("days") and both.get("time"):
                    return both, None
            return got
    return None, None


def _meeting(window: str, issue_date: str | None, lang: str | None) -> tuple[dict | None, dict | None]:
    t = fold(window)
    days = []
    for m in DAY_RX.finditer(t):
        if lang == "vi" and not m.group(2):
            continue                       # "thu" / "sat" are Vietnamese words ("thứ sáu" is Friday)
        d = m.group(2) or ES_DAY.get(m.group(3) or "") or AB_DAY.get((m.group(1) or "").lower())
        if d and d not in days:
            days.append(d)
    om = ORD_RX.search(t)
    tm = TIME_RX.search(t)
    tval = None
    if tm:
        if tm.group(6):
            tval = "12:00"
        else:
            tval = hhmm(tm.group(1), tm.group(2), tm.group(5))
    freq = None
    if om:
        d = ES_DAY.get(om.group(3).lower(), om.group(3).lower())
        o2 = f" and {ORD_N[om.group(2).lower()]}" if om.group(2) else ""
        freq = f"monthly ({ORD_N[om.group(1).lower()]}{o2} {d.capitalize()})"
        if d not in days:
            days.insert(0, d)
    elif BIWEEKLY_RX.search(t) and days:
        freq = "every other week"
    elif MONTHLY_RX.search(t) and days:
        freq = "monthly"
    elif WEEKLY_RX.search(t) and days:
        freq = "weekly"
    dm = ONE_DATE_RX.search(t)
    nxt = None
    if dm and not freq:
        try:
            if dm.group(1):
                mo, dd = MONTHS[dm.group(1)[:3]], int(dm.group(2))
            elif dm.group(3):
                mo, dd = int(dm.group(3)), int(dm.group(4))
            else:
                mo, dd = ES_MON[dm.group(6)], int(dm.group(5))
            base = date.fromisoformat(issue_date) if issue_date else date.today()
            cand = [date(y, mo, dd) for y in (base.year, base.year + 1)]
            cand = [c for c in cand if -10 <= (c - base).days <= 120]
            if cand:
                nxt = {"date": cand[0].isoformat(), "time": tval}
        except (ValueError, KeyError):
            pass
        if nxt:
            return None, nxt
    if not days and not freq:
        return None, None
    if not days:
        return None, None
    parts = []
    if freq and freq.startswith("monthly ("):
        parts.append(freq[9:-1] + " of the month")
    else:
        dn = [d.capitalize() + ("s" if freq in ("weekly",) else "") for d in days[:3]]
        parts.append(" and ".join(dn) if len(dn) <= 2 else ", ".join(dn))
        if freq in ("every other week", "monthly"):
            parts.append(freq)
    if tval:
        parts.append(nice_time(tval))
    return {"days": [d.capitalize() for d in days[:3]], "time": tval, "frequency": freq,
            "text": ", ".join(parts)}, None


LOC_RX = re.compile(
    r"\b(?:in|at|en|el|la)\s+(?:the\s+)?("
    r"(?:parish|church|social|fellowship|community|ministry|activity|activities|youth|family|pastoral|faith formation|"
    r"religious education|conference|meeting|music|hospitality|education|multi-?purpose|st\.?\s+[A-Z]\w+|"
    r"msgr\.?\s+[A-Z]\w+|fr\.?\s+[A-Z]\w+|[A-Z]\w+)\s+(?:hall|center|centre|room|rm\.?|building|house|library|chapel|"
    r"lounge|annex)\b(?:\s*#?\s*\d{1,3}[a-z]?)?"
    r"|(?:main |old |small |adoration |day )?(?:chapel|church|sanctuary|library|cafeteria|gym|gymnasium|courtyard|"
    r"patio|cry room|rectory|convent|parish hall|hall|classroom\s*#?\s*\d+|room\s*#?\s*[0-9A-Z][\w-]{0,3})\b"
    r"|sal[oó]n\s+(?:parroquial|social|de usos m[uú]ltiples|[A-Z]\w+)|capilla|iglesia|templo|biblioteca|sacrist[ií]a)",
    re.I)


LOC_BARE = re.compile(r"\b((?:class)?room\s*#?\s*\d{1,3}[a-z]?|rm\.?\s*#?\s*\d{1,3}[a-z]?|parish hall|church hall|"
                      r"parish (?:center|centre|office conference room)|social hall|youth (?:room|center)|library|"
                      r"cafeteria|gym|chapel|sal[oó]n parroquial|capilla)\b", re.I)


def location(window: str) -> str | None:
    m = LOC_RX.search(window) or LOC_BARE.search(window)
    if not m:
        return None
    loc = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
    if privacy.redact(loc)[1] or cal_privacy.names_private_person(loc):
        return None
    if len(loc) < 3 or len(loc.split()) > 6:
        return None
    return loc[:1].upper() + loc[1:]


# ------------------------------------------------------------------------------------------------ names
CONTACT_CUT = re.compile(r"\s(?:[-–—|:•·]\s)|\s{2,}|\b(?:contact|call|e-?mail|text|llame|llamar|contacte|info|"
                         r"for (?:more )?info\w*|coordinator|coordinadora?|chair|leader|president|presidente|"
                         r"facilitator|director|directora?)\b", re.I)


NAME_END = re.compile(r"\s(?:meets?|meeting|will|is|are|begins?|starts?|returns?|resumes?|continues?|on|every|each|this|"
                      r"next|join|invites?|welcomes?|for all|para|se re[uú]ne[n]?|invita|cada|todos|todas|now|today|"
                      r"tonight|registration|sign[- ]?ups?|after|before|following|from|in the|at the)\b", re.I)


def name_ok(n: str) -> bool:
    if not n or len(n) < 3 or len(n.split()) > 8:
        return False
    if re.search(r"\d{3,}", n) and not re.search(r"(?:council|court|consejo|corte|#)\s*#?\s*\d{3,5}$", n, re.I):
        return False
    if privacy.redact(n)[1] or bg.private_name_risk(n) or cal_privacy.names_private_person(n) or \
            cal_privacy.mass_for_person(n) or privacy.EMAIL_RE.search(n) or privacy.PHONE_RE.search(n):
        return False
    return True


_TERM_WORDS: set[str] = set()


def term_words() -> set[str]:
    """Every word of the vocabulary patterns and the taxonomy's names: ordinary group words, never a person."""
    if not _TERM_WORDS:
        for pat, *rest in V:
            _TERM_WORDS.update(w for w in re.findall(r"[a-z\uac00-\ud7a3]{2,}", fold(pat + " " + rest[5])))
        for rx, *rest in bg.R:
            _TERM_WORDS.update(w for w in re.findall(r"[a-z]{2,}", rx.pattern + " " + (rest[-1] or "")))
        _TERM_WORDS.update("""rosario misericordia eng span esp mens womens ladies oica rica ocia rcia ocic ultreya guadalupanas
        guadalupanos hora santa santo adoracion capilla jovenes damas caballeros colon hijas legio mariae praesidium
        columbiettes vincentians vicente griefshare divorcecare exodus tmiy crhp mops lifeteen edge ministerio ministerios
        grupo grupos comunidad parroquial english spanish espanol bilingual junior juniors senior seniors adult adults
        mtry mnstry minstry jr sr catholic catolica catolico catolicas catolicos parish parroquia ministry ministries
        society sociedad club guild team circle community fellowship formation program classes class night nights
        sunday weekday daily morning evening group""".split())
    return _TERM_WORDS


PATRON = re.compile(r"^(?:st|sts|ste|saint|saints|san|santa|santo|sao)\.?$", re.I)   # "St. Bruno", "San Judas"
NAME_NOISE = re.compile(r"\b(?:retreat|retiro|drive|mass|misa|conference|workshop|information|info|registration|"
                        r"inscripci\w+|meetings?|reuni\w+|minister|coordinator|center|centre|update|news|book of|"
                        r"remembrance|january|february|march|april|may|june|july|august|september|october|november|"
                        r"december|interested|expressed|brought|changed|has|have|had|lead|leads|led|starting|"
                        r"announce\w*|schedule|calendar|sign|needed|needs|wanted|recruit\w*)\b", re.I)
FRAG_END2 = re.compile(r"\b(?:y|de|del|or|o|their|his|her|its|an|el|la|los|las|en|con|para|por|from|into|about|us|we|"
                       r"them|you|and|in|to)$", re.I)
NAME_BAD = re.compile(r"^(?:help|remember|next|one|through|questions?|no|todas?|todos|nov|oct|dec|the weekly|o|a|an|"
                      r"any|some|all|each|every|this|these|that|our new|new)\b|\b(?:helps?|support|hours|operation|grades?|"
                      r"dinner|corner|discussions?|particular|grand knight|night out|program(?:s)?|mentors?|wanted|"
                      r"\d+(?:st|nd|rd|th))\b|[^\w\s'’&/().,\-–—#:+]", re.I)
SHORT_OK = {"of", "de", "la", "el", "y", "en", "to", "in", "at", "a", "st", "sr", "jr", "ii", "ng", "sa", "&", "mm"}
ONE_WORD_OK = {"lectors", "ushers", "sacristans", "servers", "catechists", "greeters", "confirmation", "bereavement",
               "cursillo", "edge", "lifeteen"}


def tokens_ok(name: str, term: str) -> bool:
    """Strict: every word is an ordinary word, a church/group word, part of the matched term, an acronym, or part of a
    patron's name (after St./San/Our Lady...). One unknown word (a surname, a given name) and the written name is not
    used: the taxonomy's generic name is used instead."""
    vocab = privacy.vocabulary()
    tw = term_words()
    inside = set(re.findall(r"[a-z\uac00-\ud7a3]+", fold(term)))
    toks = re.findall(r"[A-Za-zÀ-ÿĀ-ỿ\uac00-\ud7a3'’]+", name)
    after_patron = 0
    for t in toks:
        w = fold(t).replace("'", "")
        w2 = re.sub(r"s$", "", w)
        if PATRON.match(w):
            after_patron = 2
            continue
        if after_patron and t[:1].isupper():
            after_patron -= 1
            continue
        after_patron = 0
        if w not in inside and w in privacy._given_names()[0] and t[:1].isupper():
            return False                  # a given name outside the group's own term ("María <surname>/Bereavement")
        if w in inside or w in tw or w2 in tw or len(w) <= 2 or (t.isupper() and len(t) <= 6) or \
                (w in vocab and w not in privacy._given_names()[0]):
            continue
        return False
    return True


TRAIL_LANG = re.compile(r"\s*[(/-]?\s*\b(?:eng|engl|sp|span|esp)\.?\)?$", re.I)


def written_name(line: str, span: tuple[int, int] | None) -> str | None:
    """The group's name as the bulletin writes it, when the line is a short label around the term; else None."""
    seg = privacy.strip_contacts(line, urls=True)
    seg = seg.split(":")[0] if ":" in seg and not re.match(r"^\s*\d", seg) else seg   # "Lectors: <name>"
    # cut at the first private-looking name
    sp = privacy.name_spans(seg, "raw")
    if sp:
        seg = seg[: min(s for s, _, _ in sp)]
    seg = CONTACT_CUT.split(seg, maxsplit=1)[0]
    seg = re.sub(r"[.…_·]{2,}.*$", "", seg)
    # the name ends where the announcement starts: a verb, a day, a time or a date
    cut = [m.start() for rx in (NAME_END, DAY_RX, TIME_RX, ONE_DATE_RX, ORD_RX) for m in [rx.search(seg if rx is
           NAME_END or rx is TIME_RX else fold(seg))] if m and m.start() > 0]
    if cut:
        seg = seg[: min(cut)]
    seg = bg.clean_name(seg).rstrip(" :;,.-–—(&/")
    seg = TRAIL_LANG.sub("", seg).strip()
    seg = re.sub(r"\s+#?\d{1,2}$", "", seg).strip()          # "Knights of Columbus 6"
    if not seg or not seg[:1].isupper() or re.match(r"^\d", seg) or NAME_NOISE.search(seg) or \
            FRAG_END2.search(seg.strip(" ,")) or re.search(r",\s*\w+$", seg) and FRAG_END2.search(seg):
        return None
    if len(seg.split()) == 1 and fold(seg) not in ONE_WORD_OK and span is None:
        return None
    toks = seg.split()
    if NAME_BAD.search(seg) or any(len(t.strip(".,()")) <= 2 and fold(t.strip(".,()")) not in SHORT_OK and
                                   not (t.isupper() and len(t) >= 2 and t.isalpha() and len(toks) > 1 and False)
                                   for t in toks if t.strip(".,()&/-–—")) or \
            re.search(r"\b(\w+(?: \w+)?)\s+\1\b", seg, re.I) or \
            len(re.findall(r"(?:youth|group|ministry|room)", fold(seg))) >= 4:
        return None
    words = seg.split()
    if len(words) > 7 or bg.FRAGMENT_END.search(bg.norm(seg)) or \
            re.search(r"(?<!\bst)(?<!\bsts)(?<!\bfr)(?<!\bmt)(?<!msgr)(?<!\bdr)\.\s|[!?“”\"]|"
                      r"\b(?:will|join|please|is|are|was|our|we|you|your|if|this|on|at|every|each|meets|para|los|"
                      r"invita|invitamos|supported|sponsored|hosted|by|how|why|what|when|where|step|means|using|"
                      r"returning|guest|presents|thank|thanks|congratulations|welcome|new|first time)\b", seg, re.I):
        return None
    if span is not None and fold(line[span[0]:span[1]]) not in fold(seg):
        return None
    if span is not None and len(seg) > 3 * (span[1] - span[0]) + 25:
        return None
    if re.fullmatch(r"[\W_]*(?:ocia|rcia|rica|oica|ocic|kofc|k of c|svdp|cda)(?:[\W_]+(?:ocia|rcia|rica|oica|ocic))*[\W_]*",
                    fold(seg)):
        return None                                   # acronyms only: the taxonomy's clear name reads better
    if not tokens_ok(seg, line[span[0]:span[1]] if span else ""):
        return None
    seg = bg.smart_title(seg)
    return seg if name_ok(seg) else None


FILLER = set("""ministry ministries ministerio ministerios society sociedad group grupo grupos club guild team english eng
spanish span sp esp espanol bilingual adult adults youth children childrens kids teens parish parroquial de la las los el
the of and y del a our junior jr senior women's men's womens mens catholic catolica community comunidad program programs
classes class clases formation prep preparation preparacion ministers minister ministros holy st saint san santa team
circle volunteers in sunday weekday daily mtry ministery minstry members & / -""".split())
SCHEDULE_SUBS = {"eucharistic_adoration", "novena", "divine_mercy", "rosary", "liturgy_of_the_hours", "funeral_ministry",
                 "recovery"}
SUB_KIND = {"young_adult_group": "young_adults", "mens_group": "mens", "womens_group": "womens", "seniors_group": "seniors",
            "bible_study": "bible_study", "small_faith_groups": "small_groups", "prayer_group": "prayer_group",
            "charismatic_renewal": "charismatic", "legion_of_mary": "legion_of_mary", "cursillo": "cursillo",
            "knights_of_columbus": "kofc", "columbiettes": "columbiettes", "catholic_daughters": "catholic_daughters",
            "st_vincent_de_paul": "svdp", "bereavement": "grief", "divorced_separated": "divorced_separated",
            "alpha": "alpha", "ocia": "ocia_inquiry", "youth_ministry": "youth", "couples_group": "couples",
            "that_man_is_you": "mens", "couples_for_christ": "couples", "young_ladies_institute": "fraternal_other"}


def formal_ok(seg: str) -> bool:
    """A short label the taxonomy recognises, with nothing else in it but ordinary group words."""
    n = bg.norm(bg.fix_mojibake(seg))
    for rx, *_ in bg.R:
        m = rx.search(n)
        if m:
            rest = (n[: m.start()] + " " + n[m.end():]).replace("/", " ").replace("&", " ")
            return all(w in FILLER or re.fullmatch(r"[\W\d]*", w) for w in rest.split())
    return False


# ------------------------------------------------------------------------------------------------ one issue
def page_language(text: str) -> str:
    w = re.findall(r"[a-záéíóúñü]+", fold(text))
    es = sum(x in ES_WORDS for x in w)
    en = sum(x in EN_WORDS for x in w)
    return "es" if es > 1.3 * en and es > 8 else "en"


def line_language(text: str) -> str | None:
    w = re.findall(r"[a-záéíóúñü]+", fold(text))
    es = sum(x in ES_WORDS for x in w)
    en = sum(x in EN_WORDS for x in w)
    if re.search(r"\b(?:en espa[nñ]ol|in spanish|spanish)\b", text, re.I):
        return "es"
    if re.search(r"\b(?:bilingual|biling[uü]e|english (?:&|and|/) spanish|spanish (?:&|and|/) english)\b", text, re.I):
        return "en+es"
    if re.search(r"\b(?:vietnamese|ti[eế]ng vi[eệ]t)\b", text, re.I):
        return "vi"
    if re.search(r"\b(?:korean|tagalog|filipino|chinese|mandarin|cantonese)\b", text, re.I):
        return {"korean": "ko", "tagalog": "tl", "filipino": "tl", "chinese": "zh", "mandarin": "zh",
                "cantonese": "zh"}[re.search(r"\b(korean|tagalog|filipino|chinese|mandarin|cantonese)\b", text, re.I)
                                   .group(1).lower()]
    if es >= 3 and es > 1.5 * en:
        return "es"
    if en >= 3 and en > 1.5 * es:
        return "en"
    return None


def ad_page(page: str) -> bool:
    hits = {m.group(0).lower() for m in AD_RX.finditer(page)}
    phones = len(privacy.PHONE_RE.findall(page))
    return len(hits) >= 6 and phones >= 6


def private_mask(lines: list[str]) -> list[bool]:
    mask = [False] * len(lines)
    i = 0
    while i < len(lines):
        ln = lines[i]
        if PRIV_HEAD.search(ln) and len(ln.split()) <= 14:
            mask[i] = True
            k = i + 1
            while k < len(lines) and k - i <= 40:
                nxt = lines[k]
                if is_heading(nxt) and not PRIV_HEAD.search(nxt) and not privacy.name_spans(nxt, "standard") and \
                        k - i >= 2 and not NAMES_LIST.search(nxt) and not DAY_RX.search(fold(nxt)) and \
                        not ONE_DATE_RX.search(fold(nxt)) and not TIME_RX.search(nxt):
                    break
                mask[k] = True
                k += 1
            i = k
        else:
            if PRIV_LINE.search(ln) or NAMES_LIST.search(ln):
                mask[i] = True
            i += 1
    return mask


def line_private(ln: str) -> bool:
    return privacy.is_intention_line(ln) or cal_privacy.mass_for_person(ln)


def vocab_hits(line: str, parish_is_vdp: bool) -> list[tuple[dict, tuple[int, int]]]:
    f = fold(line)
    out = []
    for v in VOCAB:
        m = None
        if v["plain"] is not None:
            m = v["plain"].search(f)
        if m is None and v["case"] is not None:
            m = v["case"].search(line)
            if m:
                m = re.search(re.escape(fold(m.group(0))), f) or m
        if m is None and v["vdp"] is not None and not parish_is_vdp:
            m = v["vdp"].search(f)
        if m is None and v["head"] is not None and is_heading(line):
            m = v["head"].search(f.strip())
        if m:
            out.append((v, (m.start(), m.end())))
    return out


PRIORITY = {k: i for i, k in enumerate(
    ["tmiy", "exodus90", "wwp", "crhp", "legion", "cursillo", "kofc", "kpc", "columbiettes", "cda", "yli", "svdp", "alpha",
     "divorced", "bereavement", "charismatic", "moms_group", "mens_group", "womens_group", "young_adults", "seniors",
     "youth_ministry", "couples", "rosary_group", "bible_study", "small_groups", "prayer_group", "ocia"])}
COUNCIL_RX = re.compile(r"\b(?:council|consejo)\s*(?:no\.?|number|#)?\s*#?\s*(\d{3,5})\b|\b(?:K ?of ?C|KofC|Knights of "
                        r"Columbus)\s*#\s*(\d{3,5})\b", re.I)
COURT_RX = re.compile(r"\bcourt\b[^,.;\n]{0,40}?#\s*(\d{2,5})\b|\bcourt\s*(?:no\.?)?\s*(\d{2,5})\b", re.I)


def extract_issue(text: str, issue: dict, parish_names: list[str], evidence: list | None) -> list[dict]:
    pages = text.split("\f")
    issue_date = issue.get("date")
    is_vdp = any("vincent de paul" in fold(n) for n in parish_names)
    found = []
    for pno, page in enumerate(pages, 1):
        if not page.strip() or ad_page(page):
            continue
        plang = page_language(page)
        lines = [re.sub(r"\s+", " ", x).strip() for x in page.split("\n")]
        lines = [x for x in lines if x]
        mask = private_mask(lines)
        for i, ln in enumerate(lines):
            if mask[i] or len(ln) > 400:
                continue
            hits = vocab_hits(ln, is_vdp)
            formal = None
            if not hits and 2 < len(ln) <= 60 and len(ln.split()) <= 6 and ln[:1].isupper():
                seg = written_name(ln, None)
                if seg and formal_ok(seg):
                    res, why = bg.classify(seg)
                    if res and res[-1] == "specific" and res[2] not in SCHEDULE_SUBS:
                        formal = (res, seg)
            if not hits and not formal:
                continue
            if line_private(ln) or AD_LINE.search(ln):
                continue
            if ELSEWHERE.search(ln):
                continue
            # window for meeting / place / language: rest of this line + next two lines of the block
            nxt = []
            for j in range(i + 1, min(i + 3, len(lines))):
                lj = lines[j]
                if mask[j] or is_heading(lj) or vocab_hits(lj, is_vdp) or line_private(lj) or \
                        re.search(r"[?!]\s*$", lj) or (len(lj.split()) <= 8 and lj.istitle()):
                    break                          # a new announcement starts: its details are not this group's
                nxt.append(lines[j])
            if formal:
                (key, cat, sub, aud, lang, disp, strength), seg = formal
                hits_g = [{"key": key, "category": cat, "subcategory": sub, "audience": aud, "lang": lang,
                           "display": disp, "kind": SUB_KIND.get(sub), "name": seg, "span_end": len(ln),
                           "via": "taxonomy"}]
            else:
                hits_g, taken = [], []
                fl = fold(ln)
                for v, (s, e) in sorted(hits, key=lambda h: PRIORITY.get(h[0]["key"], 99)):
                    nm = written_name(ln, (s, e))
                    r0, r1 = s, e
                    if nm:
                        k0 = fl.find(fold(nm))
                        if k0 >= 0:
                            r0, r1 = min(s, k0), max(e, k0 + len(nm))
                    if any(r0 < t1 and t0 < r1 for t0, t1 in taken):
                        continue          # the same words are a more specific group already
                    taken.append((r0, r1))
                    hits_g.append(dict(v, name=nm, span_end=max(e, r1)))
            for g in hits_g:
                after = ln[g["span_end"]:] if g["span_end"] < len(ln) else ""
                window = " ".join([after] + nxt)
                vi = bool(TERM_LANG[1][1].search(fold(ln))) or g.get("lang") == "vi"
                meets, nm = meeting(after, issue_date, "vi" if vi else None, nxt)
                loc = location(after) or location(" ".join(nxt))
                tl = None
                for code, rx in TERM_LANG:
                    if rx.search(fold(ln)):
                        tl = code
                        break
                term_lang = g.get("lang") or tl
                nm_l = fold(g.get("name") or "")
                name_lang = ("en+es" if re.search(r"english|ingles", nm_l) and re.search(r"spanish|espanol", nm_l) else
                             "en" if re.search(r"\benglish\b|\bingles\b|\beng\b", nm_l) else
                             "es" if re.search(r"\bspanish\b|\bespanol\b|\bspan\b", nm_l) else None)
                if name_lang:
                    term_lang = name_lang
                lang = term_lang or line_language(" ".join([ln] + nxt)) or None
                assumed = term_lang is None
                if not lang:
                    lang = plang if plang == "es" else "en"
                qual = None
                if g["key"] == "kofc":
                    cm = COUNCIL_RX.search(" ".join([ln] + nxt))
                    if cm:
                        qual = cm.group(1) or cm.group(2)
                if g["key"] == "cda":
                    cm = COURT_RX.search(" ".join([ln] + nxt))
                    if cm:
                        qual = cm.group(1) or cm.group(2)
                rec = {"key": g["key"], "category": g["category"], "subcategory": g["subcategory"],
                       "audience": g["audience"], "display": g["display"], "kind": g.get("kind"),
                       "informal": g.get("kind") is not None, "name": g.get("name"), "language": lang,
                       "language_assumed": assumed, "meets": meets, "next_meeting": nm, "location": loc,
                       "qualifier": qual, "page": pno, "line": i, "issue": issue, "term_lang": term_lang,
                       "via": g.get("via", "vocabulary")}
                found.append(rec)
                if evidence is not None:
                    evidence.append({"key": g["key"], "name": g.get("name") or g["display"], "page": pno, "line": i,
                                     "text_path": issue.get("text_path"),
                                     "lines": [ln] + nxt, "meets": meets, "next": nm, "loc": loc, "lang": lang})
    return found


# ------------------------------------------------------------------------------------------------ merge per site
def group_key(r: dict) -> str:
    """Same group = same vocabulary key (+ council/court number) and, only when the term itself is in another language
    ("Grupo de Oración" vs "Prayer Group"), that language. A named variant ("Men of St. Joseph") stays its own group."""
    if r["via"] == "vocabulary":
        k = r["key"]
        if r.get("name") and norm_name(r["name"]) != norm_name(r["display"]) and r["key"] in (
                "mens_group", "womens_group", "bible_study", "prayer_group", "youth_ministry", "young_adults",
                "rosary_group", "small_groups", "couples", "seniors", "moms_group"):
            k += ":" + norm_name(r["name"])[:40]
    else:
        k = "t:" + r["key"] + ":" + norm_name(r["name"] or r["display"])[:40]
    if r.get("qualifier"):
        k += f":{r['qualifier']}"
    return f"{k}:{r['term_lang'] or ''}"


def nice_date(d: str | None) -> str:
    if not d:
        return "date not printed"
    x = date.fromisoformat(d)
    return f"{x.strftime('%B')} {x.day}, {x.year}"


ONE_PER_PARISH = ("kofc", "svdp", "cda", "columbiettes", "ocia", "cursillo", "crhp", "alpha", "yli", "kpc", "divorced")


def merge_site(found: list[dict], site_key: str) -> list[dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in found:
        by[group_key(r)].append(r)
    # an unnamed mention ("Men's Group") folds into the one named variant of the same key ("Parish Men's Club");
    # "Knights of Columbus" folds into the one numbered council
    base = defaultdict(list)
    for k in by:
        parts = k.split(":")
        base[(parts[0], parts[-1])].append(k)
    for (b, lang), ks in base.items():
        plain = f"{b}:{lang}"
        named = [k for k in ks if k != plain]
        if plain in by and len(named) == 1:
            by[named[0]] += by.pop(plain)
    # one per parish whatever the language of the mention: councils, conferences, courts, OCIA, Cursillo...
    for b in ONE_PER_PARISH:
        ks = [k for k in list(by) if k.split(":")[0] == b and len(k.split(":")) == 2]
        if len(ks) > 1:
            keep = max(ks, key=lambda k: len(by[k]))
            langs = {r["language"] for k in ks for r in by[k]}
            for k in ks:
                if k != keep:
                    by[keep] += by.pop(k)
            if {"en", "es"} <= langs:
                for r in by[keep]:
                    r["language"], r["language_assumed"] = "en+es", False
    out = []
    for k, rs in by.items():
        rs.sort(key=lambda r: (r["issue"].get("date") or ""), reverse=True)
        names = Counter(r["name"] for r in rs if r.get("name"))
        r0 = rs[0]
        name = names.most_common(1)[0][0] if names else r0["display"]
        langs = Counter(r["language"] for r in rs)
        lang_assumed = all(r["language_assumed"] for r in rs if r["language"] == langs.most_common(1)[0][0])
        r0 = dict(r0, language=langs.most_common(1)[0][0], language_assumed=lang_assumed)
        if r0.get("qualifier") and r0["key"] == "kofc" and not re.search(r"\d{3,5}", name):
            name = f"Knights of Columbus Council {r0['qualifier']}"
        if r0.get("qualifier") and r0["key"] == "cda" and not re.search(r"\d{2,5}", name):
            name = f"Catholic Daughters of the Americas, Court #{r0['qualifier']}"
        if not name_ok(name):
            name = r0["display"]
        meets = next((r["meets"] for r in rs if r.get("meets") and r["meets"].get("time")), None) or \
            next((r["meets"] for r in rs if r.get("meets")), None)
        nxt = next((r["next_meeting"] for r in rs if r.get("next_meeting")), None)
        loc = Counter(r["location"] for r in rs if r.get("location")).most_common(1)
        srcs, seen = [], set()
        for r in rs:
            u = r["issue"]["url"]
            if (u, r["page"]) in seen:
                continue
            seen.add((u, r["page"]))
            srcs.append({"url": u, "issue_date": r["issue"].get("date"), "page": r["page"],
                         "ocr": bool(r["issue"].get("ocr")) and r["page"] in (r["issue"].get("ocr_pages") or [])})
        issues = sorted({s["issue_date"] or "" for s in srcs}, reverse=True)
        conf = "high" if (meets or nxt) and r0["informal"] else ("medium" if r0["informal"] or len(issues) > 1 else "low")
        if any(s["ocr"] for s in srcs) and conf == "high":
            conf = "medium"
        last = issues[0] or None
        out.append({
            "id": f"{site_key}:bulletin:{k}",
            "name": name,
            "category": r0["category"], "category_label": bg.CATEGORIES.get(r0["category"], r0["category"]),
            "subcategory": r0["subcategory"], "audience": r0["audience"],
            "language": r0["language"], "language_assumed": r0["language_assumed"],
            "informal": r0["informal"], "informal_kind": r0["kind"],
            "meets": meets, "next_meeting": nxt, "location": loc[0][0] if loc else None,
            "contact": CONTACT,
            "source_kind": "bulletin",
            "source_label": f"From the parish bulletin, {nice_date(last)}",
            "source_url": srcs[0]["url"],
            "last_seen": last,
            "seen_in_issues": len([i for i in issues if i]) or 1,
            "sources": srcs,
            "confidence": conf,
            "flags": (["ocr"] if any(s["ocr"] for s in srcs) else []) +
                     (["issue_older_than_120_days"] if last and (date.today() - date.fromisoformat(last)).days > 120 else []),
        })
    out.sort(key=lambda g: (not g["informal"], g["category"], g["name"]))
    return out


# ------------------------------------------------------------------------------------------------ final screen
def final_screen(g: dict) -> list[str]:
    """Every string written to data/real goes through both screens; returns problems (should be empty)."""
    bad = []
    for f in ("name", "location"):
        v = g.get(f)
        if not v:
            continue
        if privacy.redact(v)[1] or privacy.EMAIL_RE.search(v) or privacy.PHONE_RE.search(v):
            bad.append(f"{f}: shared screen")
        if cal_privacy.names_private_person(v) or cal_privacy.mass_for_person(v):
            bad.append(f"{f}: calendar screen")
        if privacy.is_intention_line(v):
            bad.append(f"{f}: intention")
    if g.get("meets") and (privacy.redact(g["meets"]["text"])[1]):
        bad.append("meets: shared screen")
    return bad


# ------------------------------------------------------------------------------------------------ main
def site_groups_norm(site_file: str) -> set[str]:
    d = json.loads((ROOT / site_file).read_text()) if (ROOT / site_file).exists() else {}
    return {norm_name(g.get("name")) for g in d.get("groups") or []} | \
        {f"sub:{g.get('subcategory')}:{g.get('language')}" for g in d.get("groups") or []}


def norm_name(n: str | None) -> str:
    n = fold(n or "")
    n = re.sub(r"\b(the|of|and|y|de|la|el|los|las|ministry|ministerio|group|grupo|society|parish|st|saint)\b", " ", n)
    return re.sub(r"[^a-z0-9가-힣]+", "", n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default=",".join(SLUGS))
    ap.add_argument("--evidence", action="store_true")
    a = ap.parse_args()
    slugs = [s for s in a.slugs.split(",") if s and s != "oakland"]
    OUT.mkdir(parents=True, exist_ok=True)
    totals = {}
    for slug in slugs:
        merged = json.loads((MERGED / f"{slug}.json").read_text())
        recs = {r["id"]: r for r in merged["records"]}
        states = [json.loads(p.read_text()) for p in sorted((STATE / slug).glob("*.json"))] if (STATE / slug).exists() else []
        parishes: dict[str, dict] = {}
        evidence_all = {}
        problems = []
        for st in states:
            ok = [x for x in st.get("issues") or [] if x.get("ok") and x.get("text_path")]
            found, ev = [], ([] if a.evidence else None)
            pnames = [p.get("name") or "" for p in st.get("parishes") or []]
            for iss in ok:
                tp = ROOT / iss["text_path"]
                if not tp.exists():
                    continue
                found += extract_issue(tp.read_text(errors="ignore"), iss, pnames, ev)
            groups = merge_site(found, st["site_key"])
            kept = []
            for g in groups:
                p = final_screen(g)
                if p:
                    problems.append({"site": st["site_key"], "problem": p})
                    continue
                kept.append(g)
            sg = site_groups_norm(st["site_file"])
            for g in kept:
                g["also_on_parish_website"] = norm_name(g["name"]) in sg or \
                    f"sub:{g['subcategory']}:{g['language']}" in sg
            if ev is not None:
                evidence_all[st["site_key"]] = ev
            link = {"site_key": st["site_key"], "site_file": st["site_file"], "domain": st.get("domain"),
                    "bulletin_link": st.get("bulletin_link"), "status": st.get("status"),
                    "issues": [{k: x.get(k) for k in ("url", "date", "date_source", "pages", "ocr", "ocr_pages",
                                                      "found_via", "fetched_at", "kind")} for x in ok],
                    "notes": st.get("notes") or [],
                    "tried": [{"url": x.get("url"), "cls": x.get("cls"), "status": x.get("status")}
                              for x in st.get("issues") or [] if not x.get("ok") and x.get("kept") is not False][:6]}
            for p in st.get("parishes") or []:
                e = parishes.setdefault(p["id"], {"parish_id": p["id"], "name": p.get("name"), "city": p.get("city"),
                                                  "links": [], "groups": []})
                e["links"].append(link)
                have = {x["id"].split(":bulletin:", 1)[1] for x in e["groups"]}
                e["groups"] += [g for g in kept if g["id"].split(":bulletin:", 1)[1] not in have]
        # measures
        before = {pid for pid, r in recs.items() if r.get("groups_n")}
        with_b = {pid for pid, e in parishes.items() if e["groups"]}
        new_info = {pid for pid, e in parishes.items() if any(not g["also_on_parish_website"] for g in e["groups"])}
        after = before | with_b
        site_st = Counter(st.get("status") for st in states)
        informal = Counter(g["informal_kind"] for e in parishes.values() for g in e["groups"] if g["informal"])
        informal_new = Counter(g["informal_kind"] for e in parishes.values() for g in e["groups"]
                               if g["informal"] and not g["also_on_parish_website"])
        counts = {
            "sites": len(states), "parishes_with_link": len(parishes),
            "links": {"attempted": len(states), "pdfs_got": sum(1 for s in states if s.get("status") == "ok"),
                      "challenged": sum(1 for s in states if s.get("status") == "challenge"),
                      "blocked_403_429": sum(1 for s in states if s.get("status") == "blocked"),
                      "failed": sum(1 for s in states if s.get("status") not in ("ok", "challenge", "blocked"))},
            "site_status": dict(site_st),
            "issues": sum(len([x for x in s.get("issues") or [] if x.get("ok")]) for s in states),
            "issues_ocr": sum(len([x for x in s.get("issues") or [] if x.get("ok") and x.get("ocr")]) for s in states),
            "parishes_total": len(recs),
            "parishes_with_groups_before": len(before), "parishes_with_groups_after": len(after),
            "parishes_with_bulletin_groups": len(with_b),
            "parishes_gaining_first_group": len(after - before),
            "parishes_with_new_group_info": len(new_info),
            "groups": sum(len(e["groups"]) for e in parishes.values()),
            "informal_groups": sum(informal.values()), "informal_by_kind": dict(informal.most_common()),
            "informal_not_on_website_by_kind": dict(informal_new.most_common()),
            "with_meeting_time": sum(1 for e in parishes.values() for g in e["groups"] if g.get("meets") or g.get("next_meeting")),
            "privacy_dropped": len(problems),
        }
        totals[slug] = counts
        out = {"diocese": slug, "built_at": now_iso(), "built_by": "ingest/california/bulletins/extract_groups.py",
               "method": "rule-based (no LLM): pdftotext / tesseract OCR, private sections skipped, informal-group "
                         "vocabulary + build_groups taxonomy; see the script's docstring",
               "privacy": "no lay names, phones or e-mails; contact is 'ask the parish office'; names and places pass "
                          "ingest/public/privacy.py and ingest/connectors/calendars/privacy.py",
               "counts": counts,
               "parishes": sorted(parishes.values(), key=lambda e: e["parish_id"])}
        (OUT / f"{slug}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
        if a.evidence:
            EVID.mkdir(parents=True, exist_ok=True)
            (EVID / f"{slug}.json").write_text(json.dumps(evidence_all, ensure_ascii=False, indent=1))
        print(f"{slug:15s} sites={counts['sites']:3d} ok={counts['links']['pdfs_got']:3d} groups={counts['groups']:4d} "
              f"informal={counts['informal_groups']:4d} parishes w/ groups {counts['parishes_with_groups_before']}->"
              f"{counts['parishes_with_groups_after']} dropped={len(problems)}", flush=True)
    (RAW / "_summary.json").write_text(json.dumps({"built_at": now_iso(), "dioceses": totals}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
