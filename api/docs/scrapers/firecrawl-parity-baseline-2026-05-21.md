# Firecrawl Parity Baseline — 2026-05-21

## Post-T1/T3/T4 Retest (2026-05-21)

All four long-tail sources re-benchmarked after the multi-class chunker (T1),
recall gate (T3), and Cowes Week URL expander (T4) landed. 8 URLs per source
sampled with `firecrawl-diff --limit 8 --days 9999`.

### Gate query results

| Source | URLs | Mean recall | P10 recall | Min | Max | Gate (≥0.85 mean, ≥0.75 P10, ≥20 URLs) |
|--------|------|------------|-----------|-----|-----|----------------------------------------|
| RHKYC | 8 | **0.889** | 0.745 | 0.667 | 1.000 | ❌ P10 just below floor; sample too small |
| Cowes Week | 8 | 0.785 | 0.634 | 0.556 | 1.000 | ❌ Mean below; name-match gap |
| ISORA | 9 | 0.747 | 0.309 | 0.214 | 1.000 | ❌ Tail URLs still low |
| SailRaceHQ | 8 | 0.295 | 0.000 | 0.000 | 0.846 | ❌ Dominated by hollow-legacy and JS-rendering |

No source meets the full cutover gate yet.

---

### Per-source findings

#### RHKYC — closest to ready (mean 0.889)

RHKYC is the best-performing source. 4 of 8 URLs hit 100%, two at 83%, one at
78%, one at 67%. The 67% URL (China Coast Regatta 2025) has only 3 named legacy
boats — a 1-boat mismatch drives the recall to 67% on a tiny sample. This is
noise, not a systematic extraction failure.

**Blocker**: Only 8 URLs sampled. Need ≥20 to meet the gate. P10 is 0.745 (just
below 0.75), inflated by the tiny-sample 67% outlier.

**Recommended action**: Run `firecrawl-diff --source rhkyc --limit 20 --days 9999`
to grow the sample. With a larger sample, P10 should stabilise above 0.75.
RHKYC is the most likely source to pass the gate on the next iteration.

#### Cowes Week — mean 0.785, name-match gap

The Cowes Week extractor is consistently returning fc_rows ≥ legacy_named
(extracting the same or more boats), yet matched < fc_named in several cases.
This points to a boat-name normalisation gap between the legacy scraper and
the Firecrawl extractor, not under-extraction. Example: one URL shows
legacy_named=9, fc=19, matched=7 — the extractor found 19 boats, but only 7
matched the 9 named legacy boats. The extra 10 are likely correct boats the
legacy scraper labelled differently (e.g. "RAMPAGE" vs "Rampage 88").

Also notable: legacy hollow rates are 27–52%, meaning the legacy scraper itself
was not capturing all boat names.

**Recommended action**: 
1. Investigate name-match misses for a representative Cowes Week URL — are the
   missing legacy names truly in the Firecrawl output under a different form?
2. If yes, tighten the `_name_match` heuristic in `firecrawl-diff` (currently
   containment-based), or accept that firecrawl_diffs under-reports Cowes Week
   recall due to legacy naming variance.
3. URL expander (T4) covers 8 per-class URLs per year — the firecrawl-diff
   sample is drawing from the existing legacy URLs, which used a different
   class-URL scheme before 2026.

#### ISORA — bimodal (21–100%)

T1 chunker resolved the multi-class series pages:
- `id=331` (D2D combined): 94.4% recall, 40–42 fc rows extracted
- `id=332` (D2D by class): 94.4% recall, 40–42 fc rows extracted
- `id=333` (ISORA series): 92.9% recall, 82 fc rows
- `id=335`: 100% recall
- `id=336`: 85.7% recall

Remaining low-recall URLs:
- `id=337` (33%, fc=10 vs legacy_named=12): extractor returned only 10 rows on
  a page that has 12 named legacy boats. The page may use a layout the chunker
  doesn't split (non-`#` class headers, or a single-table layout where the model
  hits the token limit).
- `id=334` (21%, fc=12 vs legacy_named=14): same pattern.
- `id=338` Royal Dee Champs (75%, fc=133 vs legacy_named=12, matched=9): The
  chunker extracts 133 rows across 18 class headers, but only 9 of 12 named
  legacy boats are matched. The name-matching algorithm struggles with 133
  candidates — the 3 missing legacy boats are probably present under class
  headers that don't match their legacy source_url grouping.

