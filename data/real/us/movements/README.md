# National movement directories (lane D) — groups by parish

Goal: parishes whose own website lists nothing still show their real groups, read from the national directory of a
Catholic movement. Code: `ingest/us/movements/`. Honest user agent
`FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`, robots.txt and Crawl-delay honoured, one
request at a time per host (>= 1.05 s apart), challenges recorded and never evaded. Institutional fields only (council /
conference names, city, public website): never a named officer's phone or e-mail.

| file | what |
|---|---|
| `parish_index.json` | our parish universe: Find build page ids + name, city, state, ZIP, see, `has_groups` (copied from fold-app `public/us/*/p`, `public/ca/*/p` by `build_parish_index.py`) |
| `kofc.json` | Knights of Columbus active US councils (type R; college councils type C kept but never matched), each with a `match` field |
| `kofc_progress.json` | ZIP codes already queried + the council-number cursor (`numbers.next`; resume state) |
| `paradisus_dei.json` | parishes hosting That Man Is You! / She Shall Be Called Woman / other Paradisus Dei programs (2026-11-02) |
| `secular_franciscans.json` | Secular Franciscan (OFS-USA) fraternities and where they meet (2026-11-02) |
| `aoh.json` | Ancient Order of Hibernians divisions and where they meet (2026-11-02) |
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
- Matching (2026-11-02: `match.py`, `common.Matcher`, used for every source): candidates = our parishes in the same state
  and city (KofC: also the council's `location` town), the same ZIP, or within 3 km of the directory's coordinates; then
  `exact` = same normalised name, unique (possessives folded: "ST MARYS" = "St. Mary"); `fuzzy` = distinctive words equal,
  difflib >= 0.9, or one name extends the other ("St. Michael" / "St. Michael the Archangel"), unique — or, when several share
  the name, the nearest map pin under 1 km; `exact` = same street address; `fuzzy` = a church meeting place within 100 m of
  the parish's map pin. A seminary / friary / retreat house never matches the parish of the same saint unless exact.
  Older rule (kept for the record): same state + same city, then normalised core name (saint/st, parish/church/council
  stripped, trailing "of <city>" stripped). `exact` = identical core, unique in the city; `fuzzy` = same distinctive
  words or difflib >= 0.9, unique; everything else `unmatched` (councils named after a person or a town — most of them —
  are never attached to a parish).
- 2026-11-02, mode `numbers`: the same page's "find by council number" call
  `GET /api/FindACouncilSearch/councilByNo/councilNum/<n>/NA/` returns one council (status, type, city, state, ZIP,
  country). `kofc.py --mode numbers` walks n = 1 .. highest known + 600, skipping numbers already found by ZIP, keeping
  active US councils (status A, country USA, type R or C). This replaces the ZIP pass (stopped at 6,535 / 6,920 ZIPs, which
  only ever returns the ~5 councils nearest each ZIP) with the complete list. ~1.3 s per request.
- Resume (sandbox): `python3 -I ingest/us/movements/kofc.py --mode numbers --minutes 60 && python3 -I ingest/us/movements/match.py`
- Resume (Actions): Actions -> "National crawl" -> Run workflow, job=`movements`, minutes=`110` (repeat until
  `kofc_progress.json` zips_done = zips_total). Never run both at once (one crawler per host).
- Refresh the parish universe after a new Find build: `python3 -I ingest/us/movements/build_parish_index.py --app ../fold-app`.

## 1b. Paradisus Dei programs (That Man Is You! and others) — `paradisus_dei.py`
- tmiy.org no longer resolves; the program page is https://paradisusdei.org/that-man-is-you/ ("Find a group near you" ->
  https://programs.paradisusdei.org/search). That page asks `GET /api/schools?search_type=current&lat=..&lng=..&miles=..`
  (read from its own JS); three radius queries (lower 48, Alaska, Hawaii) return every participating parish: 1,078 US
  parishes (TMIY 931, She Shall Be Called Woman 317, Mysteries of the Rosary 121, EHET 36, The Choice Wine 26).
- robots.txt of programs.paradisusdei.org is empty (all allowed); paradisusdei.org's legal page reserves copyright in its
  content and allows quoting with the source noted — we store facts (which parish hosts which program) with the source link.
- The per-course coordinator name / e-mail / phone (route /places/courses) is never requested.
- From GitHub Actions runners the same route answers 403 with a challenge (2026-11-02 07:45Z, recorded, not retried), so
  this source is refreshed from the sandbox only (`python3 -I ingest/us/movements/paradisus_dei.py && python3 -I
  ingest/us/movements/match.py`); it is not a leg of the Actions `movements` matrix.

## 1c. Secular Franciscan Order USA — `secular_franciscans.py`
- https://www.secularfranciscansusa.org/map-of-local-fraternities/ embeds its map data (WP Google Map plugin) as base64
  JSON: 530 fraternities with name, number, region, meeting place, street address, coordinates. robots.txt disallows only
  /wp-admin/. Contact e-mail / phone fields are dropped.

## 1d. Ancient Order of Hibernians — `aoh.py`
- https://aoh.com/aoh-directory/ lists every division (345) by state and county: name, meeting day/time, meeting place and
  address. robots.txt disallows /membership-roster/, /join/, /contact/, /archive/ only. The "Contact:" part (officer name,
  e-mail, phone) is cut before parsing and never stored. Most divisions meet in halls or pubs, so few match a parish.

## Actions
`movements` is a matrix with one leg per host (kofc, ofs, aoh; paradisus_dei is sandbox-only, see 1b), each committing only its own file;
`movements-match` runs `match.py` on the latest main afterwards. Dispatch:
`gh api -X POST repos/narthexlabs/fold-crawlt/actions/workflows/crawl.yml/dispatches -f ref=main -f 'inputs[job]=movements' -f 'inputs[minutes]=110'`
(repeat until `kofc_progress.json` numbers.complete_at is set).

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
2026-11-02: rows now also carry `audience`, `language` (null = assumed English) and `dedupe` (a regex: a group of that name
already on the parish's site/bulletin gains this directory as a second source instead of a duplicate). Via codes / labels:
`kofc`, `paradisus_dei` ("From Paradisus Dei's group finder ..."), `ofs` ("From the Secular Franciscans' (OFS-USA)
fraternity map"), `aoh` ("From the Ancient Order of Hibernians' division directory").
For each parish record `r` with id `pid` in a see, after the site/bulletin groups are collected:
`for g in by_parish["parishes"].get(pid, [])` append to the page's `groups` a row
`{name: g.name, category: g.category ("fraternal_orders"), subcategory: g.subcategory, audience: "all", language: "en",
language_assumed: True, meets: g.meeting_time, source_url: g.source_url, url: g.url}`, add `{"label": g.label, "url":
g.source_url, "fetched_at": g.fetched_at}` to `groups_via` with the label **"From the Knights of Columbus's directory"**
(i.e. "From the <movement>'s directory"), skip it when the page already has a Knights of Columbus group (name contains
"knights of columbus" / "k of c"), and emit it in `groups.json` rows with `via = "kofc"`. Ids in by_parish are the build's
own page ids as of `parish_index.json.built_at`; if the build changes ids, re-run `build_parish_index.py` and `match.py`.
