# Wayback Machine recoveries (`data/real/us/wayback/`)

What the live crawl could not read (bot challenge, 403, dead site), read from the Internet Archive's copies instead.
web.archive.org is unreachable from the project sandbox but reachable from GitHub runners: the `wayback` job in
`.github/workflows/crawl.yml` runs `ingest/us/wayback/run.py`.

**Counts (2026-11-01).**
- A. Diocese directories: 57 tried; results {'written': 26, 'no-list': 19, 'error': 9, 'cdx-failed': 3}; written 26 sees, 1314 parishes, 1239 with a website.
- B. Parish sites: 242 tried; results {'written': 181, 'cdx-failed': 42, 'no-capture': 11, 'no-usable-capture': 8}; written 181 sites: 1117 groups, 165 sites with a bulletin link, 2025 schedule rows (all needs_review).

## What is written

- **A. Diocese directories**: `data/real/us/dioceses/<slug>/parishes.json` with `source_label: "wayback-<capture date>"`,
  `archive_url`, `capture_date`, `live_status`; only for sees whose live list could not be read (directory_status.json
  status challenge / dead / blocked / html_unknown) and that have no live `parishes.json`. Same record shape and readers as
  `directory_html.py` (home-page nav -> /parishes/ /find-a-parish/ /parish-directory/ -> list entries -> detail pages),
  run against archived pages. A live list that appears later replaces it (the reader never overwrites a non-wayback file).
- **B. Parish sites**: `data/real/us/sites/<diocese>/<domain>.json` with `crawl_status: "wayback-<capture date>"` and
  `crawl_env: "wayback"`, the usual parsed fields (`groups`, `social`, `bulletin`, `pages`, `cms_guess`, `schedule_rows`) and a
  `wayback` block (captures with archive URLs, capture age, `prior_status`). **Every `schedule_rows[]` entry is
  `needs_review: true`** (California's rule: a Wayback schedule is shown only after a person reads it); groups, social and
  bulletin links are as of the capture date. Never overwrites an `ok` read.
- `dioceses_state.json`, `sites_state.json`: one result per item (written / no-capture / no-usable-capture / cdx-failed / ...),
  committed so a lost cache does not repeat work. Raw CDX listings and captures: `data/raw/us/wayback/` (gitignored).

## Method and politeness

One archive client, one request at a time, >= 1.1 s apart (CDX and captures alike), honest `FindTheChurch/0.1` UA; 429 -> 90 s
cool-down, 5xx/timeouts -> two retries, 8 failures in a row -> the run stops cleanly. Per site: one CDX request (newest
200/html capture of each URL since 2023, `collapse=urlkey&sort=reverse`), then `id_` raw captures at the exact timestamp.
Captures that look like a challenge page are not used. The page plan and parser are `fetch_site.Site` and
`parse_site.parse_site` (imported), so the output is the same shape as a live read. Privacy screen applied.

## Precision

Archived pages are old: median capture age is recorded per site (`wayback.capture_age_days`). Parish lists may include closed or
merged parishes and miss new ones; websites may have moved. Treat as a lead list, not the official directory.

## Resume

Run again (state is per item): `python3 -u ingest/us/wayback/run.py --minutes 110 --git` (or Actions -> National crawl ->
job `wayback`). Parts: `--only-a` (directories), `--only-b` (sites); `dioceses.py --only slug --retry`, `sites.py --only domain --retry`.