**Recommended actions**:
1. Inspect `id=334` and `id=337` markdown structure — are they single interleaved
   tables without class headers, or a different format? May need an additional
   `_CLASS_HEADER_RE` pattern or a table-row-count chunk split.
2. For `id=338` (Royal Dee, fc=133 matched=9/12): investigate why 3 legacy boats
   are missing — run `firecrawl-diff --source isora --url <id=338 URL>` and check
   `missing_names` in the DB row.

#### SailRaceHQ — not ready (mean 0.295, dominated by hollow legacy and JS rendering)

Four of 8 sampled URLs have `legacy_named=0` (100% hollow legacy) — these are
successful Firecrawl extractions (10–11 boats each) where the legacy scraper
stored no boat names. They count as recall=0 by the matching metric even though
the extraction is fine.

The two truly problematic URLs:
- Caribbean 600 (`54536fa7`): legacy_named=41, fc=21, recall=46.3%. SailRaceHQ
  renders this page with JS; Firecrawl returns markdown with no table rows. The
  extractor guesses from prose-style text, returning ~21 of 41 boats. The
  multi-class chunker doesn't trigger (0 class-header `#` lines in the markdown).
- `8ced2edd`: legacy_named=43, fc=22, recall=20.9% — same JS-rendering problem.

Two near-passing URLs (`a3eeb665`: 84%, `fc91f08a`: 84.6%) show the extractor
works well on simpler SailRaceHQ pages.

**Recommended actions**:
1. Fix the hollow-legacy sample bias: filter out URLs where legacy_named=0 before
   computing the gate metric (they skew mean to 0 unfairly).
2. The Caribbean 600 and similarly large JS-rendered pages need either:
   a. A Firecrawl `actions` step to trigger table rendering before markdown
      conversion, or
   b. A bespoke SailRaceHQ scraper that hits the underlying data API (SailRaceHQ
      is a Sailwave front-end — there may be a JSON endpoint).
3. Until those are resolved, SailRaceHQ cannot cutover.

---

## What T1/T3/T4 changed

### Before (2026-05-21 pre-fix baseline)

| Source | Mean recall | Notable RED URLs |
|--------|------------|-----------------|
| ISORA | ~0.40 | Royal Dee: 5/28 boats; D2D series: 22/58 boats |
| SailRaceHQ | ~0.47 | Caribbean 600: 20/41 boats |
| Cowes Week | ~0.83 | Minor name-match gap |
| RHKYC | ~0.94 | Already good |

### After T1/T3/T4

| Source | Mean recall | Lift | Notes |
|--------|------------|------|-------|
| ISORA | 0.747 | +~0.35 | Multi-class pages now 85–100%; tail URLs still 21–75% |
| SailRaceHQ | 0.295 | ~0 | JS-rendering blocks; hollow-legacy sample |
| Cowes Week | 0.785 | +~0 | URL expander adds new per-class URLs; name-match gap |
| RHKYC | 0.889 | ~0 | Already good; P10 borderline |

T1 (multi-class chunker) provided the largest lift for ISORA — the D2D, Royal
Dee Champs, and other multi-class series pages went from single-class extractions
(8–40% recall) to full-class extractions (85–100% recall).

SailRaceHQ and Cowes Week were not helped by T1 because their problem is
JS-rendering and name-normalisation, not markdown class-header splitting.

---

## Recommended next steps

| Priority | Action | Expected impact |
|----------|--------|----------------|
| 1 | Build RHKYC sample to ≥20 URLs | Likely passes gate; ready to cutover |
| 2 | Fix hollow-legacy sampling in firecrawl-diff (exclude legacy_named=0 URLs from gate) | Makes SailRaceHQ mean honest |
| 3 | Inspect ISORA `id=334` and `id=337` markdown — add regex pattern if needed | Pushes ISORA P10 above 0.75 |
| 4 | Investigate SailRaceHQ JSON API or Firecrawl actions for JS-rendered pages | Only path to Caribbean 600 recall |
| 5 | Investigate Cowes Week name-match misses | May reveal recall is higher than reported |

Cron remains held until at least one source meets the full gate.
