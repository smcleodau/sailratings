"""Transactional tests for DP-04-04 — canonical link, merge and split.

The issue's verification criterion:

    "Transactional tests cover concurrent merge, chain merge, split and
    rollback."

Covered here:

* **link** — source identities attach as aliases + re-keyed assertions;
  overlapping duplicate links are refused; links to removed entities are
  refused and routed to the survivor.
* **concurrent merge** — optimistic concurrency: a decision made against
  stale entity versions raises :class:`ConcurrentDecisionError`, mutates
  nothing, and is itself recorded on the immutable receipt log.
* **chain merge** — A ← B then A ← C re-roots cleanly; A ← C where C had
  already absorbed D (C ← D) follows the chain so removed entities never
  point at removed entities; merging into a removed entity is refused.
* **split** — named assertions and selected aliases move onto a fresh
  ``split_from`` entity; everything else stays; both sides keep history;
  resolved truth per era is correct (the §6.2 re-issued sail number).
* **rollback** — a commit-time failure (overlapping alias surfaced inside
  a merge) restores the exact pre-operation state: entities, alias index,
  assertions, versions and event log all roll back; the failure is
  recorded as a receipt.
* **decision reversal** — merge reversal un-stamps the absorbed entity
  and re-links exactly the moved memberships onto a fresh entity; link
  reversal unbinds the identity; split reversal re-merges the split-off
  entity.  History is never deleted — the original receipt is stamped
  ``reversed_by`` and both receipts cross-reference.
* **rebuild** — downstream views are projections of live state and
  reflect every operation once rebuilt.

All timestamps are fixed; tests are deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from irc_data.assertions import AssertionV1
from irc_data.domain import (
    Alias,
    DomainError,
    DuplicateAliasError,
    EntityType,
    MergeSameEntityError,
    SplitError,
)
from irc_data.matching.blocking import EntityObservation
from irc_data.matching.operations import (
    OP_LINK,
    OP_MERGE,
    OP_REVERSE,
    OP_SPLIT,
    OUTCOME_COMMITTED,
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    ConcurrentDecisionError,
    DecisionNotFoundError,
    IdentityDecisionInput,
    IdentityGraph,
    IdentityLink,
    IdentityOperationError,
    IdentityOperationReceiptV1,
    ReversalError,
    SCHEMA_VERSION,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)
T4 = T0 + timedelta(hours=4)
T5 = T0 + timedelta(hours=5)

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def decide(
    decision_id: str,
    *,
    at: datetime = T1,
    actor: str = "data-steward",
    reason: str = "reviewed",
    evidence: tuple[str, ...] = ("match-candidate-1", "score-9"),
    expected: dict[str, int] | None = None,
) -> IdentityDecisionInput:
    return IdentityDecisionInput(
        decision_id=decision_id,
        actor=actor,
        decided_at=at,
        reason=reason,
        evidence_refs=evidence,
        expected_versions=expected or {},
    )


def tcc(entity_key: str, value: str, *, at: datetime, source: str = "irc-certs") -> AssertionV1:
    return AssertionV1(
        entity_type="boat",
        entity_key=entity_key,
        field="tcc",
        value=value,
        valid_from=EPOCH,
        recorded_at=at,
        source_slug=source,
        provenance_uri=f"sha256:{value}-{at.isoformat()}",
    )


def link_aliases(*pairs: tuple[str, str], valid_from: datetime = EPOCH) -> IdentityLink:
    return IdentityLink(
        aliases=tuple(
            Alias(kind=kind, value=value, valid_from=valid_from, source_slug="test")
            for kind, value in pairs
        )
    )


@pytest.fixture
def graph() -> IdentityGraph:
    return IdentityGraph()


@pytest.fixture
def two_boats(graph: IdentityGraph):
    a = graph.create_entity(EntityType.BOAT, at=T0)
    b = graph.create_entity(EntityType.BOAT, at=T0)
    return a, b


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


class TestLink:
    def test_link_attaches_aliases_and_rekeys_assertions(self, graph, two_boats):
        a, _ = two_boats
        identity = IdentityLink(
            aliases=(Alias(kind="sail_number", value="GBR 8310", valid_from=EPOCH),),
            assertions=(tcc("src-row-7", "1.024", at=T0),),
        )
        receipt = graph.link(a.entity_id, identity, decide("d-link-1"))

        assert receipt.outcome == OUTCOME_COMMITTED
        assert receipt.kind == OP_LINK
        assert receipt.schema_version == SCHEMA_VERSION
        assert receipt.decision.evidence_refs == ("match-candidate-1", "score-9")

        entity = graph.model.get(a.entity_id)
        assert [al.value for al in entity.aliases] == ["GBR 8310"]
        # The assertion was re-keyed onto the entity (join by reference).
        assert len(entity.assertions) == 1
        ref = entity.assertions[0]
        stored = graph.model._assertions[ref.assertion_id]
        assert stored.entity_key == entity.entity_key
        assert stored.value == "1.024"
        # Resolved truth reflects the link.
        truth = graph.model.resolve_truth(a.entity_id, as_of=T2)
        assert truth.value("tcc") == "1.024"

    def test_link_from_observation(self, graph, two_boats):
        a, _ = two_boats
        obs = EntityObservation(
            observation_id="obs-1",
            sail_number="GBR 8310",
            name="Wild Oats XI",
            registry_id="ABC123",
        )
        receipt = graph.link(
            a.entity_id, IdentityLink.from_observation(obs, source_slug="sailsys"),
            decide("d-link-obs"),
        )
        kinds = {a_["kind"] for a_ in receipt.applied["aliases"]}
        assert kinds == {"sail_number", "registry_id", "boat_name"}

    def test_link_requires_actor(self):
        with pytest.raises(IdentityOperationError):
            decide("d-noactor", actor=" ")

    def test_overlapping_alias_link_refused_without_side_effects(self, graph, two_boats):
        a, b = two_boats
        graph.link(a.entity_id, link_aliases(("sail_number", "GBR 8310")), decide("d1"))
        with pytest.raises(IdentityOperationError, match="rolled back"):
            graph.link(b.entity_id, link_aliases(("sail_number", "GBR8310")), decide("d2"))
        # Nothing partial committed: b has no aliases; failure is logged.
        assert graph.model.get(b.entity_id).aliases == []
        failed = [r for r in graph.receipts if r.outcome == OUTCOME_FAILED]
        assert len(failed) == 1 and failed[0].kind == OP_LINK
        assert "DuplicateAliasError" in failed[0].error

    def test_link_to_removed_entity_refused(self, graph, two_boats):
        a, b = two_boats
        graph.merge(a.entity_id, b.entity_id, decide("d-merge"))
        with pytest.raises(IdentityOperationError, match="survivor"):
            graph.link(b.entity_id, link_aliases(("boat_name", "X")), decide("d-link-dead"))

    def test_link_conflict_checked(self, graph, two_boats):
        a, _ = two_boats
        graph.link(a.entity_id, link_aliases(("boat_name", "A")), decide("d1"))
        with pytest.raises(ConcurrentDecisionError):
            graph.link(
                a.entity_id,
                link_aliases(("boat_name", "B")),
                decide("d2", expected={a.entity_id: 0}),  # stale: actual is v1
            )
        assert graph.model.get(a.entity_id).aliases[0].value == "A"


# ---------------------------------------------------------------------------
# Concurrent merge — the acceptance criterion
# ---------------------------------------------------------------------------


class TestConcurrentMerge:
    def test_concurrent_merge_is_conflict_checked(self, graph):
        """Two stewards decide overlapping merges against the same state.

        Both decisions quote ``expected_versions`` from the state they
        reviewed.  The first commits and bumps the versions; the second is
        rejected with :class:`ConcurrentDecisionError`, mutates nothing,
        and leaves a ``conflict`` receipt naming the winning operation.
        """
        a = graph.create_entity(EntityType.BOAT, at=T0)
        b = graph.create_entity(EntityType.BOAT, at=T0)
        c = graph.create_entity(EntityType.BOAT, at=T0)
        # Both stewards review the same snapshot: everything at v0.
        stale = {a.entity_id: 0, b.entity_id: 0, c.entity_id: 0}

        first = graph.merge(a.entity_id, b.entity_id, decide("d-A1", expected=stale))
        assert first.outcome == OUTCOME_COMMITTED

        # The concurrent decision: merge c into b — but b moved under it.
        # (Stale versions are detected before the removed-survivor check:
        # the conflict, not the liveness error, is what the steward sees.)
        with pytest.raises(ConcurrentDecisionError) as excinfo:
            graph.merge(b.entity_id, c.entity_id, decide("d-A2", expected=stale))
        err = excinfo.value
        assert [r.operation_id for r in err.conflicts] == [first.operation_id]

        # Nothing from the conflicted decision was applied.
        assert graph.model.get(c.entity_id).is_live
        assert graph.model.get(c.entity_id).merged_into is None
        # …but the attempt is on the immutable log.
        conflict = [r for r in graph.receipts if r.outcome == OUTCOME_CONFLICT]
        assert len(conflict) == 1
        assert conflict[0].kind == OP_MERGE
        assert conflict[0].decision.decision_id == "d-A2"
        assert conflict[0].conflicts == (first.operation_id,)

        # Re-based onto current state, the retry succeeds.
        rebased = {
            a.entity_id: graph.get_version(a.entity_id).version,
            b.entity_id: graph.get_version(b.entity_id).version,
            c.entity_id: graph.get_version(c.entity_id).version,
        }
        retry = graph.merge(a.entity_id, c.entity_id, decide("d-A3", expected=rebased))
        assert retry.outcome == OUTCOME_COMMITTED
        assert graph.model.get(c.entity_id).merged_into == a.entity_id

    def test_merge_into_removed_entity_refused(self, graph):
        a = graph.create_entity(EntityType.BOAT, at=T0)
        b = graph.create_entity(EntityType.BOAT, at=T0)
        c = graph.create_entity(EntityType.BOAT, at=T0)
        graph.merge(a.entity_id, b.entity_id, decide("d1"))
        with pytest.raises(IdentityOperationError, match="chain root"):
            graph.merge(b.entity_id, c.entity_id, decide("d2"))

    def test_merge_same_entity_refused(self, graph, two_boats):
        a, _ = two_boats
        with pytest.raises(MergeSameEntityError):
            graph.merge(a.entity_id, a.entity_id, decide("d-self"))

    def test_merge_across_types_refused(self, graph):
        boat = graph.create_entity(EntityType.BOAT, at=T0)
        design = graph.create_entity(EntityType.DESIGN, at=T0)
        with pytest.raises(DomainError, match="cannot merge"):
            graph.merge(boat.entity_id, design.entity_id, decide("d-x"))

    def test_decision_applied_at_most_once(self, graph, two_boats):
        a, b = two_boats
        d = decide("d-once")
        graph.merge(a.entity_id, b.entity_id, d)
        with pytest.raises(IdentityOperationError, match="already applied"):
            graph.merge(a.entity_id, b.entity_id, d)


# ---------------------------------------------------------------------------
# Chain merge
# ---------------------------------------------------------------------------


class TestChainMerge:
    def test_linear_chain_reroots(self, graph):
        """A ← B then A ← C: both removed entities point at the root A."""
        a = graph.create_entity(EntityType.BOAT, at=T0)
        b = graph.create_entity(EntityType.BOAT, at=T0)
        c = graph.create_entity(EntityType.BOAT, at=T0)
        graph.merge(a.entity_id, b.entity_id, decide("d1", at=T1))
        graph.merge(a.entity_id, c.entity_id, decide("d2", at=T2))

        assert graph.model.get(b.entity_id).merged_into == a.entity_id
        assert graph.model.get(c.entity_id).merged_into == a.entity_id
        assert graph.model.get(a.entity_id).is_live

    def test_merge_follows_through_existing_chain(self, graph):
        """C ← D, then A ← C: D is re-rooted onto A in the same transaction."""
        a = graph.create_entity(EntityType.BOAT, at=T0)
        c = graph.create_entity(EntityType.BOAT, at=T0)
        d = graph.create_entity(EntityType.BOAT, at=T0)
        graph.link(d.entity_id, link_aliases(("sail_number", "D1")), decide("l1", at=T1))
        graph.merge(c.entity_id, d.entity_id, decide("m1", at=T2))
        assert graph.model.get(d.entity_id).merged_into == c.entity_id

        receipt = graph.merge(a.entity_id, c.entity_id, decide("m2", at=T3))
        assert receipt.outcome == OUTCOME_COMMITTED
        # The whole chain was the subject of the second merge.
        assert set(receipt.subjects) == {a.entity_id, c.entity_id, d.entity_id}
        assert receipt.applied["chain_rerooted"] == [d.entity_id]
        # D never points at a removed entity.
        assert graph.model.get(d.entity_id).merged_into == a.entity_id
        assert graph.model.get(c.entity_id).merged_into == a.entity_id
        # D's alias rides the chain onto the root.
        assert [al.value for al in graph.model.get(a.entity_id).aliases] == ["D1"]
        # Alias resolution follows to the live root.
        resolved = graph.model.resolve_alias("sail_number", "D1", at=T4)
        assert resolved is not None and resolved.entity_id == a.entity_id

    def test_pre_merge_state_reproducible(self, graph):
        """History is append-only: resolve as of before the merge."""
        a = graph.create_entity(EntityType.DESIGN, at=T0)
        b = graph.create_entity(EntityType.DESIGN, at=T0)
        # "Sydney 38" recorded first, the "Sydney 38 OD" spelling later —
        # so pre-merge views of the survivor see only their own spelling.
        for ent, val, rec in ((a, "Sydney 38", T1), (b, "Sydney 38 OD", T2)):
            ref_assertion = AssertionV1(
                entity_type="design",
                entity_key=ent.entity_key,
                field="class_name",
                value=val,
                valid_from=EPOCH,
                recorded_at=rec,
                source_slug="orc",
            )
            graph.model.assert_about(ent.entity_id, ref_assertion)
        graph.merge(a.entity_id, b.entity_id, decide("d-design", at=T3))

        # After the merge both spellings resolve on the survivor: the
        # later-recorded "Sydney 38 OD" wins the rank, the other spelling
        # is retained as an *observable* conflict — nothing is deleted.
        after = graph.model.resolve_truth(a.entity_id, as_of=T4)
        assert after.value("class_name") == "Sydney 38 OD"
        assert "Sydney 38" in {
            c.value for c in after.conflicts.get("class_name", ())
        }
        # The pre-merge state reproduces via as_of replay: resolving the
        # survivor as of before the merge only sees its own spelling.
        before = graph.model.resolve_truth(a.entity_id, as_of=T1)
        assert before.value("class_name") == "Sydney 38"
        assert "class_name" not in before.conflicts
        # The removed design is preserved for audit.
        assert graph.model.get(b.entity_id).merged_into == a.entity_id


# ---------------------------------------------------------------------------
# Split — restores correct memberships
# ---------------------------------------------------------------------------


class TestSplit:
    def _two_era_entity(self, graph: IdentityGraph):
        """One boat polluted with two eras of a re-issued sail number (§6.2)."""
        boat = graph.create_entity(EntityType.BOAT, at=T0)
        era_2008 = datetime(2008, 1, 1, tzinfo=timezone.utc)
        era_2019 = datetime(2019, 1, 1, tzinfo=timezone.utc)
        end_2010 = datetime(2010, 1, 1, tzinfo=timezone.utc)
        a1 = AssertionV1(
            entity_type="boat", entity_key=boat.entity_key, field="tcc",
            value="1.001", valid_from=era_2008, valid_to=end_2010,
            recorded_at=T1, source_slug="sailsys",
        )
        a2 = AssertionV1(
            entity_type="boat", entity_key=boat.entity_key, field="tcc",
            value="1.050", valid_from=era_2019, recorded_at=T2, source_slug="irc-certs",
        )
        graph.model.assert_about(boat.entity_id, a1)
        graph.model.assert_about(boat.entity_id, a2)
        graph.link(
            boat.entity_id,
            IdentityLink(aliases=(
                Alias(kind="sail_number", value="GBR 8310", valid_from=era_2008,
                      valid_to=datetime(2010, 1, 1, tzinfo=timezone.utc)),
                Alias(kind="sail_number", value="GBR 8310", valid_from=era_2019),
            )),
            decide("d-link", at=T2),
        )
        return boat, a1, a2

    def test_split_restores_memberships(self, graph):
        boat, a2008, a2019 = self._two_era_entity(graph)
        receipt = graph.split(
            boat.entity_id,
            assertion_ids=[a2019.assertion_id],
            decision=decide("d-split", at=T3, reason="two hulls share GBR 8310"),
            # No selector: the interval-correct default moves the 2019
            # alias with the 2019 assertions and leaves 2008 behind.
        )
        assert receipt.outcome == OUTCOME_COMMITTED
        new_id = receipt.applied["new_entity"]
        new_entity = graph.model.get(new_id)
        original = graph.model.get(boat.entity_id)

        # Memberships restored: each hull holds exactly its own era.
        assert new_entity.split_from == boat.entity_id
        assert {r.field for r in new_entity.assertions} == {"tcc"}
        assert len(original.assertions) == 1
        # Each hull resolves its own era's TCC (valid_as_of picks the era;
        # the 2008 assertion is out-of-validity for the 2019 hull now).
        truth_new = graph.model.resolve_truth(
            new_id, as_of=T4, valid_as_of=datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        truth_old = graph.model.resolve_truth(
            boat.entity_id, as_of=T4, valid_as_of=datetime(2009, 1, 1, tzinfo=timezone.utc)
        )
        assert truth_new.value("tcc") == "1.050"
        assert truth_old.value("tcc") == "1.001"

        # The 2019 alias moved with the 2019 assertions; the 2008 alias stayed.
        moved = receipt.applied["moved_aliases"]
        assert len(moved) == 1
        assert moved[0]["valid_from"].startswith("2019")
        assert [al.valid_from.year for al in original.aliases] == [2008]

        # Alias resolution is era-correct again: each era's label binds to
        # the hull holding that era's assertions.  (Resolve at a query time
        # when both entities are live — the DP-03-01 resolver gates on
        # entity liveness at query time.)
        at = T5
        r2009 = graph.model.resolve_alias(
            "sail_number", "GBR 8310",
            at=datetime(2009, 6, 1, tzinfo=timezone.utc),
        )
        # 2009 predates both entities' creation: unresolved is correct.
        assert r2009 is None
        r_now = graph.model.resolve_alias("sail_number", "GBR 8310", at=at)
        assert r_now is not None and r_now.entity_id == new_id
        # The 2008 alias interval sits untouched on the original hull.
        old_aliases = graph.model.get(boat.entity_id).aliases
        assert [al.valid_from.year for al in old_aliases] == [2008]

    def test_split_by_alias_values(self, graph):
        boat = graph.create_entity(EntityType.BOAT, at=T0)
        graph.link(
            boat.entity_id,
            link_aliases(("boat_name", "Wild Thing"), ("sail_number", "AUS 1")),
            decide("d1", at=T1),
        )
        receipt = graph.split(
            boat.entity_id,
            assertion_ids=[],
            decision=decide("d2", at=T2),
            alias_values=["wild thing"],
        )
        new_entity = graph.model.get(receipt.applied["new_entity"])
        assert [a.value for a in new_entity.aliases] == ["Wild Thing"]
        assert [a.value for a in graph.model.get(boat.entity_id).aliases] == ["AUS 1"]

    def test_split_unknown_assertion_refused(self, graph, two_boats):
        a, _ = two_boats
        with pytest.raises(SplitError):
            graph.split(a.entity_id, assertion_ids=["nope"], decision=decide("d1"))

    def test_split_removed_entity_refused(self, graph, two_boats):
        a, b = two_boats
        graph.merge(a.entity_id, b.entity_id, decide("d1"))
        with pytest.raises(IdentityOperationError, match="removed"):
            graph.split(b.entity_id, assertion_ids=[], decision=decide("d2"))

    def test_split_is_conflict_checked(self, graph, two_boats):
        a, b = two_boats
        graph.link(a.entity_id, link_aliases(("boat_name", "A")), decide("d1"))
        with pytest.raises(ConcurrentDecisionError):
            graph.split(
                a.entity_id, assertion_ids=[],
                decision=decide("d2", expected={a.entity_id: 0}),
            )


# ---------------------------------------------------------------------------
# Rollback — transactional integrity
# ---------------------------------------------------------------------------


class TestRollback:
    def test_failed_merge_rolls_back_everything(self, graph):
        """A commit-time failure (alias overlap surfaced mid-merge) restores
        the exact pre-operation state: no partial assertion moves, no alias
        moves, no version bumps, no extra registry events."""
        a = graph.create_entity(EntityType.BOAT, at=T0)
        b = graph.create_entity(EntityType.BOAT, at=T0)
        graph.link(a.entity_id, link_aliases(("boat_name", "Alpha")), decide("l1", at=T1))
        graph.link(b.entity_id, IdentityLink(
            aliases=(Alias(kind="boat_name", value="Beta", valid_from=EPOCH),),
            assertions=(tcc("row-9", "1.011", at=T1),),
        ), decide("l2", at=T1))

        # Simulate the DB-check-constraint failure surfacing *after* the
        # registry mutation: force attach-time validation to blow up by
        # pre-seeding a conflicting alias on a third entity.
        c = graph.create_entity(EntityType.BOAT, at=T0)
        graph.link(c.entity_id, link_aliases(("sail_number", "X1")), decide("l3", at=T1))

        snapshot_before = graph.model.snapshot()
        versions_before = {k: v.version for k, v in graph._versions.items()}
        events_before = len(graph.model.event_log)

        # Craft a merge whose post-merge alias attach would violate the
        # overlap rule: monkeypatch the registry merge to fail mid-commit.
        import irc_data.domain.entities as ent_mod

        original_merge = ent_mod.DomainModel.merge

        def exploding_merge(self, survivor_id, removed_id, *, at=None, reason=""):
            original_merge(self, survivor_id, removed_id, at=at, reason=reason)
            raise DuplicateAliasError("check constraint: overlapping sail_number")

        ent_mod.DomainModel.merge = exploding_merge
        try:
            with pytest.raises(IdentityOperationError, match="merge rolled back"):
                graph.merge(a.entity_id, b.entity_id, decide("m1", at=T2))
        finally:
            ent_mod.DomainModel.merge = original_merge

        # Full rollback: registry state identical to before the attempt.
        assert graph.model.snapshot() == snapshot_before
        assert {k: v.version for k, v in graph._versions.items()} == versions_before
        assert len(graph.model.event_log) == events_before
        assert graph.model.get(b.entity_id).is_live
        assert graph.model.get(b.entity_id).merged_into is None
        assert [al.value for al in graph.model.get(b.entity_id).aliases] == ["Beta"]
        assert len(graph.model.get(b.entity_id).assertions) == 1

        # The failure is itself recorded — the log is never silent.
        failed = [r for r in graph.receipts if r.outcome == OUTCOME_FAILED]
        assert len(failed) == 1
        assert failed[0].kind == OP_MERGE
        assert "DuplicateAliasError" in failed[0].error
        assert failed[0].committed_at is None

        # And the graph still works after the rollback.
        ok = graph.merge(a.entity_id, b.entity_id, decide("m2", at=T3))
        assert ok.outcome == OUTCOME_COMMITTED
        assert graph.model.get(b.entity_id).merged_into == a.entity_id

    def test_conflict_mutates_nothing(self, graph, two_boats):
        a, b = two_boats
        graph.link(a.entity_id, link_aliases(("boat_name", "A")), decide("d1"))
        snapshot_before = graph.model.snapshot()
        with pytest.raises(ConcurrentDecisionError):
            graph.merge(
                a.entity_id, b.entity_id,
                decide("d2", expected={a.entity_id: 0, b.entity_id: 0}),
            )
        assert graph.model.snapshot() == snapshot_before


# ---------------------------------------------------------------------------
# Decision reversal — history retained
# ---------------------------------------------------------------------------


class TestReversal:
    def test_merge_reversal_restores_memberships_and_history(self, graph):
        a = graph.create_entity(EntityType.BOAT, at=T0)
        b = graph.create_entity(EntityType.BOAT, at=T0)
        graph.link(b.entity_id, IdentityLink(
            aliases=(Alias(kind="boat_name", value="Beta", valid_from=EPOCH),),
            assertions=(tcc("row-b", "1.011", at=T1),),
        ), decide("l1", at=T1))
        merge_receipt = graph.merge(a.entity_id, b.entity_id, decide("m1", at=T2))

        rev = graph.reverse_decision("m1", decide("r1", at=T3, reason="not the same hull"))
        assert rev.outcome == OUTCOME_COMMITTED
        assert rev.supersedes == "m1"

        # The absorbed entity stands alone again (un-stamped, never deleted).
        b_after = graph.model.get(b.entity_id)
        assert b_after.is_live
        assert b_after.merged_into is None

        # The moved memberships were re-linked onto a fresh entity (ids are
        # never resurrected) holding exactly what the merge had moved.
        restored_id = rev.applied["relinked_entity"]
        restored = graph.model.get(restored_id)
        assert [al.value for al in restored.aliases] == ["Beta"]
        assert len(restored.assertions) == 1
        assert graph.model.resolve_truth(restored_id, as_of=T4).value("tcc") == "1.011"
        # The survivor keeps nothing of the merged identity.
        a_after = graph.model.get(a.entity_id)
        assert a_after.aliases == []
        assert a_after.assertions == []

        # History retained: the original receipt is superseded, not erased.
        original = graph.receipt(merge_receipt.operation_id)
        assert original.reversed_by == rev.operation_id
        assert rev.operation_id in [r.operation_id for r in graph.receipts]
        kinds = [r.kind for r in graph.receipts]
        assert kinds == [OP_LINK, OP_MERGE, OP_REVERSE]
        # The registry event log records the reversal too.
        assert any(
            e.event_type == "reverse_decision" for e in graph.model.event_log
        )

    def test_link_reversal_unbinds_identity(self, graph, two_boats):
        a, _ = two_boats
        graph.link(a.entity_id, link_aliases(("boat_name", "Alpha")), decide("l1"))
        rev = graph.reverse_decision("l1", decide("r1", reason="wrong boat"))
        restored = graph.model.get(rev.applied["relinked_entity"])
        assert [al.value for al in restored.aliases] == ["Alpha"]
        assert graph.model.get(a.entity_id).aliases == []

    def test_split_reversal_remerges(self, graph):
        boat = graph.create_entity(EntityType.BOAT, at=T0)
        a1 = AssertionV1(
            entity_type="boat", entity_key=boat.entity_key, field="tcc",
            value="1.001", valid_from=EPOCH, recorded_at=T1, source_slug="irc-certs",
        )
        graph.model.assert_about(boat.entity_id, a1)
        split_receipt = graph.split(
            boat.entity_id, assertion_ids=[a1.assertion_id], decision=decide("s1", at=T2)
        )
        new_id = split_receipt.applied["new_entity"]

        rev = graph.reverse_decision("s1", decide("r1", at=T3))
        assert rev.outcome == OUTCOME_COMMITTED
        # The split-off entity merged back (non-destructively) into the original.
        split_off = graph.model.get(new_id)
        assert not split_off.is_live
        assert split_off.merged_into == boat.entity_id
        assert split_off.split_from == boat.entity_id  # full history retained
        truth = graph.model.resolve_truth(boat.entity_id, as_of=T4)
        assert truth.value("tcc") == "1.001"

    def test_double_reversal_refused(self, graph, two_boats):
        a, b = two_boats
        graph.merge(a.entity_id, b.entity_id, decide("m1"))
        graph.reverse_decision("m1", decide("r1"))
        with pytest.raises(ReversalError, match="already reversed"):
            graph.reverse_decision("m1", decide("r2"))

    def test_reversal_of_unknown_decision_refused(self, graph):
        with pytest.raises(DecisionNotFoundError):
            graph.reverse_decision("nope", decide("r1"))

    def test_reversal_of_conflict_is_noop_error(self, graph, two_boats):
        a, b = two_boats
        graph.link(a.entity_id, link_aliases(("boat_name", "A")), decide("d1"))
        with pytest.raises(ConcurrentDecisionError):
            graph.merge(a.entity_id, b.entity_id,
                        decide("d2", expected={a.entity_id: 0, b.entity_id: 0}))
        with pytest.raises(ReversalError, match="did not commit"):
            graph.reverse_decision("d2", decide("r1"))


# ---------------------------------------------------------------------------
# Downstream views rebuild
# ---------------------------------------------------------------------------


class TestRebuildViews:
    def test_views_reflect_every_operation(self, graph):
        a = graph.create_entity(EntityType.BOAT, at=T0)
        b = graph.create_entity(EntityType.BOAT, at=T0)
        graph.link(a.entity_id, link_aliases(("sail_number", "A1")), decide("l1", at=T1))
        graph.link(b.entity_id, link_aliases(("sail_number", "B1")), decide("l2", at=T1))

        views = graph.rebuild_views()
        directory = {(r["kind"], r["value"]): r for r in views["alias_directory"]}
        assert directory[("sail_number", "A1")]["live_entity"] == a.entity_id
        assert directory[("sail_number", "B1")]["live_entity"] == b.entity_id

        graph.merge(a.entity_id, b.entity_id, decide("m1", at=T2))
        views = graph.rebuild_views()
        directory = {(r["kind"], r["value"]): r for r in views["alias_directory"]}
        # The absorbed alias projects to the surviving root.
        assert directory[("sail_number", "B1")]["live_entity"] == a.entity_id
        index = {r["entity_id"]: r for r in views["entity_index"]}
        assert index[b.entity_id]["live"] is False
        assert index[b.entity_id]["merged_into"] == a.entity_id
        assert index[a.entity_id]["alias_count"] == 2

        # After reversal the rebuilt views restore the pre-merge projection.
        graph.reverse_decision("m1", decide("r1", at=T3))
        views = graph.rebuild_views()
        index = {r["entity_id"]: r for r in views["entity_index"]}
        assert index[b.entity_id]["live"] is True
        directory = {(r["kind"], r["value"]): r for r in views["alias_directory"]}
        assert directory[("sail_number", "B1")]["live_entity"] != a.entity_id


# ---------------------------------------------------------------------------
# Receipt contract
# ---------------------------------------------------------------------------


class TestReceiptContract:
    def test_receipt_serialises(self, graph, two_boats):
        a, b = two_boats
        receipt = graph.merge(a.entity_id, b.entity_id, decide("m1"))
        d = receipt.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION
        assert d["kind"] == OP_MERGE
        assert d["outcome"] == OUTCOME_COMMITTED
        assert d["decision"]["decision_id"] == "m1"
        assert d["decision"]["evidence_refs"] == ["match-candidate-1", "score-9"]
        IdentityOperationReceiptV1(**{
            **{k: v for k, v in d.items() if k in IdentityOperationReceiptV1.__dataclass_fields__},
            "decision": receipt.decision,
            "committed_at": receipt.committed_at,
            "subjects": tuple(d["subjects"]),
        })
        assert receipt.to_json()

    def test_receipts_are_append_only_and_complete(self, graph, two_boats):
        a, b = two_boats
        graph.link(a.entity_id, link_aliases(("boat_name", "A")), decide("d1"))
        with pytest.raises(ConcurrentDecisionError):
            graph.merge(a.entity_id, b.entity_id,
                        decide("d2", expected={a.entity_id: 0}))
        graph.merge(a.entity_id, b.entity_id,
                    decide("d3", expected={a.entity_id: graph.get_version(a.entity_id).version,
                                           b.entity_id: 0}))
        outcomes = [(r.kind, r.outcome) for r in graph.receipts]
        assert outcomes == [
            (OP_LINK, OUTCOME_COMMITTED),
            (OP_MERGE, OUTCOME_CONFLICT),
            (OP_MERGE, OUTCOME_COMMITTED),
        ]
        assert graph.decision_receipt("d1").kind == OP_LINK
        assert graph.decision_receipt("d3").kind == OP_MERGE
