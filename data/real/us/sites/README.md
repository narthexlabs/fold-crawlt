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
| archdiocese-of-new-york | 318 | 319 | 69 | 226 | 0 | 24 | 0 |
| unassigned-nm | 268 | 1 | 1 | 0 | 0 | 0 | 0 |
| archdiocese-of-boston | 257 | 251 | 75 | 51 | 2 | 123 | 0 |
| archdiocese-of-chicago | 250 | 57 | 30 | 8 | 4 | 15 | 0 |
| unassigned-la | 246 | 3 | 3 | 0 | 0 | 0 | 0 |
| unassigned-ne | 226 | 2 | 2 | 0 | 0 | 0 | 0 |
| unassigned-az | 213 | 4 | 3 | 0 | 0 | 1 | 0 |
| unassigned-mo | 212 | 2 | 2 | 0 | 0 | 0 | 0 |
| archdiocese-of-newark | 199 | 202 | 138 | 46 | 1 | 17 | 0 |
| unassigned-or | 194 | 3 | 2 | 0 | 0 | 1 | 0 |
| unassigned-co | 184 | 2 | 1 | 0 | 0 | 1 | 0 |
| archdiocese-of-st-paul-and-minneapolis | 183 | 182 | 111 | 55 | 0 | 16 | 0 |
| unassigned-ks | 183 | 3 | 3 | 0 | 0 | 0 | 0 |
| archdiocese-of-washington | 175 | 176 | 35 | 132 | 0 | 9 | 0 |
| diocese-of-brooklyn | 173 | 174 | 154 | 11 | 0 | 9 | 0 |
| unassigned-ky | 169 | 2 | 1 | 0 | 0 | 1 | 0 |
| archdiocese-of-galveston-houston | 166 | 61 | 22 | 34 | 0 | 5 | 0 |
| diocese-of-cleveland | 164 | 202 | 132 | 46 | 1 | 23 | 0 |
| unassigned-ok | 146 | 0 | 0 | 0 | 0 | 0 | 0 |
| archdiocese-of-milwaukee | 145 | 146 | 116 | 18 | 2 | 10 | 0 |
| archdiocese-of-st-louis | 144 | 144 | 83 | 50 | 0 | 11 | 0 |
| diocese-of-austin | 137 | 130 | 27 | 79 | 0 | 24 | 0 |
| unassigned-tn | 131 | 4 | 4 | 0 | 0 | 0 | 0 |
| archdiocese-of-san-antonio | 127 | 39 | 17 | 17 | 1 | 4 | 0 |
| diocese-of-buffalo | 126 | 12 | 6 | 1 | 0 | 5 | 0 |
| diocese-of-rockville-centre | 126 | 127 | 87 | 12 | 0 | 28 | 0 |
| unassigned-mt | 119 | 1 | 1 | 0 | 0 | 0 | 0 |
| unassigned-wv | 119 | 1 | 1 | 0 | 0 | 0 | 0 |
| archdiocese-of-seattle | 117 | 117 | 81 | 30 | 0 | 6 | 0 |
| diocese-of-richmond | 114 | 124 | 89 | 19 | 1 | 15 | 0 |
| unassigned-al | 113 | 5 | 4 | 0 | 0 | 1 | 0 |
| unassigned-nd | 109 | 1 | 1 | 0 | 0 | 0 | 0 |
| unassigned-sd | 108 | 3 | 2 | 0 | 0 | 1 | 0 |
| diocese-of-albany | 107 | 109 | 62 | 24 | 4 | 19 | 0 |
| archdiocese-of-atlanta | 105 | 106 | 83 | 4 | 0 | 8 | 0 |
| archdiocese-of-baltimore | 104 | 104 | 57 | 24 | 1 | 6 | 0 |
| unassigned-ms | 102 | 1 | 0 | 0 | 0 | 1 | 0 |
| archdiocese-of-denver | 101 | 102 | 91 | 5 | 0 | 6 | 0 |
| unassigned-nh | 101 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-dallas | 97 | 84 | 29 | 40 | 0 | 15 | 0 |
| diocese-of-joliet | 95 | 0 | 0 | 0 | 0 | 0 | 0 |
| archdiocese-of-hartford | 94 | 95 | 73 | 1 | 0 | 21 | 0 |
| unassigned-ri | 91 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-raleigh | 89 | 92 | 60 | 24 | 1 | 7 | 0 |
| archdiocese-of-dubuque | 88 | 79 | 22 | 24 | 0 | 33 | 0 |
| diocese-of-harrisburg | 88 | 89 | 57 | 30 | 0 | 2 | 0 |
| diocese-of-la-crosse | 88 | 90 | 64 | 19 | 2 | 5 | 0 |
| diocese-of-columbus | 88 | 87 | 32 | 19 | 1 | 35 | 0 |
| unassigned-dc | 84 | 3 | 2 | 1 | 0 | 0 | 0 |
| diocese-of-orlando | 83 | 83 | 73 | 6 | 0 | 4 | 0 |
| unassigned-me | 83 | 1 | 0 | 1 | 0 | 0 | 0 |
| archdiocese-of-omaha | 81 | 81 | 63 | 6 | 3 | 9 | 0 |
| diocese-of-st-petersburg | 81 | 82 | 58 | 23 | 0 | 1 | 0 |
| diocese-of-charleston | 80 | 81 | 67 | 9 | 1 | 4 | 0 |
| diocese-of-scranton | 80 | 83 | 63 | 11 | 0 | 9 | 0 |
| diocese-of-sacramento | 79 | 83 | 45 | 36 | 0 | 2 | 0 |
| diocese-of-charlotte | 76 | 76 | 56 | 16 | 0 | 4 | 0 |
| diocese-of-corpus-christi | 76 | 63 | 32 | 17 | 0 | 12 | 2 |
| unassigned-hi | 76 | 2 | 1 | 0 | 0 | 1 | 0 |
| diocese-of-fort-worth | 74 | 39 | 18 | 19 | 0 | 2 | 0 |
| diocese-of-fort-wayne-south-bend | 73 | 73 | 45 | 21 | 0 | 7 | 0 |
| unassigned-ga | 73 | 4 | 4 | 0 | 0 | 0 | 0 |
| diocese-of-brownsville | 72 | 62 | 22 | 13 | 1 | 17 | 9 |
| diocese-of-wichita | 70 | 5 | 5 | 0 | 0 | 0 | 0 |
| diocese-of-little-rock | 68 | 68 | 47 | 10 | 1 | 10 | 0 |
| diocese-of-peoria | 68 | 71 | 62 | 6 | 0 | 3 | 0 |
| diocese-of-kansas-city-st-joseph | 66 | 68 | 48 | 20 | 0 | 0 | 0 |
| diocese-of-allentown | 65 | 67 | 49 | 6 | 1 | 11 | 0 |
| diocese-of-burlington | 63 | 66 | 9 | 55 | 1 | 1 | 0 |
| diocese-of-camden | 63 | 3 | 2 | 0 | 0 | 1 | 0 |
| diocese-of-las-cruces | 63 | 7 | 5 | 0 | 0 | 2 | 0 |
| unassigned-wi | 63 | 3 | 2 | 0 | 0 | 1 | 0 |
| diocese-of-savannah | 61 | 62 | 31 | 24 | 3 | 4 | 0 |
| diocese-of-grand-rapids | 60 | 60 | 52 | 5 | 0 | 3 | 0 |
| unassigned-vt | 60 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-venice | 59 | 59 | 49 | 5 | 0 | 5 | 0 |
| diocese-of-wilmington | 59 | 62 | 40 | 17 | 1 | 4 | 0 |
| diocese-of-youngstown | 59 | 60 | 47 | 7 | 0 | 6 | 0 |
| unassigned-ut | 59 | 2 | 2 | 0 | 0 | 0 | 0 |
| diocese-of-fall-river | 56 | 56 | 33 | 15 | 0 | 8 | 0 |
| unassigned-tx | 56 | 23 | 9 | 14 | 0 | 0 | 0 |
| unassigned-ca | 56 | 56 | 36 | 8 | 1 | 11 | 0 |
| diocese-of-ogdensburg | 55 | 55 | 21 | 19 | 1 | 14 | 0 |
| diocese-of-belleville | 55 | 55 | 29 | 3 | 0 | 23 | 0 |
| diocese-of-st-cloud | 54 | 56 | 40 | 0 | 0 | 16 | 0 |
| diocese-of-norwich | 53 | 8 | 2 | 3 | 1 | 2 | 0 |
| diocese-of-tyler | 53 | 42 | 13 | 18 | 0 | 11 | 0 |
| diocese-of-memphis | 52 | 52 | 39 | 3 | 0 | 10 | 0 |
| diocese-of-saginaw | 52 | 54 | 37 | 16 | 1 | 0 | 0 |
| diocese-of-palm-beach | 51 | 51 | 41 | 6 | 0 | 4 | 0 |
| diocese-of-st-augustine | 51 | 58 | 38 | 12 | 0 | 8 | 0 |
| unassigned-md | 50 | 7 | 4 | 1 | 0 | 2 | 0 |
| diocese-of-beaumont | 49 | 49 | 14 | 19 | 1 | 13 | 2 |
| unassigned-ct | 49 | 7 | 4 | 0 | 0 | 3 | 0 |
| diocese-of-owensboro | 48 | 48 | 31 | 10 | 0 | 7 | 0 |
| diocese-of-baton-rouge | 48 | 48 | 19 | 12 | 0 | 16 | 1 |
| diocese-of-des-moines | 47 | 51 | 33 | 13 | 1 | 4 | 0 |
| diocese-of-altoona-johnstown | 45 | 45 | 29 | 6 | 1 | 9 | 0 |
| unassigned-ar | 45 | 2 | 2 | 0 | 0 | 0 | 0 |
| unassigned-nv | 45 | 3 | 2 | 1 | 0 | 0 | 0 |
| diocese-of-boise | 43 | 46 | 28 | 16 | 0 | 2 | 0 |
| diocese-of-covington | 43 | 43 | 29 | 5 | 0 | 9 | 0 |
| unassigned-de | 43 | 0 | 0 | 0 | 0 | 0 | 0 |
| diocese-of-gary | 42 | 42 | 30 | 5 | 0 | 7 | 0 |
| diocese-of-amarillo | 42 | 42 | 0 | 37 | 0 | 5 | 0 |
| archdiocese-of-los-angeles | 41 | 41 | 23 | 8 | 5 | 5 | 0 |
| unassigned-sc | 41 | 4 | 4 | 0 | 0 | 0 | 0 |
| diocese-of-gaylord | 40 | 40 | 38 | 1 | 0 | 1 | 0 |
| diocese-of-davenport | 40 | 40 | 22 | 3 | 0 | 15 | 0 |
| unassigned-mn | 40 | 6 | 4 | 2 | 0 | 0 | 0 |
| diocese-of-el-paso | 39 | 12 | 10 | 0 | 0 | 0 | 2 |
| diocese-of-fresno | 39 | 33 | 11 | 9 | 0 | 13 | 0 |
| unassigned-id | 38 | 2 | 2 | 0 | 0 | 0 | 0 |
| archdiocese-of-santa-fe | 36 | 42 | 18 | 8 | 1 | 15 | 0 |
| diocese-of-spokane | 36 | 2 | 0 | 2 | 0 | 0 | 0 |
| eparchy-of-newton | 35 | 36 | 24 | 2 | 1 | 9 | 0 |
| archdiocese-of-anchorage-juneau | 34 | 11 | 7 | 0 | 0 | 3 | 0 |
| diocese-of-jefferson-city | 34 | 34 | 22 | 3 | 0 | 9 | 0 |
| unassigned-wy | 33 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-salina | 32 | 32 | 29 | 3 | 0 | 0 | 0 |
| diocese-of-sioux-falls | 32 | 32 | 18 | 14 | 0 | 0 | 0 |
| diocese-of-birmingham | 32 | 32 | 21 | 3 | 0 | 8 | 0 |
| diocese-of-colorado-springs | 32 | 32 | 14 | 4 | 0 | 14 | 0 |
| diocese-of-duluth | 31 | 31 | 4 | 11 | 0 | 16 | 0 |
| diocese-of-oakland | 30 | 34 | 20 | 9 | 1 | 4 | 0 |
| diocese-of-san-angelo | 27 | 11 | 4 | 5 | 0 | 1 | 1 |
| unassigned-ak | 27 | 6 | 2 | 2 | 0 | 2 | 0 |
| archdiocese-of-las-vegas | 26 | 2 | 1 | 1 | 0 | 0 | 0 |
| archdiocese-of-louisville | 24 | 3 | 1 | 1 | 0 | 1 | 0 |
| diocese-of-fargo | 24 | 24 | 8 | 9 | 0 | 7 | 0 |
| diocese-of-bismarck | 23 | 23 | 6 | 13 | 0 | 4 | 0 |
| diocese-of-cheyenne | 22 | 22 | 8 | 7 | 0 | 7 | 0 |
| unassigned-ia | 21 | 4 | 4 | 0 | 0 | 0 | 0 |
| diocese-of-biloxi | 21 | 21 | 5 | 8 | 0 | 8 | 0 |
| unassigned-ny | 21 | 21 | 17 | 0 | 0 | 4 | 0 |
| archdiocese-of-philadelphia | 20 | 20 | 13 | 4 | 0 | 3 | 0 |
| diocese-of-shreveport | 20 | 20 | 8 | 9 | 0 | 3 | 0 |
| diocese-of-orange | 19 | 20 | 20 | 0 | 0 | 0 | 0 |
| unassigned-pa | 19 | 21 | 18 | 0 | 2 | 1 | 0 |
| archdiocese-of-cincinnati | 18 | 18 | 12 | 4 | 1 | 1 | 0 |
| diocese-of-arlington | 17 | 17 | 12 | 5 | 0 | 0 | 0 |
| diocese-of-rapid-city | 17 | 5 | 5 | 0 | 0 | 0 | 0 |
| unassigned-il | 16 | 18 | 13 | 1 | 0 | 4 | 0 |
| diocese-of-crookston | 16 | 16 | 8 | 3 | 0 | 5 | 0 |
| diocese-of-alexandria | 14 | 14 | 8 | 6 | 0 | 0 | 0 |
| unassigned-fl | 14 | 14 | 9 | 1 | 1 | 3 | 0 |
| unassigned-ma | 14 | 14 | 7 | 2 | 0 | 5 | 0 |
| archdiocese-of-detroit | 13 | 13 | 8 | 2 | 0 | 3 | 0 |
| diocese-of-laredo | 12 | 8 | 4 | 1 | 0 | 3 | 0 |
| archdiocese-of-san-francisco | 12 | 36 | 21 | 6 | 0 | 9 | 0 |
| diocese-of-dodge-city | 12 | 12 | 9 | 1 | 0 | 2 | 0 |
| unassigned-oh | 11 | 11 | 9 | 1 | 0 | 1 | 0 |
| unassigned-mi | 10 | 10 | 8 | 1 | 0 | 1 | 0 |
| diocese-of-manchester | 10 | 10 | 4 | 4 | 1 | 1 | 0 |
| diocese-of-lubbock | 9 | 4 | 1 | 2 | 0 | 0 | 1 |
| diocese-of-metuchen | 9 | 9 | 1 | 1 | 0 | 7 | 0 |
| unassigned-unresolved | 9 | 9 | 6 | 0 | 0 | 3 | 0 |
| chaldean-eparchy-of-saint-thomas-the-apostle | 8 | 8 | 8 | 0 | 0 | 0 | 0 |
| archdiocese-of-new-orleans | 8 | 8 | 2 | 6 | 0 | 0 | 0 |
| unassigned-nj | 8 | 8 | 6 | 1 | 0 | 1 | 0 |
| diocese-of-providence | 8 | 8 | 3 | 2 | 0 | 3 | 0 |
| diocese-of-evansville | 7 | 7 | 2 | 1 | 1 | 3 | 0 |
| diocese-of-lansing | 7 | 7 | 2 | 3 | 0 | 2 | 0 |
| diocese-of-toledo | 7 | 12 | 12 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-eparchy-of-st-josaphat-parma-oh | 7 | 7 | 6 | 0 | 1 | 0 | 0 |
| archdiocese-of-the-military-services | 6 | 6 | 4 | 0 | 0 | 2 | 0 |
| diocese-of-great-falls-billings | 6 | 6 | 6 | 0 | 0 | 0 | 0 |
| diocese-of-bridgeport | 6 | 6 | 4 | 2 | 0 | 0 | 0 |
| archdiocese-of-miami | 6 | 6 | 5 | 0 | 0 | 1 | 0 |
| diocese-of-honolulu | 6 | 6 | 4 | 2 | 0 | 0 | 0 |
| diocese-of-worcester | 6 | 6 | 0 | 2 | 0 | 4 | 0 |
| diocese-of-trenton | 6 | 6 | 3 | 0 | 1 | 2 | 0 |
| unassigned-wa | 6 | 8 | 6 | 1 | 0 | 1 | 0 |
| diocese-of-gallup | 5 | 6 | 6 | 0 | 0 | 0 | 0 |
| diocese-of-greensburg | 5 | 11 | 11 | 0 | 0 | 0 | 0 |
| diocese-of-new-ulm | 5 | 7 | 5 | 1 | 0 | 1 | 0 |
| diocese-of-san-jose | 5 | 5 | 3 | 1 | 0 | 1 | 0 |
| archdiocese-of-portland-in-oregon | 5 | 5 | 3 | 2 | 0 | 0 | 0 |
| diocese-of-monterey | 5 | 5 | 4 | 1 | 0 | 0 | 0 |
| diocese-of-phoenix | 4 | 4 | 1 | 2 | 0 | 1 | 0 |
| diocese-of-rockford | 4 | 4 | 2 | 1 | 0 | 1 | 0 |
| unassigned-in | 4 | 4 | 2 | 0 | 0 | 2 | 0 |
| unassigned-nc | 4 | 4 | 3 | 0 | 0 | 1 | 0 |
| diocese-of-paterson | 4 | 4 | 1 | 2 | 0 | 1 | 0 |
| diocese-of-syracuse | 4 | 4 | 3 | 0 | 0 | 1 | 0 |
| diocese-of-pittsburgh | 4 | 4 | 1 | 2 | 0 | 1 | 0 |
| unassigned-xx | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| diocese-of-green-bay | 3 | 3 | 3 | 0 | 0 | 0 | 0 |
| diocese-of-tucson | 3 | 3 | 3 | 0 | 0 | 0 | 0 |
| ukrainian-catholic-archeparchy-of-philadelphia | 3 | 3 | 2 | 0 | 0 | 1 | 0 |
| diocese-of-marquette | 3 | 3 | 3 | 0 | 0 | 0 | 0 |
| maronite-eparchy-of-our-lady-of-lebanon | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| eparchy-of-st-maron-of-brooklyn | 2 | 1 | 0 | 0 | 0 | 1 | 0 |
| archdiocese-of-mobile | 2 | 2 | 0 | 1 | 0 | 1 | 0 |
| diocese-of-san-diego | 2 | 2 | 0 | 1 | 0 | 1 | 0 |
| diocese-of-san-bernardino | 2 | 2 | 0 | 1 | 0 | 1 | 0 |
| diocese-of-pensacola-tallahassee | 2 | 2 | 1 | 1 | 0 | 0 | 0 |
| diocese-of-springfield-in-illinois | 2 | 2 | 1 | 1 | 0 | 0 | 0 |
| archdiocese-of-indianapolis | 2 | 2 | 1 | 0 | 0 | 1 | 0 |
| diocese-of-springfield-cape-girardeau | 2 | 2 | 1 | 0 | 0 | 1 | 0 |
| diocese-of-jackson | 2 | 2 | 1 | 1 | 0 | 0 | 0 |
| diocese-of-reno | 2 | 2 | 0 | 2 | 0 | 0 | 0 |
| diocese-of-rochester | 2 | 2 | 0 | 0 | 0 | 2 | 0 |
| diocese-of-tulsa | 2 | 2 | 1 | 1 | 0 | 0 | 0 |
| diocese-of-baker | 2 | 2 | 2 | 0 | 0 | 0 | 0 |
| byzantine-catholic-archeparchy-of-pittsburgh | 2 | 2 | 1 | 0 | 0 | 1 | 0 |
| diocese-of-victoria | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-fairbanks | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-pueblo | 1 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-sioux-city | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-lafayette-in-indiana | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| archdiocese-of-kansas-city-in-kansas | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-lexington | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-houma-thibodaux | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-lake-charles | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-lafayette-in-louisiana | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-kalamazoo | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-winona-rochester | 1 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-helena | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-grand-island | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-lincoln | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| byzantine-catholic-eparchy-of-passaic | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-steubenville | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| archdiocese-of-oklahoma-city | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-erie | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-nashville | 1 | 1 | 0 | 0 | 0 | 1 | 0 |
| diocese-of-knoxville | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-salt-lake-city | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-yakima | 1 | 1 | 0 | 1 | 0 | 0 | 0 |
| diocese-of-superior | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-wheeling-charleston | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| unassigned-va | 1 | 2 | 2 | 0 | 0 | 0 | 0 |

