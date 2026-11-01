# Brave Search lookup

Queries so far: 5294; distinct parish sites found: 2794 (53% hit rate). One query per parish name + city + state; first result whose host is not a directory/social/diocese host and whose title or URL looks like a parish. Evidence per query in data/raw (done.jsonl).

Resume: `BRAVE_KEY=... python3 -I ingest/us/url_sources/brave_lookup.py --budget N`
