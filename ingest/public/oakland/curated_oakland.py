"""Write the curated Diocese of Oakland deep-dive datasets (research doc 09).

Every record is paraphrased from a cited public source; no private individuals.
Usage: python -I ingest/public/oakland/curated_oakland.py data/real/oakland
Source notes and gaps: data/real/oakland/README.md
"""
import json
import os
import sys

RETRIEVED = "2026-10-26"
D = "https://www.oakdiocese.org"
REPO = "repo:"  # pointer to an existing dataset in this repository

out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)


def dump(name, payload):
    payload = {"retrieved": RETRIEVED, **payload}
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")


# ---------------------------------------------------------------- timeline
timeline = [
    ("1772-03-27", "pre-diocese", "First recorded Mass in the East Bay, Fr. Juan Crespi, near the future Lake Merritt", [f"{D}/history-of-our-diocese/"]),
    ("1797", "pre-diocese", "Mission San Jose founded (14th California mission) to catechize the Chochenyo Ohlone", [f"{D}/history-of-our-diocese/"]),
    ("1861", "pre-diocese", "St. Mary of the Immaculate Conception, Oakland, second parish in the future diocese (Wikipedia gives 1858)", [f"{D}/history-of-our-diocese/", "https://en.wikipedia.org/wiki/Diocese_of_Oakland"]),
    ("1864", "pre-diocese", "St. Leander, San Leandro, dedicated for Portuguese immigrants", ["https://en.wikipedia.org/wiki/Diocese_of_Oakland"]),
    ("1869", "pre-diocese", "St. Paul, San Pablo: first parish in Contra Costa County", [f"{D}/history-of-our-diocese/"]),
    ("1878", "pre-diocese", "St. Patrick, West Oakland, founded for Irish immigrants (later a Black Catholic and Latino home)", ["https://www.bishop-accountability.org/2026/05/st-patrick-catholic-church-slated/"]),
    ("1893-07-09", "cathedral", "St. Francis de Sales church dedicated (cathedral from 1962)", ["https://en.wikipedia.org/wiki/Cathedral_of_Saint_Francis_de_Sales_(Oakland,_California)"]),
    ("1934", "charity", "East Bay Catholic Charities formed by merging 36 Catholic aid agencies", [f"{D}/history-of-our-diocese/"]),
    ("1962-01-13", "founding", "Diocese erected by Pope John XXIII from the Archdiocese of San Francisco (with Santa Rosa and Stockton)", [f"{D}/history-of-our-diocese/"]),
    ("1962-04-28", "bishops", "Bishop Floyd L. Begin installed; attends all sessions of Vatican II", [f"{D}/history-of-our-diocese/"]),
    ("1964", "berkeley", "St. Albert's College (Dominican, Oakland) joins the Graduate Theological Union", ["https://en.wikipedia.org/wiki/Dominican_School_of_Philosophy_and_Theology"]),
    ("1967-02-04", "cathedral", "St. Francis de Sales rededicated after Vatican II renovation", ["https://en.wikipedia.org/wiki/Cathedral_of_Saint_Francis_de_Sales_(Oakland,_California)"]),
    ("1968", "berkeley", "Franciscan School of Theology moves to Berkeley and joins the GTU (left for San Diego in 2013)", ["https://en.wikipedia.org/wiki/Franciscan_School_of_Theology"]),
    ("1969", "berkeley", "Alma College (Jesuit) relocates to Berkeley as the Jesuit School of Theology (GTU article gives 1966 for joining)", ["https://en.wikipedia.org/wiki/Jesuit_School_of_Theology_of_Santa_Clara_University", "https://en.wikipedia.org/wiki/Graduate_Theological_Union"]),
    ("1977-05-03", "bishops", "John S. Cummins appointed second bishop", ["https://www.catholic-hierarchy.org/diocese/doakl.html"]),
    ("1978-02-17", "clergy", "First class of 27 permanent deacons ordained", [f"{D}/diaconate/"]),
    ("1984", "lay leadership", "First Diocesan Pastoral Convention (350 delegates, 87 parishes) creates a Diocesan Pastoral Council", [f"{D}/history-of-our-diocese/"]),
    ("1989-10-17", "cathedral", "Loma Prieta earthquake damages St. Francis de Sales cathedral and Sacred Heart beyond affordable repair (~$8M)", ["https://en.wikipedia.org/wiki/Cathedral_of_Saint_Francis_de_Sales_(Oakland,_California)"]),
    ("1993", "cathedral", "St. Francis de Sales and Sacred Heart razed", [f"{D}/history-of-our-diocese/"]),
    ("2003-10-01", "bishops", "Allen H. Vigneron succeeds as bishop", ["https://www.catholic-hierarchy.org/diocese/doakl.html"]),
    ("2005", "abuse crisis", "Diocese settles pending abuse suits for $56M (insurers 57%)", ["https://en.wikipedia.org/wiki/Diocese_of_Oakland"]),
    ("2005-05-21", "cathedral", "Groundbreaking for the Cathedral of Christ the Light", ["https://en.wikipedia.org/wiki/Cathedral_of_Christ_the_Light_(Oakland,_California)"]),
    ("2008-09-25", "cathedral", "Cathedral of Christ the Light dedicated; healing garden for abuse survivors opened in 2008", ["https://en.wikipedia.org/wiki/Cathedral_of_Christ_the_Light_(Oakland,_California)", f"{D}/history-of-our-diocese/"]),
    ("2009-03-23", "bishops", "Salvatore J. Cordileone appointed (to San Francisco in 2012)", ["https://www.catholic-hierarchy.org/diocese/doakl.html"]),
    ("2012", "jubilee", "50th anniversary: Vatican II symposium and multicultural Unity Festival", [f"{D}/history-of-our-diocese/"]),
    ("2013-05-25", "bishops", "Michael C. Barber, SJ ordained and installed (appointed May 3, 2013)", [f"{D}/bishop-barber/"]),
    ("2019-02-18", "abuse crisis", "Bishop publishes list of credibly accused clergy (20 diocesan priests; 22 religious priests, deacons and brothers; 3 extern priests)", [f"{D}/credible-accusations/"]),
    ("2020-06-19", "eucharist", "Diocesan Eucharistic Congress called for June 19-20, 2020 (whether held as planned during COVID is unverified)", ["https://www.ncronline.org/news/spirituality/shocking-ignorance-real-presence-prompts-eucharistic-congress"]),
    ("2021", "MAP", "Mission Alignment Process begins", [f"{D}/mission-alignment-process/"]),
    ("2021-06-21", "communications", "Bishop announces The Catholic Voice newspaper becomes a magazine (from Aug 2021)", ["https://22403.sites.ecatholic.com/news/bishop-barber-announces-change-to-the-catholic-voice", "https://catholicvoiceoakland.org"]),
    ("2022-03-31", "synod", "Deadline for parish synod listening reports to the diocese", ["https://files.ecatholic.com/22403/documents/2022/2/Youth%20Session%20Model%20022122docx.pdf?t=1645464276000"]),
    ("2022-09-10", "MAP", "MAP Interim Report (attendance down 42% 2019-2021; 25% fewer priests than 1985)", [REPO + "data/real/map_2022_summary.json"]),
    ("2023-05", "MAP", "MAP Phase 1 Implementation Report: three parish priorities; mergers and clusters", [f"{D}/wp-content/uploads/2025/09/MAP-Phase-1-Implementation-Report-EN.pdf", f"{D}/mission-alignment-process/"]),
    ("2023-05-08", "chapter 11", "RCBO files Chapter 11 (330+ abuse lawsuits under AB 218)", [f"{D}/chapter-11/"]),
    ("2023-07", "communities", "African American Pastoral Center newsletter Volume 1", [f"{D}/african-american-pastoral-center/"]),
    ("2024-02-01", "synod", "'Bringing the Synod Home' (diocese + JST) with Fr. Orobator, SJ, Santa Maria Parish, Orinda", ["https://www.scu.edu/media/jst/student-life/jst-magis-media/events-images-amp-files/ACFrOgA37w9jeON_RuTYUy8MolYn__blbVJp7f27CLHzVVoAJusY-nL5W7PPd4HDsYr-zuQ5SIkC0rcS0eQyCQbSLfVA1ld-cw8HbmjynnP-FoWMiNrFBwFAdaSyOfnj2IpBnef1ykqWIJzD18mp.pdf"]),
    ("2024-07", "eucharist", "About 200 Oakland pilgrims attend the National Eucharistic Congress (Indianapolis) with the bishop", [f"{D}/christ-is-the-reason-were-all-here/"]),
    ("2024-12-13", "abuse crisis", "Bishop's unconditional apology to survivors", [f"{D}/apology/"]),
    ("2025-01", "charity", "Mario Alioto becomes CEO of Catholic Charities East Bay", [f"{D}/a-calling-why-mario-alioto-moved-from-san-francisco-giants-to-leading-catholic-charities-east-bay/"]),
    ("2025-06", "immigration", "Bishop's statement in solidarity with Abp. Gomez on immigration raids; diocesan ICE preparation-and-response guide", [f"{D}/immigration/"]),
    ("2025-08", "immigration", "Immigration prayer services ('United in Prayer with Our Immigrant Community')", [f"{D}/immigration/", f"{D}/you-are-christs-body-a-catholic-call-to-community-amidst-the-immigration-crisis/"]),
    ("2025-09-20", "communities", "Chautauqua multicultural gathering at the Cathedral", [f"{D}/ethnic-pastoral-and-cultural-centers/"]),
    ("2025-10-11", "clergy", "14 permanent deacons ordained", [f"{D}/meet-oaklands-new-deacons/"]),
    ("2025-10-22", "devotion", "Relics of St. Therese at the Cathedral", [f"{D}/bishop-barber-the-appeal-of-the-relics-of-saint-therese/"]),
    ("2025-11-16", "communities", "Nigeria Igbo Catholic Community 20th-anniversary Mass, All Saints, Hayward", [f"{D}/nigerian-igbo-community-celebrates-20-years-of-catholic-ecosystem-in-east-bay/"]),
    ("2025-12", "works of mercy", "Holy Family Care Center / regional hub for mothers opens at St. Francis of Assisi, Concord", [f"{D}/holy-family-care-center/", f"{D}/catholic-regional-hub-for-moms-with-needs-launches-in-concord/"]),
    ("2026-02-05", "communities", "USCCB SCAPA regional API consultation 'New Seasons, New Grounds' hosted at the Cathedral (Feb 5-7)", [f"{D}/new-seasons-new-grounds-continues-cultivation-of-ministry-to-asian-and-pacific-island-community/"]),
    ("2026-03-25", "works of mercy", "Missionaries of Charity open new convent in East Oakland (moved from Richmond)", [f"{D}/closeness-to-christs-poor-in-oakland-missionaries-of-charity-move-their-convent-to-the-city/"]),
    ("2026-04-22", "chapter 11", "Diocesan statement after a jury verdict; apology reiterated", [f"{D}/chapter-11/"]),
    ("2026-04-28", "MAP", "Bishop's letter closes 13 worship sites (12 parish sites + St. Andrew Kim Korean Pastoral Center)", [f"{D}/aligning-our-reality-with-our-mission/"]),
    ("2026-05-23", "clergy", "Three priests ordained for the diocese", [f"{D}/diocese-celebrates-ordination-of-three-new-priests/"]),
    ("2026-06-12", "eucharist", "Consecration of the diocese to the Sacred Heart for the U.S. 250th (diocesan calendar)", [REPO + "data/real/diocese_events.json", f"{D}/united-states-to-be-consecrated-to-sacred-heart-before-independence-day/"]),
    ("2026-07-05", "eucharist", "27 East Bay pilgrims at the National Eucharistic Pilgrimage finale, Philadelphia", [f"{D}/oakland-pilgrims-mark-americas-250th-birthday-in-philly-by-encountering-christ-on-national-eucharistic-pilgrimage/"]),
    ("2026-08-22", "communities", "St. Francis jubilee Mass and Ohlone Memorial Garden blessing at Mission San Jose", [f"{D}/bishop-barber-honors-st-francis-ohlone-people-at-mission-mass-aug-22/"]),
    ("2026-10-02", "chapter 11", "Court denies confirmation of the Modified Fourth Amended Plan; bishop calls it a setback", [f"{D}/chapter-11/", "https://www.leagle.com/decision/inbco20261005608"]),
    ("2026-10-17", "communities", "Chautauqua 2026 at the Cathedral (scheduled)", [REPO + "data/real/diocese_events.json"]),
    ("2026-11-18", "chapter 11", "Scheduled hearing on the diocese's motion to amend findings / reconsideration", [REPO + "data/real/diocese/chapter11_timeline.json"]),
]
dump("timeline.json", {
    "dataset": "Diocese of Oakland: dated milestones in history and recent life (curated)",
    "note": "Paraphrased; dates as precise as the source allows (YYYY, YYYY-MM or YYYY-MM-DD). Chapter 11 detail lives in data/real/diocese/chapter11_timeline.json.",
    "count": len(timeline),
    "events": [{"date": d, "category": c, "summary": s, "sources": src} for d, c, s, src in timeline],
})

