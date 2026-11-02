# Wayback Machine recoveries (`data/real/us/wayback/`)

What the live crawl could not read (bot challenge, 403, dead site), read from the Internet Archive's copies instead.
web.archive.org is unreachable from the project sandbox but reachable from GitHub runners: the `wayback` job in
`.github/workflows/crawl.yml` runs `ingest/us/wayback/run.py`.

**Counts (2026-11-02).**
- A. Diocese directories: 90 tried; results {'written': 42, 'no-list': 36, 'error': 9, 'cdx-failed': 1, 'no-capture': 2}; written 42 sees, 1944 parishes, 1818 with a website.
- B. Parish sites: 915 tried; results {'written': 691, 'cdx-failed': 147, 'no-capture': 29, 'no-usable-capture': 48}; written 691 sites: 4653 groups, 651 sites with a bulletin link, 8698 schedule rows (all needs_review).

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

## Laptop run (part B from Matthew's laptop, 2026-11-01)

Same targets and output as part B, fetched from Matthew's laptop (web.archive.org is unreachable from the sandbox).
- Targets: `python3 -I ingest/us/wayback/laptop_targets.py targets.json` (challenge / blocked_403 / blocked_429 site files with
  no wayback-* status, not in any `sites_state*.json`, not reopened by the laptop live pass). Order: sees in the coverage
  ledger's `blocked` table, most parishes known minus verified first; then California `ca-*`; then the rest. 4,964 at build.
- Laptop: `C:\Users\Matthew\Desktop\New folder\wayback-us\` (`$HOME/mnt/New folder/wayback-us/` in the device shell):
  `wayback_us_run.py` (= `ingest/us/wayback/laptop_run.py`), `repo/` (copies of wblib.py, sites.py, fetch_site.py),
  `targets.json`, `progress.json` (per-site state; `done` = finished), `raw/<diocese>/<key>__cdx.json` + `raw/<diocese>/<key>/`
  (manifest + gzipped captures), `out/batch_NN.zip`. The laptop fetches only (stdlib; one request at a time, 4.1 s apart:
  the archive refused connections at 1.1 s; 429 / refused connection -> persisted cool-down).
- Sandbox: `python3 -I ingest/us/wayback/laptop_build.py --zip <batch_NN.zip>` unpacks into `data/raw/us/wayback/laptop/`
  and replays `sites.one()` on the caches with no network, so records are identical in shape to the runner's
  (`crawl_status "wayback-<date>"`, schedule rows `needs_review`, never over an `ok` read). State: `sites_state.laptop.json`.

**Status (2026-11-02 01:35Z, stopped at the agent's 2-hour limit):** 24 of 4,864 targets finished (all Diocese of
Pittsburgh, the first see in priority order): 19 written (172 captures used, 204 groups, 19 sites with a bulletin link, 353
schedule rows, all needs_review), 5 with no capture since 2023; 1 more partly fetched on the laptop. California `ca-*` not
reached. 233 archive requests in 4 batches. Throughput is the
archive's limit, not ours: CDX listings take 30-145 s each, and web.archive.org refused TCP connections from the laptop for
minutes at a time after ~13 requests at 1.1-6 s pace and after ~70 requests at 10 s pace, so the real rate is ~10-15 sites an
hour. The California laptop run hit the same wall. At this rate the full list is ~300+ hours; the GitHub runner shards
(`sites_state.<i>of3.json`) remain the main route and `laptop_targets.py` skips whatever they finish.

**Resume** (device shell; each call <= 170 s; repeat; if it prints COOLDOWN wait that long):

    cd "$HOME/mnt/New folder/wayback-us" && python3 wayback_us_run.py run --budget 165

Every ~150 finished sites: `python3 wayback_us_run.py zip --min 150` (or `--all`), stage `out/batch_NN.zip` back, then
`python3 -I ingest/us/wayback/laptop_build.py --zip "/mnt/user-data/uploads/New folder/wayback-us/out/batch_NN.zip"`, commit
`data/real/us/sites/**` changes + `sites_state.laptop.json`. Status: `python3 wayback_us_run.py status`.
