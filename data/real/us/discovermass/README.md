# DiscoverMass national address book (lane G step 3)

**SECONDARY SOURCE. Do not use these Mass times as truth.** DiscoverMass pages carry no last-updated date and only about
78% of weekend times agree with the parish's own site (docs/research/13-mass-times-accuracy.md). This folder is an
*address book*: for each church the page gives the parish website URL, coordinates, the diocese line and (secondary,
labelled `secondary-discovermass`) times. The per-state website lists feed the national parish-site crawler (lane G4).

## Files (per state, lowercase code)
- `<state>.json`: one record per church page fetched: slug, name, city_guess, state, street/city/zip, diocese_line,
  rite, website (own site; directory links dropped), website_listed, social links, lat/lng (+ source), times
  (secondary rows with day/time/language/flags), source_url, fetched_at. Pages that 404 are kept as `unavailable`.
- `<state>.websites.json`: deduplicated parish website URLs, each with the slug/name/city/diocese/coordinates of the
  church(es) listing it. `social_only` holds churches whose "website" is a Facebook/Instagram page.
- `progress.json`: per-state counts (index_count, records, parsed, with_website, distinct_websites, with_latlng).
- California is excluded here (its own crawler, ingest/california/discovermass_crawl.py). 110 index slugs with no state
  go last under `unresolved`.

## Method
`ingest/us/discovermass_crawl.py` reuses the California page parser unchanged. Index: data/real/us/discovermass_index.json
(20,147 pages, duplicates `-2` excluded). robots.txt read each run (Crawl-delay 10 -> 10.5 s between requests), honest UA
`FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`, one process per machine (flock on
/tmp/discovermass-crawl.lock), 403/429/challenge stops the run, nothing evaded. Raw HTML cached in
data/raw/us/discovermass/<state>/ (gitignored); a fresh clone resumes from the slugs already in `<state>.json`.
Order: TX NY IL FL PA NJ MA OH MI WI MN, then the remaining states by index count. At 10.5 s/page that is about 340
pages/hour, so about 59 hours for all 19,000 non-California pages.

## Status (2026-11-01, session 1)
**Zero pages fetched.** The sandbox's egress proxy answers 403 to CONNECT for discovermass.com (and for example.com):
the host is not on this session's allowed-domains list (claude.ai Settings > Capabilities). Not worked around. The
crawler, publisher and chunk script are written and the publisher was tested on two synthetic cached pages (removed).
Counts per state: all 0 of the index counts in progress.json.

## Resume (any machine that can reach discovermass.com)
    scripts/us/discovermass_chunk.sh 100 /home/claude/fold-app
or by hand, in the background with a 2-hour timeout:
    python3 -I ingest/us/discovermass_crawl.py crawl --all --minutes 100
    python3 -I ingest/us/discovermass_crawl.py publish --all
    git add data/real/us/discovermass && git commit && git pull --rebase --autostash origin main && git push origin HEAD:main
One state: `crawl --state TX`; several: `crawl --states TX,NY`; `status` prints index/fetched/website counts.

2026-11-01 01:06 UTC: scheduled run found no web egress

Then the pages: `python3 -I scripts/us/build_us_static.py`, but NOT from this scheduled crawl: public/us/** deploys on push, so a separate, less frequent task runs `bash scripts/us/rebuild_and_deploy.sh` (pull, build, typecheck/build, commit public/us, push; one deploy, none if unchanged).
