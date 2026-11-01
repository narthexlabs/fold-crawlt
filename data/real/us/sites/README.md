# US parish websites (`data/real/us/sites/`)

Lane G4 of docs/HANDOFF.md: California's parish-website crawl (data/real/california/sites/) run for every US diocese. Files only; nothing here is in Supabase. Code: `ingest/us/sites/run_state.py` (list + crawl + parse), which imports `ingest/california/sites/fetch_site.py` (crawler) and `run_diocese.py` / `parse_site.py` / `events.py` (parser, templates, calendar feeds, privacy screen) at run time, so lane A's template work applies here automatically.

## Sources of the website list

1. The diocese's own directory where it is machine-readable: `data/real/us/dioceses/<slug>/parishes.json`, written by `ingest/us/dioceses/directory_probe.py` (status per see in `data/real/us/dioceses/directory_status.json`). Primary.
2. DiscoverMass church pages, `data/real/us/discovermass/<ST>.websites.json` (lane G3). Fallback; each website is assigned to a diocese by the DiscoverMass diocese line (unmatched lines go to `unassigned-<st>`).
Sites are keyed by domain (one site may serve a parish and its missions). Not crawled: social-media pages (kept as `social_only` with the link), the dioceses' own sites, LPi-hosted pages (parishesonline.com) and hosts that read as schools.

## Rules kept

- Honest user agent `FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`; robots.txt Disallow and Crawl-delay honoured; one request at a time per host, at least 1 s apart; up to 24 different hosts at once; TLS verified; no cookies, no JavaScript.
- A challenge (Cloudflare, Sucuri, Imperva, captcha) is recorded as `challenge`; 403/429 as `blocked_403`/`blocked_429`. Never retried harder, solved or worked around.
- If our own network refuses a request (the sandbox proxy answers 403 to CONNECT), nothing is recorded for that site and the chunk stops; it is not a fact about the parish.
- Privacy: the same screens as California (`ingest/public/privacy.py` screen + contact sweep on every record; calendar events through `ingest/connectors/calendars/privacy.py` and the shared detector). No names, phones or e-mails of private people.

## Files

Same shape as `data/real/california/sites/` (see its README): `<diocese>/<domain>.json` per site, `<diocese>/summary.json`, and `progress.json` (per-diocese counts and the log of chunks).

## Resume

From a clean clone: `bash scripts/us/sites_chunk.sh` (pull, 100 minutes over the next unfinished dioceses, commit, push). By hand: `python3 -I ingest/us/sites/run_state.py chunk --minutes 100`. Directory probe: `python3 -I ingest/us/dioceses/directory_probe.py`.

## Status per diocese

