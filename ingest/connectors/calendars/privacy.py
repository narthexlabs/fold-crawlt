"""Privacy screen for public parish calendar events.

Charter rules applied (docs/research/03-ethics-and-data-charter.md): R19/R21 (no person-level records);
class 4 and 5 data (private individuals, sick lists, named intentions, internal forum) are never stored.

Parish calendars mix public offerings (Masses, classes, socials) with bookings that name private people:
funerals, weddings, quinceañeras, individual baptisms, Mass intentions, rentals, appointments, and room
bookings titled with a couple's or a family's name. This module decides, from the title (and any category
the source supplies), whether an event is a *public parish event*, and cleans what is kept. Descriptions are
read only to detect private items; they are never stored.

    keep, reason, title, location = screen_event(title, location, source_categories, description, class_)

`reason` is a short code ('funeral_memorial', 'names_person', ...) used only for counts. Titles of excluded
events are never written anywhere. Public roles (Fr., Bishop, Deacon, Sr., saints) may be named; lay people
may not, so names riding along on public events ("Small Group Kate") are cut and bare names are dropped.

The rules were calibrated on the 2026-10-26 Oakland feeds and fail closed: when a title might name a
private person, the event is dropped. Run `python3 privacy.py` for the self-test.

Relation to ingest/public/privacy.py (the shared screen for excerpts, staff lists and the public-people allow-list):
this module stays standalone on purpose. It answers a different question (keep or drop a whole calendar event, by
event type: funerals, weddings, bookings, intentions), its vocabulary is calibrated on feed titles, and it must run
in the connector without the repository's data files that the shared screen's allow-list reads. Both apply the same
rule (CLAUDE.md: no private individuals; clergy and public staff may be named). The media feed runs event titles
through both (ingest/public/media/feed_build.py: this screen, then media_privacy.py on the shared detector).
"""
from __future__ import annotations

import html
import re

# --------------------------------------------------------------------------------------------- feeds
# A whole calendar is skipped when its own name or description says it is one of these.
FEED_EXCLUDE = [
    ("mass_intentions", r"mass\s+intentions?|intenciones|intentions?\s+(?:de\s+)?misa|thánh\s+lễ\s+cầu"),
    ("funerals", r"funerals?|memorials?\s+(?:mass|service)s?|obituar|difuntos|deceased"),
    ("weddings", r"weddings?|bodas|quincea"),
    ("sick_or_prayer_list", r"prayer\s+list|sick\s+list|homebound\s+list|lista\s+de\s+enfermos"),
    ("personal", r"birthdays?|cumplea|private|personal|\bpto\b|time[\s-]*off|out\s+of\s+office|vacation|availability"),
    ("appointments", r"appointments?|spiritual\s+direction"),
]


def feed_excluded(name: str | None, desc: str | None = None) -> str | None:
    text = f"{name or ''} {desc or ''}".lower()
    for code, pat in FEED_EXCLUDE:
        if re.search(pat, text):
            return code
    return None


