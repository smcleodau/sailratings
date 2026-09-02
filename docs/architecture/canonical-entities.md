# Canonical Entity Boundaries and Identifiers (DP-03-01)

> The stable language of the sailing knowledge base: what the entities
> *are*, where one ends and the next begins, how they are identified, and
> how observed claims are kept separate from resolved truth.
>
> **Code of record:** `api/src/irc_data/domain/entities.py`
> (`SCHEMA_VERSION = "canonical-entity-v1"`).
> **Builds on:** DP-02-01 (immutable raw artifacts / provenance envelopes),
> DP-03-02 (source assertions & bitemporal resolution).
> **Verification:** `api/tests/domain/test_canonical_entities.py` and the
> human-runnable evidence script `api/scripts/verify_dp_03_01.py`, which
> walks the five messy real-world examples of §6 through the model.

---

## 1. Purpose and goal

**Goal: create stable language for the sailing knowledge base.**

Every data-platform component — scrapers, transformers, the assertion
store, the matching engine, the public API — must speak the same language
about *what a thing is*.  Before this document, "boat" could mean a row in
`boats`, a sail number on a results page, a certificate, or a marketing
name.  DP-03-01 fixes the vocabulary so the rest of the DP-03 series
(normalisation, transformation, migrations) has something to hang on.

Two invariants govern everything below:

1. **Observed assertions are separate from resolved truth.**  An entity
   record never *contains* fact values.  Facts arrive as immutable source
   assertions (DP-03-02) and the resolved truth is *derived* — per field,
   per system time — by the deterministic resolution rules.  Both layers
   carry full temporal history (valid time + system time), so the answer
   to "what did we believe about X on date D" is always reproducible.
2. **Identifiers are opaque and never derived from mutable names.**  A
   boat's id is `boat_01J4Z…` — a random ULID.  It is not, and cannot be,
   computed from the boat's name, sail number, certificate number or any
   other label a source might change, misspell, re-issue or reuse.
   Mutable labels are **aliases**: lookup aids with their own temporal
   validity, never keys.

---

## 2. Identifier scheme

### 2.1 Shape

An opaque canonical identifier reads:

```
<entity_type_prefix>_<26-char Crockford base-32 ULID>
boat_01J4Z9K7W2Q8E0R1T3Y5U7I9O0
cert_01J4ZA3M0X5N7B2V8C1D4F6G8H
```

* **Prefix** — one per canonical entity type (`boat`, `boat_identity`,
  `design`, `organisation`, `person`, `event`, `race`, `entry`, `result`,
  `certificate`, `rating`, `measurement`, `sail`, `venue`,
  `source_assertion`).  The prefix makes ids self-describing in logs and
  makes cross-type joins fail loudly instead of silently.
* **ULID body** — 48 bits of creation millisecond + 80 bits of
  randomness, Crockford base-32 (no `I`, `L`, `O`, `U`).  Sortable by
  creation time, collision-free for practical purposes, and *carrying no
  domain meaning whatsoever*.

### 2.2 Opacity is enforced, not hoped for

`new_entity_id()` takes **no name-like input** — it is structurally
impossible to mint an id from a boat name or sail number through the
public API.  `check_id_opacity()` additionally *audits* any id against a
list of mutable-name tokens (sail-number country prefixes like `GBR`,
rule-system labels like `IRC`, plus any supplied alias strings) and raises
`IdentifierDerivationError` if a name has been smuggled into the key.

This is the guard against the failure mode the acceptance criteria name:
an identifier derived from a mutable name silently breaks (or worse,
silently re-points) when the name changes.  With opaque ids, a rename is
an alias event; the id — and every assertion, join and cache key hanging
off it — does not move.

### 2.3 Joining to the assertion store

`AssertionV1.entity_type` / `entity_key` (DP-03-02) are the two *parts*
of the opaque id: `boat` + `01J4Z…` ≡ `boat_01J4Z…`.  The bitemporal
assertion store therefore references canonical entities with **no schema
change**, and the compatibility views (DP-03-05) keep legacy integer keys
readable while canonical ids become the join currency.

---

## 3. The two layers: assertions vs resolved truth