# ---------------------------------------------------------------- bishop's priorities
priorities = {
    "dataset": "Bishop Michael C. Barber, SJ: stated priorities and self-description (curated)",
    "mission_statement": {"text": "To know Christ better and to make Him better known", "sources": [f"{D}/communications/", f"{D}/"]},
    "pillars": [
        {"id": 1, "name": "Sunday Mass / worship", "phrasing_variants": ["strengthen parishioners' Sunday experience", "prayerful celebrations of the Mass"],
         "detail": "Homily, sacred music, hospitality; the 'vertical' dimension of worship (MAP Phase 1 Appendix 2)"},
        {"id": 2, "name": "Corporal and spiritual works of mercy", "phrasing_variants": ["embody the corporal and spiritual works of mercy", "prioritize works of mercy"],
         "detail": "Builds on the diocese's Year of Mercy; episcopal motto 'Deus misericordiarum Pater'"},
        {"id": 3, "name": "Missionary disciples", "phrasing_variants": ["form missionary disciples"],
         "detail": "Faith-sharing, social justice engagement and spiritual growth (MAP Phase 1 Appendix 2)"},
    ],
    "pillar_sources": [
        f"{D}/wp-content/uploads/2025/09/MAP-Phase-1-Implementation-Report-EN.pdf",
        f"{D}/bishop-barber/", f"{D}/communications/", f"{D}/aligning-our-reality-with-our-mission/",
    ],
    "themes": [
        {"theme": "Mission over maintenance (MAP)", "summary": "Status quo not sustainable; parishes built for the Church of 1965; finances a symptom of declining participation and fewer priests; receiving parishes must welcome newcomers into parish life; ~10-minute drive maximum", "dates": ["2026-04-28", "2026-05-02"],
         "sources": [f"{D}/aligning-our-reality-with-our-mission/", f"{D}/mission-alignment-process-challenges-opportunities/"]},
        {"theme": "Eucharist and reverent liturgy", "summary": "Eucharistic Congress called for 2020; ~200 pilgrims to 2024 National Eucharistic Congress; praises chant, silence, focus on Christ; cathedral sacred-music program; Corpus Christi procession; Sacred Heart consecration June 2026", "dates": ["2019/2020", "2024-07", "2026-06-12"],
         "sources": ["https://www.ncronline.org/news/spirituality/shocking-ignorance-real-presence-prompts-eucharistic-congress", f"{D}/christ-is-the-reason-were-all-here/", "https://thomasaquinas.edu/news/greater-glory-god-interview-most-rev-michael-c-barber-sj"]},
        {"theme": "Catechesis and lay formation", "summary": "Parish catechesis judged weak; wants lay catechists (Ministry of Catechist); Serra Catechetical Institute; School for Pastoral Ministry forms 'missionary disciples'", "dates": ["2021-06-18"],
         "sources": ["https://thomasaquinas.edu/news/greater-glory-god-interview-most-rev-michael-c-barber-sj", f"{D}/serra-catechetical-institute/", f"{D}/spm/"]},
        {"theme": "Catholic identity of schools", "summary": "Formation of principals first, then hiring contracts (2021 pushback)", "dates": ["2021"],
         "sources": ["https://thomasaquinas.edu/news/greater-glory-god-interview-most-rev-michael-c-barber-sj"]},
        {"theme": "Vocations (priests, deacons, marriage)", "summary": "3 priests ordained 2026; 14 deacons 2025; new vocations and diaconate directors; BMA 'Build Our Future'", "dates": ["2025-10-11", "2026-05-23"],
         "sources": [f"{D}/diocese-celebrates-ordination-of-three-new-priests/", f"{D}/meet-oaklands-new-deacons/", f"{D}/2026-bishops-ministries-appeal-one-faith-one-family-one-mission/"]},
        {"theme": "Immigration", "summary": "Solidarity with Abp. Gomez on raids (June 2025); prayer services built on 1 Cor 12 (Aug 2025): pray together, share each other's situation across divides, press legislators for comprehensive reform; diocesan preparedness guide; 'Pilgrims of Hope' parish visits", "dates": ["2025-06", "2025-08"],
         "sources": [f"{D}/immigration/", f"{D}/you-are-christs-body-a-catholic-call-to-community-amidst-the-immigration-crisis/"]},
        {"theme": "Poor and unhoused", "summary": "No stand-alone homelessness statement found; addressed via works-of-mercy pillar: Diocesan Campaign for Mercy (root causes incl. housing), BMA 'Sustain Our Family', Parishes in Action, Holy Family Care Center, Missionaries of Charity convent", "dates": ["2025-12", "2026-03-25"],
         "sources": [f"{D}/diocesan-campaign-for-mercy/", f"{D}/parishes-in-action/", f"{D}/holy-family-care-center/"]},
        {"theme": "Abuse survivors", "summary": "2019 credibly-accused list ('nothing to hide'); Dec 2024 unconditional apology (survivors blameless); Victims Assistance 24/7 line; healing garden", "dates": ["2019-02-18", "2024-12-13", "2026-04-22"],
         "sources": [f"{D}/credible-accusations/", f"{D}/apology/", f"{D}/victims-assistance/", f"{D}/chapter-11/"]},
        {"theme": "Bankruptcy", "summary": "Fairness to all survivors, not only those first to trial; BMA gifts held in trust; Oct 2, 2026 denial called a setback, commitment renewed; 120 active priests pledge service", "dates": ["2023-05-08", "2026-10-02"],
         "sources": [f"{D}/chapter-11/"]},
        {"theme": "Religious liberty and tradition", "summary": "Opposed SB 360 on the seal of confession (2019); continued permission for 1962 Missal (July 2021)", "dates": ["2019-05", "2021-07-18"],
         "sources": ["https://en.wikipedia.org/wiki/Michael_C._Barber"]},
    ],
    "self_description": [
        {"summary": "Holds full responsibility for the pastoral care of every Catholic in the diocese; priority that every affected parishioner is welcomed", "source": f"{D}/aligning-our-reality-with-our-mission/"},
        {"summary": "Shepherd of 'my flock'; diocese as 'One Faith, One Family, One Mission'", "source": f"{D}/bearing-one-anothers-burdens-brings-unity/"},
        {"summary": "2013: the bishop looks after the priests so the priests can look after the people; wants to do for Oakland what Francis does for the Church", "source": "https://www.ewtnnews.com/world/us/new-oakland-bishop-looks-to-pope-francis-example"},
        {"summary": "Grieves with those affected by closures; a closing church is where his paternal grandparents married", "source": f"{D}/mission-alignment-process-challenges-opportunities/"},
    ],
    "biography": {"born": "1954-07-13", "jesuit_since": 1973, "ordained_priest": "1985-06-08", "appointed": "2013-05-03", "installed": "2013-05-25",
                  "motto": "Deus misericordiarum Pater", "navy_reserve_chaplain": "1991-, Captain 2012",
                  "canonical_retirement_age_reached": "2029-07", "sources": [f"{D}/bishop-barber/", "https://en.wikipedia.org/wiki/Michael_C._Barber"]},
    "consultative_bodies": {"college_of_consultors_size": 8, "presbyteral_council_seats": "5 regions + parochial vicars (2) + men religious + incardinated retired + special works + 5 bishop's appointees + ex officio",
                            "source": f"{D}/bishop-barber/"},
}
dump("bishop_priorities.json", priorities)

