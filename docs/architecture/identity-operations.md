# Canonical Link, Merge and Split Operations (DP-04-04)

> Apply identity decisions **without destructive row merging** — every
> operation is transactional, conflict-checked, receipted and reversible,
> and history is never deleted.
>
> **Code of record:** `api/src/irc_data/matching/operations.py`
> (`SCHEMA_VERSION = "identity-operation-v1"`).
> **Builds on:** DP-03-01 (canonical entity vocabulary and the
> merge/split primitives on `DomainModel`), DP-04-01 (identity candidate /
> match-decision contracts), DP-04-03 (explainable match scoring),
> DP-04-02 (blocking — the `EntityObservation` link shape).
> **Verification:** `api/tests/matching/test_identity_operations.py`
> (transactional tests covering concurrent merge, chain merge, split and
> rollback) and the human-runnable evidence script
> `api/scripts/verify_dp_04_04.py`.

---

## 1. Purpose and goal

**Goal: apply identity decisions without destructive row merging.**

DP-04-02 finds candidate pairs; DP-04-03 scores them; DP-04-01 defines
the decision contract.  DP-04-04 is where those decisions *land*: the
`IdentityGraph` applies link / merge / split / reverse operations to the
DP-03-01 canonical registry inside a transactional envelope, so that:

* **No row is ever destroyed.**  A merge *aliases* the absorbed entity
  onto the survivor (`removed_at` / `merged_into` stamps); the absorbed
  entity keeps its id, its history and its audit trail.  A split moves
  the named memberships onto a fresh entity stamped `split_from`.  A
  reversal *re-links* memberships onto a fresh entity — ids are never
  resurrected or reused.
* **Concurrent decisions are conflict-checked.**  Optimistic concurrency:
  every decision quotes the entity versions it was made against; a stale
  decision raises `ConcurrentDecisionError`, mutates nothing, and is
  itself recorded on the immutable receipt log.
* **Everything is receipted.**  The handoff contract is
  `IdentityOperationReceiptV1`: one receipt per *attempted* operation,
  with `outcome = committed | conflict | failed`.  The log is the audit
  trail — nothing is silent.

## 2. The input contract: `IdentityDecisionInput`

The minimal DP-04-01 `IdentityDecisionV1` surface this layer needs:

| field | meaning |
|---|---|
| `decision_id` | unique id of the decision (applied at most once) |
| `actor` | who/what decided — required, decisions are attributable |
| `decided_at` | system time of the decision (the commit timestamp) |
| `reason` | free-text rationale |
| `evidence_refs` | provenance — match-candidate ids, score ids, artifact URIs; no merge happens without stored evidence |
| `expected_versions` | entity id → the `EntityVersion.version` the decider observed (the optimistic-concurrency token) |

`IdentityLink` carries the source identity being linked: aliases
(`sail_number` / `registry_id` / `boat_name` …) plus assertions.
`IdentityLink.from_observation()` adapts a DP-04-02 `EntityObservation`
directly.

## 3. The output contract: `IdentityOperationReceiptV1`

```python
IdentityOperationReceiptV1(
    operation_id="iop-000003",
    kind="merge",                      # link | merge | split | reverse_decision
    decision=IdentityDecisionInput(...),
    subjects=("boat_A", "boat_B"),     # every entity touched, sorted
    outcome="committed",               # committed | conflict | failed
    committed_at=datetime(...),        # set exactly when applied
    applied={"survivor": ..., "removed": ..., "moved_assertion_ids": [...],
             "moved_aliases": [...], "chain_rerooted": [...]},
    error=None,                        # populated on conflict / failed
    conflicts=("iop-000001", ...),     # winning receipts, on conflict
    supersedes=None,                   # decision id, on reversal
    reversed_by=None,                  # stamped on the original when reversed
    schema_version="identity-operation-v1",
)
```

The receipt log is **append-only**: conflicted and failed attempts are
recorded alongside committed ones, so the full decision history —
including the attempts that lost — is auditable.

## 4. The four operations

### 4.1 Link

`link(entity_id, identity, decision)` attaches a source identity to a
canonical entity: aliases attach under the DP-03-01 overlap rule (one
label names at most one live entity at a time — overlapping duplicates
raise `DuplicateAliasError`), and assertions are **re-keyed** onto the
entity (a new content-addressed id; the source record stays immutable).
Linking to a removed entity is refused with a pointer to the survivor.

### 4.2 Merge — aliasing, not destruction