# --------------------------------------------------------------------------------------------- vocabulary
# EVENT words: capitalised words in parish titles that are not personal names. SAINTS: given names that are
# also saints' names; they neither prove nor rule out a private person, so "John Smith" is still caught.
EVENT_WORDS = set("""
a about academy acceptance accion action active ad add adoracion adoración adoration adult adultos adults
advent after afternoon al alameda alehouse all alpha altar altarcitos alumni am ambassadors an anchor and
angel angels animacion anniversary annual anointing apostle apostles apostolate appreciation april arcangel
art arts ash assembling assembly assumption at august auction available avenue awareness baby back bags bake
bakers band banquet baptism baptismal baptisms barbecue basilica basketball bazaar bbq be beads bears
beatitudes beef before begins beginners bell bells benediction bereavement berkeley bible bienvenida
bilingual bingo bishop bishops block blessed blessing blessings blood board book books borromeo bosco
boutique boy boys bread break breakfast brothers brunch building business by cafe calendar call camp campus
can candlelight cantor cantors capuchin cards care caring carismatica carol carols carnival catechesis
catechetical catechist catechists catequistas cathedral catholic catholics ccd celebrate celebration
cenacle center centering centre certification ch change chapel chaplet charismatic charities charity cheer
chess child children childrens children's chinese choir choirs christ christi christian christmas church
circle circles class classes classrooms cleanup clergy clinic close closed club clw cn coat coffee college
columbiettes columbus come commemoration commission committee communion community concepcion conception
concert concierto conf conference confession confessions confirmation confimation conflict connection
consecration consultations contemplative contemporary conversation convert cor core coro corpus council
couples course cpr cr crab craft crafts crecimiento creek cristo crusade cruz cultural cursillo cyo daily
dance daughters day days de deacon deacons deanery dec december decor degree dei del demo departed dia
dining diocesan diocese dio directory disciples discipleship discussion dismissal distribution divina
divine divorce docent domestic dominican domincan don donation donuts door dress drive drop during dutch
early east easter echo edge education el election emaus emglish emmaus en encounter end eng english
enrichment entertainment env environement environment epiphany es escuela eschatology espanol español
eucaristico eucarística eucharist eucharistic evangelizacion evangelization eve evening event events
excercise exemplification exhibit expectant exposition facilitator fair faith faithful fall families
family farewell fast father fe feast feasts feed fellowship fest festival fiesta fil filipino film final
finance finane first fish food football for formacion formation forum fr free friday fridays friendly
friends friendship from fund fundraiser gabi gala gallo game games garden gathering general genius gentle
gift girl giveaway giving glory god god's golden golf good gospel gpo gr grace grade graders grades
graduation green grief griteria group groups grp grupo guadalupana gudalupana guadulupe guest guild gym
hall halloween harvest have hayward healing health heart hearts help high hike his hispanic holiday holy
home homebound homeless hombres honor hope hospitality hour hours house how hr ice icf imago immaculada
immaculate immigration in info information initiation inmigration inquiry installation instruction
instruments intercesion interior international intersecion into introduction invitation irish is italy
its job joy jr jubilee july june junior juvenil keeper kick kids kindergarten king kingdom kitchen knight
knights knitting ko kof korean la ladies lady lane language las last latin latino laudes lay leaders
leadership learn learning lectio lector lectores legal legion lent lenten les lessons library life light
litany liturgical liturgy live livermore lo location lom los lot love luncheon lunch made madonna
magnificat main maintenance man mananitas mandarin mariachi marian marraige marriage mass masses
matrimonios matrimony meal meals meditation meet meeting meetings memorial memory men mens men's mercy
messiah midnight middle minimum ministries ministry miracle misa mission missions modulos monday month
monthly morning mother mothers mov movie mtg mtgs mujeres museum music musical musicians my name nativity
nature navidad nd neighborhood net new newman night nino no nocturna non not novena november now
obligation ocia ocic october of off office officers on one online open opening or oracion order orders
ordinary ordination organ orientem our outreach overflow overnight packing padre pageant pancake pantry
parade parent parents parish parishioners parking party pastoral pastor pastorela pastries patio pc peace
penance penitential pentecost pending people perishables perpetual person photos piano picnic pictures
pilgrimage planning plants play plenary pm polling porch port portuguese posada posadas postponed
practice praise pray prayer prayers preaching pre prep preparation prepare presentation priests primer
procession process program project prolife pro pumpkin purpose querubines quiet rally reading readings
reception reconcilation reconciliation reconciliations recollection recovery rectory red reflection
reflections registration reh rehearsal rehearsals relaxation religious rememberance remembrance renewal
renovacion reservaciones respect retiro retreat retreats reunion revival rite rites roast room rosarios
rosary rummage sacrament sacramental sacraments sacramento sacred safe saint saints sale salones salvation
san santa santo santísimo saturday scene school schola schools science scout scouts scripture season
second seder seminar seminarians seminary senior seniors sensory serenata serra server servers servidores
service services session sessions set setup share sharing shepherd shop sick sign silent simbang sing
singers single sinulog sip sisters sjc sjws slice small social societies society sodality solemn
solemnity song souls souls’ soup sp spa span spanish spark speaker special spirit spiritual spirituality
sponsors sports spred spring sr st staff stanford stations stewardship storage street study sunday supper
support svdp table tag tagalo taize talk tea teach teacher teachers team teams teen teens testament th
thanksgiving that the theology things third thursday thánh ti time to together tongan tour tournament
training transfiguration treat tree trinity triduum trivia troop trunk tues tuesday tutoring unavailable
unborn united up upper ups us use varsity vbs vespers veteran's veterans vi viacrucis vianney viernes
vietnamese vigil vine virgin virtual virtus visit visitaciones vocation vocations voces volunteer
volunteers walk walking walnut watch way we wed wednesday week weekday weekend weekly welcome welcoming west
wine winter with women womens women's dinner dinners supper potluck tiny treasures americas corn maze pumkin patch word workshop world worship year years yli you young youth yr zoom
albany antioch brentwood byron castro valley concord crockett danville dublin cerrito sobrante emeryville
fremont hercules lafayette moraga newark oakland oakley orinda piedmont pinole pittsburg pleasant hill
pleasanton richmond rodeo ramon leandro lorenzo pablo union city bay point clayton discovery kensington alamo
diablo mt mount sunol
""".split())

