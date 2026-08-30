"""Policy enforcement primitives (SPEC-012 §3, INTERIM-POLICY.md).

This module is the thin, dependency-free policy gate.  It lives in the
SDK so every adapter — including the reference fake — can call it
without importing a DB module.  When DP-01-02 lands its full policy
enforcement layer it can either re-export these helpers or wrap them;
the contract (``assert_policy_current`` / ``assert_source_approved``)
is stable.
"""

from __future__ import annotations

from .contracts import (
    CURRENT_POLICY_VERSION,
    DataSource,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)

__all__ = [
    "CURRENT_POLICY_VERSION",
    "assert_policy_current",
    "assert_source_approved",
    "assert_source_collectable",
]


def assert_policy_current(source: DataSource) -> None:
    """Raise :class:`PolicyVersionMismatchError` if ``source`` is stale.

    Every adapter calls this before its first fetch (SPEC-012 §3.1).
    """
    if source.policy_version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(
            slug=source.slug,
            source_version=source.policy_version,
            current_version=CURRENT_POLICY_VERSION,
        )


def assert_source_approved(source: DataSource) -> None:
    """Raise :class:`SourceNotApprovedError` unless the source is collectable.

    A source is collectable iff ``legal_status == 'approved'`` *and*
    ``enabled`` is true (the kill switch, SPEC-012 §3.4).  ``hold`` and
    ``blocked`` sources must produce **zero** fetch attempts.
    """
    if not source.enabled:
        raise SourceNotApprovedError(source.slug, reason="kill switch enabled=FALSE")
    if source.legal_status != "approved":
        raise SourceNotApprovedError(
            source.slug, reason=f"legal_status={source.legal_status!r}"
        )


def assert_source_collectable(source: DataSource) -> None:
    """Full pre-flight: policy version + approval gate."""
    assert_policy_current(source)
    assert_source_approved(source)
