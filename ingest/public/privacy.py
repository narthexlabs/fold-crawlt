"""Privacy screen for text taken from public parish sources (bulletins, parish websites, diocesan pages).

Rule (CLAUDE.md; docs/research/03-ethics-and-data-charter.md, R19/R21): never store private individuals'
data. That means parishioners, the sick, the deceased, Mass intentions, donors, lay committee members and
ministry volunteers' contact names. Clergy, religious and staff named by their parish or the diocese in a
public role may appear.

Used by the ingest scripts (parse_bulletins.py, build_parish_profiles.py, parish_sites.py,
merge_parish_sites.py, parish_signals.py, alt_sources_add.py), by scripts/build_real_seed.py and
scripts/build_briefings.py, and by privacy_scrub.py, which applies the same screen to files already
committed. The screen is idempotent: running it on its own output changes nothing.

    redact(text, mode)          -> (text, n)   private names in a short excerpt -> "[name withheld]"
    scrub_schedule_line(line)   -> (line, n)   keeps the day and time, withholds Mass-intention text
    staff_row_ok(name, title)   -> (ok, why)   a public staff or clergy listing, or junk / an ad / a ministry contact
    screen(obj, rules)          -> Counter     applies the above to the fields of a data file (see RULES)
    private_name_risk(text)                    the stricter yes/no test the briefings use (drop when in doubt)

How names are spotted (conservative; a false alarm only withholds a few words):
  * a lay title (Mr., Mrs., Ms., Dr., Sra.) followed by capitalised words;
  * a given name (privacy_given_names.txt) followed by a capitalised word that reads as a surname: one of the
    5,000 most frequent U.S. surnames (privacy_surnames.txt), or a word that is neither ordinary church,
    calendar or place vocabulary nor an ordinary word (privacy_common_words.txt);
  * an unknown capitalised word followed by a frequent surname ("Cath Sullivan");
  * in texts about funerals, the deceased, intentions, birthdays, the sick or donors, and after contact words
    ("Contact:", "call", "RSVP", "led by"), any capitalised run with a word outside that vocabulary;
  * in "raw" mode (page snippets cut out of running text) also an unknown capitalised word before a frequent
    surname, or an initial before an unknown word ("J. Builta").
This is the one shared screen. ingest/public/media/media_privacy.py imports it and adds media-title rules (public
honorific spans, program guests, private occasions). ingest/connectors/calendars/privacy.py stays standalone (an
event-level keep/drop screen that must run without the repository's data files; see its docstring).

Names stay when they belong to a public person: clergy and religious (Fr., Deacon, Sr., Bishop...), the
public staff in data/seed/persons.json and data/real/chancery_staff.json, the clergy in the diocese's official
appointments (data/real/oakland/official_appointments.json), school principals, and public figures (saints,
popes, founders, patrons of the diocese's parishes and schools). In texts about the dead, the sick or
intentions only clergy and public figures stay.
"""
from __future__ import annotations

import collections
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WITHHELD = "[name withheld]"
INTENTION_WITHHELD = "[Mass intention withheld]"

# ---------------------------------------------------------------- vocabulary shared with scripts/build_briefings.py
# COMMON_BASE is the briefings' list of church, calendar and place words, unchanged, so private_name_risk() gives the
# briefings exactly the answers it gave before this module existed.
COMMON_BASE = frozenset("""
a about after all also am an and annual any are as at autumn back be before beginning bishop bishops blessed blood bring
by call calendar can catholic celebrate celebration center centre chapel children church come coming community
confirmation council day days december dinner do donate donation donations drive during each english evening event events
every faith fall family february feast festival first for fourth free friday friends from fund gathering gift gifts give
god grace group groups guild hall have help here high holy hospitality hour house in information is it its january join july
june junior knights ladies last lent life light lunch march mass masses may meeting men ministry monday month morning
mother next night no not november now october of on one open or our out parish parishioners party please pm prayer
program register registration retreat room rosary sacrament saturday school second see september service session sign
social spanish special spirit st saint saints souls sunday support team the their third this thursday to today tuesday up
us vigil volunteer volunteers we wednesday week weekend welcome will with women world year young youth adoration advent
christmas easter thanksgiving halloween all-saints novena benediction eucharist eucharistic lord jesus christ mary
marian our-lady lady columbus daughters americas cursillo legion bible study class classes ocia rcia ccd cyo spred youth
ministries apostolate apostleship society vincent paul de la el los las en y misa grupo oración oracion reunión reunion
noche dia día domingo sábado sabado viernes jueves miércoles miercoles martes lunes santo santa san señor nuestra virgen
guadalupe padre comunidad parroquia catequesis confirmación bautismo matrimonio jóvenes jovenes familia familias
filipino vietnamese chinese portuguese polish tongan latin bilingual dominican franciscan jesuit friars sisters order
coffee donuts doughnuts breakfast potluck pancake crab feed bazaar fair auction gala raffle bingo movie concert choir
music worship liturgy lectors ushers altar servers ministers eucharistic sacristans pastoral finance committee cemetery
memorial remembrance anniversary jubilee procession pilgrimage feast-day relics confession reconciliation penance
healing anointing respect pro-life rally walk run fundraiser fundraising sale food pantry clothing coat coats toy toys
book books flu shot shots clinic immigration legal workshop training talk lecture series speaker night dinners sunday-dinners
kids teens adults seniors widowed bereavement grief support divorced separated marriage engaged couples encounter men's
women's hope peace joy love life-teen edge teen nursery preschool kindergarten elementary middle tk office parking lot gym
gymnasium library courtyard plaza garden patio room parish-hall church-hall center-hall north south east west main
upper lower annex rectory convent friary priory chapel-of narthex sanctuary welcome-center gathering-place
mon tue tues wed thu thur thurs fri sat sun jan feb mar apr jun jul aug sep sept oct nov dec town away leader leaders daily
offering offerings phase cenacle dinners dinner volunteers all-volunteers lunch brunch picnic potluck tootsie roll rolls
sale tickets ticket dance gala fiesta posada posadas simbang gabi parol lantern adviento cuaresma semana santa
""".split())
CLERGY_WORD = re.compile(r"^(fr|father|rev|reverend|msgr|monsignor|deacon|dcn|bishop|sr|sister|br|brother|padre|hermana|pope|cardinal|fray)\.?$", re.I)
PATRON_START = re.compile(r"^(st|saint|san|santa|santo|our|holy|blessed|mother|nuestra|sacred|immaculate|queen|corpus|divine|good|christ)\b\.?", re.I)
SENSITIVE = re.compile(r"--\s*D\b|\(D\)|†|\bR\.?I\.?P\b|in memory|in loving memory|intention|deceased|passed away|rest in peace|"
                       r"\bsick\b|prayer list|descanse|difunt|enfermo|funeral of|condolence|\bbanns\b|baptized|newly baptized|wedding of", re.I)


def private_name_risk(text: str, common=COMMON_BASE) -> bool:
    """True when a short excerpt might name a private person. Conservative: false alarms just drop the excerpt.
    Looks for runs of two or more capitalised words (a run breaks at punctuation) that contain a word outside a
    vocabulary of church, calendar and place words, unless the run follows a clergy or religious title.
    (Moved here unchanged from scripts/build_briefings.py, which still uses it to drop risky excerpts.)"""
    if SENSITIVE.search(text):
        return True
    for chunk in re.split(r"[,;:()\[\]|/–—•]|\.\s|\s-\s|\s\d", text):
        if _run_risk(re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\.]*", chunk.replace("-", " ")), common):
            return True
    return False


def _run_risk(toks, common=COMMON_BASE) -> bool:
    i = 0
    while i < len(toks):
        if not toks[i][0].isupper():
            i += 1
            continue
        j = i
        while j < len(toks) and toks[j][0].isupper():
            j += 1
        run = toks[i:j]
        prev = toks[i - 1] if i else ""
        if len(run) >= 2 and not PATRON_START.match(run[0]):
            skip = 3 if CLERGY_WORD.match(prev.strip(".")) else 0
            for w in run:
                core = w.strip(".'’-").lower()
                if CLERGY_WORD.match(core):
                    skip = 3
                    continue
                if skip:
                    skip -= 1
                    continue
                if core not in common and not re.fullmatch(r"[ivx]+|\d+", core):
                    return True
        i = j
    return False


