"""Store & bitemporal history tests for the assertion model (DP-03-02).

These tests verify the issue's acceptance criteria against the temporal
fixtures:

  * **Conflicting assertions coexist** — the full history retains every
    assertion; nothing is overwritten.
  * **Current resolved view is reproducible for any prior system time** —
    the resolved winner at each historical ``as_of`` matches a
    hand-computed expectation, and re-resolving is stable.

and the verification criterion:

  * **Temporal fixtures cover corrections, late arrivals, deletions and
    conflicting measurements** — each case has a dedicated test.

Runs against in-memory SQLite (no Postgres/Alembic dependency); the
data layer deliberately uses portable SQL so behaviour is identical on
Postgres in production.
"""

from __future__ import annotations

import pytest

from irc_data.assertions import AssertionStatus, AssertionStore, AssertionV1

from .fixtures import (
    A0_SAILSYS_INITIAL,
    A1_ORC_CONFLICT,
    A2_SAILSYS_CORRECTION,
    A3_TOPYACHT_LATE,
    BOAT,
    ENTITY_TYPE,
    R0_RATING,
    T0,
    T1,
    T2,
    T3,
    T4,
    build_timeline,
    populated_store,
)


@pytest.fixture()
def store() -> AssertionStore:
    return populated_store()


# ---------------------------------------------------------------------------
# Append-only history: nothing is overwritten
# ---------------------------------------------------------------------------


def test_full_history_is_retained(store):
    history = store.history(ENTITY_TYPE, BOAT, "tcc")
    ids = {a.assertion_id for a in history}
    assert ids == {
        A0_SAILSYS_INITIAL.assertion_id,
        A1_ORC_CONFLICT.assertion_id,
        A2_SAILSYS_CORRECTION.assertion_id,
        A3_TOPYACHT_LATE.assertion_id,
    }


def test_correction_does_not_overwrite_original_value(store):
    original = store.get(A0_SAILSYS_INITIAL.assertion_id)
    assert original.value == 1.024  # preserved
    # ...but it is marked superseded by the correction.
    assert original.superseded_by == A2_SAILSYS_CORRECTION.assertion_id


def test_provenance_is_recorded(store):
    a = store.get(A1_ORC_CONFLICT.assertion_id)
    assert a.source_slug == "orc"
    assert a.provenance_uri == "sha256:bbb1"
    assert a.unit == "tcc"
    assert a.confidence == 0.80


def test_record_is_idempotent(store):
    # Re-recording the same assertion does not duplicate it.
    before = len(store.history(ENTITY_TYPE, BOAT, "tcc"))
    returned = store.record(A0_SAILSYS_INITIAL)
    after = len(store.history(ENTITY_TYPE, BOAT, "tcc"))
    assert returned == A0_SAILSYS_INITIAL.assertion_id
    assert before == after


# ---------------------------------------------------------------------------
# Conflicting measurements coexist
# ---------------------------------------------------------------------------


def test_conflicting_measurements_coexist(store):
    res = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=T1)
    live = {res.winner.assertion_id} | {c.assertion_id for c in res.conflicts}
    assert A0_SAILSYS_INITIAL.assertion_id in live
    assert A1_ORC_CONFLICT.assertion_id in live


# ---------------------------------------------------------------------------
# Reproducible resolved view for any prior system time
# ---------------------------------------------------------------------------

# Hand-computed expected winner at each historical as_of.
EXPECTED_WINNERS = {
    "T0": A0_SAILSYS_INITIAL.assertion_id,   # only SailSys initial known
    "T1": A0_SAILSYS_INITIAL.assertion_id,   # SailSys(0.90) > ORC(0.80)
    "T2": A2_SAILSYS_CORRECTION.assertion_id,  # correction supersedes T0
    "T3": A2_SAILSYS_CORRECTION.assertion_id,  # correction still wins
    "T4": A1_ORC_CONFLICT.assertion_id,      # correction retracted; ORC remains
}
TIMES = {"T0": T0, "T1": T1, "T2": T2, "T3": T3, "T4": T4}