# ---------------------------------------------------------------- synod / listening
synod = {
    "dataset": "Synod on Synodality (2021-2024) and other listening data for the Diocese of Oakland",
    "oakland_synthesis_status": {
        "found": False,
        "note": "Oakland's 2022 diocesan synthesis was not publicly retrievable: old eCatholic site disabled; oakdiocese.org has no synod page; Wayback blocked by sandbox egress; canonical copy of the Oct 2024 'People of Hope' article timed out on a permission prompt.",
        "urls_tried": ["https://22403.sites.ecatholic.com/news/synod-people-of-hope", "https://eqzx6a.sites.ecatholic.com/news/synod-people-of-hope", f"{D}/synod/"],
        "owner_to_ask": "Office of Faith Formation & Evangelization (synod core team contact: Fr. Jimmy Macalinao)",
    },
    "process": {
        "diocesan_contact": "Fr. Jimmy Macalinao (Synod Core Team)",
        "format": "2-hour sessions; prayer (ideally adoration) then small groups of ~5 with facilitator and scribe; affirm, do not debate",
        "parish_report_deadline": "2022-03-31",
        "diocesan_youth_session": {"date": "2022-03-12", "place": "St. Isidore, Danville"},
        "diocesan_young_adult_session": {"date": "2022-03-25", "place": "Assumption, San Leandro"},
        "flow": "parish recorder -> pastor approval -> diocese -> Synod Core Team summary -> bishop approval -> USCCB",
        "sources": ["https://files.ecatholic.com/22403/documents/2022/2/Youth%20Session%20Model%20022122docx.pdf?t=1645464276000",
                    "https://22403.sites.ecatholic.com/news/diocesan-core-team-announced-for-synod-2021-2023"],
    },
    "region_xi_synthesis": {
        "source": "https://usccb.org/resources/Region%20XI%20Synthesis%20Final.pdf",
        "dioceses": 14,
        "participation": {"in_person_sessions": 3290, "in_person_participants": 61056, "virtual_sessions": 284, "virtual_participants": 4034, "online_survey_respondents_7_dioceses": 166165},
        "oakland_specific": "Only Oakland quote is in the youth/young adult theme (pp. 3-4): bring young people and families back; Church is dying without them",
        "themes": [
            {"theme": "joys", "summary": "Eucharist, sacraments, parish community; COVID deepened longing for Communion"},
            {"theme": "wounds", "summary": "abuse crisis not healed; mistrust of leadership; exclusion felt by some, incl. divorced"},
            {"theme": "hopes", "summary": "continue listening; more lay involvement; effective pastoral councils"},
            {"theme": "youth", "summary": "losing ages ~15-25 to 'Nones' and megachurches; new outreach incl. social media"},
            {"theme": "formation_liturgy", "summary": "better homilies tied to life and CST; adult formation; value lay liturgical ministries"},
            {"theme": "social_justice", "summary": "unhoused, immigrants, creation"},
            {"theme": "margins", "summary": "homeless, immigrants, disabled, addiction/mental illness, incarcerated, divorced, LGBTQ+; many underrepresented"},
            {"theme": "women", "summary": "more leadership; some ask diaconate; others disagree"},
            {"theme": "lgbtq", "summary": "experience of judgment; split between welcome with or without doctrinal change"},
            {"theme": "clergy_accountability", "summary": "transparency on abuse; regular review of ministry; reasons for rules; lay voice in decisions"},
        ],
    },
    "other_listening_sources": [
        {"name": "Disciple Maker Index (CLI)", "date": "2021-10/2021-12", "scope": "every parish; 20,000+ responses", "source": REPO + "docs/research-prior-art.md"},
        {"name": "MAP clergy gathering", "date": "2022-03", "scope": "98 clergy; comments coded (finances 30%, attendance 26%)", "source": REPO + "data/real/map_2022_summary.json"},
        {"name": "MAP regional meetings", "date": "2021-2022", "scope": "368 observations", "source": REPO + "data/real/map_2022_summary.json"},
        {"name": "Bringing the Synod Home", "date": "2024-02-01", "scope": "diocese + JST; Fr. Orobator SJ", "source": "https://www.scu.edu/media/jst/student-life/jst-magis-media/events-images-amp-files/ACFrOgA37w9jeON_RuTYUy8MolYn__blbVJp7f27CLHzVVoAJusY-nL5W7PPd4HDsYr-zuQ5SIkC0rcS0eQyCQbSLfVA1ld-cw8HbmjynnP-FoWMiNrFBwFAdaSyOfnj2IpBnef1ykqWIJzD18mp.pdf"},
        {"name": "People of Hope (synod article, title/summary only)", "date": "2024-10", "scope": "Oakland's contributions to the synod", "source": "https://22403.sites.ecatholic.com/news/synod-people-of-hope"},
        {"name": "African American Pastoral Center listening sessions", "date": "2023-", "scope": "committed; results not published", "source": f"{D}/african-american-pastoral-center/"},
        {"name": "USCCB SCAPA regional API consultation", "date": "2026-02-05/07", "scope": "synodal discussions; panels on mental health, LGBTQ+, AI, youth", "source": f"{D}/new-seasons-new-grounds-continues-cultivation-of-ministry-to-asian-and-pacific-island-community/"},
        {"name": "Parish Annual Addendum (quantitative return)", "date": "2026-01-12 (CY2025 data)", "scope": "registered Catholics, ethnicity %, sacraments, faith formation, personnel, councils", "source": f"{D}/parish-annual-addendum/"},
    ],
}
dump("synod_listening.json", synod)

