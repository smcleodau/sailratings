"""Policy enforcement — the gate every fetch must pass.

Implements the invariant from SPEC-012 §3: every collection job MUST
resolve a ``data_sources`` row before fetching.  If
``legal_status != 'approved'`` or ``enabled = False``, raise
``SourceNotApprovedError`` and abort.
"""

from __future__ import annotations

from irc_data.sources.models import DataSource

CURRENT_POLICY_VERSION = "interim-v0"


class PolicyVersionMismatchError(Exception):
    """Raised when a source's policy_version ≠ CURRENT_POLICY_VERSION."""

    def __init__(self, message: str, source_slug: str | None = None) -> None:
        super().__init__(message)
        self.source_slug = source_slug


class SourceNotApprovedError(Exception):
    """Raised when a source is not approved or is disabled."""

    def __init__(self, slug: str, reason: str = "") -> None:
        msg = f"Source '{slug}' is not approved for collection"
        if reason:
            msg = f"{msg}: {reason}"
        super().__init__(msg)
        self.slug = slug
        self.reason = reason


def assert_policy_current(source: DataSource) -> None:
    """Raise ``PolicyVersionMismatchError`` if the source's policy is stale."""
    if source.policy_version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(
            f"{source.slug} references {source.policy_version}, "
            f"current is {CURRENT_POLICY_VERSION}",
            source_slug=source.slug,
        )


def assert_source_approved(source: DataSource) -> None:
    """Raise ``SourceNotApprovedError`` if the source is not collectable.

    Checks both ``enabled`` and ``legal_status``.
    """
    if not source.enabled:
        raise SourceNotApprovedError(source.slug, reason="source is disabled (kill switch)")
    if source.legal_status != "approved":
        raise SourceNotApprovedError(
            source.slug,
            reason=f"legal_status is '{source.legal_status}', must be 'approved'",
        )


def assert_source_collectable(source: DataSource) -> None:
    """Full policy gate: version + approval + enabled.

    Convenience function combining all checks.
    """
    assert_policy_current(source)
    assert_source_approved(source)