@pytest.mark.parametrize("label", ["T0", "T1", "T2", "T3", "T4"])
def test_resolved_view_reproducible_for_prior_time(store, label):
    as_of = TIMES[label]
    res = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=as_of)
    assert res.winner is not None, label
    assert res.winner.assertion_id == EXPECTED_WINNERS[label], label

    # Re-resolving at the same as_of is byte-identical (reproducible).
    res2 = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=as_of)
    assert res2.winner == res.winner


def test_correction_is_a_correction(store):
    """T2: the corrected value wins, the superseded original does not."""
    res = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=T2)
    assert res.winner.value == 1.027
    assert res.winner.assertion_id == A2_SAILSYS_CORRECTION.assertion_id
    conflict_ids = {c.assertion_id for c in res.conflicts}
    assert A0_SAILSYS_INITIAL.assertion_id not in conflict_ids  # superseded, out
    assert A1_ORC_CONFLICT.assertion_id in conflict_ids         # still coexists


def test_late_arrival_recorded_late_but_valid_early(store):
    """T3: TopYacht's assertion is recorded at T3 but valid from 2024-12-15.

    It does not win on confidence, but it *does* coexist (visible as a
    conflict) and is correctly excluded from the valid-time view at T2.
    """
    res = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=T3)
    conflict_ids = {c.assertion_id for c in res.conflicts}
    assert A3_TOPYACHT_LATE.assertion_id in conflict_ids
    # It was not known before T3:
    earlier = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=T2)
    earlier_live = {earlier.winner.assertion_id} | {
        c.assertion_id for c in earlier.conflicts
    }
    assert A3_TOPYACHT_LATE.assertion_id not in earlier_live


def test_deletion_removes_winner_but_preserves_history(store):
    """T4: SailSys's corrected assertion is retracted.  ORC's conflicting
    measurement becomes the winner; the retracted row is still in history."""
    # History still contains the retracted row.
    history = store.history(ENTITY_TYPE, BOAT, "tcc")
    retracted = [
        a for a in history if a.assertion_id == A2_SAILSYS_CORRECTION.assertion_id
    ]
    assert retracted and retracted[0].status == AssertionStatus.RETRACTED.value

    # But it no longer wins.
    res = store.resolve(ENTITY_TYPE, BOAT, "tcc", as_of=T4)
    assert res.winner.assertion_id == A1_ORC_CONFLICT.assertion_id


def test_deletion_can_empty_a_fact(store):
    """The rating fact's only assertion is retracted -> winner is None at T4,
    but the view at T0 still shows the value (history preserved)."""
    at_t0 = store.resolve(ENTITY_TYPE, BOAT, "rating", as_of=T0)
    assert at_t0.winner is not None
    assert at_t0.winner.value == 1.050

    at_t4 = store.resolve(ENTITY_TYPE, BOAT, "rating", as_of=T4)
    assert at_t4.winner is None


# ---------------------------------------------------------------------------
# Supersession chain
# ---------------------------------------------------------------------------


def test_supersession_chain_links(store):
    original = store.get(A0_SAILSYS_INITIAL.assertion_id)
    correction = store.get(A2_SAILSYS_CORRECTION.assertion_id)
    assert correction.supersedes == original.assertion_id
    assert original.superseded_by == correction.assertion_id


# ---------------------------------------------------------------------------
# Unit / confidence / provenance stored and returned faithfully
# ---------------------------------------------------------------------------


def test_value_unit_confidence_round_trip(store):
    a = store.get(A3_TOPYACHT_LATE.assertion_id)
    assert a.value == 1.010
    assert a.unit == "tcc"
    assert a.confidence == 0.70
    assert a.source_slug == "topyacht"


def test_list_all_returns_everything(store):
    all_ids = {a.assertion_id for a in store.all()}
    assert {
        A0_SAILSYS_INITIAL.assertion_id,
        A1_ORC_CONFLICT.assertion_id,
        A2_SAILSYS_CORRECTION.assertion_id,
        A3_TOPYACHT_LATE.assertion_id,
        R0_RATING.assertion_id,
    } <= all_ids
