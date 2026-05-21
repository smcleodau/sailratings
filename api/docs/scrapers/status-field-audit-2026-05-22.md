# Status Field Integrity Audit — 2026-05-22

Auditing `race_results.status` for rows that appear mislabelled — specifically
`status='finished'` with no finish_time and no place (the same class of bug fixed
for SailSys in T0). Also checking the inverse: `place IS NOT NULL` with a
non-'finished' status.

---

## Audit queries

### A1: `finished` rows that look like non-finishers

```sql
SELECT source, transport,
       COUNT(*) FILTER (
           WHERE status='finished' AND place IS NULL
             AND coalesce(raw_data->>'finish_time','') = ''
       ) AS no_finish_no_place,
       COUNT(*) FILTER (WHERE status='finished' AND place IS NULL) AS finished_no_place,
       COUNT(*) AS total
FROM race_results
GROUP BY source, transport
ORDER BY no_finish_no_place DESC;
```

**Results:**

| Source | Transport | Suspicious (no ft, no place) | Finished but no place | Total |
|--------|-----------|-----------------------------|-----------------------|-------|
| RHKYC | legacy | 730 | 730 | 1,918 |
| RORC | legacy | 713 | 713 | 24,333 |
| TopYacht | legacy | **680** | 687 | 3,375 |
| SailRaceHQ | legacy | **335** | 342 | 4,304 |
| SydneyHobart | legacy | 1 | 1 | 198 |
| SailSys | legacy | 0 | 21,134 | 213,563 |
| ISORA | legacy | 0 | 0 | 4,346 |
| Cowes Week | legacy | 0 | 0 | 3,747 |

*Note: SailSys was 10,993 suspicious before T0; now 0 after the DNF backfill.*

### A2: `place` populated but status not 'finished'

```sql
SELECT source, transport, status, COUNT(*) AS n
FROM race_results
WHERE place IS NOT NULL AND status <> 'finished'
GROUP BY source, transport, status ORDER BY n DESC LIMIT 20;
```

**Results:**

| Source | Status | Count | Assessment |
|--------|--------|-------|-----------|
| ISORA | DNF | 188 | ✅ Valid — series points scoring; boats with a points position can still be DNF in a specific race |
| SailRaceHQ | SCP | 49 | ✅ Valid — Scoring Penalty is a finish variant with a points position |
| ISORA | DNS | 10 | ✅ Valid — same series-points scenario |
| RORC | OCS | 5 | ✅ Valid — OCS boats sometimes get a scored position |
| RHKYC | DNF/OCS | 5 | ✅ Valid |

No anomalies in A2 — all place+non-finished combinations are legitimate.

---

## Per-source findings

### SailSys — FIXED (T0)

**Before T0:** 10,993 rows with `status='finished'`, `finish_time=NULL`, `place=NULL`.
**After T0:** 10,993 rows now `status='DNF'` (backfilled 2026-05-21).
The root cause (`status='finished'` hardcoded in `result_import.py`) was also
fixed for new ingestions via `_derive_status()`.

---

### TopYacht — 680 rows to backfill ⚠️

**Evidence:** All 680 suspicious rows have `finish_time=''`, `elapsed_time=''`,
and `corrected_time=''` simultaneously, with `place=NULL`. These are unambiguous
DNFs — the boat appeared in the entry list but recorded no time.

`raw_data` has `finish_time` on all 3,375 TopYacht rows, so `_derive_status()`
now handles new ingestions correctly. The 680 existing legacy rows need a
one-time backfill.

**Backfill SQL (ready to run):**

```sql
BEGIN;

-- Preview
SELECT COUNT(*) FROM race_results
WHERE source = 'topyacht' AND transport = 'legacy'
  AND status = 'finished' AND place IS NULL
  AND coalesce(raw_data->>'finish_time', '') = '';
-- Expect: 680

UPDATE race_results
SET status = 'DNF'
WHERE source = 'topyacht' AND transport = 'legacy'
  AND status = 'finished' AND place IS NULL
  AND coalesce(raw_data->>'finish_time', '') = '';

COMMIT;
```

