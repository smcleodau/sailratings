"""Responsible collection policy and source classification (DP-01-02).

This module defines:

* ``CollectionPolicyDecisionV1`` — the global responsible-collection policy
  config (rule blocks for robots, rate limiting, attribution, takedown,
  personal-data, and retention).
* ``SourceDecisionV1`` — per-source output contract (DP-01-05 addition);
  produced by ``resolve_source()`` when a source passes all gates.
* ``CollectionRules`` — per-source enforcement rules (DP-01-05 addition).
* ``ContentType`` — dominant content type enum (DP-01-05 addition).
* Emergency disable helpers, collection window checks, and policy summary.

Policy versioning — the adapter SDK cannot run without an approved policy
version.  Policy text: ``docs/SOURCE-POLICY.md`` (v1.0, approved 2026-09-02,
DP-01-02), which supersedes ``docs/INTERIM-POLICY.md`` (interim-v0, DP-00-01).
Spec: SPEC-012 §3.

Backward-compatibility helpers ``assert_policy_current``,
``assert_source_approved``, and ``assert_source_collectable`` are retained
for existing scrapers that import them directly.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

CURRENT_POLICY_VERSION = "v1.0"
"""The policy version that every active ``data_sources`` row must reference.

``v1.0`` is the DP-01-02 responsible-collection policy
(``docs/SOURCE-POLICY.md``); it supersedes ``interim-v0`` on approval.
"""

SUPERSEDED_POLICY_VERSIONS: tuple[str, ...] = ("interim-v0",)
"""Policy versions that are no longer approved.  Any source row or envelope
referencing one of these fails the policy-version gate (the adapter cannot
run) until the row is stamped with ``CURRENT_POLICY_VERSION``."""

POLICY_APPROVED_DATE = "2026-09-02"
"""Human-readable approval date of the current policy."""

POLICY_AUTHORITY = "Stuart McLeod"
"""The human authority who approved the current policy."""

POLICY_AUTHORITY_EMAIL = "stuart@sailratings.com"
"""Contact address for takedown / complaint requests."""

POLICY_USER_AGENT = (
    "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)"
)
"""The User-Agent string that every HTTP request from this platform must send."""


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


class ContentType(str, enum.Enum):
    """The dominant content type a source serves."""

    HTML = "html"
    API = "api"
    PDF = "pdf"
    FILE = "file"
    FEED = "feed"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PolicyVersionMismatchError(Exception):
    """Raised when a source references a policy version ≠ CURRENT_POLICY_VERSION."""

    def __init__(self, slug: str, source_version: str | None = None, message: str | None = None, current_version: str | None = None):
        self.slug = slug
        self.source_version = source_version
        self.current_version = current_version or CURRENT_POLICY_VERSION
        if message:
            super().__init__(message)
        elif source_version is not None:
            super().__init__(
                f"Source '{slug}' references policy_version='{source_version}', "
                f"current is '{self.current_version}'. "
                f"Update the source record or the policy."
            )
        else:
            super().__init__(f"Source '{slug}' has stale policy version")


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


class CollectionWindowClosedError(Exception):
    """Raised when collection is attempted outside the permitted window."""

    def __init__(self, slug: str, detail: str = ""):
        self.slug = slug
        self.detail = detail
        msg = f"Collection window closed for '{slug}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# CollectionRules — per-source enforcement rules (DP-01-05)
# ---------------------------------------------------------------------------


@dataclass
class CollectionRules:
    """Per-source enforcement rules derived from the policy."""

    respect_robots: bool = True
    rate_limit_seconds: float = 2.0
    rate_jitter_seconds: float = 1.0
    collection_window_start: int = 1  # 01:00 local
    collection_window_end: int = 6   # 06:00 local
    use_conditional_requests: bool = True
    max_object_size_mb: int = 25
    max_fetches_per_night: int = 5_000
    max_total_mb_per_night: int = 500
    user_agent: str = POLICY_USER_AGENT
    attribution_header: str | None = None
    no_auth_circumvention: bool = True
    no_personal_data: bool = True
    retention_days: int | None = None
    takedown_contact: str = POLICY_AUTHORITY_EMAIL


# ---------------------------------------------------------------------------
# Policy rule blocks (DP-01-02/03)
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

    user_agent: str = POLICY_USER_AGENT
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
    contact: str = POLICY_AUTHORITY_EMAIL


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
# CollectionPolicyDecisionV1 — global policy config (DP-01-02/03)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionPolicyDecisionV1:
    """DP-01-02 deliverable — the responsible-collection policy decision.

    This is the **global policy config** bundled with every rule block.
    It is immutable (frozen=True) so it can be safely shared across
    threads and workflows without accidental mutation.

    Note: DP-01-05 defines a per-source ``SourceDecisionV1`` (alias
    ``CollectionPolicyDecisionV1`` in that branch) for the output of
    ``resolve_source()``.  Both exist in this merged module.
    """

    version: str = CURRENT_POLICY_VERSION
    approved_on: date = field(default_factory=lambda: date(2026, 9, 2))
    authority: str = POLICY_AUTHORITY

    robots: RobotsRule = field(default_factory=RobotsRule)
    rate: RateRule = field(default_factory=RateRule)
    attribution: AttributionRule = field(default_factory=AttributionRule)
    takedown: TakedownRule = field(default_factory=TakedownRule)
    personal_data: PersonalDataRule = field(default_factory=PersonalDataRule)
    retention: RetentionRule = field(default_factory=RetentionRule)
    collection_window: CollectionWindowRule = field(default_factory=CollectionWindowRule)

    # --- classification table (v1.0 rulings, docs/SOURCE-POLICY.md §3) -----

    source_classes: dict[str, SourceClass] = field(
        default_factory=lambda: {
            "sailsys": SourceClass.PUBLIC,
            # v1.0 §3.3 — public club-published results pages only
            "topyacht": SourceClass.PUBLIC,
            "irc-tcc": SourceClass.PUBLIC,
            # v1.0 §3.3 — public data.orc.org JSON API only; ToS-restricted
            # areas are out of scope
            "orc": SourceClass.PUBLIC,
            "yachtscoring": SourceClass.PUBLIC,
            "manage2sail": SourceClass.PUBLIC,
            "sailwave": SourceClass.PUBLIC,
            "sailing-news": SourceClass.PUBLIC,
            # v1.0 §3.4 — grey-area ruling: approved with special conditions
            "irc-certs": SourceClass.PUBLIC,
            # v1.0 §3.5 — ToS-restricted; rights ruling pending → hold
            "clubspot": SourceClass.UNCLEAR,
            "kwindoo": SourceClass.UNCLEAR,
        }
    )

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

    authenticated_domains: tuple[str, ...] = (
        "app.sailsys.com.au",
    )

    prohibited_domains: tuple[str, ...] = ()

    # Human-readable record of the v1.0 named rulings
    # (docs/SOURCE-POLICY.md §3).  Surfaced in ``to_summary()`` for audit.
    source_rulings: dict[str, str] = field(
        default_factory=lambda: {
            "orc": "approved — public data.orc.org JSON API only; "
                   "ToS-restricted areas excluded (v1.0 §3.3)",
            "topyacht": "approved — public club-published results pages only "
                        "(v1.0 §3.3)",
            "clubspot": "hold — ToS restricts automated access; rights ruling "
                        "pending; discovery metadata only (v1.0 §3.5)",
            "kwindoo": "hold — ToS restricts automated access; rights ruling "
                       "pending; discovery metadata only (v1.0 §3.5)",
            "irc-certs": "approved — grey-area ruling with special conditions: "
                         "attribution header, personal-data redaction, no raw "
                         "PDF redistribution, immediate takedown path (v1.0 §3.4/§6)",
        }
    )

    # --- evaluation helpers ----------------------------------------------

    def classify(self, slug: str | None, domain: str | None = None) -> SourceClassification:
        """Classify a source (and optionally a domain) for collection."""
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
            "source_rulings": dict(self.source_rulings),
            "supersedes": list(SUPERSEDED_POLICY_VERSIONS),
            "policy_doc": "docs/SOURCE-POLICY.md",
        }


# ---------------------------------------------------------------------------
# SourceDecisionV1 — per-source output contract (DP-01-05)
# ---------------------------------------------------------------------------


@dataclass
class SourceDecisionV1:
    """Per-source output contract produced by ``resolve_source()``.

    This is the DP-01-05 deliverable: when a source passes all policy gates,
    ``resolve_source()`` returns a ``SourceDecisionV1`` with ``allowed=True``
    and the enforcement rules the adapter must follow.
    """

    slug: str
    display_name: str
    base_url: str
    category: str
    policy_version: str
    legal_status: str
    source_class: str
    content_type: str
    allowed: bool
    rules: CollectionRules
    robots_disallow: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "base_url": self.base_url,
            "category": self.category,
            "policy_version": self.policy_version,
            "legal_status": self.legal_status,
            "source_class": self.source_class,
            "content_type": self.content_type,
            "allowed": self.allowed,
            "robots_disallow": self.robots_disallow,
            "reason": self.reason,
            "rules": {
                "respect_robots": self.rules.respect_robots,
                "rate_limit_seconds": self.rules.rate_limit_seconds,
                "rate_jitter_seconds": self.rules.rate_jitter_seconds,
                "collection_window_start": self.rules.collection_window_start,
                "collection_window_end": self.rules.collection_window_end,
                "use_conditional_requests": self.rules.use_conditional_requests,
                "max_object_size_mb": self.rules.max_object_size_mb,
                "max_fetches_per_night": self.rules.max_fetches_per_night,
                "max_total_mb_per_night": self.rules.max_total_mb_per_night,
                "user_agent": self.rules.user_agent,
                "attribution_header": self.rules.attribution_header,
                "no_auth_circumvention": self.rules.no_auth_circumvention,
                "no_personal_data": self.rules.no_personal_data,
                "retention_days": self.rules.retention_days,
                "takedown_contact": self.rules.takedown_contact,
            },
        }


# ---------------------------------------------------------------------------
# Module-level singleton (for easy import)
# ---------------------------------------------------------------------------

#: The active policy decision.  Import this everywhere enforcement is needed.
ACTIVE_POLICY: CollectionPolicyDecisionV1 = CollectionPolicyDecisionV1()


# ---------------------------------------------------------------------------
# Collection window helpers (DP-01-05)
# ---------------------------------------------------------------------------


def is_within_collection_window(
    hour: int | None = None,
    *,
    start: int = 1,
    end: int = 6,
) -> bool:
    """Return True if *hour* (0–23) is within the nightly collection window.

    If *hour* is ``None``, the current UTC hour is used.
    """
    if hour is None:
        hour = datetime.now(timezone.utc).hour
    return start <= hour < end


# ---------------------------------------------------------------------------
# Robots helpers (DP-01-05)
# ---------------------------------------------------------------------------


def is_path_disallowed(source: Any, path: str) -> bool:
    """Return True if *path* matches a robots.txt disallow rule for *source*."""
    disallow = getattr(source, "robots_disallow", []) or []
    for pattern in disallow:
        if path.startswith(pattern):
            return True
    return False


def is_path_allowed(source: Any, path: str) -> bool:
    """Return True if *path* is allowed by robots.txt for *source*."""
    return not is_path_disallowed(source, path)


# ---------------------------------------------------------------------------
# Policy summary (DP-01-05)
# ---------------------------------------------------------------------------


def get_policy_summary(sources: list | None = None) -> dict[str, Any]:
    """Return a summary of the current policy for the API and UI."""
    from irc_data.sources.registry import get_in_memory_sources, LegalStatus as _LS

    if sources is None:
        sources = get_in_memory_sources()

    approved = [s for s in sources if getattr(s, "legal_status", None) in (LegalStatus.APPROVED, "approved", _LS.APPROVED)]
    hold = [s for s in sources if getattr(s, "legal_status", None) in (LegalStatus.HOLD, "hold", _LS.HOLD)]
    return {
        "version": CURRENT_POLICY_VERSION,
        "approved_date": POLICY_APPROVED_DATE,
        "authority": POLICY_AUTHORITY,
        "authority_email": POLICY_AUTHORITY_EMAIL,
        "user_agent": POLICY_USER_AGENT,
        "issue_label": "DP-01-02",
        "spec_reference": "SPEC-012",
        "counts": {
            "approved": len(approved),
            "hold": len(hold),
            "blocked": len(sources) - len(approved) - len(hold),
            "total": len(sources),
        },
    }


# ---------------------------------------------------------------------------
# classify_source helper (DP-01-05)
# ---------------------------------------------------------------------------


def classify_source(source: Any) -> tuple[str, str]:
    """Return ``(source_class, classification_label)`` for *source*.

    Returns ``("public", "approved")`` etc.
    """
    legal_status = getattr(source, "legal_status", None)
    source_class = getattr(source, "source_class", SourceClass.PUBLIC.value)
    enabled = getattr(source, "enabled", True)

    status_val = legal_status.value if hasattr(legal_status, "value") else (legal_status or "")
    sc_val = source_class.value if hasattr(source_class, "value") else (source_class or "")

    if status_val == "approved" and enabled:
        return sc_val, "approved"
    if status_val == "hold":
        return sc_val, "hold"
    if status_val == "blocked":
        return sc_val, "blocked"
    return sc_val, "unknown"


# ---------------------------------------------------------------------------
# resolve_source — DP-01-05's per-source policy resolution
# ---------------------------------------------------------------------------


def resolve_source(slug: str, db: Any = None) -> SourceDecisionV1:
    """Resolve a source through every policy gate.

    Returns a :class:`SourceDecisionV1` when the source passes all checks.
    Raises ``PolicyVersionMismatchError`` or ``SourceNotApprovedError`` if not.
    """
    from irc_data.sources.registry import get_source as _get_source

    source = _get_source(db, slug)

    # Policy version gate
    pv = getattr(source, "policy_version", None)
    if pv != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(slug, pv)

    # Approval gate
    enabled = getattr(source, "enabled", True)
    legal_status = getattr(source, "legal_status", None)
    status_val = legal_status.value if hasattr(legal_status, "value") else (legal_status or "")

    if not enabled:
        raise SourceNotApprovedError(slug, reason="source is disabled (kill switch active)")
    if status_val != "approved":
        raise SourceNotApprovedError(slug, reason=f"legal_status={status_val}")

    quarantined = getattr(source, "quarantine_until", None)
    if quarantined:
        raise SourceNotApprovedError(slug, reason="source is quarantined")

    rules = CollectionRules()
    if slug == "irc-certs":
        rules.attribution_header = "X-SailRatings-Source: irc-certs"

    return SourceDecisionV1(
        slug=slug,
        display_name=getattr(source, "display_name", slug),
        base_url=getattr(source, "base_url", ""),
        category=getattr(source, "category", ""),
        policy_version=pv or CURRENT_POLICY_VERSION,
        legal_status=status_val,
        source_class=getattr(source, "source_class", SourceClass.PUBLIC.value),
        content_type=getattr(source, "content_type", ContentType.HTML.value),
        allowed=True,
        rules=rules,
        robots_disallow=list(getattr(source, "robots_disallow", []) or []),
    )


# ---------------------------------------------------------------------------
# Backward-compatibility helpers (used by existing scrapers)
# ---------------------------------------------------------------------------


def assert_policy_current(source: Any) -> None:
    """Raise ``PolicyVersionMismatchError`` if the source's policy is stale."""
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


def assert_source_approved(source: Any) -> None:
    """Raise ``SourceNotApprovedError`` if the source is not collectable."""
    slug = getattr(source, "slug", "<unknown>")
    enabled = getattr(source, "enabled", True)
    legal_status = getattr(source, "legal_status", None)

    if not enabled:
        raise SourceNotApprovedError(slug, reason="source is disabled (kill switch)")

    status_val = legal_status.value if hasattr(legal_status, "value") else legal_status
    if status_val != "approved":
        raise SourceNotApprovedError(
            slug,
            reason=f"legal_status is '{status_val}', must be 'approved'",
        )


def assert_source_collectable(source: Any) -> None:
    """Full policy gate: version + approval + enabled."""
    assert_policy_current(source)
    assert_source_approved(source)


def is_current_policy_version(version: str) -> bool:
    """Return True if *version* matches ``CURRENT_POLICY_VERSION``."""
    return version == CURRENT_POLICY_VERSION