SAINTS = set("""
mary joseph jesus john james paul peter mark luke matthew thomas andrew stephen philip jude judas tadeo
thaddeus anne ann francis anthony antonio michael miguel therese theresa teresa catherine joan patrick agnes
monica augustine agustin ignatius dominic benedict clare martin lawrence vincent raphael gabriel isidore
leo jerome callistus clement joachim leander felicitas edward barnabas bede perpetua columba jarlath bernard
cornelius bonaventure elizabeth rose raymond emmanuel cecilia lucy juan jose josé pedro pablo santiago lucas
mateo marcos francisco rita kateri diego charles maria pio pascual guadalupe lourdes santos rebecca jeanne
""".split())

PARTICLES = {"de", "del", "la", "las", "los", "y", "van", "von", "da", "dos", "di", "le", "du"}

PUBLIC_ROLE = re.compile(
    r"\b(?:fr|father|bishop|archbishop|cardinal|pope|deacon|dcn|msgr|monsignor|sr|sister|br|brother|rev|"
    r"reverend|pastor|padre|madre|mother|dr|prof|professor|sts?|saint|saints|san|santa|santo|blessed|bl|"
    r"our\s+lady\s+of|guest)\.?\s*$", re.I)

NAME = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ'’]+(?:-[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?"
_NAME_RX = re.compile(NAME + r"\Z")
_CONNECTORS = {"w/", "with", "by", "leader", "leaders", "group", "grp", "speaker", "presenter", "host",
               "facilitator", "led", "con", "-", "–", "—"}
_OCCASION = re.compile(r"\b(?:rehearsal|reh|wedd\w*|boda|anniv\w*|anny|aniversario|reception|ceremony|vows|"
                       r"blessing|baptism|bapt|quince|xv|celebration|party|shower|birthday|funeral|memorial|"
                       r"reunion|graduation)\b", re.I)


def _cls(word: str) -> str:
    w = word.lower().replace("’", "'").strip(".,':;()")
    if w in EVENT_WORDS:
        return "event"
    if w in SAINTS:
        return "saint"
    if w in PARTICLES:
        return "particle"
    return "other"


def _preceded_by_role(text: str, start: int) -> bool:
    return bool(PUBLIC_ROLE.search(text[:start].rstrip()[-30:]))


def is_bare_name(text: str) -> bool:
    """True when `text` is nothing but a personal name ("Fernando Garcia", "Marina Martinez Garcia")."""
    toks = [t.strip(",.;:") for t in text.split() if t.strip(",.;:")]
    if not 1 <= len(toks) <= 4:
        return False
    if any(not (_NAME_RX.match(t) or t.lower() in PARTICLES) for t in toks):
        return False
    classes = [_cls(t) for t in toks]
    return "event" not in classes and "other" in classes


def _initial_surname(text: str) -> bool:                         # "J. Builta"
    return bool(re.fullmatch(r"[A-Z]\.\s?" + NAME, text.strip()))


_FAMILY = re.compile(r"\b(" + NAME + r")\s+(?:[Ff]amily|[Ff]amilia)\b|\b[Ff]amilia\s+(" + NAME + r")\b|"
                     r"\b[Tt]he\s+(" + NAME + r")s\b")
_SLASH_PAIR = re.compile(r"\b(" + NAME + r")\s*/\s*(" + NAME + r")\b")
_AND_PAIR = re.compile(r"\b(" + NAME + r")\s*(?:and|&|\by\b|\+)\s*(" + NAME + r")\b")
_POSSESSIVE = re.compile(r"\b(" + NAME + r")['’]s\s+(?:birthday|anniversary|baptism|wedding|funeral|party|"
                         r"celebration|retirement|graduation|shower|reception|memorial)\b", re.I)


def names_private_person(title: str) -> bool:
    """Heuristic: does the title appear to name a private individual, couple or family?"""
    mostly_upper = sum(c.isupper() for c in title) > 0.6 * max(1, sum(c.isalpha() for c in title))
    variants = [title] + ([" ".join(w.title() if len(re.sub(r"\W", "", w)) >= 4 else w for w in title.split())]
                          if mostly_upper else [])
    for t in variants:
        if is_bare_name(t):
            return True
        for m in _POSSESSIVE.finditer(t):
            if not _preceded_by_role(t, m.start()) and _cls(m.group(1)) != "event":
                return True
        words = len([w for w in t.split() if w not in "/&+-–—:"])
        for m in _FAMILY.finditer(t):
            w = next(g for g in m.groups() if g)
            if _cls(w) != "other" or _preceded_by_role(t, m.start()):
                continue
            if m.group(3) and not (words <= 4 or _OCCASION.search(t)):      # "the Smiths", not "the Americas"
                continue
            return True
        for rx in (_SLASH_PAIR, _AND_PAIR):
            for m in rx.finditer(t):
                c = (_cls(m.group(1)), _cls(m.group(2)))
                if "event" in c or "other" not in c or _preceded_by_role(t, m.start()):
                    continue
                limit = 2 if rx is _SLASH_PAIR else 6                       # "Smith/Jones", "Mia and Mike"
                if words <= limit or _OCCASION.search(t):
                    return True
    return False


