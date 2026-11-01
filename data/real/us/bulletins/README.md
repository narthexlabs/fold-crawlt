# Groups from US parish bulletins (`data/real/us/bulletins/<diocese>/<domain>.json`)

Lane G9, rebuilt 2026-11-01 by `ingest/us/bulletins/run.py`. Files only; nothing here is in Supabase. Raw PDFs, text, OCR and the
HTTP cache stay in `data/raw/us/bulletins/` (gitignored).

Why: parish websites list formal ministries; the informal groups a newcomer looks for (men's, women's, moms, young adults,
Bible study, prayer groups) are announced in the weekly bulletin. Goal metric: parishes whose own site gave Mass times AND groups.

## Resume

```bash
python3 -I ingest/us/bulletins/run.py --minutes 100 [--workers 8] [--dioceses diocese-of-austin] [--refresh]
python3 -I ingest/us/bulletins/run.py --extract-only      # re-extract from cached text, no network
bash scripts/us/bulletins_chunk.sh [minutes]               # pull, run, commit per diocese, push (for a scheduled task)
```
Input: `data/real/us/sites/<diocese>/<domain>.json` with `crawl_status: ok` and a `bulletin.url`. A site with a state file in
`data/raw/us/bulletins/_state/` is skipped (new sites from the site crawl are picked up on the next run). Order: Mass times but
no groups first, then times + groups, then the rest; links that only point at discovermass.com last (no discovermass.com request is
ever made; they resolve only if the parish's own page links a PDF).

## File shape
Same as California's (`data/real/california/bulletins/README.md`), one file per parish website instead of per diocese: `diocese`,
`domain`, `site_file`, `parishes[]` (church_id, name, city), `status`, `had_mass_times`, `site_groups_before`,
`gained_first_groups`, `issues[]` (url, date, pages, ocr, fetched_at), `tried[]`, `groups[]` (id, name, category, subcategory,
audience, language, informal, informal_kind, meets, next_meeting, location, contact "ask the parish office", source_label
"From the parish bulletin, <date>", source_url, last_seen, confidence, flags, also_on_parish_website).

## Integration note for `scripts/us/build_us_static.py`
Exactly as California's merge (steps 1-6 of the California README), keyed by the site file:
1. For each parish, read `data/real/us/bulletins/<diocese>/<site_key>.json` for the site(s) attached to it (the sites file
   lists `matched_church_ids`; the bulletin file repeats them in `parishes[].church_id`).
2. Skip groups whose `last_seen` is over 180 days before the build date, and `informal: false` groups whose `subcategory` the
   site groups already have.
3. Dedupe against the site's groups by normalised name and by `(subcategory, language)`: keep the site record, add the bulletin as a
   second source, fill `meets` from the bulletin when the site has none.
4. Append the rest with `source_url`, `meets` = `meets.text` (or "Next: <date>"), `confidence`, and the label
   **"From the parish bulletin, <Month D, YYYY>"** (`source_label`). Contact stays "ask the parish office".
5. Coverage "groups" source becomes "the parish's own website and bulletin".

## Rules kept
Honest FindTheChurch user agent, HTTPS only, robots.txt and Crawl-delay honoured, at most 1 request per second and one in flight per
host, a challenge/403/429 is recorded and the host not asked again in the run, no discovermass.com request, only links and container
codes already seen on the parish's pages (never enumerated). Extraction is rule-based with California's privacy screens; no lay names,
phones or e-mails are stored.

## Measures (2026-11-01)
`times_no_groups_sites` = sites that had Mass times but no groups on their own site (the target); `gained_first_groups` = of
those, sites where the bulletin supplied at least one group.

| diocese | sites | PDFs got | issues read | times, no site groups | sites with bulletin groups | gained first groups | groups | informal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| archdiocese-of-anchorage-juneau | 2 | 2 | 3 | 1 | 2 | 1 | 19 | 16 |
| archdiocese-of-atlanta | 35 | 17 | 29 | 9 | 14 | 3 | 93 | 83 |
| archdiocese-of-baltimore | 41 | 4 | 6 | 7 | 2 | 0 | 6 | 6 |
| archdiocese-of-boston | 46 | 20 | 37 | 9 | 17 | 2 | 74 | 65 |
| archdiocese-of-chicago | 15 | 9 | 16 | 2 | 7 | 0 | 28 | 25 |
| archdiocese-of-cincinnati | 4 | 1 | 2 | 0 | 1 | 0 | 3 | 3 |
| archdiocese-of-denver | 78 | 33 | 59 | 14 | 28 | 6 | 107 | 96 |
| archdiocese-of-detroit | 3 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| archdiocese-of-dubuque | 14 | 2 | 3 | 3 | 0 | 0 | 0 | 0 |
| archdiocese-of-galveston-houston | 14 | 7 | 13 | 6 | 7 | 3 | 59 | 52 |
| archdiocese-of-hartford | 25 | 8 | 13 | 4 | 6 | 4 | 26 | 16 |
| archdiocese-of-miami | 1 | 1 | 2 | 1 | 1 | 1 | 1 | 0 |
| archdiocese-of-milwaukee | 24 | 6 | 9 | 24 | 4 | 4 | 18 | 15 |
| archdiocese-of-new-york | 8 | 7 | 12 | 8 | 5 | 5 | 18 | 17 |
| archdiocese-of-newark | 42 | 23 | 34 | 42 | 18 | 18 | 67 | 53 |
| archdiocese-of-omaha | 11 | 3 | 6 | 11 | 2 | 2 | 6 | 4 |
| archdiocese-of-philadelphia | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| archdiocese-of-san-antonio | 3 | 3 | 6 | 3 | 3 | 3 | 15 | 13 |
| archdiocese-of-san-francisco | 5 | 4 | 8 | 5 | 3 | 3 | 14 | 9 |
| archdiocese-of-santa-fe | 13 | 5 | 9 | 5 | 5 | 5 | 30 | 20 |
| archdiocese-of-seattle | 9 | 3 | 5 | 9 | 2 | 2 | 29 | 27 |
| archdiocese-of-st-louis | 67 | 16 | 29 | 15 | 15 | 5 | 65 | 55 |
| archdiocese-of-st-paul-and-minneapolis | 9 | 5 | 9 | 8 | 3 | 2 | 4 | 1 |
| archdiocese-of-washington | 3 | 3 | 5 | 3 | 1 | 1 | 7 | 7 |
| byzantine-catholic-archeparchy-of-pittsburgh | 1 | 1 | 2 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-albany | 10 | 2 | 4 | 10 | 2 | 2 | 8 | 7 |
| diocese-of-alexandria | 2 | 2 | 4 | 2 | 2 | 2 | 15 | 12 |
| diocese-of-allentown | 8 | 4 | 8 | 8 | 4 | 4 | 21 | 15 |
| diocese-of-altoona-johnstown | 5 | 2 | 4 | 5 | 2 | 2 | 7 | 7 |
| diocese-of-arlington | 3 | 1 | 2 | 3 | 1 | 1 | 12 | 12 |
| diocese-of-austin | 6 | 1 | 2 | 5 | 1 | 1 | 10 | 10 |
| diocese-of-baton-rouge | 4 | 2 | 4 | 4 | 1 | 1 | 2 | 2 |
| diocese-of-beaumont | 4 | 2 | 4 | 4 | 2 | 2 | 6 | 6 |
| diocese-of-belleville | 4 | 4 | 7 | 4 | 3 | 3 | 16 | 12 |
| diocese-of-birmingham | 3 | 3 | 6 | 3 | 3 | 3 | 16 | 15 |
| diocese-of-bismarck | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-boise | 7 | 3 | 6 | 7 | 3 | 3 | 16 | 13 |
| diocese-of-bridgeport | 2 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| diocese-of-brooklyn | 35 | 31 | 56 | 35 | 27 | 27 | 99 | 81 |
| diocese-of-brownsville | 5 | 4 | 7 | 5 | 3 | 3 | 7 | 5 |
| diocese-of-buffalo | 2 | 2 | 4 | 2 | 2 | 2 | 8 | 6 |
| diocese-of-charleston | 15 | 10 | 16 | 15 | 10 | 10 | 52 | 44 |
| diocese-of-charlotte | 8 | 7 | 12 | 8 | 6 | 6 | 33 | 24 |
| diocese-of-cleveland | 22 | 6 | 12 | 22 | 4 | 4 | 9 | 6 |
| diocese-of-colorado-springs | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-columbus | 4 | 3 | 5 | 4 | 3 | 3 | 17 | 13 |
| diocese-of-corpus-christi | 7 | 7 | 13 | 7 | 7 | 7 | 32 | 22 |
| diocese-of-covington | 2 | 2 | 4 | 2 | 2 | 2 | 4 | 3 |
| diocese-of-dallas | 16 | 5 | 8 | 6 | 4 | 3 | 29 | 26 |
| diocese-of-davenport | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-des-moines | 9 | 3 | 4 | 9 | 2 | 2 | 11 | 10 |
| diocese-of-el-paso | 8 | 5 | 8 | 2 | 4 | 1 | 30 | 24 |
| diocese-of-fall-river | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-fargo | 1 | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-fort-wayne-south-bend | 5 | 3 | 4 | 5 | 3 | 3 | 4 | 2 |
| diocese-of-fort-worth | 8 | 2 | 4 | 6 | 2 | 2 | 11 | 8 |
| diocese-of-gallup | 2 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| diocese-of-gary | 3 | 1 | 2 | 3 | 1 | 1 | 3 | 3 |
| diocese-of-gaylord | 3 | 2 | 3 | 3 | 1 | 1 | 6 | 5 |
| diocese-of-grand-rapids | 41 | 8 | 13 | 7 | 6 | 2 | 32 | 29 |
| diocese-of-great-falls-billings | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-harrisburg | 6 | 4 | 8 | 6 | 4 | 4 | 13 | 10 |
| diocese-of-honolulu | 2 | 2 | 4 | 2 | 2 | 2 | 5 | 4 |
| diocese-of-jackson | 1 | 1 | 1 | 1 | 1 | 1 | 4 | 3 |
| diocese-of-jefferson-city | 2 | 1 | 2 | 2 | 1 | 1 | 4 | 4 |
| diocese-of-kansas-city-st-joseph | 13 | 4 | 6 | 13 | 3 | 3 | 8 | 8 |
| diocese-of-la-crosse | 25 | 12 | 23 | 25 | 10 | 10 | 41 | 33 |
| diocese-of-laredo | 3 | 3 | 5 | 1 | 3 | 1 | 13 | 10 |
| diocese-of-little-rock | 10 | 5 | 9 | 10 | 5 | 5 | 26 | 25 |
| diocese-of-lubbock | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| diocese-of-manchester | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-marquette | 1 | 1 | 2 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-memphis | 23 | 10 | 14 | 3 | 6 | 2 | 22 | 19 |
| diocese-of-new-ulm | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-oakland | 2 | 1 | 2 | 2 | 1 | 1 | 2 | 1 |
| diocese-of-ogdensburg | 2 | 2 | 2 | 2 | 2 | 2 | 4 | 4 |
| diocese-of-orange | 3 | 0 | 0 | 3 | 0 | 0 | 0 | 0 |
| diocese-of-orlando | 13 | 10 | 18 | 13 | 7 | 7 | 30 | 26 |
| diocese-of-owensboro | 12 | 4 | 7 | 12 | 3 | 3 | 21 | 17 |
| diocese-of-palm-beach | 5 | 4 | 6 | 5 | 2 | 2 | 18 | 14 |
| diocese-of-paterson | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| diocese-of-peoria | 52 | 20 | 37 | 18 | 20 | 13 | 83 | 70 |
| diocese-of-providence | 1 | 1 | 2 | 1 | 1 | 1 | 5 | 3 |
| diocese-of-raleigh | 30 | 14 | 24 | 7 | 9 | 3 | 47 | 43 |
| diocese-of-richmond | 16 | 9 | 12 | 16 | 8 | 8 | 35 | 31 |
| diocese-of-rockville-centre | 5 | 4 | 8 | 5 | 4 | 4 | 20 | 13 |
| diocese-of-sacramento | 11 | 6 | 10 | 11 | 3 | 3 | 10 | 10 |
| diocese-of-saginaw | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-salina | 1 | 1 | 2 | 1 | 1 | 1 | 2 | 1 |
| diocese-of-san-angelo | 4 | 1 | 2 | 2 | 1 | 0 | 3 | 2 |
| diocese-of-san-jose | 2 | 1 | 2 | 0 | 1 | 0 | 1 | 1 |
| diocese-of-savannah | 7 | 2 | 2 | 7 | 2 | 2 | 8 | 6 |
| diocese-of-scranton | 9 | 5 | 10 | 9 | 4 | 4 | 8 | 6 |
| diocese-of-shreveport | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-sioux-falls | 8 | 3 | 6 | 8 | 3 | 3 | 30 | 27 |
| diocese-of-springfield-in-illinois | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-st-augustine | 9 | 2 | 3 | 9 | 2 | 2 | 9 | 8 |
| diocese-of-st-cloud | 12 | 3 | 6 | 12 | 3 | 3 | 11 | 9 |
| diocese-of-st-petersburg | 11 | 4 | 7 | 11 | 2 | 2 | 22 | 19 |
| diocese-of-steubenville | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-toledo | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-trenton | 1 | 1 | 2 | 1 | 1 | 1 | 2 | 1 |
| diocese-of-tucson | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| diocese-of-tyler | 6 | 4 | 8 | 5 | 4 | 3 | 18 | 15 |
| diocese-of-venice | 2 | 1 | 1 | 2 | 1 | 1 | 4 | 3 |
| diocese-of-wilmington | 4 | 2 | 4 | 4 | 1 | 1 | 2 | 1 |
| diocese-of-youngstown | 5 | 1 | 2 | 5 | 1 | 1 | 4 | 4 |
| eparchy-of-newton | 7 | 5 | 10 | 7 | 1 | 1 | 2 | 2 |
| ukrainian-catholic-eparchy-of-st-josaphat-parma-oh | 2 | 1 | 2 | 2 | 1 | 1 | 2 | 2 |
| unassigned-al | 1 | 1 | 2 | 1 | 1 | 1 | 10 | 10 |
| unassigned-ar | 1 | 1 | 2 | 1 | 1 | 1 | 7 | 7 |
| unassigned-ca | 7 | 4 | 7 | 7 | 2 | 2 | 4 | 4 |
| unassigned-fl | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| unassigned-il | 3 | 2 | 3 | 3 | 2 | 2 | 6 | 6 |
| unassigned-la | 1 | 1 | 2 | 1 | 1 | 1 | 5 | 4 |
| unassigned-md | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| unassigned-ny | 1 | 1 | 1 | 1 | 1 | 1 | 7 | 4 |
| unassigned-oh | 1 | 1 | 2 | 1 | 1 | 1 | 9 | 9 |
| unassigned-pa | 2 | 1 | 2 | 2 | 1 | 1 | 5 | 4 |
| unassigned-ri | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| unassigned-tx | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| **all** | **1092** | **481** | **839** | **673** | **394** | **288** | **1800** | **1511** |

Site statuses: ok 481, no_pdf_found 255, lpi_app_no_code 237, failed:ok,robots_disallowed 38, failed:ok,skipped_policy_no_discovermass 38, not_a_bulletin_link 15, challenge 11, failed:not_a_pdf,ok 11, folder_needs_javascript 3, blocked 1, failed:not_found,ok 1, failed:ok,pdf_too_large 1.

Informal groups by kind: youth 209, ocia_inquiry 185, kofc 179, prayer_group 137, bible_study 125, womens 95, svdp 93, mens 92, rosary_group 67, legion_of_mary 52, grief 46, small_groups 42, catholic_daughters 34, young_adults 33, cursillo 27, charismatic 23, couples 23, seniors 22, moms 13, fraternal_other 5, columbiettes 3, alpha 3, crhp 2, divorced_separated 1.