# ---------------------------------------------------------------- offices
offices = [
    {"office": "Office of the Bishop", "functions": ["bishop's calendar and correspondence", "College of Consultors", "Presbyteral Council", "Priest Personnel Board"],
     "programs_2025_26": ["public calendar (88 entries Apr-Nov 2026)"], "public_lead": "Bishop Michael C. Barber, SJ",
     "data_assets_aggregate": ["calendar", "council agendas"], "platform_priority": 1, "sources": [f"{D}/bishop-barber/", REPO + "data/real/diocese_events.json"]},
    {"office": "Vicar General / Pastoral Life (clergy)", "functions": ["vicar general", "vicar for priests", "clergy services incl. retired-priest care", "vocations", "seminarians", "permanent diaconate and formation", "ongoing formation of priests"],
     "programs_2025_26": ["vocations director since 2025-07", "new diaconate director (2025-26)", "priests' retreat 2026-08", "International Priest Day 2026-10-15"],
     "public_lead": "Vicar General Fr. Lawrence D'Anjou", "data_assets_aggregate": ["assignments", "age/retirement projection", "seminarian and deacon cohorts"], "platform_priority": 2,
     "sources": [f"{D}/helping-connect-the-dots-in-gods-call-and-a-mans-response-to-priestly-vocations/", f"{D}/father-jimmy-once-a-deacon-now-their-director/", f"{D}/diaconate/", REPO + "data/real/chancery_staff.json"],
     "notes": "Staff directory still lists an earlier vocations director; a March 2026 diocesan post names Fr. Matthew Murray (since July 2025)."},
    {"office": "Office of the Chancellor", "functions": ["safe environment (Virtus every 3 years; Live Scan)", "victims assistance / survivor advocacy", "letters of good standing", "policy compliance for events", "sacramental-register norms and diocesan archives", "HR sits under Chancellor in the directory"],
     "programs_2025_26": ["MAP closures: register transfer", "Oct 2025 sacramental records guidelines"], "public_lead": "Chancellor Rick Medeiros",
     "data_assets_aggregate": ["training/fingerprint compliance per parish", "register custody log"], "platform_priority": 1,
     "sources": [f"{D}/safe-environment/", f"{D}/victims-assistance/", f"{D}/guidelines-sacramental-records-and-reports/", f"{D}/parish-resources-forms/"]},
    {"office": "Tribunal", "functions": ["marriage nullity cases", "canonical advice", "proper-pastor permissions and tribunal forms"],
     "programs_2025_26": ["Spanish-language case support"], "public_lead": None, "data_assets_aggregate": ["case counts and turnaround"], "platform_priority": 4,
     "sources": [f"{D}/tribunal/", f"{D}/for-marco-pozo-compassionate-service-for-oakland-tribunal-traverses-the-miles/"]},
    {"office": "Faith Formation & Evangelization", "functions": ["catechesis and OCIA", "catechist certification", "adult faith formation", "youth and young adult ministry", "marriage and family life", "SPRED", "Office of Worship", "Latino Ministries", "Ethnic Pastoral and Cultural Centers", "African American Pastoral Center", "Life and Justice (respect life, social justice, detention ministry, immigration)"],
     "programs_2025_26": ["Serra Catechetical Institute", "School for Pastoral Ministry (3 yrs)", "Youth Leadership Summit 2026-07-20/23", "Anointed 2026-05-30", "new marriage preparation 2026 (20-hour minimum)", "Chautauqua", "detention ministry (~5,900 people, 10 facilities)", "Holy Family Care Center", "Pilgrims of Hope immigration parish visits"],
     "public_lead": None, "data_assets_aggregate": ["OCIA, catechist and program enrollments", "events"], "platform_priority": 3,
     "sources": [f"{D}/catechist/", f"{D}/serra-catechetical-institute/", f"{D}/spm/", f"{D}/yls/", f"{D}/anointed/", f"{D}/new-more-holistic-marriage-preparation-program-launching-in-2026/", f"{D}/spred/", f"{D}/detention-ministry/", f"{D}/immigration/", f"{D}/latino-ministries/", f"{D}/ethnic-pastoral-and-cultural-centers/", f"{D}/african-american-pastoral-center/"]},
    {"office": "Department of Catholic Schools", "functions": ["superintendent and associate superintendents", "technology and professional learning", "Lumen Christi Academies"],
     "programs_2025_26": ["Saint Francis de Sales Teacher Awards 2026", "Mass with elementary principals 2026-08-03"], "public_lead": "Superintendent Dr. Andrew Currier",
     "data_assets_aggregate": ["enrollment (CDE K-12: 14,621 in 2023-24; 14,112 in 2025-26)", "accreditation"], "platform_priority": 3,
     "sources": [REPO + "data/real/README-schools.md", f"{D}/2026-saint-francis-de-sales-teacher-award-winners/", f"{D}/chapter-11/"],
     "notes": "Schools are separate legal entities, not in the bankruptcy; the Welfare Corporation contributes to the settlement."},
    {"office": "Mission Advancement", "functions": ["Bishop's Ministries Appeal (pooled fund, parish goals)", "FACE need-based tuition grants (since 1978)", "Diocesan Campaign for Mercy grants", "Missionary Cooperative Plan", "second collections", "legacy giving"],
     "programs_2025_26": ["BMA 2026 'One Faith. One Family. One Mission.' (Sustain Our Family; Build Our Future; Heal Our Members; Empower Our Mission)", "FACE 2026-27 grants: $2,750 elementary, $3,500 high school; ~700 students/yr", "FACE Gala"],
     "public_lead": "Director Terri Porter", "data_assets_aggregate": ["BMA % of goal and participation by parish", "FACE grants", "Campaign for Mercy grants"], "platform_priority": 2,
     "sources": [f"{D}/bishops-ministries-appeal/", f"{D}/2026-bishops-ministries-appeal-one-faith-one-family-one-mission/", f"{D}/face-about/", f"{D}/diocesan-campaign-for-mercy/", f"{D}/missionary-cooperative-plan/"]},
    {"office": "Communications", "functions": ["The Catholic Voice (quarterly magazine: Mar/Jun/Sep/Dec, online)", "OAK Catholic (bi-weekly email)", "The Weekly (Mondays, parish leadership; submissions Wed 5 pm)", "website and social (Facebook, Instagram, Vimeo)", "event and web-change request forms"],
     "programs_2025_26": ["coordinated plan around the bishop's three pillars"], "public_lead": "Director Helen Osman (associate publisher, The Catholic Voice)",
     "data_assets_aggregate": ["subscriber counts", "Weekly items"], "platform_priority": 1,
     "sources": [f"{D}/communications/", f"{D}/the-weekly/", f"{D}/summer-edition-of-the-catholic-voice-now-available-online/"]},
    {"office": "Financial & Material Resources", "functions": ["CFO", "controllers incl. parish controller", "AP/AR", "payroll", "facilities planning", "contracts, insurance and risk", "cathedral center operations", "security"],
     "programs_2025_26": ["Chapter 11 property sales since 2025-08 (review, broker, pastor + parish finance council, bishop approval)"], "public_lead": "CFO Attila Bardos",
     "data_assets_aggregate": ["assessments", "deposit and loan fund", "property inventory"], "platform_priority": 4,
     "sources": [REPO + "data/real/chancery_staff.json", f"{D}/chapter-11/"]},
    {"office": "Human Resources", "functions": ["personnel policies handbook (2024)", "lay retirement plan", "benefits", "parish holidays", "job postings"],
     "programs_2025_26": ["parish postings: bookkeepers, music directors, religious-education coordinator, business manager"], "public_lead": None,
     "data_assets_aggregate": ["open roles (public)"], "platform_priority": 5, "sources": [f"{D}/parish-resources-forms/", f"{D}/jobs/"]},
    {"office": "Chaplains", "functions": ["hospital chaplaincy", "jail chaplaincy (with detention ministry)"],
     "programs_2025_26": ["White Mass 2026-10-11"], "public_lead": None, "data_assets_aggregate": ["chaplaincy coverage"], "platform_priority": 5,
     "sources": [REPO + "data/real/chancery_staff.json", f"{D}/diocese-celebrates-ordination-of-three-new-priests/"]},
]
related = [
    {"entity": "Catholic Charities East Bay", "relationship": "separate nonprofit; not in bankruptcy", "notes": "CEO Mario Alioto since 2025-01; ~2,500 people served in prior year (holiday guide)", "sources": [f"{D}/a-calling-why-mario-alioto-moved-from-san-francisco-giants-to-leading-catholic-charities-east-bay/", f"{D}/the-gift-of-giving-2025/", REPO + "data/real/cceb_990.json"]},
    {"entity": "Catholic Funeral & Cemetery Services", "relationship": "separate corporation; not in bankruptcy", "notes": "see cemeteries.json", "sources": [REPO + "data/real/cemeteries.json", f"{D}/chapter-11/"]},
    {"entity": "Order of Malta Clinic of Northern California", "relationship": "independent clinic on the cathedral campus", "notes": "mobile dental with La Clinica from 2026-07-09; ophthalmology space", "sources": [f"{D}/order-of-malta-clinic-expands-dental-and-eye-care-services/"]},
    {"entity": "Catholic Cathedral Corporation of the East Bay", "relationship": "owns the Cathedral of Christ the Light; separate from the chancery", "notes": "lease extension heard in Chapter 11 court 2026-09-30", "sources": ["https://en.wikipedia.org/wiki/Cathedral_of_Christ_the_Light_(Oakland,_California)", REPO + "data/real/diocese/chapter11_timeline.json"]},
]
dump("offices.json", {
    "dataset": "Diocese of Oakland chancery offices, functions, current programs and platform fit (curated)",
    "note": "Public office-holders named only where the diocese lists them publicly; platform_priority 1 = benefits first (see doc 09 section 7.2).",
    "offices": offices, "related_entities": related,
})

