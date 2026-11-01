# GCatholic.org church index (lane G8)

Facts only (name, city, state, status, website URL, coordinates decoded from GCatholic's Plus Code, GCatholic id).
Attribution: GCatholic.org. robots.txt allows us (disallows /temp/ and AI-training bots; no Crawl-delay). One crawler,
>= 2.2 s between requests, honest FindTheChurch user agent; a 403/429/challenge writes `STOPPED.json` and halts.
Method: `/dioceses/country/US` (index of 195 US sees, all mapped to USCCB slugs: `diocese_map.json`) ->
`/churches/local/<code>` (one row per church) -> each `/churches/<region>/<id>` page (Website, Plus Code, address).
Raw HTML cache: `data/raw/us/url_sources/gcatholic/` (gitignored). Outputs: `../gcatholic.json`, `<ST>.websites.json`
(DiscoverMass shape, read by run_state.build_inputs via its url_sources glob), `summary.json`.
Priority: sees whose own directory is challenge/dead/blocked/egress_closed first.

Resume (skips cached pages; stop cleanly after N minutes; rebuild outputs at the end of each run):

    python3 ingest/us/url_sources/gcatholic.py --minutes 115
    python3 ingest/us/url_sources/gcatholic.py --build      # outputs only, from cache

## Progress
- 2026-11-01: 10 dioceses complete, 790 church pages cached. Resume: python3 ingest/us/url_sources/gcatholic.py --minutes 115
- 2026-11-01: 20 dioceses complete, 2014 church pages cached.
