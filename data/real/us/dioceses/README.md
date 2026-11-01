# US diocese directories (lanes G4, G4b)

Each see's **own** parish directory, read from its own website, so the national parish-site crawler
(`ingest/us/sites/run_state.py`, lane G4) has a list of parish websites per diocese that does not depend on
DiscoverMass. Files only; nothing here is in Supabase.

## Files
- `directory_status.json` — one row per see (196) from `ingest/us/dioceses/directory_probe.py` (lane G4): which door
  the site has (`open-rest`, `rest-pages`, `html`, `html_unknown`, `challenge`, `blocked`, `dead`), plus, after the
  reader pass, a `reader` object (result, reader used, CMS guess, counts of the `parishes.json` on disk) and
  `reader_totals`.
- `reader_status.json` — the reader pass per see (lane G4b): result (`written`, `kept-existing`, `no-list`,
  `rejected-not-a-parish-list`, `skipped-crawl-delay`), reader, cms_guess, request counts, which list pages and detail
  pages were read.
- `<slug>/parishes.json` — the list. Top level: `diocese`, `name`, `source`, `source_label`
  (`diocese-directory-rest` / `diocese-directory-pages` / `diocese-directory-html`), `fetched_at`, `method`, `cms_guess`,
  `note`, `count`, `with_website`, `with_office_phone`, `with_office_email`, `records`.
  Each record: `name`, `type_in_source`, `address`, `city`, `state`, `zip`, `lat`, `lng`, `website`, `website_basis`
  (how the website was chosen: `vendor-field`, `rest-field`, `labelled` = link text/label says "website",
  `domain-text` = the link text is the domain, `first-external` = first outside link on the parish's own page that is
  not page chrome, `list-block` / `list-labelled` = link inside the parish's block on a list page; prefixed `page-`
  when read from the parish page behind a REST record), `office_phone`, `office_email`, `source_url`, `rest_id`,
  `confidence` and `extracted_by`.
  - `confidence`: `rest` (a parish post type in WordPress REST; fields guessed from its custom fields), `page` (read from
    the parish's own page on the diocese site), `html-vendor` (a known directory widget's structured fields),
    `html-generic` (a list page read by the generic block rule). `html-generic` is the loosest: spot-check it.

## Privacy
Only institutional data is kept: parish name, address, coordinates, website, and (Matthew, 2026-11-01) the parish
**office** phone and general office e-mail. No named person's phone or e-mail: pastor/staff/contact fields are never
read; an e-mail whose local part reads as a person's name (`john.smith@`, `jsmith@`, `maria@`, `frjoe@`) is dropped
(`ingest/us/dioceses/dirlib.office_email_ok`, using the shared given-name and surname lists of
`ingest/public/privacy.py`); a phone is taken from a vendor's parish-level phone field or from text labelled
office/parish/phone, never next to a person's title. A number or mailbox shared by 4+ parishes in one list (the chancery
footer) is dropped, as are coordinates and addresses repeated across a list.

## Method (lane G4b, 2026-11-01)
All reads: honest user agent `FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`, robots.txt read
and obeyed, Crawl-delay honoured, one request at a time and at most ~1 per second per host, HTTPS only (sandbox).
A challenge, 403 or 429 is recorded and never worked around. dsj.org (San José, Crawl-delay 600) is skipped
(`skipped-crawl-delay`). Every response is cached in `data/raw/us/dioceses/<slug>/` (gitignored), so a rerun is
offline except for what is missing.

- `ingest/us/dioceses/directory_pages.py` — the 12 `open-rest` and 49 `rest-pages` sees. For `open-rest`: the REST
  records rarely carry the website (Charlotte 0/95), so each record's `link` (its page on the diocese site) is read for
  the website, address, coordinates and office contacts. For `rest-pages`: WordPress REST page search + the sitemap's
  parish-like sub-sitemaps + the directory page, then the generic engine.
- `ingest/us/dioceses/directory_html.py` — the 41 `html` and 8 `html_unknown` sees, generic engine.
- `ingest/us/dioceses/vendors.py` — readers for directory widgets met on the way, tried first:
  Diocesan Publications `dpi_gis` (data-name/address/lat/lng/website on each list item: Hartford, Orlando,
  Wilmington), WP Go Maps marker REST (`wpgmza/v1/markers`), WP Google Map Pro/Gold inline `"places"` JSON
  (any inline JSON array of points with title + coordinates; Tyler), Super Store Finder XML (Las Cruces).