# ---------------------------------------------------------------- vocabulary for redaction (broader than the base)
_EXTRA = frozenset("""
academy accounting adult adults advent africa african agape alpha altar angels annual answer answers apostles appeal
april art arts assembly association august auxiliary ave avenue award awards baptism baptisms baptismal basilica begin
bilingue birthday blessing blessings blvd board boulevard boy boys bread brothers building business cafe cafeteria
calvary campaign campus candle candles cantonese care caregiver caregivers cathedral catechesis catechism catechist catechists
celebrations charismatic charity child choirs christian christians circle city clinic clinics closet club co-op coalition
college collection collections column communion communities conference congress contact contemporary council councils court
creek crowning crusade cub cultural culture daily deacon deacons dear december department devotion devotions diocese
diocesan director directors discipleship discussion district division dollars donor donors drama drive drives east
education emmaus emergency employment english en ensemble entrance environment escuela español espanol eucharist evangelization
event exhibit exposition faith family fathers fellowship festival fifth film finance first flower flowers folklorico
forum foundation fourth fridays friend fun gate gift girl girls giving glory gospel grade grades grand gratitude greeters
guild hallow halls harvest health heart hearts heaven help helpers heritage hispanic history holiday holidays home homebound
homeless hope hospice hospital housing hymn immaculate in-person information inn institute interfaith international jail
journey justice kermes kitchen knight lady latino lay leadership learning lectio legacy library life lifeteen lights
literacy liturgical lives living local lot lounge love madonna magazine magnificat mandarin manor marriage martyr martyrs
meal meals media medical men mental mercy mission missionary missions moms monthly mothers multicultural museum musicians
national nativity needs neighbors network newman news newsletter nocturna nocturnal notes officers online open operation
organ outreach pacific parents parent pastor pastors path peace pentecost people perpetual pets pilgrims pizza planning
plate pledge pope prayers preparation presentation priest priests prison project ps pts quad queen reading readings
recovery redemption relief religious renewal resource resources respect retreat retreats revival ridge road rooms rummage
sacraments sacred safe safety saint salvation sanctity scholarship scholarships scout scouts scripture season seminar
seminarian seminarians seminary senior serra servants servers sharing shelter shepherd shop shrine sing singers singles
small soup speakers spirituality sports spring stations stewardship street students summer supper symposium table
tagalog taize talent teachers teaching tech theology thrift today tour tours town tradition trinity troop trunk tutoring
unidos united university valley vespers veterans victory vietnamese village vincentian virtual vocation vocations
volunteering walking way weekly wellness wine winter wisdom word works workshop worship writing year years youth zoom
blvd rd st. ave. dr. ln. hwy suite unit bldg building hall-room parish-office
general actual budget difference total goal ytd variance envelope envelopes weekly offertory sacrificial loose
initiation rite christian meditation contemplative tiny treasures announcements bulletin auction gala raffle
residence emeritus retired senior associate parochial vicar administrator principal superintendent assistant
manager secretary receptionist bookkeeper coordinator coordinators facilities maintenance custodian sacristan organist
cantor pianist accompanist teacher teachers counselor nurse webmaster editor chancellor notary controller
ma'am sir hon honorable mayor governor senator councilmember supervisor congresswoman congressman assemblymember
cemetery cemeteries mausoleum funeral funerals mortuary cremation
i'm i'll i've i'd we're we'll we've you're you'll it's that's let's
mundi caeli coeli maris stella mater dei domini christi agnus opus imago patri deo filii spiritus sancti sanctus sancta
ecclesia angelorum pacis misericordiae sapientiae gratia plena benedicta salve ave maria' carismatico carismatica
chaplet chaplets messiah guadalupano guadalupana guadalupanas purisima posada juvenil adoracion santisimo sacramento
""".split())

# public figures (saints, popes, founders, historical clergy) who may appear without a title; parish and school
# patrons are added from the entity names in the committed data (public_figures()).
PUBLIC_FIGURES = frozenset({
    "frank duff", "padre pio", "junipero serra", "juan crespi", "basil moreau", "don bosco", "john bosco", "mother teresa",
    "dorothy day", "martin luther king", "john muir", "oscar romero", "carlo acutis", "pier giorgio frassati",
    "fulton sheen", "michael barber", "floyd begin", "john cummins", "allen vigneron", "salvatore cordileone",
    "gregory boyle", "bill odonnell", "thomas dooley", "eugenio pacelli", "chiara lubich", "francis xavier",
    "ignatius loyola", "teresa avila", "therese lisieux", "john paul", "benedict xvi", "maximilian kolbe",
    "elizabeth seton", "kateri tekakwitha", "juan diego", "lorenzo ruiz", "andrew kim", "pedro calungsod",
    "vincent de paul", "louise de marillac", "frederic ozanam", "michael mcgivney", "katharine drexel",
    "frances cabrini", "rose philippine duchesne", "damien molokai", "martin de porres", "thomas aquinas",
    "catherine siena", "francis assisi", "anthony padua", "clare assisi", "john vianney", "mary magdalen",
    "mary magdalene", "john baptist", "peter martyr", "joan arc", "louis bertrand", "charles borromeo",
    "rose lima", "albert great", "leo great", "joseph worker", "john neumann", "pope francis", "pope leo",
    "leo xiv", "john xxiii", "paul vi", "john paul ii", "benedict", "faustina kowalska", "edith stein",
    "jose sanchez del rio", "miguel pro", "toribio romo", "andre bessette", "isaac jogues", "junipero",
    "john henry newman", "flannery oconnor", "g k chesterton", "gk chesterton", "thomas merton", "henri nouwen",
    "scott hahn", "jeff cavins", "bishop barron", "robert barron", "ignatius of loyola", "dorothy stang",
    "frank sheed", "maisie ward", "jacques maritain", "walker percy", "c s lewis", "cs lewis", "j r r tolkien",
})

_GIVEN_FILE, _SURNAME_FILE, _WORDS_FILE = (HERE / "privacy_given_names.txt", HERE / "privacy_surnames.txt",
                                           HERE / "privacy_common_words.txt")


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _lines(path: Path):
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            yield ln


PREFIX = re.compile(r"^(the most reverend|most rev\.?|rev\.? mr\.?|rev\.? fr\.?|rev\.?|very rev\.?|fr\.?|father|msgr\.?|monsignor|"
                    r"deacon|dcn\.?|dn\.?|mr\.?|mrs\.?|ms\.?|dr\.?|sr\.?|sister|bishop)\s+", re.I)


def person_key(n: str) -> str:
    """Same normalisation as scripts/build_real_seed.py and scripts/build_briefings.py."""
    s = _ascii(n)
    s = re.sub(r'"[^"]*"', " ", s).split(",")[0]
    prev = None
    while prev != s:
        prev, s = s, PREFIX.sub("", s.strip())
    toks = [t for t in re.sub(r"[^a-z\- ]", " ", s.lower()).split() if len(t) > 1 and t not in {"sj", "op", "svd", "jcl", "jd", "fcj"}]
    return " ".join(toks)


@lru_cache(maxsize=1)
def _given_names() -> tuple[frozenset, frozenset]:
    """(given names, given names that are also ordinary words or very short). See privacy_given_names.txt."""
    names, ambiguous = set(), set()
    for ln in _lines(_GIVEN_FILE):
        if ln.startswith("~"):
            ambiguous.add(ln[1:])
            names.add(ln[1:])
        else:
            names.add(ln)
    return frozenset(names), frozenset(ambiguous)


@lru_cache(maxsize=1)
def _surnames() -> frozenset:
    return frozenset(_lines(_SURNAME_FILE))


@lru_cache(maxsize=1)
def _common_words() -> frozenset:
    return frozenset(_lines(_WORDS_FILE))


def _load(rel: str):
    p = ROOT / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@lru_cache(maxsize=1)
def domain_vocabulary() -> frozenset:
    """Church, calendar and place words: the briefings' base list, the extra list above, and every word of an entity,
    city or organisation name in the committed data (parish patrons, halls, schools, charities), except words that are
    given names or frequent surnames (so "Mercy Brown Bag" does not make "Brown" an ordinary word)."""
    words = {_ascii(w).lower().strip(".") for w in COMMON_BASE | _EXTRA}
    given, _ = _given_names()
    sur = _surnames()
    ents = (_load("data/seed/entities.json") or {}).get("entities", [])
    orgs = (_load("data/real/ecosystem/orgs.json") or {}).get("records", [])
    named = [(e.get("name") or "") + " " + (e.get("city") or "") for e in ents]
    named += [o.get("name") or "" for o in orgs if o.get("type") not in ("kofc_council", "kofc_assembly")]
    for n in named:  # council names honour individual Knights; their words are not vocabulary
        for w in re.findall(r"[A-Za-zÀ-ÿ]+", n):
            w = _ascii(w).lower()
            if len(w) > 1 and w not in given and w not in sur:
                words.add(w)
    return frozenset(words)


@lru_cache(maxsize=1)
def vocabulary() -> frozenset:
    """Domain vocabulary plus ordinary words (privacy_common_words.txt)."""
    return domain_vocabulary() | _common_words()


_PATRON_NAME = re.compile(r"\b(?:St|Sts|Saint|Saints|San|Santa|Santo|Ste)\.?\s+((?:[A-Z][a-zà-ÿ'’\-]+\s*(?:of|the|de|del|and|&)?\s*){1,4})")


@lru_cache(maxsize=1)
def public_figures() -> frozenset:
    """PUBLIC_FIGURES plus the patrons named in the committed entity and organisation names ("St. Mary Magdalen")."""
    out = set(PUBLIC_FIGURES)
    ents = (_load("data/seed/entities.json") or {}).get("entities", [])
    orgs = (_load("data/real/ecosystem/orgs.json") or {}).get("records", [])
    for n in [e.get("name") or "" for e in ents] + [o.get("name") or "" for o in orgs]:
        for m in _PATRON_NAME.finditer(n):
            k = person_key(re.sub(r"\b(?:Parish|Church|School|Catholic|Elementary|High|Academy|Cemetery|Mission|Conference|"
                                  r"Basilica|Cathedral|Council|Center|Community)\b.*$", "", m.group(1)))
            k = " ".join(t for t in k.split() if t not in {"of", "the", "de", "del", "and"})
            if len(k.split()) >= 2:
                out.add(k)
    return frozenset(out)