# ---------------------------------------------------------------- communities
communities = [
    {"community": "Latino / Hispanic", "ministry": "Latino Ministries (Pastoral de Conjunto)", "signals": ["about half of parishes have Spanish Masses (diocesan coordinator, 2025)", "~10 apostolic movements", "annual diocesan Guadalupe pilgrimage", "Hispanic Charismatic Renewal Congress Mass 2026-07-25"],
     "sources": [f"{D}/latino-ministries/", f"{D}/you-are-christs-body-a-catholic-call-to-community-amidst-the-immigration-crisis/", REPO + "data/real/diocese_events.json"]},
    {"community": "Black / African American", "ministry": "African American Pastoral Center (newsletters since 2023-07)", "signals": ["St. Benedict, St. Columba, St. Patrick identify as Black parishes; St. Louis Bertrand also listed", "688 of 9,308 diocesan school students (7.39%)", "St. Patrick on 2026 closure list"],
     "sources": [f"{D}/african-american-pastoral-center/", f"{D}/aligning-our-reality-with-our-mission/"]},
    {"community": "Asian and Pacific Islander (Filipino, Vietnamese, Chinese, Korean, Indonesian, Thai, Laotian Khmu, Fijian, Tongan, Indian)", "ministry": "Ethnic Pastoral and Cultural Centers", "signals": ["Masses in Vietnamese, Tagalog, Cantonese, Mandarin, Korean, Indonesian, Kmhmu", "Tongan Mass listed at St. Bernard, Oakland (diocesan directory)", "SCAPA regional consultation hosted 2026-02-05/07", "Simbang Gabi; Our Lady of Vailankanni feasts in several parishes", "St. Andrew Kim Korean Pastoral Center on 2026 closure list"],
     "sources": [f"{D}/ethnic-pastoral-and-cultural-centers/", f"{D}/new-seasons-new-grounds-continues-cultivation-of-ministry-to-asian-and-pacific-island-community/", f"{D}/aligning-our-reality-with-our-mission/", REPO + "data/real/parishes_directory.json"]},
    {"community": "Nigerian Igbo", "ministry": "Nigeria Igbo Catholic Community (chaplaincy)", "signals": ["20 years in the East Bay (2025)", "Igbo-language Masses"],
     "sources": [f"{D}/nigerian-igbo-community-celebrates-20-years-of-catholic-ecosystem-in-east-bay/"]},
    {"community": "Portuguese", "ministry": None, "signals": ["Portuguese Masses listed by the diocese", "St. Leander (1864) founded for Portuguese immigrants"],
     "sources": [f"{D}/ethnic-pastoral-and-cultural-centers/", "https://en.wikipedia.org/wiki/Diocese_of_Oakland"]},
    {"community": "Polish", "ministry": None, "signals": ["Polish Masses listed by the diocese"], "sources": [f"{D}/ethnic-pastoral-and-cultural-centers/"]},
    {"community": "Ohlone (indigenous)", "ministry": None, "signals": ["Ohlone Memorial Garden blessed at Mission San Jose 2026-08-22"], "sources": [f"{D}/bishop-barber-honors-st-francis-ohlone-people-at-mission-mass-aug-22/"]},
    {"community": "Deaf", "ministry": None, "signals": ["American Sign Language listed among Mass languages (Wikipedia)"], "sources": ["https://en.wikipedia.org/wiki/Diocese_of_Oakland"]},
    {"community": "People with developmental disabilities", "ministry": "SPRED", "signals": ["parish SPRED centers; model and training center in Oakland"], "sources": [f"{D}/spred/"]},
]
dump("communities.json", {
    "dataset": "Language and cultural communities and their ministries (curated)",
    "diocese_wide": {
        "mass_languages_named_by_diocese": ["Spanish", "Portuguese", "Polish", "Kmhmu", "Vietnamese", "Indonesian", "Cantonese", "Mandarin", "Korean", "Tagalog"],
        "mass_language_count": "more than a dozen (diocese); 15 incl. ASL and Latin (Wikipedia)",
        "map_2022_non_english_or_culture_based_weekend_masses": {"value": 102, "of_total": 402, "note": "region subtotals sum to 114 of 427 (source discrepancy)", "source": REPO + "data/real/map_2022_summary.json"},
        "chautauqua": {"what": "diocesan multicultural 'gathering of peoples' at the Cathedral", "dates": ["2025-09-20", "2026-10-17"], "sources": [f"{D}/ethnic-pastoral-and-cultural-centers/", REPO + "data/real/diocese_events.json"]},
        "deacon_class_2025_birthplaces": {"born_outside_us": 10, "of": 14, "by_country": {"Philippines": 4, "Mexico": 3, "Peru": 1, "India": 1, "Fiji": 1}, "source": f"{D}/meet-oaklands-new-deacons/"},
        "sources": [f"{D}/ethnic-pastoral-and-cultural-centers/", "https://en.wikipedia.org/wiki/Diocese_of_Oakland"],
    },
    "communities": communities,
})

