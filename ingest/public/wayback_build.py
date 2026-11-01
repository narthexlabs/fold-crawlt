"""Wayback Machine pass, step 2: Mass times and groups from captures read on a home connection (2026-10-31).

    # step 1 (on a machine that reaches web.archive.org; stdlib only):
    python3 ingest/public/wayback_home.py cdx|fetch ...      -> captures/ + manifest.json  (copy to data/raw/wayback/)
    # step 2 (here):
    python3 -I ingest/public/wayback_build.py --have-json <mass_schedule export> --groups-existing <ministry export>
        -> data/real/wayback_2026-10-31.json, /tmp/wayback_mass.sql, /tmp/wayback_groups.sql (INSERT only)
    python3 -I ingest/public/wayback_build.py --merge-recovery   # fold the result into recovery_2026-10-31.json

Targets: the 13 parishes that still had no own-source times or bulletin after the side-door pass
(data/real/recovery_2026-10-31.json). Captures were listed with the CDX API (filter=statuscode:200, read newest
first) and fetched as raw `id_` captures; manifest.json records each capture's original URL, archive URL and
capture timestamp.

Times. Every row below was read off the capture by hand (REVIEWED): eCatholic pages keep their section headings
(Mass / Confession / Adoration) in <header>, which parish_sites.visible_lines drops, so the rule-based parser
mislabels them; Lourdes's schedule is a table whose columns a line reader would mix. The parser output is kept
in the report as a comparison only. Rules:
  * captures older than 2025-01-01 are not used for times;
  * a kind is inserted when the parish has no own-source rows of that kind (existing rows DiscoverMass only, or
    none), or when its own-source rows all agree with the capture (the capture then adds what the crawl missed);
    a candidate identical to an existing own-source row (kind, day, time) is not inserted again;
  * a parish with hand-checked (curated) Mass rows gets no Mass rows (the app shows the curated rows only);
  * DiscoverMass rows are kept; where a row agrees with one, the DiscoverMass day wording, site tag and start
    time are reused so the parish page shows one line, and an English Mass the DiscoverMass row leaves without a
    language stays without one (the app reads an unstated language as English); every disagreement is listed.
  * source `wayback-<capture date>`, source_url the archive URL. Good Shepherd's rows come from its current LPi
    bulletin instead (source `bulletin-lpi-2026-11-01`), whose code 05-1955 the archived site gave away.
Groups. Names were picked by hand from the captured ministry pages (GROUPS), then go through the same taxonomy,
privacy and duplicate checks as every other source (build_groups.classify / private_name_risk, bulletins/load.py
display + stem + nkey). Older captures are allowed for groups; the capture date is the row's last_seen and its
flag `source:wayback-<date>`. No parishioner names are stored: contact names on the pages are never copied.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "groups"))
sys.path.insert(0, str(HERE / "bulletins"))
from common import RAW, REAL, ROOT, now_iso, save_json  # noqa: E402

MANIFEST = RAW / "wayback" / "manifest.json"
OUT = REAL / "wayback_2026-10-31.json"
TIMES_CUTOFF = "2025-01-01"
GS_BULLETIN = "https://container.parishesonline.com/bulletins/05/1955/20261101B.pdf"

# entity -> list of (capture: (original URL, capture timestamp) | bulletin URL, rows). Read by hand, 2026-10-31.
R = lambda k, d, t, l, raw: {"kind": k, "day": d, "time": t, "language": l, "raw": raw}  # noqa: E731
REVIEWED = {
    "christ-light-oakland": [(("https://www.ctlcathedral.org/", "20260616220917"), [
        # home page modules "Mass Times" / "Confession Times" / "Adoration Times" (same as /mass, 2026-05-27)
        R("mass", "Sunday", "08:00", "Vietnamese", "Sunday 8:00 AM Vietnamese"),
        R("mass", "Sunday", "11:00", None, "Sunday 11:00 AM English"),
        R("mass", "Sunday", "13:30", "Spanish", "Sunday 1:30 PM Spanish"),
        R("mass", "Monday–Friday", "12:10", None, "Monday–Friday 12:10 PM English (Thursday: followed by Adoration)"),
        R("mass", "First Friday of the month", "19:00", "Vietnamese", "1st Friday 7:00 PM Vietnamese"),
        R("mass", "Saturday", "07:30", "Vietnamese", "Saturday 7:30 AM Vietnamese"),
        R("confession", "Sunday", "10:20", None, "Sunday 10:20 AM to 10:50 AM"),
        R("confession", "Tuesday", "11:30", None, "Tuesday 11:30 AM to 12:00 PM"),
        R("confession", "Thursday", "11:30", None, "Thursday 11:30 AM to 12:00 PM"),
        R("adoration", "Second Tuesday of the month", "19:00-20:00", None, "Tuesday 7:00 PM to 8:00 PM (2nd Tuesday Holy Hour)"),
        R("adoration", "Thursday", "13:00-17:00", None, "Thursday 1:00 PM to 5:00 PM")])],
    "leo-great-oakland": [(("https://ctlcathedral.org/st-leo", "20260419202312"), [
        R("mass", "Saturday", "17:00", None, "Saturday: 5:00 pm (Vigil)"),
        R("mass", "Sunday", "09:00", None, "Sunday: 9:00 am"),
        R("mass", "Tuesday", "09:00", None, "Tuesday: 9:00 am"),
        R("mass", "Thursday", "09:00", None, "Thursday: 9:00 am"),
        R("confession", "Saturday", "16:30", None, "The Sacrament of Reconciliation (Confession) is offered on Saturdays at 4:30 pm")])],
    "mark-richmond": [(("https://stmarkrichmond.org/", "20260213165637"), [
        # Spanish home page "MISAS"; the English page lists the same Sunday Masses; confession only by reservation
        R("mass", "Sunday", "08:00", "Spanish", "Misas dominicales: Domingo 8:00 (español)"),
        R("mass", "Sunday", "09:30", "Spanish", "Misas dominicales: Domingo 9:30 (español)"),
        R("mass", "Sunday", "11:00", None, "Sunday 11:00 am (English)"),
        R("mass", "Sunday", "12:30", "Spanish", "Misas dominicales: Domingo 12:30 (español)"),
        R("mass", "Wednesday, Friday", "08:30", "Spanish", "Misas entre semana: Miércoles y Viernes 8:30 am (español)"),
        R("mass", "First Saturday of the month", "19:00", "Spanish",
          "Misas los primeros sábados del mes 7:00 pm (español), seguido de Adoración Eucarística"),
        R("adoration", "Wednesday", "09:00-19:30", None, "Adoración miércoles: Miércoles 9:00 am – 7:30 pm")])],
    "perpetua-lafayette": [(("https://www.stperpetua.org/mass-times-directions", "20250424165917"), [
        R("mass", "Saturday", "17:00", None, "Saturdays 5:00 PM, Church"),
        R("mass", "Sunday", "09:30", None, "Sundays 9:30 AM, Church"),
        R("mass", "Sunday", "11:30", None, "Sundays 11:30 AM, Church"),
        R("mass", "Monday, Tuesday, Thursday, Friday", "09:00", None, "Daily Mass M, T, TH & F 9:00 AM, Chapel"),
        R("mass", "Wednesday", "09:15", None, "Daily Mass Wednesday 9:15 AM, Church"),
        R("confession", "Saturday", "16:00", None, "Confession Saturdays 4:00 PM - 5:00 PM, Church"),
        R("confession", "Sunday", "09:15", None, "Confession Sundays 9:15 AM - 9:30 AM, Church"),
        R("confession", "Sunday", "11:15", None, "Confession Sundays 11:15 AM - 11:30 AM, Church"),
        R("confession", "Monday–Friday", "08:45-09:00", None, "Confession Monday - Friday 8:45 AM - 9:00 AM"),
        R("adoration", "Friday", "09:30", None, "Adoration Friday 9:30am - noon, Chapel")])],
    "anthony-mary-help-christians-oakland": [
        (("https://stanthonymaryhelp.org/vietnamese", "20250518134557"), [
            # "Chương Trình Thánh Lễ" on the Vietnamese community page (the parish's newest Mass list in the archive;
            # its own Mass-times page was last captured 2024-05-27). The page is in Vietnamese ("Tiếng Anh" = English,
            # "tại Thánh Antôn" = at St. Anthony); raw lines are given in English (the privacy screen reads the mixed
            # Vietnamese/English lines as Mass intentions and withholds them).
            R("mass", "Saturday", "19:30", "Vietnamese", "[St. Anthony] Saturday 7:30 PM Vietnamese at St. Anthony"),
            R("mass", "Sunday", "08:00", None, "[St. Anthony] Sunday 8:00 AM English at St. Anthony"),
            R("mass", "Sunday", "09:00", "Eritrean", "[Mary Help of Christians] Sunday 9:00 AM, Eritrean Mass at Mary Help of Christians"),
            R("mass", "Sunday", "09:30", "Spanish", "[St. Anthony] Sunday 9:30 AM Spanish at St. Anthony"),
            R("mass", "Sunday", "11:30", "Vietnamese", "[St. Anthony] Sunday 11:30 AM Vietnamese at St. Anthony"),
            R("mass", "Sunday", "11:45", "Spanish", "[Mary Help of Christians] Sunday 11:45 AM Spanish at Mary Help of Christians"),
            R("mass", "Monday, Wednesday, Thursday, Friday", "08:00", None,
              "[St. Anthony] Monday, Wednesday, Thursday, Friday 8:00 AM English at St. Anthony"),
            R("mass", "Tuesday", "08:05", None, "[St. Anthony] Tuesday 8:05 AM English at St. Anthony Church"),
            R("mass", "Tuesday, Thursday", "19:30", "Vietnamese", "[St. Anthony] Tuesday and Thursday 7:30 PM Vietnamese at St. Anthony"),
            R("mass", "First Friday of the month", "18:00", "Spanish", "[St. Anthony] First Friday 6:00 PM Spanish at St. Anthony"),
            R("mass", "First Friday of the month", "19:30", "Vietnamese", "[St. Anthony] First Friday 7:30 PM Vietnamese at St. Anthony")]),
        (("https://stanthonymaryhelp.org/confession-times", "20250418215651"), [
            R("confession", "Tuesday", "17:00-17:45", "Spanish", "[St. Anthony] Tuesday 5:00 PM to 5:45 PM, en español en la Iglesia de San Antonio"),
            R("confession", "Saturday", "19:00-19:30", None, "Saturday 7:00 PM to 7:30 PM (Bí Tích Hòa Giải)")])],
    "good-shepherd-pittsburg": [(GS_BULLETIN, [
        # LPi bulletin 2026-11-01, p.1 "Pray & Worship with Us" box (OCR). Holy days, the 5th-Saturday healing Mass
        # (no time) and "Our Lady of Guadalupe 2nd Friday 7PM" (a devotion; see groups) are not rows.
        R("mass", "Saturday", "17:30", None, "Vigil Mass (Saturday) 5:30PM"),
        R("mass", "Sunday", "08:00", None, "Sunday 8AM, 10AM (livestreamed), 12PM"),
        R("mass", "Sunday", "10:00", None, "Sunday 8AM, 10AM (livestreamed), 12PM"),
        R("mass", "Sunday", "12:00", None, "Sunday 8AM, 10AM (livestreamed), 12PM"),
        R("mass", "Sunday", "15:00", "Spanish", "3PM Spanish"),
        R("mass", "First Sunday of the month", "16:30", "French", "French Mass 4:30PM, first Sunday of the month"),
        R("mass", "Monday–Friday", "07:30", None, "Daily Mass Monday-Friday 7:30AM (livestreamed)"),
        R("mass", "Saturday", "08:00", None, "Daily Mass Saturday 8AM"),
        R("mass", "First Friday of the month", "19:00", None, "First Friday 7PM Sacred Heart"),
        R("confession", "Friday", "08:00-09:00", None, "Confession Fridays 8AM-9AM"),
        R("confession", "Saturday", "16:00-17:00", None, "Confession Saturdays 4PM-5PM or by appointment"),
        R("adoration", "Friday", "08:00-09:00", None, "Eucharistic Adoration Fridays 8AM-9AM"),
        R("adoration", "First Friday of the month", "08:00-19:00", None,
          "Eucharistic Adoration First Fridays 8AM-7PM (adoration chapel: call office for code)")])],
    "divine-mercy-oakland": [(("https://www.divinemercyoak.org/mass", "20260612050646"), [
        R("mass", "Saturday", "16:00", "English", "[St. Paschal Baylon] Saturday 4:00 PM English @ St. Paschal church 3700 Dorisa Ave."),
        R("mass", "Saturday", "17:30", "English", "[St. Lawrence O’Toole] Saturday 5:30 PM English @ St. Lawrence church 3725 High St."),
        R("mass", "Sunday", "08:00", "English", "[St. Paschal Baylon] Sunday 8:00 AM English"),
        R("mass", "Sunday", "10:00", "Vietnamese", "[St. Paschal Baylon] Sunday 10:00 AM Vietnamese"),
        R("mass", "Sunday", "10:00", "English", "[St. Lawrence O’Toole] Sunday 10:00 AM English"),
        R("mass", "Sunday", "12:15", "Spanish", "[St. Lawrence O’Toole] Sunday 12:15 PM Spanish"),
        R("mass", "Monday, Tuesday, Thursday, Friday", "08:30", "English", "[St. Lawrence O’Toole] M Tu Th F 8:30 AM English"),
        R("mass", "Wednesday", "08:30", "English", "[St. Paschal Baylon] Wednesday 8:30 AM English"),
        R("mass", "Wednesday", "18:30", "English", "[St. Lawrence O’Toole] Wednesday 6:30 PM English"),
        R("mass", "First Saturday of the month", "10:00", "English", "[St. Paschal Baylon] 1st Saturday 10:00 AM English, monthly Anointing Mass"),
        R("confession", "Wednesday", "19:00-20:30", None, "[St. Lawrence O’Toole] Wednesday 7:00 PM to 8:30 PM at St. Lawrence church"),
        R("confession", "Saturday", "15:00-15:55", None, "[St. Paschal Baylon] Saturday 3:00 PM to 3:55 PM at St. Paschal church"),
        R("adoration", "Wednesday", "19:00-20:30", None, "[St. Lawrence O’Toole] Adoration Wednesday 7:00 PM to 8:30 PM at St. Lawrence church")])],
    "cornelius-richmond": [(("https://www.stcorneliusrichmond.org/", "20250424180507"), [
        R("mass", "Sunday", "07:00", "Spanish", "Sunday 7:00 AM Spanish/Español"),
        R("mass", "Sunday", "08:30", None, "Sunday 8:30 AM English/Inglés"),
        R("mass", "Sunday", "10:00", "Spanish", "Sunday 10:00 AM Spanish/Español"),
        R("mass", "Sunday", "12:00", "Spanish", "Sunday 12:00 PM Spanish/Español"),
        R("mass", "Sunday", "14:00", "Spanish", "Sunday 2:00 PM Spanish/Español"),
        R("mass", "Sunday", "18:00", "Spanish", "Sunday 6:00 PM Spanish/Español"),
        R("mass", "Saturday", "18:00", "Spanish", "Saturday 6:00 PM Spanish/Español"),
        R("mass", "Monday", "08:30", None, "Monday & Wednesday 8:30 AM (English/Inglés inside Chapel)"),
        R("mass", "Wednesday", "08:30", None, "Monday & Wednesday 8:30 AM (English/Inglés inside Chapel)"),
        *[R("mass", d, "08:30", "Spanish", "Monday- Saturday 8:30 AM Spanish/Español")
          for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")],
        R("confession", "Saturday", "15:00", None, "Confesiones/Confessions: Saturdays from 3-4 PM inside the church")])],
    "rose-lima-patrick-crockett": [(("https://www.strosecrockett.org/", "20260426215423"), [
        # footer "Mass Schedules" (dropped by visible_lines with the footer); same as the crawled-2026-10-30 rows
        R("mass", "Tuesday–Friday", "11:00", None, "[St. Rose] Daily Mass 11:00AM at St Rose : Tuesday thru Friday"),
        R("mass", "Saturday", "16:30", None, "[St. Rose] Saturday Vigil Mass 4:30 PM at St Rose"),
        R("mass", "Sunday", "09:00", None, "[St. Rose] Sunday Masses 9:00 AM at St Rose"),
        R("mass", "Sunday", "11:00", None, "[St. Patrick Mission] Sunday Masses 11:00 AM at St Patrick Mission")])],
    "our-lady-lourdes-oakland": [(("https://lourdesoakland.com/", "20250324025339"), [
        # "Weekly Schedule: In-Person and Online" table: a day/time row over a label row (read by column).
        # Thursday 8:00 am is a Communion Service, not a Mass. The domain later went to a gambling site (by 2025-08-27).
        R("mass", "Sunday", "10:00", None, "Sunday 10:00 am Mass – Livestream"),
        R("mass", "Monday, Wednesday, Friday", "08:00", None, "Monday, Wednesday, Friday 8:00 am Mass (Thursday 8:00 am: Communion Service)"),
        R("mass", "Saturday", "16:00", None, "Saturday 4:00 Mass (after Silent Meditation 3:00 – 4:00 pm)")])],
}

# entity -> list of (page suffix, capture timestamp, [group names as the page heads them], {name: meets})
GROUPS = {
    "good-shepherd-pittsburg": [
        ("/CGS/liturgicalministries.html", "20260518184443",
         ["Acolytes (Altar Servers)", "Arts and Environment", "Lectors", "Extraordinary Ministers of Holy Communion",
          "Sacristans", "St. Martha Guild", "Ushers"], {}),
        ("/CGS/devotionsandprayersministries.html", "20260518182112",
         ["Saint Padre Pio Prayer Group", "Divine Mercy Ministry", "Santo Niño Prayer Ministry",
          "Book of the Sick and Special Needs Prayer Group"], {"Saint Padre Pio Prayer Group": "St. Padre Pio novena in the chapel every Thursday at 6:30 pm"}),
        ("/CGS/serviceandoutreach.html", "20260518184937", ["Gabriel Project"], {}),
        ("/CGS/socialjustice.html", "20260518183221", ["St. Vincent de Paul Society", "Senior Brown Bag Program", "Shepherd's Closet"], {}),
        ("/CGS/ministeriohispano.html", "20260420222317",
         ["Sagrada Comunión a los Enfermos", "Movimiento Familiar Cristiano Católico", "Grupo de Biblia San Jerónimo",
          "Parejas para Cristo (Couples for Christ)", "Virgen de Guadalupe / Virgen Viajera", "Catequesis"],
         {"Virgen de Guadalupe / Virgen Viajera": "Second Friday of each month at 7:00 pm"}),
        ("/CGS/faithformation.html", "20260518181154",
         ["Children's Liturgy", "Children with Special Needs", "EDGE Middle School", "High School Confirmation",
          "Order of Christian Initiation for Children (OCIC)", "Thursday Night Bible Study Group"], {}),
        ("/CGS/communitylife.html", "20260211122605",
         ["Catholic Daughters of the Americas", "Catholic Youth Organization (CYO)", "Missionary Families for Christ",
          "Eucharistic Ministers to the Homebound", "Filipino Ministry", "Grief Ministry", "Iron Sharpens Iron",
          "Italian Catholic Federation", "Knights of Columbus Council 2956", "Young Ladies' Institute Teresita #123",
          "Young Men's Institute Sadoc Council #59", "Hospitality Ministry", "Cursillo Movement",
          "Building & Grounds Ministry", "Nigerian Community", "Tongan Community", "Young Adult Ministry"], {}),
        ("/CGS/musicministries.html", "20260420221749", ["Music Ministry"],
         {"Music Ministry": "Choir rehearsals: 10:00 AM choir Wednesdays 7:00 PM (church); 8:00 AM and Noon choirs Thursdays 7:00 PM "
                            "(Ministry Building); Spanish choirs Sundays 2:00 PM (church)"}),
        ("/CGS/pastoralcommmittees.html", "20260518185152", ["Pastoral Council", "Pastoral Finance Committee", "Liturgical Committee"], {}),
    ],
    "divine-mercy-oakland": [
        ("divinemercyoak.org/ministries", "20260612043950",
         ["Altar Servers", "Altar Society", "Art and Environment", "Eucharistic Ministers", "Greeters and Ushers",
          "Homebound Ministry", "Hospitality Ministry", "Lay Presiders", "Lectors", "Music Ministry", "Sacristans",
          "Adult Faith Formation", "Sacramental Faith Formation", "Cursillo", "Faith in Action East Bay",
          "Fellowship of Catholic Christian Women (FCCW)", "Fraternidad Católica Cristiana de Mujeres (FCCM)",
          "Funeral and Grief Ministry", "Knights of Columbus", "OCIA", "St. Vincent de Paul", "Social Justice Committee",
          "Youth Ministry"], {}),
        ("divinemercyoak.org/prayer-team", "20260612051824", ["Prayer Team"], {}),
    ],
    "perpetua-lafayette": [
        ("stperpetua.org/liturgical-support", "20250424173510",
         ["Art & Environment Committee", "Bible Study", "Centering Prayer", "Collection Counters",
          "Helping Hands Funeral Ministry", "Ministers to the Sick & Homebound"], {}),
        ("stperpetua.org/outreach", "20250324123020", ["Outreach Committee"],
         {"Outreach Committee": "1st Tuesday of each month, 7:30 pm, parish office"}),
        ("stperpetua.org/social-justice", "20251017082636", ["Social Justice Committee"],
         {"Social Justice Committee": "3rd Thursday of the month, 7:00 pm, parish office"}),
        ("stperpetua.org/womens-ministry", "20250324134823", ["Women's Ministry"], {}),
        ("stperpetua.org/music-ministries", "20250424161335", ["Parish Choir", "Cantors", "Instrumentalists"], {}),
        ("stperpetua.org/high-school-youth-ministry", "20250621235459", ["High School Youth Ministry"], {}),
        ("stperpetua.org/respect-life-ministry", "20250514202414", ["Respect Life Ministry"], {}),
        ("stperpetua.org/the-edge-grades-6-8", "20251015203840", ["TEAM Middle School Youth Ministry"],
         {"TEAM Middle School Youth Ministry": "Sundays 6:00-7:15 pm"}),
        ("stperpetua.org/becoming-catholic-ocia", "20250520164145", ["OCIA (Order of Christian Initiation of Adults)"], {}),
        ("stperpetua.org/communication-community-building-and-fellowship", "20250621234858",
         ["Oktoberfest Carnival Committee", "Coffee & Donut Service", "CYO Athletics", "Knights of Columbus",
          "Seniors' Activities Group"], {}),
        ("stperpetua.org/liturgical-ministries", "20240530021844",
         ["Altar Care", "Altar Servers", "Children's Liturgy of the Word (CLOW)", "Eucharistic Ministers",
          "Greeters and Ushers", "Lectors", "Liturgy Committee"], {}),
        ("stperpetua.org/parish-organizations", "20240225004754",
         ["Prayer Network Ministry", "Children's Choir", "Small Christian Communities", "Baby Sitting Co-op",
          "Welcoming Committee", "Kitchen Ministry"], {}),
    ],
    "christ-light-oakland": [
        ("ctlcathedral.org/ministry-to-the-sick-and-homebound", "20260527025949", ["Ministry to the Sick and Homebound"], {}),
        ("ctlcathedral.org/liturgical-ministries", "20221004142741",
         ["Altar Servers", "Altar Society", "Lectors", "Extraordinary Ministers of Holy Communion",
          "Children's Liturgy of the Word", "Art & Environment", "Hospitality After Mass", "Greeters and Ushers",
          "Music Ministry"], {}),
        ("ctlcathedral.org/cathedral-ministries", "20221004140405", ["Points of Light", "Bearers of Light", "Behind the Scenes"], {}),
        ("ctlcathedral.org/social-outreach-ministries", "20221004123218",
         ["Pope Francis Legal Clinic", "Knights of Columbus", "St. Vincent de Paul Society, Christ the Light Conference"], {}),
        ("ctlcathedral.org/music", "20220119161004",
         ["Men's Schola", "Choir of Men & Boys", "Cathedral Camerata", "St. Hildegard Schola", "Vietnamese Choir",
          "Filipino Choir", "Hispanic Music Ensemble"], {}),
        ("ctlcathedral.org/young-adult-ministry", "20220119161814", ["Young Adult Ministry"], {}),
    ],
    "our-lady-lourdes-oakland": [
        ("lourdesoakland.com/", "20250324025339",  # site menu "Ministries" + the weekly schedule table
         ["Bible Study Ministry", "Blessing Bags Ministry", "Eucharist Bread Ministry", "Hospitality Ministry",
          "Music Ministry", "Ministry to the Sick and Homebound", "Young Adults", "Saint Vincent de Paul", "Silent Meditation Group"],
         {"Bible Study Ministry": "Wednesdays 7:00 pm", "Silent Meditation Group": "Saturdays 3:00-4:00 pm, before the 4:00 Mass"}),
    ],
    "anthony-mary-help-christians-oakland": [
        ("stanthonymaryhelp.org/communities", "20250325120405", ["Cộng Đoàn Việt Nam (Vietnamese Community)", "Latino Community"], {}),
    ],
    "raymond-dublin": [
        ("st-raymond-dublin.org/", "20200611121338",  # site menu "Ministries"; the domain is parked from 2023 on
         ["Finance Committee", "Facilities Committee", "Social Justice Committee", "Catholic Daughters of the Americas",
          "WINGS", "Women's Guild", "Kairos", "Joblink", "Cursillo Community", "St. Vincent de Paul", "Homebound Ministry",
          "Grief Support Ministry", "Supportive Hands Ministry", "Welcoming Committee", "Band of Brothers",
          "Tri-Valley Catholic Young Adult Ministry", "Altar Servers", "Eucharistic Ministers", "Music Ministry", "Lectors",
          "Ministers of Hospitality", "Hispanic Community", "Filipino Community", "CityServe of the Tri-Valley",
          "Middle School Ministry", "High School Ministry", "Adult Faith Formation (RCIA)", "Food Bank"],
         {"Food Bank": "Distribution 3rd Saturday of the month, 8-11 am, at the church"}),
    ],
    "anthony-oakley": [
        ("sites.google.com/site/stanthonyoakley94561/", "20150123123252",  # the parish site stanthonyoakley.org framed
         ["Columbiettes", "Finance Committee", "Liturgy Committee", "Parish Council", "Grief Ministry",
          "Social Justice Ministry", "Pray for Priests", "Small Communities", "Youth Confirmation", "RCIA"], {}),
    ],
}

# Names written differently from the page, so the fixed taxonomy can read them (page wording -> name used)
RENAMED = {"good-shepherd-pittsburg": {"Hospitality Ministries": "Hospitality Ministry", "The Cursillo Movement": "Cursillo Movement",
                                       "Parejas para Cristo": "Parejas para Cristo (Couples for Christ)"},
           "our-lady-lourdes-oakland": {"Silent Meditation": "Silent Meditation Group"}}

# What the last good capture shows for the dead / hijacked / parked domains, and other per-parish notes.
NOTES = {
    "christ-light-oakland": "Home page captured 2026-06-16 (same schedule as /mass, 2026-05-27). No Saturday vigil (the blog notes the 5:00 pm Mass was suspended). The weekly bulletin is a Flocknote note ('CTL Weekly'); the eCatholic bulletin archive stops at 2020-10.",
    "leo-great-oakland": "Parish page on ctlcathedral.org/st-leo, captured 2026-04-19; agrees with DiscoverMass on every time. stleothegreat.org is the school's site.",
    "mark-richmond": "Home page captured 2026-02-13 (Spanish) and the English home page: Sunday 8:00, 9:30, 12:30 (Spanish) and 11:00 (English); weekday Mass Wed and Fri 8:30 (Spanish); confession by reservation (Wednesday afternoons). DiscoverMass disagrees on almost every time. /jf (bulletin) and /join-our-groups were never captured.",
    "perpetua-lafayette": "Mass-times page captured 2025-04-24 (newest in the archive). The bulletin ('The Perpetuan') is emailed, not posted; the eCatholic bulletin archive stops at 2018.",
    "anthony-mary-help-christians-oakland": "Mass list from the Vietnamese community page (2025-05-18), confession from /confession-times (2025-04-18); the parish's own Mass-times page was last captured 2024-05-27. Its 2024 Spanish page gives Wednesday 8:05 where the 2025 page gives Tuesday 8:05. Bulletins on files.ecatholic.com (site 34018) stop at 2024-06-16; the 2026-11-01 file is not there (403). stanthony-maryhelp.org (the directory's spelling) was a parked page in every capture (2021-2024).",
    "good-shepherd-pittsburg": "Not dead: the site moved to goodshepherdpittsburg.org/CGS/ (root last captured 2020-02-24); /CGS/ pages were captured up to 2026-08-05. Its Bulletins page (2025-11-17) points to Parishes Online, and its own bulletin files are named 05-1955 (LPi job number), so the current LPi bulletins were read: 2026-10-25 and 2026-11-01. Times come from the 2026-11-01 bulletin; the 2026-08-05 capture agrees on Mass and confession and lists the chapel's open hours (Mon-Fri 7 am-8 pm) rather than adoration times.",
    "divine-mercy-oakland": "TLS fails on the live site, but the archive has it to 2026-07-11. /mass (2026-06-12) agrees with the hand-checked weekend Masses and adds weekday Masses, confession and adoration. Mass rows are not loaded (curated parish); confession and adoration rows are loaded but the app shows only the curated rows at a parish that has them. Bulletins: one 2018 eCatholic file.",
    "raymond-dublin": "Last good capture 2020-06-11 (site menu and news); 2023-02-14 is a parked page (park.js), 2024-02-04 a provider error, 2024-06-27 to 2025-04-24 a parked 'Related Searches' page. No capture usable for times (2018 Mass page only). Groups from the 2020 menu.",
    "anthony-oakley": "stanthonyoakley.org framed the parish's Google Site (sites.google.com/site/stanthonyoakley94561) in 2016-2018; from 2019-12-13 the domain serves an unrelated blog. Last good capture: the Google Site, 2015-01-23. No times (too old); groups from its 2015 menu.",
    "cornelius-richmond": "Home page captured 2025-04-24, the last before the domain stopped resolving. Agrees with DiscoverMass on all Sunday and Saturday Masses; adds Spanish 8:30 am Mass on Monday and Wednesday (with the English one in the chapel). Confession Saturday 3-4 pm only (DiscoverMass also lists Friday 6 pm, from the 2023 page). Adoration 'every Friday' with no time. Ministries and bulletin pages are scripts the archive did not render.",
    "our-lady-lourdes-oakland": "Last parish capture 2025-03-24 (also 2025-02-03, with a pastoral leaders' letter about the parish's future, January 2025); by 2025-08-27 lourdesoakland.com is an Indonesian gambling site. Ministry pages from 2023-2024 are cut at 1 MiB in the archive (titles only). DiscoverMass (Sun 9:00 and 11:00, Sat 5:00 pm, weekdays Mon-Fri) disagrees with the parish's own 2025 schedule (Sun 10:00, Sat 4:00 pm, Mon/Wed/Fri 8:00).",
    "rose-lima-patrick-crockett": "Home page captured 2026-04-26: 'Mass Schedules' footer agrees exactly with the crawled-2026-10-30 rows (Sat 4:30 pm, Sun 9:00 at St. Rose, Sun 11:00 at St. Patrick, Tue-Fri 11:00); confession by appointment. Nothing new to load. The home page prints prayer lists of the sick and deceased; nothing from them is stored.",
    "bernard-oakland": "No parish website. The only archived page is the 2015 oakdiocese.org directory entry (its menu is the diocese's, not the parish's); the current oakdiocese.org/bios page was never captured. Nothing loaded (the parish has hand-checked times).",
}


def norm_days(d: str | None) -> list[str]:
    """Day text -> comparable tokens (weekday names, or the text itself for monthly rules)."""
    W = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if not d:
        return ["?"]
    s = d.replace("- ", "–").replace("-", "–")
    if "of the month" in s:
        return [s.lower()]
    out = []
    for part in re.split(r",\s*| and ", s):
        m = re.match(r"(\w+)\s*–\s*(\w+)", part.strip())
        if m and m.group(1) in W and m.group(2) in W:
            i, j = W.index(m.group(1)), W.index(m.group(2))
            out += W[i:j + 1]
        elif part.strip() in W:
            out.append(part.strip())
        else:
            out.append(part.strip().lower())
    return out


def start(t: str | None) -> str:
    return (t or "").split("-")[0]


def expand(rows):
    return {(r["kind"], d, start(r["time"])) for r in rows for d in norm_days(r["day"])}


def manifest_index():
    man = json.loads(MANIFEST.read_text())["captures"]
    by = {}
    for v in man.values():
        if v.get("capture_timestamp") and v.get("status") == 200:
            by[(v["entity_id"], v["capture_timestamp"])] = by.get((v["entity_id"], v["capture_timestamp"]), []) + [v]
    return man, by


def find_capture(by, eid, suffix, ts):
    for v in by.get((eid, ts), []):
        if v["original"].rstrip("/").lower().endswith(suffix.rstrip("/").lower()):
            return v
    raise SystemExit(f"capture not in manifest: {eid} {suffix} {ts}")


def archive_url(v) -> str:
    return f"https://web.archive.org/web/{v['capture_timestamp']}/{v['original']}"


def parser_rows(v) -> list[dict]:
    """The rule-based reading (parish_sites + crawled_mass_schedule filters), kept as a comparison only."""
    from bs4 import BeautifulSoup  # noqa: PLC0415
    import parish_sites as ps  # noqa: PLC0415
    from crawled_mass_schedule import rows_from_record  # noqa: PLC0415
    p = RAW / "wayback" / v["path"]
    if not p.exists() or not p.name.endswith(".html"):
        return []
    soup = BeautifulSoup(p.read_text(errors="ignore"), "html.parser")
    block = ps.mass_block(ps.visible_lines(soup))
    rec = {"id": v["entity_id"], "website": v["original"],
           "mass_schedule": {"raw": block or "", "parsed": ps.parse_mass(block), "source_url": archive_url(v)}}
    return rows_from_record(rec, "parser")


def merge_recovery() -> int:
    """Fold this pass into data/real/recovery_2026-10-31.json (per parish 'wayback' block, times, groups, note)."""
    rec_p = REAL / "recovery_2026-10-31.json"
    rec = json.loads(rec_p.read_text())
    wb = json.loads(OUT.read_text())
    man, _ = manifest_index()
    for eid, w in wb["report"].items():
        r = rec["report"][eid]
        ok = [v for v in man.values() if v["entity_id"] == eid and v.get("status") == 200]
        times_caps = sorted({k.split(":")[0] for k in w["times"]})
        prev = r.get("wayback", {})
        r["wayback"] = {
            "content_read": True, "read_on": "2026-10-31", "read_from": "Matthew's laptop (ingest/public/wayback_home.py)",
            "captures_read": len(ok), "newest_capture_read": max((v["capture_date"] for v in ok), default=None),
            "used_for_times": times_caps,
            "times_added": w["times_added"], "groups_added": len(w["groups_added"]),
            "what_the_last_good_capture_shows": w["note"],
            "captures": w["captures_read"], "not_captured": w["captures_missing"],
            "availability_api_2026-10-31": {k: prev.get(k) for k in ("latest_capture", "captures_found", "history")}}
        r["times"]["wayback"] = w["times"]
        r["times"]["rows_added"] = (r["times"].get("rows_added") or 0) + sum(w["times_added"].values())
        r["groups_added_from_wayback"] = [g["group_id"] for g in w["groups_added"]]
        r["groups_added"] = (r.get("groups_added") or 0) + len(w["groups_added"])
        if eid == "good-shepherd-pittsburg":
            r["bulletins"] = r.get("bulletins", []) + [
                {"date": d, "url": f"https://container.parishesonline.com/bulletins/05/1955/{d.replace('-', '')}B.pdf", "host": "lpi",
                 "found_via": "LPi code 05-1955 from the parish site's own bulletin file names (Wayback captures)"}
                for d in ("2026-11-01", "2026-10-25")]
        r["note"] = (r.get("note") or "").split(" | Wayback 2026-10-31: ")[0] + " | Wayback 2026-10-31: " + w["note"]
    rec["method"]["wayback"] = (
        "Side-door pass: web.archive.org unreachable from the sandbox, so only Availability-API dates were recorded. "
        "Then (same day) ingest/public/wayback_home.py (stdlib) ran on Matthew's laptop: CDX listings (filter=statuscode:200, "
        "read newest first) and id_ captures at a 4 s pace; ingest/public/wayback_build.py read them. Times read by hand from "
        "captures dated 2025-01-01 or later; groups from any capture, dated. See data/real/wayback_2026-10-31.json.")
    rec["loaded"]["wayback_2026-10-31"] = {"mass_schedule": wb["totals"]["mass_schedule_rows"], "by_kind": wb["totals"]["by_kind"],
                                           "parishes_with_rows": wb["totals"]["parishes_with_rows"],
                                           "ministry": wb["totals"]["groups"], "parishes_with_groups": wb["totals"]["parishes_with_groups"],
                                           "bulletin_index": {"added": 2, "parish": "good-shepherd-pittsburg", "host": "lpi"}}
    rec["generated_at"] = now_iso()
    save_json(rec_p, rec)
    print("merged into", rec_p)
    return 0


def main() -> int:
    if "--merge-recovery" in sys.argv:
        return merge_recovery()
    import build_groups as bg  # noqa: PLC0415
    from load import display, nkey, stem  # noqa: PLC0415
    from load_sql import COLS, lit  # noqa: PLC0415
    from privacy import scrub_schedule_line  # noqa: PLC0415
    ap = argparse.ArgumentParser()
    ap.add_argument("--have-json", required=True, help="[{entity_id, source, kind, day, time, language}] for the 13 parishes")
    ap.add_argument("--groups-existing", required=True, help="[{entity_id, name, group_id}] for the 13 parishes")
    a = ap.parse_args()
    have = defaultdict(list)
    for r in json.loads(Path(a.have_json).read_text()):
        have[r["entity_id"]].append(r)
    gex = json.loads(Path(a.groups_existing).read_text())
    have_gid = {stem(x["group_id"]) for x in gex if x.get("group_id")}
    have_name = {(x["entity_id"], nkey(x["name"])) for x in gex}
    ents = {e["id"]: e for e in json.loads((ROOT / "data/seed/entities.json").read_text())["entities"]}
    man, by = manifest_index()
    targets = json.loads((ROOT / "data/real/recovery_2026-10-31.json").read_text())["report"]
    ids = list(NOTES)
    report, mass_rows, grp_rows, dropped = {}, [], {}, defaultdict(list)
    for eid in ids:
        cur = have.get(eid, [])
        caps = sorted([v for v in man.values() if v["entity_id"] == eid], key=lambda v: v.get("capture_timestamp") or "")
        rec = {"name": (ents.get(eid) or {}).get("name") or targets.get(eid, {}).get("name"), "note": NOTES[eid],
               "captures_read": [{k: v.get(k) for k in ("role", "original", "capture_date", "archive_url", "status")}
                                 for v in caps if v.get("status") == 200],
               "captures_missing": [{"original": v["original"], "requested": v["requested_timestamp"], "status": v["status"]}
                                    for v in caps if v.get("status") != 200],
               "times": {}, "groups_added": [], "groups_skipped": []}
        # ---------------------------------------------------------------- times
        added = []
        for cap, rows in REVIEWED.get(eid, []):
            if isinstance(cap, tuple):
                v = find_capture(by, eid, cap[0].split("://", 1)[1], cap[1])
                cdate, src, url = v["capture_date"], f"wayback-{v['capture_date']}", archive_url(v)
                parsed = parser_rows(v)
            else:
                cdate, src, url, parsed = "2026-11-01", "bulletin-lpi-2026-11-01", cap, []
            usable = cdate >= TIMES_CUTOFF
            curated_mass = any(r["source"].startswith("curated") and r["kind"] == "mass" for r in cur)
            for kind in ("mass", "confession", "adoration"):
                cand = [r for r in rows if r["kind"] == kind]
                if not cand:
                    continue
                ex = [r for r in cur if r["kind"] == kind]
                own = [r for r in ex if not r["source"].startswith("secondary-")]
                ck, ek, ok = expand(cand), expand(ex), expand(own)
                if not usable:
                    why, eligible = f"capture {cdate} is older than {TIMES_CUTOFF}", False
                elif kind == "mass" and curated_mass:
                    why, eligible = "parish has hand-checked Mass rows (curated wins)", False
                elif not own:
                    why, eligible = "no own-source rows of this kind (DiscoverMass only or none)", True
                elif ok <= ck:
                    why, eligible = "own-source rows of this kind all agree with the capture", True
                else:
                    why, eligible = "own-source rows of this kind disagree with the capture", False
                ins = []
                if eligible:
                    for r in cand:
                        dup = any(o["kind"] == kind and norm_days(o["day"]) == norm_days(r["day"]) and start(o["time"]) == start(r["time"])
                                  and (not r["language"] or r["language"] == "English" or o.get("language") == r["language"]) for o in own)
                        if dup:
                            continue
                        clean, _ = scrub_schedule_line(r["raw"])
                        row = {"entity_id": eid, "kind": kind, "day": r["day"], "time": r["time"], "language": r["language"],
                               "raw": clean[:200], "source": src, "source_url": url}
                        ins.append(row)
                rec["times"][f"{src}:{kind}"] = {
                    "capture_date": cdate, "source_url": url, "candidate_rows": len(cand),
                    "existing_sources": sorted({r["source"] for r in ex}),
                    "agree": sorted(f"{d} {t}" for k, d, t in ck & ek),
                    "only_in_capture": sorted(f"{d} {t}" for k, d, t in ck - ek),
                    "only_in_existing": sorted(f"{d} {t}" for k, d, t in ek - ck),
                    "eligible": eligible, "why": why, "rows_added": len(ins),
                    "parser_rows_for_comparison": sorted({f"{p['kind']} {p['day']} {p['time']}" for p in parsed if p["kind"] == kind})}
                added += ins
        mass_rows += added
        rec["times_added"] = dict(Counter(r["kind"] for r in added))
        # ---------------------------------------------------------------- groups
        ent = ents.get(eid) or {}
        for suffix, ts, names, meets in GROUPS.get(eid, []):
            v = find_capture(by, eid, suffix, ts)
            for nm in names:
                raw = bg.clean_name(bg.fix_mojibake(nm))
                res, why = bg.classify(raw)
                if res is None:
                    rec["groups_skipped"].append({"name": nm, "why": why})
                    dropped[why].append(f"{eid}: {nm}")
                    continue
                if bg.private_name_risk(raw):
                    rec["groups_skipped"].append({"name": nm, "why": "private_name_risk"})
                    continue
                key = "kofc" if res[2] == "knights_of_columbus" else res[0]
                gid = f"{eid}:{key}"
                name = display(raw, res)
                if gid in grp_rows or stem(gid) in have_gid or (eid, nkey(name)) in have_name or (eid, nkey(raw)) in have_name:
                    rec["groups_skipped"].append({"name": nm, "why": "already held (same group or name)"})
                    continue
                _, cat, sub, aud, lang, _, strength = res
                if not lang and "ministeriohispano" in suffix:  # Good Shepherd's Spanish-language ministries page
                    lang = "es"
                grp_rows[gid] = {
                    "group_id": gid, "entity_id": eid, "name": name, "source_url": archive_url(v), "scope": "parish",
                    "category": cat, "subcategory": sub, "audience": aud, "language": lang or "en",
                    "language_assumed": not lang, "meets": meets.get(nm),
                    "location": ", ".join(x for x in [ent.get("name"), ent.get("city")] if x),
                    "contact_role": "parish office", "description": None,
                    "join_how": ("Contact the parish office to ask when the next session starts and how to register."
                                 if cat == "formation" else
                                 "Ask the parish office or the ministry coordinator how to join and whether training is offered."
                                 if cat == "liturgical" else "Contact the parish office to ask when it meets."),
                    "source_kind": "website", "confidence": "low" if (strength != "specific" or v["capture_date"] < "2025-01-01") else "medium",
                    "last_seen": v["capture_date"], "fetched_at": v["fetched_at"],
                    "flags": [f"source:wayback-{v['capture_date']}"], "raw_name": raw}
                rec["groups_added"].append({"group_id": gid, "name": name, "capture_date": v["capture_date"]})
        report[eid] = rec
    save_json(OUT, {"generated_at": now_iso(), "generated_by": "ingest/public/wayback_build.py",
                    "method": __doc__.split("\n\n", 2)[2].strip(), "report": report,
                    "totals": {"mass_schedule_rows": len(mass_rows), "by_kind": dict(Counter(r["kind"] for r in mass_rows)),
                               "parishes_with_rows": len({r["entity_id"] for r in mass_rows}), "groups": len(grp_rows),
                               "parishes_with_groups": len({g["entity_id"] for g in grp_rows.values()})},
                    "groups_dropped_by_taxonomy": dict(dropped), "groups_renamed": RENAMED, "group_rows": list(grp_rows.values()), "mass_rows": mass_rows})

    def q(x):
        return "NULL" if x is None else "'" + str(x).replace("'", "''") + "'"
    vals = ",\n".join("(" + ",".join(q(r[c]) for c in ("entity_id", "day", "time", "language", "kind", "raw", "source",
                                                        "source_url")) + ")" for r in mass_rows)
    Path("/tmp/wayback_mass.sql").write_text(f"""with ins as (
insert into mass_schedule (entity_id, day, time, language, kind, raw, source, source_url)
select v.* from (values
{vals}) as v(entity_id, day, time, language, kind, raw, source, source_url)
where not exists (select 1 from mass_schedule m where m.entity_id = v.entity_id and m.source = v.source
                  and m.kind = v.kind and m.day is not distinct from v.day and m.time = v.time)
returning entity_id, kind)
select count(*) rows_inserted, count(distinct entity_id) parishes from ins;
""")
    recs = list(grp_rows.values())
    cols = ",".join(COLS)
    gvals = ",\n".join("(" + ",".join(lit(r[c], c, i == 0) for c in COLS) + ")" for i, r in enumerate(recs))
    Path("/tmp/wayback_groups.sql").write_text(f"""with v({cols}) as (values
{gvals})
, ins as (
  insert into ministry ({cols}) select {cols} from v
  where not exists (select 1 from ministry x where x.entity_id = v.entity_id and
                    regexp_replace(x.group_id, '_(en\\+es|es|vi|zh|ko|tl|pt|to|ig)$', '') =
                    regexp_replace(v.group_id, '_(en\\+es|es|vi|zh|ko|tl|pt|to|ig)$', ''))
    and not exists (select 1 from ministry x where x.entity_id = v.entity_id
                    and regexp_replace(lower(x.name), '[^a-z]', '', 'g') = regexp_replace(lower(v.name), '[^a-z]', '', 'g'))
  returning entity_id)
select count(*) inserted, count(distinct entity_id) parishes from ins;
""")
    print(f"mass rows {len(mass_rows)} {dict(Counter(r['entity_id'] for r in mass_rows))}")
    print(f"groups {len(recs)} {dict(Counter(g['entity_id'] for g in recs))}")
    print("dropped by taxonomy:", {k: len(v) for k, v in dropped.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
