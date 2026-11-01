"""Keyword guess of an event's category, in the Church's own terms (offerings, not judgments).

The category is a *guess* from the title and any category the source supplies; it is labelled as such in
data/real/calendars/README.md. Order matters: the first matching rule wins.

Categories
    notice                   office hours, closures, "no Mass", cancellations
    sacramental_preparation  baptism/marriage/confirmation/First Communion prep, OCIA/RCIA
    reconciliation           confession times
    liturgy                  Mass, Liturgy of the Hours, Simbang Gabi
    devotion_prayer          adoration, rosary, novena, stations, holy hour, prayer groups
    youth_young_adults       youth ministry, teens, young adults, CYO, campus
    formation                Bible study, classes, talks, retreats, faith formation, catechesis
    charity_service          food pantry, St. Vincent de Paul, drives, outreach, immigration help
    liturgical_ministries    choir/schola rehearsal, altar servers, lectors, sacristans, ushers
    meetings_governance      councils, committees, staff and group meetings, Knights, guilds
    school                   parish-school calendar items
    community                socials, festivals, meals, fundraisers, concerts
    other                    anything else
"""
from __future__ import annotations

import re

RULES: list[tuple[str, str]] = [
    ("notice", r"\boffice\s+(?:open|closed|hours)\b|\bclosed\b|\bno\s+(?:mass|masses|class|classes|school|"
               r"religious\s+education|faith\s+formation|baptisms|confessions?|exposition|adoration)\b|"
               r"\bcancel(?:l?ed)?\b|\bholiday\b|_no_|\bschedule\s+change\b"),
    ("sacramental_preparation", r"\bbaptism(?:al)?\s+(?:class|prep|preparation)|pre-?cana|marriage\s+prep|"
               r"engaged\s+encounter|(?:confirmation|first\s+(?:holy\s+)?communion|first\s+reconciliation|"
               r"primera\s+comuni[oó]n|confirmaci[oó]n)\s+(?:class|classes|prep|preparation|retreat|session|"
               r"sessions|meeting|program|registration|parent|parents|practice|rehearsal)|"
               r"(?:first\s+(?:holy\s+)?communion|first\s+reconciliation|confirmation)\s+class|"
               r"children'?s\s+first\s+reconciliation|\bocia\b|\brcia\b|catechumen|\binquiry\b|\bplatica|"
               r"\bplática|sacramental\s+prep|rite\s+of\s+(?:acceptance|election)"),
    ("liturgy", r"\bmass(?:es)?\b|\bmisa\b|\bthánh\s+lễ\b|\beucharist(?:ic)?\s+celebration\b|\bliturgy\b|"
               r"\bsimbang\s+gabi\b|\bvespers\b|\blauds\b|\bdivine\s+office\b|\bliturgy\s+of\s+the\s+hours\b|"
               r"\bvigil\b|\bsolemnity\b|\btriduum\b|\bpassion\b|\bseder\b|\bconfirmations?\b|"
               r"\bfirst\s+(?:holy\s+)?communions?\b|\bordination\b|\binstallation\b|\bdedication\b"),
    ("reconciliation", r"\bconfessions?\b|\breconciliation\b|\bpenance\b|\bconfesi[oó]n|\breconciliaci[oó]n|"
               r"giải\s+tội|xưng\s+tội|\bpenitential\b"),
    ("devotion_prayer", r"\badoration\b|\bexposition\b|\bbenediction\b|\bholy\s+hour\b|\brosary\b|\brosario\b|"
               r"\bchaplet\b|\bnovena\b|\bstations\s+of\s+the\s+cross\b|\bvia\s+crucis\b|\bdivine\s+mercy\b|"
               r"\bprayer\b|\boraci[oó]n\b|\bpray\b|\bpraise\b|\bworship\b|\btaiz[eé]\b|\blectio\b|\bchầu\b|"
               r"mân\s+côi|\bprocession\b|\bprocesi[oó]n\b|\bmeditation\b|\bblessing\s+of\b|\bconsecration\b|"
               r"\blegion\s+of\s+mary\b|\bcharismatic\b"),
    ("youth_young_adults", r"\byouth\b|\bteens?\b|\bjr\.?\s+high\b|\bhigh\s+school\b|\bhsym\b|\bmsym\b|"
               r"\bedge\b|\blife\s*teen\b|\byoung\s+adults?\b|\bj[oó]venes\b|\bcyo\b|\bcampus\b|\bnewman\b|"
               r"\bthiếu\s+nhi\b|\bvbs\b|\bscouts?\b"),
    ("formation", r"\bbible\b|\bscripture\b|\bfaith\s+formation\b|\breligious\s+ed|\bccd\b|\bcatechesis\b|"
               r"\bcatechism\b|\bcatecismo\b|\bcatequesis\b|\bclass(?:es)?\b|\bstudy\b|\bbook\s+club\b|"
               r"\blecture\b|\btalk\b|\bspeaker\b|\bseminar\b|\bworkshop\b|\balpha\b|\bformed\b|\bretreat\b|"
               r"\bretiro\b|\brecollection\b|\bparish\s+mission\b|\bthat\s+man\s+is\s+you\b|\bmen'?s\s+group\b|"
               r"\bwomen'?s\s+group\b|\bsmall\s+(?:faith\s+)?group\b|\bformation\b|\bcursillo\b|\bevangeli"),
    ("charity_service", r"\bfood\s+(?:pantry|bank|drive|distribution|closet)\b|\bst\.?\s*vincent\b|\bsvdp\b|"
               r"\bsoup\b|\bhomeless\b|\bshelter\b|\bclothing\b|\bcoat\s+drive\b|\bblood\s+drive\b|\boutreach\b|"
               r"\bgiving\s+tree\b|\btoy\s+drive\b|\bthanksgiving\s+baskets?\b|\bimmigration\b|\blegal\s+consult|"
               r"\bcatholic\s+charities\b|\bhabitat\b|\bworks\s+of\s+mercy\b|\bsocial\s+justice\b|\brespect\s+life\b|"
               r"\bpro[-\s]?life\b|\bvolunteer|\bmeals?\s+(?:program|ministry)\b|\bcare\s+center\b|\bdonation"),
    ("liturgical_ministries", r"\bchoir\b|\bschola\b|\bcantors?\b|\bcoro\b|\brehearsal\b|\baltar\s+servers?\b|"
               r"\blectors?\b|\bsacristans?\b|\bushers?\b|\bhospitality\s+ministers?\b|\beucharistic\s+ministers?\b|"
               r"\bextraordinary\s+ministers\b|\benvironment\b|\bdecor\b|\bmusicians?\b|\bmusic\s+ministry\b"),
    ("meetings_governance", r"\bcouncil\b|\bcommittee\b|\bboard\b|\bstaff\s+meeting\b|\bmeeting\b|\bmtg\b|"
               r"\bfinance\b|\bbudget\b|\bknights\s+of\s+columbus\b|\bk\s*of\s*c\b|\bguild\b|\bsociety\b|"
               r"\bsodality\b|\bcatholic\s+daughters\b|\bcda\b|\byli\b|\bserra\b|\bofficers\b|\bconference\b"),
    ("school", r"\bschool\b|\bsds\b|\bstudents?\b|\bteachers?\b|\bpta\b|\bptg\b|\bminimum\s+day\b|"
               r"\bhalf\s+day\b|\bopen\s+house\b|\bgrade\b|\bkindergarten\b|\bpreschool\b|\bday\s+camp\b"),
    ("community", r"\bfestival\b|\bfiesta\b|\bkermes|\bpotluck\b|\bpicnic\b|\bbreakfast\b|\bbrunch\b|"
               r"\blunch(?:eon)?\b|\bdinner\b|\bsupper\b|\bsocial\b|\bcoffee\b|\bdonuts?\b|\bbingo\b|\bdance\b|"
               r"\bparty\b|\bcelebration\b|\bfundraiser\b|\bgala\b|\bauction\b|\bcrab\s+feed\b|\bpancake\b|"
               r"\btrunk\s+or\s+treat\b|\bhalloween\b|\bmovie\b|\bconcert\b|\bgame\s+night\b|\bbbq\b|"
               r"\bbarbecue\b|\btea\b|\bfellowship\b|\bexhibit\b|\bbazaar\b|\bsale\b|\bfair\b|\bcarnival\b|"
               r"\bpumpkin\b|\bcorn\s+maze\b|\bhike\b|\bpilgrimage\b|\bwelcome\b|\breception\b|\bchess\b|"
               r"\bmessiah\b|\bsing[-\s]?along\b|\bfarewell\b"),
]
_COMPILED = [(c, re.compile(p, re.I)) for c, p in RULES]


def guess_category(title: str, source_categories: list[str] | None = None) -> str:
    text = title or ""
    for cat, rx in _COMPILED:
        if rx.search(text):
            return cat
    extra = " ".join(source_categories or [])
    if extra:
        for cat, rx in _COMPILED:
            if rx.search(extra):
                return cat
    return "other"


if __name__ == "__main__":
    for t in ["Office OPEN", "Adoration, Mass, and Confession", "Children's First Reconciliation Class #3",
              "Adult Choir Rehearsal", "That Man is You! at St. John Vianney, Walnut Creek", "Giving Tree setup",
              "Free Immigration Legal Consultations", "High School Youth Ministry (HSYM) / SAFE ENVIRONMENT LESSON",
              "@SDS = Halloween Party / Parking Lot Trunk Or Treat", "Community Committee Weekly Meeting"]:
        print(f"{guess_category(t):24s} {t}")