# ---------------------------------------------------------------- closures 2026
cl = [
    ("Mary Help of Christians", "Oakland", "site of St. Anthony-Mary Help of Christians Parish", "anthony mary help christians|oakland", None),
    ("Our Lady of Guadalupe (Blacow Road site)", "Fremont", "relocates to the parish's Fremont Boulevard site", "our lady guadalupe|fremont", None),
    ("Our Lady of Lourdes", "Oakland", "clustered under Corpus Christi, Piedmont (MAP Phase 1); parishioners organized Sept 2025 to save it (Oaklandside)", "our lady lourdes|oakland", "https://www.bishop-accountability.org/2026/05/7-oakland-catholic-churches-to-close/"),
    ("Sacred Heart", "Oakland", "parish site; original church razed 1993 after the 1989 earthquake", "sacred heart|oakland", f"{D}/history-of-our-diocese/"),
    ("St. Albert the Great", "Alameda", "site of St. Philip Neri-St. Albert the Great Parish", "philip albert|alameda", "https://alamedapost.com/news/st-barnabas-st-albert-great-among-dozen-parishes-closed-diocese/"),
    ("St. Andrew Kim Korean Pastoral Center", "Oakland", "pastoral center for the Korean Catholic community", None, None),
    ("St. Augustine", "Oakland", "inter-parish association with St. Joseph the Worker (MAP Phase 1)", "augustine|oakland", None),
    ("St. Barnabas", "Alameda", "merged under St. Joseph Basilica (MAP Phase 1); still hosted a diocesan-listed event 2026-10-11", "barnabas|alameda", "https://alamedapost.com/news/st-barnabas-st-albert-great-among-dozen-parishes-closed-diocese/"),
    ("St. Paschal Baylon", "Oakland", "led by the pastor of Divine Mercy Parish (St. Lawrence O'Toole site)", None, "https://www.bishop-accountability.org/2026/05/7-oakland-catholic-churches-to-close/"),
    ("St. Patrick", "Oakland", "founded 1878; Black Catholic and Latino community; clustered under the Cathedral (MAP Phase 1); members discussed canonical appeal", "patrick|oakland", "https://www.bishop-accountability.org/2026/05/st-patrick-catholic-church-slated/"),
    ("St. Rose of Lima", "Crockett", "St. Rose of Lima-St. Patrick Mission (Crockett/Port Costa)", "rose patrick|crockett", None),
    ("St. Stephen", "Walnut Creek", "merged with Christ the King, Pleasant Hill (completed April 2022); ~300 parishioners; town hall 2026-05-17", "stephen|walnut-creek", "https://www.bishop-accountability.org/2026/05/east-bay-parishioners-were-optimistic/"),
    ("Transfiguration", "Castro Valley", "clustered under Our Lady of Grace (MAP Phase 1)", "transfiguration|castro-valley", None),
]
dump("closures_2026.json", {
    "dataset": "Worship sites closed by Bishop Barber's letter of April 28, 2026 (MAP next phase)",
    "announcement": {"date": "2026-04-28", "source": f"{D}/aligning-our-reality-with-our-mission/",
                     "follow_up_column": f"{D}/mission-alignment-process-challenges-opportunities/",
                     "stated_reasons": ["declining Mass attendance, sacraments and school enrollment since early 2010s", "all-time low of priests assigned to 80 parishes; rising average age", "some parishes and schools financially unsustainable"],
                     "commitments": ["every affected parishioner welcomed at a nearby parish", "no one should drive more than ~10 minutes", "work with pastor, pastoral council and finance council of each site", "property disposition with receiving pastor(s)", "help lay staff find work"],
                     "status_oct_2026": "No closure decrees or dates found in sources reviewed; FAQ says timelines are unique to each site."},
    "canonical_notes": {"recourse_window": "appeal within 10 days of the formal decree (as reported by Black Catholic Messenger)",
                        "registers": "suppressed parish registers go to diocesan archives; merged parish keeps all registers",
                        "sources": ["https://www.bishop-accountability.org/2026/05/st-patrick-catholic-church-slated/", f"{D}/guidelines-sacramental-records-and-reports/"]},
    "join_key_note": "map_canon_key joins to data/real/map_2022_attendance.json rows (parish-level; multi-site parishes are not separable by site).",
    "sites": [{"site": s, "city": c, "context": ctx, "map_canon_key": k, "extra_source": src} for s, c, ctx, k, src in cl],
    "mapping_source": f"{D}/wp-content/uploads/2025/09/MAP-Phase-1-Implementation-Report-EN.pdf",
})

