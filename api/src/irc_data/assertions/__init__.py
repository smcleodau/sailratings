"""Source assertion and bitemporal history model (DP-03-02).

Preserves *who said what, and when the truth changed*:

* :class:`AssertionV1` — the immutable, append-only source assertion
  (source-valid time, system-observed time, provenance, value, unit,
  confidence, supersession).
* :func:`resolve` / :class:`ResolutionV1` — the deterministic,
  reproducible resolved view of a fact for any prior system time.
* :class:`AssertionStore` and the module-level functions in
  :mod:`irc_data.assertions.store` — the append-only persistence layer.
"""

from irc_data.assertions.models import (
    SCHEMA_VERSION,
    STATUSES,
    AssertionStatus,
    AssertionValidationError,
    AssertionV1,
    ResolutionV1,
    resolve,
)
from irc_data.assertions.store import (
    AssertionStore,
    fact_assertions,
    get_assertion,
    get_assertions_for_fact,
    init_assertion_tables,
    list_assertions,
    metadata,
    record_assertion,
    resolve_fact,
    retract_assertion,
)

__all__ = [
    "SCHEMA_VERSION",
    "STATUSES",
    "AssertionStatus",
    "AssertionValidationError",
    "AssertionV1",
    "ResolutionV1",
    "resolve",
    "AssertionStore",
    "fact_assertions",
    "metadata",
    "init_assertion_tables",
    "record_assertion",
    "retract_assertion",
    "get_assertion",
    "get_assertions_for_fact",
    "list_assertions",
    "resolve_fact",
]
