# SPEC-12: Boat Deduplication & Identity Matching

## 1. Overview
This specification outlines the data hygiene and identity resolution pipeline for boats. The primary goal is to maintain a single, canonical representation of each physical boat in the `boats` table, even when different scraping sources (SailSys, TopYacht, IRC, ORC) use slightly different naming conventions.

## 2. The "Aggressive Merge" Strategy
Based on product requirements, the system will use an **Aggressive Merge** strategy based strictly on the Sail Number.

### 2.1 The Rules
1. **Primary Key Match:** The `sail_number` is the ultimate source of truth for physical identity.
2. **Ignore Name Drifts:** If Boat A is named `"Wild Oats XI"` and Boat B is named `"WOXI"`, but both share the sail number `"4343"`, they MUST be merged.
3. **Handling Conflicts:** When merging two records with the same sail number:
   - The most recently updated record dictates the canonical `name`.
   - All historical names are appended to the `identities` JSONB array or an `alias` table for searchability.
   - All foreign keys (certificates, race results) pointing to the obsolete `boat_id` must be repointed to the canonical `boat_id`.

## 3. Database Operations (Postgres)
- Create an Alembic migration for a stored procedure or a Python script in `api/scripts/merge_boat_dupes_aggressive.py`.
- The script must:
  1. Find all duplicate `sail_number` entries in the `boats` table.
  2. Elect the row with the most recent `updated_at` as the canonical row.
  3. Re-assign `boat_id` in `irc_certificates`, `orc_certificates`, `tcc_snapshots`, and `boat_events` tables.
  4. Soft-delete or hard-delete the obsolete boat rows.

## 4. Acceptance Criteria
- [ ] `merge_boat_dupes_aggressive.py` script is fully implemented and tested.
- [ ] All boats with identical sail numbers are merged successfully in the database.
- [ ] The `identities` array of the canonical boat contains all prior names so users can still search for them via old aliases.