- `ingest/us/dioceses/dirlib.py` — the generic engine (`read_see`): home page → directory links (text "find a parish",
  "parishes", "directory"...), the probe's directory page and search hits, `/parishes/` etc.; on each list page,
  entries = an external link plus the smallest block around it naming a parish; pagination followed; when the list
  gives fewer websites than parish pages, each parish detail page is read (the largest family of sibling links, or a
  parish-like sitemap) for the website. Chrome hosts (linked from the home page or from 25%+ of detail pages) are not
  websites. A list is written only when 60%+ of its entries pass the parish-name test (`is_parish_record`: a
  saint/church word and not a school/office/charity name) — this is why e.g. Los Angeles' `/parishes/` (a school
  page) is `rejected-not-a-parish-list`.
- `ingest/us/dioceses/directory_report.py` — folds `reader_status.json` into `directory_status.json` and prints the
  table below (`--write-readme` refreshes it here).
- A probe-era `parishes.json` (REST) is replaced only by a list with at least as many websites; a file this lane wrote
  is always replaced by a rerun (newer filters).

## Resume / rerun
```
python3 -I ingest/us/dioceses/directory_pages.py --minutes 95 --workers 16      # sees not yet read
python3 -I ingest/us/dioceses/directory_html.py  --minutes 95 --workers 16
#   add --force to re-read every see (fast: the raw cache answers), --only slug,slug for one see, --refresh to refetch
python3 -I ingest/us/dioceses/directory_report.py --write-readme
```

## Not read (why)
- `challenge` (73 sees): Cloudflare/Sucuri/Imperva challenge on the probe — not attempted, by rule.
- JavaScript-only finders with no data in the HTML (e.g. Joliet's Liferay app, Pueblo/Maronite Wix, Erie's `#` menu,
  Miami's PHP map, Stamford's site builder, Camden, Rochester, Buffalo): `no-list`; each needs a per-site look or a
  browser. Seattle's `/finder/` is JS but its parish pages are linked elsewhere and were read.
- `dead` / DNS-failing hosts (Bridgeport, Reno, Byzantine Pittsburgh: home page did not answer over HTTPS).

## Per-see status
Totals after the 2026-11-01 reader pass: **78 sees with a parish list** (12 before), **7,677 records** (823 before), **5,586 with a website** (223 before); 4,836 records with an office phone, 1,282 with an office e-mail. Reader results (reader_status.json): written 74, no-list 28, rejected-not-a-parish-list 4, kept-existing 3, skipped-crawl-delay 1. Written by: generic 60, rest+page 8, dpi_gis 3, wpgmza 1, Super Store Finder 1, WP Google Map Pro 1.

