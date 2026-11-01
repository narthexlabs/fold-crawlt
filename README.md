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

**Privacy**: the crawl keeps institutional information only — parish names, addresses, office phone and office e-mail,
websites, Mass and confession times, public groups and events. It does not keep anything about private individuals
(parishioners, the sick, the deceased, donors, lay volunteers' names or contact details); `ingest/public/privacy.py`
screens every saved record. Public clergy roles as listed by the diocese are kept.

**Resume / operate**: each `scripts/us/*_chunk.sh` pulls, runs for N minutes, commits and pushes; they skip what is
already done. See each `data/real/us/<source>/README.md`. To stop everything: Actions → "National crawl" → Disable.

**Licence**: code MIT (see LICENSE). Data: facts gathered from the sources above; each record names its source, and
the sources' own terms apply to their material (GCatholic.org, Wikidata CC0, Wikipedia CC BY-SA for text).