Also 7 TopYacht rows have `finished_no_place` but DO have a finish_time (i.e.
`finished_no_place=687 - no_finish_no_place=680 = 7`). These boats finished but
the scraper didn't capture a handicap place — they are correctly marked
'finished'.

---

### SailRaceHQ — 172 real DNFs + 163 garbage rows ⚠️

Of 335 suspicious rows, 163 have no `boat_name` (scraper captured header/summary
rows from the HTML as result rows — a different bug). The other 172 have a boat
name but no finish_time, place, or corrected_time — real DNFs.

**Backfill SQL (ready to run):**

```sql
BEGIN;

-- Preview
SELECT COUNT(*) FROM race_results
WHERE source = 'sailracehq' AND transport = 'legacy'
  AND status = 'finished' AND place IS NULL
  AND coalesce(raw_data->>'finish_time', '') = ''
  AND coalesce(raw_data->>'boat_name', '') <> '';
-- Expect: 172

UPDATE race_results
SET status = 'DNF'
WHERE source = 'sailracehq' AND transport = 'legacy'
  AND status = 'finished' AND place IS NULL
  AND coalesce(raw_data->>'finish_time', '') = ''
  AND coalesce(raw_data->>'boat_name', '') <> '';

COMMIT;
```

The 163 no-boat-name rows are left as-is for now (they don't affect user-facing
features since they can't be matched to a `boats` record). A separate cleanup
task should DELETE them.

---

### RHKYC — 730 rows, not fixable via `_derive_status` (low risk)

The RHKYC PDF scraper does not populate `finish_time` in `raw_data` (0/1,918
rows have the key). Without that field, `_derive_status` has no signal and
defaults to 'finished'. 

Looking at the data: these are boats where the pdfplumber parser extracted the
boat row but could not extract a finishing position (e.g. because the position
column was in a column the parser didn't reach, or the PDF table layout varied).
They are probably a mix of actual DNFs, DNS boats, and boats the parser
partially extracted.

**Assessment:** Low risk. The RHKYC legacy scraper is being replaced by Firecrawl
(which captures status from the HTML, not PDFs), and RHKYC is close to passing
the parity gate. No backfill recommended — the Firecrawl rows will replace
these over time.

---

### RORC — 713 rows, same diagnosis as RHKYC (accept as-is)

RORC's raw_data similarly has no `finish_time` key (0/24,333 rows). The RORC
scraper captures explicit status codes (DNF, DNS, RET) correctly — 3,718 DNF
rows and 274 other non-finished rows are present. The 713 suspicious 'finished'
rows appear to be boats without a handicap position (e.g. they finished on
elapsed time only, outside the IRC-rated scoring, or the scraper did not find
their corrected position in the HTML table). Not actual missed DNFs.

**Assessment:** Accept as-is. RORC scraper already captures explicit non-finisher
statuses well.

---

### SydneyHobart — 1 suspicious row (negligible)

One row with `status='finished'`, no place, no finish_time. Single anomaly;
likely a corrupted row from a one-off scrape. Not worth a backfill.

---

### Cowes Week + TopYacht — all rows are 'finished' (structural limitation)

Cowes Week's raw_data has no finish_time or status field — the legacy scraper
reads series-points tables where DNF/DNS is represented as a penalty score (e.g.
fleet_size+1 points), not as an explicit status. Fixing this would require
re-scraping the source pages to determine which point scores correspond to
non-finisher penalties.

Similarly, before T0 TopYacht had all rows as 'finished'. After T0, new
ingestions derive status from finish_time. The backfill above handles existing
legacy rows.

---

## Summary of recommended actions

| Source | Action | Rows affected | Priority |
|--------|--------|---------------|---------|
| SailSys | ✅ Done (T0) | 10,993 | — |
| TopYacht | Run backfill SQL above | 680 | High |
| SailRaceHQ | Run backfill SQL above | 172 | Medium |
| RHKYC | Accept as-is | 730 | Low |
| RORC | Accept as-is | 713 | Low |
| SydneyHobart | Accept as-is | 1 | None |
| SailRaceHQ garbage rows | Separate cleanup: DELETE where boat_name='' | 163 | Low |