@lru_cache(maxsize=1)
def public_names() -> tuple[frozenset, frozenset]:
    """Keys of people named in public roles: (full person keys, (first, last) pairs)."""
    names = []
    seed = _load("data/seed/persons.json")
    for p in (seed or {}).get("persons", []):
        if (p.get("attrs") or {}).get("private"):
            continue
        names.append(p["name"])
        names += (p.get("attrs") or {}).get("also_listed_as") or []
    for s in (_load("data/real/chancery_staff.json") or {}).get("staff", []):
        if not s.get("is_team_or_office"):
            names.append(s["name"])
    for a in (_load("data/real/oakland/official_appointments.json") or {}).get("appointments", []):
        names.append(a["name"])
    for r in (_load("data/real/schools_directory.json") or {}).get("records", []):
        if r.get("principal"):
            names.append(r["principal"])
    # staff named with a staff title on a parish's own website or in its bulletin's staff box
    for r in (_load("data/real/parish_sites.json") or {}).get("records", []):
        for it in ((r.get("staff") or {}).get("items") or []):
            if staff_row_ok(it.get("name") or "", it.get("title"))[0]:
                names.append(it["name"])
    for f in sorted((ROOT / "data/real/enrichment/bulletins").glob("*.json")):
        for b in json.loads(f.read_text(encoding="utf-8")).get("bulletins", []):
            for it in b.get("staff") or []:
                if staff_row_ok(it.get("name") or "", it.get("title"))[0]:
                    names.append(it["name"])
    keys = {person_key(n) for n in names} - {""}
    pairs = {(k.split()[0], k.split()[-1]) for k in keys if len(k.split()) >= 2}
    return frozenset(keys), frozenset(pairs)


def is_public_figure(span: str) -> bool:
    """A saint, pope, founder or parish patron: the span's name key equals a public figure's (initials and particles
    aside), e.g. "Frank Duff", "Elizabeth Seton", "John Paul II"."""
    toks = [t for t in person_key(span).replace("-", " ").split() if t not in PARTICLES and len(t) > 1
            and not re.fullmatch(r"[ivx]+", t)]
    if not toks:
        return False
    key = " ".join(toks)
    figs = public_figures()
    if key in figs:
        return True
    return any(" ".join(t for t in f.split() if t not in PARTICLES) == key for f in figs)


def is_public_name(span: str) -> bool:
    if is_public_figure(span):
        return True
    k = person_key(span)
    if not k:
        return False
    keys, pairs = public_names()
    if k in keys:
        return True
    t = k.split()
    return len(t) >= 2 and (t[0], t[-1]) in pairs


# ---------------------------------------------------------------- name spotting
_L = r"A-Za-zÀ-ÖØ-öø-ÿĀ-žƠ-ưḀ-ỿ"
TOKEN_RE = re.compile(rf"[{_L}](?:[{_L}'’\-]*[{_L}])?\.?|[†✟✝©&+]")
LAY_TITLE = re.compile(r"^(mr|mrs|ms|mx|miss|dr|sra|srta|ma)\.?$", re.I)
CLERGY_TITLE = re.compile(r"^(fr|father|rev|revs|reverend|msgr|monsignor|deacon|deacons|dcn|dn|diacono|diácono|bishop|archbishop|"
                          r"cardinal|pope|sr|srs|sister|sisters|hermana|br|brother|padre|fray|abbot|abbess|mother|friar|pastor)\.?$", re.I)
CLERGY_LEAD = re.compile(r"^(the|very|most|rt|right)\.?$", re.I)   # "The Most Rev.", "Very Rev.": a title only before one
SAINT_BEFORE = re.compile(r"(?:\bSt|\bSts|\bSte|Saint|Saints|\bSan|\bSanta|\bSanto|\bSão|\bSao|Our\s+Lady\s+of|Nuestra\s+Se[ñn]ora\s+de|"
                          r"Blessed|\bBl|Pope|Venerable|Servant\s+of\s+God|Knights\s+of|Daughters\s+of|Sisters\s+of|"
                          r"Brothers\s+of|Order\s+of|Society\s+of|Congregation\s+of|Missionaries\s+of|Servants\s+of|Friends\s+of|"
                          r"Ministers\s+of|Church\s+of|Parish\s+of|School\s+of|Feast\s+of|Solemnity\s+of|Memorial\s+of)\.?\s*$", re.I)
PARTICLES = {"de", "del", "la", "las", "los", "da", "das", "do", "dos", "di", "van", "von", "der", "den", "le", "du", "y", "e"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq", "phd", "md"}
ABBREV_DOT = {"st", "sts", "mr", "mrs", "ms", "dr", "jr", "sr", "fr", "rev", "msgr", "dcn", "dn", "br", "sra", "srta", "mt", "ft", "ma",
              "ave", "blvd", "rd", "co", "inc", "no", "vol", "op", "sj", "svd", "omi"}
SPANISH_MALE = {"juan", "jose", "carlos", "luis", "jorge", "miguel", "pedro", "francisco", "antonio", "manuel",
                "javier", "rafael", "roberto", "ricardo", "fernando", "alejandro", "arturo", "mario", "raul",
                "sergio", "victor", "oscar", "ruben", "jesus", "alberto", "enrique", "jaime", "eduardo", "gerardo"}
SENSITIVE_X = re.compile(
    SENSITIVE.pattern + r"|✟|✝|\bfuneral|\bmemorial\s+(?:mass\s+)?for|\bwake\b|\bvelorio|\bnovenario|\bb-?day\b|"
    r"\bbirthday|cumplea[ñn]os|\b(?:for\s+the\s+)?health\s+of\b|\bsalud\s+de\b|\bpor\s+la\s+salud|\bhealing\s+of|\brecovery\s+of|"
    r"\bin\s+honou?r\s+of|\bdied\b|\brepose\b|well-being|\bdonors?\b|\bdonantes\b|\bhonor\s+roll|\bthank\s+you\s+to\b|"
    r"\bgracias\s+a\b|\bpray(?:ers?)?\s+for\b|\boremos\s+por\b|\borar\s+por\b|\bobituar|\bfalleci|\bq\.?e\.?p\.?d\b|"
    r"\banniversary\s+of\s+(?:the\s+)?death|\bdeath\s+of\b|\bsurviv(?:ed|ors)\b|\bhospitali[sz]ed|\bnewly\s+(?:married|weds)|"
    r"\bwedding\b|\bquincea|\bbaptism\s+of\b|\bwelcome\s+to\s+(?:our\s+)?(?:newly|new\s+members)", re.I)
MARKER_AFTER = re.compile(r"^\s*(?:\+|†|✟|✝|\(\s*(?:rip|r\.i\.p\.?|d|dec|deceased|bday|b-day|birthday|health|sick)\s*\)|--\s*d\b|-\s*d\b)", re.I)
CONTACT_CUE = re.compile(r"\b(?:contact(?:o|ar|e)?|call|calling|llame|llamar|e-?mail|text|rsvp|questions\??|info(?:rmation|rmación)?|"
                         r"led\s+by|coordinated\s+by|chaired\s+by|organi[sz]ed\s+by|hosted\s+by|presented\s+by|run\s+by|"
                         r"sponsored\s+by|courtesy\s+of|thanks\s+to|thank\s+you,?|gracias\s+a|coordinator|coordinadora?|"
                         r"chair(?:person|man|woman)?|president[ae]?|grand\s+knight|leader|captain|see|ask|speak\s+(?:to|with)|"
                         r"reach\s+out\s+to|comun[ií]quese\s+con|hable\s+con)\s*[:\-–—,]?\s*$", re.I)
# A contact cue followed by a lone first name, or a list of them ("contact Rich or Sharon at the email above", "contact
# Joy at"): the name rules above need a known given name with a surname, or an unambiguous given name, so short or
# common-word first names slipped through (2026-10-31). A capitalised word right after the name ("contact Faith
# Formation") makes it an office or ministry, not a person; titles and office words are never names here.
CONTACT_NAME = re.compile(
    r"\b(?:contact(?:o|ar|e)?|call|e-?mail|text|ask(?:\s+for)?|speak\s+(?:to|with)|talk\s+to|reach\s+out\s+to|"
    r"llam[ae]\s+a|hable\s+con|comun[ií]quese\s+con|cont[aá]ctenos\s+con)\s+"
    r"((?-i:[A-Z][a-zà-ÿ'’]+)\b(?:\s*(?:,|\bor\b|\band\b|&|/|\by\b|\bo\b)\s*(?-i:[A-Z][a-zà-ÿ'’]+)\b)*)(?!\s*(?-i:[A-Z]))", re.I)
NOT_CONTACT_NAME = {"us", "our", "the", "your", "parish", "office", "rectory", "father", "fr", "deacon", "dcn", "sister", "sr", "brother",
                    "br", "pastor", "msgr", "monsignor", "bishop", "st", "saint", "san", "santa", "him", "her", "them", "me", "one",
                    "any", "either", "each", "all", "today", "now", "ahead", "back", "info", "information", "nosotros", "la", "el",
                    "los", "las", "este", "esta", "this", "that", "here", "there", "below", "above", "page", "website", "email",
                    "hospitality", "music", "liturgy", "youth", "sacristan", "reception", "registration", "stewardship", "development",
                    "finance", "administration", "maintenance", "school", "preschool", "ministry", "ministries", "outreach",
                    "bereavement", "sacraments", "confirmation", "baptism", "baptisms", "marriage", "weddings", "funerals", "ushers",
                    "lectors", "catechists", "volunteers", "staff", "secretary", "bookkeeper", "receptionist", "coordinator",
                    "christ", "jesus", "god", "lord", "spirit", "in", "on", "at", "to", "for", "by", "from", "an", "a", "and",
                    "or", "if", "when", "before", "after", "during", "it", "is", "we", "you", "bulletin", "bulletins", "calendar",
                    "schedule", "flyer", "details", "now", "toll", "free"}
EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+|\[\s*email\s*protected\s*\]", re.I)
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}\b(?:\s*(?:x|ext\.?)\s*\d+)?")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
NAMEY = ("given", "surname", "both", "unknown")
_LINK_WORDS = {"and", "y", "e", "or", "with", "con", "for", "por", "to", "by", "from", "at", "para", "of", "de"}
STREET_AFTER = re.compile(r"^\s+(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|way|ct|court|ln|lane|pl|place|"
                          r"pkwy|parkway|hwy|highway|terrace|ter|circle|cir|square|sq)\b\.?(?=\s*(?:[,;:)\]]|$|\d|(?-i:[a-z])|#|"
                          r"(?:suite|ste|apt|unit)\b))", re.I)
