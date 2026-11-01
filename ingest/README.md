# ingest/

Adapters that pull from an outside system and write normalized rows into the schema in `../schema`. Nothing here runs yet; the folders mark where each adapter will live.

## Adapter pattern

Each adapter is a folder with one entry point that does four things:

1. **Fetch** from the source (API, export file, or public web page). Raw pulls go in `<adapter>/raw/`, which is git-ignored.
2. **Match** source records to `entity.id`. Keep the crosswalk (source id → entity id) in the adapter folder; unmatched records are reported, never guessed.
3. **Map** source fields to `kpi_value` rows (`entity_id, kpi_id, period, value, source, confidence`) and, where relevant, `integration` and `assignment` rows.
4. **Upsert** with the Supabase service role on the natural keys (`kpi_value` primary key is `entity_id, kpi_id, period`), so reruns are idempotent.

Confidence rules: values an adapter pulls straight from a system of record are `synced`; values from a parish-submitted form are `reported`; anything derived or imputed is `estimate`.

## Planned adapters

| Folder | Source | First outputs |
|---|---|---|
| `public/` | Diocesan website directory, parish/school sites, CDE private school data, Census | entity names, addresses, coordinates, websites |
| `parishsoft/` | ParishSOFT diocesan export | registered households, sacraments, faith-formation enrollment |
| `flocknote/` | Flocknote API / exports | list sizes and engagement per parish |

Further adapters (Realm, Pushpay, OSV, FACTS, Blackbaud) follow the same shape; see `../docs/integration-roadmap.md`.
