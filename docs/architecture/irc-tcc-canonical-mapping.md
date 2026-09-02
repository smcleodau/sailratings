# IRC TCC Listings → Canonical Assertions (DP-06-03)

> The mapping contract for the DP-06 vertical-slice source **`irc-tcc`**
> (IRC TCC Listings): field mapping, units, missing-value semantics,
> transforms and rejects — with source spans preserved end-to-end so
> every canonical record is lineage-complete back to the raw artifact.
>
> **Code of record:**
> `api/src/irc_data/transform/irc_tcc_mapping.py` (mapping +
> transformer), `api/src/irc_data/parsers/tcc_listing_parser.py`
> (span-preserving parser), `api/src/irc_data/transform/lineage.py`
> (lineage query).
> **Builds on:** DP-06-01 (source selection), DP-06-02 (certified
> adapter/parser, `ExtractionBatchV1`), DP-03-04 (schema-versioned
> transformation pipeline), DP-02-03 (extraction contract / source
> spans), DP-02-01 (raw artifacts / content hashes).
> **Output contract:** `CanonicalBatchV1`
> (`TransformationBatchV1` of `CanonicalAssertionV1`, assertion type
> `tcc_listing`, schema `v1`).
> **Verification:** `api/tests/transform/test_irc_tcc_golden.py` (golden
> source records vs expected assertions + provenance) and the
> human-runnable evidence script `api/scripts/verify_dp_06_03.py`.

---

## 1. Source and goal

DP-06-01 selected **IRC TCC Listings** (`data_sources.slug = "irc-tcc"`,
<https://ircrating.org/irc-racing/online-tcc-listings/>, format CSV,
public-domain, already an Active source) as the first vertical-slice
source.  DP-06-03's goal is to **produce lineage-complete canonical
records** from it.

The source publishes one CSV (``ClubListing_YYYYMMDD.csv``) with one row
per IRC certificate.  Two variants exist in the wild and are both
handled (via the shared column-alias map):

* **2026 format** — utf-8-sig; headers `Boat Name, Sail No, Cert No,
  Issue Date, Cert Year, TCC, Endorsed, Secondary, Non Spi TCC, …`.
* **2009 Wayback format** — latin-1; headers `Valid Date`, `SYSCertYear`,
  `E`, `Short Handed`, `LOA`, `TCC Non spi` (aliased to the same
  extracted fields).

---

## 2. Pipeline

```
raw CSV artifact (DP-02-01, content-addressed)
      │  IRCTCCListingParser            (DP-02-03, span-preserving)
      ▼
ExtractionBatchV1                     — one tcc_listing_row per CSV row,
                                        every field a CSV_ROW Locator
      │  IRCTCCListingTransformer      (DP-03-04 BaseTransformer,
      │                                 applies FIELD_MAPPINGS)
      ▼
TransformationBatchV1  =  CanonicalBatchV1
   ├─ assertions: CanonicalAssertionV1 (tcc_listing@v1, lineage, units)
   └─ rejects:    RejectedRecordV1     (machine-readable reasons)
      │  trace_assertion / LineageIndex (this module, lineage.py)
      ▼
LineageReport: assertion → extraction batch → raw artifact (+CSV span)
```

The transformer is a **pure function** of the extraction batch: no
network, filesystem or clock.  Replaying the same artifact yields the
same `transformation_id`, `assertion_id`s and hashes.

---

## 3. Field mapping

`FIELD_MAPPINGS` is the authoritative, executable mapping.  Every
canonical `tcc_listing` field names its source field(s), transform,
missing semantics and rationale.  Required fields: **`sail_number`** and
**`tcc`** — a listing row that cannot produce both is rejected, not
published.

| Canonical field | Source column(s) | Transform | Missing | Unit |
|---|---|---|---|---|
| `sail_number` | `Sail No` | normalise (upper-case, trim, collapse ws) | **required** → reject | — |
| `tcc` | `TCC` | decimal | **required** → reject | dimensionless |
| `boat_name` | `Boat Name` | strip ` - SEC` / ` (SH)` suffix | → `None` | — |
| `cert_number` | `Cert No` | string | → `None` | — |
| `cert_year` | `Cert Year` / `SYSCertYear` | int | → `None` | year |
| `issue_date` | `Issue Date` / `Valid Date` | ISO-8601 date | → `None` | — |
| `non_spi_tcc` | `Non Spi TCC` / `TCC Non spi` | decimal | → `None` | dimensionless |
| `endorsed` | `Endorsed` / `E` | string | → `None` | — |
| `is_secondary` | derived (` - SEC`/` (SH)`/`Secondary`) | bool | → `False` | — |
| `crew` | `Crew` | int | → `None` | count |
| `dlr` | `DLR` | int | → `None` | dimensionless |
| `lh` | `LH` / `LOA` | decimal | → `None` | m |
| `beam` | `Beam` | decimal | → `None` | m |
| `draft` | `Draft` | decimal | → `None` | m |
| `single_furling_headsail` | `Single Furling Headsail` | string | → `None` | — |
| `headsails` | `Headsails` | int | → `None` | count |
| `flying_headsails` | `Flying Headsails` | int | → `None` | count |
| `spinnakers` | `Spinnakers` | int | → `None` | count |
| `series_date` | `Series Date` | int | → `None` | year |
| `age_date` | `Age Date` | int | → `None` | year |
| `racing_area` | `Racing area` | int | → `None` | code |
| `ssb_base_value` | `SSS Base Value` | int | → `None` | points |
| `stix` | `STIX` | int | → `None` | points |
| `avs` | `AVS` | int | → `None` | degrees |
| `category` | `Category` | string | → `None` | — |
| `valid_code` | `ValidCode` | string | → `None` | — |

