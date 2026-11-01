"""Build data/real/groups/groups.json: the "Find a group" layer (parish groups and ministries a Catholic could join).

One record per group per parish (or per diocese, for diocese-wide movements and language communities), in the fixed
taxonomy of docs/design/groups-taxonomy.md. Every record carries the source it came from; nothing is invented.

Inputs (all already in the repo; this script makes no network calls)
  data/real/parish_sites.json                    ministries lists from each parish website crawl
  data/real/enrichment/parish_profiles.json      ministries/groups from bulletins + website (page refs)
  data/real/enrichment/bulletins/<id>.json       parsed bulletin metadata (date, source_url)
  data/real/calendars/events.json                public parish calendar feeds (recurring events = groups)
  data/real/ecosystem/orgs.json, relationships.json   KofC councils, SVdP conferences, movements, apostolates
  data/seed/entities.json                        valid entity ids, names, cities
  data/raw/parish_sites/<id>/*.html, data/raw/bulletins/<id>/<date>.txt   OPTIONAL caches (gitignored). Used only
                                                 to read a meeting time or a one-line description on the same line
                                                 as the group name. Without them the build still runs; records just
                                                 have fewer meeting times/descriptions.

Rules
  * Fixed taxonomy; names that match no rule are dropped (counted in meta.dropped), never guessed.
  * Noise dropped: headings ("Liturgical Ministries"), staff titles, councils/committees, fundraisers, one-off events,
    Masses/confession schedules, sentence fragments, and anything from a Bishop's Appeal page.
  * Privacy: never a private person's name, phone or email. contact_role is a public role only. Any name or description
    that privacy.private_name_risk() flags is dropped (descriptions) or the item is dropped (names).
  * Duplicates within a parish merge on (parish, group key); the record keeps every source in `sources` and uses the
    best one (calendar > bulletin > website > ecosystem for meeting time; website > bulletin > calendar for the name).

Usage
  python3 -I ingest/public/groups/build_groups.py
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "public"))
from privacy import private_name_risk as _strict_risk, redact, strip_contacts  # noqa: E402


def private_name_risk(text: str) -> bool:
    """Short group names: use the standard redaction screen (the strict briefing test flags ordinary ministry
    names such as "Catholic Men's Fellowship"). Anything already withheld upstream is a risk too."""
    if not text:
        return False
    if "[name withheld]" in text or "withheld" in text:
        return True
    return redact(text)[1] > 0


def desc_risk(text: str) -> bool:
    """Descriptions are running text cut from a page: use the raw-mode screen."""
    return "withheld" in text or redact(text, "raw")[1] > 0

REAL = ROOT / "data" / "real"
RAW = ROOT / "data" / "raw"
OUT = REAL / "groups" / "groups.json"

CATEGORIES = {
    "prayer_devotion": "Prayer & devotion",
    "scripture_study": "Scripture & study",
    "formation": "Formation",
    "men": "Men",
    "women": "Women",
    "young_adults": "Young adults",
    "youth": "Youth",
    "families": "Families",
    "seniors": "Seniors",
    "service_mercy": "Service & mercy",
    "liturgical": "Liturgical ministries",
    "fraternal_orders": "Fraternal & orders",
    "language_cultural": "Language & cultural communities",
    "grief_support": "Grief & support",
    "marriage_family": "Marriage & family",
    "movements": "Movements",
    "other": "Other",
}

# --------------------------------------------------------------------------- text helpers

def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("​", "").replace("’", "'").replace("‘", "'")
    return s


def norm(s: str) -> str:
    s = fold(s).lower()
    s = re.sub(r"[“”\"]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .,:;-–—|*_")
    return s


def fix_mojibake(s: str) -> str:
    if "Ã" in s:
        try:
            return s.encode("latin-1").decode("utf-8")
        except Exception:
            return s
    return s


ACRONYMS = {"ocia": "OCIA", "rcia": "RCIA", "ocic": "OCIC", "cyo": "CYO", "svdp": "SVdP", "icf": "ICF", "yli": "YLI",
            "cda": "CDA", "spred": "SPRED", "aa": "AA", "ccd": "CCD", "cff": "CFF", "emhc": "EMHC", "smt": "SMT",
            "k": "K", "ii": "II", "rcia/rica": "RCIA/RICA", "sfa": "SFA", "olg": "OLG", "jp2": "JP2", "yag": "YAG", "fil-am": "Fil-Am", "filam": "FilAm", "uc": "UC"}
SMALL = {"of", "the", "and", "for", "to", "in", "on", "at", "a", "de", "la", "el", "los", "las", "y", "del", "with"}


def smart_title(s: str) -> str:
    letters = [c for c in s if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return s
    out = []
    for i, w in enumerate(s.lower().split()):
        core = w.strip("()[],.:;")
        if core in ACRONYMS:
            out.append(w.replace(core, ACRONYMS[core]))
        elif i and core in SMALL:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


LIGATURE_FIX = {"mee ng": "meeting", "a er": "after", "forma on": "formation", "educa on": "education",
                "adora on": "adoration", "ini a on": "initiation", "ac vi es": "activities", "celebra on": "celebration",
                "minis try": "ministry", "nd musicians": "and musicians"}


def clean_name(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    for a, b in LIGATURE_FIX.items():
        s = re.sub(rf"\b{a}\b", lambda m: b if m.group(0).islower() else b.capitalize(), s, flags=re.I)
    s = re.sub(r"^[\W_]+", "", s)                         # "~~~ Adoration", "_ prayer"
    s = re.sub(r"^\d{1,2}(:\d{2})?\s*(-\s*\d{1,2}(:\d{2})?)?\s*(am|pm|a\.m\.|p\.m\.)?\s+", "", s, flags=re.I)
    s = re.sub(r"[\s&,/–—$~*#|-]+$", "", s)                 # "Youth Ministry &", "Adoration Chapel $"
    if s.count(")") > s.count("("):                       # "RCIA)", "CYO etc.)"
        s = re.sub(r"\s*\b(etc\.?)?\)\s*$", "", s).replace(")", "")
    if s.count("(") > s.count(")"):
        s = s.replace("(", "")
    s = re.sub(r"\.$", "", s)
    return s.strip()


def clip_words(s: str, n: int = 40) -> str:
    w = s.split()
    return s if len(w) <= n else " ".join(w[:n]) + "…"


# --------------------------------------------------------------------------- noise rules

DROP_ALWAYS = [
    r"\b(director|directora|coordinator|coordinatior|coordinadora?|manager|secretary|administrator|bookkeeper|staff)\b",
    r"\b(phone|e-?mail|ext\.?|office|oficina|contact us|website)\b",
    r"\b(pastoral council|finance council|parish council|school board|committee|comite|minutes|leaders? (mtg|meeting)|"
    r"team (mtg|meeting)|mtg with|parent'?s? (mtg|meeting|workshop)|parents meeting|setup|set up)\b",
    r"\b(appeal|collection|giving|donat\w*|fundrais\w*|raffle|sale|gala|festival|fair|fiesta|breakfast|luncheon|dinner|"
    r"disco|bingo|concert|nativity|christmas|easter|thanksgiving|halloween|trunk|pumpkin|polling|election|closed|"
    r"no mass|no class|no school|holiday|half day|registration|register|packet|application|handbook|news|newsletter|"
    r"request|requests|hub|social media|follow|read more|learn more|click|visit link|schedule|calendar|bulletin|"
    r"livestream|live stream|give-?away|memo|tables?|pictures|photos|tour|organ demo|art exhibit|anniversary|birthday|"
    r"blessings?|simbang|posada|feast|jubilee|pilgrimage|retreat|workshop|congress|convention|gathering place|"
    r"training|practice session|dismissal|information table|info table|sign[- ]?up|volunteer opportunities|"
    r"respect life month|month of|world day|cpr|polling|for priests|clergy|seminarians)\b",
    r"\b(vacation bible school|vbs|bible school|sibang gabi|facebook|instagram|mission statement|guidelines|binder|"
    r"core message|recommended reading|resources|dates and topics|update|directory of|needed|hours|award|candidates|"
    r"conferences|new american bible|inviting all|meet the|cemetery|funeral services|funeral mass|spanish liturgy|"
    r"ministry schedul\w*|scheduler|project|trip|toy drive|conversations|onsite prayer|worship with us|devotions & "
    r"adoration|curious about|opportunities for|experience)\b",
    r"^(about|the|our|all|join|welcome)\b(?!.*\b(group|society|guild|legion)\b)",
    r"^\d{4}\b", r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w* \d",
    r"\b(pastoral care)$", r"^(prayers?|worship|outreach|liturgy|liturgical|music & choir|gatherings?|stewardship|"
    r"ministries|ministry|councils|reaching out|hospitality minister schedule|faith formation|faith formation news|"
    r"formation ministries|religious education & formation|liturgical ministry|music/ musica|liturgy/ liturgia|"
    r"leadership & administration|youth ministry/ministerios de jovenes|youth ministry/ ministerios de jovenes|"
    r"faith formation/ formacion de fe|study groups/ grupos de estudio|ministries & groups|prayer & study|"
    r"service & outreach|explore ministries|other ministries|auxiliary ministries|social & services organizations|"
    r"corporal works of mercy|spiritual works of mercy|opportunities for works of mercy|ministry application|"
    r"ministries and faith formation|ministries & faith formation|evangelization and outreach efforts|"
    r"children & youth ministries|faith formation programs|anointing of the sick|school|parish ministries|"
    r"02 parish ministries|guild|ministry leader hub|catechesis phone|ministry tables|english|spanish|espanol|"
    r"an adoration and stewardship parish)$",
    r"^(\w+ )?(ministries|programs|opportunities|efforts|organizations)$",
]
DROP_ALWAYS = [re.compile(p) for p in DROP_ALWAYS]

# Masses / confession are schedules (shown elsewhere), not groups, unless the line is clearly about a group.
SCHEDULE = re.compile(r"\b(mass|misa|masses|confessions?|reconciliation|reconcilation|thanh le|vigil|edge mass|"
                      r"office open|church[- ]closed|exposition .*chapel$)\b")
GROUPISH = re.compile(r"\b(rosary|rosario|chaplet|novena|legion|choir|coro|music|lector|usher|server|hospitality|"
                      r"sacristan|ministry|group|grupo|society|council|knights)\b")
FRAGMENT_END = re.compile(r"\b(on|the|for|of|and|to|at|in|with|a|our|is|are|by)$")

# --------------------------------------------------------------------------- taxonomy rules
# (regex on folded lowercase name, key, category, subcategory, default audience, default display name)
R = [
    # fraternal & orders (before generic words)
    (r"knights? of columbus|\bk ?of ?c\b|\bkofc\b|caballeros de colon|round table|council \d{3,5}", "kofc",
     "fraternal_orders", "knights_of_columbus", "men", "Knights of Columbus"),
    (r"columbiettes", "columbiettes", "fraternal_orders", "columbiettes", "women", "Columbiettes"),
    (r"catholic daughters|\bcda\b|court (st|our|mary|queen|\w+ \d+)", "cda", "fraternal_orders", "catholic_daughters",
     "women", "Catholic Daughters of the Americas"),
    (r"young ladies'? institute|\byli\b", "yli", "fraternal_orders", "young_ladies_institute", "women",
     "Young Ladies' Institute"),
    (r"italian catholic federation|\bicf\b", "icf", "fraternal_orders", "italian_catholic_federation", "all",
     "Italian Catholic Federation"),
    (r"secular franciscan|third order|\bofs\b|lay carmelite|lay dominican|secular carmelite|benedictine oblate",
     "third_order", "fraternal_orders", "third_order", "all", "Third Order / Secular Order"),
    (r"serra club", "serra", "fraternal_orders", "serra_club", "all", "Serra Club"),
    (r"order of malta", "malta", "fraternal_orders", "order_of_malta", "all", "Order of Malta"),
    # service & mercy
    (r"vincent de paul|vicente de paul|\bsvdp\b|\bsvp\b", "svdp", "service_mercy", "st_vincent_de_paul", "all",
     "St. Vincent de Paul Society"),
    (r"prison|jail|detention|incarcerat", "prison", "service_mercy", "prison_ministry", "all", "Prison Ministry"),
    (r"food pantry|pantry|food bank|food delivery|svdp-food|food ministry|meals? (ministry|program)|soup kitchen|sunday supper", "food",
     "service_mercy", "food_ministry", "all", "Food Ministry"),
    (r"homeless|shelter|street outreach|unhoused", "homeless", "service_mercy", "homeless_outreach", "all",
     "Homeless Outreach"),
    (r"(ministers?|ministry) (of|to) the sick|homebound|home-bound|pastoral care to the sick|sick (&|and) homebound|"
     r"\bhospitals?\b|visitation ministry|ministry to the sick", "sick", "service_mercy", "sick_homebound", "all",
     "Ministry to the Sick and Homebound"),
    (r"respect life|pro-?life|walking with moms|gabriel project|st\.? joseph for life", "respect_life",
     "service_mercy", "respect_life", "all", "Respect Life Ministry"),
    (r"social justice|peace (&|and) justice|justice (&|and) peace|social concerns", "justice", "service_mercy",
     "social_justice", "all", "Social Justice Ministry"),
    (r"immigra|legal (clinic|consultations?)", "immigration", "service_mercy", "immigrant_support", "all",
     "Immigrant Support"),
    (r"neighborhood outreach|outreach (ministry|program)|outreach ministries? team|works of mercy ministry",
     "outreach", "service_mercy", "outreach", "all", "Outreach Ministry"),
    (r"prayer shawl|knitting", "prayer_shawl", "service_mercy", "prayer_shawl_knitting", "all",
     "Prayer Shawl / Knitting Ministry"),
    # grief & support
    (r"bereavement|grief|duelo|consolation", "bereavement", "grief_support", "bereavement", "all",
     "Bereavement Ministry"),
    (r"funeral", "funeral", "grief_support", "funeral_ministry", "all", "Funeral Ministry"),
    (r"divorc|separated", "divorced", "grief_support", "divorced_separated", "all", "Divorced & Separated Ministry"),
    (r"\baa\b|alcoholics anonymous|al-?anon|recovery|12[- ]step|addiction", "recovery", "grief_support",
     "recovery", "all", "Recovery Meeting"),
    (r"mental health", "mental_health", "grief_support", "mental_health", "all", "Mental Health Ministry"),
    (r"stephen ministry", "stephen", "grief_support", "stephen_ministry", "all", "Stephen Ministry"),
    (r"caring circles?", "caring", "grief_support", "care_ministry", "all", "Caring Circles"),
    # movements
    (r"cursillo|ultreya", "cursillo", "movements", "cursillo", "all", "Cursillo"),
    (r"charismatic|carismatic|renovacion|alleluia prayer|holy spirit prayer group", "charismatic", "movements",
     "charismatic_renewal", "all", "Charismatic Prayer Group"),
    (r"communion and liberation|school of community", "cl", "movements", "communion_liberation", "all",
     "Communion and Liberation"),
    (r"focolare", "focolare", "movements", "focolare", "all", "Focolare"),
    (r"opus dei", "opus_dei", "movements", "opus_dei", "all", "Opus Dei"),
    (r"neocatechumen|neocatecumen", "neocat", "movements", "neocatechumenal_way", "all", "Neocatechumenal Way"),
    (r"jovenes para cristo|\bjpc\b", "jpc", "movements", "jovenes_para_cristo", "young_adult", "Jóvenes para Cristo"),
    (r"movimiento familiar cristiano|\bmfc\b", "mfc", "movements", "movimiento_familiar_cristiano", "families",
     "Movimiento Familiar Cristiano"),
    (r"escuela de la cruz", "escuela_cruz", "movements", "escuela_de_la_cruz", "all", "Escuela de la Cruz"),
    (r"marian movement of priests|\bimhm\b", "mmp_cenacle", "movements", "marian_movement", "all",
     "Marian Movement Cenacle"),
    (r"couples for christ|\bcfc\b", "cfc", "movements", "couples_for_christ", "families", "Couples for Christ"),
    # marriage & family
    (r"marriage encounter|encuentros? matrimonial", "wwme", "marriage_family", "marriage_encounter", "all",
     "Worldwide Marriage Encounter"),
    (r"engaged encounter|marriage prep|pre-?cana|marriage preparation|preparacion matrimonial", "marriage_prep",
     "marriage_family", "marriage_prep", "all", "Marriage Preparation"),
    (r"matrimonios|couples (talk|group|ministry)|married couples", "couples", "marriage_family", "couples_group",
     "all", "Married Couples Group"),
    # prayer & devotion
    (r"legion of mary|legion de maria|legio mariae", "legion", "prayer_devotion", "legion_of_mary", "all",
     "Legion of Mary"),
    (r"divine mercy|chaplet|divina misericordia|coronilla", "divine_mercy", "prayer_devotion", "divine_mercy", "all",
     "Divine Mercy Chaplet"),
    (r"rosary|rosario", "rosary", "prayer_devotion", "rosary", "all", "Rosary"),
    (r"nocturnal|nocturna", "nocturnal_adoration", "prayer_devotion", "nocturnal_adoration", "all",
     "Nocturnal Adoration"),
    (r"adoration|adoracion|holy hour|hora santa|benediction|exposition", "adoration", "prayer_devotion",
     "eucharistic_adoration", "all", "Eucharistic Adoration"),
    (r"novena", "novena", "prayer_devotion", "novena", "all", "Novena"),
    (r"apostleship of prayer|worldwide prayer network", "apostleship_prayer", "prayer_devotion", "apostleship_of_prayer", "all",
     "Apostleship of Prayer"),
    (r"liturgy of the hours|morning prayer group|evening prayer group", "hours", "prayer_devotion",
     "liturgy_of_the_hours", "all", "Liturgy of the Hours"),
    (r"contemplative|centering prayer|lectio divina|meditation group|christian meditation", "contemplative",
     "prayer_devotion", "contemplative_prayer", "all", "Contemplative Prayer Group"),
    (r"guadalupan|guadalupanos", "guadalupanas", "prayer_devotion", "guadalupanas", "all",
     "Guadalupanas Society"),
    (r"prayer (line|chain|ministry)|intercess|intersecion|7 wounds|seven wounds|drive thru prayer|"
     r"repentance prayer|prayer team", "intercessory", "prayer_devotion", "intercessory_prayer", "all",
     "Prayer Ministry"),
    (r"prayer group|prayer community|grupo de oracion|gpo\.? de oracion|grupo oracion|prayer circle",
     "prayer_group", "prayer_devotion", "prayer_group", "all", "Prayer Group"),
    # scripture & study
    (r"that man is you", "tmiy", "men", "that_man_is_you", "men", "That Man Is You"),
    (r"walking with purpose", "wwp", "scripture_study", "bible_study", "women", "Walking with Purpose"),
    (r"bible|estudio biblico|scripture|gospel reflection|book of \w+", "bible_study", "scripture_study",
     "bible_study", "all", "Bible Study"),
    (r"book club|reading group|flannery o'?connor", "book_club", "scripture_study", "book_club", "all", "Book Club"),
    (r"small faith|faith sharing|emmaus group|small christian communit", "small_groups", "scripture_study",
     "small_faith_groups", "all", "Small Faith Groups"),
    (r"study group|grupos? de estudio|theology|catechism study", "study_group", "scripture_study", "study_group",
     "all", "Study Group"),
    # formation
    (r"\bocic\b|\brcic\b|children'?s initiation|rcia (for|adapted for) children", "ocic", "formation",
     "ocia_children", "families", "OCIC (Christian Initiation for Children)"),
    (r"adult confirmation|confirmacion de adultos", "adult_confirmation", "formation", "adult_confirmation", "all",
     "Adult Confirmation"),
    (r"\bocia\b|\brcia\b|\boica\b|christian initiation|become catholic|becoming catholic|convert", "ocia",
     "formation", "ocia", "all", "OCIA (becoming Catholic)"),
    (r"confirmation|confirmacion", "confirmation", "formation", "confirmation_prep", "youth", "Confirmation Preparation"),
    (r"\balpha\b", "alpha", "formation", "alpha", "all", "Alpha"),
    (r"spred|special religious education", "spred", "formation", "disability_catechesis", "all",
     "SPRED (Special Religious Education)"),
    (r"baptism(al)? (prep|class|seminar|preparation)|pre-?baptism|platicas? (pre)?bautism", "baptism_prep",
     "formation", "baptism_prep", "families", "Baptism Preparation"),
    (r"first (holy )?communion|primera comunion", "first_communion", "formation", "first_communion_prep",
     "families", "First Communion Preparation"),
    (r"adult (faith )?formation|adult spirituality|adult education|formacion de adultos|modulos de formacion|"
     r"ongoing formation", "adult_formation", "formation", "adult_formation", "all", "Adult Faith Formation"),
    (r"children'?s faith formation|children'?s religious education|religious education|\bccd\b|\bcff\b|"
     r"faith formation (1st|k-|class|for children)|\bre class|family catechesis|catequesis|catechesis|k-5|"
     r"sjakids|kids'? faith|children'?s faith", "children_ff", "formation", "children_faith_formation", "families",
     "Children's Faith Formation"),
    (r"catechists?|catequistas", "catechists", "formation", "catechists", "all", "Catechists"),
    (r"spiritual direction", "spiritual_direction", "formation", "spiritual_direction", "all", "Spiritual Direction"),
    (r"evangeli[sz]", "evangelization", "formation", "evangelization_team", "all", "Evangelization Team"),
    # liturgical ministries
    (r"children'?s liturgy", "childrens_liturgy", "liturgical", "childrens_liturgy_of_the_word", "families",
     "Children's Liturgy of the Word"),
    (r"lector|readers?\b|lectores|proclaim", "lectors", "liturgical", "lectors", "all", "Lectors"),
    (r"usher|greeter|ujier", "ushers", "liturgical", "ushers_greeters", "all", "Ushers & Greeters"),
    (r"altar server|acolyte|monaguillo|servers\b", "servers", "liturgical", "altar_servers", "youth",
     "Altar Servers"),
    (r"eucharistic minister|eucharistic ministry|extraordinary minister|\bemhc\b|ministros? de la eucaristia",
     "emhc", "liturgical", "emhc", "all", "Extraordinary Ministers of Holy Communion"),
    (r"sacristan", "sacristans", "liturgical", "sacristans", "all", "Sacristans"),
    (r"altar society|altar guild|environment|flowers|decor\b|env\.", "altar_society", "liturgical",
     "altar_society_environment", "all", "Altar Society / Environment"),
    (r"choir|coro\b|music|musica|ensemble|schola|mariachi|cantor|musicians|bells|voces", "choir", "liturgical",
     "choir_music", "all", "Music Ministry / Choir"),
    (r"hospitality", "hospitality", "liturgical", "hospitality", "all", "Hospitality Ministry"),
    # audience-defined groups
    (r"young adult|jovenes adultos|students at uc|campus ministry|\byag\b|20s ?(&|and) ?30s|family of faith-young", "young_adults", "young_adults",
     "young_adult_group", "young_adult", "Young Adult Group"),
    (r"\bmen'?s (group|ministry|fellowship|bible|club)|catholic men\b|hombres|\bmens ministry",
     "mens_group", "men", "mens_group", "men", "Men's Group"),
    (r"women'?s (group|ministry|meditation|guild|fellowship|club)|mujeres|girlfriends with grace|women'?s "
     r"recollection|ladies'? (guild|club|aid)", "womens_group", "women", "womens_group", "women", "Women's Group"),
    (r"\bcyo\b", "cyo", "youth", "cyo_sports", "youth", "CYO Sports"),
    (r"scouts?|troop \d+|cub scout|girl scout|boy scout", "scouts", "youth", "scouting", "youth", "Scouting"),
    (r"youth|teen|high school|middle school|jr\.? high|\bedge\b|life teen|\becho\b|\blight\b \(high school\)|"
     r"spark night|grupo (de )?jovenes|grupo juvenil|gpo\.? juvenil|jovenes|juvenil|student ministry",
     "youth_ministry", "youth", "youth_ministry", "youth", "Youth Ministry"),
    (r"senior|golden friends|young at heart|60\+", "seniors", "seniors", "seniors_group", "seniors", "Seniors Group"),
    (r"family ministry|families ministry|family of faith|mothers|moms\b|mom'?s group", "family", "families",
     "family_ministry", "families", "Family Ministry"),
    # language & cultural communities
    (r"filipino|fil-?am|tagalog|pilipino", "filipino", "language_cultural", "filipino", "all", "Filipino Community"),
    (r"vietnam", "vietnamese", "language_cultural", "vietnamese", "all", "Vietnamese Community"),
    (r"hispanic|latino|hispano|comunidad hispana", "hispanic", "language_cultural", "hispanic_latino", "all",
     "Hispanic Ministry"),
    (r"chinese|mandarin|cantonese", "chinese", "language_cultural", "chinese", "all", "Chinese Community"),
    (r"korean", "korean", "language_cultural", "korean", "all", "Korean Community"),
    (r"portugu|brazil", "portuguese", "language_cultural", "portuguese_brazilian", "all",
     "Portuguese / Brazilian Community"),
    (r"tongan", "tongan", "language_cultural", "tongan", "all", "Tongan Community"),
    (r"igbo|nigeria", "igbo", "language_cultural", "igbo_nigerian", "all", "Igbo Catholic Community"),
    (r"eritre", "eritrean", "language_cultural", "eritrean", "all", "Eritrean Community"),
    (r"indian society|indian (catholic )?community|syro|malayalam|konkani", "indian", "language_cultural", "indian",
     "all", "Indian Community"),
    (r"african american|black catholic", "african_american", "language_cultural", "african_american", "all",
     "African American Catholic Community"),
    (r"samoan", "samoan", "language_cultural", "samoan", "all", "Samoan Community"),
]
R = [(re.compile(p), *rest) for p, *rest in R]
FALLBACK = re.compile(r"(\b(society|guild|club|association|organization|fellowship|apostolate|sodality|confraternity|"
                      r"ministry)$|^grupo\b|^ministerio\b|"
                      r"\bfriendship group$)")

LANG_RULES = [
    ("en+es", re.compile(r"eng/sp|english (&|and|/) spanish|spanish (&|and|/) english|bilingual|eng/esp|/espanol|"
                         r"\| san |—eng/sp|eng ?/ ?span")),
    ("vi", re.compile(r"vietnam|tieng viet")),
    ("zh", re.compile(r"chinese|mandarin|cantonese")),
    ("ko", re.compile(r"korean")),
    ("tl", re.compile(r"filipino|fil-?am|filam|tagalog|pilipino")),
    ("pt", re.compile(r"portugu|brazil")),
    ("to", re.compile(r"tongan")),
    ("ig", re.compile(r"igbo")),
    ("es", re.compile(r"espanol|spanish|span\.|\bspa\.?$|\bspan\b|\(sp\)|—sp\b|-sp\b|hispan|latino|\bgrupo\b|\bgpo\b|\bcoro\b|"
                      r"legion de maria|guadalup|jovenes|oracion|hombres|mujeres|matrimonios|catequesis|catequistas|"
                      r"carismatica|renovacion|adoracion|sociedad|amigos de cristo|lectores|escuela de la cruz|"
                      r"cursillos?\b.*(espanol|hispan)|voces|mariachi|oica|modulos|evangelizacion|"
                      r"san vicente|monaguillos|movimiento|encuentros?|platicas|primera comunion|confirmacion")),
]

AUD_RULES = [
    ("men", re.compile(r"\bmen'?s\b|\bmen\b|hombres|catholic men|that man is you")),
    ("women", re.compile(r"women|mujeres|ladies|girlfriends")),
    ("young_adult", re.compile(r"young adult|jovenes adultos|\byag\b|20s")),
    ("youth", re.compile(r"youth|teen|high school|middle school|jr\.? high|juvenil|children'?s choir|"
                         r"querubines|youth choir|lectores jovenes|\bcyo\b")),
    ("families", re.compile(r"family|families|parents|moms\b|mothers")),
    ("seniors", re.compile(r"senior|golden|young at heart")),
]


def classify(name: str):
    """Return (key, category, subcategory, audience, language, default_display, strength) or (None, reason)."""
    n = norm(fix_mojibake(name))
    if not n or len(n) < 3:
        return None, "empty"
    for rx in DROP_ALWAYS:
        if rx.search(n):
            return None, "noise"
    if SCHEDULE.search(n) and not GROUPISH.search(n):
        return None, "schedule"
    if len(n.split()) > 9:
        return None, "sentence"
    if (FRAGMENT_END.search(n) or n[:1] in "(-–—_" or name.strip()[:1].islower()
            or re.match(r"^(for|to|with|in|at|thank|thanks|support the|help|please|we|you|if|no)\b", n)
            or re.search(r"\b(leaders?|music prep|prep, church)\b", n)):
        return None, "fragment"
    lang = None
    for code, rx in LANG_RULES:
        if rx.search(n):
            lang = code
            break
    if lang == "es" and re.search(r"\s(\||-|/)\s", n):
        sides = re.split(r"\s(?:\||-|/)\s", n, maxsplit=1)
        es_rx = dict(LANG_RULES)["es"]
        if sum(bool(es_rx.search(x)) for x in sides) == 1 and re.search(
                r"\b(lectors?|ushers?|servers?|sacristans?|choirs?|confirmation|society|ministry|prayer group|"
                r"youth group)\b", n):
            lang = "en+es"
    aud = None
    if re.search(r"\bwomen\b", n) and re.search(r"\bmen\b", n):
        aud = "all"
    for a, rx in AUD_RULES:
        if aud:
            break
        if rx.search(n):
            aud = a
    for rx, key, cat, sub, d_aud, disp in R:
        if rx.search(n):
            audience = aud or d_aud
            if key in ("kofc", "columbiettes", "cda", "yli", "mens_group", "womens_group", "tmiy"):
                audience = d_aud
            k = key
            if audience not in ("all", d_aud):
                k += f"_{audience}"
            if lang and cat not in ("language_cultural",):
                k += f"_{lang}"
            if key == "choir":
                m = re.search(r"(coro|voces|choir -|ensemble)\s+(.+)$", n)
                proper = re.search(r"\b(coro|voces)\s+(?!practice)(\w+)", n)
                if proper:
                    k = "choir_" + re.sub(r"\W+", "_", n.split(proper.group(1), 1)[1]).strip("_")[:30]
            strength = "generic" if n in ("music", "music ministry", "hospitality", "choir", "lector", "usher",
                                          "ushers", "lectors", "liturgy") else "specific"
            return (k, cat, sub, audience, lang, disp, strength), None
    if FALLBACK.search(n) and len(n.split()) <= 6 and not private_name_risk(name):
        k = "other_" + re.sub(r"\W+", "_", n)[:40].strip("_")
        return (k, "other", "unclassified", aud or "all", lang, None, "fallback"), None
    return None, "unmatched"


# --------------------------------------------------------------------------- meeting time extraction

DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
DAY_ES = {"lunes": "Monday", "martes": "Tuesday", "miercoles": "Wednesday", "jueves": "Thursday", "viernes": "Friday",
          "sabado": "Saturday", "domingo": "Sunday"}
TIME_RX = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|am|pm|noon)\b", re.I)
RECUR_RX = re.compile(rf"\b(every|each|weekly|monthly|cada|todos los)\b|\b({DAYS})s\b|"
                      rf"\b(1st|2nd|3rd|4th|first|second|third|fourth|last)\s+({DAYS})\b", re.I)
MONTHS_RX = re.compile(r"\b(january|february|march|april|june|july|august|september|october|november|december|"
                       r"jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\.?\s+\d", re.I)


def meets_from_text(text: str) -> str | None:
    """A recurring day/time stated on the same line as the group name (no one-off dates)."""
    t = fold(text)
    t = re.sub(r"\b(todos los|cada)\s+(lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)\b",
               lambda m: "every " + DAY_ES[m.group(2).rstrip("s") if m.group(2).startswith(("sabado", "domingo")) else m.group(2)],
               t, flags=re.I)
    t = re.sub(r"(?<=[a-z]) (?=[a-z]\b)", "", t) if re.search(r"\bever y\b", t) else t   # pdf letter-spacing "ever y"
    if MONTHS_RX.search(t):
        return None
    if not RECUR_RX.search(t):
        return None
    m = re.search(rf"((?:every|each|1st|2nd|3rd|4th|first|second|third|fourth|last)\s+(?:other\s+)?(?:{DAYS})"
                  rf"|(?:{DAYS})s(?:\s*(?:&|and|,)\s*(?:{DAYS})s?)?|weekly|monthly)", t, re.I)
    tm = TIME_RX.search(t)
    if not m and not tm:
        return None
    parts = []
    if m:
        parts.append(m.group(1)[:1].upper() + m.group(1)[1:])
    if tm:
        parts.append(tm.group(0).replace(" ", "").lower())
    if not m:
        return None
    return ", ".join(parts)


def ordinal_week(d: date) -> int:
    return (d.day - 1) // 7 + 1


def meets_from_events(evs: list[dict]) -> tuple[str | None, str | None]:
    dates, times = [], Counter()
    for e in evs:
        s = e["start"]
        try:
            d = date.fromisoformat(s[:10])
        except ValueError:
            continue
        dates.append(d)
        if "T" in s:
            hh, mm = int(s[11:13]), int(s[14:16])
            times[f"{(hh % 12) or 12}:{mm:02d}{'am' if hh < 12 else 'pm'}"] += 1
        else:
            tm = TIME_RX.search(e["title"])
            if tm:
                times[tm.group(0).replace(" ", "").lower()] += 1
    dates = sorted(set(dates))
    if not dates:
        return None, None
    wd = Counter(d.strftime("%A") for d in dates)
    top_day, top_n = wd.most_common(1)[0]
    t = times.most_common(1)[0][0] if times else None
    n = len(dates)
    if n >= 8 and top_n / n >= 0.8:
        freq = f"Weekly on {top_day}s"
    elif n >= 8 and len(wd) >= 2:
        days = [k for k, v in wd.most_common() if v >= 2][:3]
        freq = "Weekly: " + ", ".join(days)
    else:
        ords = Counter((d.strftime("%A"), ordinal_week(d)) for d in dates)
        (dname, o), c = ords.most_common(1)[0]
        if c >= 2 and c == n:
            freq = f"{['1st', '2nd', '3rd', '4th', '5th'][o - 1]} {dname} of the month"
        elif top_n == n and n >= 3:
            freq = f"{top_day}s ({n} dates in the next 90 days)"
        else:
            freq = f"{n} dates in the next 90 days"
    nxt = dates[0].isoformat()
    return (f"{freq}{', ' + t if t else ''}", nxt)


def slot_of(evs: list[dict]) -> str:
    """Weekday + start time signature, so two different Bible studies at one parish stay two groups."""
    c = Counter()
    for e in evs:
        s = e["start"]
        c[(date.fromisoformat(s[:10]).strftime("%a"), s[11:16] if "T" in s else "")] += 1
    (d, t), _ = c.most_common(1)[0]
    return f"{d}{t.replace(':', '')}"


# --------------------------------------------------------------------------- loaders

def load(p):
    return json.loads(Path(p).read_text())


def html_lines(path: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup
        html = Path(path).read_text(errors="ignore")
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()
    text = soup.get_text("\n")
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if ln.strip()]


def starts_new_item(line: str) -> bool:
    """The next line is another listing ("Choir – At all Masses ...") rather than this group's description."""
    head = re.split(r"\s[–—:|-]\s|:\s", line, maxsplit=1)[0]
    if head != line and len(head.split()) <= 5:
        return True
    letters = [c for c in line if c.isalpha()]
    if len(line.split()) <= 6 and classify(line)[0] is not None:
        return True
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def sentence_like(c: str) -> bool:
    w = c.split()
    if len(w) < 6 or not c[:1].isupper():
        return False
    lower = sum(1 for x in w if x[:1].islower()) / len(w)
    return lower >= 0.45 and (c.rstrip()[-1:] in ".!?" or len(w) >= 10)