def redact_names(title: str) -> str:
    """Cut lay people's names that ride along on otherwise public events.

    'Small Faith Group (J. Builta)' -> 'Small Faith Group'; 'Bible Study - Small Group Kate' -> 'Bible Study -
    Small Group'; 'WINGS - Speaker, Doug Pavicich' -> 'WINGS - Speaker'; "Jeff Burn's - Topic" -> 'Topic'.
    Public roles (Fr., Bishop, Sr., Dcn., saints) are kept.
    """
    def paren(m: re.Match) -> str:
        inner = m.group(1).strip()
        inner2 = re.sub(r"^(?:speaker|presenter|leader|led\s+by|host|facilitator|with|by)\s*[:,]?\s*", "", inner, flags=re.I)
        if PUBLIC_ROLE.search(inner2.split()[0] + " " if inner2.split() else "") or re.match(PUBLIC_ROLE.pattern.replace("\\s*$", ""), inner2, re.I):
            return m.group(0)
        if _initial_surname(inner2) or is_bare_name(inner2):
            return ""
        return m.group(0)

    t = re.sub(r"\s*\(([^()]{2,60})\)", paren, title)
    # leading possessive name: "Jeff Burn's - Flannery O'Connor"
    m = re.match(r"^((?:" + NAME + r")(?:\s+" + NAME + r")?)['’]s?\s*[-–—:]\s*", t)
    if m and is_bare_name(m.group(1).rstrip("'’")) and not _preceded_by_role(t, m.start()):
        t = t[m.end():]
    # trailing name after a separator or a connector word ("- Talino", "Group Kate", "Speaker, Doug Pavicich"), also
    # when a place in brackets follows it ("Workshop: <given name> <surname> (Pastoral Center)" -> "Workshop (Pastoral Center)")
    tail = re.search(r"\s*\([^()]{2,60}\)\s*$", t)
    place, t = (tail.group(0), t[:tail.start()]) if tail else ("", t)
    toks = t.split()
    for k in (3, 2, 1):
        if len(toks) <= k:
            continue
        name, head, prev = " ".join(toks[-k:]), " ".join(toks[:-k]), toks[-k - 1]
        if not (is_bare_name(name) or _initial_surname(name)) or _preceded_by_role(head, len(head)):
            continue
        if prev[-1] in "-–—:," or prev.lower() in _CONNECTORS:
            t = head
            break
    return (t.strip(" -–—:,;/") + place).strip()


# --------------------------------------------------------------------------------------------- rules
_GENERIC_PREP = (r"class|classes|prep|preparation|platica|plática|charla|seminar|workshop|catechesis|"
                 r"orientation|info(?:rmation)?\b|registration|sign[\s-]?up|ministry|ministers|team|training|"
                 r"retreat|encounter|enrichment|anniversary\s+blessing|world\s+marriage|engaged|pre-?cana|"
                 r"bereavement|grief|support\s+group|all\s+souls|no\s+baptisms|schedule\s+change|"
                 r"expectant\s+parents")