Diocese directory probe: {'challenge': 73, 'rest-pages': 49, 'html': 41, 'open-rest': 12, 'dead': 10, 'html_unknown': 8, 'blocked': 3}.

## Chunk log

- 2026-11-01T05:09:00+00:00: fetched {'robots_disallowed': 6, 'done': 1282, 'unreachable_via_proxy': 183, 'challenge': 251, 'ssl_error': 64, 'not_found': 22, 'ok': 722, 'parked': 6, 'timeout': 2, 'http_5xx': 8, 'conn_error': 29, 'http_4xx': 1, 'cut': 16, 'blocked_429': 2, 'egress_refused': 56, 'http_307': 1, 'dns': 1, 'halted_egress': 0}; parsed 21 dioceses
- 2026-11-01T05:18:37+00:00: fetched {'dns': 4, 'done': 57, 'ok': 6, 'timeout': 52, 'cut': 5, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T05:56:12+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T05:56:21+00:00: fetched {'ok': 1, 'cut': 1, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T06:14:07+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T07:19:40+00:00: fetched {'dns': 4, 'done': 57, 'challenge': 35, 'ssl_error': 2, 'parked': 1, 'ok': 14, 'blocked_403': 1, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T07:27:47+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T07:34:43+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T07:34:53+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T07:49:45+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T08:04:48+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T08:19:52+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T08:34:55+00:00: fetched {'dns': 1, 'done': 1, 'halted_egress': 0}; parsed 0 dioceses
- 2026-11-01T08:49:57+00:00: fetched {}; parsed 0 dioceses
- 2026-11-01T09:04:51+00:00: fetched {'robots_disallowed': 1, 'done': 32, 'challenge': 3, 'ok': 25, 'dns': 1, 'timeout': 2, 'halted_egress': 0}; parsed 0 dioceses