FAMILY_RE = re.compile(rf"\b(?:[Tt]he\s+)?([A-Z][{_L}'’\-]+(?:\s+[A-Z][{_L}'’\-]+)?)\s+(?:[Ff]amil(?:y|ies|ia)|[Hh]ousehold|[Cc]lan)\b|"
                       rf"\b(?:[Ff]amilia|[Ff]amily\s+of)\s+([A-Z][{_L}'’\-]+(?:\s+[A-Z][{_L}'’\-]+)?)")


def _is_cap(t: str) -> bool:
    return t[:1].isupper()


def _core(t: str) -> str:
    c = _ascii(t.replace("’", "'")).lower()
    c = re.sub(r"'s$", "", c)
    return c.strip(".'-")


def _tokens(text: str):
    return [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text)]


def _joined(text: str, a, b) -> bool:
    """True when token b continues the run of token a: only spaces (or a nickname quote/paren) between them, and a
    not a word that ends a sentence ("Smith. The")."""
    if a[2].endswith(".") and len(a[2]) > 2 and _core(a[2]) not in ABBREV_DOT:
        return False
    gap = text[a[1]:b[0]]
    return gap != "" and re.fullmatch(r"[ \t]{1,3}|[ \t]*[\"“(][ \t]*|[ \t]*[\"”)][ \t]*", gap) is not None


def _run_from(text, toks, i):
    """Index just past the capitalised run (initials, particles and nicknames allowed) that starts at toks[i]."""
    j = i + 1
    while j < len(toks) and _joined(text, toks[j - 1], toks[j]):
        w = toks[j][2]
        if _is_cap(w) or (w.lower() in PARTICLES and j + 1 < len(toks) and _is_cap(toks[j + 1][2])
                          and _joined(text, toks[j], toks[j + 1])):
            j += 1
            continue
        break
    while j - 1 > i and toks[j - 1][2].lower() in PARTICLES:
        j -= 1
    return j


SAINT_TOKEN = re.compile(r"^(?:st|sts|ste|saint|saints|san|santa|santo|sao|são|blessed|bl|venerable|pope)\.?$", re.I)
_GENERIC_TAIL = {"parish", "church", "school", "catholic", "elementary", "high", "academy", "cemetery", "center", "centre",
                 "college", "university", "mission", "basilica", "cathedral", "community", "the", "of", "and", "site", "campus"}


CHURCH_PHRASES = {tuple(p.split()) for p in (
    "thanh le", "chua nhat", "duc me", "giao xu", "thanh ca", "ca doan", "thanh the", "cha xu", "thanh duong", "giao ly",
    "regina mundi", "regina caeli", "regina coeli", "salve regina", "stella maris", "ave maria", "mater dei", "agnus dei",
    "opus dei", "imago dei", "corpus christi", "gloria patri", "santa maria", "maria auxiliadora", "jesus sacramentado",
    "divina misericordia", "virgen maria", "sagrado corazon", "nino dios", "santo nino", "santo nino de cebu",
    "simbang gabi", "misa de gallo", "las posadas", "via crucis", "lectio divina", "pueblo santo", "jose obrero",
    "san jose obrero", "cristo rey", "christ king", "our lady", "lady of", "holy family", "sagrada familia", "la purisima")}


@lru_cache(maxsize=1)
def place_phrases() -> frozenset:
    """Multi-word names of places and organisations in the committed data ("Pleasant Hill", "Walnut Creek", "Mary Help
    of Christians", "Holy Names"), as tuples of lower-case words. Inside a run they read as places, not people."""
    names = []
    ents = (_load("data/seed/entities.json") or {}).get("entities", [])
    for e in ents:
        names += [e.get("name") or "", e.get("city") or ""]
    for o in (_load("data/real/ecosystem/orgs.json") or {}).get("records", []):
        if o.get("type") not in ("kofc_council", "kofc_assembly"):
            names += [o.get("name") or "", o.get("city") or ""]
    for r in (_load("data/real/parishes_directory.json") or {}).get("records", []):
        names.append(r.get("city") or "")
    pl = _load("data/real/civic/places_diocese.json") or {}
    for r in pl.get("places", pl.get("records", [])) if isinstance(pl, dict) else []:
        names.append(r.get("name") or "")
    out = set(CHURCH_PHRASES)
    for n in names:
        toks = [_core(t) for t in re.findall(r"[A-Za-zÀ-ÿ'’]+", n)]
        toks = [t for t in toks if t]
        while toks and (SAINT_TOKEN.match(toks[0]) or toks[0] in _GENERIC_TAIL):
            toks = toks[1:]
        for cut in range(len(toks), 1, -1):
            sub = tuple(toks[:cut])
            if len(sub) >= 2 and sub[-1] not in _GENERIC_TAIL:
                out.add(sub)
    return frozenset(out)


def _fold(c: str, words) -> bool:
    """c or its singular is in words ("rehearsals" -> "rehearsal", "ministries" -> "ministry")."""
    if c in words:
        return True
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("'s", "")):
        if c.endswith(suf) and len(c) - len(suf) >= 3 and c[: -len(suf)] + rep in words:
            return True
    return False


def _kind(tok: str, upper_text: bool = False) -> str:
    """How a word reads: 'domain' (church, calendar, place), 'surname' (frequent surname), 'common' (ordinary word),
    'given' (a given name), 'initial', 'suffix' (Jr., III), 'acronym' (GK, SB, OFM in mixed-case text), 'particle',
    'title' (a clergy or lay title) or 'unknown' (most likely part of a name)."""
    c = _core(tok)
    if c in PARTICLES and tok[:1].islower():
        return "particle"
    if not c or len(c) == 1:
        return "initial"
    if CLERGY_TITLE.match(tok) or LAY_TITLE.match(tok):
        return "title"
    if c in SUFFIXES or re.fullmatch(r"[ivx]+", c):
        return "suffix"
    letters = re.sub(r"[^A-Za-z]", "", tok)
    if not upper_text and 2 <= len(letters) <= 5 and letters.isupper():
        return "acronym"
    if SAINT_TOKEN.match(tok):
        return "saint"
    if c in domain_vocabulary():
        return "given_domain" if c in _given_names()[0] else "domain"
    if c in _surnames():
        return "both" if c in _given_names()[0] else "surname"
    if c in _common_words():
        return "common"
    if c in _given_names()[0]:
        return "given"
    if _fold(c, domain_vocabulary()):
        return "domain"
    if _fold(c, _common_words()):
        return "common"
    return "unknown"


