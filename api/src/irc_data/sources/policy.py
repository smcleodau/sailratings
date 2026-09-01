"""Responsible collection policy and source classification (DP-01-02).

This module defines ``CollectionPolicyDecisionV1`` — the handoff / output
contract for DP-01-02.  It encodes:

* Source classification: **public**, **authenticated**, **licensed**,
  **prohibited**, and **unclear**.
* Enforcement rules: robots.txt, rate limiting, attribution, takedown,
  personal-data, and retention.
* Policy versioning — the adapter SDK cannot run without an approved policy
  version.

The policy is a pure-Python data structure so it can be unit-tested without
a database.  The companion ``gate`` module applies it at runtime.

Backward-compatibility helpers ``assert_policy_current``,
``assert_source_approved``, and ``assert_source_collectable`` are retained
for existing scrapers that import them directly.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence


# ---------------------------------------------------------------------------
# Policy version
# ---------------------------------------------------------------------------

CURRENT_POLICY_VERSION = "interim-v0"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LegalStatus(str, enum.Enum):
    """Lifecycle status of a source record in the registry."""

    APPROVED = "approved"
    HOLD = "hold"
    BLOCKED = "blocked"


class SourceClass(str, enum.Enum):
    """Classification of *how* a source publishes its data.

    ``public``
        Freely accessible, no authentication, no paywall.
    ``authenticated``
        Requires an account / login to access.  Collection only permitted
        when the platform holds explicit written authorisation and
        credentials are submitted honestly (no auth circumvention).
    ``licensed``
        Data acquired under a licence or data-sharing agreement.
        Collection permitted only within the licence scope.
    ``prohibited``
        Robots.txt disallow, login wall without authorisation, paywall,
        or ToS forbidding automated access.
    ``unclear``
        Rights / robots status cannot be determined.  Collection deferred
        pending a human rights ruling.
    """

    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    LICENSED = "licensed"
    PROHIBITED = "prohibited"
    UNCLEAR = "unclear"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PolicyVersionMismatchError(Exception):
    """Raised when a source references a policy version ≠ CURRENT_POLICY_VERSION."""

    def __init__(self, slug: str, source_version: str | None = None, message: str | None = None):
        self.slug = slug
        self.source_version = source_version
        if message:
            super().__init__(message)
        else:
            super().__init__(
                f"Source '{slug}' references policy_version='{source_version}', "
                f"current is '{CURRENT_POLICY_VERSION}'. "
                f"Update the source record or the policy."
            )


class SourceNotApprovedError(Exception):
    """Raised when a source is not approved (hold / blocked / disabled)."""

    def __init__(self, slug: str, reason: str = ""):
        self.slug = slug
        self.reason = reason
        msg = f"Source '{slug}' is not approved for collection"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class ProhibitedCollectionError(Exception):
    """Raised when a URL is classified as prohibited (disallow / login wall)."""

    def __init__(self, url: str, reason: str = ""):
        self.url = url
        self.reason = reason
        msg = f"Collection prohibited for {url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Policy rule blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RobotsRule:
    """robots.txt compliance configuration."""

    fetch_at_session_start: bool = True
    cache_ttl_hours: int = 24
    respect_user_agent_star: bool = True
    stop_on_fetch_error: bool = True  # 5xx / network error → stop source


@dataclass(frozen=True)
class RateRule:
    """Per-domain rate limiting configuration."""

    min_delay_seconds: float = 2.0
    jitter_seconds: float = 1.0
    honour_retry_after: bool = True
    backoff_sequence: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)
    abort_after_backoff_exhausted: bool = True


@dataclass(frozen=True)
class AttributionRule:
    """How to identify ourselves and attribute collection."""

    user_agent: str = (
        "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)"
    )
    attribution_header: str = "X-SailRatings-Source"
    require_source_header: bool = True


@dataclass(frozen=True)
class TakedownRule:
    """Takedown / complaint response procedure."""

    response_window_hours: int = 4
    disable_immediate: bool = True
    quarantine_existing: bool = True
    quarantine_path: str = "data/raw/quarantine"
    derived_review_hours: int = 48
    incident_type: str = "takedown_request"
    contact: str = "stuart@sailratings.com"


@dataclass(frozen=True)
class PersonalDataRule:
    """What personal data may or may not be collected."""

    collect_published_results: bool = True
    prohibited_fields: tuple[str, ...] = (
        "owner_name",
        "owner_email",
        "owner_phone",
        "owner_address",
        "home_port",
        "financial_data",
    )
    no_auth_circumvention: bool = True
    no_captcha_bypass: bool = True
    no_paywall_circumvention: bool = True
    no_session_hijack: bool = True


@dataclass(frozen=True)
class RetentionRule:
    """Data retention and deduplication configuration."""

    hash_algorithm: str = "sha256"
    skip_duplicate_hashes: bool = True
    max_object_size_mb: int = 25
    max_fetches_per_night: int = 5000
    max_total_mb_per_night: int = 500
    raw_artifact_retention_days: int = 365
    conditional_requests: bool = True  # If-None-Match / If-Modified-Since


@dataclass(frozen=True)
class CollectionWindowRule:
    """When collection is permitted to run."""

    start_hour_local: int = 1
    end_hour_local: int = 6
    allow_daytime_health_checks: bool = True
    health_check_max_fetches: int = 1


# ---------------------------------------------------------------------------
# Source classification helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceClassification:
    """Classification of a single source or URL.

    Encapsulates the decision of *whether* and *how* data may be collected.
    """

    source_class: SourceClass
    legal_status: LegalStatus
    reason: str = ""

    @property
    def collectible(self) -> bool:
        """True only when collection is explicitly permitted."""
        return (
            self.source_class
            in (SourceClass.PUBLIC, SourceClass.AUTHENTICATED, SourceClass.LICENSED)
            and self.legal_status == LegalStatus.APPROVED
        )


# ---------------------------------------------------------------------------
# The deliverable: CollectionPolicyDecisionV1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionPolicyDecisionV1:
    """DP-01-02 deliverable — the responsible-collection policy decision.

    This is the **handoff / output contract** for DP-01-02.  It bundles every
    rule block and provides helper methods for evaluating sources.

    The policy is immutable (frozen=True) so it can be safely shared across
    threads and workflows without accidental mutation.
    """

    version: str = CURRENT_POLICY_VERSION
    approved_on: date = field(default_factory=lambda: date(2026, 8, 30))
    authority: str = "Stuart McLeod, SailRatings founder"

    robots: RobotsRule = field(default_factory=RobotsRule)
    rate: RateRule = field(default_factory=RateRule)
    attribution: AttributionRule = field(default_factory=AttributionRule)
    takedown: TakedownRule = field(default_factory=TakedownRule)
    personal_data: PersonalDataRule = field(default_factory=PersonalDataRule)
    retention: RetentionRule = field(default_factory=RetentionRule)
    collection_window: CollectionWindowRule = field(default_factory=CollectionWindowRule)

    # --- classification table -------------------------------------------

    #: Mapping of source slug → SourceClass for all interim-v0 sources.
    #: This is the canonical classification table shipped with the policy.
    source_classes: dict[str, SourceClass] = field(
        default_factory=lambda: {
            # Public results / ratings — freely published
            "sailsys": SourceClass.PUBLIC,
            "topyacht": SourceClass.PUBLIC,
            "irc-tcc": SourceClass.PUBLIC,
            "orc": SourceClass.PUBLIC,
            "yachtscoring": SourceClass.PUBLIC,
            "manage2sail": SourceClass.PUBLIC,
            "sailwave": SourceClass.PUBLIC,
            "sailing-news": SourceClass.PUBLIC,
            # Certificates — publicly accessible PDFs
            "irc-certs": SourceClass.PUBLIC,
            # Hold sources — unclear rights
            "clubspot": SourceClass.UNCLEAR,
            "kwindoo": SourceClass.UNCLEAR,
        }
    )

    #: Legal status per slug (mirrors the data_sources seed rows).
    legal_statuses: dict[str, LegalStatus] = field(
        default_factory=lambda: {
            "sailsys": LegalStatus.APPROVED,
            "topyacht": LegalStatus.APPROVED,
            "irc-tcc": LegalStatus.APPROVED,
            "orc": LegalStatus.APPROVED,
            "yachtscoring": LegalStatus.APPROVED,
            "manage2sail": LegalStatus.APPROVED,
            "sailwave": LegalStatus.APPROVED,
            "sailing-news": LegalStatus.APPROVED,
            "irc-certs": LegalStatus.APPROVED,
            "clubspot": LegalStatus.HOLD,
            "kwindoo": LegalStatus.HOLD,
        }
    )

    #: Domains where login walls / auth are encountered and require explicit
    #: authorisation before any collection.
    authenticated_domains: tuple[str, ...] = (
        "app.sailsys.com.au",  # auth-gated admin areas (public results are open)
    )

    #: Domains explicitly prohibited (robots disallow + ToS).
    prohibited_domains: tuple[str, ...] = ()

    # --- evaluation helpers ----------------------------------------------

    def classify(self, slug: str | None, domain: str | None = None) -> SourceClassification:
        """Classify a source (and optionally a domain) for collection.

        Resolution order:
        1. If *domain* is in ``prohibited_domains`` → prohibited.
        2. If *domain* is in ``authenticated_domains`` → authenticated.
        3. If *slug* has a known classification → use it.
        4. Otherwise → unclear (defer to human review).
        """
        if domain:
            domain_lower = domain.lower()
            for d in self.prohibited_domains:
                if domain_lower == d or domain_lower.endswith("." + d):
                    return SourceClassification(
                        SourceClass.PROHIBITED,
                        LegalStatus.BLOCKED,
                        f"Domain '{domain}' is on the prohibited list",
                    )
            for d in self.authenticated_domains:
                if domain_lower == d or domain_lower.endswith("." + d):
                    sc = SourceClass.AUTHENTICATED
                    ls = self.legal_statuses.get(slug, LegalStatus.HOLD)
                    return SourceClassification(
                        sc, ls, f"Domain '{domain}' requires authentication"
                    )

        if slug and slug in self.source_classes:
            sc = self.source_classes[slug]
            ls = self.legal_statuses.get(slug, LegalStatus.BLOCKED)
            reason = f"Source '{slug}' classified as {sc.value} / {ls.value}"
            return SourceClassification(sc, ls, reason)

        return SourceClassification(
            SourceClass.UNCLEAR,
            LegalStatus.BLOCKED,
            "No source record or domain classification — defer to human review",
        )

    def assert_version(self, source_version: str, slug: str = "<unknown>") -> None:
        """Raise ``PolicyVersionMismatchError`` if *source_version* ≠ current."""
        if source_version != self.version:
            raise PolicyVersionMismatchError(slug, source_version)

    def is_current(self, source_version: str) -> bool:
        """Return True if *source_version* matches this policy version."""
        return source_version == self.version

    def to_summary(self) -> dict:
        """Return a JSON-serialisable summary of the policy (for audit logs)."""
        return {
            "version": self.version,
            "approved_on": self.approved_on.isoformat(),
            "authority": self.authority,
            "robots": {
                "fetch_at_session_start": self.robots.fetch_at_session_start,
                "cache_ttl_hours": self.robots.cache_ttl_hours,
                "stop_on_fetch_error": self.robots.stop_on_fetch_error,
            },
            "rate": {
                "min_delay_seconds": self.rate.min_delay_seconds,
                "jitter_seconds": self.rate.jitter_seconds,
                "honour_retry_after": self.rate.honour_retry_after,
            },
            "attribution": {
                "user_agent": self.attribution.user_agent,
                "attribution_header": self.attribution.attribution_header,
            },
            "takedown": {
                "response_window_hours": self.takedown.response_window_hours,
                "disable_immediate": self.takedown.disable_immediate,
                "quarantine_existing": self.takedown.quarantine_existing,
            },
            "personal_data": {
                "prohibited_fields": list(self.personal_data.prohibited_fields),
                "no_auth_circumvention": self.personal_data.no_auth_circumvention,
            },
            "retention": {
                "hash_algorithm": self.retention.hash_algorithm,
                "max_object_size_mb": self.retention.max_object_size_mb,
                "max_fetches_per_night": self.retention.max_fetches_per_night,
                "max_total_mb_per_night": self.retention.max_total_mb_per_night,
            },
            "collection_window": {
                "start_hour_local": self.collection_window.start_hour_local,
                "end_hour_local": self.collection_window.end_hour_local,
            },
            "source_count": len(self.source_classes),
        }


# ---------------------------------------------------------------------------
# Module-level singleton (for easy import)
# ---------------------------------------------------------------------------

#: The active policy decision.  Import this everywhere enforcement is needed.
ACTIVE_POLICY: CollectionPolicyDecisionV1 = CollectionPolicyDecisionV1()


# ---------------------------------------------------------------------------
# Backward-compatibility helpers (used by existing scrapers)
# ---------------------------------------------------------------------------


def assert_policy_current(source) -> None:
    """Raise ``PolicyVersionMismatchError`` if the source's policy is stale.

    Accepts any object with ``policy_version`` and ``slug`` attributes
    (both ``DataSource`` and ``SourceRecord`` work).
    """
    version = getattr(source, "policy_version", None)
    slug = getattr(source, "slug", "<unknown>")
    if version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(
            slug,
            version,
            message=(
                f"{slug} references {version}, "
                f"current is {CURRENT_POLICY_VERSION}"
            ),
        )


def assert_source_approved(source) -> None:
    """Raise ``SourceNotApprovedError`` if the source is not collectable.

    Checks both ``enabled`` and ``legal_status``.  Accepts both
    ``DataSource`` (string legal_status) and ``SourceRecord`` (enum).
    """
    slug = getattr(source, "slug", "<unknown>")
    enabled = getattr(source, "enabled", True)
    legal_status = getattr(source, "legal_status", None)

    if not enabled:
        raise SourceNotApprovedError(slug, reason="source is disabled (kill switch)")

    # legal_status may be a string or LegalStatus enum
    status_val = legal_status.value if hasattr(legal_status, "value") else legal_status
    if status_val != "approved":
        raise SourceNotApprovedError(
            slug,
            reason=f"legal_status is '{status_val}', must be 'approved'",
        )


def assert_source_collectable(source) -> None:
    """Full policy gate: version + approval + enabled.

    Convenience function combining all checks.
    """
    assert_policy_current(source)
    assert_source_approved(source)