# ---------------------------------------------------------------- clergy
clergy = {
    "dataset": "Clergy picture: counts by definition, projection, ordinations, deacons (curated pointers + new facts)",
    "counts": [
        {"label": "Annuario 2023", "priests_total": 358, "diocesan": 184, "religious": 174, "permanent_deacons": 99, "male_religious": 312, "female_religious": 366, "parishes": 85, "source": REPO + "data/real/diocese/hierarchy_stats.json"},
        {"label": "Annuario 2021", "priests_total": 378, "diocesan": 194, "religious": 184, "permanent_deacons": 122, "source": REPO + "data/real/diocese/hierarchy_stats.json"},
        {"label": "Diocese 'About' page (undated)", "priests_total": 345, "permanent_deacons": 110, "religious_brothers_and_sisters": 455, "parishes": 82, "pastoral_centers": 11, "source": f"{D}/history/"},
        {"label": "Diocese statement 2019-02-18", "active_priests": "more than 120", "source": f"{D}/credible-accusations/"},
        {"label": "Diocese statement 2026-10-02", "active_priests": 120, "source": f"{D}/chapter-11/"},
        {"label": "MAP chart: pastors + administrators + parochial vicars", "by_year": {"1985": 155, "2022": 117}, "source": REPO + "data/real/map_2022_summary.json"},
    ],
    "projection_priests_in_ministry_map_chart": {"source": REPO + "data/real/map_2022_summary.json", "note": "read from chart image; total constant at 130 each year",
        "rows": {"2022": {"le70": 119, "71_75": 5, "ge76": 6}, "2026": {"le70": 106, "71_75": 14, "ge76": 10}, "2030": {"le70": 96, "71_75": 16, "ge76": 18}, "2034": {"le70": 73, "71_75": 24, "ge76": 33}}},
    "retirement_liabilities_csa": {"fy2010_usd": 6917932, "fy2021_usd": 18647000, "fy2021_pension_plan_assets_usd": 23054000,
        "definition": "unfunded liability of two diocesan priests' defined-benefit plans + retired priests' post-retirement health benefits (CSA notes 12-13)", "source": REPO + "data/real/diocese/financials.json"},
    "ordinations_priests": [
        {"year": 2026, "count": 3, "date": "2026-05-23", "seminary": "St. Patrick's Seminary, Menlo Park", "first_assignments": "2 parochial vicars (one also Santa Rita Jail chaplain); 1 continuing studies at CUA", "source": f"{D}/diocese-celebrates-ordination-of-three-new-priests/"},
        {"year": 2025, "count": ">=1", "note": "Fr. Alvaro Santamaria (first Mass 2025-05-25)", "source": f"{D}/helping-connect-the-dots-in-gods-call-and-a-mans-response-to-priestly-vocations/"},
    ],
    "seminarians": {"count": None, "status": "not published in sources reviewed", "owner": "Vocations and Seminarians office (Director of Seminarians)"},
    "permanent_deacons": {"first_class": {"date": "1978-02-17", "count": 27}, "class_2025": {"date": "2025-10-11", "count": 14, "born_outside_us": 10},
        "current_total": "more than 100 (diocese)", "formation": "five years; inquiry -> aspirancy -> candidacy -> ordination; age 35-55 at application",
        "sources": [f"{D}/diaconate/", f"{D}/meet-oaklands-new-deacons/", f"{D}/father-jimmy-once-a-deacon-now-their-director/"]},
    "international_and_religious_priests": {"evidence": ["International Priest Day vespers 2026-10-15 (St. Bede, Hayward)", "Missionaries of Faith (MF) priests staff two parishes; an MF priest holds the Region 4 Presbyteral Council seat", "ethnic-community chaplains (e.g., Igbo)", "174 religious priests (Annuario 2023)"],
        "not_public": "country-of-origin or incardination breakdown; do not infer from names; never store visa/immigration status",
        "sources": [REPO + "data/real/diocese_events.json", f"{D}/vocations/", f"{D}/bishop-barber/", f"{D}/nigerian-igbo-community-celebrates-20-years-of-catholic-ecosystem-in-east-bay/"]},
}
dump("clergy.json", clergy)

