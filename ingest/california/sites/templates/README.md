# Per-platform schedule templates (`ingest/california/sites/templates/`)

Readers for Mass, confession and adoration times on parish websites, one per site builder, each measured before it is
trusted. Offline only: they read the cached pages (`parse_site.Page` objects); nothing here fetches or writes.

```python
import templates as T                       # with ingest/california/sites on sys.path
res = T.parse_schedule(pages, names)        # names: the parish's names from the input list
res["rows"], res["template"], res["parse_quality"], res["dropped"]
```

## Files

- `common.py`: the strict reader every template uses (headings, tables, clauses; the check's fixes). See its docstring.
- `generic_strict.py`: the strict reader on the main content of each schedule page; the fallback.
- `ecatholic.py`: reads eCatholic's own `sacramentTimesModule` widgets (day classes, start/end, language, notes)
  structurally, then free-text modules for kinds the widgets lack.
- `wix.py`, `squarespace.py`, `weebly.py`, `godaddy.py`, `wordpress.py`: generic_strict scoped to the builder's
  content container, minus its header and footer. GoDaddy content cards repeat their heading in hidden copies (dropped).
- `parishsoft.py`: ParishSoft is **not** a builder here. All 59 `cms_guess: parishsoft` sites only link to
  giving.parishsoft.com / Family Suite; they are WordPress 19, Wix 10, Weebly 7, Squarespace 3 and others.
  `platform_of()` ignores giving links. This template only matches a page served by ParishSoft (none cached).
- `__init__.py`: `platform_of`, `parse_schedule`, `quality` (parse_quality), `dm_agreement`, `rows_for_church`,
  `trusted`, and `PASSED` (templates that met the bar; empty until Phase B decides).
- `truth/oakland-curated-2026-10-26.json`: snapshot of the 404 hand-checked Oakland rows (44 parishes, read-only SELECT).
- `truth/review15-seed20261031.json`: the independent check's truth table (15 parishes, Mass only).

Rows keep the existing schedule-row shape, plus `confidence` (high/medium/low) and `template`. Confession and
adoration ranges are `HH:MM-HH:MM`. On a multi-church site, rows under one church's heading carry `[<church>]` at the
start of `raw`; `rows_for_church()` drops the other churches' rows from a record.

## Scoring

```
python3 -I scripts/california/eval_templates.py                     # all sets, ~3 min, 2 workers
python3 -I scripts/california/eval_templates.py --set review15 --ids <unit or domain>   # one site, with its rows
```

Slot = (kind, weekday, start time, language when both sides state one), as in the check. Sets: **oakland** (truth,
36 parishes with cached pages), **review15** (truth, the 8 of 15 with a readable site), **referee** (DiscoverMass,
458 California parishes; a referee, not truth). Calibration: DiscoverMass itself scores 78.2% precision / 76.4% recall /
89.4% Sunday recall against the Oakland hand-checked rows (39 parishes), so about 78% is the referee ceiling for a
perfect parser.

## Status / resume (2026-10-31, stopped at the coordinator's request: usage limit)

**Built (Phase A):** everything above, plus the harness. Not wired into `parse_site.py`; nothing re-parsed or rebuilt.

**Scores, baseline (current `parse_site.schedule_rows` + merge `sanity`) -> templates, last full run** (precision /
recall / Sunday recall):

| set | platform | n | baseline | templates |
|---|---|---:|---|---|
| oakland | ALL | 36 | 83.4 / 64.9 / 67.9 | 90.9 / 79.6 / 81.0 |
| oakland | generic_strict | 14 | 85.5 / 72.5 / 82.1 | 92.8 / 84.4 / 85.7 |
| oakland | wordpress | 12 | 77.9 / 60.0 / 60.0 | 92.3 / 77.5 / 77.8 |
| oakland | wix / squarespace / weebly / godaddy | 3 / 2 / 2 / 2 | small n | 90.0 / 89.7 / 84.4 / 100 precision |
| review15 | ALL | 8 | 87.0 / 50.0 / 40.0 (error rate 52.4%) | 93.8 / 88.3 / 97.5 (error rate 11.7%) |
| referee | ALL | 458 | 67.6 / 43.8 / 50.7 | 75.7 / 58.5 / 61.5 |
| referee | ecatholic | 33 | 62.1 / 27.6 / 31.1 | 78.7 / 75.7 / 81.8 |
| referee | wordpress | 118 | 66.0 / 46.9 / 58.7 | 76.2 / 58.1 / 64.6 |
| referee | wix | 92 | 71.8 / 56.0 / 63.9 | 73.6 / 62.1 / 63.3 |
| referee | generic_strict | 117 | 68.1 / 37.6 / 43.4 | 78.7 / 46.7 / 50.4 |
| referee | weebly / squarespace / godaddy | 38 / 35 / 25 | 69.9 / 65.0 / 65.3 precision | 72.2 / 70.9 / 79.8 precision |

Kind error (confession/adoration shown under the wrong kind), oakland: 20.2% -> 9.5%; referee: 14.6% -> 13.4%.
Unmeasured: the last edits (stricter eCatholic detection, which had caught a Weebly site through an ecatholic.com link;
holidays as kind other; "no Mass at ..." negation; sticky kind only for Mass lists). Re-run the harness first.

**Left in Phase A**
- Re-run; then look at the largest referee disagreements (`--ids`), e.g. missions sharing the parish site (St. Anne,
  Raymond), Maxwell/Williams (Spanish "5:30 pm a 7:00 pm" ranges; "before Mass" read as a kind), San Dimas, Long Beach
  East Village.
- Sunday recall below 85% on oakland wordpress (77.8) and the referee for most builders; truth n per builder is small.
- Truth-table note: Modesto's weekday 8:00 AM is Spanish on the parish's own table and week view; the check's table
  has no language there, so 4 "mislabels" on review15 are the table's.
- Decide `PASSED`: no builder reaches 90% on the referee (ceiling ~78%); judge on the truth sets plus referee parity
  with DiscoverMass. Confidence levels are not yet calibrated (referee agreement high 78%, medium 75%, low 67%).

**Phase B: wired 2026-10-31 (lane A, session 2); not yet re-parsed.**
- `parse_site.schedule_rows_templated()` (also on the side-door path in `run_diocese.py`) calls `templates.parse_schedule`;
  the generic reader is the fallback when a template raises. Each site record gets `schedule_template` {template, version,
  parse_quality, passed_bar, trusted_candidate, generic_rows}. `TEMPLATE_VERSION` is `ca-sites/2`.
- The bar is `BAR` / `passes()` in `__init__.py`; `PASSED` is read from `grades.json`, which
  `scripts/california/eval_templates.py --write-grades` rewrites after a full run. The current grades.json was copied from
  the table above (not re-run: no raw cache in that clone): generic_strict passes; wordpress fails Sunday recall (77.8%);
  the others have < 10 truth parishes.
- `scripts/california/merge.py`: a trusted candidate whose Sunday Masses agree with DiscoverMass (`sunday_agrees`) gets
  source `site-<template>-<date>`, tier 3 (above DiscoverMass, tier 4); otherwise `crawled-` (tier 6). Same tiers in
  `lib/find.ts sourceTier`; `site` added to `KINDED` in `lib/schedule.ts`. Multi-church sites: `rows_for_church`.
  Documented in data/real/california/merged/README.md.
- To do where `data/raw/california/sites/` exists: `eval_templates.py --write-grades`, the merges (RESUME.md), then
  `run_diocese.py parse <slug> --offline` for every diocese, `build_ca_static.py --no-geocode`, the 15-parish check
  (`scripts/california/check15.js`) and parity (`scripts/california/parity.js`).

**Phase B plan (as written before wiring)**
1. Wait for the other agent (both conditions in the brief); re-check `git status`.
2. `parse_site.parse_site()` and the side-door path in `run_diocese.py`: call `templates.parse_schedule`; store
   `template`, `parse_quality`, `schedule_dropped`.
3. Precedence: a trusted parse (template in `PASSED`, parse_quality high, Sunday agrees with DiscoverMass) gets source
   `site-<template>-<date>` at a tier between hand-read (2) and DiscoverMass (3) in both `merge.tier` and
   `lib/find.ts sourceTier` (it must not speak for a whole group); label "From the parish website"; add `site` to
   `KINDED` in `lib/schedule.ts`. Untrusted stays `crawled-` (tier 5). Use `rows_for_church` per record in merge.
4. Laptop merge loop (8 dioceses), `run_diocese.py parse <slug> --offline` for all, `readme`,
   `build_ca_static.py --no-geocode`, `npm run typecheck`, `npm run build`.
5. Re-measure with /tmp/review/compare.js (seed 20261031) against 33.5% and the other agent's 5.3%; trusted counts per
   platform; Oakland Find counts. Commit in steps; append "Templates 2026-10-31" to the review doc and a line to CLAUDE.md.

## 2026-11-01: whole weekly schedule (confession, adoration, devotions)

Matthew: a parish is complete only with its whole posted weekly schedule. Changes (parser version `ca-sites/3`):
- Fetch: page kinds `confession`, `adoration`, `liturgy` (fetch_site.CATS; one page each, one guessed `/reconciliation` and
  `/adoration` for eCatholic / WordPress without such a link). A page fetched as confession / adoration whose URL or title
  names it is read with that kind as its default (common.page_kind_default). `run_state.py topup` fetches only these kinds
  for sites already crawled ok (runners: scripts/us/sites_chunk.sh, TOPUP_MINUTES, default 25 taken from the chunk).
- Parse: "(except ...)" / "[except in Summer]" / "except January 1" become the row's `note` and never drop it
  (common.split_except); "until about 3:30 pm" is the end; em dashes join a time to its kind; "Eucharistic Adoration"
  after a range is adoration; devotions (rosary, novena, stations, chaplet, Gorzkie Zale, vespers) are kind `devotion`
  (merge / lib/schedule.ts show it as other); "Holy Days of Obligation:" is not a church name; a church heading above a
  home-page "MASS TIMES" block is carried into the block (two-church sites); the home page is always a schedule source;
  "Friday after the 8:30 Mass" is kept as a needs_review row with `relative`; "by appointment" / "30 minutes before each
  Mass" go to `schedule_notes`; "No Mass on Mondays" to `schedule_none`; weekly calendar-feed entries to
  `calendar_schedule_rows` (merge tier 7, off until docs/patches/2026-11-01-find-calendar-tier.patch is built and applied).
- Regression cases: `truth/fixtures/` (real caches + three screenshot reconstructions ending `.invalid`):
  `python3 -I ingest/california/sites/templates/truth/fixtures/check_fixtures.py`
