"""Contract tests for the AssertionV1 model (DP-03-02).

Covers validation invariants, deterministic content-addressed ids,
JSON round-trip, and the pure in-memory resolution rules
(coexistence, supersession, retraction, bitemporal filtering).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from irc_data.assertions import (
    AssertionStatus,
    AssertionValidationError,
    AssertionV1,
    ResolutionV1,
    resolve,
)

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
)

HOUR = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _base_kwargs(**over):
    kw = dict(
        entity_type="boat",
        entity_key="GBR 1",
        field="tcc",
        value=1.0,
        source_slug="sailsys",
        recorded_at=T0,
        valid_from=T0,
    )
    kw.update(over)
    return kw


def test_valid_assertion_constructs():
    a = AssertionV1(**_base_kwargs())
    assert a.assertion_id  # id assigned
    assert a.status == AssertionStatus.ACTIVE.value


@pytest.mark.parametrize(
    "missing",
    ["entity_type", "entity_key", "field", "source_slug"],
)
def test_required_fields(missing):
    with pytest.raises(AssertionValidationError):
        AssertionV1(**_base_kwargs(**{missing: ""}))


@pytest.mark.parametrize("bad", [-0.1, 1.1, 5])
def test_confidence_bounds(bad):
    with pytest.raises(AssertionValidationError):
        AssertionV1(**_base_kwargs(confidence=bad))


def test_confidence_boundaries_ok():
    assert AssertionV1(**_base_kwargs(confidence=0.0)).confidence == 0.0
    assert AssertionV1(**_base_kwargs(confidence=1.0)).confidence == 1.0


def test_invalid_status_rejected():
    with pytest.raises(AssertionValidationError):
        AssertionV1(**_base_kwargs(status="bogus"))


def test_valid_interval_enforced():
    with pytest.raises(AssertionValidationError):
        AssertionV1(**_base_kwargs(valid_from=T1, valid_to=T0))


def test_naive_timestamps_normalised_to_utc():
    naive = datetime(2025, 1, 1, 9, 0, 0)  # no tzinfo
    a = AssertionV1(**_base_kwargs(valid_from=naive, recorded_at=naive))
    assert a.valid_from.tzinfo is not None
    assert a.recorded_at == T0


# ---------------------------------------------------------------------------
# Deterministic, content-addressed ids (idempotency)
# ---------------------------------------------------------------------------


def test_id_is_deterministic_for_identical_content():
    a1 = AssertionV1(**_base_kwargs())
    a2 = AssertionV1(**_base_kwargs())
    assert a1.assertion_id == a2.assertion_id


def test_id_changes_when_value_changes():
    a1 = AssertionV1(**_base_kwargs(value=1.0))
    a2 = AssertionV1(**_base_kwargs(value=2.0))
    assert a1.assertion_id != a2.assertion_id


def test_id_changes_when_recorded_at_changes():
    a1 = AssertionV1(**_base_kwargs(recorded_at=T0))
    a2 = AssertionV1(**_base_kwargs(recorded_at=T1))
    assert a1.assertion_id != a2.assertion_id


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_json_round_trip():
    original = A2_SAILSYS_CORRECTION
    restored = AssertionV1.from_json(original.to_json())
    assert restored == original
    assert restored.assertion_id == original.assertion_id
    assert restored.supersedes == A0_SAILSYS_INITIAL.assertion_id


def test_to_dict_is_json_safe():
    import json

    json.dumps(A1_ORC_CONFLICT.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# Immutability of the correction / deletion helpers
# ---------------------------------------------------------------------------


def test_supersede_does_not_mutate_original():
    original = A0_SAILSYS_INITIAL
    corrected = A2_SAILSYS_CORRECTION
    marked = original.supersede(corrected)
    assert original.superseded_by is None  # untouched history
    assert marked.superseded_by == corrected.assertion_id
    assert marked.value == original.value  # value preserved


def test_retract_preserves_value_and_history():
    retracted = R0_RATING.retract()
    assert retracted.status == AssertionStatus.RETRACTED.value
    assert retracted.value == R0_RATING.value
    assert R0_RATING.status == AssertionStatus.ACTIVE.value  # original intact


def test_cannot_supersede_self():
    with pytest.raises(AssertionValidationError):
        A0_SAILSYS_INITIAL.supersede(A0_SAILSYS_INITIAL)


# ---------------------------------------------------------------------------
# Pure resolution rules (no DB)
# ---------------------------------------------------------------------------

ALL_TCC = [A0_SAILSYS_INITIAL, A1_ORC_CONFLICT, A2_SAILSYS_CORRECTION, A3_TOPYACHT_LATE]


def test_conflicting_assertions_coexist_in_resolution():
    """At T1 both SailSys (1.024) and ORC (1.031) are live; neither is
    discarded — the loser is reported as a conflict."""
    res = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T1)
    assert res.winner is not None
    conflict_ids = {c.assertion_id for c in res.conflicts}
    # Both T0 and T1 assertions are live at T1 (T2+ not yet recorded).
    live_ids = {res.winner.assertion_id} | conflict_ids
    assert A0_SAILSYS_INITIAL.assertion_id in live_ids
    assert A1_ORC_CONFLICT.assertion_id in live_ids
    assert len(live_ids) == 2  # exactly the two coexisting


def test_higher_confidence_wins():
    # SailSys initial (0.90) vs ORC conflict (0.80) -> SailSys wins at T1.
    res = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T1)
    assert res.winner.assertion_id == A0_SAILSYS_INITIAL.assertion_id


def test_future_assertions_invisible_as_of_earlier_time():
    """Bitemporal filter: at T0, only the T0 assertion is known."""
    res = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T0)
    assert res.winner.assertion_id == A0_SAILSYS_INITIAL.assertion_id
    assert res.conflicts == ()


def test_superseded_assertion_does_not_win():
    """After the T2 correction, the superseded T0 row must not win even
    though it is still live by confidence alone."""
    res = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T3)
    assert res.winner.assertion_id == A2_SAILSYS_CORRECTION.assertion_id
    assert res.winner.value == 1.027


def test_retracted_assertion_never_wins_after_retraction():
    retracted = R0_RATING.retract(at=T0 + HOUR)
    res = resolve(
        [retracted], ENTITY_TYPE, BOAT, "rating", as_of=T0 + 2 * HOUR
    )
    assert res.winner is None


def test_retraction_is_bitemporal():
    """A retraction at T4 does not change the view as of T0."""
    retracted = R0_RATING.retract(at=T4)
    before = resolve([retracted], ENTITY_TYPE, BOAT, "rating", as_of=T0)
    assert before.winner is not None  # still visible before the deletion
    after = resolve([retracted], ENTITY_TYPE, BOAT, "rating", as_of=T4)
    assert after.winner is None


def test_retraction_without_timestamp_means_always_retracted():
    """status=retracted with no retracted_at is treated as retracted from
    the start of system time (conservative current-state hint)."""
    always = AssertionV1.from_dict(
        {**R0_RATING.to_dict(), "status": "retracted", "retracted_at": None}
    )
    res = resolve([always], ENTITY_TYPE, BOAT, "rating", as_of=T0)
    assert res.winner is None


def test_resolution_is_reproducible():
    """Same inputs -> identical winner, every time."""
    r1 = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T3)
    r2 = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T3)
    assert r1.winner == r2.winner
    assert {c.assertion_id for c in r1.conflicts} == {
        c.assertion_id for c in r2.conflicts
    }


def test_valid_time_filter_excludes_not_yet_valid():
    """An assertion whose valid_from is after valid_as_of is not live."""
    res = resolve(
        ALL_TCC,
        ENTITY_TYPE,
        BOAT,
        "tcc",
        as_of=T3,
        valid_as_of=T0,  # before T1/T2/T3 valid_from
    )
    # Only A0 (valid_from=T0) and A3 (valid_from pre-T0) are valid at T0.
    live_ids = {res.winner.assertion_id} | {c.assertion_id for c in res.conflicts}
    assert A1_ORC_CONFLICT.assertion_id not in live_ids
    assert A2_SAILSYS_CORRECTION.assertion_id not in live_ids


def test_valid_to_excludes_expired():
    expiring = AssertionV1(
        **_base_kwargs(value=9.9, valid_from=T0, valid_to=T1, confidence=1.0)
    )
    # At valid_as_of=T2 the expiring assertion is out of its interval.
    res = resolve([expiring], "boat", "GBR 1", "tcc", as_of=T3, valid_as_of=T2)
    assert res.winner is None


def test_resolution_reports_considered_count():
    res = resolve(ALL_TCC + [R0_RATING], ENTITY_TYPE, BOAT, "tcc", as_of=T4)
    # R0_RATING is a different field -> not considered.
    assert res.considered == 4


def test_resolution_view_to_dict_serialisable():
    import json

    res = resolve(ALL_TCC, ENTITY_TYPE, BOAT, "tcc", as_of=T3)
    json.dumps(res.to_dict())  # must not raise
