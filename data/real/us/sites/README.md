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
| archdiocese-of-chicago | 479 | 483 | 248 | 17 | 8 | 152 | 3 |
| unassigned-ca | 463 | 56 | 36 | 8 | 1 | 11 | 0 |
| archdiocese-of-new-york | 457 | 459 | 94 | 40 | 3 | 102 | 0 |
| archdiocese-of-boston | 415 | 418 | 156 | 85 | 2 | 135 | 0 |
| unassigned-oh | 310 | 423 | 273 | 78 | 6 | 64 | 2 |
| archdiocese-of-newark | 299 | 302 | 160 | 11 | 2 | 88 | 1 |
| archdiocese-of-detroit | 298 | 300 | 149 | 48 | 0 | 72 | 2 |
| unassigned-pa | 295 | 626 | 433 | 88 | 7 | 80 | 18 |
| archdiocese-of-philadelphia | 255 | 255 | 171 | 15 | 0 | 24 | 2 |
| diocese-of-cleveland | 250 | 286 | 170 | 57 | 1 | 58 | 0 |
| diocese-of-brooklyn | 243 | 243 | 181 | 14 | 2 | 41 | 5 |
| unassigned-nm | 231 | 263 | 163 | 50 | 4 | 46 | 0 |
| archdiocese-of-milwaukee | 226 | 214 | 140 | 5 | 2 | 51 | 0 |
| archdiocese-of-galveston-houston | 209 | 209 | 71 | 30 | 1 | 40 | 0 |
| diocese-of-buffalo | 201 | 201 | 110 | 21 | 3 | 67 | 0 |
| unassigned-ny | 197 | 215 | 138 | 19 | 6 | 52 | 0 |
| unassigned-mo | 192 | 220 | 150 | 25 | 3 | 42 | 0 |
| archdiocese-of-st-paul-and-minneapolis | 183 | 183 | 112 | 55 | 0 | 16 | 0 |
| archdiocese-of-baltimore | 182 | 182 | 83 | 5 | 1 | 53 | 0 |
| diocese-of-green-bay | 179 | 122 | 52 | 26 | 0 | 44 | 0 |
| unassigned-ne | 177 | 222 | 161 | 20 | 0 | 41 | 0 |
| archdiocese-of-washington | 175 | 176 | 35 | 132 | 0 | 9 | 0 |
| diocese-of-albany | 169 | 173 | 92 | 26 | 4 | 50 | 1 |
| archdiocese-of-hartford | 168 | 169 | 87 | 0 | 1 | 78 | 0 |
| diocese-of-rockville-centre | 167 | 167 | 112 | 14 | 9 | 32 | 0 |
| archdiocese-of-san-antonio | 167 | 168 | 75 | 27 | 2 | 34 | 9 |
| archdiocese-of-cincinnati | 166 | 166 | 108 | 39 | 2 | 13 | 1 |
| unassigned-in | 164 | 179 | 91 | 48 | 2 | 38 | 0 |
| archdiocese-of-portland-in-oregon | 157 | 157 | 55 | 25 | 2 | 31 | 0 |
| archdiocese-of-miami | 156 | 156 | 103 | 0 | 1 | 42 | 1 |
| unassigned-wi | 155 | 265 | 164 | 46 | 6 | 49 | 0 |
| unassigned-co | 150 | 185 | 108 | 21 | 7 | 49 | 0 |
| diocese-of-trenton | 147 | 147 | 61 | 30 | 3 | 53 | 0 |
| diocese-of-austin | 145 | 146 | 31 | 86 | 0 | 28 | 1 |
| archdiocese-of-st-louis | 144 | 144 | 83 | 47 | 0 | 11 | 0 |
| archdiocese-of-denver | 142 | 142 | 105 | 0 | 0 | 28 | 0 |
| diocese-of-la-crosse | 140 | 119 | 71 | 21 | 4 | 23 | 0 |
| diocese-of-peoria | 139 | 142 | 100 | 8 | 2 | 32 | 0 |
| diocese-of-providence | 139 | 139 | 75 | 23 | 2 | 39 | 0 |
| unassigned-az | 139 | 180 | 122 | 40 | 2 | 16 | 0 |
| diocese-of-springfield-in-illinois | 138 | 138 | 87 | 21 | 1 | 28 | 1 |
| unassigned-tx | 138 | 183 | 84 | 68 | 4 | 25 | 2 |
| diocese-of-syracuse | 137 | 138 | 83 | 13 | 1 | 41 | 0 |
| unassigned-ct | 137 | 152 | 107 | 6 | 1 | 36 | 2 |
| unassigned-wa | 136 | 142 | 80 | 33 | 1 | 28 | 0 |
| diocese-of-paterson | 134 | 135 | 15 | 107 | 1 | 12 | 0 |
| archdiocese-of-atlanta | 133 | 135 | 94 | 5 | 2 | 21 | 0 |
| archdiocese-of-new-orleans | 131 | 131 | 21 | 10 | 0 | 19 | 0 |
| unassigned-mi | 131 | 269 | 168 | 67 | 4 | 30 | 0 |
| unassigned-la | 131 | 245 | 82 | 123 | 0 | 40 | 0 |
| archdiocese-of-los-angeles | 130 | 41 | 23 | 2 | 5 | 5 | 0 |
| diocese-of-columbus | 130 | 130 | 55 | 33 | 2 | 40 | 0 |
| unassigned-ks | 124 | 161 | 92 | 34 | 12 | 23 | 0 |
| diocese-of-orlando | 122 | 123 | 88 | 6 | 0 | 28 | 1 |
| diocese-of-joliet | 121 | 121 | 80 | 22 | 0 | 19 | 0 |
| diocese-of-worcester | 120 | 121 | 17 | 74 | 0 | 30 | 0 |
| unassigned-nj | 120 | 172 | 102 | 25 | 10 | 30 | 5 |
| unassigned-ky | 119 | 147 | 82 | 25 | 3 | 37 | 0 |
| diocese-of-metuchen | 118 | 119 | 43 | 46 | 0 | 30 | 0 |
| archdiocese-of-seattle | 117 | 117 | 81 | 27 | 0 | 6 | 0 |
| diocese-of-charlotte | 117 | 118 | 62 | 16 | 0 | 40 | 0 |
| diocese-of-richmond | 114 | 124 | 89 | 19 | 1 | 15 | 0 |
| unassigned-mn | 114 | 154 | 95 | 32 | 3 | 24 | 0 |
| diocese-of-dallas | 111 | 112 | 47 | 46 | 0 | 19 | 0 |
| unassigned-wv | 110 | 119 | 97 | 3 | 3 | 16 | 0 |
| diocese-of-scranton | 107 | 110 | 80 | 11 | 0 | 19 | 0 |
| diocese-of-erie | 107 | 107 | 70 | 6 | 3 | 28 | 0 |
| diocese-of-camden | 104 | 104 | 54 | 5 | 2 | 43 | 0 |
| unassigned-fl | 104 | 139 | 83 | 16 | 6 | 34 | 0 |
| diocese-of-allentown | 103 | 105 | 73 | 8 | 1 | 23 | 0 |
| unassigned-ma | 103 | 159 | 68 | 64 | 3 | 24 | 0 |
| diocese-of-fall-river | 100 | 101 | 53 | 22 | 0 | 26 | 0 |
| diocese-of-fort-wayne-south-bend | 100 | 100 | 52 | 22 | 0 | 26 | 0 |
| diocese-of-harrisburg | 100 | 101 | 66 | 31 | 0 | 4 | 0 |
| diocese-of-pittsburgh | 100 | 100 | 12 | 59 | 0 | 9 | 1 |
| diocese-of-rockford | 99 | 99 | 45 | 33 | 1 | 20 | 0 |
| diocese-of-belleville | 98 | 98 | 60 | 8 | 0 | 30 | 0 |
| diocese-of-burlington | 98 | 101 | 13 | 59 | 1 | 28 | 0 |
| unassigned-ia | 98 | 118 | 65 | 32 | 1 | 20 | 0 |
| diocese-of-charleston | 95 | 96 | 69 | 10 | 1 | 16 | 0 |
| diocese-of-fort-worth | 95 | 96 | 34 | 46 | 0 | 16 | 0 |
| diocese-of-greensburg | 94 | 100 | 81 | 8 | 0 | 11 | 0 |
| diocese-of-ogdensburg | 94 | 93 | 30 | 22 | 1 | 38 | 2 |
| unassigned-sd | 94 | 110 | 69 | 17 | 1 | 23 | 0 |
| diocese-of-grand-rapids | 93 | 94 | 68 | 5 | 2 | 19 | 0 |
| diocese-of-rochester | 92 | 92 | 60 | 19 | 1 | 12 | 0 |
| diocese-of-lansing | 90 | 91 | 39 | 29 | 1 | 22 | 0 |
| diocese-of-st-petersburg | 90 | 92 | 63 | 26 | 0 | 3 | 0 |
| diocese-of-little-rock | 89 | 89 | 54 | 11 | 1 | 23 | 0 |
| diocese-of-kansas-city-st-joseph | 88 | 90 | 49 | 23 | 0 | 18 | 0 |
| diocese-of-madison | 88 | 78 | 14 | 38 | 1 | 25 | 0 |
| diocese-of-raleigh | 88 | 93 | 61 | 24 | 1 | 7 | 0 |
| archdiocese-of-san-francisco | 87 | 53 | 26 | 6 | 0 | 16 | 0 |
| archdiocese-of-omaha | 86 | 86 | 67 | 1 | 2 | 9 | 0 |
| diocese-of-altoona-johnstown | 85 | 86 | 48 | 12 | 1 | 19 | 6 |
| unassigned-ms | 85 | 98 | 45 | 31 | 1 | 21 | 0 |
| unassigned-ok | 85 | 114 | 58 | 34 | 4 | 18 | 0 |
| diocese-of-corpus-christi | 84 | 84 | 37 | 27 | 1 | 16 | 3 |
| archdiocese-of-dubuque | 83 | 87 | 24 | 5 | 0 | 34 | 0 |
| diocese-of-youngstown | 82 | 82 | 50 | 9 | 0 | 20 | 3 |
| diocese-of-arlington | 79 | 80 | 54 | 11 | 1 | 14 | 0 |
| diocese-of-sacramento | 79 | 83 | 45 | 36 | 0 | 2 | 0 |
| unassigned-mt | 79 | 105 | 66 | 17 | 0 | 22 | 0 |
| diocese-of-manchester | 79 | 79 | 28 | 32 | 1 | 17 | 1 |
| unassigned-al | 78 | 96 | 37 | 41 | 1 | 17 | 0 |
| archdiocese-of-louisville | 77 | 82 | 46 | 0 | 0 | 24 | 0 |
| diocese-of-brownsville | 77 | 77 | 30 | 16 | 1 | 19 | 11 |
| diocese-of-phoenix | 76 | 76 | 49 | 11 | 1 | 15 | 0 |
| diocese-of-fresno | 75 | 49 | 13 | 15 | 1 | 20 | 0 |
| diocese-of-lafayette-in-louisiana | 75 | 76 | 33 | 15 | 2 | 26 | 0 |
| unassigned-il | 75 | 144 | 101 | 17 | 0 | 25 | 1 |
| diocese-of-jefferson-city | 73 | 73 | 28 | 4 | 0 | 41 | 0 |
| diocese-of-las-cruces | 73 | 73 | 12 | 1 | 0 | 60 | 0 |
| diocese-of-norwich | 73 | 77 | 29 | 8 | 2 | 38 | 0 |
| archdiocese-of-kansas-city-in-kansas | 73 | 74 | 28 | 4 | 1 | 23 | 3 |
| unassigned-or | 73 | 152 | 45 | 88 | 1 | 18 | 0 |
| diocese-of-venice | 72 | 72 | 55 | 7 | 0 | 10 | 0 |
| unassigned-hi | 71 | 76 | 54 | 12 | 3 | 7 | 0 |
| unassigned-dc | 71 | 83 | 59 | 14 | 4 | 6 | 0 |
| diocese-of-wichita | 70 | 74 | 33 | 6 | 34 | 1 | 0 |
| diocese-of-winona-rochester | 70 | 70 | 32 | 15 | 0 | 23 | 0 |
| diocese-of-palm-beach | 67 | 67 | 45 | 8 | 0 | 12 | 2 |
| unassigned-nd | 67 | 99 | 48 | 46 | 1 | 4 | 0 |
| archdiocese-of-oklahoma-city | 66 | 66 | 28 | 6 | 2 | 12 | 0 |
| unassigned-ga | 66 | 80 | 56 | 8 | 3 | 13 | 0 |
| diocese-of-memphis | 65 | 65 | 39 | 3 | 0 | 23 | 0 |
| diocese-of-owensboro | 65 | 65 | 34 | 12 | 1 | 18 | 0 |
| diocese-of-st-augustine | 65 | 71 | 45 | 15 | 0 | 11 | 0 |
| diocese-of-pensacola-tallahassee | 64 | 64 | 34 | 16 | 1 | 12 | 1 |
| diocese-of-boise | 63 | 65 | 33 | 17 | 0 | 15 | 0 |
| diocese-of-tyler | 63 | 65 | 23 | 26 | 0 | 12 | 4 |
| unassigned-tn | 63 | 113 | 54 | 49 | 1 | 9 | 0 |
| diocese-of-orange | 62 | 20 | 20 | 0 | 0 | 0 | 0 |
| diocese-of-savannah | 61 | 62 | 31 | 24 | 3 | 4 | 0 |
| diocese-of-saginaw | 60 | 61 | 41 | 17 | 1 | 2 | 0 |
| diocese-of-tucson | 60 | 60 | 24 | 12 | 0 | 21 | 3 |
| diocese-of-wilmington | 59 | 63 | 41 | 17 | 1 | 4 | 0 |
| diocese-of-san-bernardino | 59 | 34 | 5 | 3 | 1 | 25 | 0 |
| diocese-of-fargo | 58 | 58 | 21 | 28 | 0 | 9 | 0 |
| diocese-of-bridgeport | 58 | 58 | 36 | 6 | 0 | 15 | 1 |
| unassigned-va | 58 | 94 | 67 | 10 | 1 | 16 | 0 |
| diocese-of-kalamazoo | 57 | 57 | 5 | 46 | 0 | 6 | 0 |
| diocese-of-knoxville | 56 | 56 | 20 | 27 | 0 | 9 | 0 |
| diocese-of-des-moines | 55 | 57 | 35 | 13 | 2 | 7 | 0 |
| diocese-of-birmingham | 54 | 54 | 34 | 5 | 0 | 15 | 0 |
| diocese-of-gary | 54 | 54 | 33 | 5 | 0 | 15 | 1 |
| diocese-of-gaylord | 54 | 55 | 46 | 1 | 0 | 7 | 1 |
| diocese-of-st-cloud | 54 | 56 | 40 | 0 | 0 | 16 | 0 |
| diocese-of-marquette | 54 | 55 | 19 | 23 | 0 | 12 | 1 |
| diocese-of-amarillo | 53 | 53 | 0 | 47 | 0 | 6 | 0 |
| diocese-of-santa-rosa | 53 | 13 | 0 | 1 | 1 | 11 | 0 |
| diocese-of-sioux-city | 52 | 52 | 6 | 30 | 0 | 16 | 0 |
| diocese-of-beaumont | 50 | 52 | 15 | 20 | 1 | 14 | 2 |
| unassigned-vt | 50 | 60 | 18 | 31 | 1 | 10 | 0 |
| diocese-of-covington | 49 | 49 | 30 | 5 | 0 | 14 | 0 |
| diocese-of-evansville | 47 | 47 | 20 | 3 | 2 | 22 | 0 |
| diocese-of-el-paso | 47 | 48 | 33 | 1 | 0 | 10 | 4 |
| diocese-of-baton-rouge | 47 | 48 | 19 | 12 | 0 | 16 | 1 |
| unassigned-me | 47 | 84 | 34 | 44 | 1 | 5 | 0 |
| diocese-of-duluth | 46 | 46 | 9 | 20 | 0 | 17 | 0 |
| byzantine-catholic-eparchy-of-passaic | 45 | 45 | 39 | 4 | 0 | 2 | 0 |
| diocese-of-steubenville | 44 | 44 | 23 | 10 | 1 | 8 | 2 |
| unassigned-nh | 43 | 99 | 56 | 33 | 0 | 10 | 0 |
| diocese-of-springfield-massachusetts | 42 | 42 | 19 | 3 | 0 | 20 | 0 |
| diocese-of-superior | 41 | 1 | 1 | 0 | 0 | 0 | 0 |
| unassigned-ut | 41 | 47 | 35 | 2 | 0 | 10 | 0 |
| diocese-of-davenport | 40 | 40 | 22 | 3 | 0 | 15 | 0 |
| diocese-of-nashville | 40 | 40 | 13 | 11 | 0 | 16 | 0 |
| diocese-of-new-ulm | 39 | 41 | 15 | 4 | 0 | 22 | 0 |
| diocese-of-monterey | 39 | 21 | 5 | 7 | 0 | 9 | 0 |
| diocese-of-victoria | 38 | 38 | 1 | 31 | 0 | 6 | 0 |
| eparchy-of-newton | 38 | 41 | 27 | 2 | 1 | 11 | 0 |
| unassigned-nv | 38 | 51 | 27 | 19 | 0 | 5 | 0 |
| diocese-of-laredo | 37 | 37 | 7 | 4 | 0 | 26 | 0 |
| archdiocese-of-mobile | 37 | 39 | 12 | 5 | 0 | 14 | 0 |
| archdiocese-of-santa-fe | 36 | 42 | 18 | 7 | 1 | 15 | 0 |
| diocese-of-spokane | 36 | 37 | 12 | 24 | 0 | 1 | 0 |
| unassigned-ar | 36 | 45 | 28 | 5 | 0 | 12 | 0 |
| diocese-of-springfield-cape-girardeau | 36 | 37 | 17 | 1 | 0 | 19 | 0 |
| archdiocese-of-anchorage-juneau | 34 | 34 | 17 | 3 | 1 | 6 | 0 |
| diocese-of-houma-thibodaux | 34 | 34 | 18 | 13 | 0 | 3 | 0 |
| diocese-of-salt-lake-city | 34 | 40 | 28 | 2 | 0 | 10 | 0 |
| diocese-of-san-angelo | 34 | 34 | 17 | 9 | 0 | 4 | 4 |
| unassigned-sc | 33 | 40 | 31 | 3 | 1 | 5 | 0 |
| archdiocese-of-las-vegas | 32 | 32 | 12 | 6 | 0 | 5 | 0 |
| diocese-of-biloxi | 32 | 32 | 12 | 10 | 0 | 10 | 0 |
| diocese-of-salina | 32 | 32 | 29 | 3 | 0 | 0 | 0 |
| diocese-of-sioux-falls | 32 | 32 | 18 | 14 | 0 | 0 | 0 |
| diocese-of-colorado-springs | 32 | 32 | 14 | 4 | 0 | 14 | 0 |
| unassigned-md | 32 | 63 | 34 | 8 | 2 | 19 | 0 |
| eparchy-of-parma | 31 | 31 | 19 | 4 | 0 | 5 | 3 |
| diocese-of-oakland | 30 | 34 | 20 | 9 | 1 | 4 | 0 |
| diocese-of-lafayette-in-indiana | 29 | 29 | 0 | 18 | 1 | 10 | 0 |
| diocese-of-tulsa | 29 | 29 | 15 | 2 | 0 | 12 | 0 |
| unassigned-de | 29 | 41 | 30 | 7 | 1 | 3 | 0 |
| unassigned-wy | 28 | 33 | 17 | 12 | 0 | 4 | 0 |
| diocese-of-portland-in-maine | 27 | 27 | 4 | 15 | 0 | 8 | 0 |
| unassigned-id | 27 | 38 | 23 | 6 | 1 | 8 | 0 |
| diocese-of-bismarck | 26 | 26 | 7 | 14 | 1 | 4 | 0 |
| holy-protection-of-mary-byzantine-catholic-eparchy-of-phoenix | 26 | 26 | 20 | 0 | 0 | 6 | 0 |
| diocese-of-stockton | 23 | 11 | 0 | 7 | 0 | 4 | 0 |
| eparchy-of-st-maron-of-brooklyn | 22 | 22 | 17 | 0 | 1 | 3 | 1 |
| diocese-of-cheyenne | 22 | 22 | 8 | 7 | 0 | 7 | 0 |
| diocese-of-shreveport | 20 | 20 | 8 | 9 | 0 | 3 | 0 |
| unassigned-ak | 20 | 28 | 11 | 7 | 2 | 5 | 3 |
| unassigned-ri | 20 | 45 | 14 | 22 | 0 | 9 | 0 |
| diocese-of-alexandria | 19 | 19 | 9 | 6 | 0 | 4 | 0 |
| diocese-of-helena | 19 | 19 | 14 | 4 | 0 | 1 | 0 |
| unassigned-nc | 19 | 22 | 14 | 0 | 0 | 8 | 0 |
| diocese-of-rapid-city | 17 | 21 | 14 | 2 | 0 | 5 | 0 |
| diocese-of-grand-island | 17 | 17 | 7 | 1 | 0 | 8 | 1 |
| diocese-of-crookston | 15 | 16 | 8 | 3 | 0 | 5 | 0 |
| diocese-of-jackson | 13 | 13 | 5 | 6 | 0 | 2 | 0 |
| diocese-of-dodge-city | 12 | 12 | 9 | 1 | 0 | 2 | 0 |
| diocese-of-lubbock | 12 | 13 | 4 | 3 | 0 | 4 | 2 |
| maronite-eparchy-of-our-lady-of-lebanon | 10 | 10 | 9 | 0 | 0 | 0 | 1 |
| unassigned-xx | 10 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-fairbanks | 9 | 9 | 3 | 4 | 0 | 2 | 0 |
| diocese-of-st-thomas-vi | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| diocese-of-lake-charles | 9 | 9 | 4 | 3 | 1 | 1 | 0 |
| unassigned-unresolved | 9 | 9 | 6 | 0 | 0 | 3 | 0 |
| chaldean-eparchy-of-saint-thomas-the-apostle | 8 | 8 | 8 | 0 | 0 | 0 | 0 |
| diocese-of-toledo | 8 | 13 | 13 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-eparchy-of-st-josaphat-parma-oh | 7 | 7 | 6 | 0 | 1 | 0 | 0 |
| archdiocese-of-indianapolis | 7 | 7 | 3 | 0 | 0 | 3 | 0 |
| archdiocese-of-the-military-services | 6 | 6 | 4 | 0 | 0 | 2 | 0 |
| diocese-of-great-falls-billings | 6 | 6 | 6 | 0 | 0 | 0 | 0 |
| diocese-of-honolulu | 6 | 6 | 4 | 2 | 0 | 0 | 0 |
| diocese-of-gallup | 5 | 6 | 6 | 0 | 0 | 0 | 0 |
| diocese-of-san-jose | 5 | 5 | 3 | 1 | 0 | 1 | 0 |
| armenian-catholic-eparchy-of-our-lady-of-nareg-in-the-usa-canada | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-archeparchy-of-philadelphia | 3 | 3 | 2 | 0 | 0 | 1 | 0 |
| diocese-of-lincoln | 3 | 3 | 1 | 0 | 0 | 2 | 0 |
| byzantine-catholic-archeparchy-of-pittsburgh | 2 | 2 | 1 | 0 | 0 | 1 | 0 |
| diocese-of-san-diego | 2 | 2 | 0 | 1 | 0 | 1 | 0 |
| diocese-of-pueblo | 2 | 2 | 0 | 0 | 0 | 2 | 0 |
| diocese-of-reno | 2 | 2 | 0 | 2 | 0 | 0 | 0 |
| diocese-of-baker | 2 | 2 | 2 | 0 | 0 | 0 | 0 |
| diocese-of-lexington | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-yakima | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-wheeling-charleston | 1 | 1 | 1 | 0 | 0 | 0 | 0 |