# ---------------------------------------------------------------- news 2024-2026
news = [
    ("2024-12-13", "chapter 11", "Bishop issues unconditional apology to survivors", "Diocese of Oakland", f"{D}/apology/"),
    ("2025-06-09", "charity", "Catholic Charities East Bay's new CEO (since Jan 2025) profiled", "Diocese of Oakland", f"{D}/a-calling-why-mario-alioto-moved-from-san-francisco-giants-to-leading-catholic-charities-east-bay/"),
    ("2025-08-14", "chapter 11", "No settlement; sales of non-essential real estate begin", "Diocese of Oakland", f"{D}/chapter-11/"),
    ("2025-10-11", "clergy", "14 permanent deacons ordained", "Diocese of Oakland", f"{D}/meet-oaklands-new-deacons/"),
    ("2025-11-10", "immigration", "Diocese frames immigration crisis through 1 Cor 12; bishop's Aug 2025 remarks", "Diocese of Oakland", f"{D}/you-are-christs-body-a-catholic-call-to-community-amidst-the-immigration-crisis/"),
    ("2025-11-12", "communities", "Nigerian Igbo community marks 20 years", "Diocese of Oakland", f"{D}/nigerian-igbo-community-celebrates-20-years-of-catholic-ecosystem-in-east-bay/"),
    ("2025-12-12", "chapter 11", "Term sheet: $200M from diocese over five years plus >$42.5M from insurers", "Diocese of Oakland", f"{D}/chapter-11/"),
    ("2025-12-16", "works of mercy", "Regional hub for mothers in need launches at St. Francis of Assisi, Concord", "Diocese of Oakland", f"{D}/catholic-regional-hub-for-moms-with-needs-launches-in-concord/"),
    ("2025-12-17", "formation", "New, more holistic marriage preparation program for 2026", "Diocese of Oakland", f"{D}/new-more-holistic-marriage-preparation-program-launching-in-2026/"),
    ("2026-03-13", "communities", "USCCB API regional consultation hosted in Oakland (Feb 5-7)", "Diocese of Oakland", f"{D}/new-seasons-new-grounds-continues-cultivation-of-ministry-to-asian-and-pacific-island-community/"),
    ("2026-04-14", "works of mercy", "Missionaries of Charity move convent to East Oakland", "Diocese of Oakland", f"{D}/closeness-to-christs-poor-in-oakland-missionaries-of-charity-move-their-convent-to-the-city/"),
    ("2026-04-22", "chapter 11", "Diocesan statement after jury verdict (a $16M verdict per Alameda Post)", "Diocese of Oakland; Alameda Post", f"{D}/chapter-11/"),
    ("2026-04-28", "MAP", "Bishop announces closure of 13 worship sites", "Diocese of Oakland; ABC7; KTVU", f"{D}/aligning-our-reality-with-our-mission/"),
    ("2026-04-30", "MAP", "Why 13 Catholic churches are closing", "USA Today (via BishopAccountability)", "https://www.bishop-accountability.org/?p=335838"),
    ("2026-05-01", "MAP", "7 Oakland churches to close; Our Lady of Lourdes and St. Paschal reactions", "The Oaklandside (via BishopAccountability)", "https://www.bishop-accountability.org/2026/05/7-oakland-catholic-churches-to-close/"),
    ("2026-05-01", "MAP", "St. Barnabas and St. Albert the Great among closures", "Alameda Post", "https://alamedapost.com/news/st-barnabas-st-albert-great-among-dozen-parishes-closed-diocese/"),
    ("2026-05-02", "MAP", "Bishop's column: challenges and opportunities; ~10-minute maximum drive", "Diocese of Oakland", f"{D}/mission-alignment-process-challenges-opportunities/"),
    ("2026-05-03", "MAP", "St. Stephen, Walnut Creek parishioners optimistic, then stunned", "San Francisco Chronicle (via BishopAccountability)", "https://www.bishop-accountability.org/2026/05/east-bay-parishioners-were-optimistic/"),
    ("2026-05-03", "MAP", "Oakland diocese announces closure of 13 parishes", "EWTN News (via The Catholic Virginian)", "https://catholicvirginian.org/?p=49180"),
    ("2026-05-19", "MAP", "St. Patrick (West Oakland) slated for closure; appeal discussed", "Black Catholic Messenger (via BishopAccountability)", "https://www.bishop-accountability.org/2026/05/st-patrick-catholic-church-slated/"),
    ("2026-05-26", "clergy", "Three priests ordained May 23", "Diocese of Oakland", f"{D}/diocese-celebrates-ordination-of-three-new-priests/"),
    ("2026-07-09", "eucharist", "27 pilgrims at National Eucharistic Pilgrimage finale in Philadelphia", "Diocese of Oakland", f"{D}/oakland-pilgrims-mark-americas-250th-birthday-in-philly-by-encountering-christ-on-national-eucharistic-pilgrimage/"),
    ("2026-08-19", "works of mercy", "Order of Malta Clinic adds dental and eye care", "Diocese of Oakland", f"{D}/order-of-malta-clinic-expands-dental-and-eye-care-services/"),
    ("2026-08-27", "parish life", "Catholic Community of Pleasanton at 125 years (~5,700 registered families)", "Diocese of Oakland", f"{D}/a-parish-too-big-for-one-church/"),
    ("2026-09-14", "communities", "Bishop honors St. Francis and the Ohlone people at Mission San Jose", "Diocese of Oakland", f"{D}/bishop-barber-honors-st-francis-ohlone-people-at-mission-mass-aug-22/"),
    ("2026-10-02", "chapter 11", "Court denies plan confirmation; bishop calls it a setback", "Diocese of Oakland; court decision", f"{D}/chapter-11/"),
]
dump("news_2024_2026.json", {
    "dataset": "Selected news 2024-2026 about the Diocese of Oakland (curated; paraphrased headlines)",
    "note": "Reposts on bishop-accountability.org name the original outlet in 'outlet'. Chapter 11 docket-level detail: data/real/diocese/chapter11_timeline.json.",
    "count": len(news),
    "items": [{"date": d, "category": c, "headline_paraphrase": h, "outlet": o, "url": u} for d, c, h, o, u in news],
})
print("wrote", sorted(os.listdir(out_dir)))
