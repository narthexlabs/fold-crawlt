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
| archdiocese-of-chicago | 478 | 483 | 248 | 17 | 8 | 152 | 3 |
| archdiocese-of-new-york | 457 | 459 | 94 | 40 | 3 | 102 | 0 |
| archdiocese-of-boston | 415 | 418 | 156 | 85 | 2 | 135 | 0 |
| unassigned-ca | 413 | 56 | 36 | 8 | 1 | 11 | 0 |
| archdiocese-of-newark | 298 | 302 | 160 | 11 | 2 | 88 | 1 |
| archdiocese-of-detroit | 298 | 300 | 149 | 48 | 0 | 72 | 2 |
| unassigned-pa | 291 | 626 | 433 | 88 | 7 | 80 | 18 |
| archdiocese-of-st-paul-and-minneapolis | 269 | 269 | 135 | 76 | 0 | 55 | 3 |
| archdiocese-of-philadelphia | 255 | 255 | 171 | 15 | 0 | 24 | 2 |
| unassigned-oh | 254 | 423 | 273 | 78 | 6 | 64 | 2 |
| diocese-of-cleveland | 250 | 286 | 170 | 57 | 1 | 58 | 0 |
| archdiocese-of-st-louis | 247 | 248 | 124 | 52 | 1 | 64 | 4 |
| diocese-of-brooklyn | 247 | 247 | 185 | 14 | 2 | 41 | 5 |
| archdiocese-of-milwaukee | 229 | 230 | 150 | 6 | 3 | 55 | 0 |
| archdiocese-of-galveston-houston | 209 | 209 | 71 | 30 | 1 | 40 | 0 |
| archdiocese-of-portland-in-oregon | 208 | 182 | 68 | 33 | 2 | 35 | 0 |
| archdiocese-of-seattle | 208 | 209 | 113 | 41 | 0 | 52 | 0 |
| archdiocese-of-hartford | 202 | 201 | 107 | 0 | 1 | 90 | 0 |
| diocese-of-buffalo | 201 | 201 | 110 | 21 | 3 | 67 | 0 |
| archdiocese-of-baltimore | 199 | 199 | 93 | 10 | 1 | 55 | 0 |
| diocese-of-rockville-centre | 198 | 198 | 117 | 15 | 10 | 56 | 0 |
| unassigned-ny | 190 | 215 | 138 | 19 | 6 | 52 | 0 |
| archdiocese-of-washington | 187 | 187 | 36 | 140 | 0 | 11 | 0 |
| diocese-of-green-bay | 181 | 183 | 85 | 42 | 0 | 50 | 6 |
| diocese-of-albany | 169 | 173 | 92 | 26 | 4 | 50 | 1 |
| diocese-of-richmond | 168 | 178 | 117 | 24 | 2 | 35 | 0 |
| archdiocese-of-san-antonio | 167 | 168 | 75 | 27 | 2 | 34 | 9 |
| archdiocese-of-denver | 164 | 164 | 123 | 1 | 0 | 31 | 0 |
| archdiocese-of-cincinnati | 164 | 166 | 108 | 39 | 2 | 13 | 1 |
| unassigned-nm | 159 | 264 | 164 | 50 | 4 | 46 | 0 |
| archdiocese-of-miami | 156 | 156 | 103 | 0 | 1 | 42 | 1 |
| unassigned-wi | 153 | 268 | 165 | 47 | 6 | 50 | 0 |
| diocese-of-la-crosse | 152 | 154 | 97 | 25 | 4 | 26 | 2 |
| archdiocese-of-new-orleans | 149 | 149 | 27 | 19 | 0 | 22 | 0 |
| diocese-of-raleigh | 147 | 150 | 74 | 30 | 1 | 45 | 0 |
| diocese-of-trenton | 147 | 147 | 61 | 30 | 3 | 53 | 0 |
| diocese-of-austin | 145 | 146 | 31 | 86 | 0 | 28 | 1 |
| diocese-of-rochester | 143 | 143 | 70 | 22 | 2 | 49 | 0 |
| archdiocese-of-atlanta | 141 | 135 | 94 | 5 | 2 | 21 | 0 |
| diocese-of-st-cloud | 140 | 141 | 84 | 5 | 1 | 50 | 1 |
| diocese-of-peoria | 139 | 142 | 100 | 8 | 2 | 32 | 0 |
| diocese-of-providence | 139 | 140 | 75 | 23 | 2 | 40 | 0 |
| diocese-of-springfield-in-illinois | 138 | 138 | 87 | 21 | 1 | 28 | 1 |
| unassigned-tx | 138 | 183 | 84 | 68 | 4 | 25 | 2 |
| diocese-of-syracuse | 137 | 138 | 83 | 13 | 1 | 41 | 0 |
| diocese-of-paterson | 134 | 135 | 15 | 107 | 1 | 12 | 0 |
| unassigned-mi | 131 | 270 | 168 | 67 | 4 | 31 | 0 |
| archdiocese-of-los-angeles | 130 | 41 | 23 | 2 | 5 | 5 | 0 |
| diocese-of-columbus | 130 | 130 | 55 | 33 | 2 | 40 | 0 |
| archdiocese-of-dubuque | 129 | 129 | 33 | 24 | 0 | 47 | 1 |
| diocese-of-charlotte | 129 | 129 | 69 | 20 | 0 | 40 | 0 |
| diocese-of-scranton | 128 | 132 | 86 | 13 | 1 | 32 | 0 |
| archdiocese-of-kansas-city-in-kansas | 125 | 126 | 57 | 20 | 4 | 27 | 3 |
| diocese-of-orlando | 122 | 123 | 88 | 6 | 0 | 28 | 1 |
| diocese-of-joliet | 121 | 121 | 80 | 22 | 0 | 19 | 0 |
| diocese-of-phoenix | 120 | 120 | 87 | 16 | 1 | 16 | 0 |
| diocese-of-worcester | 120 | 121 | 17 | 74 | 0 | 30 | 0 |
| diocese-of-jefferson-city | 119 | 119 | 70 | 4 | 0 | 45 | 0 |
| unassigned-nj | 119 | 172 | 102 | 25 | 10 | 30 | 5 |
| archdiocese-of-santa-fe | 118 | 122 | 55 | 32 | 1 | 28 | 5 |
| diocese-of-metuchen | 118 | 119 | 43 | 46 | 0 | 30 | 0 |
| diocese-of-sacramento | 114 | 83 | 45 | 36 | 0 | 2 | 0 |
| diocese-of-dallas | 111 | 112 | 47 | 46 | 0 | 19 | 0 |
| diocese-of-fort-wayne-south-bend | 111 | 111 | 59 | 23 | 0 | 29 | 0 |
| unassigned-la | 111 | 267 | 91 | 126 | 1 | 44 | 5 |
| archdiocese-of-omaha | 110 | 111 | 80 | 4 | 3 | 14 | 3 |
| diocese-of-youngstown | 108 | 108 | 57 | 10 | 0 | 38 | 3 |
| diocese-of-bridgeport | 108 | 108 | 76 | 13 | 0 | 17 | 2 |
| diocese-of-st-petersburg | 107 | 109 | 68 | 27 | 1 | 13 | 0 |
| diocese-of-erie | 107 | 107 | 70 | 6 | 3 | 28 | 0 |
| diocese-of-kansas-city-st-joseph | 106 | 107 | 55 | 26 | 0 | 22 | 4 |
| diocese-of-tucson | 106 | 106 | 49 | 26 | 0 | 27 | 4 |
| archdiocese-of-louisville | 104 | 106 | 60 | 5 | 1 | 28 | 0 |
| diocese-of-camden | 104 | 104 | 54 | 5 | 2 | 43 | 0 |
| diocese-of-allentown | 103 | 105 | 73 | 8 | 1 | 23 | 0 |
| unassigned-ma | 103 | 159 | 68 | 64 | 3 | 24 | 0 |
| unassigned-ne | 101 | 225 | 163 | 20 | 0 | 42 | 0 |
| diocese-of-fall-river | 100 | 101 | 53 | 22 | 0 | 26 | 0 |
| diocese-of-harrisburg | 100 | 101 | 66 | 31 | 0 | 4 | 0 |
| diocese-of-norwich | 100 | 104 | 52 | 8 | 2 | 41 | 1 |
| diocese-of-pittsburgh | 100 | 100 | 12 | 59 | 0 | 9 | 1 |
| unassigned-fl | 99 | 139 | 83 | 16 | 6 | 34 | 0 |
| diocese-of-rockford | 99 | 99 | 45 | 33 | 1 | 20 | 0 |
| diocese-of-belleville | 98 | 98 | 60 | 8 | 0 | 30 | 0 |
| diocese-of-burlington | 98 | 101 | 13 | 59 | 1 | 28 | 0 |
| diocese-of-arlington | 97 | 98 | 67 | 14 | 1 | 16 | 0 |
| archdiocese-of-indianapolis | 97 | 97 | 46 | 26 | 0 | 23 | 1 |
| diocese-of-charleston | 95 | 96 | 69 | 10 | 1 | 16 | 0 |
| diocese-of-fort-worth | 95 | 96 | 34 | 46 | 0 | 16 | 0 |
| diocese-of-greensburg | 94 | 100 | 81 | 8 | 0 | 11 | 0 |
| diocese-of-ogdensburg | 94 | 93 | 30 | 22 | 1 | 38 | 2 |
| diocese-of-wichita | 94 | 98 | 44 | 6 | 39 | 9 | 0 |
| diocese-of-grand-rapids | 93 | 94 | 68 | 5 | 2 | 19 | 0 |
| diocese-of-las-cruces | 93 | 93 | 20 | 5 | 0 | 66 | 2 |
| diocese-of-savannah | 93 | 78 | 33 | 26 | 3 | 16 | 0 |
| diocese-of-madison | 92 | 92 | 16 | 49 | 1 | 26 | 0 |
| unassigned-mo | 92 | 222 | 150 | 25 | 3 | 44 | 0 |
| diocese-of-fargo | 91 | 91 | 25 | 55 | 0 | 11 | 0 |
| diocese-of-lansing | 90 | 91 | 39 | 29 | 1 | 22 | 0 |
| diocese-of-little-rock | 89 | 89 | 54 | 11 | 1 | 23 | 0 |
| diocese-of-saginaw | 89 | 90 | 43 | 17 | 1 | 29 | 0 |
| unassigned-ct | 89 | 155 | 109 | 6 | 1 | 37 | 2 |
| archdiocese-of-san-francisco | 88 | 53 | 26 | 6 | 0 | 16 | 0 |
| archdiocese-of-oklahoma-city | 87 | 66 | 28 | 6 | 2 | 12 | 0 |
| diocese-of-altoona-johnstown | 85 | 86 | 48 | 12 | 1 | 19 | 6 |
| archdiocese-of-mobile | 85 | 39 | 12 | 5 | 0 | 14 | 0 |
| unassigned-co | 84 | 186 | 108 | 21 | 7 | 50 | 0 |
| unassigned-ms | 84 | 98 | 45 | 31 | 1 | 21 | 0 |
| diocese-of-corpus-christi | 83 | 84 | 37 | 27 | 1 | 16 | 3 |
| diocese-of-venice | 83 | 83 | 60 | 9 | 0 | 14 | 0 |
| diocese-of-toledo | 81 | 84 | 59 | 3 | 1 | 21 | 0 |
| diocese-of-owensboro | 80 | 80 | 41 | 12 | 1 | 21 | 5 |
| diocese-of-st-augustine | 80 | 85 | 48 | 16 | 0 | 21 | 0 |
| diocese-of-manchester | 79 | 79 | 28 | 32 | 1 | 17 | 1 |
| diocese-of-birmingham | 78 | 54 | 34 | 5 | 0 | 15 | 0 |
| diocese-of-tyler | 78 | 80 | 23 | 26 | 0 | 27 | 4 |
| unassigned-wv | 78 | 119 | 97 | 3 | 3 | 16 | 0 |
| diocese-of-brownsville | 77 | 77 | 30 | 16 | 1 | 19 | 11 |
| unassigned-wa | 77 | 143 | 80 | 33 | 1 | 29 | 0 |
| diocese-of-duluth | 76 | 76 | 24 | 33 | 0 | 19 | 0 |
| diocese-of-davenport | 76 | 76 | 49 | 7 | 0 | 20 | 0 |
| diocese-of-fresno | 75 | 49 | 13 | 15 | 1 | 20 | 0 |
| diocese-of-lafayette-in-louisiana | 75 | 77 | 33 | 15 | 2 | 27 | 0 |
| diocese-of-sioux-city | 75 | 75 | 9 | 42 | 0 | 22 | 2 |
| unassigned-il | 75 | 144 | 101 | 17 | 0 | 25 | 1 |
| diocese-of-san-diego | 75 | 2 | 0 | 1 | 0 | 1 | 0 |
| diocese-of-springfield-cape-girardeau | 74 | 76 | 43 | 9 | 0 | 19 | 5 |
| unassigned-in | 73 | 179 | 91 | 48 | 2 | 38 | 0 |
| unassigned-mn | 73 | 167 | 99 | 36 | 5 | 27 | 0 |
| unassigned-sd | 73 | 110 | 69 | 17 | 1 | 23 | 0 |
| diocese-of-des-moines | 71 | 71 | 45 | 13 | 2 | 9 | 2 |
| diocese-of-evansville | 70 | 70 | 38 | 5 | 3 | 24 | 0 |
| diocese-of-winona-rochester | 70 | 70 | 32 | 15 | 0 | 23 | 0 |
| unassigned-hi | 70 | 76 | 54 | 12 | 3 | 7 | 0 |
| diocese-of-sioux-falls | 69 | 69 | 33 | 21 | 3 | 12 | 0 |
| diocese-of-pueblo | 69 | 69 | 23 | 13 | 3 | 25 | 5 |
| diocese-of-tulsa | 69 | 34 | 18 | 3 | 0 | 13 | 0 |
| unassigned-ky | 68 | 149 | 83 | 26 | 3 | 37 | 0 |
| diocese-of-baton-rouge | 68 | 69 | 35 | 15 | 0 | 18 | 1 |
| diocese-of-palm-beach | 67 | 67 | 45 | 8 | 0 | 12 | 2 |
| unassigned-az | 67 | 183 | 123 | 41 | 2 | 17 | 0 |
| unassigned-dc | 67 | 83 | 59 | 14 | 4 | 6 | 0 |
| diocese-of-bismarck | 66 | 66 | 14 | 45 | 2 | 4 | 1 |
| diocese-of-memphis | 65 | 65 | 39 | 3 | 0 | 23 | 0 |
| diocese-of-pensacola-tallahassee | 64 | 64 | 34 | 16 | 1 | 12 | 1 |
| unassigned-ks | 64 | 161 | 92 | 34 | 12 | 23 | 0 |
| diocese-of-boise | 63 | 65 | 33 | 17 | 0 | 15 | 0 |
| unassigned-tn | 63 | 113 | 54 | 49 | 1 | 9 | 0 |
| diocese-of-orange | 62 | 20 | 20 | 0 | 0 | 0 | 0 |
| diocese-of-covington | 61 | 61 | 36 | 8 | 0 | 17 | 0 |
| diocese-of-gary | 60 | 60 | 37 | 5 | 0 | 17 | 1 |
| diocese-of-superior | 60 | 61 | 40 | 20 | 0 | 1 | 0 |
| unassigned-ia | 60 | 118 | 65 | 32 | 1 | 20 | 0 |
| diocese-of-wilmington | 59 | 63 | 41 | 17 | 1 | 4 | 0 |
| unassigned-ga | 59 | 80 | 56 | 8 | 3 | 13 | 0 |
| diocese-of-san-bernardino | 59 | 34 | 5 | 3 | 1 | 25 | 0 |
| diocese-of-lincoln | 58 | 58 | 33 | 7 | 0 | 18 | 0 |
| diocese-of-kalamazoo | 57 | 57 | 5 | 46 | 0 | 6 | 0 |
| diocese-of-new-ulm | 57 | 59 | 24 | 8 | 0 | 27 | 0 |
| diocese-of-knoxville | 56 | 56 | 20 | 27 | 0 | 9 | 0 |
| diocese-of-lafayette-in-indiana | 55 | 55 | 0 | 44 | 1 | 10 | 0 |
| diocese-of-gaylord | 54 | 55 | 46 | 1 | 0 | 7 | 1 |
| diocese-of-marquette | 54 | 55 | 19 | 23 | 0 | 12 | 1 |
| diocese-of-amarillo | 53 | 53 | 0 | 47 | 0 | 6 | 0 |
| diocese-of-santa-rosa | 53 | 13 | 0 | 1 | 1 | 11 | 0 |
| diocese-of-beaumont | 50 | 52 | 15 | 20 | 1 | 14 | 2 |
| diocese-of-spokane | 50 | 51 | 20 | 28 | 0 | 3 | 0 |
| diocese-of-colorado-springs | 50 | 50 | 22 | 4 | 1 | 23 | 0 |
| diocese-of-crookston | 50 | 52 | 23 | 20 | 0 | 9 | 0 |
| unassigned-vt | 50 | 60 | 18 | 31 | 1 | 10 | 0 |
| diocese-of-grand-island | 49 | 49 | 29 | 3 | 0 | 12 | 5 |
| diocese-of-san-jose | 48 | 5 | 3 | 1 | 0 | 1 | 0 |
| diocese-of-helena | 47 | 47 | 30 | 13 | 0 | 3 | 1 |
| diocese-of-el-paso | 47 | 48 | 33 | 1 | 0 | 10 | 4 |
| unassigned-ok | 46 | 114 | 58 | 34 | 4 | 18 | 0 |
| byzantine-catholic-eparchy-of-passaic | 45 | 45 | 39 | 4 | 0 | 2 | 0 |
| unassigned-or | 45 | 152 | 45 | 88 | 1 | 18 | 0 |
| diocese-of-steubenville | 43 | 44 | 23 | 10 | 1 | 8 | 2 |
| unassigned-nh | 43 | 99 | 56 | 33 | 0 | 10 | 0 |
| diocese-of-springfield-massachusetts | 42 | 42 | 19 | 3 | 0 | 20 | 0 |
| unassigned-va | 42 | 97 | 68 | 11 | 1 | 17 | 0 |
| unassigned-ut | 41 | 47 | 35 | 2 | 0 | 10 | 0 |
| diocese-of-salina | 40 | 40 | 35 | 3 | 2 | 0 | 0 |
| diocese-of-nashville | 40 | 40 | 13 | 11 | 0 | 16 | 0 |
| eparchy-of-newton | 39 | 41 | 27 | 2 | 1 | 11 | 0 |
| diocese-of-monterey | 39 | 21 | 5 | 7 | 0 | 9 | 0 |
| unassigned-mt | 39 | 105 | 66 | 17 | 0 | 22 | 0 |
| diocese-of-wheeling-charleston | 39 | 39 | 24 | 1 | 1 | 13 | 0 |
| diocese-of-houma-thibodaux | 38 | 38 | 21 | 13 | 1 | 3 | 0 |
| diocese-of-victoria | 38 | 38 | 1 | 31 | 0 | 6 | 0 |
| unassigned-nd | 38 | 100 | 48 | 47 | 1 | 4 | 0 |
| diocese-of-laredo | 37 | 37 | 7 | 4 | 0 | 26 | 0 |
| diocese-of-rapid-city | 36 | 40 | 29 | 2 | 0 | 8 | 1 |
| diocese-of-great-falls-billings | 35 | 35 | 27 | 5 | 1 | 2 | 0 |
| archdiocese-of-anchorage-juneau | 34 | 34 | 17 | 3 | 1 | 6 | 0 |
| diocese-of-salt-lake-city | 34 | 40 | 28 | 2 | 0 | 10 | 0 |
| diocese-of-lexington | 34 | 35 | 17 | 11 | 0 | 6 | 1 |
| diocese-of-yakima | 34 | 34 | 13 | 12 | 0 | 9 | 0 |
| diocese-of-san-angelo | 34 | 34 | 17 | 9 | 0 | 4 | 4 |
| unassigned-me | 34 | 84 | 34 | 44 | 1 | 5 | 0 |
| diocese-of-shreveport | 33 | 33 | 9 | 12 | 0 | 12 | 0 |
| unassigned-md | 33 | 67 | 36 | 8 | 2 | 21 | 0 |
| unassigned-ar | 33 | 45 | 28 | 5 | 0 | 12 | 0 |
| unassigned-sc | 33 | 40 | 31 | 3 | 1 | 5 | 0 |
| archdiocese-of-las-vegas | 32 | 32 | 12 | 6 | 0 | 5 | 0 |
| diocese-of-biloxi | 32 | 32 | 12 | 10 | 0 | 10 | 0 |
| eparchy-of-parma | 31 | 32 | 20 | 4 | 0 | 5 | 3 |
| diocese-of-dodge-city | 30 | 30 | 18 | 3 | 3 | 5 | 1 |
| diocese-of-oakland | 30 | 34 | 20 | 9 | 1 | 4 | 0 |
| holy-protection-of-mary-byzantine-catholic-eparchy-of-phoenix | 30 | 30 | 23 | 1 | 0 | 6 | 0 |
| unassigned-al | 29 | 96 | 37 | 41 | 1 | 17 | 0 |
| unassigned-de | 28 | 41 | 30 | 7 | 1 | 3 | 0 |
| unassigned-wy | 28 | 33 | 17 | 12 | 0 | 4 | 0 |
| eparchy-of-st-maron-of-brooklyn | 27 | 26 | 20 | 0 | 2 | 3 | 1 |
| unassigned-id | 27 | 38 | 23 | 6 | 1 | 8 | 0 |
| diocese-of-baker | 26 | 26 | 18 | 4 | 0 | 2 | 2 |
| diocese-of-lake-charles | 25 | 25 | 12 | 10 | 1 | 2 | 0 |
| unassigned-nv | 24 | 51 | 27 | 19 | 0 | 5 | 0 |
| diocese-of-stockton | 23 | 11 | 0 | 7 | 0 | 4 | 0 |
| diocese-of-cheyenne | 22 | 22 | 8 | 7 | 0 | 7 | 0 |
| diocese-of-alexandria | 21 | 21 | 10 | 6 | 0 | 5 | 0 |
| unassigned-ak | 20 | 28 | 11 | 7 | 2 | 5 | 3 |
| unassigned-ri | 20 | 45 | 14 | 22 | 0 | 9 | 0 |
| diocese-of-reno | 19 | 19 | 8 | 9 | 0 | 2 | 0 |
| diocese-of-gallup | 18 | 18 | 15 | 2 | 0 | 1 | 0 |
| unassigned-nc | 17 | 23 | 14 | 0 | 0 | 9 | 0 |
| diocese-of-portland-in-maine | 16 | 27 | 4 | 15 | 0 | 8 | 0 |
| diocese-of-jackson | 13 | 13 | 5 | 6 | 0 | 2 | 0 |
| diocese-of-lubbock | 12 | 13 | 4 | 3 | 0 | 4 | 2 |
| maronite-eparchy-of-our-lady-of-lebanon | 10 | 10 | 9 | 0 | 0 | 0 | 1 |
| unassigned-xx | 10 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-fairbanks | 9 | 9 | 3 | 4 | 0 | 2 | 0 |
| diocese-of-st-thomas-vi | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| unassigned-unresolved | 9 | 9 | 6 | 0 | 0 | 3 | 0 |
| chaldean-eparchy-of-saint-thomas-the-apostle | 8 | 8 | 8 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-eparchy-of-st-josaphat-parma-oh | 7 | 7 | 6 | 0 | 1 | 0 | 0 |
| archdiocese-of-the-military-services | 6 | 6 | 4 | 0 | 0 | 2 | 0 |
| diocese-of-honolulu | 6 | 6 | 4 | 2 | 0 | 0 | 0 |
| armenian-catholic-eparchy-of-our-lady-of-nareg-in-the-usa-canada | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-archeparchy-of-philadelphia | 3 | 3 | 2 | 0 | 0 | 1 | 0 |
| byzantine-catholic-archeparchy-of-pittsburgh | 2 | 2 | 1 | 0 | 0 | 1 | 0 |