def _segments(kinds, cores):
    """Name segments within a capitalised run: maximal stretches of given names, surnames, unknown words and initials
    (particles and suffixes inside). A new segment starts at a given name that follows a surname once the segment
    already has a given name ("Cath Sullivan Agatha Leong" -> two names)."""
    given = _given_names()[0]
    for idx, k in enumerate(kinds):  # "JC Orton": two capitals before a name are initials
        if k == "acronym" and len(cores[idx]) == 2 and idx + 1 < len(kinds) and kinds[idx + 1] in ("surname", "both", "unknown", "given"):
            kinds[idx] = "initial"
    for idx, k in enumerate(kinds):  # "Mary", "Paul", "Grace": a first name only before a surname, not in "Divine Mercy"
        if k == "given_domain":
            first = idx == 0 or kinds[idx - 1] not in ("domain", "common", "given_domain", "saint", "public", "held") \
                or cores[idx - 1] in _LINK_WORDS
            kinds[idx] = "given" if first and idx + 1 < len(kinds) and kinds[idx + 1] in ("surname", "both", "unknown", "given") \
                else "domain"
    segs, a, has_given = [], None, False
    for idx, k in enumerate(kinds):
        inside = a is not None
        if k in NAMEY or k == "initial" or (inside and k in ("particle", "suffix")):
            if inside and k == "given" and has_given and kinds[idx - 1] in ("surname", "unknown") and cores[idx] in given:
                segs.append((a, idx))
                a, has_given = idx, True
                continue
            if a is None:
                a, has_given = idx, False
            has_given = has_given or k in ("given", "both")
            continue
        if inside:
            segs.append((a, idx))
            a = None
    if a is not None:
        segs.append((a, len(kinds)))
    out = []
    for a, b in segs:
        while a < b and kinds[a] in ("particle", "suffix"):
            a += 1
        while b > a and kinds[b - 1] == "particle":
            b -= 1
        if b > a:
            out.append((a, b))
    return out


def name_spans(text: str, mode: str = "standard", sensitive: bool | None = None):
    """[(start, end, kind)] for spans that look like a private person's name. kind: lay_title | given_name | surname |
    sensitive | marker | contact | strict. Public people (clergy titles, the public lists) are not returned."""
    if not isinstance(text, str) or not re.search(r"[A-Z]", text):
        return []
    given, ambiguous = _given_names()
    words = [t for t in _tokens(text) if t[2] not in "†✟✝©&+"]
    if sensitive is None:
        sensitive = bool(SENSITIVE_X.search(text))
    upper_text = sum(c.isupper() for c in text) > 0.6 * max(1, sum(c.isalpha() for c in text))
    spans = []
    i = 0
    while i < len(words):
        s, e, w = words[i]
        c = _core(w)
        if not _is_cap(w):
            i += 1
            continue
        nxt = words[i + 1][2] if i + 1 < len(words) else ""
        if CLERGY_LEAD.match(w) and (CLERGY_TITLE.match(nxt) or CLERGY_LEAD.match(nxt)) and _joined(text, words[i], words[i + 1]):
            i += 1
            continue
        j = _run_from(text, words, i)
        run = words[i:j]
        kinds = [_kind(t[2], upper_text) for t in run]
        cores = [_core(t[2]) for t in run]
        phrases = place_phrases()
        x = 0
        while x < len(run):  # known places and organisations: "Pleasant Hill", "Mary Help of Christians"
            hit = 0
            for L in range(min(6, len(run) - x), 1, -1):
                if tuple(cores[x:x + L]) in phrases:
                    hit = L
                    break
            for y in range(x, x + hit):
                if kinds[y] not in ("title", "saint"):
                    kinds[y] = "domain"
            x += hit or 1
        # titles inside the run: a clergy or religious title makes the name segment after it public; a lay title makes
        # it a name to withhold ("Mrs. Janet Money"). "Sr. Juan" is Señor; "Winton Dr." is a street.
        skip_to = 0
        for idx, (tok, k) in enumerate(zip(run, kinds)):
            if idx < skip_to or k not in ("title", "saint"):
                continue
            t = tok[2]
            tc = cores[idx]
            after = idx + 1
            seg_end = after
            while seg_end < len(run) and kinds[seg_end] in NAMEY + ("initial", "particle", "suffix", "common", "given_domain",
                                                                   "saint") \
                    and seg_end - after < 4 and not (kinds[seg_end] in ("common", "saint") and seg_end > after):
                seg_end += 1
            spanish_senor = tc in ("sr", "srs") and after < len(run) and cores[after] in SPANISH_MALE
            if tc in ("sra", "senora") and (idx and cores[idx - 1] in ("nuestra", "ntra") or
                                             re.search(r"(?i)\b(?:nuestra|ntra\.?)\s*$", text[:tok[0]])):
                k = "saint"  # "Nuestra Sra. del Rosario": Our Lady
            if k == "title" and (LAY_TITLE.match(t) or spanish_senor):
                street = tc == "dr" and idx > 0
                if (tc == "ma" and not t.endswith(".")) or street:
                    kinds[idx] = "domain"
                    continue
                if seg_end > after:
                    spans.append((tok[0], run[seg_end - 1][1], "lay_title"))
                for x in range(idx, seg_end):
                    kinds[x] = "held"
                skip_to = seg_end
            else:
                if tc == "mother" and after < len(run) and cores[after] in ("of",):
                    kinds[idx] = "domain"
                    continue
                for x in range(idx, seg_end):
                    kinds[x] = "public"
                skip_to = seg_end
        # a run right after "St." or another patron cue is a saint or an organisation's name
        before = text[max(0, s - 40):s]
        if SAINT_BEFORE.search(before) or (i and CLERGY_WORD.match(_core(words[i - 1][2]))
                                           and re.fullmatch(r"[ \t.]+", text[words[i - 1][1]:s] or "x")):
            k0 = 0
            while k0 < len(kinds) and kinds[k0] not in ("held",) and (k0 == 0 or kinds[k0] in NAMEY + ("initial", "particle", "suffix")):
                kinds[k0] = "public"
                k0 += 1
        for a, b in _segments(kinds, cores):
            seg_k = kinds[a:b]
            real = [x for x, k in zip(range(a, b), seg_k) if k in NAMEY]
            n_real = len(real)
            ss, se = run[a][0], run[b - 1][1]
            first_real = real[0] if real else None
            kind = None
            if n_real >= 2:
                ks = [kinds[x] for x in real]
                fc = cores[first_real]
                if ks[0] in ("given", "both") and any(k in ("surname", "both", "unknown") for k in ks[1:]) and n_real <= 5:
                    # an ambiguous first word ("Grace", "Will", "Fe") needs a known surname after it, not just an unlisted word
                    if not (fc in ambiguous and (n_real > 3 or not any(k in ("surname", "both") for k in ks[1:]))):
                        kind = "given_name"
                elif n_real <= 3 and ks[0] == "surname" and any(k in ("given", "both") for k in ks[1:]):
                    kind = "surname"   # surname first: "Nguyen Van Minh", "Tran Thanh"
                elif n_real <= 3 and ks[0] == "unknown" and ks[-1] in ("surname", "both") and \
                        (sensitive or mode == "raw" or CONTACT_CUE.search(before[-34:])):
                    kind = "surname"   # an unlisted first name: "Cath Sullivan"
                if kind is None and sensitive:
                    kind = "sensitive"
            elif n_real == 1:
                x = real[0]
                single = cores[x]
                after_txt = text[run[b - 1][1]:]
                if len(single) > 2 and MARKER_AFTER.match(after_txt):
                    kind = "marker"
                elif b - a >= 2 and any(k == "initial" for k in seg_k) and kinds[x] in ("surname", "unknown") and \
                        (sensitive or mode == "raw"):
                    kind = "sensitive" if sensitive else "raw"   # "J. Builta"
            if kind is None and a == 0 and n_real >= 1 and CONTACT_CUE.search(before[-34:]):
                if n_real >= 2 or (kinds[first_real] in ("given", "both") and cores[first_real] not in ambiguous):
                    kind = "contact"
            if kind is None:
                continue
            if any(is_public_figure(text[ss:run[min(len(run), b + x) - 1][1]]) for x in (1, 2) if b + x - 1 < len(run)):
                continue  # "John Henry Newman": the saint's surname is also a place word
            if STREET_AFTER.match(text[se:]):
                continue  # "Van Buren St.", "Martin Luther King Jr. Way": an address
            # a couple: "Felix & Guilhermina Menezes", "Angelo and Ivana Matteucci"
            if kind == "given_name" and a == 0:
                m = re.search(rf"([{_L}][{_L}'’\-]*)\s*(?:&|\band\b|\by\b|\+)\s*$", text[:ss])
                if m and _core(m.group(1)) in given and _is_cap(m.group(1)) and not re.search(
                        r"(?i)\b(?:fr|father|rev|deacon|dcn|dn|msgr|bishop|sr|sister|br|brother)\.?\s*$", text[:m.start(1)]):
                    ss = m.start(1)
            spans.append((ss, se, kind))
        i = j
    for m in CONTACT_NAME.finditer(text):  # "contact Rich or Sharon", "for more information contact Joy at"
        first = _core(m.group(1).split()[0])
        if first in NOT_CONTACT_NAME or _DATE_WORDS.fullmatch(m.group(1).split()[0]):
            continue
        spans.append((m.start(1), m.end(1), "contact"))
    for m in FAMILY_RE.finditer(text):  # "the Garcia family", "Familia Lopez"
        g = 1 if m.group(1) else 2
        toks = m.group(g).split()
        if all(_kind(t, upper_text) in ("surname", "unknown", "given") for t in toks):
            spans.append((m.start(g), m.end(g), "family"))
    out = []
    for s, e, kind in spans:
        span = text[s:e]
        bare = re.sub(r"^(?:[A-Z]{1,2}\.?\s+)+(?=[A-Z][a-z])", "", span)  # "SB Bernadette Baria" -> "Bernadette Baria"
        if any(is_public_figure(x) or (not sensitive and kind != "marker" and is_public_name(x)) for x in {span, bare}):
            continue
        out.append((s, e, kind))
    return out


