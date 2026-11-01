# Brave Search lookup

Queries so far: 15829; distinct parish sites found: 6066 (38% hit rate). One query per parish name + city + state; first result whose host is not a directory/social/diocese host and whose title or URL looks like a parish. Evidence per query in data/raw (done.jsonl).

Second run (2026-11-01, ranked): tier 1: 2539 queries, 1089 hits; tier 2: 6295 queries, 2454 hits; tier 3: 1000 queries, 205 hits. Tier 1 = diocesan directory / GCatholic / browser-read records with no website; tier 2 = catholicparish.net slugs that do not fuzzy-match (name tokens + city) a parish we already have a site for; tier 3 = the rest. States with >=70% of catholicparish.net slugs already matched are skipped outside tier 1. Queries run in batches of 500; a tier stops when its last-500 new-domain rate falls below 30%.

Third run (2026-11-01): 4,849 queries (tier 2 to exhaustion, tier 3 gated at 22%; then states 70-85% covered: tier 2 gated at 17%, tier 3 at 19%). A 40-hit spot check of that run found ~53% right, so a host-pattern deny (DENY_RE: .gov, .edu, tourism, newspapers, town pages, other denominations) now filters picks and the build.

Resume: `BRAVE_KEY=... python3 -I ingest/us/url_sources/brave_lookup.py --budget N [--checkpoint-cmd CMD]`
