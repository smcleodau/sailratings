"""Collection gate — responsible-collection enforcement (DP-01-02).

The ``CollectionGate`` is the single enforcement point every adapter must
pass through before issuing HTTP requests.  It enforces:

1. **Policy version** — the source record must reference the current
   approved policy version.  If not, ``PolicyVersionMismatchError`` is
   raised and the adapter cannot run.
2. **Source approval** — the source must have ``legal_status = approved``
   and ``enabled = True``.  Hold / blocked / disabled sources are rejected.
3. **Emergency disable** — by source slug **and** by domain.  Either
   dimension blocks collection immediately.
4. **Robots.txt compliance** — URLs matching disallow rules are rejected.
5. **Source classification** — prohibited / unclear sources are rejected.
6. **Collection window** — collection outside the nightly window is
   rejected (with an escape hatch for lightweight health checks).
7. **Rate limiting** — per-domain rate limit with jitter is enforced.

The gate is designed to work with or without a database.  When a real DB
session is available it queries ``data_sources``; otherwise it falls back
to the in-memory ``SourceRecord`` fixtures.  This makes it fully testable
without infrastructure.
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence
from urllib.parse import urlparse

from irc_data.sources.policy import (
    CollectionPolicyDecisionV1,
    CURRENT_POLICY_VERSION,
    LegalStatus,
    SourceClass,
    SourceClassification,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    ProhibitedCollectionError,
)
from irc_data.sources.robots import RobotsRules, check_url_against_robots


# ---------------------------------------------------------------------------
# Source record (in-memory representation; mirrors data_sources table)
# ---------------------------------------------------------------------------


@dataclass
class SourceRecord:
    """In-memory representation of a ``data_sources`` row.

    When a real DB session is available, the gate fetches a row and
    constructs this object.  For testing, fixtures provide ``SourceRecord``
    instances directly.
    """

    slug: str
    display_name: str
    base_url: str
    category: str  # results | ratings | certificates | news
    policy_version: str = CURRENT_POLICY_VERSION
    legal_status: LegalStatus = LegalStatus.APPROVED
    enabled: bool = True
    robots_disallow: list[str] = field(default_factory=list)
    robots_checked_at: datetime | None = None
    quarantine_until: datetime | None = None
    contact_email: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Gate decision
# ---------------------------------------------------------------------------


@dataclass
class GateDecision:
    """Result of a gate evaluation — tells the adapter whether to proceed."""

    allowed: bool
    source: SourceRecord | None = None
    classification: SourceClassification | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


# ---------------------------------------------------------------------------
# Collection gate
# ---------------------------------------------------------------------------


class CollectionGate:
    """Enforcement gate for responsible data collection.

    Usage (adapter pattern)::

        gate = CollectionGate(policy)
        source = gate.resolve_source("sailsys")
        decision = gate.check_url(source, "https://app.sailsys.com.au/results/123")
        if not decision:
            raise ProhibitedCollectionError(url)
        await gate.rate_limiter_wait(domain)

    The gate maintains:
    * ``_emergency_disabled_sources`` — set of slugs disabled at runtime
    * ``_emergency_disabled_domains`` — set of domains disabled at runtime
    * ``_robots_cache`` — domain → RobotsRules cache
    * ``_rate_last_request`` — domain → monotonic timestamp for rate limiting
    """

    def __init__(
        self,
        policy: CollectionPolicyDecisionV1 | None = None,
        sources: Sequence[SourceRecord] | None = None,
    ):
        self.policy = policy or CollectionPolicyDecisionV1()
        self._sources: dict[str, SourceRecord] = {}
        if sources:
            for s in sources:
                self._sources[s.slug] = s

        # Emergency disable (runtime, immediate)
        self._emergency_disabled_sources: set[str] = set()
        self._emergency_disabled_domains: set[str] = set()

        # Global kill switch
        self._collection_enabled: bool = True

        # Robots cache: domain → RobotsRules
        self._robots_cache: dict[str, RobotsRules] = {}

        # Rate limiting: domain → last_request_monotonic
        self._rate_last_request: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Source resolution
    # ------------------------------------------------------------------

    def register_source(self, source: SourceRecord) -> None:
        """Register or update an in-memory source record."""
        self._sources[source.slug] = source

    def resolve_source(self, slug: str) -> SourceRecord:
        """Resolve a source record and assert policy version + approval.

        Raises:
            SourceNotApprovedError: if source not found, not approved, or disabled.
            PolicyVersionMismatchError: if source policy_version ≠ current.
        """
        source = self._sources.get(slug)
        if source is None:
            raise SourceNotApprovedError(slug, "No source record found")

        # 1. Policy version gate
        self.policy.assert_version(source.policy_version, slug)

        # 2. Emergency disable by source (checked before the enabled flag so
        #    the error message is specific to the takedown/kill-switch path).
        if slug in self._emergency_disabled_sources:
            raise SourceNotApprovedError(slug, "emergency disabled by source")

        # 3. Legal status gate
        if source.legal_status != LegalStatus.APPROVED:
            raise SourceNotApprovedError(
                slug, f"legal_status={source.legal_status.value}"
            )

        # 4. Enabled gate (DB kill switch)
        if not source.enabled:
            raise SourceNotApprovedError(slug, "source is disabled (enabled=False)")

        # 5. Quarantine check
        if source.quarantine_until:
            now = datetime.now(timezone.utc)
            if source.quarantine_until > now:
                raise SourceNotApprovedError(
                    slug,
                    f"quarantined until {source.quarantine_until.isoformat()}",
                )

        return source

    # ------------------------------------------------------------------
    # Emergency disable
    # ------------------------------------------------------------------

    def emergency_disable_source(self, slug: str, reason: str = "") -> None:
        """Immediately disable collection for a source slug.

        This is the takedown / complaint kill switch.  It takes effect on
        the next ``resolve_source`` call — no DB round-trip required.
        """
        self._emergency_disabled_sources.add(slug)
        # Also update the source record if it exists
        if slug in self._sources:
            self._sources[slug].enabled = False

    def emergency_disable_domain(self, domain: str, reason: str = "") -> None:
        """Immediately disable collection for a domain.

        All URLs whose host matches *domain* (or is a subdomain) will be
        rejected by ``check_url``.
        """
        self._emergency_disabled_domains.add(domain.lower())

    def emergency_enable_source(self, slug: str) -> None:
        """Re-enable a source that was emergency-disabled."""
        self._emergency_disabled_sources.discard(slug)
        if slug in self._sources:
            self._sources[slug].enabled = True

    def emergency_enable_domain(self, domain: str) -> None:
        """Re-enable a domain that was emergency-disabled."""
        self._emergency_disabled_domains.discard(domain.lower())

    def set_collection_enabled(self, enabled: bool) -> None:
        """Global kill switch — halts all collection."""
        self._collection_enabled = enabled

    def is_source_disabled(self, slug: str) -> bool:
        """Check if a source is emergency-disabled."""
        return slug in self._emergency_disabled_sources

    def is_domain_disabled(self, domain: str) -> bool:
        """Check if a domain is emergency-disabled (exact or subdomain match)."""
        domain_lower = domain.lower()
        for d in self._emergency_disabled_domains:
            if domain_lower == d or domain_lower.endswith("." + d):
                return True
        return False

    # ------------------------------------------------------------------
    # URL-level checks
    # ------------------------------------------------------------------

    def check_url(
        self,
        source: SourceRecord,
        url: str,
        robots_rules: RobotsRules | None = None,
        is_health_check: bool = False,
    ) -> GateDecision:
        """Evaluate whether *url* may be collected from *source*.

        This performs all per-URL checks:
        1. Global collection enabled
        2. Emergency domain disable
        3. Source classification (prohibited / unclear)
        4. Robots.txt compliance
        5. Collection window (unless health check)

        Returns a ``GateDecision`` with ``allowed=True/False`` and a reason.
        """
        # 1. Global kill switch
        if not self._collection_enabled:
            return GateDecision(allowed=False, source=source,
                                reason="Global collection is disabled")

        parsed = urlparse(url)
        domain = parsed.hostname or ""

        # 2. Emergency domain disable
        if self.is_domain_disabled(domain):
            return GateDecision(
                allowed=False,
                source=source,
                reason=f"Domain '{domain}' is emergency-disabled",
            )

        # 3. Source classification
        classification = self.policy.classify(source.slug, domain)
        if classification.source_class == SourceClass.PROHIBITED:
            return GateDecision(
                allowed=False,
                source=source,
                classification=classification,
                reason=f"Source classified as prohibited: {classification.reason}",
            )
        if classification.source_class == SourceClass.UNCLEAR:
            return GateDecision(
                allowed=False,
                source=source,
                classification=classification,
                reason=f"Source classified as unclear: {classification.reason}",
            )

        # 4. Robots.txt compliance
        if robots_rules is None:
            robots_rules = self._robots_cache.get(domain)
        if robots_rules is not None:
            ua = self.policy.attribution.user_agent
            if not check_url_against_robots(url, robots_rules, ua):
                return GateDecision(
                    allowed=False,
                    source=source,
                    classification=classification,
                    reason=f"URL disallowed by robots.txt: {url}",
                )
        elif source.robots_disallow:
            # Fall back to cached disallow paths from the source record
            path = parsed.path or "/"
            for pattern in source.robots_disallow:
                if path.startswith(pattern):
                    return GateDecision(
                        allowed=False,
                        source=source,
                        classification=classification,
                        reason=f"URL matches cached disallow pattern '{pattern}'",
                    )

        # 5. Collection window (skip for health checks)
        if not is_health_check:
            window = self.policy.collection_window
            now = datetime.now(timezone.utc)
            hour = now.hour
            if not (window.start_hour_local <= hour < window.end_hour_local):
                return GateDecision(
                    allowed=False,
                    source=source,
                    classification=classification,
                    reason=(
                        f"Outside collection window "
                        f"({window.start_hour_local:02d}:00–{window.end_hour_local:02d}:00)"
                    ),
                )

        return GateDecision(
            allowed=True,
            source=source,
            classification=classification,
        )

    # ------------------------------------------------------------------
    # Robots cache
    # ------------------------------------------------------------------

    def cache_robots(self, domain: str, rules: RobotsRules) -> None:
        """Cache parsed robots.txt rules for a domain."""
        self._robots_cache[domain.lower()] = rules

    def get_robots(self, domain: str) -> RobotsRules | None:
        """Return cached robots rules for *domain*, or None."""
        return self._robots_cache.get(domain.lower())

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def rate_limit_wait(self, domain: str) -> float:
        """Enforce per-domain rate limit.  Returns seconds slept.

        Uses the policy's ``RateRule``: min_delay + jitter per domain.
        """
        rate = self.policy.rate
        domain_lower = domain.lower()
        now = time.monotonic()
        last = self._rate_last_request.get(domain_lower, 0.0)
        elapsed = now - last
        delay = rate.min_delay_seconds + random.uniform(0, rate.jitter_seconds)

        if elapsed < delay:
            sleep_time = delay - elapsed
            time.sleep(sleep_time)
            self._rate_last_request[domain_lower] = time.monotonic()
            return sleep_time

        self._rate_last_request[domain_lower] = now
        return 0.0

    async def rate_limit_wait_async(self, domain: str) -> float:
        """Async version of ``rate_limit_wait``."""
        import asyncio

        rate = self.policy.rate
        domain_lower = domain.lower()
        now = time.monotonic()
        last = self._rate_last_request.get(domain_lower, 0.0)
        elapsed = now - last
        delay = rate.min_delay_seconds + random.uniform(0, rate.jitter_seconds)

        if elapsed < delay:
            sleep_time = delay - elapsed
            await asyncio.sleep(sleep_time)
            self._rate_last_request[domain_lower] = time.monotonic()
            return sleep_time

        self._rate_last_request[domain_lower] = now
        return 0.0

    # ------------------------------------------------------------------
    # Object size and fetch count enforcement
    # ------------------------------------------------------------------

    def check_object_size(self, content_length: int) -> bool:
        """Return True if *content_length* (bytes) is within the size cap."""
        max_bytes = self.policy.retention.max_object_size_mb * 1024 * 1024
        return content_length <= max_bytes

    # ------------------------------------------------------------------
    # Full evaluation (convenience)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        slug: str,
        url: str,
        robots_rules: RobotsRules | None = None,
        is_health_check: bool = False,
    ) -> GateDecision:
        """One-shot: resolve source + check URL.

        This is the convenience method most adapters will call::

            decision = gate.evaluate("sailsys", url, robots_rules)
            if not decision:
                log.warning(f"Blocked: {decision.reason}")
                return
            await gate.rate_limit_wait_async(domain)
            # ... proceed with fetch ...
        """
        try:
            source = self.resolve_source(slug)
        except (SourceNotApprovedError, PolicyVersionMismatchError) as exc:
            return GateDecision(allowed=False, reason=str(exc))

        return self.check_url(source, url, robots_rules, is_health_check)