def redact(text: str, mode: str = "standard", sensitive: bool | None = None) -> tuple[str, int]:
    """Replace private names in a short excerpt with "[name withheld]" and drop e-mail addresses and phone numbers.
    Returns (text, number of names withheld)."""
    if not isinstance(text, str) or not text:
        return text, 0
    t = strip_contacts(text, urls=(mode == "raw"))
    spans = name_spans(t, mode, sensitive)
    if not spans:
        return t, 0
    out, last, n = [], 0, 0
    for s, e, _ in sorted(spans):
        if s < last:
            continue
        out.append(t[last:s])
        out.append(WITHHELD)
        last, n = e, n + 1
    out.append(t[last:])
    return re.sub(r"(\[name withheld\])(?:\s*(?:,|&|and|y|/|\+)\s*\[name withheld\])+", r"\1", "".join(out)), n


def strip_contacts(text: str, urls: bool = False) -> str:
    """Remove e-mail addresses and phone numbers (never stored); with urls=True (raw page snippets) also web links."""
    if not isinstance(text, str):
        return text
    t = EMAIL_RE.sub("", text)
    t = PHONE_RE.sub("", t)
    if urls:
        t = URL_RE.sub("", t)
    if t != text:
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"\(\s*\)|\[\s*\]", "", t).strip(" ,;:-–—|")
    return t


def clip(text: str, n: int) -> str:
    """First n words, keeping "[name withheld]" whole (it counts as one word)."""
    t = re.sub(r"\s+", " ", (text or "").replace(WITHHELD, "")).strip().split(" ")
    out = " ".join(t[:n]) + ("…" if len(t) > n else "")
    return out.replace("", WITHHELD)


# ---------------------------------------------------------------- schedules: Mass intentions
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:[ap]\.?\s?m\b\.?|o[mn]\b)|\bnoon\b|\bmediod[ií]a\b", re.I)
INTENTION_STRONG = re.compile(
    r"[†✟✝©]|(?<=[A-Za-zÀ-ÿ.)])\s?\+(?=[\s;,.)]|$)|\(\s*(?:rip|r\.i\.p\.?|d|dec)\s*\)|\bR\.?I\.?P\.?(?=\W|$)|--\s*D\b|"
    r"\b(?:special\s+intentions?|int\.\s*of|intenci[oó]n(?:es)?|intentions?|difunci[oó]n|difunt[oa]s?|deceased|"
    r"repose|living\s+and\s+deceased|souls?\s+in\s+purgatory|almas\s+del\s+purgatorio|q\.?e\.?p\.?d)\b", re.I)
INTENTION_WEAK = re.compile(
    r"\b(?:birthday|birthda|b-?day|cumplea[ñn]os|salud|health|conversion|conversi[oó]n|anniversary|aniversario|anniversario|"
    r"thanksgiving|acci[oó]n\s+de\s+gracias|well-being|wellbeing|recovery|healing|in\s+memory|en\s+memoria|"
    r"pro\s+populo|people\s+of\s+the\s+parish|for\s+the\s+people|req(?:uested)?\s+by|by\s+the\s+family|family)\b", re.I)
# "5:00pm Mass for <Name> (L)": the English intention form ("Misa por <Name>" likewise); a cause, a day or a feast is not a name.
MASS_FOR_NAME = re.compile(
    r"\b(?i:mass|misa)\s+(?i:for|por|para)\s+(?:(?i:sr|br|fr|sister|brother|mr|mrs|ms|dr)\.?\s+)?(?!(?i:all|the|our|poor|holy|"
    r"peace|life|vocations?|healing|unity|souls?|faithful|departed|unborn|sick|parishioners|people|famil(?:y|ies)|children|students|"
    r"deceased|dead|st|sts|saint|saints|san|santa|santo|sunday|monday|tuesday|wednesday|thursday|friday|saturday|sun|mon|tue|tues|"
    r"wed|thu|thur|thurs|fri|sat|domingo|s[aá]bado|christmas|easter|ash|new|thanksgiving|immaculate|assumption|pentecost|epiphany|"
    r"ascension|first|english|spanish|youth|school|couples|seniors|veterans|mother'?s|father'?s|mothers|fathers|those|priests|"
    r"vigil|weekdays?|daily|todos|los|las|la|el|nuestra|nuestros|difuntos|almas|paz|vida)\b)[A-ZÁÉÍÓÚÑ][a-záéíóúñ'’]+")
OCR_CROSS_RE = re.compile(r"(?:[ap]m|noon)[\s_—–-]+[A-Z][A-Za-z'’.\-]+(?:\s+(?:&\s+)?[A-Z][A-Za-z'’.\-]+){0,3}\s+(?:t|T|Tt|tT|TT)\b")
_DATE_WORDS = re.compile(r"\b(?:sun|mon|tue|tues|wed|thu|thur|thurs|fri|sat)(?:day|nesday|urday|sday|rsday)?\b\.?|"
                         r"\b(?:domingo|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado)\b|"
                         r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b|"
                         r"\b\d{1,2}\s+de\s+[a-z]+\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|"
                         r"\b(?:vigil|saturday\s+vigil|weekday|daily)\b", re.I)


def is_intention_line(line: str) -> bool:
    """A Mass-intention line: an intention marker (†, RIP, "Int. of", "For the repose of"), or a weak cue (birthday,
    health, anniversary) with a name, or a private person named next to a time ("8:00 am Mel Solis")."""
    if INTENTION_STRONG.search(line) or OCR_CROSS_RE.search(line) or MASS_FOR_NAME.search(line):
        return True
    if INTENTION_WEAK.search(line) and name_spans(line, "standard", sensitive=True):
        return True
    return bool(TIME_RE.search(line)) and bool(name_spans(line, "raw"))


def scrub_schedule_line(line: str) -> tuple[str, int]:
    """A schedule line from a bulletin or a parish page. A Mass-intention line keeps only its day, date and time
    and loses the rest ("Saturday 4:30pm [Mass intention withheld]"); other lines go through redact()."""
    if not isinstance(line, str) or not line or INTENTION_WITHHELD in line:
        return line, 0
    if is_intention_line(line):
        keep = sorted([(m.start(), m.end()) for m in _DATE_WORDS.finditer(line)] +
                      [(m.start(), m.end()) for m in TIME_RE.finditer(line)])
        head, last = [], -1
        for s, e in keep:
            if s >= last:
                head.append(line[s:e].strip())
                last = e
        return (" ".join(head + [INTENTION_WITHHELD])).strip(), 1
    return redact(line)


# ---------------------------------------------------------------- staff listings
NAME_JUNK = {"business", "music", "ministry", "ministries", "servers", "server", "lectors", "lector", "greeters", "greeter",
             "team", "office", "parish", "school", "church", "council", "committee", "dj", "dynamic", "liturgy", "altar",
             "choir", "youth", "staff", "pastoral", "faith", "formation", "religious", "education", "coordinators", "director",
             "secretary", "maintenance", "the", "and", "of", "for", "phone", "email", "managing", "partner", "collections",
             "collection", "two", "facility", "facilities", "calendar", "missions", "mission", "second", "overview",
             "company", "roofing", "inc", "llc", "dds", "dmd", "realty", "insurance", "mortuary", "funeral", "sunday", "share",
             "your", "vis", "program", "programs", "regular", "center", "services", "service", "care", "home", "next", "week"}
AD_RE = re.compile(r"funeral\s+(?:director|home|chapel|services?)|mortuary|managing\s+partner|realtor|real\s+estate|"
                   r"insurance\s+(?:agent|agency|broker|services)|roofing|attorney|law\s+(?:office|firm|group)|plumbing|\bdds\b|"
                   r"\bdmd\b|\bllc\b|\binc\b|music\s+by\b|sponsored\s+by|advertis|\bfd\s?\d{3,}|lic(?:ense)?\.?\s*#", re.I)
STAFF_TITLE_RE = re.compile(
    r"\b(?:pastor|vicar|vicario|administrat\w*|admin\b|priest|deacon|di[aá]cono|secretar\w*|manager|bookkeep\w*|accountant|"
    r"accounting|accounts\s+(?:payable|receivable)|payroll|controller|comptroller|director\w*|coordinat\w*|coord\b|principal|"
    r"associate|assoc\b|receptionist|reception|assistant|facilit\w*|maintenance|custodian|janitor|groundskeep\w*|grounds|"
    r"housekeep\w*|organist|pianist|accompanist|musician|cantor|sacristan|chaplain|in\s+residence|emerit\w*|retired|"
    r"communications|development|advancement|stewardship|steward|youth\s+minister|young\s+adult\s+minister|minister\s+of|"
    r"campus\s+minister|pastoral\s+minister|consultant|spiritual\s+director|liturgist|bulletin|webmaster|website|office|"
    r"clerk|registrar|rector|superior|chancellor|notary|superintendent|chief|officer|executive|president|vice\s+president|"
    r"treasurer|teacher|dre|ocia|rcia|catechist|specialist|analyst|technician|it\b|human\s+resources|risk|security|"
    r"operations|counsel|attorney|nurse|counselor|librarian|registrar|dean|head\s+of\s+school|headmaster|staff)\b", re.I)
