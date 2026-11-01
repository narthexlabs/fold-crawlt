# ingest/public — public data adapter (stub)

No credentials needed. Intended sources: the diocesan parish and school directory pages, individual parish and school websites (Mass times, address, staff), California Department of Education private-school enrollment filings, and Census ACS tracts for population context.

Output: `entity` rows (name, address, lat/lng, website) with `name_source = verified`, replacing the mock seed. Geocode addresses once and cache the result.
