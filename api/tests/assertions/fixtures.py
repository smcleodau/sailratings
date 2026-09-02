"""Temporal fixtures for the bitemporal assertion model (DP-03-02).

These fixtures implement the issue's verification criterion:

    "Temporal fixtures cover corrections, late arrivals, deletions and
    conflicting measurements."

Everything is built on **fixed timestamps** so the resolved view for any
prior system time is reproducible and the tests are deterministic.

Timeline (all UTC)
------------------

The scenario follows one boat's ``tcc`` rating (a measurement) plus one
``rating`` fact that gets deleted, and captures the four required cases:

  T0 = 2025-01-01  SailSys asserts tcc = 1.024 (initial measurement)
  T1 = 2025-02-01  ORC asserts   tcc = 1.031 (CONFLICTING measurement,
                                                coexists with SailSys)
  T2 = 2025-03-01  SailSys CORRECTS its tcc → 1.027 (supersedes T0 row;
                                                    history preserved)
  T3 = 2025-04-01  TopYacht LATE ARRIVAL: asserts tcc = 1.010 but with
                   valid_from = 2024-12-15 (valid *before* T0!) — arrives
                   in the system *after* the facts it contradicts.
  T4 = 2025-05-01  SailSys DELETES its (corrected) tcc assertion
                   (retraction).  ORC's conflicting measurement survives.

A second fact, ``rating``, exists purely to exercise a deletion that
leaves *no* surviving assertion (winner becomes None at later as_of).

Usage::

    timeline = build_timeline()           # ordered list of events
    store = AssertionStore.in_memory()
    apply_timeline(store, timeline)       # append every assertion
    res = store.resolve("boat", BOAT, "tcc", as_of=T2)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal

from irc_data.assertions import AssertionStatus, AssertionStore, AssertionV1


# ---------------------------------------------------------------------------
# Fixed clock — every event in the scenario happens at one of these.
# ---------------------------------------------------------------------------

T0 = datetime(2025, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2025, 2, 1, 9, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2025, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
T3 = datetime(2025, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
T4 = datetime(2025, 5, 1, 9, 0, 0, tzinfo=timezone.utc)

#: The entity every fixture assertion is about.
BOAT = "GBR 8310"
ENTITY_TYPE = "boat"

#: The pre-T0 valid time used by the late-arriving assertion.
LATE_VALID_FROM = datetime(2024, 12, 15, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Timeline events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEvent:
    """One thing that happened at ``at``.

    ``kind`` is ``assert`` (append a new assertion) or ``retract``
    (delete an existing assertion by its label).
    """

    at: datetime
    kind: Literal["assert", "retract"]
    label: str
    assertion: AssertionV1 | None = None
    retract_label: str | None = None


# ---------------------------------------------------------------------------
# Assertion builders (deterministic ids via fixed timestamps)
# ---------------------------------------------------------------------------


def _mk(**kw) -> AssertionV1:
    defaults = dict(
        entity_type=ENTITY_TYPE,
        entity_key=BOAT,
        field="tcc",
        unit="tcc",
    )
    defaults.update(kw)
    return AssertionV1(**defaults)


#: T0 — initial SailSys measurement.
A0_SAILSYS_INITIAL = _mk(
    value=1.024,
    valid_from=T0,
    recorded_at=T0,
    source_slug="sailsys",
    provenance_uri="sha256:aaa0",
    confidence=0.90,
)

#: T1 — ORC conflicting measurement (coexists, does not overwrite).
A1_ORC_CONFLICT = _mk(
    value=1.031,
    valid_from=T1,
    recorded_at=T1,
    source_slug="orc",
    provenance_uri="sha256:bbb1",
    confidence=0.80,
)

#: T2 — SailSys correction; supersedes the T0 row.
A2_SAILSYS_CORRECTION = _mk(
    value=1.027,
    valid_from=T2,
    recorded_at=T2,
    source_slug="sailsys",
    provenance_uri="sha256:ccc2",
    confidence=0.95,
    supersedes=A0_SAILSYS_INITIAL.assertion_id,
)

#: T3 — TopYacht late arrival: recorded at T3 but valid from *before* T0.
A3_TOPYACHT_LATE = _mk(
    value=1.010,
    valid_from=LATE_VALID_FROM,
    recorded_at=T3,
    source_slug="topyacht",
    provenance_uri="sha256:ddd3",
    confidence=0.70,
)

#: rating fact used to exercise a deletion that empties the fact.
R0_RATING = _mk(
    field="rating",
    unit="irc",
    value=1.050,
    valid_from=T0,
    recorded_at=T0,
    source_slug="sailsys",
    provenance_uri="sha256:eee0",
    confidence=0.90,
)

ASSERTIONS = {
    "sailsys_initial": A0_SAILSYS_INITIAL,
    "orc_conflict": A1_ORC_CONFLICT,
    "sailsys_correction": A2_SAILSYS_CORRECTION,
    "topyacht_late": A3_TOPYACHT_LATE,
    "rating": R0_RATING,
}


def build_timeline() -> list[TimelineEvent]:
    """The full scenario timeline in chronological order."""
    return [
        TimelineEvent(T0, "assert", "sailsys_initial", A0_SAILSYS_INITIAL),
        TimelineEvent(T0, "assert", "rating", R0_RATING),
        TimelineEvent(T1, "assert", "orc_conflict", A1_ORC_CONFLICT),
        TimelineEvent(T2, "assert", "sailsys_correction", A2_SAILSYS_CORRECTION),
        TimelineEvent(T3, "assert", "topyacht_late", A3_TOPYACHT_LATE),
        # Deletions.
        TimelineEvent(
            T4, "retract", "del_correction", None, retract_label="sailsys_correction"
        ),
        TimelineEvent(
            T4, "retract", "del_rating", None, retract_label="rating"
        ),
    ]


def apply_timeline(store: AssertionStore, timeline: Iterable[TimelineEvent]) -> None:
    """Replay a timeline into a store, resolving labels to assertion ids."""
    ids: dict[str, str] = {}
    for event in timeline:
        if event.kind == "assert":
            assert event.assertion is not None
            ids[event.label] = store.record(event.assertion)
        elif event.kind == "retract":
            target = ids[event.retract_label]
            # Bitemporal deletion: the retraction is stamped with the
            # event's system time so as-of views before it are unchanged.
            store.retract(target, at=event.at)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown event kind {event.kind!r}")


def populated_store() -> AssertionStore:
    """An in-memory store with the full timeline applied."""
    store = AssertionStore.in_memory()
    apply_timeline(store, build_timeline())
    return store
