"""Responsible collection policy and enforcement gate (DP-01-02).

Implements the policy version gate, source approval gate, emergency disable,
and the ``CollectionPolicyDecisionV1`` output contract.

Every adapter MUST call ``resolve_source()`` (or ``assert_policy_current()``
plus the approval check) before the first fetch.  If the policy version is
stale, the source is on hold / blocked / disabled / quarantined, or the
collection window is closed, a ``SourceNotApprovedError`` is raised and the
adapter aborts.

Policy text: ``docs/INTERIM-POLICY.md`` (interim-v0, approved 2026-08-30).
Spec reference: SPEC-012 §3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ── Policy constants ────────────────────────────────────────────────────────

CURRENT_POLICY_VERSION = "interim-v0"
"""The policy version that every active ``data_sources`` row must reference."""

POLICY_APPROVED_DATE = "2026-08-30"
"""Human-readable approval date of the current policy."""

POLICY_AUTHORITY = "Stuart McLeod"
"""The human authority who approved the interim policy."""

POLICY_AUTHORITY_EMAIL = "stuart@sailratings.com"
"""Contact address for takedown / complaint requests."""

POLICY_USER_AGENT = (
    "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)"
)
"""The User-Agent string that every HTTP request from this platform must send."""


# ── Enums ───────────────────────────────────────────────────────────────────


class LegalStatus(str, Enum):
    """The legal / approval standing of a source."""

    APPROVED = "approved"
    HOLD = "hold"
    BLOCKED = "blocked"


class SourceClass(str, Enum):
    """Classification of *how* a source publishes its content.

    ``public``       — no authentication; publicly accessible.
    ``authenticated`` — requires login but collection is permitted under ToS.
    ``licensed``     — data obtained under a written licence / agreement.
    ``prohibited``   — login wall, paywall, or CAPTCHA; collection forbidden.
    ``unclear``      — rights ruling pending; metadata only.
    """

    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    LICENSED = "licensed"
    PROHIBITED = "prohibited"
    UNCLEAR = "unclear"


class ContentType(str, Enum):
    """The dominant content type a source serves."""

    HTML = "html"
    API = "api"
    PDF = "pdf"
    FILE = "file"
    FEED = "feed"


# ── Exceptions ───────────────────────────────────────────────────────────────


class PolicyVersionMismatchError(Exception):
    """Raised when a source references a stale policy version."""

    def __init__(self, slug: str, source_version: str, current_version: str):
        self.slug = slug
        self.source_version = source_version
        self.current_version = current_version
        super().__init__(
            f"{slug} references policy_version={source_version!r}, "
            f"current is {current_version!r}"
        )


class SourceNotApprovedError(Exception):
    """Raised when a source is not approved for content collection."""

    def __init__(self, slug: str, reason: str = ""):
        self.slug = slug
        self.reason = reason
        msg = f"Source {slug!r} is not approved for collection"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class CollectionWindowClosedError(Exception):
    """Raised when collection is attempted outside the permitted window."""

    def __init__(self, slug: str, detail: str = ""):
        self.slug = slug
        self.detail = detail
        msg = f"Collection window closed for {slug!r}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class CollectionRules:
    """Per-source enforcement rules derived from the policy."""

    respect_robots: bool = True
    rate_limit_seconds: float = 2.0
    rate_jitter_seconds: float = 1.0
    collection_window_start: int = 1  # 01:00 local
    collection_window_end: int = 6  # 06:00 local
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


@dataclass
class DataSource:
    """An in-memory representation of a ``data_sources`` row.

    This mirrors the database schema from SPEC-012 §2.1 so that the policy
    gate can operate without a live database connection (essential for tests
    and for the adapter SDK's ``__init__`` resolution step).
    """

    slug: str
    display_name: str
    base_url: str
    category: str
    adapter_class: str | None = None
    policy_version: str = CURRENT_POLICY_VERSION
    legal_status: str = LegalStatus.APPROVED.value
    source_class: str = SourceClass.PUBLIC.value
    content_type: str = ContentType.HTML.value
    robots_checked_at: str | None = None
    robots_disallow: list[str] = field(default_factory=list)
    contact_email: str | None = None
    notes: str | None = None
    enabled: bool = True
    quarantined: bool = False
    quarantine_until: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    # ── Convenience properties ────────────────────────────────────────────

    @property
    def is_approved(self) -> bool:
        return self.legal_status == LegalStatus.APPROVED.value

    @property
    def is_hold(self) -> bool:
        return self.legal_status == LegalStatus.HOLD.value

    @property
    def is_blocked(self) -> bool:
        return self.legal_status == LegalStatus.BLOCKED.value

    @property
    def is_clear(self) -> bool:
        """True when the source is approved, enabled, and not quarantined."""
        return self.is_approved and self.enabled and not self.quarantined


@dataclass
class CollectionPolicyDecisionV1:
    """Output contract for a resolved source (DP-01-02 handoff).

    Produced by ``resolve_source()`` when a source passes every gate.
    Adapters consume this object to configure their fetch behaviour.
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


# ── Seed sources (interim-v0) ────────────────────────────────────────────────

# The 11 approved / hold sources from SPEC-012 §2.2 and INTERIM-POLICY.md §2.
SEED_SOURCES: list[DataSource] = [
    DataSource(
        slug="sailsys",
        display_name="SailSys",
        base_url="https://www.sailsys.com.au",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Australian race management; publicly published results",
    ),
    DataSource(
        slug="topyacht",
        display_name="TopYacht",
        base_url="https://www.topyacht.com.au",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Australian race management; publicly published results",
    ),
    DataSource(
        slug="irc-tcc",
        display_name="IRC TCC Listings",
        base_url="https://ircrating.org",
        category="ratings",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.FILE.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Published for racing administration; CSV download from ircrating.org",
    ),
    DataSource(
        slug="orc",
        display_name="ORC",
        base_url="https://data.orc.org",
        category="ratings",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.API.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Published for racing administration; JSON API from data.orc.org",
    ),
    DataSource(
        slug="yachtscoring",
        display_name="Yacht Scoring",
        base_url="https://www.yachtscoring.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="US/international race results; publicly published",
    ),
    DataSource(
        slug="manage2sail",
        display_name="Manage2Sail",
        base_url="https://manage2sail.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="European race management; publicly published results",
    ),
    DataSource(
        slug="sailwave",
        display_name="Sailwave",
        base_url="https://www.sailwave.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.FILE.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Results files publicly linked from club sites",
    ),
    DataSource(
        slug="sailing-news",
        display_name="Sailing News Feeds",
        base_url="https://www.sailingnews.com",
        category="news",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.FEED.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="RSS/Atom feeds; explicitly published for syndication",
    ),
    DataSource(
        slug="irc-certs",
        display_name="IRC Certificate PDFs",
        base_url="https://ircrating.org/pdfdirectory",
        category="certificates",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.PDF.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Published for racing administration; publicly accessible; core platform data. Attribution: X-SailRatings-Source: irc-certs",
    ),
    DataSource(
        slug="clubspot",
        display_name="ClubSpot",
        base_url="https://clubspot.com",
        category="results",
        source_class=SourceClass.UNCLEAR.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.HOLD.value,
        notes="Rights ruling pending; ToS review incomplete",
    ),
    DataSource(
        slug="kwindoo",
        display_name="Kwindoo",
        base_url="https://www.kwindoo.com",
        category="results",
        source_class=SourceClass.UNCLEAR.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.HOLD.value,
        notes="Rights ruling pending; ToS review incomplete",
    ),
]


# ── Test fixtures (not part of the public registry) ─────────────────────────
#
# These sources exist so that the policy tests can exercise login-wall,
# robots-disallow, and other edge cases without polluting the seed list.

FIXTURE_SOURCES: list[DataSource] = [
    DataSource(
        slug="fixture-login-wall",
        display_name="Login Wall Fixture",
        base_url="https://login-wall.example.com",
        category="results",
        source_class=SourceClass.PROHIBITED.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.BLOCKED.value,
        notes="Login wall — collection prohibited",
    ),
    DataSource(
        slug="fixture-paywall",
        display_name="Paywall Fixture",
        base_url="https://paywall.example.com",
        category="news",
        source_class=SourceClass.PROHIBITED.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.BLOCKED.value,
        notes="Paywall — collection prohibited",
    ),
    DataSource(
        slug="fixture-disallow",
        display_name="Robots Disallow Fixture",
        base_url="https://disallow.example.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        robots_disallow=["/private", "/admin", "/results/secret"],
        notes="Public source but robots.txt disallows certain paths",
    ),
    DataSource(
        slug="fixture-licensed",
        display_name="Licensed Data Fixture",
        base_url="https://licensed.example.com",
        category="ratings",
        source_class=SourceClass.LICENSED.value,
        content_type=ContentType.API.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Data obtained under written licence agreement",
    ),
    DataSource(
        slug="fixture-authenticated",
        display_name="Authenticated API Fixture",
        base_url="https://auth.example.com",
        category="results",
        source_class=SourceClass.AUTHENTICATED.value,
        content_type=ContentType.API.value,
        legal_status=LegalStatus.APPROVED.value,
        notes="Requires login but collection permitted under ToS",
    ),
    DataSource(
        slug="fixture-quarantined",
        display_name="Quarantined Fixture",
        base_url="https://quarantine.example.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        quarantined=True,
        quarantine_until="2099-01-01T00:00:00Z",
        notes="Quarantined due to structure change incident",
    ),
    DataSource(
        slug="fixture-stale-version",
        display_name="Stale Policy Version Fixture",
        base_url="https://stale.example.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        policy_version="interim-v-unknown",
        notes="References a stale policy version",
    ),
    DataSource(
        slug="fixture-disabled",
        display_name="Disabled Fixture",
        base_url="https://disabled.example.com",
        category="results",
        source_class=SourceClass.PUBLIC.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.APPROVED.value,
        enabled=False,
        notes="Kill switch activated",
    ),
    DataSource(
        slug="fixture-blocked",
        display_name="Blocked Fixture",
        base_url="https://blocked.example.com",
        category="results",
        source_class=SourceClass.PROHIBITED.value,
        content_type=ContentType.HTML.value,
        legal_status=LegalStatus.BLOCKED.value,
        notes="Explicitly blocked",
    ),
]

ALL_SOURCES: list[DataSource] = SEED_SOURCES + FIXTURE_SOURCES


# ── Registry helpers ─────────────────────────────────────────────────────────

_SOURCE_INDEX: dict[str, DataSource] = {s.slug: s for s in ALL_SOURCES}


def get_source(slug: str, db: Any = None) -> DataSource:
    """Return the ``DataSource`` for *slug*.

    If *db* is provided, a real database lookup could be performed, but for
    the interim-v0 implementation the in-memory registry is authoritative.
    Raises ``KeyError`` if the slug is not found.
    """
    if slug not in _SOURCE_INDEX:
        raise KeyError(f"Unknown source slug: {slug!r}")
    return _SOURCE_INDEX[slug]


def list_sources(include_fixtures: bool = False) -> list[DataSource]:
    """Return all registered sources (seed only unless *include_fixtures*)."""
    if include_fixtures:
        return list(ALL_SOURCES)
    return list(SEED_SOURCES)


def list_all_sources() -> list[DataSource]:
    """Return every source including fixtures."""
    return list(ALL_SOURCES)


# ── Policy gates ─────────────────────────────────────────────────────────────


def is_current_policy_version(version: str) -> bool:
    """Return True if *version* matches ``CURRENT_POLICY_VERSION``."""
    return version == CURRENT_POLICY_VERSION


def assert_policy_current(source: DataSource) -> None:
    """Raise ``PolicyVersionMismatchError`` if *source* is on a stale policy."""
    if source.policy_version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(
            slug=source.slug,
            source_version=source.policy_version,
            current_version=CURRENT_POLICY_VERSION,
        )


def _build_rules(source: DataSource) -> CollectionRules:
    """Derive the enforcement rules for *source* from the policy."""
    rules = CollectionRules()
    # IRC certs get a special attribution header
    if source.slug == "irc-certs":
        rules.attribution_header = "X-SailRatings-Source: irc-certs"
    return rules


def classify_source(source: DataSource) -> tuple[str, str]:
    """Return ``(source_class, classification_label)`` for *source*.

    The classification label is a human-readable string used in the UI:
    ``"approved"``, ``"hold"``, ``"blocked"``, or ``"unclear"``.
    """
    if source.is_approved:
        return source.source_class, "approved"
    if source.is_hold:
        return source.source_class, "hold"
    if source.is_blocked:
        return source.source_class, "blocked"
    return source.source_class, "unknown"


def resolve_source(slug: str, db: Any = None) -> CollectionPolicyDecisionV1:
    """Resolve a source through every policy gate.

    This is the primary entry point for adapters.  It performs:

    1. **Policy version gate** — raises ``PolicyVersionMismatchError`` if the
       source references a stale policy version.
    2. **Approval gate** — raises ``SourceNotApprovedError`` if the source is
       on hold, blocked, disabled, quarantined, or unknown.

    On success, returns a ``CollectionPolicyDecisionV1`` with ``allowed=True``
    and the enforcement rules the adapter must follow.
    """
    source = get_source(slug, db)

    # Gate 1 — policy version
    assert_policy_current(source)

    # Gate 2 — approval / enable / quarantine
    if not source.is_approved:
        raise SourceNotApprovedError(
            slug,
            reason=f"legal_status={source.legal_status}",
        )
    if not source.enabled:
        raise SourceNotApprovedError(
            slug,
            reason="source is disabled (kill switch active)",
        )
    if source.quarantined:
        raise SourceNotApprovedError(
            slug,
            reason="source is quarantined",
        )

    rules = _build_rules(source)
    return CollectionPolicyDecisionV1(
        slug=source.slug,
        display_name=source.display_name,
        base_url=source.base_url,
        category=source.category,
        policy_version=source.policy_version,
        legal_status=source.legal_status,
        source_class=source.source_class,
        content_type=source.content_type,
        allowed=True,
        rules=rules,
        robots_disallow=list(source.robots_disallow),
    )


# ── Emergency disable ────────────────────────────────────────────────────────


def emergency_disable_source(slug: str) -> DataSource:
    """Activate the kill switch for *slug* in the in-memory registry.

    This is the emergency-disable path: the source is immediately marked
    ``enabled=False``.  Any subsequent ``resolve_source()`` call will raise
    ``SourceNotApprovedError``.
    """
    source = get_source(slug)
    source.enabled = False
    return source


def emergency_disable_domain(domain: str) -> list[DataSource]:
    """Disable every source whose ``base_url`` host matches *domain*.

    Returns the list of affected sources.
    """
    affected: list[DataSource] = []
    for source in ALL_SOURCES:
        if domain in source.base_url:
            source.enabled = False
            affected.append(source)
    return affected


def is_source_enabled(slug: str) -> bool:
    """Return True if *slug* is still enabled (kill switch not active)."""
    source = get_source(slug)
    return source.enabled


def is_domain_enabled(domain: str) -> bool:
    """Return True if at least one source for *domain* is enabled."""
    for source in ALL_SOURCES:
        if domain in source.base_url and source.enabled:
            return True
    return False


# ─ Robots helpers ────────────────────────────────────────────────────────────


def is_path_disallowed(source: DataSource, path: str) -> bool:
    """Return True if *path* matches a robots.txt disallow rule for *source*."""
    for pattern in source.robots_disallow:
        if path.startswith(pattern):
            return True
    return False


def is_path_allowed(source: DataSource, path: str) -> bool:
    """Return True if *path* is allowed by robots.txt for *source*."""
    return not is_path_disallowed(source, path)


# ─ Collection window helpers ─────────────────────────────────────────────────


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


# ─ Policy summary (for the API / UI) ─────────────────────────────────────────


def get_policy_summary() -> dict[str, Any]:
    """Return a summary of the current policy for the API and UI."""
    approved = [s for s in SEED_SOURCES if s.is_approved]
    hold = [s for s in SEED_SOURCES if s.is_hold]
    blocked = [s for s in SEED_SOURCES if s.is_blocked]
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
            "blocked": len(blocked),
            "total": len(SEED_SOURCES),
        },
        "sources": [_source_to_dict(s) for s in SEED_SOURCES],
    }


def _source_to_dict(source: DataSource) -> dict[str, Any]:
    """Serialise a ``DataSource`` to a flat dict for JSON responses."""
    source_class, classification = classify_source(source)
    return {
        "slug": source.slug,
        "display_name": source.display_name,
        "base_url": source.base_url,
        "category": source.category,
        "policy_version": source.policy_version,
        "legal_status": source.legal_status,
        "source_class": source_class,
        "content_type": source.content_type,
        "classification": classification,
        "enabled": source.enabled,
        "quarantined": source.quarantined,
        "robots_disallow": list(source.robots_disallow),
        "notes": source.notes,
    }