PRIVATE_ROLE_RE = re.compile(
    r"council\s+(?:member|chair|president|secretary|treasurer)|(?:pastoral|finance|parish|school)\s+council|committee|"
    r"\bchair(?:man|woman|person|s)?\b|grand\s+knight|\bGK\b|trustee|board\s+(?:member|chair)|\bmember\b|volunteer|"
    r"contact\s+person|ministry\s+(?:leader|contact|lead)|team\s+(?:leader|lead|captain)|captain|representative|\brep\b|"
    r"liaison|ministers?\s+to\s+the\s+sick|homebound|"
    r"(?:council|society|club|guild|conference|circle|court|auxiliary|knights?|ladies|daughters|columbiettes|svdp|icf|yli|"
    r"cda|group|association|federation|legion|sodality|cursillo|guadalupanas?)\b[^|]{0,30}\b(?:president|vice|secretary|treasurer|"
    r"coordinator|leader|head)|\b(?:president|vice\s+president|secretary|treasurer|coordinator|leader|head)\b[^|]{0,30}\b(?:of\s+)?"
    r"(?:the\s+)?(?:council|society|club|guild|conference|circle|court|auxiliary|knights?|ladies|daughters|columbiettes|svdp|icf|"
    r"yli|cda|group|association|federation|legion|sodality|cursillo|guadalupanas?)\b", re.I)
VOLUNTEER_MINISTRY_RE = re.compile(
    r"\b(?:lectors?|ushers?|greeters?|altar\s+servers?|eucharistic\s+ministers?|extraordinary\s+ministers?|emhc|hospitality|"
    r"bereavement|prayer\s+(?:chain|group|line|shawl)|rosary|legion\s+of\s+mary|knights|columbus|catholic\s+daughters|"
    r"vincent\s+de\s+paul|svdp|guadalupanas?|cursillo|women'?s\s+(?:group|guild|club)|men'?s\s+(?:group|club)|altar\s+society|"
    r"guild|sodality|art\s+(?:&|and)\s+environment|funeral\s+(?:luncheon|reception)|coffee|donuts|bingo|choir\s+member)\b",
    re.I)
STRONG_STAFF_RE = re.compile(r"\b(?:director\w*|manager|secretar\w*|staff|minister\s+of|pastoral\s+associate|dre|organist|"
                             r"accompanist|pianist|musician|sacristan|bookkeep\w*|administrat\w*)\b", re.I)
CLERGY_NAME_RE = re.compile(r"^(?:the\s+)?(?:most\s+|very\s+)?(?:rev|fr|father|msgr|monsignor|deacon|dcn|dn|sr|sister|br|brother|"
                            r"bishop|padre|fray)\b\.?", re.I)
POSTNOMINAL_RE = re.compile(r"(?:,?\s+(?:S\.?\s?J|O\.?\s?P|S\.?\s?V\.?\s?D|O\.?\s?M\.?\s?I|O\.?\s?F\.?\s?M\.?(?:\s*,?\s*(?:Conv|Cap)\.?)?|"
                            r"C\.?\s?S\.?\s?C|S\.?\s?D\.?\s?B|C\.?\s?S\.?\s?P|S\.?\s?de\s?J|M\.?\s?F|F\.?\s?C\.?\s?J|S\.?\s?N\.?\s?J\.?\s?M|"
                            r"C\.?\s?S\.?\s?J|R\.?\s?S\.?\s?M|O\.?\s?S\.?\s?F|J\.?\s?C\.?\s?L|J\.?\s?D|Ph\.?\s?D|Ed\.?\s?D|M\.?\s?A|"
                            r"M\.?\s?Div|D\.?\s?Min|Jr|Sr|II|III|IV)\.?)+\s*$")


def clean_staff_name(n: str) -> str:
    """'Pastoral Team Ms. Carol DuPont' -> 'Ms. Carol DuPont'; 'Rey. Luis Lopez' -> 'Rev. Luis Lopez'."""
    n = unicodedata.normalize("NFC", n or "").replace("​", "").strip(" ,;/")
    n = re.sub(r"^Rey\.\s", "Rev. ", n)
    m = re.search(r"\b(Rev\.?|Fr\.?|Father|Msgr\.?|Deacon|Dcn\.?|Dn\.?|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Sr\.?|Sister)\s+[A-Z]", n)
    if m and m.start() > 0 and not re.match(r"(?i)^(?:the\s+)?(?:most|very)\s", n):
        n = n[m.start():]
    return re.sub(r"\s+", " ", n)


def staff_row_ok(name: str, title: str | None) -> tuple[bool, str | None]:
    """Is (name, title) a public clergy or staff listing? Returns (True, None) or (False, reason). Reasons:
    'not a person' (crawl junk such as "Two Collections"), 'advertisement' (a funeral home's partner),
    'ministry contact or committee member' (private, never stored), 'no staff title' (a name next to a
    ministry heading: treated as a ministry contact)."""
    n = clean_staff_name(name)
    bare = POSTNOMINAL_RE.sub("", n).strip(" ,")
    t = (title or "").replace("​", "").strip()
    key = person_key(bare).split()
    toks = bare.replace(",", " ").split()
    if (not 2 <= len(key) <= 5 or any(k in NAME_JUNK for k in key) or not all(re.fullmatch(r"[a-z\-']+", k) for k in key)
            or (not bare.isupper() and any(len(x) >= 4 and x.isupper() for x in toks)) or WITHHELD in n):
        return False, "not a person"
    if AD_RE.search(t) or AD_RE.search(n):
        return False, "advertisement"
    if PRIVATE_ROLE_RE.search(t):
        return False, "ministry contact or committee member"
    clergy = bool(CLERGY_NAME_RE.match(n) or re.match(r"(?i)^rev\.?\s+mr\b", n))
    if VOLUNTEER_MINISTRY_RE.search(t) and not STRONG_STAFF_RE.search(t) and not clergy:
        return False, "ministry contact or committee member"
    if not STAFF_TITLE_RE.search(t):
        if clergy:
            return True, None
        return False, "no staff title"
    return True, None


# ---------------------------------------------------------------- structured screening of data files
# A rule is (path, action). Paths are dotted keys from the top of the object; "[]" steps into every list item and
# "*" matches any key. Actions:
#   text      redact(..., "standard")          bulletin and page excerpts, ministry and event names
#   raw       redact(..., "raw")               page snippets cut out of running text (signal contexts, page dumps)
#   schedule  scrub_schedule_line()            Mass, confession and adoration lines
#   people    keep rows that pass staff_row_ok() (name in "name", title in "title" or "titles"); rows that fail move
#             out as {"withheld": "<neutral role text>", page/source refs} into a sibling list "<key>_withheld"
#   names     a list of name strings (directory clergy): keep public ones (clergy titles), withhold others
WITHHELD_ROLE = {"not a person": None, "advertisement": None,
                 "ministry contact or committee member": "ministry contact or committee member",
                 "no staff title": "ministry contact"}


def _walk(obj, parts, fn):
    if not parts:
        return
    head, rest = parts[0], parts[1:]
    if head == "[]":
        if isinstance(obj, list):
            for x in obj:
                _walk(x, rest, fn)
        return
    if not isinstance(obj, dict):
        return
    keys = list(obj.keys()) if head == "*" else ([head] if head in obj else [])
    for k in keys:
        if not rest:
            fn(obj, k)
        else:
            _walk(obj[k], rest, fn)


def _split(path: str):
    return [p for p in re.split(r"\.|(\[\])", path) if p]