| diocese | sites in list | records | ok | challenge | blocked | dead | social-only |
|---|---:|---:|---:|---:|---:|---:|---:|
| unassigned-pa | 570 | 21 | 18 | 0 | 2 | 1 | 0 |
| unassigned-ca | 495 | 56 | 36 | 8 | 1 | 11 | 0 |
| archdiocese-of-chicago | 476 | 469 | 238 | 37 | 9 | 149 | 2 |
| archdiocese-of-new-york | 391 | 393 | 87 | 253 | 2 | 51 | 0 |
| unassigned-nm | 259 | 263 | 163 | 50 | 4 | 46 | 0 |
| archdiocese-of-boston | 257 | 257 | 81 | 11 | 2 | 123 | 0 |
| diocese-of-brooklyn | 241 | 240 | 179 | 14 | 2 | 41 | 4 |
| unassigned-oh | 217 | 107 | 57 | 22 | 2 | 26 | 0 |
| diocese-of-cleveland | 214 | 250 | 145 | 49 | 1 | 55 | 0 |
| unassigned-mo | 213 | 215 | 144 | 25 | 2 | 44 | 0 |
| archdiocese-of-galveston-houston | 209 | 209 | 71 | 97 | 1 | 40 | 0 |
| unassigned-ne | 203 | 214 | 152 | 20 | 8 | 34 | 0 |
| diocese-of-buffalo | 201 | 201 | 110 | 21 | 3 | 67 | 0 |
| archdiocese-of-newark | 199 | 202 | 138 | 46 | 1 | 17 | 0 |
| archdiocese-of-st-paul-and-minneapolis | 183 | 183 | 112 | 55 | 0 | 16 | 0 |
| archdiocese-of-baltimore | 182 | 182 | 83 | 8 | 1 | 53 | 0 |
| archdiocese-of-washington | 175 | 176 | 35 | 132 | 0 | 9 | 0 |
| diocese-of-albany | 169 | 173 | 92 | 26 | 4 | 50 | 1 |
| archdiocese-of-san-antonio | 168 | 168 | 75 | 48 | 2 | 34 | 9 |
| diocese-of-rockville-centre | 167 | 167 | 112 | 14 | 9 | 32 | 0 |
| unassigned-co | 159 | 180 | 106 | 20 | 7 | 47 | 0 |
| archdiocese-of-portland-in-oregon | 157 | 157 | 55 | 69 | 2 | 31 | 0 |
| unassigned-az | 154 | 173 | 117 | 39 | 2 | 15 | 0 |
| archdiocese-of-milwaukee | 145 | 146 | 116 | 18 | 2 | 10 | 0 |
| diocese-of-austin | 145 | 146 | 31 | 86 | 0 | 28 | 1 |
| archdiocese-of-st-louis | 144 | 144 | 83 | 50 | 0 | 11 | 0 |
| archdiocese-of-denver | 142 | 142 | 105 | 5 | 0 | 28 | 0 |
| unassigned-ks | 140 | 157 | 89 | 33 | 12 | 23 | 0 |
| diocese-of-providence | 139 | 139 | 75 | 23 | 2 | 39 | 0 |
| diocese-of-syracuse | 138 | 135 | 82 | 13 | 1 | 39 | 0 |
| unassigned-in | 136 | 37 | 19 | 7 | 0 | 11 | 0 |
| unassigned-ky | 134 | 146 | 82 | 25 | 3 | 36 | 0 |
| archdiocese-of-atlanta | 133 | 133 | 92 | 6 | 2 | 21 | 0 |
| archdiocese-of-new-orleans | 131 | 131 | 21 | 91 | 0 | 19 | 0 |
| diocese-of-paterson | 126 | 126 | 12 | 101 | 1 | 12 | 0 |
| unassigned-la | 126 | 198 | 43 | 119 | 0 | 36 | 0 |
| unassigned-wi | 125 | 57 | 31 | 12 | 1 | 13 | 0 |
| archdiocese-of-detroit | 123 | 123 | 52 | 23 | 1 | 45 | 0 |
| diocese-of-springfield-in-illinois | 122 | 64 | 28 | 16 | 1 | 19 | 0 |
| unassigned-wa | 120 | 120 | 63 | 31 | 0 | 26 | 0 |
| diocese-of-charlotte | 118 | 118 | 62 | 16 | 0 | 40 | 0 |
| archdiocese-of-seattle | 117 | 117 | 81 | 30 | 0 | 6 | 0 |
| unassigned-wv | 115 | 119 | 97 | 3 | 3 | 16 | 0 |
| diocese-of-richmond | 114 | 124 | 89 | 19 | 1 | 15 | 0 |
| unassigned-ia | 107 | 22 | 11 | 6 | 0 | 5 | 0 |
| diocese-of-peoria | 105 | 78 | 67 | 7 | 0 | 4 | 0 |
| unassigned-ma | 105 | 62 | 17 | 29 | 1 | 15 | 0 |
| unassigned-sd | 105 | 110 | 69 | 17 | 1 | 23 | 0 |
| diocese-of-dallas | 100 | 101 | 39 | 45 | 0 | 17 | 0 |
| diocese-of-burlington | 98 | 101 | 13 | 59 | 1 | 28 | 0 |
| diocese-of-rockford | 98 | 69 | 27 | 24 | 0 | 18 | 0 |
| unassigned-tx | 98 | 74 | 12 | 52 | 2 | 6 | 2 |
| unassigned-nh | 98 | 99 | 56 | 33 | 0 | 10 | 0 |
| diocese-of-belleville | 96 | 96 | 59 | 8 | 0 | 29 | 0 |
| diocese-of-trenton | 96 | 96 | 36 | 13 | 3 | 44 | 0 |
| diocese-of-charleston | 95 | 96 | 69 | 10 | 1 | 16 | 0 |
| diocese-of-joliet | 95 | 95 | 70 | 22 | 0 | 3 | 0 |
| diocese-of-metuchen | 94 | 95 | 32 | 36 | 0 | 27 | 0 |
| archdiocese-of-hartford | 93 | 95 | 73 | 1 | 0 | 21 | 0 |
| unassigned-mt | 93 | 105 | 66 | 17 | 0 | 22 | 0 |
| diocese-of-rochester | 92 | 92 | 60 | 19 | 1 | 12 | 0 |
| unassigned-ms | 92 | 96 | 43 | 31 | 1 | 21 | 0 |
| unassigned-ny | 89 | 42 | 31 | 2 | 1 | 8 | 0 |
| diocese-of-harrisburg | 88 | 89 | 57 | 30 | 0 | 2 | 0 |
| diocese-of-la-crosse | 88 | 90 | 64 | 19 | 2 | 5 | 0 |
| diocese-of-raleigh | 88 | 93 | 61 | 24 | 1 | 7 | 0 |
| diocese-of-columbus | 88 | 88 | 33 | 19 | 1 | 35 | 0 |
| archdiocese-of-san-francisco | 87 | 53 | 26 | 11 | 0 | 16 | 0 |
| unassigned-ok | 87 | 105 | 52 | 34 | 4 | 15 | 0 |
| diocese-of-allentown | 84 | 86 | 57 | 7 | 1 | 21 | 0 |
| diocese-of-camden | 84 | 84 | 40 | 5 | 2 | 37 | 0 |
| diocese-of-corpus-christi | 84 | 81 | 35 | 26 | 1 | 16 | 3 |
| unassigned-il | 84 | 100 | 68 | 14 | 0 | 18 | 0 |
| archdiocese-of-dubuque | 83 | 87 | 24 | 13 | 0 | 34 | 0 |
| unassigned-nd | 83 | 99 | 48 | 46 | 1 | 4 | 0 |
| unassigned-tn | 83 | 113 | 54 | 49 | 1 | 9 | 0 |
| diocese-of-orlando | 82 | 83 | 73 | 6 | 0 | 4 | 0 |
| archdiocese-of-omaha | 81 | 81 | 63 | 6 | 3 | 9 | 0 |
| unassigned-dc | 81 | 83 | 59 | 14 | 4 | 6 | 0 |
| diocese-of-scranton | 80 | 83 | 63 | 11 | 0 | 9 | 0 |
| unassigned-or | 80 | 151 | 45 | 88 | 0 | 18 | 0 |
| diocese-of-arlington | 79 | 80 | 54 | 11 | 1 | 14 | 0 |
| diocese-of-sacramento | 79 | 83 | 45 | 36 | 0 | 2 | 0 |
| diocese-of-st-petersburg | 79 | 82 | 58 | 23 | 0 | 1 | 0 |
| unassigned-al | 79 | 96 | 37 | 41 | 1 | 17 | 0 |
| unassigned-ct | 79 | 49 | 24 | 2 | 0 | 21 | 2 |
| diocese-of-madison | 78 | 78 | 14 | 38 | 1 | 25 | 0 |
| archdiocese-of-louisville | 77 | 81 | 45 | 11 | 1 | 24 | 0 |
| diocese-of-brownsville | 77 | 77 | 30 | 16 | 1 | 19 | 11 |
| diocese-of-fort-worth | 76 | 77 | 28 | 44 | 0 | 5 | 0 |
| diocese-of-phoenix | 76 | 76 | 49 | 11 | 1 | 15 | 0 |
| diocese-of-fresno | 75 | 49 | 13 | 15 | 1 | 20 | 0 |
| diocese-of-lafayette-in-louisiana | 75 | 76 | 33 | 15 | 2 | 26 | 0 |
| unassigned-nj | 74 | 28 | 15 | 9 | 1 | 3 | 0 |
| diocese-of-fort-wayne-south-bend | 73 | 73 | 45 | 21 | 0 | 7 | 0 |
| unassigned-hi | 73 | 76 | 54 | 12 | 3 | 7 | 0 |
| archdiocese-of-kansas-city-in-kansas | 73 | 73 | 27 | 17 | 3 | 23 | 3 |
| diocese-of-ogdensburg | 72 | 71 | 26 | 22 | 1 | 20 | 2 |
| diocese-of-pittsburgh | 72 | 72 | 8 | 61 | 0 | 3 | 0 |
| unassigned-ga | 72 | 80 | 56 | 8 | 3 | 13 | 0 |
| diocese-of-wichita | 70 | 74 | 33 | 6 | 34 | 1 | 0 |
| unassigned-mn | 70 | 82 | 46 | 23 | 0 | 13 | 0 |
| diocese-of-little-rock | 68 | 68 | 47 | 10 | 1 | 10 | 0 |
| archdiocese-of-oklahoma-city | 66 | 66 | 28 | 20 | 6 | 12 | 0 |
| diocese-of-kansas-city-st-joseph | 66 | 68 | 48 | 20 | 0 | 0 | 0 |
| diocese-of-worcester | 66 | 66 | 11 | 30 | 0 | 25 | 0 |
| diocese-of-tyler | 64 | 65 | 23 | 26 | 0 | 12 | 4 |
| diocese-of-altoona-johnstown | 63 | 63 | 35 | 8 | 1 | 19 | 0 |
| diocese-of-boise | 63 | 65 | 33 | 17 | 0 | 15 | 0 |
| diocese-of-las-cruces | 63 | 63 | 5 | 0 | 0 | 58 | 0 |
| unassigned-mi | 63 | 10 | 8 | 1 | 0 | 1 | 0 |
| diocese-of-savannah | 61 | 62 | 31 | 24 | 3 | 4 | 0 |
| unassigned-me | 61 | 78 | 28 | 44 | 1 | 5 | 0 |
| diocese-of-grand-rapids | 60 | 60 | 52 | 5 | 0 | 3 | 0 |
| diocese-of-tucson | 60 | 60 | 24 | 12 | 0 | 21 | 3 |
| diocese-of-venice | 59 | 59 | 49 | 5 | 0 | 5 | 0 |
| diocese-of-wilmington | 59 | 63 | 41 | 17 | 1 | 4 | 0 |
| diocese-of-youngstown | 59 | 60 | 47 | 7 | 0 | 6 | 0 |
| diocese-of-san-bernardino | 59 | 34 | 5 | 3 | 1 | 25 | 0 |
| diocese-of-fargo | 58 | 58 | 21 | 28 | 0 | 9 | 0 |
| diocese-of-bridgeport | 58 | 58 | 36 | 6 | 0 | 15 | 1 |
| diocese-of-fall-river | 56 | 56 | 33 | 15 | 0 | 8 | 0 |
| diocese-of-knoxville | 56 | 56 | 20 | 27 | 0 | 9 | 0 |
| diocese-of-des-moines | 55 | 57 | 35 | 13 | 2 | 7 | 0 |
| diocese-of-birmingham | 54 | 54 | 34 | 5 | 0 | 15 | 0 |
| diocese-of-norwich | 54 | 58 | 25 | 7 | 2 | 24 | 0 |
| diocese-of-st-cloud | 54 | 56 | 40 | 0 | 0 | 16 | 0 |
| diocese-of-amarillo | 53 | 53 | 0 | 47 | 0 | 6 | 0 |
| diocese-of-santa-rosa | 53 | 13 | 0 | 1 | 1 | 11 | 0 |
| diocese-of-memphis | 52 | 52 | 39 | 3 | 0 | 10 | 0 |
| diocese-of-saginaw | 52 | 54 | 37 | 16 | 1 | 0 | 0 |
| unassigned-fl | 52 | 14 | 9 | 1 | 1 | 3 | 0 |
| diocese-of-palm-beach | 51 | 51 | 41 | 6 | 0 | 4 | 0 |
| diocese-of-st-augustine | 51 | 58 | 38 | 12 | 0 | 8 | 0 |
| diocese-of-beaumont | 50 | 52 | 15 | 20 | 1 | 14 | 2 |
| unassigned-vt | 50 | 59 | 17 | 31 | 1 | 10 | 0 |
| diocese-of-covington | 49 | 49 | 30 | 5 | 0 | 14 | 0 |
| diocese-of-winona-rochester | 49 | 49 | 18 | 11 | 0 | 20 | 0 |
| diocese-of-owensboro | 48 | 48 | 31 | 10 | 0 | 7 | 0 |
| diocese-of-baton-rouge | 47 | 48 | 19 | 12 | 0 | 16 | 1 |
| diocese-of-duluth | 46 | 46 | 9 | 20 | 0 | 17 | 0 |
| byzantine-catholic-eparchy-of-passaic | 45 | 43 | 37 | 4 | 0 | 2 | 0 |
| unassigned-ut | 43 | 36 | 26 | 1 | 0 | 9 | 0 |
| diocese-of-gary | 42 | 42 | 30 | 5 | 0 | 7 | 0 |
| unassigned-ar | 42 | 45 | 28 | 5 | 0 | 12 | 0 |
| diocese-of-springfield-massachusetts | 42 | 42 | 19 | 3 | 0 | 20 | 0 |
| unassigned-nv | 42 | 49 | 26 | 19 | 0 | 4 | 0 |
| archdiocese-of-los-angeles | 41 | 41 | 23 | 8 | 5 | 5 | 0 |
| diocese-of-el-paso | 41 | 41 | 32 | 1 | 0 | 4 | 4 |
| diocese-of-gaylord | 40 | 40 | 38 | 1 | 0 | 1 | 0 |
| diocese-of-davenport | 40 | 40 | 22 | 3 | 0 | 15 | 0 |
| diocese-of-monterey | 39 | 21 | 5 | 7 | 0 | 9 | 0 |
| unassigned-md | 39 | 50 | 26 | 5 | 0 | 19 | 0 |
| archdiocese-of-mobile | 37 | 39 | 12 | 13 | 0 | 14 | 0 |
| diocese-of-springfield-cape-girardeau | 37 | 37 | 17 | 1 | 0 | 19 | 0 |
| unassigned-de | 37 | 41 | 30 | 7 | 1 | 3 | 0 |
| archdiocese-of-santa-fe | 36 | 42 | 18 | 8 | 1 | 15 | 0 |
| diocese-of-spokane | 36 | 37 | 12 | 24 | 0 | 1 | 0 |
| diocese-of-salt-lake-city | 35 | 39 | 27 | 2 | 0 | 10 | 0 |
| eparchy-of-newton | 35 | 38 | 26 | 2 | 1 | 9 | 0 |
| unassigned-sc | 35 | 40 | 31 | 3 | 1 | 5 | 0 |
| archdiocese-of-anchorage-juneau | 34 | 34 | 17 | 3 | 1 | 6 | 0 |
| diocese-of-houma-thibodaux | 34 | 34 | 18 | 13 | 0 | 3 | 0 |
| diocese-of-jefferson-city | 34 | 34 | 22 | 3 | 0 | 9 | 0 |
| diocese-of-san-angelo | 34 | 34 | 17 | 9 | 0 | 4 | 4 |
| unassigned-va | 33 | 61 | 44 | 9 | 0 | 8 | 0 |
| archdiocese-of-las-vegas | 32 | 32 | 12 | 15 | 0 | 5 | 0 |
| diocese-of-biloxi | 32 | 32 | 12 | 10 | 0 | 10 | 0 |
| diocese-of-salina | 32 | 32 | 29 | 3 | 0 | 0 | 0 |
| diocese-of-sioux-falls | 32 | 32 | 18 | 14 | 0 | 0 | 0 |
| diocese-of-colorado-springs | 32 | 32 | 14 | 4 | 0 | 14 | 0 |
| unassigned-wy | 32 | 33 | 17 | 12 | 0 | 4 | 0 |
| unassigned-id | 31 | 38 | 23 | 6 | 1 | 8 | 0 |
| diocese-of-oakland | 30 | 34 | 20 | 9 | 1 | 4 | 0 |
| diocese-of-lafayette-in-indiana | 29 | 29 | 0 | 18 | 1 | 10 | 0 |
| diocese-of-tulsa | 29 | 29 | 15 | 2 | 0 | 12 | 0 |
| diocese-of-portland-in-maine | 27 | 27 | 4 | 15 | 0 | 8 | 0 |
| diocese-of-bismarck | 26 | 26 | 7 | 14 | 1 | 4 | 0 |
| diocese-of-pensacola-tallahassee | 26 | 26 | 8 | 7 | 1 | 10 | 0 |
| unassigned-ak | 22 | 28 | 11 | 7 | 2 | 5 | 3 |
| diocese-of-marquette | 22 | 23 | 9 | 2 | 0 | 11 | 1 |
| diocese-of-cheyenne | 22 | 22 | 8 | 7 | 0 | 7 | 0 |
| diocese-of-victoria | 21 | 21 | 1 | 14 | 0 | 6 | 0 |
| unassigned-nc | 21 | 8 | 5 | 0 | 0 | 3 | 0 |
| diocese-of-stockton | 21 | 11 | 0 | 7 | 0 | 4 | 0 |
| archdiocese-of-philadelphia | 20 | 20 | 13 | 4 | 0 | 3 | 0 |
| diocese-of-shreveport | 20 | 20 | 8 | 9 | 0 | 3 | 0 |
| diocese-of-sioux-city | 20 | 20 | 2 | 5 | 0 | 13 | 0 |
| unassigned-ri | 20 | 41 | 10 | 22 | 0 | 9 | 0 |
| diocese-of-alexandria | 19 | 19 | 9 | 6 | 0 | 4 | 0 |
| diocese-of-helena | 19 | 19 | 14 | 4 | 0 | 1 | 0 |
| diocese-of-orange | 19 | 20 | 20 | 0 | 0 | 0 | 0 |
| archdiocese-of-cincinnati | 18 | 18 | 12 | 1 | 1 | 1 | 0 |
| diocese-of-rapid-city | 17 | 21 | 14 | 2 | 0 | 5 | 0 |
| diocese-of-grand-island | 17 | 17 | 7 | 1 | 0 | 8 | 1 |
| diocese-of-crookston | 16 | 16 | 8 | 3 | 0 | 5 | 0 |
| unassigned-xx | 14 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-jackson | 13 | 13 | 5 | 6 | 0 | 2 | 0 |
| diocese-of-laredo | 13 | 13 | 6 | 3 | 0 | 4 | 0 |
| diocese-of-dodge-city | 12 | 12 | 9 | 1 | 0 | 2 | 0 |
| diocese-of-lubbock | 11 | 12 | 4 | 3 | 0 | 3 | 2 |
| diocese-of-manchester | 10 | 10 | 4 | 4 | 1 | 1 | 0 |
| diocese-of-lake-charles | 9 | 9 | 4 | 3 | 1 | 1 | 0 |
| unassigned-unresolved | 9 | 9 | 6 | 0 | 0 | 3 | 0 |
| chaldean-eparchy-of-saint-thomas-the-apostle | 8 | 8 | 8 | 0 | 0 | 0 | 0 |
| diocese-of-fairbanks | 8 | 8 | 2 | 4 | 0 | 2 | 0 |
| diocese-of-evansville | 7 | 7 | 2 | 1 | 1 | 3 | 0 |
| diocese-of-lansing | 7 | 7 | 2 | 3 | 0 | 2 | 0 |
| diocese-of-toledo | 7 | 12 | 12 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-eparchy-of-st-josaphat-parma-oh | 7 | 7 | 6 | 0 | 1 | 0 | 0 |
| archdiocese-of-indianapolis | 7 | 7 | 3 | 1 | 0 | 3 | 0 |
| archdiocese-of-the-military-services | 6 | 6 | 4 | 0 | 0 | 2 | 0 |
| diocese-of-great-falls-billings | 6 | 6 | 6 | 0 | 0 | 0 | 0 |
| archdiocese-of-miami | 6 | 6 | 5 | 0 | 0 | 1 | 0 |
| diocese-of-honolulu | 6 | 6 | 4 | 2 | 0 | 0 | 0 |
| diocese-of-gallup | 5 | 6 | 6 | 0 | 0 | 0 | 0 |
| diocese-of-greensburg | 5 | 11 | 11 | 0 | 0 | 0 | 0 |
| diocese-of-new-ulm | 5 | 7 | 5 | 1 | 0 | 1 | 0 |
| diocese-of-san-jose | 5 | 5 | 3 | 1 | 0 | 1 | 0 |
| eparchy-of-st-maron-of-brooklyn | 5 | 5 | 4 | 0 | 0 | 1 | 0 |
| diocese-of-green-bay | 3 | 3 | 3 | 0 | 0 | 0 | 0 |
| maronite-eparchy-of-our-lady-of-lebanon | 3 | 2 | 2 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-archeparchy-of-philadelphia | 3 | 3 | 2 | 0 | 0 | 1 | 0 |
| diocese-of-lincoln | 3 | 3 | 1 | 0 | 0 | 2 | 0 |
| diocese-of-san-diego | 2 | 2 | 0 | 1 | 0 | 1 | 0 |
| diocese-of-reno | 2 | 2 | 0 | 2 | 0 | 0 | 0 |
| diocese-of-baker | 2 | 2 | 2 | 0 | 0 | 0 | 0 |
| byzantine-catholic-archeparchy-of-pittsburgh | 2 | 2 | 1 | 0 | 0 | 1 | 0 |
| eparchy-of-parma | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| diocese-of-pueblo | 1 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-lexington | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-kalamazoo | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-steubenville | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-erie | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-nashville | 1 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-yakima | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-superior | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-wheeling-charleston | 1 | 1 | 1 | 0 | 0 | 0 | 0 |