def context(name: str, lines: list[str], allow_next: bool = True) -> tuple[str | None, str | None]:
    """(meets, description) from the line that carries the group name, plus the next line for a description."""
    nn = norm(name)
    if len(nn) < 4:
        return None, None
    for i, ln in enumerate(lines):
        if nn not in norm(ln):
            continue
        meets = meets_from_text(ln)
        desc = None
        rest = re.split(re.escape(name.strip()), ln, maxsplit=1, flags=re.I)
        tail = rest[1] if len(rest) > 1 else ""
        tail = tail.strip(" :–—-|")
        nxt = lines[i + 1] if (allow_next and i + 1 < len(lines)) else ""
        if nxt and starts_new_item(nxt):
            nxt = ""
        cand = tail if len(tail.split()) >= 5 else nxt
        if not meets and nxt:
            meets = meets_from_text(nxt)
        cand = strip_contacts(cand or "").strip()
        if (sentence_like(cand) and not re.search(r"@|\d{3}[-.) ]\d{3}|http|www\.", cand)
                and not desc_risk(cand) and not classify_is_heading(cand)):
            desc = clip_words(cand, 40)
        return meets, desc
    return None, None


def classify_is_heading(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


# --------------------------------------------------------------------------- build

def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    ents = {e["id"]: e for e in load(ROOT / "data/seed/entities.json")["entities"]}
    profiles = {r["id"]: r for r in load(REAL / "enrichment/parish_profiles.json")["records"]}
    sites = {r["id"]: r for r in load(REAL / "parish_sites.json")["records"]}
    dropped = Counter()
    dropped_examples = defaultdict(list)
    obs_list = []             # (parish_id, observation)
    # Items lifted from a parish's Bishop's Appeal page are diocesan programs, not parish groups: drop the same
    # names wherever else they show up for that parish (the older crawl copied them without the page URL).
    appeal_names = set()
    for pid, s in sites.items():
        m = s.get("ministries") or {}
        for item, u in (m.get("item_sources") or {}).items():
            if u and re.search(r"appeal", u, re.I):
                appeal_names.add((pid, norm(item)))

    def add(pid, raw_name, kind, url, fetched, seen, extra=None, cls_name=None):
        if pid not in ents:
            dropped["unknown_entity"] += 1
            return
        if (url and re.search(r"appeal", url, re.I)) or (pid, norm(raw_name or "")) in appeal_names:
            dropped["appeal_page"] += 1
            return
        raw_name = clean_name(fix_mojibake(raw_name or "").replace("​", ""))
        name, desc_inline = raw_name, None
        m = re.match(r"^(.{3,60}?)\s*(?:–|—|:| - )\s*(.+)$", raw_name)
        if m and not re.search(r"council \d|court", m.group(2), re.I):
            right = m.group(2).strip()
            rw = right.split()
            lower_share = sum(1 for w in rw if w[:1].islower()) / max(1, len(rw))
            if len(rw) >= 4 and lower_share >= 0.4:
                name, desc_inline = m.group(1).strip(), right
            elif len(rw) <= 4 and re.search(r"\b(room|hall|church|center|centre|chapel|gym|library|parlor|"
                                             r"classroom|rectory|school)\b", right, re.I) and kind == "bulletin":
                name = m.group(1).strip()
                extra = dict(extra or {}, location=right)
            elif re.fullmatch(r"(eng|sp|esp|span|english|spanish)([ /&]+(eng|sp|esp|span|english|spanish))*\.?",
                              right, re.I):
                pass  # keep language marker in name for detection
        res, why = classify(cls_name or name)
        if res is None:
            dropped[why] += 1
            if len(dropped_examples[why]) < 25:
                dropped_examples[why].append(raw_name[:60])
            return
        if private_name_risk(name) and kind != "ecosystem":
            dropped["private_name_risk"] += 1
            return
        key, cat, sub, aud, lang, disp, strength = res
        o = dict(raw_name=name, kind=kind, url=url, fetched_at=fetched, seen=seen, cat=cat, sub=sub, aud=aud,
                 lang=lang, disp=disp, strength=strength, desc=None, meets=None, location=None, next=None)
        if desc_inline and not desc_risk(desc_inline) and not re.search(r"@|\d{3}[-.) ]\d{3}", desc_inline):
            o["desc"] = clip_words(desc_inline, 40)
        if extra:
            o.update({k: v for k, v in extra.items() if v})
        o["base"] = key
        o["key"] = key + ("@" + extra["slot"] if extra and extra.get("slot") else "")
        obs_list.append((pid, o))

    # 1. parish websites (current crawl)
    html_cache: dict[str, list[str]] = {}
    for pid, s in sites.items():
        m = s.get("ministries") or {}
        url_to_raw = {p.get("url"): p.get("raw_file") for p in s.get("pages") or []}
        for item in m.get("items") or []:
            url = (m.get("item_sources") or {}).get(item) or m.get("source_url")
            extra = {}
            raw = url_to_raw.get(url)
            if raw and Path(raw).exists():
                if raw not in html_cache:
                    html_cache[raw] = html_lines(raw)
                meets, desc = context(item, html_cache[raw])
                extra = {"meets": meets, "desc": desc}
            seen = (s.get("crawled_at") or "")[:10]
            add(pid, item, "website", url, s.get("crawled_at"), seen, extra)

    # 2. bulletins + older website crawl / summaries via parish_profiles
    bull_meta = {}
    for f in (REAL / "enrichment/bulletins").glob("*.json"):
        b = load(f)
        for x in b.get("bulletins") or []:
            bull_meta[x["source_url"]] = (b["id"], x["date"])
    text_cache: dict[tuple, list[str]] = {}
    for pid, p in profiles.items():
        for mi in p.get("ministries") or []:
            srcs = mi.get("sources") or []
            if not srcs:
                continue
            s0 = sorted(srcs, key=lambda s: s.get("bulletin_date") or s.get("fetched_at") or "", reverse=True)[0]
            method = s0.get("method")
            kind = "bulletin" if method == "parsed bulletin" else "website"
            seen = s0.get("bulletin_date") or (s0.get("fetched_at") or "")[:10]
            extra = {}
            if kind == "bulletin":
                for s in srcs:
                    bid, bdate = bull_meta.get(s["source_url"], (pid, s.get("bulletin_date")))
                    tf = RAW / "bulletins" / bid / f"{bdate}.txt"
                    if not tf.exists():
                        continue
                    ck = (str(tf), s.get("page"))
                    if ck not in text_cache:
                        pages = tf.read_text(errors="ignore").split("\f")
                        pg = s.get("page")
                        txt = pages[pg - 1] if pg and 0 < pg <= len(pages) else "\n".join(pages)
                        text_cache[ck] = [ln.strip() for ln in txt.split("\n") if ln.strip()]
                    meets, desc = context(mi["name"], text_cache[ck], allow_next=False)
                    if meets or desc:
                        extra = {"meets": meets, "desc": desc, "bulletin_page": s.get("page")}
                        break
                extra.setdefault("bulletin_page", s0.get("page"))
            elif method == "webfetch_summary":
                extra = {"summary_only": True}
            add(pid, mi["name"], kind, s0.get("source_url"), s0.get("fetched_at"), seen, extra)

    # 3. calendars: recurring events whose titles are groups
    cal = load(REAL / "calendars/events.json")
    byt = defaultdict(list)
    for e in cal["events"]:
        if not e.get("parish_id") or e.get("category") in ("liturgy", "reconciliation", "school", "notice",
                                                            "meetings_governance"):
            continue
        t = e["title"]
        t2 = re.sub(r"^(SJC|SJWS\d|HALL|MISSION|SFA|RECTORY[\w ]*?)\s*(&\s*HALL)?\s*-\s*", "", t)
        t2 = re.sub(r"\s*-?\s*\b\d{1,2}(:\d{2})?\s*(AM|PM|am|pm)\b.*$", "", t2).strip(" -")
        t2 = re.sub(r"\b(Fr|Father|Deacon|Sr)\.? [A-Z]\w+$", "", t2).strip(" -")
        t2 = re.sub(r"\s+(q\d|Mtg|Meet\.?|Meeting|Weekly Meeting|Practice|Rehearsal)$", "", t2, flags=re.I).strip()
        t2 = re.sub(r"\s*\b(class session|session|yr\.?|year|week|part|q)\s*\d+\b", "", t2, flags=re.I).strip(" -")
        byt[(e["parish_id"], t2)].append(e)
    for (pid, title), evs in byt.items():
        if len(evs) < 2:
            dropped["calendar_one_off"] += 1
            continue
        if re.search(r"\bzoom\b|online", title, re.I):
            loc = "Online (Zoom)"
        else:
            locs = Counter(e["location"] for e in evs if e.get("location"))
            loc = locs.most_common(1)[0][0] if locs else None
            if loc and (re.search(r"@|\d{3}[-.) ]\d{3}", loc) or private_name_risk(loc)):
                loc = None
        meets, nxt = meets_from_events(evs)
        title_clean = re.sub(r"\s*\bzoom\b", "", title, flags=re.I).strip()
        add(pid, smart_title(title_clean), "calendar", evs[0]["source_url"], evs[0]["fetched_at"],
            evs[0]["fetched_at"][:10], {"meets": meets, "location": loc, "next": nxt, "slot": slot_of(evs),
                                        "calendar_dates_90d": len({e['start'][:10] for e in evs})})

    # 4. ecosystem: KofC councils, SVdP conferences, movements and orders that meet at a parish
    orgs = {o["id"]: o for o in load(REAL / "ecosystem/orgs.json")["records"]}
    rels = load(REAL / "ecosystem/relationships.json")["records"]
    ONE_OFF = re.compile(r"\b(mass held|luncheon|retreat|weekends?|congress|recollection|anniversary|general assembly|"
                         r"classes take place|\(\w{3} 20\d\d\)|20\d\d\))", re.I)
    for r in rels:
        o = orgs.get(r["from_id"])
        if not o or r.get("status") != "current" or r["relation"] not in ("meets_at", "based_at"):
            continue
        if o["type"] not in ("kofc_council", "svdp_conference", "lay_movement", "fraternal_order",
                             "diocesan_ministry", "ethnic_apostolate"):
            continue
        if ONE_OFF.search(r.get("evidence") or ""):
            dropped["ecosystem_one_off_event"] += 1
            continue
        pid = r["to_id"]
        if o["type"] == "kofc_council":
            num = o.get("kofc_number") or re.search(r"\d+", o["name"]).group(0)
            nm = f"Knights of Columbus Council {num}"
            cls = "Knights of Columbus"
        elif o["type"] == "svdp_conference":
            nm, cls = "St. Vincent de Paul Conference", "St. Vincent de Paul"
        else:
            nm = re.sub(r"\s*\(.*?\)\s*$", "", o["name"])
            cls = nm
        add(pid, nm, "ecosystem", r.get("source_url") or o.get("source_url"), r.get("fetched_at") or o.get("fetched_at"),
            (r.get("fetched_at") or o.get("fetched_at") or "")[:10],
            {"basis": r.get("basis"), "org_id": o["id"], "evidence": r.get("evidence")}, cls_name=cls)

    # 4b. diocese-wide movements and language communities (no single parish home)
    DIOCESAN = ["mov-cursillo-en", "mov-cursillo-es", "mov-charismatic-en", "mov-rcc-hispana", "mov-rcc-juvenil",
                "mov-jpc", "mov-epj", "mov-mfc", "mov-wwme", "mov-wwme-es", "mov-ignatian-slc", "mov-escuela-cruz",
                "eth-igbo", "eth-korean-center", "eth-tongan", "eth-brazilian-portuguese", "dm-aapc"]
    for oid in DIOCESAN:
        o = orgs.get(oid)
        if not o:
            continue
        nm = o["name"]
        cls = nm
        if oid == "mov-epj":
            cls = "Encuentros de Promocion Juvenil youth"
        if oid == "mov-ignatian-slc":
            cls = "Ignatian contemplative prayer"
        if oid == "dm-aapc":
            cls = "African American Catholic"
        if oid == "mov-rcc-juvenil":
            cls = "Renovacion Carismatica Juvenil"
        add("dio-oakland", nm, "ecosystem", o.get("source_url"), o.get("fetched_at"), (o.get("fetched_at") or "")[:10],
            {"basis": "stated", "org_id": oid, "desc": clip_words(o.get("description") or "", 40) or None,
             "scope": "diocesan", "closing": "close" in (o.get("description") or "").lower()},
            cls_name=cls + (" espanol" if oid.endswith("-es") or oid in ("mov-rcc-hispana", "mov-epj", "mov-mfc",
                                                                         "mov-jpc", "mov-escuela-cruz",
                                                                         "mov-rcc-juvenil") else ""))

    # ------------------------------------------------------------------ merge
    KIND_NAME = {"website": 0, "bulletin": 1, "calendar": 2, "ecosystem": 3}
    KIND_TIME = {"calendar": 0, "bulletin": 1, "website": 2, "ecosystem": 3}
    records = []
    # Re-key: (a) a listing with no meeting time joins the one calendar slot of the same group, if there is exactly
    # one; (b) where a bilingual (en+es) listing exists, the English-only and Spanish-only listings of the same
    # ministry fold into it; (c) all Knights of Columbus mentions at one parish are one council record.
    slots = defaultdict(set)
    for pid, o in obs_list:
        if o["kind"] == "calendar":
            slots[(pid, o["base"])].add(o["key"])
    for pid, o in obs_list:
        if o["kind"] != "calendar" and len(slots.get((pid, o["base"]), ())) == 1:
            o["key"] = next(iter(slots[(pid, o["base"])]))
    stem = lambda k: re.sub(r"_(en\+es|es|vi|zh|ko|tl|pt|to|ig)(?=$|@)", "", k)
    bil = {(pid, stem(o["key"])) for pid, o in obs_list if o["lang"] == "en+es"}
    for pid, o in obs_list:
        if (pid, stem(o["key"])) in bil and "@" not in o["key"]:
            o["key"] = stem(o["key"]) + "_en+es"
            o["lang"] = "en+es"
        if o["sub"] == "knights_of_columbus":
            o["key"] = "kofc"
    obs = defaultdict(list)
    for pid, o in obs_list:
        obs[(pid, o["key"])].append(o)
    for (pid, key), os_ in obs.items():
        ent = ents[pid]
        prof = profiles.get(pid) or {}
        kinds = sorted({o["kind"] for o in os_}, key=lambda k: KIND_NAME[k])
        # display name: KofC with council number wins; else website > bulletin > calendar > ecosystem, specific first
        rec_lang = next((o["lang"] for o in os_ if o["lang"] == "en+es"), None)

        def name_rank(o):
            n = o["raw_name"]
            return (0 if re.search(r"council \d", n, re.I) else 1, 0 if o["lang"] == rec_lang else 1, KIND_NAME[o["kind"]],
                    0 if o["strength"] == "specific" else 1, len(n))
        best = sorted(os_, key=name_rank)[0]
        nm = smart_title(best["raw_name"]).strip(" .,:;")
        nm = re.sub(r"\s*(Visit link for details|\( ?Membership Application Available Online ?\))", "", nm).strip()
        if best["disp"] and (best["strength"] == "generic" or len(nm.split()) > 6 or
                             re.match(rf"^(\d|every|each|first|1st)", nm, re.I) or
                             re.search(rf"\b({DAYS})s?\b", nm, re.I)):
            nm = best["disp"]
            pre = {"men": "Men's", "women": "Women's", "young_adult": "Young Adult", "youth": "Youth"}.get(best["aud"])
            if pre and best["cat"] in ("scripture_study", "prayer_devotion", "service_mercy") and \
                    pre.split("'")[0].lower() not in nm.lower():
                nm = f"{pre} {nm}"
            if best["lang"] and best["lang"] != "en" and best["cat"] != "language_cultural":
                nm += " (" + {"es": "Spanish", "en+es": "English & Spanish", "vi": "Vietnamese", "zh": "Chinese",
                              "tl": "Filipino", "ko": "Korean", "pt": "Portuguese", "to": "Tongan",
                              "ig": "Igbo"}.get(best["lang"], best["lang"]) + ")"
        if best["sub"] == "knights_of_columbus":
            num = next((re.search(r"council (\d{3,5})", o["raw_name"], re.I) for o in os_
                        if re.search(r"council \d{3,5}", o["raw_name"], re.I)), None)
            nm = f"Knights of Columbus Council {num.group(1)}" if num else "Knights of Columbus"
        timed = sorted([o for o in os_ if o.get("meets")], key=lambda o: KIND_TIME[o["kind"]])
        meets_o = timed[0] if timed else None
        descs = [o["desc"] for o in sorted(os_, key=lambda o: KIND_NAME[o["kind"]]) if o.get("desc")]
        # a description that is really about another named organisation (context bleed) or is our own crawl note
        NAMED = {"columbiettes", "knights_of_columbus", "catholic_daughters", "young_ladies_institute",
                 "italian_catholic_federation", "st_vincent_de_paul", "legion_of_mary"}
        def foreign(dsc):
            nd = norm(dsc)
            return any(rx.search(nd) and sub_ in NAMED and sub_ != best["sub"] for rx, _k, _c, sub_, _a, _d in R)
        descs = [x for x in descs if not foreign(x)
                 and not re.search(r"environment\)|this environment|crawled|seen on|\bhad a\b", x, re.I)]
        loc = next((o["location"] for o in os_ if o.get("location")), None)
        primary = meets_o if (meets_o and meets_o["kind"] == "calendar") else best
        cat, sub, aud, lang = best["cat"], best["sub"], best["aud"], best["lang"]
        lang_assumed = False
        if not lang:
            langs = [o["lang"] for o in os_ if o["lang"]]
            lang = langs[0] if langs else None
        if cat == "language_cultural" and not lang:
            lang = {"filipino": "tl", "vietnamese": "vi", "hispanic_latino": "es", "chinese": "zh", "korean": "ko",
                    "portuguese_brazilian": "pt", "tongan": "to", "igbo_nigerian": "ig"}.get(sub)
        if not lang:
            lang, lang_assumed = "en", True
        distinct_urls = {o["url"] for o in os_}
        ecos_inferred_only = kinds == ["ecosystem"] and all(o.get("basis") == "inferred" for o in os_)
        cal_n = max([o.get("calendar_dates_90d") or 0 for o in os_] + [0])
        if ecos_inferred_only or best["strength"] == "fallback" or all(o.get("summary_only") for o in os_):
            conf = "low"
        elif len(kinds) >= 2 or cal_n >= 3 or (len(distinct_urls) >= 2 and best["strength"] == "specific"):
            conf = "high"
        elif best["strength"] == "generic":
            conf = "low"
        else:
            conf = "medium"
        scope = "diocesan" if pid == "dio-oakland" else "parish"
        if scope == "parish":
            addr = (prof.get("address") or {})
            street = addr.get("street")
            if street and re.search(r"p\.?\s?o\.? box", street, re.I):
                street = None
            location = loc or ", ".join(x for x in [ent.get("name"), street, addr.get("city") or ent.get("city")] if x)
        else:
            location = loc  # diocesan groups meet in several places; not stated
        has_bulletin = "bulletin" in kinds
        if sub == "knights_of_columbus":
            contact = "council (Knights of Columbus council finder, kofc.org) or parish office"
        elif scope == "diocesan":
            contact = "diocesan office or movement secretariat (see source)"
        elif has_bulletin:
            contact = "ministry coordinator (see bulletin) or parish office"
        else:
            contact = "parish office"
        meets = meets_o["meets"] if meets_o else None
        if meets:
            join = f"Come to a meeting ({meets}); first-timers can call the parish office to confirm."
        elif cat == "formation":
            join = "Contact the parish office to ask when the next session starts and how to register."
        elif cat == "liturgical":
            join = "Ask the parish office or the ministry coordinator how to join and whether training is offered."
        elif scope == "diocesan":
            join = "Check the source page for upcoming gatherings and contacts."
        else:
            join = "Contact the parish office to ask when it meets."
        seen = max((o["seen"] or "") for o in os_) or None
        rec = {
            "id": f"{pid}:{key}",
            "parish_id": pid,
            "parish_name": ent.get("name"),
            "city": (ent.get("attrs") or {}).get("city") or prof.get("city"),
            "scope": scope,
            "name": nm,
            "category": cat,
            "category_label": CATEGORIES[cat],
            "subcategory": sub,
            "audience": aud,
            "language": lang,
            "language_assumed": lang_assumed,
            "meets": meets,
            "next_date": meets_o.get("next") if meets_o else None,
            "location": location,
            "geo": ({"lat": prof["geo"].get("lat"), "lng": prof["geo"].get("lng")} if (prof.get("geo") or {}).get("lat")
                    else ({"lat": ent["lat"], "lng": ent["lng"]} if ent.get("lat") else None)),
            "contact_role": contact,
            "description": descs[0] if descs else None,
            "join_how": join,
            "source_url": primary["url"],
            "source_kind": primary["kind"],
            "fetched_at": primary["fetched_at"],
            "confidence": conf,
            "last_seen": seen,
            "flags": [f for f in ["closing_announced" if any(o.get("closing") for o in os_) else None,
                                  "kofc_council_inferred_from_name" if ecos_inferred_only else None] if f],
            "sources": sorted({(o["kind"], o["url"]) for o in os_}),
        }
        rec["sources"] = [{"kind": k, "url": u} for k, u in rec["sources"]]
        if meets_o and meets_o["kind"] != primary["kind"]:
            rec["meets_source_url"] = meets_o["url"]
        if best.get("bulletin_page"):
            rec["bulletin_page"] = best["bulletin_page"]
        records.append(rec)

    records.sort(key=lambda r: (r["scope"] != "parish", r["parish_id"], list(CATEGORIES).index(r["category"]),
                                r["name"]))
    # summary
    parish_recs = [r for r in records if r["scope"] == "parish"]
    parishes = {r["parish_id"] for r in parish_recs}
    all_parishes = sorted(i for i, e in ents.items() if e.get("type") == "parish")
    out = {
        "generated_at": now,
        "generated_by": "ingest/public/groups/build_groups.py",
        "taxonomy": "docs/design/groups-taxonomy.md",
        "privacy": "Groups only. No private person's name, phone or email; contact_role is a public role.",
        "summary": {
            "groups": len(records),
            "parish_groups": len(parish_recs),
            "diocesan_groups": len(records) - len(parish_recs),
            "parishes_with_groups": len(parishes),
            "parishes_in_seed": len(all_parishes),
            "parishes_without_groups": [p for p in all_parishes if p not in parishes],
            "with_meeting_time": sum(1 for r in records if r["meets"]),
            "with_description": sum(1 for r in records if r["description"]),
            "by_category": dict(Counter(r["category"] for r in records).most_common()),
            "by_audience": dict(Counter(r["audience"] for r in records).most_common()),
            "by_language": dict(Counter(r["language"] for r in records if not r["language_assumed"]).most_common()),
            "language_assumed_english": sum(1 for r in records if r["language_assumed"]),
            "by_source_kind": dict(Counter(r["source_kind"] for r in records).most_common()),
            "by_confidence": dict(Counter(r["confidence"] for r in records).most_common()),
            "multi_source": sum(1 for r in records if len({s["kind"] for s in r["sources"]}) > 1),
        },
        "dropped": dict(dropped.most_common()),
        "dropped_examples": {k: v for k, v in dropped_examples.items()},
        "records": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(out["summary"], indent=1, ensure_ascii=False))
    print("dropped:", out["dropped"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
