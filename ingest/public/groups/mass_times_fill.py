"""Mass / confession / adoration times for listed parishes and missions that the public Find page showed as
"See the parish website for times" or with only fragmentary rows (fill of 2026-10-30, Pacific date).

    python3 -I ingest/public/groups/mass_times_fill.py
        -> data/real/parish_sites/mass_times_fill_2026-10-30.json  and  /tmp/mass_times_fill.sql

Every row below was transcribed from a page fetched on 2026-10-30 (Pacific; fetched_at per source, UTC), or for two
St. Anne (Union City) rows from the residential crawl of 2026-10-29 (data/real/parish_sites.json). Nothing is inferred:
lines that are relative ("after the 9 AM Mass"), unreadable, contradictory, or evidently mistyped are left out and noted.

Sources, in order of preference:
  1. the parish's own website (plain curl, default User-Agent, from this sandbox or the owner's home connection).
     Sites behind a Cloudflare "Just a moment..." challenge were recorded as unreadable, never bypassed.
  2. discovermass.com church pages (robots.txt: Crawl-delay 10, honoured). Their schedules carry the note
     "Powered by MassTimes.org" and still show a COVID-19 banner, so their age is unknown. masstimes.org itself is a
     client-side app: a plain fetch returns no schedule text, so it was not used directly.

Row shape is the same as ingest/public/groups/crawled_mass_schedule.py (entity_id, day, time = start "HH:MM",
language, kind, raw, source, source_url). Labels:
  source = 'crawled-2026-10-30'        own parish website, read 2026-10-30, unverified
  source = 'crawled-2026-10-29'        own parish website, residential crawl of 2026-10-29, unverified
  source = 'secondary-discovermass-2026-10-30'   secondary source (DiscoverMass / MassTimes.org data), unverified
Multi-site parishes put the church in raw as "[Site] ..." (lib/schedule.ts splitSite).
INSERT only, and only rows not already present (same entity_id, day, time, kind, language); never updates or deletes.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/real/parish_sites/mass_times_fill_2026-10-30.json"
OWN, OWN29, SEC = "crawled-2026-10-30", "crawled-2026-10-29", "secondary-discovermass-2026-10-30"
DM = "https://discovermass.com/church/{}/"
DMNOTE = "secondary source: DiscoverMass.com (MassTimes.org data), age unknown"

MF = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def dm_days(days, time, kind="mass", lang=None, raw=None):
    return [(kind, d, time, lang, raw) for d in days]


# Each parish: outcome, sources [{url, kind: own|secondary, fetched_at, status}], rows [(kind, day, time, lang, raw)]
# per source key, notes, discrepancies.
P: dict[str, dict] = {}

# ---------------------------------------------------------------- the 15 active parishes with no rows

P["good-shepherd-pittsburg"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://goodshepherdpittsburg.org", "status": "unreadable: TLS handshake fails (sandbox and home connection)"},
    sec={"url": DM.format("good-shepherd-parish-pittsburg-ca"), "fetched_at": "2026-10-31T05:07:45Z"},
    sec_rows=[("mass", "Saturday", "08:00", None, None), ("mass", "Saturday", "17:30", None, None),
              ("mass", "Sunday", "08:00", None, None), ("mass", "Sunday", "10:00", None, None),
              ("mass", "Sunday", "12:00", None, None), ("mass", "Monday–Friday", "07:00", None, None)],
)
P["our-lady-lourdes-oakland"] = dict(
    outcome="found (secondary source only)",
    own={"url": "http://www.lourdesoakland.com/", "status": "unreadable: domain now redirects to an unrelated (gambling) site; the diocesan directory lists no website"},
    sec={"url": DM.format("our-lady-of-lourdes-oakland-ca"), "fetched_at": "2026-10-31T05:07:56Z"},
    sec_rows=[("mass", "Saturday", "17:00", None, None), ("mass", "Sunday", "09:00", None, None),
              ("mass", "Sunday", "11:00", None, None), ("mass", "Monday–Friday", "08:00", None, None),
              ("confession", "Saturday", "15:30", None, "Confessions Sat 3:30pm-4:30pm, or by appointment")],
)
P["our-lady-mercy-richmond"] = dict(
    outcome="found (parish website)",
    own={"url": "https://pointrichmondcatholic.org/", "fetched_at": "2026-10-31T05:02:27Z"},
    own_rows=[("mass", "Saturday", "16:30", None, "Saturday Liturgy 4:30PM; Saturday Mass happens weekly at 4:30pm"),
              ("mass", "Sunday", "10:00", None, "Sunday Mass 10:00AM – 11:00AM")],
    sec={"url": DM.format("our-lady-of-mercy-point-richmond-ca"), "fetched_at": "2026-10-31T05:08:07Z"},
    notes=["Own site: 'Confession services after mass on Saturdays' (no start time) - no confession row."],
    discrepancies=["DiscoverMass lists confession Sat 4:00-4:30pm (before Mass); the parish site says after the Saturday Mass. Parish site kept; no confession row inserted."],
)
P["anne-union-city"] = dict(
    outcome="found (secondary source + parish website for First Friday)",
    own={"url": "https://saintannecatholic.org/mass-schedule", "status": "unreadable 2026-10-30: Cloudflare challenge (sandbox and home connection); 2026-10-29 residential crawl text used",
         "fetched_at": "2026-10-29T16:40:59Z"},
    own_rows=[("mass", "First Friday of the month", "19:00", None, "Benediction and reposition at 6:45 pm followed by Mass at 7:00 pm")],
    own_source=OWN29,
    sec={"url": DM.format("st-anne-union-city-ca"), "fetched_at": "2026-10-31T05:08:18Z"},
    sec_rows=[("mass", "Saturday", "08:00", None, None), ("mass", "Saturday", "17:00", None, None),
              ("mass", "Sunday", "08:00", None, None), ("mass", "Sunday", "10:00", None, None),
              ("mass", "Sunday", "12:00", None, None), ("mass", "Sunday", "18:00", None, None),
              ("mass", "Monday–Friday", "08:00", None, None),
              ("confession", "Saturday", "15:30", None, "Confessions Sat 3:30pm-4:30pm")],
    notes=["Own site (2026-10-29): Friday Exposition starts 'following the end of 8:00 AM Mass' - relative start, no adoration row."],
    discrepancies=["DiscoverMass: First Friday Mass 7:30pm; parish site: 7:00 pm. Parish site kept, DiscoverMass row dropped."],
)
P["anthony-mary-help-christians-oakland"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://stanthonymaryhelp.org/", "status": "unreadable: Cloudflare challenge (sandbox and home connection); the directory's stanthony-maryhelp.org is parked (/lander redirect)"},
    sec={"url": DM.format("st-anthonys-oakland-ca") + " ; " + DM.format("st-anthony-mary-help-of-christians-parish-oakland-ca"),
         "fetched_at": "2026-10-31T05:11:37Z"},
    sec_rows=[("mass", "Saturday", "16:30", None, "[St. Anthony] Saturday 4:30pm"), ("mass", "Saturday", "19:30", None, "[St. Anthony] Saturday 7:30pm"),
              ("mass", "Sunday", "08:00", None, "[St. Anthony] Sunday 8:00am"), ("mass", "Sunday", "09:30", None, "[St. Anthony] Sunday 9:30am"),
              ("mass", "Sunday", "11:30", None, "[St. Anthony] Sunday 11:30am"), ("mass", "Monday–Friday", "08:00", None, "[St. Anthony] Monday–Friday 8:00am"),
              ("mass", "Sunday", "09:00", None, "[Mary Help of Christians] Sunday 9:00am"),
              ("mass", "Sunday", "11:45", None, "[Mary Help of Christians] Sunday 11:45am")],
    sec_url_by_site={"St. Anthony": DM.format("st-anthonys-oakland-ca"),
                     "Mary Help of Christians": DM.format("st-anthony-mary-help-of-christians-parish-oakland-ca")},
)
P["anthony-oakley"] = dict(
    outcome="no readable source",
    own={"url": "https://stanthonyoakley.org", "status": "unreadable: domain does not resolve (sandbox and home connection)"},
    sec={"url": None, "status": "not listed on discovermass.com (sitemap checked); masstimes.org is client-rendered, no schedule in a plain fetch"},
)
P["augustine-oakland"] = dict(
    outcome="found (parish website)",
    own={"url": "https://staugustineoakland.com/mass-schedule/", "fetched_at": "2026-10-31T05:02:50Z"},
    own_rows=[("confession", "Saturday", "16:00", None, "Confession/Sacrament of Reconciliation Saturdays at 4:00 p.m."),
              ("mass", "Saturday", "17:00", None, "Saturday Vigil Mass at 5:00 p.m."),
              ("mass", "Sunday", "10:00", None, "Sunday Masses at 10:00 a.m. and 6:00 p.m."),
              ("mass", "Sunday", "18:00", None, "Sunday Masses at 10:00 a.m. and 6:00 p.m."),
              ("mass", "Thursday", "08:30", None, "Weekday Mass on Thursdays at 8:30 a.m.")],
    sec={"url": DM.format("st-augustine-oakland-ca"), "fetched_at": "2026-10-31T05:08:39Z"},
    notes=["Page also lists St. Joseph the Worker and St. Ambrose (separate parishes in the cluster); only the St. Augustine block used.",
           "DiscoverMass agrees."],
)
P["benedict-oakland"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://www.saintbenedictoakland.com/mass-times", "status": "unreadable: Cloudflare challenge (sandbox and home connection)"},
    sec={"url": DM.format("st-benedict-oakland-ca"), "fetched_at": "2026-10-31T05:08:50Z"},
    sec_rows=[("mass", "Saturday", "16:00", None, None), ("mass", "Sunday", "10:00", None, None)],
)
P["cornelius-richmond"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://www.stcorneliusrichmond.org/", "status": "unreadable: domain does not resolve (sandbox and home connection)"},
    sec={"url": DM.format("st-cornelius-richmond-ca"), "fetched_at": "2026-10-31T05:09:01Z"},
    sec_rows=[("mass", "Saturday", "08:30", "Spanish", None), ("mass", "Saturday", "18:00", "Spanish", None),
              ("mass", "Sunday", "07:00", "Spanish", None), ("mass", "Sunday", "08:30", None, None),
              ("mass", "Sunday", "10:00", "Spanish", None), ("mass", "Sunday", "12:00", "Spanish", None),
              ("mass", "Sunday", "14:00", "Spanish", None), ("mass", "Sunday", "18:00", "Spanish", None),
              ("mass", "Monday", "08:30", None, None), ("mass", "Tuesday", "08:30", "Spanish", None),
              ("mass", "Wednesday", "08:30", None, None), ("mass", "Thursday", "08:30", "Spanish", None),
              ("mass", "Friday", "08:30", "Spanish", None),
              ("adoration", "Friday", "09:00", None, "Adoration Fri 9:00am-8:00pm"),
              ("confession", "Saturday", "15:00", None, "Confessions Sat 3:00pm-4:00pm"),
              ("confession", "Friday", "18:00", None, "Confessions Fri 6:00pm-7:00pm (not every week)")],
)
P["jarlath-oakland"] = dict(
    outcome="found (parish website)",
    own={"url": "https://saintjarlath.org/", "fetched_at": "2026-10-31T05:02:28Z"},
    own_rows=[("mass", "Monday–Friday", "07:30", "English", "Weekday English Mass: 7:30 AM (Rosary Following)"),
              ("mass", "Tuesday, Friday", "18:30", "Spanish", "Weekday Spanish Mass: Tuesday & Friday 6:30 PM"),
              ("mass", "Saturday", "07:30", "English", "Saturdays English Mass: 7:30 AM & 5:00 PM"),
              ("mass", "Saturday", "17:00", "English", "Saturdays English Mass: 7:30 AM & 5:00 PM"),
              ("mass", "Sunday", "09:00", "English", "Sundays: 9:00 AM (English)"),
              ("mass", "Sunday", "11:00", "Spanish", "Sundays: 11:00 AM (Spanish)"),
              ("mass", "Sunday", "13:00", "Spanish", "Sundays: 1:00 PM (Spanish)"),
              ("confession", "Tuesday", "17:00", None, "Confession Tuesdays: 5:00 PM – 6:15 PM"),
              ("confession", "Friday", "17:00", None, "Confession Fridays: 5:00 PM – 6:15 PM"),
              ("confession", "Saturday", "15:45", None, "Confession Saturdays: 3:45PM – 4:45 PM"),
              ("adoration", "Tuesday", "08:00", None, "Adoration Tuesdays: 8:00 AM – 6:15 PM"),
              ("adoration", "Friday", "08:00", None, "Adoration Fridays: 8:00 AM – 6:15 PM"),
              ("adoration", "First Friday of the month (overnight)", "19:15", None, "First Friday of the month all night 7:15pm – 7:45am")],
    sec={"url": DM.format("st-jarlath-oakland-ca"), "fetched_at": "2026-10-31T05:09:12Z"},
    discrepancies=["The parish home page has two schedule blocks. The main block lists Sunday 7:45 am (Spanish) and French Mass 5:00 PM on the 'Second Sunday of the Month'; the footer block omits 7:45 and says 'First Sunday'. DiscoverMass: no 7:45, French on 2nd Sundays. Both rows left out until checked."],
)
P["leo-great-oakland"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://ctlcathedral.org/st-leo", "status": "unreadable: Cloudflare challenge (sandbox and home connection); stleothegreat.org (in the DB) is the parish school's site and lists no Mass times"},
    sec={"url": DM.format("st-leo-the-great-oakland-ca"), "fetched_at": "2026-10-31T05:09:23Z"},
    sec_rows=[("mass", "Saturday", "17:00", None, None), ("mass", "Sunday", "09:00", None, None),
              ("mass", "Tuesday", "09:00", None, None), ("mass", "Thursday", "09:00", None, None),
              ("confession", "Saturday", "16:30", None, "Confessions Sat 4:30pm-4:50pm")],
)
P["mark-richmond"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://stmarkrichmond.org", "status": "unreadable: Cloudflare challenge (sandbox and home connection)"},
    sec={"url": DM.format("st-mark-parish-richmond-ca"), "fetched_at": "2026-10-31T05:09:34Z"},
    sec_rows=[("mass", "Saturday", "08:00", None, None), ("mass", "Sunday", "07:45", None, None),
              ("mass", "Sunday", "09:15", None, None), ("mass", "Sunday", "11:00", None, None),
              ("mass", "Monday–Thursday", "08:00", None, None)],
    notes=["DiscoverMass also lists 'Sunday 12:30am' - evidently a typing error; left out."],
)
P["perpetua-lafayette"] = dict(
    outcome="found (secondary source; parish site partly agrees)",
    own={"url": "https://stperpetua.org/mass-times-directions", "status": "unreadable 2026-10-30: Cloudflare challenge; 2026-10-29 residential crawl mentions '9:00am Mass' Wed/Fri only",
         "fetched_at": "2026-10-29T16:36:54Z"},
    sec={"url": DM.format("st-perpetua-lafayette-ca"), "fetched_at": "2026-10-31T05:09:45Z"},
    sec_rows=[("mass", "First Saturday of the month", "09:00", None, "Saturday 9:00am - First Sat."),
              ("mass", "Saturday", "17:00", None, None), ("mass", "Sunday", "09:30", None, None),
              ("mass", "Sunday", "11:30", None, None), ("mass", "Monday, Wednesday, Friday", "09:00", None, None),
              ("adoration", "Friday", "09:30", None, "Adoration Fri 9:30am-2:30pm"),
              ("confession", "Saturday", "16:00", None, "Confessions Sat 4:00pm-5:00pm"),
              ("confession", "Sunday", "09:15", None, "Confessions Sun 9:15am-9:30am"),
              ("confession", "Sunday", "11:15", None, "Confessions Sun 11:15am-11:30am")],
    discrepancies=["DiscoverMass lists weekday confessions 8:45-9:00am (Mon, Tue, Wed, Fri); the parish page (2026-10-29) shows Wed/Fri 9:30 AM 'For 15 minutes after 9:00am Mass in the Chapel' under an unread heading. Weekday confession rows left out."],
)
P["peter-martyr-pittsburg"] = dict(
    outcome="found (parish website)",
    own={"url": "https://saintpetermartyr.org/horarios-contacto/", "fetched_at": "2026-10-31T05:10:56Z"},
    own_rows=[("mass", "Monday–Friday", "08:00", "Spanish", "Monday – Friday: 8:00am (Español)"),
              ("mass", "Saturday", "19:00", "Spanish", "Saturday Vigil / Sábados -Vigilia: 7:00pm (Español)"),
              ("mass", "Sunday", "07:00", "Spanish", "7:00am (Español)"), ("mass", "Sunday", "08:30", "English", "8:30am (English)"),
              ("mass", "Sunday", "10:00", "Spanish", "10:00am (Español)"), ("mass", "Sunday", "12:00", "Spanish", "12:00pm (Español)"),
              ("mass", "Sunday", "14:00", "Spanish", "2:00pm (Español)"), ("mass", "Sunday", "19:00", "Spanish", "7:00pm (Español)"),
              ("confession", "Thursday", "16:30", None, "Reconciliations / Reconciliaciones Thursdays / Jueves 4:30 PM to 6:30 PM")],
    own_url_by_kind={"confession": "https://saintpetermartyr.org/"},
    sec={"url": DM.format("st-peter-martyr-of-verona-pittsburg-ca"), "fetched_at": "2026-10-31T05:09:56Z"},
    notes=["The DB and diocesan directory list no website; saintpetermartyr.org is linked from the DiscoverMass listing and names the parish and address. DiscoverMass agrees."],
)
P["raymond-dublin"] = dict(
    outcome="found (secondary source only)",
    own={"url": "http://www.st-raymond-dublin.org", "status": "unreadable: certificate does not match the host over https; http returns a provider error page"},
    sec={"url": DM.format("st-raymond-dublin-ca"), "fetched_at": "2026-10-31T05:10:07Z"},
    sec_rows=[("mass", "Saturday", "08:30", None, None), ("mass", "Saturday", "17:00", None, None),
              ("mass", "Sunday", "07:30", None, None), ("mass", "Sunday", "09:00", None, None),
              ("mass", "Sunday", "10:30", None, None), ("mass", "Sunday", "12:00", None, None),
              ("mass", "Sunday", "13:30", None, None), ("mass", "Monday–Friday", "08:30", None, None),
              ("mass", "Monday, Tuesday, Wednesday, Friday", "12:15", None, None),
              ("confession", "Saturday", "15:30", None, "Confessions Sat 3:30pm-4:30pm")],
    notes=["DiscoverMass lists Thursday '12:15am' - evidently a typing error; Thursday 12:15 left out."],
)

# ---------------------------------------------------------------- the other places the Find page showed as 'none'

P["columba-oakland"] = dict(
    outcome="found (secondary source; parish calendar agrees on Sat 4 PM)",
    own={"url": "https://www.stcolumba-oak.com/mass-times", "status": "unreadable 2026-10-30: Cloudflare challenge; 2026-10-29 crawl only caught a calendar entry (Sat 4:00 PM Mass)"},
    sec={"url": DM.format("st-columba-oakland-ca"), "fetched_at": "2026-10-31T05:14:27Z"},
    sec_rows=[("mass", "Saturday", "16:00", None, None), ("mass", "Sunday", "08:00", None, None),
              ("mass", "Sunday", "10:30", None, None), ("mass", "Monday–Friday", "08:00", None, None)],
    notes=["Existing crawled confession row (Sat 3:30 PM) matches DiscoverMass; not duplicated."],
)
P["our-lady-guadalupe-fremont"] = dict(
    outcome="found (parish website - schedule image)",
    own={"url": "https://www.olog.church/uploads/8/8/4/5/8845951/mass_schedule_092026.png", "fetched_at": "2026-10-31T05:16:44Z",
         "page": "https://www.olog.church/mass-schedule.html"},
    own_rows=[("mass", "Monday–Saturday", "09:00", "English", "English: Mondays - Saturdays 9:00am"),
              ("mass", "Wednesday, Friday", "19:00", "Spanish", "Español: Wednesday & Fridays 7:00pm"),
              ("mass", "Saturday", "17:00", "English", "Saturday Vigil 5:00pm"),
              ("mass", "Saturday", "19:00", "Spanish", "Misa de vigilia del sábado: 7:00pm"),
              ("mass", "Sunday", "07:15", "English", "Sundays: 7:15am"),
              ("mass", "Sunday", "10:30", "English", "Sundays: 10:30am (ASL)"),
              ("mass", "Sunday", "18:00", "English", "Sundays: 6:00pm"),
              ("mass", "Sunday", "08:45", "Spanish", "Domingos: 8:45am"),
              ("mass", "Sunday", "12:30", "Spanish", "Domingos: 12:30pm")],
    sec={"url": DM.format("our-lady-of-guadalupe-parish-fremont-ca"), "fetched_at": "2026-10-31T05:14:38Z"},
    notes=["Schedule is an image (file name dated 09/2026) on the parish's Mass Schedule page; read by eye.",
           "Friday adoration starts 'after 9am mass' and First Friday 24-hour adoration has no start time: no adoration rows.",
           "Existing crawled confession row (Sat 3:30 PM) matches; not duplicated."],
    discrepancies=["DiscoverMass is older: Sunday 8:30am/12:15pm and Wednesday-only 7pm vs the parish image's 8:45am/12:30pm and Wed & Fri 7pm. Parish image kept."],
)
P["patrick-oakland"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://www.stpatrickoakland.org/", "status": "unreadable: Cloudflare challenge (sandbox and home connection)"},
    sec={"url": DM.format("st-patrick-oakland-ca"), "fetched_at": "2026-10-31T05:13:11Z"},
    sec_rows=[("mass", "Sunday", "09:00", "Spanish", None), ("mass", "Sunday", "10:45", None, None)],
)
P["corpus-christi-piedmont"] = dict(
    outcome="found (parish website)",
    own={"url": "https://corpuschristipiedmont.org/", "fetched_at": "2026-10-31T05:15:47Z",
         "note": "single-page app; schedule text read from the site's own script bundle /assets/index-DbrFDcvM.js"},
    own_rows=[("mass", "Saturday", "17:00", None, "Saturday Vigil 5:00pm"),
              ("mass", "Sunday", "08:00", None, "Sunday 8:00am & 10:30am"),
              ("mass", "Sunday", "10:30", None, "Sunday 8:00am & 10:30am"),
              ("mass", "Monday–Friday", "08:00", None, "Daily Mass Mon–Fri 8:00am")],
    sec={"url": DM.format("corpus-christi-piedmont-ca"), "fetched_at": "2026-10-31T05:13:22Z"},
    sec_rows=[("confession", "Saturday", "16:00", None, "Confessions Sat 4:00pm-4:30pm")],
    notes=["Parish site publishes no confession time; DiscoverMass confession row added as secondary. DiscoverMass Mass times agree."],
)
P["john-baptist-el-cerrito"] = dict(
    outcome="found (secondary source only)",
    own={"url": "http://www.sjtbc.us", "status": "unreadable: Cloudflare challenge (sandbox and home connection)"},
    sec={"url": DM.format("st-john-the-baptist-el-cerrito-ca"), "fetched_at": "2026-10-31T05:13:33Z"},
    sec_rows=[("mass", "Saturday", "08:00", None, None), ("mass", "Saturday", "17:00", None, None),
              ("mass", "Sunday", "08:00", None, None), ("mass", "Sunday", "09:30", None, None),
              ("mass", "Sunday", "11:30", None, None), ("mass", "Sunday", "13:00", "Mandarin", None),
              ("mass", "Sunday", "18:00", "Portuguese", None), ("mass", "Monday–Friday", "08:00", None, None),
              ("confession", "Saturday", "16:00", None, "Confessions Sat 4:00pm-4:45pm"),
              ("confession", "Sunday", "17:00", "Portuguese", "Confessions Sun 5:00pm-5:45pm (Portuguese)")],
    notes=["DiscoverMass adoration entry '12:00am' has no day; left out."],
)
P["joseph-pinole"] = dict(
    outcome="found (parish website)",
    own={"url": "https://www.sjcpinole.church/", "fetched_at": "2026-10-31T05:15:48Z"},
    own_rows=[("mass", "Monday–Friday", "09:00", None, "Monday to Friday: 9:00am"),
              ("mass", "First Friday of the month", "19:30", None, "First Friday of the Month: 7:30pm"),
              ("mass", "Saturday", "09:00", None, "Saturday: 9:00am (Mass)"),
              ("confession", "Saturday", "15:00", None, "Saturday: 3:00 - 4:00pm (Confession)"),
              ("mass", "Saturday", "16:30", None, "Saturday: 4:30pm (Vigil)"),
              ("mass", "Saturday", "18:30", "Spanish", "Saturday: 6:30pm (Spanish Mass)"),
              ("mass", "Sunday", "08:00", None, "Sunday: 8:00am, 10:00am, 12:00pm, 5:00pm"),
              ("mass", "Sunday", "10:00", None, "Sunday: 8:00am, 10:00am, 12:00pm, 5:00pm"),
              ("mass", "Sunday", "12:00", None, "Sunday: 8:00am, 10:00am, 12:00pm, 5:00pm"),
              ("mass", "Sunday", "17:00", None, "Sunday: 8:00am, 10:00am, 12:00pm, 5:00pm")],
    sec={"url": DM.format("st-joseph-pinole-ca"), "fetched_at": "2026-10-31T05:13:44Z"},
    discrepancies=["DiscoverMass: Saturday Spanish Mass 6:00pm; parish site: 6:30pm. Parish site kept."],
)
P["stephen-walnut-creek"] = dict(
    outcome="found (parish website)",
    own={"url": "https://www.ctkph.org/", "fetched_at": "2026-10-31T05:15:49Z"},
    own_rows=[("mass", "Saturday", "17:00", None, "St. Stephen Campus: 5:00 p.m."),
              ("mass", "Sunday", "09:30", None, "St. Stephen Campus: 9:30 a.m."),
              ("mass", "Monday, Wednesday, Friday", "09:00", None, "Weekday Masses (9:00 a.m.) Monday, Wednesday, Friday – St. Stephen")],
    sec={"url": DM.format("st-stephen-walnut-creek-ca"), "fetched_at": "2026-10-31T05:13:54Z"},
    notes=["Merged parish Christ the King – St. Stephen; these are the St. Stephen church's times (already curated on christ-king-stephen-pleasant-hill).",
           "Confession 'before the 5:00 p.m. Mass' has no start time; DiscoverMass (4:30pm) not used against it."],
)
P["anne-byron"] = dict(
    outcome="found (secondary source only)",
    own={"url": "https://www.anne.church/", "status": "unreadable: Cloudflare challenge (sandbox and home connection)"},
    sec={"url": DM.format("saint-anne-byron-ca"), "fetched_at": "2026-10-31T05:14:05Z"},
    sec_rows=[("mass", "Saturday", "17:00", None, "Saturday 5:00pm - Community Life Center"),
              ("mass", "Sunday", "08:30", None, "Sunday 8:30am - Community Life Center"),
              ("mass", "Sunday", "10:30", None, "Sunday 10:30am - Community Life Center"),
              ("mass", "Sunday", "12:30", "Spanish", "Sunday 12:30pm (Spanish) - Community Life Center"),
              ("mass", "Monday–Thursday", "09:00", None, "Monday–Thursday 9:00am - Chapel"),
              ("confession", "Saturday", "16:00", None, "Confessions Sat 4:00pm-4:30pm")],
)
P["rose-lima-patrick-crockett"] = dict(
    outcome="found (parish website)",
    own={"url": "https://www.strosecrockett.org/", "fetched_at": "2026-10-31T05:15:51Z"},
    own_rows=[("mass", "Tuesday–Friday", "11:00", None, "[St. Rose] Daily Mass 11:00AM at St Rose : Tuesday thru Friday"),
              ("mass", "Saturday", "16:30", None, "[St. Rose] Saturday Vigil Mass 4:30 PM at St Rose"),
              ("mass", "Sunday", "09:00", None, "[St. Rose] Sunday Masses 9:00 AM at St Rose"),
              ("mass", "Sunday", "11:00", None, "[St. Patrick Mission] Sunday Masses 11:00 AM at St Patrick Mission")],
    sec={"url": DM.format("st-rose-of-lima-crockett-ca"), "fetched_at": "2026-10-31T05:14:16Z"},
    notes=["Confession by appointment (no time). Directory listed no website; strosecrockett.org is linked from DiscoverMass and names the parish."],
)

# ---------------------------------------------------------------- 'No Mass listed today or tomorrow' with fragmentary rows

P["christ-light-oakland"] = dict(
    outcome="found (secondary source; supplements fragmentary crawl rows)",
    own={"url": "https://www.ctlcathedral.org/", "status": "unreadable 2026-10-30: Cloudflare challenge (sandbox and home connection)"},
    sec={"url": DM.format("cathedral-of-christ-the-light-oakland-ca"), "fetched_at": "2026-10-31T05:14:49Z"},
    sec_rows=[("mass", "Saturday", "07:30", "Vietnamese", None), ("mass", "Sunday", "11:00", None, None),
              ("mass", "Sunday", "13:30", "Spanish", None), ("mass", "Monday–Friday", "12:10", None, None),
              ("mass", "First Friday of the month", "19:00", "Vietnamese", "Friday 7:00pm (Vietnamese) - 1st Fridays"),
              ("adoration", "Thursday", "12:30", None, "Adoration Thr 12:30pm-5:00pm"),
              ("confession", "Tuesday", "11:30", None, "Confessions Tue 11:30am-12:00pm"),
              ("confession", "Thursday", "11:30", None, "Confessions Thr 11:30am-12:00pm")],
    notes=["Existing crawled rows (Sunday 8:00 AM Mass, Sunday 10:20 confession) agree with DiscoverMass and are kept; DiscoverMass's Sunday 8:00am (Vietnamese) not duplicated."],
)
P["louis-bertrand-oakland"] = dict(
    outcome="found (secondary source; partly conflicts with parish calendar)",
    own={"url": "https://www.slboakland.com/", "status": "unreadable 2026-10-30: Cloudflare challenge; 2026-10-29 crawl caught one calendar day (Thu: Misa ESP 9:00am, 6:30pm)"},
    sec={"url": DM.format("st-louis-bertrand-oakland-ca"), "fetched_at": "2026-10-31T05:15:00Z"},
    sec_rows=[("mass", "Saturday", "16:00", None, None), ("mass", "Saturday", "18:30", None, None),
              ("mass", "Sunday", "09:00", None, None), ("mass", "Sunday", "11:00", None, None),
              ("mass", "Sunday", "18:00", None, None), ("mass", "Monday–Friday", "18:30", None, None)],
    discrepancies=["DiscoverMass weekday 7:45am Mass vs parish calendar Thursday 9:00am Misa: weekday morning row left out."],
)
P["most-holy-rosary-antioch"] = dict(
    outcome="found (secondary source; parish page agrees on 8:00 AM and 5:30 PM)",
    own={"url": "https://holyrosaryantioch.org/mass", "status": "unreadable 2026-10-30: Cloudflare challenge; 2026-10-29 crawl lists 8:00 AM (bilingual), 5:30 PM (English), 7:30 PM with no days"},
    sec={"url": DM.format("most-holy-rosary-antioch-ca"), "fetched_at": "2026-10-31T05:15:11Z"},
    sec_rows=[("mass", "Saturday", "08:00", None, None), ("mass", "Saturday", "17:30", None, None),
              ("mass", "Sunday", "07:15", None, None), ("mass", "Sunday", "08:45", None, None),
              ("mass", "Sunday", "10:30", None, None), ("mass", "Sunday", "12:10", "Spanish", None),
              ("mass", "Sunday", "13:45", "Spanish", None), ("mass", "Sunday", "17:30", None, None),
              ("mass", "Monday–Friday", "08:00", None, None), ("mass", "Monday–Friday", "17:30", None, None),
              ("adoration", "Monday–Friday", "09:00", None, "Adoration Mon-Thr 9:00am-5:00pm, Fri 9:00am-6:00pm"),
              ("confession", "Friday", "16:00", None, "Confessions Fri 4:00pm-5:00pm"),
              ("confession", "Saturday", "16:00", None, "Confessions Sat 4:00pm-5:00pm")],
)
P["clement-hayward"] = dict(
    outcome="found (parish website - schedule image)",
    own={"url": "https://saintclementchurch.com/wp-content/uploads/1000278159-791x1024.png", "fetched_at": "2026-10-31T05:16:48Z",
         "page": "https://saintclementchurch.com/schedule-mass/"},
    own_rows=[("mass", "Saturday", "17:00", None, "Every Saturday 5:00 PM valid for Sunday Mass"),
              ("mass", "Sunday", "08:00", "English", "English Mass Sunday 8:00 AM 11:15 AM 6:30 PM"),
              ("mass", "Sunday", "11:15", "English", "English Mass Sunday 8:00 AM 11:15 AM 6:30 PM"),
              ("mass", "Sunday", "18:30", "English", "English Mass Sunday 8:00 AM 11:15 AM 6:30 PM"),
              ("mass", "Sunday", "09:30", "Spanish", "Spanish Mass Sunday 9:30 AM 1:00 PM"),
              ("mass", "Sunday", "13:00", "Spanish", "Spanish Mass Sunday 9:30 AM 1:00 PM"),
              ("mass", "Tuesday", "19:00", "Spanish", "Spanish Mass Tuesday 7:00 PM"),
              ("mass", "Monday, Tuesday, Thursday, Friday, Saturday", "08:00", None, "Daily Mass Monday to Saturday 8:00 AM"),
              ("mass", "Wednesday", "08:30", None, "Daily Mass Wednesday 8:30 AM")],
    sec={"url": DM.format("st-clement-hayward-ca"), "fetched_at": "2026-10-31T05:15:22Z"},
    notes=["Schedule is an image on the parish's Schedule Mass page; read by eye. DiscoverMass agrees.",
           "The parish's only hand-checked row is adoration, which hid all Mass rows (lib/find.ts preferCuratedRows); see the code change."],
)


def build():
    rows, report = [], []
    for eid, p in P.items():
        own, sec = p.get("own") or {}, p.get("sec") or {}
        n = 0
        for kind, day, time, lang, raw in p.get("own_rows", []):
            url = (p.get("own_url_by_kind") or {}).get(kind) or own.get("page") or own["url"]
            rows.append(dict(entity_id=eid, day=day, time=time, language=lang, kind=kind, raw=(raw or f"{day} {time}")[:200],
                             source=p.get("own_source", OWN), source_url=url, fetched_at=own.get("fetched_at"), secondary=False))
            n += 1
        for kind, day, time, lang, raw in p.get("sec_rows", []):
            url = sec["url"]
            if raw and raw.startswith("[") and p.get("sec_url_by_site"):
                url = p["sec_url_by_site"][raw[1:raw.index("]")]]
            rows.append(dict(entity_id=eid, day=day, time=time, language=lang, kind=kind,
                             raw=(raw or f"{day} {time}" + (f" ({lang})" if lang else "")) + " - secondary source",
                             source=SEC, source_url=url, fetched_at=sec.get("fetched_at"), secondary=True, note=DMNOTE))
            n += 1
        report.append(dict(entity_id=eid, outcome=p["outcome"], rows=n, own_source=own, secondary_source=sec,
                           notes=p.get("notes", []), discrepancies=p.get("discrepancies", [])))
    return rows, report


def sql(rows):
    def q(v):
        return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
    cols = ("entity_id", "day", "time", "language", "kind", "raw", "source", "source_url")
    vals = ",\n".join("(" + ",".join(q(r[c]) for c in cols) + ")" for r in rows)
    return f"""with ins as (
insert into mass_schedule (entity_id, day, time, language, kind, raw, source, source_url)
select v.*
from (values
{vals}) as v(entity_id, day, time, language, kind, raw, source, source_url)
where not exists (select 1 from mass_schedule m where m.entity_id = v.entity_id and m.kind = v.kind
                  and m.time = v.time and m.day is not distinct from v.day and m.language is not distinct from v.language)
returning entity_id, kind, source)
select count(*) rows_inserted, count(distinct entity_id) places, count(*) filter (where kind = 'mass') mass,
       count(*) filter (where kind = 'confession') confession, count(*) filter (where kind = 'adoration') adoration,
       count(*) filter (where source like 'secondary%') secondary
from ins;
"""


def main():
    rows, report = build()
    OUT.write_text(json.dumps({
        "generated_for": "2026-10-30 (Pacific)", "note": "unverified; own parish website preferred, DiscoverMass.com (MassTimes.org data) as secondary source",
        "labels": {OWN: "own parish website, read 2026-10-30", OWN29: "own parish website, residential crawl 2026-10-29",
                   SEC: DMNOTE},
        "parishes": report, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=1) + "\n")
    Path("/tmp/mass_times_fill.sql").write_text(sql(rows))
    from collections import Counter
    print(len(rows), "rows,", len({r["entity_id"] for r in rows}), "places", Counter(r["kind"] for r in rows),
          Counter(r["source"] for r in rows))


if __name__ == "__main__":
    main()