Diocese directory probe: {'challenge': 73, 'rest-pages': 49, 'html': 41, 'open-rest': 12, 'dead': 10, 'html_unknown': 8, 'blocked': 3}.

## Chunk log

- 2026-11-07T16:18:43+00:00: fetched {}; parsed 0 dioceses
- 2026-11-07T16:18:51+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-07T16:19:05+00:00: fetched {}; parsed 0 dioceses
- 2026-11-07T16:19:13+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-07T16:19:25+00:00: fetched {'challenge': 1, 'done': 1, 'halted_egress': 0}; parsed 1 dioceses
- 2026-11-07T16:19:36+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-07T16:20:02+00:00: fetched {'http_4xx': 1, 'done': 23, 'dns': 2, 'http_5xx': 1, 'challenge': 6, 'ok': 13, 'halted_egress': 0}; parsed 2 dioceses
- 2026-11-07T16:20:33+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-07T16:20:43+00:00: fetched {}; parsed 0 dioceses
- 2026-11-07T16:20:49+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-07T16:21:06+00:00: fetched {}; parsed 0 dioceses
- 2026-11-07T16:21:13+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-07T16:21:24+00:00: fetched {'dns': 1, 'done': 36, 'challenge': 9, 'blocked_403': 1, 'ssl_error': 2, 'ok': 23, 'halted_egress': 0}; parsed 2 dioceses
- 2026-11-07T16:23:18+00:00: top-up ('confession', 'adoration', 'liturgy'): {'sites': 23, 'requests': 0}; 0 sites left
- 2026-11-07T21:43:04+00:00: fetched {}; parsed 0 dioceses