<!-- G4b table begin -->
| see | probe status | reader result | reader | CMS guess | records | with website |
|---|---|---|---|---|---:|---:|
| archdiocese-of-atlanta | rest-pages | written | generic | wordpress | 104 | 104 |
| archdiocese-of-baltimore | rest-pages | written | generic | wordpress | 144 | 117 |
| archdiocese-of-chicago | html | no-list | generic | unknown | 0 | 0 |
| archdiocese-of-denver | rest-pages | written | generic | wordpress | 114 | 114 |
| archdiocese-of-detroit | html | written | generic | hubspot | 217 | 0 |
| archdiocese-of-hartford | rest-pages | written | dpi_gis | diocesan (dpi_gis) | 167 | 128 |
| archdiocese-of-los-angeles | rest-pages | rejected-not-a-parish-list | generic | wordpress | 0 | 0 |
| archdiocese-of-miami | html_unknown | no-list | generic | unknown | 0 | 0 |
| archdiocese-of-milwaukee | html | written | generic | unknown | 212 | 201 |
| archdiocese-of-new-york | html | written | generic | ecatholic | 328 | 321 |
| archdiocese-of-newark | rest-pages | written | generic | ecatholic | 190 | 190 |
| archdiocese-of-omaha | rest-pages | written | generic | wordpress | 93 | 93 |
| archdiocese-of-philadelphia | open-rest | written | rest+page | ecatholic | 17 | 9 |
| archdiocese-of-santa-fe | html | written | generic | ecatholic | 32 | 32 |
| archdiocese-of-seattle | html_unknown | written | generic | wordpress | 174 | 139 |
| archdiocese-of-st-louis | open-rest | kept-existing | rest+page | wordpress | 165 | 153 |
| archdiocese-of-st-paul-and-minneapolis | open-rest | written | rest+page | ecatholic | 210 | 197 |
| archdiocese-of-the-military-services | rest-pages | rejected-not-a-parish-list | generic | wordpress | 0 | 0 |
| archdiocese-of-washington | html | written | generic | wordpress | 192 | 182 |
| byzantine-catholic-archeparchy-of-pittsburgh | html_unknown | no-list | generic | - | 0 | 0 |
| chaldean-catholic-eparchy-of-st-peter-the-apostle | open-rest | kept-existing | rest+page | wordpress | 20 | 0 |
| chaldean-eparchy-of-saint-thomas-the-apostle | rest-pages | written | generic | wordpress | 11 | 9 |
| diocese-of-albany | html | written | generic | concrete5 | 142 | 94 |
| diocese-of-alexandria | rest-pages | written | generic | ecatholic | 129 | 14 |
| diocese-of-allentown | html | written | generic | ecatholic | 87 | 68 |
| diocese-of-altoona-johnstown | html | written | generic | wordpress | 180 | 47 |
| diocese-of-arlington | html | written | generic | wordpress | 15 | 13 |
| diocese-of-boise | html | written | generic | wix | 101 | 74 |
| diocese-of-bridgeport | html_unknown | no-list | generic | - | 0 | 0 |
| diocese-of-brooklyn | rest-pages | written | generic | wordpress | 167 | 167 |
| diocese-of-buffalo | rest-pages | no-list | generic | wordpress | 0 | 0 |
| diocese-of-burlington | rest-pages | written | generic | wordpress | 129 | 79 |
| diocese-of-camden | rest-pages | no-list | generic | ecatholic | 0 | 0 |
| diocese-of-charleston | rest-pages | written | generic | wordpress | 85 | 85 |
| diocese-of-charlotte | open-rest | written | rest+page | wordpress | 95 | 80 |
| diocese-of-cleveland | html | written | generic | ecatholic | 217 | 166 |
| diocese-of-covington | html_unknown | written | generic | wordpress | 43 | 43 |
| diocese-of-des-moines | html | written | generic | unknown | 55 | 55 |
| diocese-of-el-paso | html | no-list | generic | ecatholic | 0 | 0 |
| diocese-of-erie | html | no-list | generic | unknown | 0 | 0 |
| diocese-of-evansville | html | rejected-not-a-parish-list | generic | wordpress | 0 | 0 |
| diocese-of-fall-river | open-rest | written | rest+page | wordpress | 91 | 78 |
| diocese-of-fort-wayne-south-bend | html | written | generic | unknown | 79 | 73 |
| diocese-of-fort-worth | html | written | generic | ecatholic | 25 | 25 |
| diocese-of-gallup | rest-pages | written | generic | wordpress | 45 | 8 |
| diocese-of-gary | html | written | generic | drupal | 61 | 50 |
| diocese-of-gaylord | html | written | generic | ecatholic | 57 | 49 |
| diocese-of-grand-rapids | rest-pages | written | generic | wordpress | 76 | 69 |
| diocese-of-great-falls-billings | html | no-list | generic | parishsoft | 0 | 0 |
| diocese-of-green-bay | rest-pages | no-list | generic | wordpress | 0 | 0 |
| diocese-of-greensburg | rest-pages | written | generic | wordpress | 105 | 7 |
| diocese-of-harrisburg | rest-pages | written | generic | wordpress | 97 | 90 |
| diocese-of-jefferson-city | rest-pages | written | generic | wordpress | 53 | 35 |
| diocese-of-joliet | html | no-list | generic | unknown | 0 | 0 |
| diocese-of-kansas-city-st-joseph | rest-pages | written | generic | wordpress | 80 | 69 |
| diocese-of-la-crosse | rest-pages | written | generic | wordpress | 304 | 119 |
| diocese-of-lansing | html | no-list | generic | drupal | 0 | 0 |
| diocese-of-laredo | open-rest | written | rest+page | wordpress | 50 | 7 |
| diocese-of-las-cruces | rest-pages | written | superstorefinder | wordpress (Super Store Finder) | 66 | 62 |
| diocese-of-lexington | open-rest | kept-existing | rest+page | wordpress | 11 | 0 |
| diocese-of-little-rock | html | written | generic | drupal | 134 | 63 |
| diocese-of-lubbock | html | no-list | generic | unknown | 0 | 0 |
| diocese-of-manchester | html | written | generic | silverstripe | 118 | 0 |
| diocese-of-memphis | open-rest | written | rest+page | ecatholic | 57 | 53 |
| diocese-of-nashville | rest-pages | no-list | generic | - | 0 | 0 |
| diocese-of-new-ulm | html | written | generic | ecatholic | 5 | 5 |
| diocese-of-norwich | html_unknown | no-list | generic | dnn | 0 | 0 |
| diocese-of-oakland | rest-pages | written | generic | wordpress | 28 | 28 |
| diocese-of-ogdensburg | html_unknown | written | generic | joomla | 77 | 54 |
| diocese-of-orange | rest-pages | written | generic | ecatholic | 70 | 17 |
| diocese-of-orlando | rest-pages | written | dpi_gis | diocesan (dpi_gis) | 98 | 81 |
| diocese-of-owensboro | rest-pages | written | generic | wordpress | 78 | 54 |
| diocese-of-palm-beach | html | written | generic | ecatholic | 51 | 51 |
| diocese-of-peoria | html | written | generic | squarespace | 96 | 71 |
| diocese-of-pueblo | html | no-list | generic | wix | 0 | 0 |
| diocese-of-raleigh | html | written | generic | drupal | 104 | 89 |
| diocese-of-rapid-city | rest-pages | no-list | generic | wordpress | 0 | 0 |
| diocese-of-reno | html_unknown | no-list | generic | - | 0 | 0 |
| diocese-of-richmond | rest-pages | written | generic | wordpress | 145 | 132 |
| diocese-of-rochester | rest-pages | no-list | generic | wordpress | 0 | 0 |
| diocese-of-rockville-centre | rest-pages | written | generic | wordpress | 126 | 126 |
| diocese-of-sacramento | html | written | generic | ecatholic | 85 | 85 |
| diocese-of-saginaw | html | written | generic | drupal | 60 | 54 |
| diocese-of-salina | html | written | generic | wordpress | 91 | 39 |
| diocese-of-san-diego | rest-pages | no-list | generic | - | 0 | 0 |
| diocese-of-san-jose | open-rest | skipped-crawl-delay | - | - | 57 | 5 |
| diocese-of-savannah | html | written | generic | diocesan | 69 | 69 |
| diocese-of-scranton | rest-pages | written | generic | diocesan | 84 | 84 |
| diocese-of-shreveport | open-rest | written | rest+page | wordpress | 35 | 21 |
| diocese-of-sioux-falls | rest-pages | written | generic | wordpress | 100 | 100 |
| diocese-of-spokane | html | no-list | generic | squarespace | 0 | 0 |
| diocese-of-st-augustine | rest-pages | written | generic | wordpress | 73 | 51 |
| diocese-of-st-cloud | rest-pages | written | generic | wordpress | 95 | 95 |
| diocese-of-st-petersburg | rest-pages | written | generic | diocesan | 82 | 82 |
| diocese-of-toledo | html | written | generic | unknown | 8 | 8 |
| diocese-of-tyler | rest-pages | written | inline_json:wp-google-map-pro | wordpress | 67 | 42 |
| diocese-of-venice | rest-pages | written | generic | diocesan | 64 | 64 |
| diocese-of-wheeling-charleston | rest-pages | no-list | generic | squarespace | 0 | 0 |
| diocese-of-wichita | rest-pages | no-list | generic | wordpress | 0 | 0 |
| diocese-of-wilmington | rest-pages | written | dpi_gis | diocesan (dpi_gis) | 79 | 75 |
| diocese-of-yakima | open-rest | rejected-not-a-parish-list | rest+page | wordpress | 0 | 0 |
| diocese-of-youngstown | rest-pages | written | generic | ecatholic | 264 | 128 |
| eparchy-of-newton | rest-pages | written | generic | wordpress | 48 | 37 |
| eparchy-of-st-maron-of-brooklyn | html | no-list | generic | squarespace | 0 | 0 |
| maronite-eparchy-of-our-lady-of-lebanon | html | no-list | generic | wix | 0 | 0 |
| st-nicholas-of-chicago-for-ukrainians | rest-pages | no-list | generic | wordpress | 0 | 0 |
| st-thomas-syro-malabar-diocese-of-chicago | rest-pages | written | wpgmza | wordpress (WP Go Maps) | 86 | 0 |
| ukrainian-catholic-archeparchy-of-philadelphia | html | no-list | generic | unknown | 0 | 0 |
| ukrainian-catholic-eparchy-of-st-josaphat-parma-oh | rest-pages | written | generic | wordpress | 8 | 8 |
| ukrainian-catholic-eparchy-of-stamford | html | no-list | generic | unknown | 0 | 0 |
<!-- G4b table end -->
