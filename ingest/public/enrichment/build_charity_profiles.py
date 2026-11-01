"""Build data/real/enrichment/charity_profiles.json from the charity pages fetched by charity_pages.py.

The facts below were read from the cached pages in data/raw/charities/<org>/ (fetched 2026-10-26) and
entered as structured data. Each fact names the page it came from (`src`, a key into that org's page
index). On build, each fact gets source_url + fetched_at from data/raw/charities/<org>/index.json, and
its short `evidence` phrase (<= 12 words, used only for checking) is searched for in the cached page text:
`verified: true` means the phrase was found on that page. Numbers are copied as printed.

Financial summaries come from the ProPublica Nonprofit Explorer API (IRS Form 990 data).
SVdP Contra Costa parish conferences are matched to parish ids in parishes_directory.json.

Usage: python ingest/public/enrichment/build_charity_profiles.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, ROOT, SESSION, canon_key, load_json, name_key, now_iso, save_json  # noqa: E402

CH = RAW / "charities"
OUT = REAL / "enrichment" / "charity_profiles.json"
PP = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"

# --------------------------------------------------------------------------- curated facts
# Each list item: dict(fact fields..., src=<page url>, evidence=<short phrase on that page>)
ORGS = [
    {
        "id": "catholic-charities-east-bay",
        "name": "Catholic Charities East Bay",
        "legal_name": "Catholic Charities of the Diocese of Oakland",
        "ein": "94-2677202",
        "raw_dir": "cceb",
        "website": "https://www.cceb.org/",
        "catholic_identity": "Official Catholic Charities agency of the Diocese of Oakland",
        "counties": ["Alameda", "Contra Costa"],
        "main_phone": {"value": "(510) 768-3100", "src": "https://www.cceb.org/locations/", "evidence": "(510) 768-3100"},
        "locations": [
            {"name": "Oakland Service Center", "address": "433 Jefferson Street, Oakland, CA 94607",
             "hours": "9 a.m.-5 p.m.; closed for lunch 12-1 p.m.; services by phone or appointment only",
             "src": "https://www.cceb.org/locations/", "evidence": "433 Jefferson Street"},
            {"name": "Richmond Service Center", "address": "217 Harbour Way, Richmond, CA 94801",
             "hours": "9 a.m.-5 p.m.; closed for lunch 12-1 p.m.; services by phone or appointment only",
             "src": "https://www.cceb.org/locations/", "evidence": "217 Harbour Way"},
            {"name": "Concord Service Center", "address": "2120 Diamond Blvd #220, Concord, CA 94520",
             "hours": "9 a.m.-5 p.m.; closed for lunch 12:30-1:30 p.m.; services by phone or appointment only",
             "src": "https://www.cceb.org/locations/", "evidence": "2120 Diamond Blvd #220"},
        ],
        "programs": [
            {"name": "Housing Services", "summary": "Homelessness prevention: back rent, overdue utility bills, security deposits",
             "src": "https://www.cceb.org/housing-services/", "evidence": "paying back rent, overdue utility bills"},
            {"name": "Immigration Legal Services", "summary": "Work authorization, green cards, citizenship, family petitions, humanitarian protections",
             "src": "https://www.cceb.org/immigration-legal-services/", "evidence": "Immigration Legal Services"},
            {"name": "Mental Health and Wellness", "summary": "Counseling and wellness services (see program page)",
             "src": "https://www.cceb.org/", "evidence": "Mental Health"},
            {"name": "Community clinics at parishes", "summary": "Free walk-in housing clinics and immigration legal consultations held at a parish",
             "src": "https://www.cceb.org/community-clinics/", "evidence": "Walk-Ins Welcome"},
            {"name": "Joy Bells Christmas Program", "summary": "Sponsors choose a child or family and buy and wrap Christmas gifts",
             "src": "https://www.cceb.org/joy-bells/", "evidence": "Sponsor a Child for Christmas"},
        ],
        "events": [
            {"date": d, "title": t, "location": "St. Francis of Assisi Catholic Church: Holy Family Care Center, 860 Oak Grove Rd., Concord",
             "time": "10:00 AM-12:00 PM", "parish_id": "francis-assisi-concord",
             "src": "https://www.cceb.org/community-clinics/", "evidence": "Holy Family Care Center"}
            for d, t in [("2026-11-04", "Free Housing Clinic"), ("2026-11-18", "Free Immigration Legal Consultations"),
                         ("2026-11-18", "Free Housing Clinic"), ("2026-12-02", "Free Immigration Legal Consultations"),
                         ("2026-12-02", "Free Housing Clinic"), ("2026-12-16", "Free Immigration Legal Consultations"),
                         ("2026-12-16", "Free Housing Clinic")]
        ],
        "volunteer": {
            "how_to_sign_up": "Complete the online volunteer form on the Volunteer page; a LiveScan background check (paid by Catholic Charities) is required for adult volunteers.",
            "signup_url": "https://www.cceb.org/volunteer/",
            "requirements": ["LiveScan background check (paid by CCEB)", "English fluency; Spanish, Korean or Vietnamese valued",
                             "Complete onboarding forms", "Able to volunteer at least once or twice a year"],
            "opportunities": [
                {"title": "Community Event Team (Golf Tournament, Gala, Joy Bells)", "location": "Oakland Office"},
                {"title": "Development Office Support", "location": "Oakland Office", "commitment": "6 hours per week"},
                {"title": "Administrative Volunteer, Immigration Legal Services", "location": "All locations / remote",
                 "note": "Bilingual English-Spanish"},
                {"title": "Pro Bono Attorney (immigration)", "location": "All locations; hybrid available"},
                {"title": "Legal Intern, Unaccompanied Minors Focus", "location": "Immigration Legal Services"},
                {"title": "Facilities", "location": "see page"},
            ],
            "src": "https://www.cceb.org/volunteer/", "evidence": "LiveScan background check",
        },
        "current_needs": [
            {"need": "Joy Bells: sponsor a child or family for Christmas; drop off wrapped gifts by Monday, Nov. 30, 4 PM at the Richmond office (Mon-Thu 10 AM-4 PM)",
             "deadline": "2026-11-30", "src": "https://www.cceb.org/joy-bells/", "evidence": "Drop off gifts by Monday, November 30"},
            {"need": "Monetary gifts (online, mail to 433 Jefferson St., vehicles, stock)", "src": "https://www.cceb.org/donate/",
             "evidence": "Donate Your Vehicle"},
        ],
        "impact": [
            {"metric": "calls for help", "value": 28637, "period": "2025", "src": "https://www.cceb.org/our-impact/", "evidence": "28,637"},
            {"metric": "food assistance and gift cards (USD)", "value": 29190, "period": "2025", "src": "https://www.cceb.org/our-impact/", "evidence": "$29,190"},
            {"metric": "housing financial assistance (USD)", "value": 762670, "period": "2025", "src": "https://www.cceb.org/our-impact/", "evidence": "$762,670"},
        ],
        "transparency": [
            {"fact": "Candid Platinum Seal of Transparency for 2026", "src": "https://www.cceb.org/donate/", "evidence": "Platinum Seal of Transparency for 2026"},
            {"fact": "Form 990s FY2009-FY2025 and audited statements FY2015-FY2025 posted", "src": "https://www.cceb.org/about-us/financials/",
             "evidence": "2024-2025 IRS Form 990"},
        ],
        "notes": [
            {"text": "The Locations page contains injected unrelated spam text (likely a compromised page); addresses unaffected.",
             "src": "https://www.cceb.org/locations/", "evidence": "Hungarian GP"},
            {"text": "The clinic venue ZIP is printed as 64518 (Concord is 94518).", "src": "https://www.cceb.org/community-clinics/",
             "evidence": "Concord, CA 64518"},
        ],
        "related_files": ["data/real/cceb_programs.json", "data/real/cceb_990.json"],
    },
    {
        "id": "svdp-alameda",
        "name": "Society of St. Vincent de Paul of Alameda County",
        "ein": "94-1156493",
        "raw_dir": "svdp-alameda",
        "website": "https://www.svdp-alameda.org/",
        "catholic_identity": "Vincentian (Catholic lay) organization; council of parish-based conferences",
        "counties": ["Alameda"],
        "main_phone": {"value": "510-638-7600", "src": "https://www.svdp-alameda.org/contact--connect.html", "evidence": "510-638-7600"},
        "locations": [
            {"name": "Community Center (headquarters)", "address": "2272 San Pablo Avenue, Oakland, CA 94612",
             "src": "https://www.svdp-alameda.org/contact--connect.html", "evidence": "2272 San Pablo Avenue"},
            {"name": "Free Dining Room", "address": "675 23rd Street, Oakland, CA 94612",
             "src": "https://www.svdp-alameda.org/contact--connect.html", "evidence": "675 23rd Street"},
            {"name": "Thrift stores", "address": "Fremont and Livermore (addresses on store page)",
             "src": "https://www.svdp-alameda.org/volunteer.html", "evidence": "Fremont or Livermore Thrift Stores"},
        ],
        "programs": [
            {"name": "Free Dining Room (meals)", "summary": "Free lunches Tuesday-Saturday; site says 4,000+ meals served each week",
             "src": "https://www.svdp-alameda.org/what-we-do.html", "evidence": "more than 4,000 meals served each week"},
            {"name": "Emergency shelter", "summary": "65 men and women nightly with City of Oakland support; opens 6:00 pm, closes 8:00 am",
             "src": "https://www.svdp-alameda.org/what-we-do.html", "evidence": "emergency shelter to 65 men and women each night"},
            {"name": "Drop-in centers", "summary": "Showers, laundry, hygiene products, diapers and clothing",
             "src": "https://www.svdp-alameda.org/what-we-do.html", "evidence": "showers, laundry, hygiene products"},
            {"name": "Food locker", "summary": "Donated and rescued grocery food used for meals and guest food items",
             "src": "https://www.svdp-alameda.org/what-we-do.html", "evidence": "food locker"},
            {"name": "Kitchen of Champions workforce development", "summary": "Nine-week cohort culinary job training; apply online or at 675 23rd St.",
             "src": "https://www.svdp-alameda.org/what-we-do.html", "evidence": "nine-week period"},
            {"name": "Parish conferences (Vincentians)", "summary": "800+ Vincentian volunteers in 37 parishes: eviction prevention, utilities, medical costs, clothing, furniture, pantries",
             "src": "https://www.svdp-alameda.org/", "evidence": "across 37 parishes in Alameda County"},
        ],
        "volunteer": {
            "how_to_sign_up": "Apply online on the Volunteer page; groups should contact the Volunteer Team at least one month ahead.",
            "signup_url": "https://www.svdp-alameda.org/volunteer.html",
            "shifts": ["Community Center 9-12 and 12:30-3", "Dining Room 8:30-1"],
            "who": ["Individuals", "Groups (Vincentian conferences, community and corporate)", "Students 14+ (waiver under 18; adult escort under 16)",
                    "Thrift store volunteers in Fremont or Livermore", "Communications and development volunteers (Oakland campus)"],
            "src": "https://www.svdp-alameda.org/volunteer.html", "evidence": "Dining Room Shifts: 8:30-1",
        },
        "current_needs": [
            {"need": "Items from the agency's Amazon registry", "url": "https://www.amazon.com/registries/custom/2XH1GZPH4D1S9/guest-view",
             "src": "https://www.svdp-alameda.org/", "evidence": "purchasing items of need from our registry"},
            {"need": "Thrift store donations (clothing, shoes, housewares, sturdy furniture, books; see accepted/not-accepted lists)",
             "src": "https://www.svdp-alameda.org/what-to-donate-thrift-stores.html", "evidence": "We accept clothes, shoes, accessories"},
            {"need": "Communications (copywriting, Canva) and development (sponsor outreach, in-kind, events) volunteers",
             "src": "https://www.svdp-alameda.org/volunteer-in-oakland.html", "evidence": "Communications Volunteers"},
        ],
        "impact": [
            {"metric": "people who volunteered in the past year", "value": 2400, "qualifier": "over", "period": "past year (as stated on page)",
             "src": "https://www.svdp-alameda.org/volunteer.html", "evidence": "Over 2,400 people came to SVdP"},
            {"metric": "volunteer hours per year", "value": 100000, "qualifier": "over", "period": "annual (as stated)",
             "src": "https://www.svdp-alameda.org/volunteer.html", "evidence": "over one hundred thousand hours every year"},
            {"metric": "meals per week", "value": 4000, "qualifier": "more than", "period": "current (as stated)",
             "src": "https://www.svdp-alameda.org/what-we-do.html", "evidence": "more than 4,000 meals served each week"},
            {"metric": "people assisted by parish conferences per year", "value": 100000, "qualifier": "over", "period": "annual (as stated)",
             "src": "https://www.svdp-alameda.org/", "evidence": "assistance for over 100,000 people a year"},
            {"metric": "donated food in operating budget (USD)", "value": 1200000, "qualifier": "more than",
             "src": "https://www.svdp-alameda.org/annual-report-and-financials.html", "evidence": "more than $1.2 million dollars in donated food"},
        ],
        "transparency": [
            {"fact": "Form 990s 2016-2025 and FY2018/FY2019 annual reports posted", "src": "https://www.svdp-alameda.org/annual-report-and-financials.html",
             "evidence": "2025_form_990_filing.pdf"},
        ],
        "notes": [{"text": "Several pages (store volunteering, Christmas event) returned HTTP 429 to the collector; not read.",
                   "src": "https://www.svdp-alameda.org/", "evidence": "St. Vincent de Paul"}],
    },
    {
        "id": "svdp-contra-costa",
        "name": "Society of St. Vincent de Paul of Contra Costa County",
        "legal_name": "District Council Contra Costa County Society of St. Vincent de Paul",
        "ein": "94-1448577",
        "raw_dir": "svdp-cc",
        "website": "https://www.svdp-cc.org/",
        "catholic_identity": "Catholic lay organization (Vincentian); 28 parish-based conferences",
        "counties": ["Contra Costa"],
        "main_phone": {"value": "(925) 439-5060", "src": "https://www.svdp-cc.org/contact/", "evidence": "(925) 439-5060"},
        "locations": [
            {"name": "Family Resource Center (main office)", "address": "2210 Gladstone Dr., Pittsburg, CA 94565",
             "hours": "Office Mon-Fri 8:30am-5:00pm", "src": "https://www.svdp-cc.org/contact/", "evidence": "Monday – Friday, 8:30am – 5:00pm"},
            {"name": "Pittsburg Thrift Store", "address": "2210 Gladstone Dr., Pittsburg, CA 94565",
             "hours": "Mon-Sat 9am-5pm; donations Mon-Wed & Fri-Sat 9-4:30", "src": "https://www.svdp-cc.org/donategoods/", "evidence": "Monday-Saturday: 9:00am-5:00pm"},
            {"name": "Pleasant Hill Thrift Store", "address": "2815 Contra Costa Blvd., Pleasant Hill, CA 94523",
             "hours": "7 days 10am-6pm; donations Sun-Tue & Thu-Sat 10-5", "src": "https://www.svdp-cc.org/donategoods/", "evidence": "2815 Contra Costa Boulevard"},
            {"name": "Brentwood Help & Hope Thrift Center", "address": "8890 Brentwood Blvd., Brentwood, CA 94513",
             "hours": "7 days 10am-6pm; donations Sun-Mon & Wed-Sat 10-5", "src": "https://www.svdp-cc.org/donategoods/", "evidence": "8890 Brentwood Blvd"},
        ],
        "programs": [
            {"name": "Free Dining Room (with Loaves & Fishes of Contra Costa)", "summary": "Hot meal at the Pittsburg Family Resource Center; about 40,000 meals a year, 180-200 a day",
             "schedule_conflict": "Dining-room page (updated 2021) says 7 days 11:00-12:45; Programs page (updated 2025) says every weekday",
             "src": "https://www.svdp-cc.org/programs-dining-room/", "evidence": "about 40,000 meals annually"},
            {"name": "RotaCare Pittsburg Free Medical Clinic", "summary": "Free care for uninsured adults 17+; Wednesdays 4-8 pm; ~18,000 patients since 2011",
             "src": "https://www.svdp-cc.org/programs-rotacare-clinic/", "evidence": "Wednesdays, 4:00 – 8:00 pm"},
            {"name": "DOW Free Dental Clinic (Dentists on Wheels)", "summary": "Dental care for uninsured adults with severe dental issues",
             "src": "https://www.svdp-cc.org/programs/", "evidence": "Dentists on Wheels"},
            {"name": "John Muir Mobile Health Clinic", "summary": "First Wednesday of each month, 9am-noon, at SVdP",
             "src": "https://www.svdp-cc.org/programs/", "evidence": "first Wednesday of each month from 9am to noon"},
            {"name": "Emergency food (with Food Bank of Contra Costa and Solano)", "summary": "One-time emergency food Monday-Friday",
             "src": "https://www.svdp-cc.org/programs/", "evidence": "one-time emergency food"},
            {"name": "Oasis Daytime Shelter (with Winter Nights)", "summary": "Daytime shelter for homeless families",
             "src": "https://www.svdp-cc.org/programs/", "evidence": "Oasis Daytime Shelter"},
            {"name": "Saturday shower trailer (with Bay Church and Loaves & Fishes)", "summary": "Saturdays 10am-1pm: shower, clothes from thrift store, hot lunch",
             "src": "https://www.svdp-cc.org/programs/", "evidence": "shower trailer"},
            {"name": "Workforce Development Program", "summary": "24-week training and work experience for people with barriers to employment",
             "src": "https://www.svdp-cc.org/programs/", "evidence": "24-week SVdP WFD Program"},
            {"name": "Parish conference home visits and assistance", "summary": "Food, clothing, furniture, gas, utility/rental help through 28 branches (27 in parishes)",
             "src": "https://www.svdp-cc.org/branches/", "evidence": "27 of which are located in Catholic Parishes"},
        ],
        "volunteer": {
            "how_to_sign_up": "Complete the SVdP Volunteer Application and bring or email it to the Volunteer Coordinator; or join your parish conference.",
            "signup_url": "https://www.svdp-cc.org/volunteer/",
            "opportunities": [
                {"title": "Family Resource Center food pantry / general", "schedule": "3-4 hour shifts Mon-Fri 9:30-4:30 (Pittsburg)"},
                {"title": "Thrift store roles", "detail": "Greeter, jewelry sorter, electronics tester, clothing & shoe sorter, linen sorter, pricer, donation attendant, furniture repair"},
                {"title": "RotaCare clinic medical professionals, social workers, translators", "schedule": "Wednesdays 4-8 pm; bilingual help especially needed"},
                {"title": "Workforce Development mentor; business-to-business job development"},
                {"title": "Communications/marketing expertise; thrift store management assistance"},
                {"title": "Dining room service (through Loaves & Fishes of Contra Costa)"},
            ],
            "src": "https://www.svdp-cc.org/volunteer/", "evidence": "Shifts of 3-4 hours are available Monday-Friday",
        },
        "current_needs": [
            {"need": "Volunteer physicians, nurses, NPs, PAs, MAs, LVNs, EMTs, dieticians, translators for RotaCare clinic",
             "src": "https://www.svdp-cc.org/programs-rotacare-clinic/", "evidence": "continues to actively recruit volunteer physicians"},
            {"need": "Household goods, clothing, furniture pick-ups; vehicle donations", "src": "https://www.svdp-cc.org/", "evidence": "We also accept vehicle donations"},
            {"need": "Donations to dining room help at month's end when demand rises", "src": "https://www.svdp-cc.org/programs-dining-room/",
             "evidence": "significant increase in meals served towards the end of the month"},
        ],
        "impact": [
            {"metric": "free goods provided through store vouchers (USD)", "value": 70000, "qualifier": "over", "period": "last year (as stated; page updated 2024-11)",
             "src": "https://www.svdp-cc.org/donategoods/", "evidence": "over $70,000 in free goods"},
            {"metric": "uninsured patients served by RotaCare clinic since 2011", "value": 18000, "qualifier": "approximately",
             "src": "https://www.svdp-cc.org/programs-rotacare-clinic/", "evidence": "18"},
            {"metric": "meals per year (dining room)", "value": 40000, "qualifier": "about", "src": "https://www.svdp-cc.org/programs-dining-room/",
             "evidence": "about 40,000 meals annually"},
            {"metric": "volunteers", "value": 1000, "qualifier": "more than", "src": "https://www.svdp-cc.org/volunteer/",
             "evidence": "More than 1,000 volunteers"},
            {"metric": "people served (direct and in-kind)", "value": 81000, "period": "undated fiscal year (page last modified 2022-08)",
             "src": "https://www.svdp-cc.org/annual-reports/", "evidence": "81,000 people served"},
            {"metric": "direct financial assistance, mostly rent (USD)", "value": 970000, "qualifier": "over",
             "period": "undated fiscal year (page last modified 2022-08)", "src": "https://www.svdp-cc.org/annual-reports/",
             "evidence": "Over $970,000 of direct financial assistance"},
        ],
        "transparency": [{"fact": "Latest annual report posted: 2019/2020", "src": "https://www.svdp-cc.org/annual-reports/",
                          "evidence": "Annual Report – 2019/2020"}],
    },
    {
        "id": "st-marys-center",
        "name": "St. Mary's Center",
        "ein": "68-0172229",
        "raw_dir": "st-marys-center",
        "website": "https://stmaryscenter.org/",
        "catholic_identity": "Grew out of St. Mary's Catholic Church, West Oakland, and programs begun by the Sisters of Social Service (1939); independent 501(c)(3) since 1992",
        "counties": ["Alameda"],
        "main_phone": {"value": "510-923-9600", "src": "https://stmaryscenter.org/", "evidence": "510-923-9600"},
        "locations": [
            {"name": "Community Center / headquarters", "address": "925 Brockhurst Street, Oakland, CA",
             "hours": "Mon-Fri 9:30am-1:00pm; Sun 11am-2pm; closed Sat", "src": "https://stmaryscenter.org/community-outreach/",
             "evidence": "MONDAY-FRIDAY (Open 9:30am – 1:00pm)"},
            {"name": "Senior Housing Services walk-in", "address": "925 Brockhurst St., Oakland",
             "hours": "Walk-in 10-2 Mon-Fri (closed first Friday); Housing Clinic Mon & Wed 10:30-12", "src": "https://stmaryscenter.org/senior-housing-services/",
             "evidence": "Walk in hours are from 10-2 PM Monday-Friday"},
            {"name": "Preschool", "address": "West Oakland site (see page)", "hours": "Mon-Fri 8:00am-5:30pm",
             "src": "https://stmaryscenter.org/about-our-preschool/", "evidence": "Monday – Friday, 8:00am – 5:30pm"},
        ],
        "programs": [
            {"name": "Senior Community Center", "summary": "Breakfast and $1-voluntary lunch, wellness, cultural events, computer literacy for seniors",
             "src": "https://stmaryscenter.org/community-outreach/", "evidence": "Lunch served for $1 voluntary contribution"},
            {"name": "Senior Housing Services", "summary": "Case management, housing navigation, Coordinated Entry, counseling for seniors 55+",
             "src": "https://stmaryscenter.org/senior-housing-services/", "evidence": "Adults 55 years and older"},
            {"name": "Transitional housing", "summary": "24 units for seniors 55+ in three houses (Closer to Home, Presentation House, Friendly Manor)",
             "src": "https://stmaryscenter.org/transitional-housing/", "evidence": "24 units of transitional housing"},
            {"name": "3135 San Pablo Ave affordable senior housing (with SAHA)", "summary": "Planned ~73 apartment homes for seniors",
             "src": "https://stmaryscenter.org/transitional-housing/", "evidence": "approximately 73 apartment homes"},
            {"name": "Full-day Preschool", "summary": "3- and 4-year-olds; QRIS Five Star; state-certified",
             "src": "https://stmaryscenter.org/about-our-preschool/", "evidence": "QRIS Five Star rated"},
            {"name": "Sunday Dinner Program", "summary": "Volunteer groups serve a Sunday meal for seniors at 925 Brockhurst St.",
             "src": "https://stmaryscenter.org/volunteer/", "evidence": "Sunday Dinner Program"},
        ],
        "volunteer": {
            "how_to_sign_up": "Submit the volunteer application; groups sign up for Sunday Dinner via the linked calendar; email the volunteer coordinator.",
            "signup_url": "https://stmaryscenter.org/volunteer/",
            "opportunities": [{"title": "Sunday Dinner for seniors (groups)"}, {"title": "Group projects: painting, repairs, gardening (2-3 weeks notice)"},
                              {"title": "Internships (social work, policy, organizing, early childhood)"}, {"title": "Preschool classroom (background check)"}],
            "src": "https://stmaryscenter.org/volunteer/", "evidence": "more than 1,000 volunteers",
        },
        "current_needs": [
            {"need": "Wish list food items (coffee, cereal, nutritional drinks, oatmeal, peanut butter, canned meats and fish, etc.); drop-off 11am-1pm weekdays and Sunday",
             "src": "https://stmaryscenter.org/wish-list/", "evidence": "Drop-offs are accepted 11am-1pm on weekdays and Sunday"},
            {"need": "Groups to provide and serve meals for seniors", "src": "https://stmaryscenter.org/community-outreach/",
             "evidence": "Sign up to help provide meals for Seniors"},
        ],
        "impact": [
            {"metric": "volunteers per year", "value": 1000, "qualifier": "more than", "src": "https://stmaryscenter.org/volunteer/",
             "evidence": "more than 1,000 volunteers"},
            {"metric": "seniors housed in 2024 still housed a year later (%)", "value": 92.5, "period": "2024 cohort, surveyed 2025-26",
             "src": "https://stmaryscenter.org/senior-housing-services/", "evidence": "92.5% are still housed"},
            {"metric": "transitional housing units", "value": 24, "src": "https://stmaryscenter.org/transitional-housing/", "evidence": "24 units"},
        ],
        "transparency": [{"fact": "Impact Report 2025; audits and Form 990s 2021-2025 posted", "src": "https://stmaryscenter.org/publications-financials/",
                          "evidence": "Impact Report 2025"}],
        "parish_links": [{"parish": "St. Mary's Catholic Church, West Oakland (historic roots; not in the 75-parish directory)",
                          "src": "https://stmaryscenter.org/mission-and-history/", "evidence": "St. Mary’s Catholic Church near 7th and Jefferson"}],
    },
    {
        "id": "oakland-catholic-worker",
        "name": "Oakland Catholic Worker",
        "raw_dir": "oakland-catholic-worker",
        "website": "https://oaklandcatholicworker.org/",
        "catholic_identity": "Catholic Worker house (lay Catholic movement founded by Dorothy Day)",
        "counties": ["Alameda"],
        "main_phone": {"value": "(510) 533-7375", "src": "https://oaklandcatholicworker.org/contact-us/", "evidence": "(510) 533-7375"},
        "programs": [
            {"name": "Transitional housing for unhoused immigrant families", "summary": "Two shelters; families stay six months to a year",
             "src": "https://oaklandcatholicworker.org/our-work/", "evidence": "two transitional housing shelters"},
            {"name": "Food Security Program", "summary": "Thursday food bank for 400+ people a week; hot lunch twice a week for 200+",
             "src": "https://oaklandcatholicworker.org/our-work/", "evidence": "serves over 400 people a week"},
            {"name": "Job training and placement; case management", "summary": "Work orientation and support finding justly paid employment",
             "src": "https://oaklandcatholicworker.org/our-work/", "evidence": "Job Training and Placement"},
            {"name": "Partner services", "summary": "Medical/dental, counseling, ESL, legal help, literacy, leadership training, immigrant-rights organizing",
             "src": "https://oaklandcatholicworker.org/our-work/", "evidence": "ENGLISH AS A SECOND LANGUAGE CLASSES"},
        ],
        "volunteer": {
            "how_to_sign_up": "Use the Volunteer Now form on the volunteer page or call the office.",
            "signup_url": "https://oaklandcatholicworker.org/volunteer",
            "opportunities": [{"title": t} for t in ["ESL teaching/tutoring", "Basic literacy tutoring", "Social media", "Women's support group",
                                                     "Work with children (tutoring, art, field trips)", "Bookkeeping", "Website support",
                                                     "Job-skills teaching and job finding", "Men's support group", "Office help", "Fundraising"]],
            "src": "https://oaklandcatholicworker.org/volunteer", "evidence": "Teach, tutor English as a second language",
        },
        "current_needs": [{"need": "Financial gifts for housing, food program and services", "src": "https://oaklandcatholicworker.org/donate/",
                           "evidence": "critically needed now more than ever"}],
        "impact": [
            {"metric": "people served weekly by Thursday food bank", "value": 400, "qualifier": "over", "src": "https://oaklandcatholicworker.org/our-work/",
             "evidence": "over 400 people a week"},
            {"metric": "people served by twice-weekly hot lunch", "value": 200, "qualifier": "over", "src": "https://oaklandcatholicworker.org/our-work/",
             "evidence": "over 200 people"},
        ],
        "parish_links": [{"parish": "Bishop O'Dowd High School hosted the Dec. 6, 2025 benefit with Fr. Gregory Boyle, S.J.",
                          "src": "https://oaklandcatholicworker.org/", "evidence": "Bishop O’Dowd for hosting us"}],
    },
    {
        "id": "saint-vincents-day-home",
        "name": "Saint Vincent's Day Home",
        "ein": "94-2195766",
        "raw_dir": "svdh",
        "website": "https://svdh.org/",
        "catholic_identity": "Founded 1911 by the Sisters of the Holy Family; now an independent, non-sectarian nonprofit (FAQ: no religious affiliation)",
        "counties": ["Alameda", "Contra Costa"],
        "main_phone": {"value": "(510) 832-8324", "src": "https://svdh.org/contact-us/", "evidence": "(510) 832-8324"},
        "locations": [{"name": "Day Home", "address": "1086 Eighth Street, Oakland, CA 94607", "hours": "Mon-Fri 7:30am-5:30pm, year-round",
                       "src": "https://svdh.org/", "evidence": "Monday – Friday 7:30am to 5:30pm"}],
        "programs": [
            {"name": "Early learning (ages 2-6, licensed kindergarten)", "summary": "HighScope curriculum; state-subsidized sliding scale",
             "src": "https://svdh.org/learning/programs-services/", "evidence": "HighScope curriculum"},
            {"name": "Family Services", "summary": "Counseling, parent education, referrals, practical crisis help (food, clothing)",
             "src": "https://svdh.org/learning/programs-services/", "evidence": "Family Services"},
        ],
        "current_needs": [{"need": "Donations (online, payroll giving, matching, planned gifts, stock)", "src": "https://svdh.org/get-involved/ways-to-give/",
                           "evidence": "Payroll Giving"}],
        "impact": [
            {"metric": "capacity (children)", "value": 220, "src": "https://svdh.org/contact-us/faq/", "evidence": "capacity of 220 children"},
            {"metric": "children who attend at no cost (%)", "value": 96, "src": "https://svdh.org/get-involved/ways-to-give/", "evidence": "96% of children attend at no cost"},
            {"metric": "children served since 1911", "value": 40000, "qualifier": "more than", "src": "https://svdh.org/about-us/history/",
             "evidence": "more than 40,000 children"},
        ],
    },
]

SVDP_CC_BRANCHES = [  # (city, parish name as printed); parsed from https://www.svdp-cc.org/branches/
    ("Antioch", "Most Holy Rosary"), ("Bay Point", "Our Lady Queen of the World"), ("Brentwood", "Immaculate Heart"),
    ("Byron", "St. Anne"), ("Concord", "St. Agnes"), ("Concord", "Queen of All Saints"), ("Concord", "St. Bonaventure"),
    ("Concord", "St. Francis of Assisi"), ("Danville", "St. Isidore"), ("El Cerrito", "St. Jerome"),
    ("El Cerrito", "St. John the Baptist"), ("El Sobrante", "St. Callistus"), ("Martinez", "St. Catherine of Siena"),
    ("Oakley", "St. Anthony"), ("Pinole", "St. Joseph"), ("Pittsburg", "Good Shepherd"), ("Pittsburg", "St. Peter Martyr"),
    ("Pleasant Hill", "Christ the King – St. Stephen"), ("Richmond", "St. David of Wales"), ("Point Richmond", "Our Lady of Mercy"),
    ("Rodeo", "St. Patrick"), ("San Pablo", "St. Paul"), ("San Ramon", "St. Joan of Arc"),
    ("San Ramon", "Vincentians of the Arc (Youth & Young Adult) at St. Joan of Arc"),
    ("Walnut Creek", "St. John Vianney"), ("Walnut Creek", "St. Mary"),
]


ALIASES = {("Immaculate Heart", "Brentwood"): "immaculate-heart-mary-brentwood",
           ("St. David of Wales", "Richmond"): "david-richmond"}


def page_index(raw_dir: str) -> dict[str, dict]:
    idx = load_json(CH / raw_dir / "index.json", {"pages": []})
    out = {}
    for p in idx["pages"]:
        for u in {p.get("url"), p.get("final_url")}:
            if u:
                out[u.rstrip("/")] = p
    # pages fetched one-off (x_*.txt) are recorded with a .meta timestamp
    for t in (CH / raw_dir).glob("x_*.txt"):
        meta = t.with_suffix(".meta")
        name = t.stem[2:].replace("_html", ".html").replace("_", "-")
        out.setdefault(f"https://www.{raw_dir}.org/{name}".rstrip("/"),
                       {"text_file": str(t.relative_to(ROOT)), "fetched_at": meta.read_text().strip() if meta.exists() else None})
    return out


def norm_txt(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("–", "-").replace("—", "-")).lower()


def attach(fact: dict, idx: dict, raw_dir: str) -> dict:
    src = fact.pop("src")
    ev = fact.pop("evidence", None)
    p = idx.get(src.rstrip("/")) or {}
    out = dict(fact)
    out["source_url"] = src
    out["fetched_at"] = p.get("fetched_at")
    verified = None
    tf = p.get("text_file")
    if not tf:  # one-off fetches: find by URL tail
        tail = re.sub(r"[^A-Za-z0-9]+", "_", src.rstrip("/").split("/")[-1])
        cand = list((CH / raw_dir).glob(f"x_{tail}*.txt"))
        if cand:
            tf = str(cand[0].relative_to(ROOT))
            meta = cand[0].with_suffix(".meta")
            out["fetched_at"] = out["fetched_at"] or (meta.read_text().strip() if meta.exists() else None)
    if ev and tf and (ROOT / tf).exists():
        html = (ROOT / tf).with_suffix(".html")
        hay = norm_txt((ROOT / tf).read_text(encoding="utf-8", errors="ignore"))
        if html.exists():
            hay += " " + norm_txt(re.sub(r"<[^>]+>", " ", html.read_text(encoding="utf-8", errors="ignore")))
        verified = norm_txt(ev) in hay
    out["evidence"] = ev
    out["verified"] = verified
    return out


def walk(obj, idx, raw_dir):
    if isinstance(obj, dict):
        if "src" in obj:
            obj = attach(dict(obj), idx, raw_dir)
        return {k: walk(v, idx, raw_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, idx, raw_dir) for v in obj]
    return obj


def propublica(ein: str) -> dict | None:
    e = ein.replace("-", "")
    try:
        r = SESSION.get(PP.format(ein=e), timeout=30)
    except Exception:  # noqa: BLE001
        return None
    if not r.ok:
        return None
    j = r.json()
    rows = []
    for f in j.get("filings_with_data", [])[:4]:
        rows.append({"tax_period": f.get("tax_prd"), "total_revenue": f.get("totrevenue"),
                     "total_expenses": f.get("totfuncexpns"), "contributions": f.get("totcntrbgfts"),
                     "total_assets_eoy": f.get("totassetsend"), "pdf_url": f.get("pdf_url")})
    return {"source_url": PP.format(ein=e), "fetched_at": now_iso(),
            "organization_name": j.get("organization", {}).get("name"), "filings": rows}


def main() -> None:
    directory = load_json(REAL / "parishes_directory.json")["records"]
    by_key = {canon_key(r["name"], r["city"]): r["id"] for r in directory}
    by_name = {}
    for r in directory:
        by_name.setdefault(name_key(r["name"]), []).append(r["id"])
    out = []
    for org in ORGS:
        o = {k: v for k, v in org.items() if k != "raw_dir"}
        idx = page_index(org["raw_dir"])
        o = walk(o, idx, org["raw_dir"])
        if org.get("ein"):
            o["form_990"] = propublica(org["ein"])
            time.sleep(1.0)
        out.append(o)
    # SVdP Contra Costa: parish conferences -> parish ids
    cc = next(o for o in out if o["id"] == "svdp-contra-costa")
    confs = []
    for city, nm in SVDP_CC_BRANCHES:
        base = re.sub(r"\s*\(.*|\s+at\s+.*", "", nm) if "Vincentians" not in nm else "St. Joan of Arc"
        pid = ALIASES.get((base, city)) or by_key.get(canon_key(base, city))
        if not pid:
            ids = [i for i in by_name.get(name_key(base), []) if city.lower().split()[-1] in i]
            pid = ids[0] if len(ids) == 1 else None
        confs.append({"city": city, "conference": nm, "parish_id": pid,
                      "note": None if pid else ("parish/mission not in the 75-parish diocesan directory snapshot; "
                                                "this SVdP listing is evidence the community is active"),
                      "source_url": "https://www.svdp-cc.org/branches/",
                      "fetched_at": (page_index("svdp-cc").get("https://www.svdp-cc.org/branches") or {}).get("fetched_at")})
    cc["parish_conferences"] = confs
    save_json(OUT, {"generated_at": now_iso(), "count": len(out),
                    "method": "Curated from cached public pages (data/raw/charities/); evidence phrases checked against page text; "
                              "Form 990 summaries from ProPublica Nonprofit Explorer API.",
                    "privacy": "Staff contact names/emails are not stored; use the source pages.",
                    "records": out})
    n_f = sum(1 for _ in re.finditer(r'"verified": (true|false)', OUT.read_text()))
    n_t = len(re.findall(r'"verified": true', OUT.read_text()))
    print(f"orgs={len(out)} facts={n_f} verified={n_t} conferences={len(confs)} "
          f"mapped={sum(1 for c in confs if c['parish_id'])}")
    for o in out:
        bad = re.findall(r'"evidence": "([^"]+)",\s*"verified": false', __import__("json").dumps(o, indent=1))
        if bad:
            print("  unverified", o["id"], bad[:6])


if __name__ == "__main__":
    main()