```
┌──────────────────────────────────────────────────────────────────┐
│ RESOLVED TRUTH  (derived, reproducible, bitemporal)              │
│   ResolvedTruth(entity_id, as_of, fields{field → winner},        │
│                 conflicts{field → (coexisting losers)})          │
│   — recomputed on demand; nothing here is stored as fact         │
├──────────────────────────────────────────────────────────────────┤
│ CANONICAL ENTITIES  (shells: opaque id + history, no values)     │
│   CanonicalEntity(entity_id, type, created_at, removed_at,       │
│                   merged_into, split_from, aliases[],            │
│                   assertion_refs[])                              │
├──────────────────────────────────────────────────────────────────┤
│ OBSERVED ASSERTIONS  (immutable, append-only, provenance)        │
│   AssertionV1(entity_type, entity_key, field, value, unit,       │
│               valid_from/to, recorded_at, source_slug,           │
│               provenance_uri, confidence, supersession, status)  │
├──────────────────────────────────────────────────────────────────┤
│ RAW ARTIFACTS  (DP-02-01 content-addressed provenance envelopes) │
└──────────────────────────────────────────────────────────────────┘
```

* **Entities own nothing.**  A `CanonicalEntity` carries its id, its
  lifecycle (created / merged / split), its aliases and *references* to
  the assertions about it.  It is impossible to "edit a boat's TCC" —
  there is no field for it.  There is only "a source asserted a TCC for
  this boat, valid over this interval" and the derived winner.
* **Resolution is bitemporal.**  `DomainModel.resolve_truth(entity_id,
  as_of=T)` filters assertions by `recorded_at <= T`, applies valid-time
  filtering, supersession and retraction exactly as DP-03-02 specifies,
  and reports both the per-field winners and the losing *conflicts* so
  disagreement between sources stays observable rather than silent.
* **Temporal history is structural, not bolted on.**  Entity-level
  changes (create, merge, split, alias attach) are entries in an
  append-only `RegistryEvent` log with system timestamps; fact-level
  history is the assertion store's supersession/retraction graph.

---

## 4. Canonical entity types and boundaries

Fifteen types.  For each: what it *is*, what is inside the boundary, what
is deliberately outside (asserted about it or a different entity), and
the mutable labels sources use to recognise it (aliases, never ids).
The machine-readable form is `ENTITY_BOUNDARIES` in
`irc_data/domain/entities.py`.

| Type | Definition (boundary) | Recognised by (aliases, never keys) | References |
|---|---|---|---|
| **boat** | The physical vessel — hull and appendages — independent of what any source calls it. Contains: opaque id, merge/split history, identity links. Excludes: name, sail number, ratings, measurements. | boat name, sail number, HIN | boat_identity, design |
| **boat_identity** | One time-bounded naming/registration state of a boat (name + sail number + flag + owner as observed). | boat name, sail number, flag | boat |
| **design** | The design/model a hull is built to ("Sydney 38", "J/122") — a class concept. Contains: opaque id, designer/builder links. Excludes: individual hulls, per-boat measurements. | class name, model name, designer name | person, organisation |
| **organisation** | A club, rating office, class association, builder or event organiser. | organisation name, acronym, country | venue |
| **person** | An individual sailor/owner/designer/measurer. Only data already published in race administration is ever attached (SOURCE-POLICY §4.8). | published name | — |
| **event** | A regatta or race meeting: organiser + venue + date window ("Sydney Hobart 2019"). Excludes: the races within it, entries. | event name, edition/year | organisation, venue |
| **race** | A single start within an event: one course, one start time, one set of observations. | race number/name within event | event |
| **entry** | The registration of one boat (under one boat identity) in one event/division. The boat↔event join object. | sail number + event (alias pair) | boat, boat_identity, event |
| **result** | One scored finishing observation for one entry in one race: place, times, status. Excludes: series scores (derived). | place + race + sail number (observation) | entry, race |
| **certificate** | A rating-office *document* (IRC cert PDF) issued to one boat at one time. Contains: boat + office references. Excludes: the measurements and rating printed on it — those are measurement/rating entities asserted *from* the certificate. | certificate number, issue date + sail number | boat, organisation |
| **rating** | A rule-system score (IRC TCC, ORC APH) valid for an interval. Asserted about a boat, never owned by it. | sail number + rule system + year | boat, certificate |
| **measurement** | One measured dimension of a boat with unit and valid interval. Excludes: the rating derived from it. | field name + sail number | boat, certificate |
| **sail** | An individual sail / measured inventory item of a boat. Excludes: its dimensions (measurements). | sail number + sail kind | boat |
| **venue** | A place racing happens: body of water / race area tied to club or region. | venue name, region/country | — |
| **source_assertion** | One immutable observed claim from a governed source: who said what, about which entity, when the truth changed. The atom of the knowledge base. | provenance URI, raw artifact hash | subject entity (any type) |

