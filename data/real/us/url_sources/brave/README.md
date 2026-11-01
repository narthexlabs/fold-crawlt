# Brave Search lookup

Queries so far: 12980; distinct parish sites found: 5755 (44% hit rate). One query per parish name + city + state; first result whose host is not a directory/social/diocese host and whose title or URL looks like a parish. Evidence per query in data/raw (done.jsonl).

Second run (2026-11-01, ranked): tier 1: 2539 queries, 1089 hits; tier 2: 4446 queries, 1933 hits. Tier 1 = diocesan directory / GCatholic / browser-read records with no website; tier 2 = catholicparish.net slugs that do not fuzzy-match (name tokens + city) a parish we already have a site for; tier 3 = the rest. States with >=70% of catholicparish.net slugs already matched are skipped outside tier 1. Queries run in batches of 500; a tier stops when its last-500 new-domain rate falls below 30%.

Resume: `BRAVE_KEY=... python3 -I ingest/us/url_sources/brave_lookup.py --budget N [--checkpoint-cmd CMD]`
