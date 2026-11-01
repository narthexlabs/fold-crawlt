# ingest/parishsoft — ParishSOFT adapter (stub)

Source: ParishSOFT (Ministry Brands) diocesan-level export. Preferred route is one scheduled diocesan export rather than per-parish logins.

Outputs (`confidence = synced`): `registered_households`, `baptisms`, `first_communions`, `confirmations`, `marriages`, `religious_ed`. Match parishes by ParishSOFT organization id → `entity.id` using a crosswalk file in this folder.
