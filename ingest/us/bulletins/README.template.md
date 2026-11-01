# Groups from US parish bulletins (`data/real/us/bulletins/<diocese>/<domain>.json`)

Lane G9, rebuilt {{DATE}} by `ingest/us/bulletins/run.py`. Files only; nothing here is in Supabase. Raw PDFs, text, OCR and the
HTTP cache stay in `data/raw/us/bulletins/` (gitignored).

Why: parish websites list formal ministries; the informal groups a newcomer looks for (men's, women's, moms, young adults,
Bible study, prayer groups) are announced in the weekly bulletin. Goal metric: parishes whose own site gave Mass times AND groups.

## Resume

```bash
python3 -I ingest/us/bulletins/run.py --minutes 100 [--workers 8] [--dioceses diocese-of-austin] [--refresh]
python3 -I ingest/us/bulletins/run.py --extract-only      # re-extract from cached text, no network
bash scripts/us/bulletins_chunk.sh [minutes]               # pull, run, commit per diocese, push (for a scheduled task)
```
Input: `data/real/us/sites/<diocese>/<domain>.json` with `crawl_status: ok` and a `bulletin.url`. A site with a state file in
`data/raw/us/bulletins/_state/` is skipped (new sites from the site crawl are picked up on the next run). Order: Mass times but
no groups first, then times + groups, then the rest; links that only point at discovermass.com last (no discovermass.com request is
ever made; they resolve only if the parish's own page links a PDF).

## File shape
Same as California's (`data/real/california/bulletins/README.md`), one file per parish website instead of per diocese: `diocese`,
`domain`, `site_file`, `parishes[]` (church_id, name, city), `status`, `had_mass_times`, `site_groups_before`,
`gained_first_groups`, `issues[]` (url, date, pages, ocr, fetched_at), `tried[]`, `groups[]` (id, name, category, subcategory,
audience, language, informal, informal_kind, meets, next_meeting, location, contact "ask the parish office", source_label
"From the parish bulletin, <date>", source_url, last_seen, confidence, flags, also_on_parish_website).

## Integration note for `scripts/us/build_us_static.py`
Exactly as California's merge (steps 1-6 of the California README), keyed by the site file:
1. For each parish, read `data/real/us/bulletins/<diocese>/<site_key>.json` for the site(s) attached to it (the sites file
   lists `matched_church_ids`; the bulletin file repeats them in `parishes[].church_id`).
2. Skip groups whose `last_seen` is over 180 days before the build date, and `informal: false` groups whose `subcategory` the
   site groups already have.
3. Dedupe against the site's groups by normalised name and by `(subcategory, language)`: keep the site record, add the bulletin as a
   second source, fill `meets` from the bulletin when the site has none.
4. Append the rest with `source_url`, `meets` = `meets.text` (or "Next: <date>"), `confidence`, and the label
   **"From the parish bulletin, <Month D, YYYY>"** (`source_label`). Contact stays "ask the parish office".
5. Coverage "groups" source becomes "the parish's own website and bulletin".

## Rules kept
Honest FindTheChurch user agent, HTTPS only, robots.txt and Crawl-delay honoured, at most 1 request per second and one in flight per
host, a challenge/403/429 is recorded and the host not asked again in the run, no discovermass.com request, only links and container
codes already seen on the parish's pages (never enumerated). Extraction is rule-based with California's privacy screens; no lay names,
phones or e-mails are stored.

## Measures ({{DATE}})
`times_no_groups_sites` = sites that had Mass times but no groups on their own site (the target); `gained_first_groups` = of
those, sites where the bulletin supplied at least one group.

| diocese | sites | PDFs got | issues read | times, no site groups | sites with bulletin groups | gained first groups | groups | informal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{{ROWS}}

Site statuses: {{STATUS}}.

Informal groups by kind: {{KINDS}}.