### Boundary rules of thumb (for reviewers and scraper authors)

1. **If it can change while the thing stays the same thing, it is not the
   id and probably not the entity** — it's an alias (name, sail number)
   or an assertion (rating, measurement).
2. **Documents and what documents say are different entities.**  A
   certificate is a document; its TCC is a rating; its hull length is a
   measurement.  Killing the certificate (retraction) must not erase the
   historical fact that it *was issued*.
3. **A sport event and its administrative records differ.**  The event
   ("Sydney Hobart 2019") survives corrections to any single result; an
   entry survives a crew-list typo; a result is just one observation.
4. **People carry the minimum the sport already publishes.**  Contact
   details are out of boundary by policy, not by schema convenience.

---

## 5. Entity lifecycle: create, merge, split

* **Create** — `DomainModel.create_entity(type)` mints an opaque id and
  logs a `create` event.  The id embeds only creation time + randomness.
* **Merge** — when two canonical entities turn out to be one real-world
  thing (the classic: IRC cert says "AKELA", a results page says
  "AKELA OF COWES", same sail number), `merge(survivor, removed)` stamps
  the loser `removed_at`/`merged_into`, re-points its assertions and
  aliases to the survivor, and logs a `merge` event.  The loser is never
  deleted: the pre-merge view is reconstructable for any system time
  before the merge, and the merge itself is auditable.  Cross-type merges
  are a contract error.
* **Split** — when one entity was really two things (two hulls sharing a
  re-issued sail number), `split(entity, assertion_ids=[...])` creates a
  fresh entity stamped `split_from` and moves the named assertions onto
  it.  Both sides keep full history.
* **Aliases move; ids don't.**  `attach_alias` records
  kind/value/validity/source; `resolve_alias` answers "which *live*
  entity did this label name at time T".  Overlapping duplicate aliases
  on different live entities are rejected — a sail number may be
  re-issued, but it names exactly one boat at a time.

---

## 6. Domain review — five messy real-world examples walked through

This section is the **verification criterion**: the model is exercised
against five cases that have actually broken simpler models in this
codebase's history.  Each walkthrough names the entities, the assertions,
and what the resolved truth says at two different system times.  All five
are executable: `tests/domain/test_canonical_entities.py` and
`scripts/verify_dp_03_01.py` run them and assert the stated outcomes.

### 6.1 The renamed boat — "Wild Thing" → "Wild Oats XI" → back

*Mess:* a maxi is campaigned under a sponsor name for a season, then
renamed back.  Results pages, certificates and news each know it by
whichever name was current, and short sail numbers recur across eras.

*Walkthrough:*

1. One **boat** `boat_B` is created.  The name never touches the id.
2. Two **boat_identity** shells attach to `boat_B`: *Wild Thing*
   (valid 2002–2004) and *Wild Oats XI* (valid 2004–), each sourced from
   certificates/results of their era.
3. Aliases: `boat_name=Wild Thing` valid_to 2004; `boat_name=Wild Oats
   XI` from 2004.  Resolving the alias as of 2003 yields `boat_B` via the
   first; as of 2005 via the second — **same id both times**.
4. Assertions about `rating`/`measurement` carry their own valid
   intervals, so a 2003 certificate's TCC never pollutes the 2005 view
   unless still valid.

*Outcome asserted in tests:* the boat id is stable across the rename;
alias resolution is time-correct; no assertion is edited.

### 6.2 The re-issued sail number — `GBR 8310` on two hulls

*Mess:* sail numbers are re-issued.  A 2008 results page and a 2019
certificate both say `GBR 8310`, but they are different physical boats.

*Walkthrough:*

1. `boat_B1` created from the 2008 results observation (alias
   `sail_number=GBR8310` valid 2008–2010).
2. `boat_B2` created from the 2019 certificate (same alias value, valid
   2019–).  Because the alias intervals don't overlap, both bindings
   coexist; `resolve_alias("sail_number", "GBR 8310", at=…)` picks the
   right hull per era.
3. If ingestion *first* mistook them for one boat (assertions of both
   eras piled on `boat_B1`), `split(boat_B1, assertion_ids=[2019 cert
   assertions])` creates `boat_B2` with `split_from=boat_B1` and moves
   exactly those assertions.  The 2008 assertions stay put.

*Outcome asserted in tests:* time-scoped alias resolution returns
different boats for 2009 vs 2020; after split, each boat's resolved truth
contains only its own era's TCC; both keep history.

### 6.3 The double-entered design — "Sydney 38" vs "Sydney 38 OD"

