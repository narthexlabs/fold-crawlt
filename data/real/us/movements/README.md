# National movement directories (lane D) — groups by parish

Goal: parishes whose own website lists nothing still show their real groups, read from the national directory of a
Catholic movement. Code: `ingest/us/movements/`. Honest user agent
`FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`, robots.txt and Crawl-delay honoured, one
request at a time per host (>= 1.05 s apart), challenges recorded and never evaded. Institutional fields only (council /
conference names, city, public website): never a named officer's phone or e-mail.

| file | what |
|---|---|
| `parish_index.json` | our parish universe: Find build page ids + name, city, state, ZIP, see, `has_groups` (copied from fold-app `public/us/*/p`, `public/ca/*/p` by `build_parish_index.py`) |
| `kofc.json` | Knights of Columbus councils (type R) near our parishes' ZIP codes, each with a `match` field |
| `kofc_progress.json` | ZIP codes already queried (resume state) |
| `by_parish.json` | parish id -> groups `[{name, category, subcategory, source, label, meeting_time, url, source_url, fetched_at, confidence}]` (exact + fuzzy matches only) |
| `cursillo.json` | diocesan Cursillo language-group websites (diocese level, not in by_parish) |
| `catholic_charities.json` | Catholic Charities USA member agencies (entities, not in by_parish) |
| `ssvp.json`, `legion_of_mary.json` | what was checked; no structured directory (0 records) |

## 1. Knights of Columbus — `kofc.py` (reachable from the sandbox and from Actions)
- Method: the public page https://www.kofc.org/get-involved/find-a-council/ calls
  `GET /api/FindACouncilSearch/councils/countryZip/USA/<zip>/` (found in its `main.js`); it returns the ~5 nearest councils,
  nearby 4th-degree assemblies and the state council. We query each ZIP of our parishes (parishes without groups first),
  keep councils (type R), store council number, name, city, state, ZIP, language, public website. The API's contact /
  e-mail / phone fields are dropped (empty anyway).
- robots.txt (2026-11-01): disallows only `/ui/`, `/utils/`, `/search/?query=`; no Crawl-delay. The API path is allowed.
- Speed: the API answers in ~2-4 s, so ~1,000 ZIPs/hour; 6,920 ZIPs in all (9,521 of 17,363 parishes have a ZIP).
- Matching (`match.py`, `common.match`): same state + same city, then normalised core name (saint/st, parish/church/council
  stripped, trailing "of <city>" stripped). `exact` = identical core, unique in the city; `fuzzy` = same distinctive
  words or difflib >= 0.9, unique; everything else `unmatched` (councils named after a person or a town — most of them —
  are never attached to a parish).
- Resume (sandbox): `python3 -I ingest/us/movements/kofc.py --minutes 60 && python3 -I ingest/us/movements/match.py`
- Resume (Actions): Actions -> "National crawl" -> Run workflow, job=`movements`, minutes=`110` (repeat until
  `kofc_progress.json` zips_done = zips_total). Never run both at once (one crawler per host).
- Refresh the parish universe after a new Find build: `python3 -I ingest/us/movements/build_parish_index.py --app ../fold-app`.

## 2. Society of St. Vincent de Paul — `probe_unstructured.py`
- Reachable (200). robots.txt: `User-agent: *` Allow / with `Content-Signal: search=yes, ai-train=no, use=reference`.
- No public conference finder: the "volunteer" page is a contact form that routes by ZIP; members.ssvpusa.org is the
  member portal; the only locator is thrift stores (entities). 0 records. Next step if wanted: diocesan / district council
  sites that publish "find a conference" lists, one by one.

## 3. Legion of Mary — `probe_unstructured.py`
- legionofmary.ie (Concilium) reachable, no unit directory; legionofmary.org now serves unrelated betting content. Skipped
  (0 records) as instructed.

## 4. Cursillo — `cursillo.py`
- natl-cursillo.org now redirects to cursillousa.org; robots.txt is a 404 (no rules). One page,
  https://cursillousa.org/links-contacts/: 123 diocesan language-group websites, 119 matched to a see slug. Diocese level
  only, so not merged into by_parish (parish Ultreyas are not listed nationally). Contact names are not read.

## 5. Catholic Charities USA — `ccusa.py`
- robots.txt allows all. The agency locator page embeds all member agencies as JSON: 168 agencies (name, address, city,
  state, ZIP, lat/lng, website, served ZIP codes). E-mail/phone/fax not stored. Entities, not parish groups.

## Integration note for `scripts/us/build_us_static.py` (owned by another lane — not edited here)
For each parish record `r` with id `pid` in a see, after the site/bulletin groups are collected:
`for g in by_parish["parishes"].get(pid, [])` append to the page's `groups` a row
`{name: g.name, category: g.category ("fraternal_orders"), subcategory: g.subcategory, audience: "all", language: "en",
language_assumed: True, meets: g.meeting_time, source_url: g.source_url, url: g.url}`, add `{"label": g.label, "url":
g.source_url, "fetched_at": g.fetched_at}` to `groups_via` with the label **"From the Knights of Columbus's directory"**
(i.e. "From the <movement>'s directory"), skip it when the page already has a Knights of Columbus group (name contains
"knights of columbus" / "k of c"), and emit it in `groups.json` rows with `via = "kofc"`. Ids in by_parish are the build's
own page ids as of `parish_index.json.built_at`; if the build changes ids, re-run `build_parish_index.py` and `match.py`.