EVENT_RULES: list[tuple[str, str, str | None]] = [
    ("funeral_memorial",
     r"\bfunerals?\b|\bmemorial\s+(?:mass|service|rosary)\b|\bcelebration\s+of\s+(?:life|the\s+life)\b|"
     r"\bin\s+(?:loving\s+)?memory\b|\bvigil\s+(?:service|for)\b|\brosary\s+for\b|\brequiem\b|\binterment\b|"
     r"\binurnment\b|\bburial\b|\bcommittal\b|\bvelorio\b|\bcuerpo\s+presente\b|\bmisa\s+de\s+difunto|"
     r"\bR\.?\s?I\.?\s?P\.?\b|†|✝|\bfor\s+the\s+repose\b|\brepose\s+of\b|\bdeceased\b|"
     r"\bthánh\s+lễ\s+an\s+táng\b|\bcầu\s+hồn\b|\bnovenario\b",
     r"ministry|ministers|bereavement|grief|all\s+souls|training|workshop|planning\s+(?:seminar|session)|"
     r"day\s+of\s+the\s+dead|d[ií]a\s+de\s+(?:los\s+)?muertos|book\s+of\s+the\s+(?:dead|deceased)|"
     r"deceased\s+(?:members|clergy|priests|parishioners)\b|for\s+all\s+(?:the\s+)?(?:faithful\s+)?departed|"
     r"all\s+souls"),
    ("wedding_quince",
     r"\bweddings?\b|\bwedd\b\.?|\bboda\b|\bbodas\b|\bmatrimonio\b|\bquincea[nñ]eras?\b|\bquincenearas?\b|"
     r"\bquince\b|\bxv\b|\bxv\s+a[nñ]os\b|\bvow\s+renewal\b|\bconvalidation\b|\bnuptial\b|\bhôn\s+phối\b|"
     r"^\s*rehearsal\s*$|\bwedding\s+rehearsal\b|\breh\.?\s*$",
     _GENERIC_PREP),
    ("baptism_individual",
     r"\bbaptisms?\b|\bbaptism\b|\bbapt\b\.?|\bbautizos?\b|\bbautismos?\b|\bbautismo\b|\brửa\s+tội\b",
     _GENERIC_PREP),
    ("mass_intention",
     r"\bintentions?\b|\bintenci[oó]n(?:es)?\b|\bspl\.?\s+int\b|\(\s*[dDlL]\.?\s*\)|^\s*[+]\s*\w|"
     r"\bmisa\s+(?:por|para)\s+(?:el\s+|la\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|"
     r"\b(?:anniversary|birthday|thanksgiving)\s+(?:mass|blessing)\s+for\b|\bacci[oó]n\s+de\s+gracias\s+por\b",
     r"prayer\s+intentions\s+of\s+the\s+pope|pope'?s\s+(?:monthly\s+)?intention"),
    ("private_booking",
     r"\bprivate\b|\brental\b|\brented\b|\brenter\b|\breserved\b|\breservation\b|\bbooked\b|\bbooking\b|"
     r"\bhold\b|\bon\s+hold\b|\btentative\b|\bblocked\b|\bblock\s+out\b|\bclosed\s+event\b|"
     r"\bappointments?\b|\bappt\.?\b|\binterviews?\b|\bone[-\s]on[-\s]one\b|\b1:1\b|\bcounsel(?:l)?ing\b|"
     r"\bspiritual\s+direction\b|\bdirecci[oó]n\s+espiritual\b|\bdoctor\b|\bdentist\b|\bmedical\b|\bsurgery\b|"
     r"\bhospital\s+visit\b|\bday\s+off\b|\bvacation\b|\bout\s+of\s+(?:the\s+)?office\b|\bOOO\b|\bpto\b|"
     r"\bbirthday\b|\bcumplea[nñ]os\b|\bhome\s+visit\b|\bhouse\s+blessing\b|\bhome\s+blessing\b|"
     r"\bhome\s+communion\b|\bsick\s+call\b|\bpersonal\b|\bbusy\b|\bcontract\s+signing\b",
     r"anointing\s+of\s+the\s+sick\s+mass|no\s+appointment|birthday\s+blessings?\b(?!\s+for)"),
]

# Not events: lectionary/rota codes ("OT_32_A", "ENGLISH = Advent_02_B"), titles without letters, and
# facility holds ("HALL - NOT AVAILABLE", "CHURCH IN USE ALL DAY").
NON_EVENT = re.compile(
    r"^(?:[A-Z]+\s*=\s*)?(?:OT|ADVENT|LENT|EASTER|CHRISTMAS|ORDINARY)_\d{1,2}(?:_[ABC])?(?:_\w+)?$|"
    r"^(?:advent|lent|easter|christmas)\s+week\s+\d+$|^week\s+\d+$|^[^A-Za-zÀ-ÿ]*$",
    re.I)
FACILITY_HOLD = re.compile(r"\b(?:not\s+available|unavailable|in\s+use(?:\s+all\s+day)?|no\s+reservaciones|"
                           r"do\s+not\s+book|reserved\s+for\s+parish\s+use)\b", re.I)
_CONFLICT = re.compile(r"\*+\s*conflict\s*\*+\s*|\bconflict\s*[-–:]\s*", re.I)
_ROOM_PREFIX = re.compile(r"^@([A-Za-z0-9]{2,12})\s*=\s*(.+)$|^([A-Z][A-Z0-9]{0,5})\s*(?::|;)\s*(.+)$")


