"""Privacy screen for media titles (videos, podcast episodes, news posts, events) — the 2026-10-29 loosened rule.

Rule (CLAUDE.md): never store private individuals' data. Public people may be named: clergy and religious, staff in a
public role, and guests who appear on an organization's published program (an author interviewed on a podcast, a
speaker at a public event). The first media build dropped any title in which the general name detector
(ingest/public/privacy.redact) found a name, which also dropped bishops, Dominican sisters, "Memorial of Saints ..."
and podcast guests. This screen keeps those and quarantines only titles that name a PRIVATE person:

  * occasions about one private person: weddings, quinceañeras, baptisms of, funerals / celebrations of life /
    "remembering X" (unless the person is clergy or religious, or the "memorial" is a liturgical memorial of a saint),
    Mass intentions, birthdays of lay people;
  * students and alumni ("Jane Doe '14", "student X", "parishioner X");
  * any other name the general detector flags after the public spans below are masked.

Public spans that are masked before the general detector runs:
  * an honorific + capitalised words: Fr., Father, Bishop, Archbishop, Cardinal, Pope, Msgr., Deacon, Dcn., Sister(s),
    Sr., Brother, Br., Mother, Abbot, Saint/St./San/Santa, Pastor, Rev., President, Principal, Mr./Ms./Mrs./Dr.
    (on an organization's own channel these are staff in a public role);
  * capitalised words + a religious-institute suffix (", OP", "SJ", "OFM", "rscj", "CSC" ...);
  * for published programs (video or podcast episode) from organizations other than schools: the guest segment after
    "with", "featuring", "ft.", "interview with", "entrevista con", "keynote:", "presents", "by" — a guest on a
    public program is a public persona. School videos never get this pass (their guests are usually students).

Quarantined items never reach the feed; data/real/media/quarantine.json keeps the item's id, organization, kind,
date, reason and a REDACTED title (names replaced by "[name withheld]") so the review page can audit the drop.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
# The shared detector (ingest/public/privacy.py: name spotting, the public-people allow-list, contact stripping). This
# module only adds the media-specific rules on top: which spans in a title are public (honorifics, institute suffixes,
# program guests) and which occasions are private. The calendar connector's screen is not a fallback any more.
import privacy as pub_privacy  # noqa: E402
DETECTOR = "ingest/public/privacy.py"

CAP = r"[A-ZÁÉÍÓÚÑ][\w'’.-]*"
NAME_RUN = rf"{CAP}(?:\s+(?:de|del|la|van|von|da|di|O'|Mc)?\s*{CAP}){{0,3}}"
HONORIFIC = (r"(?:Fr\.?|Father|Padre|Bishop|Obispo|Archbishop|Arzobispo|Cardinal|Pope|Papa|Msgr\.?|Monsignor|Deacon|"
             r"Dcn\.?|Di[aá]cono|Sisters?|Sr\.?|Hermana|Brother|Br\.?|Mother|Madre|Abbot|Saints?|St\.?|Sts\.?|San|Santa|"
             r"Santo|Pastor|Rev\.?|Reverend|President|Principal|Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Coach|Superintendent)")
SUFFIX = (r"(?:OP|O\.P\.|SJ|S\.J\.|OFM|O\.F\.M\.|OFM ?Cap|rscj|RSCJ|CSC|OSB|O\.S\.B\.|SDB|OCD|OCDS|CSJ|SNJM|FSC|"
          r"CSsR|C\.Ss\.R\.|OSF|MM|SVD|CM|OMI|SM|PBVM|OSU|SND|DC|SSS|OSA|OCarm|O\.Carm\.|CP|SCJ|SP)")
PUBLIC_HONORIFIC = re.compile(rf"\b{HONORIFIC}\s+{NAME_RUN}(?:\s*(?:,|and|&|y)\s*{NAME_RUN})*")
PUBLIC_SUFFIX = re.compile(rf"{NAME_RUN},?\s+{SUFFIX}(?!\w)")
GUEST = re.compile(r"\b(?:with|featuring|feat\.|ft\.|interview with|an interview with|entrevista con(?: el| la)?|con|"
                   r"keynote:?|presents?|by|guest:?|hosted by|led by)\s+(.+?)(?=\s*(?:\||–|—| - |\(|:|$))", re.I)
PRIVATE_MARKER = re.compile(r"\b(parishioner|feligr[eé]s|student|estudiante|alumn[ia]|alumnus|class of|'\d{2}\b|‘\d{2}\b|’\d{2}\b)", re.I)

ALUMNI = re.compile(rf"\b{CAP}(?: {CAP})+\s*['‘’]\d{{2}}\b")
LAY_ROLE_NAME = re.compile(rf"\b(?:parishioner|feligr[eé]s|student|estudiante|alumn[ia]|alumnus|volunteer)\s+{CAP}\s+{CAP}", re.I)
WEDDING = re.compile(r"\b(wedding|boda|quincea|xv a[nñ]os|baptism of|bautizo de|bautismo de|engagement|vow renewal of)\b", re.I)
INTENTION = re.compile(r"\bmass intentions?\b|\bintenciones\b|\bmisa (?:por|para) (?:el |la )?[A-Z]", re.I)
DEATH = re.compile(r"\b(funeral|memorial|obituar|rest in peace|\brip\b|celebration of life|in loving memory|in memoriam|"
                   r"remembering|requiem for|vigil for|rosary for|velorio|novenario|misa de cuerpo presente)\b", re.I)
LITURGICAL_MEMORIAL = re.compile(r"\bmemorial of (?:the )?(?:saints?|sts?\.?|blessed|our lady|the)\b|\bmemorial (?:altar|day)\b|"
                                 r"\ball souls\b|\bd[ií]a de (?:los )?(?:muertos|difuntos)\b|\bmemorial acclamation\b", re.I)
CLERGY_DEATH = re.compile(rf"\b(?:funeral|memorial|requiem|vigil|rosary|remembering|in memoriam)\b.*\b{HONORIFIC}\s+{CAP}|"
                          rf"\b(?:funeral|memorial|remembering|in memoriam)\b.*{NAME_RUN},?\s+{SUFFIX}(?!\w)", re.I)
LAY_BIRTHDAY = re.compile(r"\b(\d+(?:st|nd|rd|th)\s+birthday|birthday (?:mass|blessing) for)\b", re.I)
RELIGIOUS = re.compile(rf"\b(?:Sister|Sr\.?|Brother|Br\.?|Fr\.?|Father|Bishop|Msgr\.?|Deacon|Mother)\b|,?\s+{SUFFIX}(?!\w)")

MASK = "someone"  # lowercase: the name detector ignores it


def _mask_public(title: str, kind: str, org_type: str | None) -> str:
    t = PUBLIC_SUFFIX.sub(MASK, title)
    t = PUBLIC_HONORIFIC.sub(MASK, t)
    if kind in ("video", "episode") and org_type not in ("school",) and not PRIVATE_MARKER.search(title):
        t = GUEST.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + MASK, t)
    return t


def redacted(title: str) -> str:
    """The title as the audit file may show it: every name the general detector sees is withheld, except masked
    public spans, which are put back."""
    out, _ = pub_privacy.redact(title or "", "raw")
    return out


def screen(title: str, kind: str = "video", org_type: str | None = None) -> tuple[bool, str | None]:
    """(keep, reason). reason is one of: wedding_baptism_quince, mass_intention, private_death_or_memorial,
    lay_birthday, student_or_alumni, parishioner_named, names_private_person, empty."""
    t = (title or "").strip()
    if not t or "[title withheld" in t:
        return False, "empty"
    if INTENTION.search(t):
        return False, "mass_intention"
    if WEDDING.search(t):
        return False, "wedding_baptism_quince"
    if DEATH.search(t) and not LITURGICAL_MEMORIAL.search(t) and not CLERGY_DEATH.search(t):
        # a ministry page ("Catholic Funeral and Cemetery Services ...") names no one: let the name test decide
        if re.search(rf"\b(?:remembering|in memoriam|in loving memory|celebration of life|rest in peace|rip)\b\s*:?\s*{CAP}", t, re.I) \
                or pub_privacy.redact(_mask_public(t, kind, org_type), "raw")[1] > 0:
            return False, "private_death_or_memorial"
    if LAY_BIRTHDAY.search(t) and not RELIGIOUS.search(t):
        return False, "lay_birthday"
    if ALUMNI.search(t):
        return False, "student_or_alumni"
    if LAY_ROLE_NAME.search(t):
        return False, "parishioner_named"
    masked = _mask_public(t, kind, org_type)
    if pub_privacy.redact(masked, "raw")[1] > 0:
        return False, "names_private_person"
    return True, None


if __name__ == "__main__":
    cases = {
        "A Sister's Story: Mary Jo Sieg, OP": True,
        "Limited Series: Sisters Sue Van Balen, OP, and Lorene Heck, OP": True,
        "Faith & Doubt: Can Doubt Actually Strengthen Faith? - with Fr. Kevin Scalf and Newton Lih": True,
        "A Message from Bishop Benny Varghese": True,
        "2026 10 19 - Memorial of Saints John de Brébeuf & Isaac Jogues, Priests, & Companions, Mar": True,
        "'Where Wonder Leads': Exploring Faith and Imagination With Eric Clayton": True,
        "Remembering Martha": False,
        "Joe McLean ‘92": False,
        "Alumni Spotlight | Carrie Truong '25": False,
        "What’s Happening at St. Isidore’s? FOCUS Missionary and Parishioner Alex Silva!": False,
        "Wedding of John and Mary": False,
        "Funeral Mass for Fr. John Smith": True,
        "Spirit Week 2026 Night Rally": True,
        "Is It a Sin to Doubt God? | A Catholic Priest Explains": True,
        "2027 Mass Intentions": False,
        "Official appointments": True,
        "A Sister Reflects: A Time Away": True,
        "The Priestly Ordination of Young Kyun Mun, C.Ss.R.": True,
        "New Principal at SJND | Mr. Jeff Danilak": True,
    }
    bad = 0
    for t, want in cases.items():
        got, why = screen(t, "video", "school" if "SJND" in t or "'25" in t else "parish")
        if got != want:
            bad += 1
            print("MISMATCH", want, got, why, "|", t)
    print(f"media privacy self-test: {len(cases) - bad}/{len(cases)} ok")
