# US parish website URLs from open sources (lane G6)

Feeds the national parish-site crawler (`ingest/us/sites/run_state.py`) with extra website URLs beyond DiscoverMass.
Built 2026-10-31. These are URLs only: no Mass times were read from either source, nothing here is ranked or scored.

## Sources
| source | file | status | licence / attribution |
|---|---|---|---|
| Wikidata SPARQL (query.wikidata.org) | `wikidata.json` | DONE: 774 items, 777 records, 759 distinct websites | CC0 |
| OpenStreetMap via Overpass (overpass-api.de) | `osm.json` | NOT FETCHED (retried 2026-11-01 via overpass.kumi.systems, which answered 200 to the coordinator: AL got 504 then timeout; a tiny WY query and /api/status also hung 120 s; stopped, no data): every request failed `conn_error` through the sandbox egress proxy (overpass-api.de, lz4/kumi/private.coffee mirrors all reset or time out). Recorded as unreachable_via_proxy; not worked around | "OpenStreetMap contributors, ODbL 1.0" (share-alike; keep this attribution if published) |

Wikidata method: step 1 expand the classes Q1088552 (Catholic church building), Q102496 (parish), Q2977 (cathedral), Q120560
(minor basilica) with P279* (104 classes); step 2 items with P31 in that set, P17=Q30 and P856 present (plain triples; a
single query with P279*, ORDER BY paging and the label service timed out on the public endpoint); step 3 English labels
and the US state (P131+ to an instance of Q35657) in batches of 200. One query at a time, honest User-Agent, 2 s apart.
Raw responses: `data/raw/us/url_sources/wikidata/` (gitignored). Only 54 items carry no state; they are placed by the
diocese label when possible, otherwise `_nostate.json` (not fed to the crawler).

## Merge (`merge.py`)
Writes `<ST>.websites.json` in the DiscoverMass shape (`websites[].url`, `websites[].churches[]` with slug/name/city/diocese/lat/lng,
plus `social_only[]`), deduplicated by host (www stripped; sites.google.com, wixsite.com etc. keep their first path
segment). Skipped like run_state: diocese sites, LPi/DiscoverMass/USCCB/aggregator hosts, school-looking hosts. If
`data/real/us/discovermass/*.websites.json` exist, hosts already listed there are dropped so the file holds only NEW sites
(`--no-exclude-discovermass` keeps them). Summary: `merge_summary.json`.
After merge (Wikidata only): **730 distinct websites**; skipped {'diocese_site': 13, 'non_parish_host': 10}.

| state | distinct websites after merge |
|---|---|
| NY | 87 |
| CA | 72 |
| IL | 58 |
| NJ | 38 |
| PA | 35 |
| TX | 31 |
| OH | 29 |
| FL | 28 |
| MA | 27 |
| _nostate | 27 |
| CT | 22 |
| MD | 20 |
| MI | 20 |
| WA | 14 |
| LA | 13 |
| AR | 11 |
| MN | 11 |
| IN | 10 |
| MO | 10 |
| AZ | 9 |
| IA | 9 |
| NC | 9 |
| WI | 9 |
| DC | 8 |
| GA | 8 |
| SC | 8 |
| VA | 8 |
| AK | 7 |
| AL | 7 |
| CO | 7 |
| KY | 7 |
| NV | 6 |
| OR | 6 |
| TN | 6 |
| HI | 5 |
| KS | 5 |
| NM | 5 |
| MS | 4 |
| MT | 4 |
| ND | 4 |
| NE | 4 |
| ID | 3 |
| OK | 3 |
| SD | 3 |
| UT | 3 |
| VT | 3 |
| DE | 2 |
| NH | 2 |
| RI | 2 |
| WV | 1 |

## How run_state reads these files
Since commit e6e90a9 `run_state.py` already globs `data/real/us/url_sources/**/*.websites.json` (`URL_DIR`), so nothing needs
changing. The state comes from the file name (`<ST>.websites.json`); `_nostate.json` is deliberately not matched. The
diocese is matched from the `diocese` label (Wikidata only; OSM has none), otherwise `unassigned-<st>`. Caveat: another lane
writing the same `<ST>.websites.json` names in this folder would collide; `merge.py` only deletes files whose
`source_label` is `url-sources`.

## Resume
    python3 ingest/us/url_sources/wikidata.py          # cached; --refresh to refetch
    python3 ingest/us/url_sources/osm.py               # per state, 25 s apart, stops after 3 failures; cached per state
    git add data/real/us/url_sources/osm.json && git commit   # commit per ~10 states
    python3 ingest/us/url_sources/merge.py
osm.py needs a machine where overpass-api.de is reachable (Matthew's laptop has python 3.10 stdlib only: the script uses
the repo fetch helpers, so run it from a full checkout, or save each state's JSON to `data/raw/us/url_sources/osm/<ST>.json`
and run `osm.py` to build `osm.json`).

## Wikipedia (lane G7)
`ingest/us/url_sources/wikipedia.py` -> `wikipedia.json` (all records, 85 list pages found / 77 parsed, 6,415 list entries,
2,527 articles read) and `wikipedia/<ST>.websites.json` (same shape as `data/real/us/discovermass/<ST>.websites.json`).
Source: English Wikipedia via the MediaWiki API only (list pages "List of churches in the (Arch)diocese of X", "List of parishes ...",
state categories "Roman Catholic churches/cathedrals in <State>" depth 1; website and coordinates from article infoboxes).
License CC BY-SA 4.0 (text); URLs are facts. ~900 records with a website, ~660 distinct domains; coverage is thin outside New York
and uneven (Wikipedia lists only notable churches), so treat it as a supplement to DiscoverMass and the diocese directories.
Filtered out: social, diocese, school, news/archive/government hosts, non-Catholic and non-US pages.
API pacing: >= 2 s per request, Retry-After honoured; the shared egress returns 429 often, so a full run takes ~2 h. Raw responses cached
in `data/raw/us/url_sources/wikipedia/` (gitignored). `wikipedia_state.json` is the resume point (discovered pages, parsed entries, articles).
Resume / rebuild: `python3 -I ingest/us/url_sources/wikipedia.py all` (cache hits make no request); `build` alone rebuilds outputs offline;
`discover2` adds each diocese's own category (about 400 more calls).