# "Mass for <Name>": the English form of a Mass intention ("Mass for <given name> and <given name> <surname>", "Mass for
# Sr. <name>"). EVENT_RULES has the Spanish form ("Misa por <Name>"); the English one was missing (2026-10-31: 11 named
# intentions passed into a California parish's events, data/real only). Fails closed: a capitalised word after "Mass for"
# that is not an event word or a cause below is taken as a name. Kept: "Mass for All Souls", "Mass for Peace",
# "Mass for the Unborn", "Mass for Vocations", "Mass for St. Jude", "Mass for Poor Souls in Purgatory".
_MASS_FOR = re.compile(r"\b(?:mass|misa|th[aá]nh\s+l[eễ])\s+(?:for|por|para|c[aầ]u\s+cho)\s+(?:the\s+)?(\S+)", re.I)
_FOR_SAINT = re.compile(r"^(?:st|sts|ste|saint|saints|san|santa|santo|our|nuestra|blessed|bl|holy|sacred|immaculate)\.?$", re.I)
_FOR_ROLE = re.compile(r"^(?:sr|br|fr|sister|brother|father|mr|mrs|ms|dr|deacon|dcn|msgr|monsignor|rev)\.?$", re.I)
_FOR_CAUSE = set("""
all poor souls peace life vocations vocation healing unity faithful departed unborn sick homebound parishioners benefactors
married couples families families' mothers fathers father's mother's grandparents children students teachers migrants
immigrants refugees nation country world priests religious seminarians deacons clergy veterans military troops first
responders victims survivors persecuted christians christian justice hope mercy respect thanksgiving harvest rain
deceased dead living members everyone those others guadalupe lourdes fatima la el los las nuestra our holy
""".split())


def mass_for_person(title: str) -> bool:
    """True when the title is a Mass offered for a named person ("Mass for <Name>", "Misa por <Name>")."""
    for m in _MASS_FOR.finditer(title or ""):
        w = m.group(1).strip(".,:;()\"'’")
        if not w or _FOR_SAINT.match(m.group(1)):
            continue
        if _FOR_ROLE.match(m.group(1)):
            return True                                   # "Mass for Sr. <name>", "Mass for Fr. <name>"
        if not w[:1].isupper():
            continue                                      # "Mass for families"
        lw = w.lower()
        if lw in EVENT_WORDS or lw in _FOR_CAUSE or re.sub(r"['’]s$", "", lw) in _FOR_CAUSE:
            continue
        return True
    return False


def split_room_prefix(title: str) -> tuple[str | None, str]:
    """'CH: Mass in English' -> ('CH', 'Mass in English'); '@JP2 = Book Club' -> ('JP2', 'Book Club')."""
    m = _ROOM_PREFIX.match(title)
    if not m:
        return None, title
    if m.group(1):                                      # '@ROOM = ...' is always a room code
        return m.group(1), m.group(2).strip()
    code = m.group(3)
    explicit = False
    short_code = len(code) <= 3 or (len(code) <= 5 and any(c.isdigit() for c in code))
    if not explicit and (not short_code or code.lower() in ("cyo", "re", "ff", "yli", "icf", "cfm", "koc", "pre")):
        return None, title
    return code, m.group(4).strip()


# --------------------------------------------------------------------------------------------- cleaning
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?:\s*(?:x|ext\.?)\s*\d+)?")
_MEETING_ID = re.compile(r"\b(?:meeting\s+id|passcode|password|pwd)\s*[:#]?\s*[\w-]{4,}|\(\s*\d{4,}\s*\)", re.I)
_HOME = re.compile(r"\bhome\s+of\b|\bhouse\s+of\b|\bresidence\b|\bcasa\s+de\b|\bprivate\s+home\b|"
                   r"\b(?:'s|’s)\s+(?:home|house)\b|\bat\s+home\b", re.I)


