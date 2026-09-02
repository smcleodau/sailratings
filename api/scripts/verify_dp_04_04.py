#!/usr/bin/env python3
"""End-to-end verification evidence for DP-04-04 — canonical link, merge
and split operations on the identity graph.

Walks the acceptance criteria through the shipped code and prints hard,
paste-able PASS/FAIL evidence for the issue board:

  1. **Link** — source identities attach to a canonical entity as aliases
     + re-keyed assertions (no destructive row writes); overlapping
     duplicate links are refused.
  2. **Concurrent merge** — two decisions made against the same snapshot:
     the first commits; the second is conflict-checked
     (``ConcurrentDecisionError``), mutates nothing, and is itself
     recorded on the immutable receipt log.
  3. **Chain merge** — ``C ← D`` then ``A ← C`` re-roots the whole chain
     onto A in one transaction; removed entities never point at removed
     entities.
  4. **Split** — the §6.2 re-issued sail number: the 2019 assertions and
     the 2019 alias move onto a fresh ``split_from`` entity; the 2008 era
     stays; each hull resolves its own TCC.
  5. **Rollback** — a commit-time failure restores the exact pre-operation
     state (registry, alias index, assertions, versions, event log) and
     records a ``failed`` receipt.
  6. **Decision reversal** — a merge is reversed without deleting history:
     the absorbed entity stands alone again, memberships re-link onto a
     fresh entity, and the original receipt is stamped ``reversed_by``.
  7. **Downstream views** — ``rebuild_views()`` projects live state and
     reflects every operation.

No database or network required — the identity graph is in-memory and
deterministic.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_04_04.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.assertions import AssertionV1  # noqa: E402
from irc_data.domain import Alias, DuplicateAliasError, EntityType  # noqa: E402
from irc_data.matching.operations import (  # noqa: E402
    OUTCOME_COMMITTED,
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    ConcurrentDecisionError,
    IdentityDecisionInput,
    IdentityGraph,
    IdentityLink,
    IdentityOperationError,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")


def decide(decision_id: str, *, at: datetime, reason: str = "reviewed",
           expected: dict[str, int] | None = None) -> IdentityDecisionInput:
    return IdentityDecisionInput(
        decision_id=decision_id,
        actor="data-steward",
        decided_at=at,
        reason=reason,
        evidence_refs=("match-candidate-1", "score-0.97"),
        expected_versions=expected or {},
    )


def tcc(entity_key: str, value: str, *, at: datetime, source: str = "irc-certs",
        valid_from: datetime = EPOCH, valid_to: datetime | None = None) -> AssertionV1:
    return AssertionV1(
        entity_type="boat", entity_key=entity_key, field="tcc", value=value,
        valid_from=valid_from, valid_to=valid_to, recorded_at=at, source_slug=source,
        provenance_uri=f"sha256:{value}-{at.isoformat()}",
    )


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    t = T0

    # ------------------------------------------------------------------ link
    banner("1. Link — source identities attach without destructive writes")
    g = IdentityGraph()
    boat = g.create_entity(EntityType.BOAT, at=t)
    receipt = g.link(
        boat.entity_id,
        IdentityLink(
            aliases=(Alias(kind="sail_number", value="GBR 8310", valid_from=EPOCH),),
            assertions=(tcc("src-row-7", "1.024", at=t),),
        ),
        decide("d-link-1", at=t),
    )
    check("link commits with a receipt", receipt.outcome == OUTCOME_COMMITTED,
          receipt.operation_id)
    ent = g.model.get(boat.entity_id)
    check("alias attached", [a.value for a in ent.aliases] == ["GBR 8310"])
    check("assertion re-keyed onto entity",
          len(ent.assertions) == 1
          and g.model._assertions[ent.assertions[0].assertion_id].entity_key == ent.entity_key)
    check("resolved truth reflects the link",
          g.model.resolve_truth(boat.entity_id, as_of=t + timedelta(hours=1)).value("tcc") == "1.024")

    other = g.create_entity(EntityType.BOAT, at=t)
    try:
        g.link(other.entity_id,
               IdentityLink(aliases=(Alias(kind="sail_number", value="GBR8310", valid_from=EPOCH),)),
               decide("d-link-dup", at=t))
        check("overlapping duplicate link refused", False, "no error raised")
    except IdentityOperationError as exc:
        check("overlapping duplicate link refused", "DuplicateAliasError" in str(exc))
    check("duplicate attempt left no aliases behind", g.model.get(other.entity_id).aliases == [])

    # ------------------------------------------------------- concurrent merge
    banner("2. Concurrent merge — conflict-checked, no partial commit")
    g = IdentityGraph()
    a = g.create_entity(EntityType.BOAT, at=t)
    b = g.create_entity(EntityType.BOAT, at=t)
    c = g.create_entity(EntityType.BOAT, at=t)
    stale = {a.entity_id: 0, b.entity_id: 0, c.entity_id: 0}
    first = g.merge(a.entity_id, b.entity_id, decide("d-A1", at=t, expected=stale))
    check("first merge commits", first.outcome == OUTCOME_COMMITTED)
    snapshot_before = g.model.snapshot()
    try:
        g.merge(b.entity_id, c.entity_id, decide("d-A2", at=t, expected=stale))
        check("concurrent merge rejected", False, "no error raised")
    except ConcurrentDecisionError as exc:
        check("concurrent merge rejected",
              [r.operation_id for r in exc.conflicts] == [first.operation_id],
              str(exc).split(";")[0])
    check("conflicted attempt mutated nothing", g.model.snapshot() == snapshot_before)
    check("conflict is on the immutable receipt log",
          any(r.outcome == OUTCOME_CONFLICT and r.decision.decision_id == "d-A2"
              for r in g.receipts))
    rebased = {eid: g.get_version(eid).version for eid in stale}
    retry = g.merge(a.entity_id, c.entity_id, decide("d-A3", at=t, expected=rebased))
    check("re-based retry commits", retry.outcome == OUTCOME_COMMITTED)

    # -------------------------------------------------------------- chain merge
    banner("3. Chain merge — C ← D then A ← C re-roots the whole chain")
    g = IdentityGraph()
    a = g.create_entity(EntityType.BOAT, at=t)
    cc = g.create_entity(EntityType.BOAT, at=t)
    d = g.create_entity(EntityType.BOAT, at=t)
    g.link(d.entity_id,
           IdentityLink(aliases=(Alias(kind="sail_number", value="D1", valid_from=EPOCH),)),
           decide("l1", at=t))
    g.merge(cc.entity_id, d.entity_id, decide("m1", at=t))
    r = g.merge(a.entity_id, cc.entity_id, decide("m2", at=t))
    check("whole chain was the subject",
          set(r.subjects) == {a.entity_id, cc.entity_id, d.entity_id})
    check("chain re-rooted onto survivor",
          r.applied["chain_rerooted"] == [d.entity_id]
          and g.model.get(d.entity_id).merged_into == a.entity_id
          and g.model.get(cc.entity_id).merged_into == a.entity_id)
    resolved = g.model.resolve_alias("sail_number", "D1", at=t + timedelta(hours=1))
    check("alias rides the chain to the live root",
          resolved is not None and resolved.entity_id == a.entity_id)

    # ------------------------------------------------------------------- split
    banner("4. Split — restores correct memberships (re-issued GBR 8310)")
    g = IdentityGraph()
    boat = g.create_entity(EntityType.BOAT, at=t)
    e2008 = datetime(2008, 1, 1, tzinfo=timezone.utc)
    e2010 = datetime(2010, 1, 1, tzinfo=timezone.utc)
    e2019 = datetime(2019, 1, 1, tzinfo=timezone.utc)
    a2008 = tcc(boat.entity_key, "1.001", at=t, valid_from=e2008, valid_to=e2010, source="sailsys")
    a2019 = tcc(boat.entity_key, "1.050", at=t, valid_from=e2019, source="irc-certs")
    g.model.assert_about(boat.entity_id, a2008)
    g.model.assert_about(boat.entity_id, a2019)
    g.link(boat.entity_id, IdentityLink(aliases=(
        Alias(kind="sail_number", value="GBR 8310", valid_from=e2008, valid_to=e2010),
        Alias(kind="sail_number", value="GBR 8310", valid_from=e2019),
    )), decide("l-era", at=t))
    split_receipt = g.split(boat.entity_id, assertion_ids=[a2019.assertion_id],
                            decision=decide("d-split", at=t, reason="two hulls"))
    new_id = split_receipt.applied["new_entity"]
    check("split commits", split_receipt.outcome == OUTCOME_COMMITTED)
    check("new entity stamped split_from",
          g.model.get(new_id).split_from == boat.entity_id)
    check("2019 assertions moved, 2008 stayed",
          len(g.model.get(new_id).assertions) == 1
          and len(g.model.get(boat.entity_id).assertions) == 1)
    check("2019 alias moved, 2008 alias stayed",
          [al.valid_from.year for al in g.model.get(new_id).aliases] == [2019]
          and [al.valid_from.year for al in g.model.get(boat.entity_id).aliases] == [2008])
    truth_new = g.model.resolve_truth(new_id, as_of=t + timedelta(hours=2),
                                      valid_as_of=datetime(2020, 1, 1, tzinfo=timezone.utc))
    truth_old = g.model.resolve_truth(boat.entity_id, as_of=t + timedelta(hours=2),
                                      valid_as_of=datetime(2009, 1, 1, tzinfo=timezone.utc))
    check("each hull resolves its own era's TCC",
          truth_new.value("tcc") == "1.050" and truth_old.value("tcc") == "1.001")

    # ---------------------------------------------------------------- rollback
    banner("5. Rollback — commit-time failure restores exact prior state")
    g = IdentityGraph()
    a = g.create_entity(EntityType.BOAT, at=t)
    b = g.create_entity(EntityType.BOAT, at=t)
    g.link(b.entity_id, IdentityLink(
        aliases=(Alias(kind="boat_name", value="Beta", valid_from=EPOCH),),
        assertions=(tcc("row-b", "1.011", at=t),),
    ), decide("l1", at=t))
    snapshot_before = g.model.snapshot()
    versions_before = {k: v.version for k, v in g._versions.items()}

    import irc_data.domain.entities as ent_mod
    original_merge = ent_mod.DomainModel.merge

    def exploding_merge(self, survivor_id, removed_id, *, at=None, reason=""):
        original_merge(self, survivor_id, removed_id, at=at, reason=reason)
        raise DuplicateAliasError("check constraint: overlapping sail_number")

    ent_mod.DomainModel.merge = exploding_merge
    try:
        g.merge(a.entity_id, b.entity_id, decide("m1", at=t))
        check("failing merge raises", False, "no error raised")
    except IdentityOperationError as exc:
        check("failing merge raises", "rolled back" in str(exc))
    finally:
        ent_mod.DomainModel.merge = original_merge
    check("registry state rolled back exactly",
          g.model.snapshot() == snapshot_before)
    check("entity versions rolled back",
          {k: v.version for k, v in g._versions.items()} == versions_before)
    check("absorbed entity still live",
          g.model.get(b.entity_id).is_live)
    check("failure recorded as a receipt",
          any(r.outcome == OUTCOME_FAILED and r.kind == "merge" for r in g.receipts))
    ok = g.merge(a.entity_id, b.entity_id, decide("m2", at=t))
    check("graph still works after rollback", ok.outcome == OUTCOME_COMMITTED)

    # ---------------------------------------------------------------- reversal
    banner("6. Decision reversal — history retained, memberships restored")
    g = IdentityGraph()
    a = g.create_entity(EntityType.BOAT, at=t)
    b = g.create_entity(EntityType.BOAT, at=t)
    g.link(b.entity_id, IdentityLink(
        aliases=(Alias(kind="boat_name", value="Beta", valid_from=EPOCH),),
        assertions=(tcc("row-b", "1.011", at=t),),
    ), decide("l1", at=t))
    merge_receipt = g.merge(a.entity_id, b.entity_id, decide("m1", at=t))
    rev = g.reverse_decision("m1", decide("r1", at=t + timedelta(hours=1),
                                          reason="not the same hull"))
    check("reversal commits", rev.outcome == OUTCOME_COMMITTED and rev.supersedes == "m1")
    check("absorbed entity stands alone again",
          g.model.get(b.entity_id).is_live and g.model.get(b.entity_id).merged_into is None)
    restored = g.model.get(rev.applied["relinked_entity"])
    check("memberships re-linked onto a fresh entity",
          [al.value for al in restored.aliases] == ["Beta"] and len(restored.assertions) == 1)
    check("original receipt superseded, not erased",
          g.receipt(merge_receipt.operation_id).reversed_by == rev.operation_id)
    check("registry event log records the reversal",
          any(e.event_type == "reverse_decision" for e in g.model.event_log))

    # ------------------------------------------------------------- downstream
    banner("7. Downstream views rebuild from live state")
    views = g.rebuild_views()
    index = {r["entity_id"]: r for r in views["entity_index"]}
    check("entity index reflects the reversal",
          index[b.entity_id]["live"] is True and index[a.entity_id]["live"] is True)
    directory = {(r["kind"], r["value"]): r for r in views["alias_directory"]}
    check("alias directory reflects the reversal",
          directory[("boat_name", "Beta")]["live_entity"] == restored.entity_id)

    # ------------------------------------------------------------------ summary
    banner("SUMMARY")
    failed = [label for label, ok, _ in RESULTS if not ok]
    print(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("  FAILING: " + "; ".join(failed))
        return 1
    print("  ALL CHECKS PASSED — DP-04-04 evidence complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