*Mess:* sources spell the class two ways; a naive pipeline mints two
design rows and the fleet fragments across them.

*Walkthrough:*

1. Two **design** entities exist (`design_D1`, `design_D2`) with aliases
   `Sydney 38` and `Sydney 38 OD` respectively; boats assert `design`
   against both ids via different sources — the conflict is visible as
   coexisting assertions.
2. Review decides they are one class: `merge(design_D1, design_D2)`.
   Assertions re-point to `design_D1`; the alias `Sydney 38 OD` moves to
   the survivor; `design_D2` keeps `merged_into` for audit.
3. Resolving any boat's `design` field now yields one winner instead of
   a conflict; the pre-merge state remains reproducible by resolving with
   `as_of` before the merge.

*Outcome asserted in tests:* post-merge resolution is conflict-free on
the survivor; the removed design is preserved with provenance; resolving
*before* the merge timestamp still shows the old two-design state.

### 6.4 The corrected race result — DNF → place after redress

*Mess:* a results page initially publishes a boat as DNF; days later a
jury decision awards 3rd place and the page is updated.  A model that
overwrites loses the fact that the DNF was ever published (handicappers
and journalists *cite* it).

*Walkthrough:*

1. Entities: **event** `event_E` (Sydney Hobart 2019), **race** `race_R`
   (the single ocean race), **entry** `entry_N` (boat B in E, IRC Div 1),
   **result** `result_RS` (the scored observation for N in R).
2. Assertion A1 (source `sailsys`, recorded T1): `result_RS.status =
   "DNF"`.  Resolved truth as of T1+ε: DNF.
3. Assertion A2 (recorded T2 > T1, `supersedes=A1.id`): `status =
   "3"` (place).  Resolved truth now: 3rd; A1 remains in the store,
   visible as superseded history; resolving with `as_of` between T1 and
   T2 still yields DNF.

*Outcome asserted in tests:* both system-time views reproduce; the
supersession chain is intact; entry/event/race entities are untouched by
the correction (boundaries hold).

### 6.5 The venue that moved — "Hamilton Island Race Week" raced from a marina rebuilt under a new name

*Mess:* an event keeps its name across years while the host venue is
renamed/rebranded; older results cite the old venue name, newer ones the
new, and a club re-organisation changes the organising authority string
mid-history.

*Walkthrough:*

1. **venue** `venue_V` created once.  Aliases `venue_name=Hamilton
   Island` (…–2016) and `venue_name=Hamilton Island Marina` (2016–) both
   bind to `venue_V` over disjoint intervals — the *place* is one entity,
   the *label* history is aliases.
2. **organisation** `org_O1` (the original YC) and `org_O2` (its
   successor corporation) are distinct entities; annual **event** shells
   reference whichever organisation was the authority that year, all
   pointing at `venue_V`.
3. A query "all editions at this venue" resolves through the *venue id*,
   immune to both the venue rename and the organiser change; a query
   "editions organised by X" resolves through the organisation id
   honestly, showing the handover year.

*Outcome asserted in tests:* venue id stable across rename; events
reference the venue id (not the name string); organiser history is two
clean entities, not a mutated string.

---

## 7. Contract summary (handoff)

| Consumer | Reads | Guarantee |
|---|---|---|
| Transformers (DP-03-04) | `EntityType`, `new_entity_id`, `Alias` | Ids are opaque; names attach as aliases only |
| Assertion store (DP-03-02) | `entity_type`/`entity_key` parts of the opaque id | No schema change; resolution stays reproducible |
| Matching engine | `resolve_alias(kind, value, at=T)` | Time-correct identity lookup; overlaps rejected |
| Migrations / views (DP-03-05) | `DomainModel.snapshot()`, `RegistryEvent` log | Append-only history; merges/splits auditable |
| Public API | `ResolvedTruth` per entity | Facts always carry provenance + as-of semantics |

**Acceptance criteria traceability**

* *"Entities separate observed assertions from resolved truth and support
  temporal history"* → §3 layering; `CanonicalEntity` has no fact fields;
  `resolve_truth(as_of=…)` + supersession/retraction + the registry event
  log give bitemporal history at both fact and entity level.
* *"Identifiers are opaque and never derived from mutable names"* → §2;
  `new_entity_id` accepts no name input; `check_id_opacity` audits;
  mutable labels exist only as `Alias` rows with validity intervals.
* *"Domain review walks at least five messy real-world examples through
  the model"* → §6, executed by `tests/domain/test_canonical_entities.py`
  and `scripts/verify_dp_03_01.py`.