### Explicitly unsupported source fields

Nothing the parser emits is silently dropped — every extracted field is
either mapped above or listed here with a reason:

| Source field | Reason not published |
|---|---|
| `secondary` | Raw flag text; superseded at extraction by the derived boolean `is_secondary`.  Publishing both would duplicate one fact in two shapes. |
| `country` | *Derived* from the sail-number prefix, not asserted by the source.  Derivation is enrichment (DP-06-04), not a source assertion. |
| `design` | *Heuristically inferred* from hull dimensions, not published per-row.  Enrichment (DP-06-04), not a source assertion. |

### Canonical fields not provided by the source

| Canonical field | Reason |
|---|---|
| `units` | Not a source column — populated by the mapping itself from `CANONICAL_UNITS` so the record is self-describing. |

`audit_mapping_coverage(batch)` checks both directions at runtime and is
asserted complete in the golden tests: every extracted field is mapped
or explicitly unsupported, and every canonical schema field has a mapping
or a stated reason.

---

## 4. Units

IRC publishes canonical SI values, so **no numeric conversion is
required** — every conversion is `identity`.  The declaration is
attached to each assertion payload under `units` (only for fields
actually present) so consumers never guess:

```
tcc, non_spi_tcc, dlr   → dimensionless
lh, beam, draft         → metres
ssb_base_value, stix    → points
avs                     → degrees
crew, headsails, …      → count
cert_year, series_date, age_date → year
racing_area             → code
```

---

## 5. Missing-value semantics

IRC publishes `""` for *not measured / not applicable / not published*.
The mapping never invents values:

* **Not published** (empty/absent cell) → canonical `None`; publishable
  for optional fields.
* **Required missing** (`sail_number`, `tcc`) → the record is rejected.
* **Present but unparseable** (e.g. `TCC = "not-a-number"`) → the record
  is rejected.  A malformed value must *not* silently degrade to `None`,
  because that would erase the distinction between "source said nothing"
  and "source said something we could not read".

## 6. Rejects

Rejected records are emitted separately (never partially publish) with
machine-readable reason codes:

* `missing_required_field:sail_number` — no usable sail number.
* `missing_required_field:tcc` — TCC absent.
* `not_parseable:tcc` / `not_parseable:date` / `not_parseable:decimal` /
  `not_parseable:int` — a present value failed coercion.

Each reject carries the transformer name/version, the schema version and
a snapshot of the raw row fields for triage.

---

## 7. Lineage — the query reaches the raw artifact

Every assertion carries an `AssertionLineage` (artifact id, content
hash, extraction batch id, parser + schema versions, and the per-field
`CSV_ROW` source spans).  `irc_data.transform.lineage` makes it
queryable:

```python
from irc_data.transform.lineage import index_batch

idx = index_batch(tx_batch)
idx.all_reach_raw_artifact(artifact_content=csv_bytes)   # True
report = idx.trace(assertion_id, artifact_content=csv_bytes)
report.chain                       # assertion → extraction batch → artifact
report.content_hash_verified       # artifact bytes hash == lineage hash
report.spans[...].resolved_text    # the raw CSV cell text (e.g. "1.015")
```

Because the artifact bytes are re-hashed at query time, a tampered or
wrong artifact fails the check (`content_hash_verified is False`).  The
golden tests assert the chain resolves the exact CSV row/column the
`TCC = 1.015` value was read from.

---

## 8. Determinism

Mapping, units and versions are module constants.  `assertion_id` is a
pure function of (extraction batch, record identity, transformer
version, schema version); `transformation_id` / `transformation_hash`
are pure functions of the batch.  Replaying the golden artifact produces
byte-identical output (verified in §6 of the verify script).
