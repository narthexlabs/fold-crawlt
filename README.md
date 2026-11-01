# fold-crawlt — the national parish crawl for Fold / Find the Church

Public-source data about Catholic parishes in the United States, gathered by polite crawlers, for the
[Find the Church](https://oakland-diocese-dashboard.vercel.app/about) map (the app itself lives in a private repo,
`narthexlabs/fold-app`, which syncs `data/real/us/` from here). This repo exists so the crawl can run on GitHub
Actions runners.

**What is here**
- `ingest/` — the crawlers and parsers (Python, standard library + `requests`). `ingest/us/` is national;
  `ingest/california/` and `ingest/public/` hold the shared parsers and the privacy screen they all run.
- `scripts/us/*.sh` — one resumable chunk per source; `.github/workflows/crawl.yml` runs them every two hours.
- `data/real/us/` — the output. Every record carries its source URL and `fetched_at`. Nothing here is typed in by hand
  and nothing is invented; missing data is simply missing.

**Sources**: each diocese's own public directory (USCCB's list of dioceses → the diocese website → its parish list),
each parish's own public website, the weekly bulletin PDF the parish publishes, GCatholic.org, Wikidata (CC0), Wikipedia
(facts and links), and DiscoverMass (Mass times as a secondary source only).

**How the crawlers behave**: they identify honestly as `FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`,
read `robots.txt` on every run and honour `Crawl-delay`, send at most one request per second to any host and only one
request at a time per host (discovermass.com: one crawler, 10.5 s apart), and record a challenge, 403 or 429 as
"blocked" and move on — never retried harder, never worked around. If you run a parish or diocese site and want us to
stop or to fix something, open an issue.

**Wayback recovery** (`ingest/us/wayback/`, job `wayback`): the diocese directories and parish sites we cannot read live
(bot challenge, 403, dead) are read from the Internet Archive's copies, at most one request per second to web.archive.org.
Diocese lists land in `data/real/us/dioceses/<slug>/parishes.json` labelled `wayback-<date>`; parish sites are merged into
their existing file with `crawl_status: wayback-<date>`, and their schedule rows stay `needs_review`. See
`data/real/us/wayback/README.md`; resume with `python3 -u ingest/us/wayback/run.py --minutes 110 --git`.

**Privacy**: the crawl keeps institutional information only — parish names, addresses, office phone and office e-mail,
websites, Mass and confession times, public groups and events. It does not keep anything about private individuals
(parishioners, the sick, the deceased, donors, lay volunteers' names or contact details); `ingest/public/privacy.py`
screens every saved record. Public clergy roles as listed by the diocese are kept.

**Resume / operate**: each `scripts/us/*_chunk.sh` pulls, runs for N minutes, commits and pushes; they skip what is
already done. See each `data/real/us/<source>/README.md`. To stop everything: Actions → "National crawl" → Disable.

**Licence**: all rights reserved (see LICENSE). The repo is public for transparency and so the crawl can run on
GitHub Actions, not as a grant to reuse the code or data. Each data record names its source; the sources' own terms
apply to their material (GCatholic.org, Wikidata CC0, Wikipedia CC BY-SA for text).

## OpenStreetMap parish websites (Geofabrik extracts, `osm` job)
`ingest/us/url_sources/osm_pbf.py` reads each state's extract from `https://download.geofabrik.de/north-america/us/<state>-latest.osm.pbf`
(no Overpass API), one state at a time: stream to a temp file, filter with `osmium tags-filter` + pyosmium, **delete the file**,
keep only the extracted facts. Filter: `amenity=place_of_worship`, `religion=christian`, `denomination` in (`catholic`,
`roman_catholic`), and a `website` / `contact:website` / `url` tag; name, website, lat/lng (way centroid = mean of node
locations; relations carry no coordinates), `addr:city`, state.
Attribution: **© OpenStreetMap contributors, ODbL 1.0** (https://www.openstreetmap.org/copyright). We use OSM as a lookup of
facts (which church has which website), not as a redistributed database; each record carries its OSM object URL.
Outputs: `data/real/us/url_sources/osm.json` (all records + counts) and `data/real/us/url_sources/osm/<ST>.websites.json`
(the shape `run_state.py` reads; deduplicated by host; diocese/school/social/aggregator hosts dropped as in `merge.py`;
hosts already in other websites files or already crawled under `data/real/us/sites/**` excluded).
Run: Actions -> "National crawl" -> job `osm`, minutes 110 (not on the schedule; each run does as many states as fit, smallest
first, and skips finished ones). **Resume command** (locally or on a runner): `python3 -u ingest/us/url_sources/osm_pbf.py --minutes 110`;
single states: `--states TX,CA`; rebuild outputs only: `python3 -I ingest/us/url_sources/osm_pbf.py --build`.

Counts per state: see `counts.by_state` in `data/real/us/url_sources/osm.json` (filled in by the first runs; table below).
<!-- osm-counts -->