def clean_text(s: str | None, limit: int = 200) -> str | None:
    if not s:
        return None
    s = html.unescape(str(s)).replace(" ", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = _URL.sub(" ", s)
    s = _EMAIL.sub(" ", s)
    s = _PHONE.sub(" ", s)
    s = _MEETING_ID.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;-–")
    return (s[:limit].rstrip() + "…") if len(s) > limit else (s or None)


def clean_location(loc: str | None) -> str | None:
    loc = clean_text(loc, 160)
    if not loc:
        return None
    if _HOME.search(loc) or names_private_person(loc):
        return None                      # a private home or a person's name: never keep it
    return loc


# --------------------------------------------------------------------------------------------- screen
def screen_event(title: str | None, location: str | None = None, source_categories: list[str] | None = None,
                 description: str | None = None, class_: str | None = None) -> tuple[bool, str | None, str | None, str | None]:
    """Return (keep, reason, clean_title, clean_location)."""
    if class_ and str(class_).upper() in ("PRIVATE", "CONFIDENTIAL"):
        return False, "marked_private", None, None
    t = clean_text(title)
    if not t:
        return False, "no_title", None, None
    t = _CONFLICT.sub("", t).strip(" -–:")
    room, t = split_room_prefix(t)
    cats = " | ".join(source_categories or [])
    probe = f"{t} || {cats}"
    for reason, pat, rescue in EVENT_RULES:
        if re.search(pat, probe, flags=re.I):
            if rescue and re.search(rescue, t, flags=re.I):
                continue
            return False, reason, None, None
    if mass_for_person(t):
        return False, "mass_intention", None, None
    if description and re.search(r"\b(?:private\s+(?:event|mass|ceremony)|not\s+open\s+to\s+the\s+public|"
                                 r"by\s+invitation\s+only|invitation\s+only|family\s+only)\b", description, re.I):
        return False, "private_booking", None, None
    if NON_EVENT.match(t):
        return False, "non_event_marker", None, None
    if FACILITY_HOLD.search(t):
        return False, "facility_hold", None, None
    if names_private_person(t):
        return False, "names_person", None, None
    t2 = redact_names(t)
    if not t2 or NON_EVENT.match(t2) or is_bare_name(t2):
        return False, "names_person", None, None
    return True, None, t2, clean_location(location) or room


# --------------------------------------------------------------------------------------------- self-test
def _selftest() -> None:
    keep = [
        ("Adoration, Mass, and Confession", "Adoration, Mass, and Confession"),
        ("Baptism Class (Spanish)", "Baptism Class (Spanish)"),
        ("Small Faith Group (J. Builta)", "Small Faith Group"),
        ("Port. Mass - Fr. Wilter Malveira", "Port. Mass - Fr. Wilter Malveira"),
        ("Rosary & Divine Mercy Chaplet ZOOM (9763)", "Rosary & Divine Mercy Chaplet ZOOM"),
        ("@CHURCH = rehearsal Choir1100", "rehearsal Choir1100"),
        ("CH: Mass in English", "Mass in English"),
        ("MISA: CA", "MISA: CA"),
        ("All Souls' Day Solemn Mass", "All Souls' Day Solemn Mass"),
        ("Memorial of St. Martin of Tours", "Memorial of St. Martin of Tours"),
        ("Holy Family Novena", "Holy Family Novena"),
        ("Marriage Preparation Facilitator Training", "Marriage Preparation Facilitator Training"),
        ("Bereavement Ministry Meeting", "Bereavement Ministry Meeting"),
        ("Sr. Rebecca Shinas, OP – Art Exhibit", "Sr. Rebecca Shinas, OP – Art Exhibit"),
        ("Church- Closed 12noon-2:30pm", "Church- Closed 12noon-2:30pm"),
        ("Padre Pio Capuchin Chess Academy", "Padre Pio Capuchin Chess Academy"),
        ("Wedding Anniversary Blessing (all couples)", "Wedding Anniversary Blessing (all couples)"),
        ("Wednesday AM Fall 26 Bible Study - Small Group Kate", "Wednesday AM Fall 26 Bible Study - Small Group"),
        ("WINGS (Women in God's Spirit) - Speaker, Doug Pavicich", "WINGS (Women in God's Spirit) - Speaker"),
        ("WINGS (Women in God's Spirit) - Speaker, Fr. Mark Wiesner", "WINGS (Women in God's Spirit) - Speaker, Fr. Mark Wiesner"),
        ("Men of Faith Dinner Meeting (Speaker: Deacon Dave)", "Men of Faith Dinner Meeting (Speaker: Deacon Dave)"),
        ("Men's Basketball - Talino", "Men's Basketball"),
        ("Don Bosco", "Don Bosco"), ("Simbang Gabi", "Simbang Gabi"), ("Coffee & Donuts", "Coffee & Donuts"),
        ("Mary and Joseph Pageant", "Mary and Joseph Pageant"), ("Sts. Peter and Paul Feast", "Sts. Peter and Paul Feast"),
        ("HALL - *CONFLICT* SIMBANG GABI RECEPTION", "HALL - SIMBANG GABI RECEPTION"),
        ("10:30 AM Confession Fr. Matthew", "10:30 AM Confession Fr. Matthew"),
        ("Blessing for Expectant Parents (All Masses)", "Blessing for Expectant Parents (All Masses)"),
        ("Pumkin Patch / Corn Maze", "Pumkin Patch / Corn Maze"), ("H: WOG", "WOG"),
        ("Mass: CA + JM", "Mass: CA + JM"), ("Advent Mission", "Advent Mission"), ("Christmas Mass", "Christmas Mass"),
        ("Catholic Daughters of the Americas Women’s Retreat", "Catholic Daughters of the Americas Women’s Retreat"),
        ("Reconciliation (no appointment needed)", "Reconciliation (no appointment needed)"),
        ("BIRTHDAY BLESSINGS FIRST SUNDAY - END OF MASS", "BIRTHDAY BLESSINGS FIRST SUNDAY - END OF MASS"),
        ("Pre-School / Tiny Treasures", "Pre-School / Tiny Treasures"),
        ("@CATHEDRAL = Dio (SE HR Ben)", "Dio (SE HR Ben)"),
        ("Mass for All Souls", "Mass for All Souls"), ("Mass for Poor Souls in Purgatory", "Mass for Poor Souls in Purgatory"),
        ("Mass for Peace", "Mass for Peace"), ("Mass for the Unborn", "Mass for the Unborn"), ("Mass for Vocations", "Mass for Vocations"),
        ("Workshop: Maria Lopez (Pastoral Center)", "Workshop (Pastoral Center)"), ("Bible Study (Spanish)", "Bible Study (Spanish)"),
        ("Mass for St. Jude", "Mass for St. Jude"), ("Mass for Tues", "Mass for Tues"), ("Mass for families", "Mass for families"),
    ]
    drop = [
        ("Funeral - John Smith", "funeral_memorial"), ("Funeral Mass", "funeral_memorial"),
        ("Memorial Mass for Mary Jones", "funeral_memorial"), ("Rosary for Ana Garcia", "funeral_memorial"),
        ("Smith/Jones Wedding", "wedding_quince"), ("XV Reh.", "wedding_quince"),
        ("Wedd. Barajas- Beltran", "wedding_quince"), ("WEdd. Reh.", "wedding_quince"),
        ("Quinceañera de Maria", "wedding_quince"), ("Baptism (English):", "baptism_individual"),
        ("Bapt. Fr. Ortiz", "baptism_individual"),
        ("Mass Intention - Available", "mass_intention"), ("+ Robert Lee", "mass_intention"),
        ("Private Mass:", "private_booking"), ("Hall Rental", "private_booking"),
        ("HOLD - Garcia", "private_booking"), ("Garcia Family Reunion", "names_person"),
        ("CH: Darlene and Kristian", "names_person"), ("CH: Rehearsal Steve and Cecilia", "names_person"),
        ("Adan & Lourdes 30th Anniversary CA", "names_person"), ("Fernando Garcia", "names_person"),
        ("Marina Martinez Garcia", "names_person"), ("John Smith", "names_person"),
        ("OT_32_A", "non_event_marker"), ("ENGLISH = Advent_02_B", "non_event_marker"), ("0", "non_event_marker"),
        ("HALL - NOT AVAILABLE 2:15 PM", "facility_hold"), ("SJC - CHURCH IN USE ALL DAY", "facility_hold"),
        ("Busy", "private_booking"), ("Spiritual Direction", "private_booking"),
        ("MARINA MARTINEZ GARCIA", "names_person"), ("CH: Concepcion 70th Birthday Mass", "private_booking"),
        ("Dinner for the Smiths", "names_person"), ("Garcia/Lopez", "names_person"),
        ("Mass for John and Rose Smith", "mass_intention"), ("Mass for Sr. Mary Jones", "mass_intention"),
        ("Mass for Ana B. Lopez", "mass_intention"), ("Mass for Mehmet Yilmaz", "mass_intention"),
        ("Mass for Carlos Reyes, Jr.", "mass_intention"), ("Mass for Fr. Smith", "mass_intention"),
    ]
    for t, want in keep:
        k, r, ct, _ = screen_event(t)
        assert k and ct == want, (t, k, r, ct)
    for t, want in drop:
        k, r, _, _ = screen_event(t)
        assert not k and r == want, (t, k, r)
    assert screen_event("Bible Study", "Home of John Smith, 12 Elm St")[3] is None
    assert screen_event("CH: Mass in English")[3] == "CH"
    assert screen_event("Mass", None, None, None, "PRIVATE")[0] is False
    assert feed_excluded("Mass Intentions at St. Margaret Mary Parish") == "mass_intentions"
    assert feed_excluded("www.StDavidOfWales.com", "Office Hours, Classes, Prayers, Socials") is None
    print(f"privacy self-test ok ({len(keep)} kept, {len(drop)} dropped)")


if __name__ == "__main__":
    _selftest()