Diocese directory probe: {'challenge': 73, 'rest-pages': 49, 'html': 41, 'open-rest': 12, 'dead': 10, 'html_unknown': 8, 'blocked': 3}.

## Chunk log

- 2026-11-03T21:46:20+00:00: fetched {}; parsed 0 dioceses
- 2026-11-03T21:46:27+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-03T21:47:15+00:00: fetched {}; parsed 0 dioceses
- 2026-11-03T21:47:22+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-03T21:44:30+00:00: fetched {'dns': 7, 'done': 81, 'challenge': 24, 'ok': 40, 'ssl_error': 6, 'blocked_403': 1, 'not_found': 3, 'halted_egress': 0}; parsed 8 dioceses
- 2026-11-03T21:49:41+00:00: top-up ('confession', 'adoration', 'liturgy'): {'sites': 38, 'requests': 0}; 0 sites left
- 2026-11-04T02:07:10+00:00: fetched {}; parsed 0 dioceses
- 2026-11-04T02:07:15+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-04T02:07:31+00:00: fetched {}; parsed 0 dioceses
- 2026-11-04T02:07:36+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-04T02:07:54+00:00: fetched {}; parsed 0 dioceses
- 2026-11-04T02:08:01+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-04T02:08:22+00:00: fetched {}; parsed 0 dioceses
- 2026-11-04T02:08:28+00:00: top-up ('confession', 'adoration', 'liturgy'): {}; 0 sites left
- 2026-11-04T02:08:41+00:00: fetched {}; parsed 0 dioceses