def screen(obj, rules, where: str = "") -> collections.Counter:
    """Apply privacy rules in place. Returns counts: names_withheld, intentions_withheld, people_withheld,
    junk_rows_dropped, contacts_removed."""
    cnt = collections.Counter()

    def do_text(mode):
        def fn(parent, k):
            if re.search(r"(?:^|_)urls?$|^href$|^link$", str(k), re.I):
                return  # URLs are references, never screened text
            v = parent[k]
            if isinstance(v, list):
                for i, x in enumerate(v):
                    if isinstance(x, str):
                        nv, n = redact(x, mode)
                        if nv != x:
                            cnt["contacts_removed"] += int(n == 0)
                            cnt["names_withheld"] += n
                            v[i] = nv
                parent[k] = [x for x in v if not (isinstance(x, str) and not x.strip())]
                return
            if isinstance(v, str) and not re.match(r"^(?:https?://|www\.)\S+$", v.strip()):
                nv, n = redact(v, mode)
                if nv != v:
                    cnt["contacts_removed"] += int(n == 0)
                    cnt["names_withheld"] += n
                    parent[k] = nv
        return fn

    def do_schedule(parent, k):
        v = parent[k]
        if isinstance(v, str):
            nv, n = scrub_schedule_line(v)
            if nv != v:
                if INTENTION_WITHHELD in nv:
                    cnt["intentions_withheld"] += 1
                else:
                    cnt["names_withheld"] += n
                    cnt["contacts_removed"] += int(n == 0)
                parent[k] = nv

    def do_people(parent, k, where=""):
        rows = parent[k]
        if not isinstance(rows, list):
            return
        keep, held = [], list(parent.get(k + "_withheld") or [])
        for r in rows:
            if not isinstance(r, dict) or not r.get("name"):
                keep.append(r)
                continue
            # titles are screened first, so a second pass decides on the same text (idempotent)
            if isinstance(r.get("title"), str):
                r["title"], n = redact(r["title"])
                cnt["names_withheld"] += n
            if isinstance(r.get("titles"), list):
                r["titles"] = [redact(x)[0] if isinstance(x, str) else x for x in r["titles"]]
            title = r.get("title")
            if title is None and r.get("titles"):
                title = next((x for x in r["titles"] if x), None)
            ok, why = staff_row_ok(r["name"], title)
            if not ok and isinstance(title, str) and CLERGY_NAME_RE.match(title.strip()) and not CLERGY_NAME_RE.match(r["name"].strip()):
                ok = staff_row_ok(title, r["name"])[0]  # columns swapped on the page: "Pastoral Care" | "Deacon Timothy Myers"
            if ok:
                keep.append(r)
                continue
            role = WITHHELD_ROLE.get(why)
            if role is None:
                cnt["junk_rows_dropped"] += 1
                continue
            cnt["people_withheld"] += 1
            ref = {kk: r[kk] for kk in ("page", "source_url", "sources") if kk in r}
            if "sources" in ref:
                ref["sources"] = [{x: s.get(x) for x in ("source_url", "fetched_at", "method", "bulletin_date", "page") if x in s}
                                  for s in ref["sources"]]
            held.append({"withheld": f"{role} listed{(' ' + where) if where else ''}", **ref})
        parent[k] = keep
        if held:
            parent[k + "_withheld"] = held

    def do_names(parent, k):
        v = parent[k]
        if not isinstance(v, list):
            return
        out = []
        for x in v:
            if isinstance(x, str) and not (CLERGY_NAME_RE.match(x.strip()) or is_public_name(x)):
                cnt["people_withheld"] += 1
                continue
            out.append(x)
        parent[k] = out

    def do_board(parent, k):
        """Form 990 Part VII: officers and key employees who are staff stay (paid, or working 20+ hours a week: public
        roles), and so do clergy; unpaid lay board members (a few hours a week) are a lay committee, so their names are
        withheld (title, hours and flags stay)."""
        rows = parent[k]
        if not isinstance(rows, list):
            return
        for r in rows:
            if not isinstance(r, dict) or not r.get("name") or r["name"] == WITHHELD:
                continue
            paid = sum((r.get(x) or 0) for x in ("comp_org", "comp_related", "comp_other")) > 0
            clergy = bool(re.match(r"(?i)^(?:the\s+)?(?:most\s+|very\s+|right\s+)?(?:rev|reverend|fr|father|msgr|monsignor|"
                                   r"bishop|archbishop|deacon|sr|sister|br|brother)\b", r["name"].strip()))
            staff = (r.get("hours_per_week") or 0) >= 20
            if paid or clergy or staff:
                continue
            r["name"] = WITHHELD
            cnt["people_withheld"] += 1

    actions = {"text": do_text("standard"), "raw": do_text("raw"), "schedule": do_schedule, "people": do_people,
               "names": do_names, "board": do_board}
    for rule in rules:
        path, action, *arg = rule
        fn = actions[action]
        _walk(obj, _split(path), (lambda parent, k, fn=fn, arg=arg: fn(parent, k, *arg)) if arg else fn)
    return cnt


# Field rules per data file (paths from the top of each JSON file).
BULLETIN_RECORD_RULES = [
    ("schedules.*[].line", "schedule"),
    ("ministries[].name", "text"),
    ("staff", "people", "in the bulletin"),
    ("events[].title", "text"),
    ("volunteer_needs[].text", "text"),
    ("second_collections[].purpose", "text"),
    ("sacramental_counts[].context", "raw"),
    ("offertory[].label", "text"), ("offertory[].headers", "text"),
    ("bma[].label", "text"), ("bma[].headers", "text"), ("bma[].context", "raw"),
]
RULES = {
    "bulletins": [("bulletins[]." + r[0], *r[1:]) for r in BULLETIN_RECORD_RULES],
    "parish_sites": [
        ("records[].mass_schedule.raw", "raw"),
        ("records[].mass_schedule.parsed[].line", "schedule"),
        ("records[].staff.items", "people", "on the parish website"),
        ("records[].ministries.items", "text"),
        ("records[].bulletin.text", "text"),
        ("records[].pages[].title", "text"),
        ("records[].discovered_links[].text", "text"),
    ],
    "parish_profiles": [
        ("records[].mass_times.directory.text", "schedule"),
        ("records[].staff", "people", "in the parish's bulletin or on its website"),
        ("records[].ministries[].name", "text"),
        ("records[].upcoming_events[].title", "text"),
        ("records[].volunteer_and_donation_needs[].text", "text"),
        ("records[].second_collections[].purpose", "text"),
        ("records[].sacramental_counts[].context", "raw"),
        ("records[].offertory.rows[].label", "text"), ("records[].offertory.rows[].headers", "text"),
        ("records[].bishops_ministries_appeal[].label", "text"), ("records[].bishops_ministries_appeal[].headers", "text"),
        ("records[].bishops_ministries_appeal[].context", "raw"),
        ("records[].charity_links[].detail", "text"),
        ("records[].notes[].text", "text"),
        ("records[].transition.text", "text"), ("records[].transition.summary", "text"),
    ],
    "parish_signals": [
        ("records[].religious_clergy[].context", "raw"),
        ("records[].kofc_councils[].context", "raw"),
        ("records[].svdp.context", "raw"),
        ("records[].groups.*.context", "raw"),
    ],
    "parish_alt_sources": [
        ("records.*[].staff", "people", "on an alternate source page"),
        ("records.*[].ministries", "text"),
        ("records.*[].volunteer_needs", "text"),
        ("records.*[].events[].title", "text"),
        ("records.*[].notes", "text"),
        ("records.*[].mass_schedule[].line", "schedule"), ("records.*[].mass_schedule[].note", "text"),
        ("records.*[].confession[].note", "text"), ("records.*[].adoration[].note", "text"),
        ("records.*[].transition.text", "text"), ("records.*[].transition.summary", "text"),
    ],
}


RULES["parish_sites_shard"] = RULES["parish_sites"]
RULES["bulletin_sources"] = [("records[].candidates[].text", "text")]
RULES["charity_profiles"] = [
    ("records[].events[].title", "text"), ("records[].current_needs[].need", "text"), ("records[].notes[].text", "text"),
    ("records[].programs[].summary", "text"), ("records[].volunteer.*", "text"), ("records[].parish_conferences[].conference", "text"),
]
RULES["offerings"] = [
    ("records[].formation_offerings.evidence.*[].text", "text"), ("records[].notes", "text"),
    ("records[].devotions[].name", "text"), ("records[].devotions[].evidence", "text"), ("records[].masses.monthly[].note", "text"),
]
RULES["cceb_990"] = [("years[].officers_and_directors", "board")]
# committed file (glob, relative to the repository root) -> rule set
FILE_RULES = [
    ("data/real/enrichment/bulletins/*.json", "bulletins"),
    ("data/real/enrichment/parish_profiles.json", "parish_profiles"),
    ("data/real/enrichment/parish_alt_sources.json", "parish_alt_sources"),
    ("data/real/enrichment/bulletin_sources.json", "bulletin_sources"),
    ("data/real/enrichment/charity_profiles.json", "charity_profiles"),
    ("data/real/ecosystem/parish_signals.json", "parish_signals"),
    ("data/real/parish_sites.json", "parish_sites"),
    ("data/real/parish_sites/*.json", "parish_sites_shard"),
    ("data/real/formation/offerings.json", "offerings"),
    ("data/real/cceb_990.json", "cceb_990"),
]


def rules_for(path) -> str | None:
    """The rule set for a data file (absolute or repository-relative path), or None."""
    import fnmatch
    p = Path(path)
    try:
        rel = p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = p.as_posix()
    return next((name for pat, name in FILE_RULES if fnmatch.fnmatch(rel, pat)), None)


def screen_file(path, obj) -> collections.Counter:
    """Apply the rules that belong to this data file (see FILE_RULES); a no-op for other files. Called by
    common.save_json, so every ingest script that writes one of these files screens it on the way out."""
    name = rules_for(path)
    return screen(obj, RULES[name]) if name else collections.Counter()


def screen_bulletin_record(rec: dict) -> collections.Counter:
    """parse_bulletins.py: one parsed bulletin."""
    return screen(rec, BULLETIN_RECORD_RULES)


__all__ = ["WITHHELD", "INTENTION_WITHHELD", "COMMON_BASE", "CLERGY_WORD", "PATRON_START", "SENSITIVE", "private_name_risk",
           "person_key", "redact", "clip", "name_spans", "scrub_schedule_line", "is_intention_line", "staff_row_ok",
           "clean_staff_name", "strip_contacts", "is_public_name", "is_public_figure", "vocabulary", "domain_vocabulary",
           "public_names", "public_figures", "screen", "RULES", "BULLETIN_RECORD_RULES", "screen_bulletin_record",
           "FILE_RULES", "rules_for", "screen_file"]
