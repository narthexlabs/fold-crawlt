# US national spine (lane G, steps 1-2)

Built 2026-10-31 by lane G, session 1. Files only: nothing here is in Supabase. Scripts: `ingest/us/`.

## 1. Diocese spine: `dioceses.json`
- **Source:** USCCB, "Bishops and Dioceses by State", https://www.usccb.org/about/bishops-and-dioceses/all-dioceses
  (public HTML page). Fetched 2026-10-31T23:31:41+00:00 (UTC), 2 requests (robots.txt, then the page, 1.5 s apart) with the
  honest user agent `FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`. robots.txt allowed the
  page; no Crawl-delay. Not blocked. The Wikipedia fallback was not needed.
- **Method:** `python3 -I ingest/us/usccb_dioceses.py` (add `--refresh` to refetch; raw HTML is cached, git-ignored,
  in `data/raw/us/usccb/`). One record per entry on the page.
- **Fields:** slug, name, rank, rite (Latin/Eastern), eastern_church (only where the title names it), see_city,
  see_state, usccb_state_heading, website, chancery_address, chancery_zip, leadership_as_listed (public clergy
  roles exactly as USCCB lists them), source_url, fetched_at. `province` and `states_served` are `null`: USCCB's
  page does not show them (states_served comes later from county shapes, as in HANDOFF section 4 step 1).
- **Count: 196 sees.** By rank: archdiocese 33, diocese 143, ordinariate 1, military services 1,
  eparchy 16, archeparchy 2. By rite: **Latin 178** (archdioceses, dioceses, the ordinariate and the Military Services),
  **Eastern 18** (the eparchies and archeparchies, including the Syro-Malabar and Syriac sees that USCCB styles "Diocese").
  Three Eastern titles (Newton, St. Maron of Brooklyn, Parma) do not name their church, so `eastern_church` is null.
- **Not on the USCCB page** (searched the parsed titles): no Puerto Rico sees (a separate episcopal conference) and no
  Guam/Pacific sees. Not added from memory; check a second source before calling the spine complete. The Personal
  Ordinariate of the Chair of St. Peter (Houston) is on the page and is ranked `ordinariate`.
- State on this page is the state of the see. Many dioceses cross state lines; `states_served` is still to do.

## 2. Church index: `discovermass_index.json`
- **Source:** discovermass.com WordPress sitemaps (`wp-sitemap.xml` -> `wp-sitemap-posts-item-1..11.xml`). Secondary source
  (about 78% of its Mass times match hand-checked ones; see docs/research/13-mass-times-accuracy.md).
- **Method:** no church page was requested and **no new request was made**. The sitemaps are national and were already fetched
  2026-10-31T06:17:00+00:00 to 2026-10-31T06:18:55+00:00 by the California crawler (robots.txt `Crawl-delay: 10`,
  10.5 s pacing, lock file). Its cache lock was free when this ran and no discovermass process was running.
  `python3 -I ingest/us/discovermass_index.py` only reads `data/raw/california/discovermass/sitemaps/`. To refresh that cache run
  `python3 -I ingest/california/discovermass_crawl.py index`, then the script.
- **Slugs** are `<parish-name>-<city>-<st>`. State = trailing postal code (`-d-c` = DC). 132 slugs end `-<st>-2`
  (WordPress's duplicate-slug marker; 131 of them have the same slug without `-2`): kept in the file with `dup`=2 and counted
  separately; the California crawler also leaves them out. `city_guess` is a heuristic (shared-suffix rule, then
  "san/new/west..." and "park/springs..." folding); it errs, so treat it as a hint.
- **National total: 20,147 church pages** (20,279 sitemap URLs including the 132 `-2` pages), all `/church/` URLs.
  State is not recoverable from the slug for 110 of them (44 end `-washington`, probably DC; 3 look like the US Virgin Islands;
  the rest have no place at all). They are listed in `unresolved_state_slugs`. Counts below are the 20,037 resolved ones;
  50 states plus DC (5); no PR/GU/VI slug resolved.

| State | Church pages (excl. `-2`) | All sitemap URLs |
|---|---:|---:|
| NY | 1,589 | 1,589 |
| PA | 1,355 | 1,355 |
| TX | 1,353 | 1,353 |
| CA | 1,327 | 1,457 |
| IL | 1,069 | 1,069 |
| OH | 875 | 875 |
| MI | 812 | 813 |
| WI | 801 | 801 |
| NJ | 748 | 748 |
| MN | 689 | 689 |
| MA | 675 | 675 |
| FL | 541 | 541 |
| LA | 522 | 522 |
| MO | 489 | 489 |
| IA | 459 | 459 |
| IN | 428 | 428 |
| CT | 397 | 397 |
| KS | 357 | 357 |
| NE | 307 | 307 |
| MD | 304 | 304 |
| WA | 299 | 299 |
| KY | 286 | 286 |
| CO | 271 | 271 |
| NM | 269 | 269 |
| VA | 243 | 243 |
| ND | 241 | 241 |
| AZ | 236 | 236 |
| OR | 207 | 207 |
| MT | 204 | 204 |
| NC | 201 | 201 |
| SD | 193 | 193 |
| OK | 189 | 190 |
| GA | 178 | 178 |
| AL | 168 | 168 |
| ME | 164 | 164 |
| RI | 163 | 163 |
| TN | 162 | 162 |
| MS | 149 | 149 |
| WV | 129 | 129 |
| VT | 126 | 126 |
| NH | 125 | 125 |
| SC | 122 | 122 |
| ID | 102 | 102 |
| AR | 92 | 92 |
| AK | 87 | 87 |
| HI | 74 | 74 |
| WY | 74 | 74 |
| NV | 67 | 67 |
| UT | 66 | 66 |
| DE | 48 | 48 |
| DC | 5 | 5 |

## Blocked
Nothing. USCCB returned 200 to the honest user agent; DiscoverMass was not contacted this session.

## Next steps (HANDOFF section 4, steps 3-5)
3. **Mass times.** Generalise ingest/california/discovermass_crawl.py with `--state`. One crawler, >=10.5 s between
   requests, resumable cache, one commit per state. Order by Catholic population (CA done; then TX, NY, IL, FL,
   PA, NJ, MA, OH, MI, ...). The whole country is roughly two days of crawling at that pace; run it in chunks.
4. **Do not** start national parish-website crawls until lane A's templates pass their bar; DiscoverMass is ~78%
   accurate against hand-checked times, so label it as a secondary source, as in California.
5. A /us landing page comes later, after California's front door is decided.