`merge(survivor_id, removed_id, decision)` stamps the removed entity
`removed_at` / `merged_into`, re-points its assertions and moves its
aliases onto the survivor.  The pre-merge state remains reproducible via
`as_of` replay (DP-03-02 bitemporal resolution).

**Chain merges follow through.**  If the removed entity had itself
absorbed others (`C ← D`), merging `A ← C` re-roots the whole chain onto
`A` in the same transaction (`applied["chain_rerooted"]`), so removed
entities never point at removed entities.

### 4.3 Split — restores correct memberships

`split(entity_id, assertion_ids=[...], decision, alias_kinds=…,
alias_values=…)` mints a fresh entity `split_from` the original and moves
exactly the named assertions.  Aliases move by selector; with **no
selector** the default is *interval-correct*: aliases whose validity
overlaps the moved assertions' combined valid-time interval move with
them.  This is the §6.2 re-issued-sail-number case — the 2019 label
rides the 2019 assertions; the 2008 label stays.  Both sides keep their
full history; each resolves its own era's facts.

### 4.4 Decision reversal

`reverse_decision(decision_id, reversal)` undoes a committed decision
**without deleting history**:

* **merge reversal** — the absorbed entity is un-stamped (`removed_at` /
  `merged_into` cleared) and stands alone again; the memberships the
  merge had moved are re-linked onto a *fresh* entity (located exactly,
  by recomputing the pre-merge content-addressed ids);
* **link reversal** — the linked aliases/assertions move off onto a fresh
  entity;
* **split reversal** — the split-off entity merges back (non-destructively)
  into the original.

The original receipt is stamped `reversed_by`; the reversal receipt
carries `supersedes=<decision_id>`; the registry event log records a
`reverse_decision` entry.  A reversal is itself a decision — attributable,
evidenced, and reversible.

## 5. Transactional guarantees

Every mutating call runs **stage → conflict-check → apply → validate →
commit** under the graph's write lock:

1. **Conflict-check first** — the decision's `expected_versions` are
   compared against the live `EntityVersion` tokens *before* any
   mutation.  A mismatch raises `ConcurrentDecisionError` carrying the
   winning receipt(s); **no shared state is mutated**.
2. **Snapshot** — the mutable registry (entities, alias index, assertion
   store, versions, event-log length) is deep-copied.
3. **Apply + validate** — the staged plan runs.  Any failure (e.g. an
   alias overlap surfacing at commit time, as a DB check constraint
   would) triggers **rollback**: the snapshot is restored byte-for-byte
   and a `failed` receipt is logged.  There is no partial commit.
4. **Commit** — the receipt is appended and the touched entities'
   versions bump, which is what makes the *next* concurrent decision
   conflict-check correctly.

## 6. Downstream views are rebuilt, not maintained

`rebuild_views()` projects live registry state into two read models —
`alias_directory` (every alias → the live entity it currently names, via
the surviving root) and `entity_index` (lifecycle + membership counts).
Nothing in the views is stored as fact: after any merge, split or
reversal the downstream picture is rebuilt by re-running the projection,
and the pre-operation state remains reproducible via `as_of` replay.

## 7. Handoff summary

| Consumer | Reads | Guarantee |
|---|---|---|
| Adjudication UI / stewards | `IdentityOperationReceiptV1` log, `decision_receipt()` | every decision — committed, conflicted or failed — is receipted and attributable |
| Matching engine (DP-04-03) | `EntityVersion` tokens | decisions quote observed versions; stale decisions conflict instead of corrupting |
| Downstream projections (DP-03-05 views) | `rebuild_views()` | views are derived and rebuildable after any operation |
| Audit / QA | `receipts`, `DomainModel.event_log`, `as_of` replay | immutable history; nothing deleted; pre-operation state reproducible |

### Acceptance-criteria traceability

* *"Concurrent decisions are conflict-checked"* → §5 step 1 (optimistic
  `expected_versions` check; `ConcurrentDecisionError` with the winning
  receipts; conflicted attempts logged and side-effect-free).
* *"Split restores correct memberships"* → §4.3 (named assertions move;
  interval-correct alias default; both sides keep history; era-correct
  resolution).
* *"Downstream views can be rebuilt"* → §6 (`rebuild_views()` projects
  live state after every operation).
* *"Transactional tests cover concurrent merge, chain merge, split and
  rollback"* → `api/tests/matching/test_identity_operations.py`
  (`TestConcurrentMerge`, `TestChainMerge`, `TestSplit`, `TestRollback`)
  plus `TestReversal` and `TestRebuildViews`; evidence script
  `api/scripts/verify_dp_04_04.py` prints 32/32 PASS.