Diocese directory probe: {'challenge': 73, 'rest-pages': 49, 'html': 41, 'open-rest': 12, 'dead': 10, 'html_unknown': 8, 'blocked': 3}.

## Chunk log

- 2026-11-01T08:19:52+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T08:34:55+00:00: fetched {'dns': 1, 'done': 1, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T08:49:57+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T09:04:51+00:00: fetched {'robots_disallowed': 1, 'done': 32, 'challenge': 3, 'ok': 25, 'dns': 1, 'timeout': 2, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T10:11:53+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T13:02:41+00:00: fetched {'dns': 4, 'done': 17, 'challenge': 5, 'ok': 6, 'conn_error': 1, 'parked': 1, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T13:09:20+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T16:02:20+00:00: fetched {'dns': 2, 'done': 5, 'challenge': 1, 'parked': 1, 'ok': 1, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T17:21:18+00:00: fetched {'dns': 54, 'done': 270, 'ssl_error': 14, 'challenge': 52, 'parked': 3, 'ok': 128, 'not_found': 7, 'http_5xx': 3, 'blocked_403': 1, 'conn_error': 2, 'timeout': 7, 'cut': 1, 'halted_egress': 0}; parsed 20 dioceses
- 2026-11-01T17:42:30+00:00: fetched {'robots_disallowed': 1, 'done': 46, 'dns': 3, 'challenge': 14, 'ssl_error': 2, 'blocked_403': 2, 'not_found': 2, 'ok': 21, 'timeout': 1, 'halted_egress': 0}; parsed 3 dioceses
- 2026-11-01T17:52:14+00:00: fetched {'ok': 1, 'done': 1, 'halted_egress': 0}; parsed 1 dioceses
- 2026-11-01T17:53:22+00:00: fetched {'ok': 11, 'done': 11, 'challenge': 1, 'dns': 1, 'cut': 2, 'halted_egress': 0}; parsed 5 dioceses
- 2026-11-01T18:03:07+00:00: fetched {'ok': 1, 'done': 1, 'halted_egress': 0}; parsed 1 dioceses
- 2026-11-01T18:51:40+00:00: fetched {'robots_disallowed': 4, 'done': 262, 'dns': 77, 'conn_error': 3, 'ssl_error': 24, 'challenge': 30, 'not_found': 5, 'ok': 92, 'parked': 8, 'http_5xx': 5, 'blocked_429': 1, 'blocked_403': 1, 'error:TooManyRedirects': 1, 'timeout': 13, 'cut': 2, 'halted_egress': 0}; parsed 6 dioceses
- 2026-11-01T20:24:59+00:00: fetched {'dns': 10, 'done': 138, 'not_found': 2, 'ssl_error': 9, 'challenge': 34, 'http_4xx': 1, 'ok': 84, 'cut': 2, 'halted_egress': 0}; parsed 4 dioceses
