"""Tests for the responsible-collection policy (DP-01-02).

Verifies:
* Policy version gate — adapter cannot run without approved policy version
* Emergency disable by source and domain
* Source classification: public, authenticated, licensed, prohibited, unclear
* Robots.txt compliance (disallow, allow, wildcard, empty)
* Rate limiting with jitter
* Object size enforcement
* Policy fixtures exercise all required cases
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from irc_data.sources.policy import (
    ACTIVE_POLICY,
    CURRENT_POLICY_VERSION,
    CollectionPolicyDecisionV1,
    LegalStatus,
    SourceClass,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    ProhibitedCollectionError,
)
from irc_data.sources.gate import (
    CollectionGate,
    GateDecision,
    SourceRecord,
)
from irc_data.sources.robots import parse_robots_txt, RobotsRules, check_url_against_robots

from tests.sources.fixtures import (
    POLICY,
    ALL_FIXTURES,
    public_html_source,
    api_source,
    pdf_source,
    login_wall_source,
    disallow_source,
    unclear_source,
    disabled_source,
    stale_policy_source,
    quarantined_source,
    public_html_robots,
    disallow_robots,
    all_disallowed_robots,
    empty_robots,
    PUBLIC_HTML_URL,
    API_URL,
    PDF_URL,
    LOGIN_WALL_URL,
    DISALLOW_URL,
    UNCLEAR_URL,
)


# ---------------------------------------------------------------------------
# Policy version gate tests
# ---------------------------------------------------------------------------


class TestPolicyVersionGate:
    """AC: Adapter cannot run without approved policy version."""

    def test_current_policy_version_is_interim_v0(self):
        assert CURRENT_POLICY_VERSION == "interim-v0"

    def test_policy_has_correct_version(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.version == CURRENT_POLICY_VERSION

    def test_assert_version_passes_for_matching_version(self):
        policy = CollectionPolicyDecisionV1()
        policy.assert_version(CURRENT_POLICY_VERSION, "sailsys")

    def test_assert_version_raises_on_mismatch(self):
        policy = CollectionPolicyDecisionV1()
        with pytest.raises(PolicyVersionMismatchError) as exc_info:
            policy.assert_version("interim-v0.9-obsolete", "sailsys")
        assert "sailsys" in str(exc_info.value)
        assert "interim-v0.9-obsolete" in str(exc_info.value)

    def test_resolve_source_raises_on_stale_policy_version(self):
        """The adapter CANNOT run without an approved policy version."""
        gate = CollectionGate(policy=POLICY, sources=[stale_policy_source()])
        with pytest.raises(PolicyVersionMismatchError):
            gate.resolve_source("sailsys")

    def test_is_current_returns_true_for_matching(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.is_current(CURRENT_POLICY_VERSION) is True

    def test_is_current_returns_false_for_mismatch(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.is_current("v2.0") is False


# ---------------------------------------------------------------------------
# Source approval gate tests
# ---------------------------------------------------------------------------


class TestSourceApprovalGate:
    """AC: SourceNotApprovedError raised for hold / blocked / disabled sources."""

    def test_approved_source_resolves(self):
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        source = gate.resolve_source("sailsys")
        assert source.slug == "sailsys"
        assert source.legal_status == LegalStatus.APPROVED

    def test_hold_source_raises(self):
        """Hold sources must not be collectible."""
        gate = CollectionGate(policy=POLICY, sources=[unclear_source()])
        with pytest.raises(SourceNotApprovedError):
            gate.resolve_source("clubspot")

    def test_blocked_source_raises(self):
        """Blocked sources must not be collectible."""
        gate = CollectionGate(policy=POLICY, sources=[login_wall_source()])
        with pytest.raises(SourceNotApprovedError):
            gate.resolve_source("private-regatta")

    def test_disabled_source_raises(self):
        """Disabled sources (enabled=False) must not be collectible."""
        gate = CollectionGate(policy=POLICY, sources=[disabled_source()])
        with pytest.raises(SourceNotApprovedError):
            gate.resolve_source("kwindoo")

    def test_unknown_source_raises(self):
        gate = CollectionGate(policy=POLICY, sources=[])
        with pytest.raises(SourceNotApprovedError):
            gate.resolve_source("nonexistent")

    def test_quarantined_source_raises(self):
        gate = CollectionGate(policy=POLICY, sources=[quarantined_source()])
        with pytest.raises(SourceNotApprovedError, match="quarantined"):
            gate.resolve_source("topyacht")


# ---------------------------------------------------------------------------
# Emergency disable tests
# ---------------------------------------------------------------------------


class TestEmergencyDisable:
    """AC: Emergency disable works by source and domain."""

    def test_emergency_disable_source(self):
        """Emergency disable by source slug blocks collection immediately."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        # Source is collectible before disable
        source = gate.resolve_source("sailsys")
        assert source is not None

        # Emergency disable
        gate.emergency_disable_source("sailsys", "takedown request")
        with pytest.raises(SourceNotApprovedError, match="emergency"):
            gate.resolve_source("sailsys")

    def test_emergency_disable_source_updates_enabled_flag(self):
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        gate.emergency_disable_source("sailsys")
        assert gate._sources["sailsys"].enabled is False

    def test_emergency_enable_source_restores(self):
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        gate.emergency_disable_source("sailsys")
        gate.emergency_enable_source("sailsys")
        source = gate.resolve_source("sailsys")
        assert source.slug == "sailsys"

    def test_emergency_disable_domain(self):
        """Emergency disable by domain blocks URL collection."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        gate.emergency_disable_domain("app.sailsys.com.au", "abuse complaint")

        # Register robots rules so the check proceeds past robots
        gate.cache_robots("app.sailsys.com.au", empty_robots())

        decision = gate.check_url(
            public_html_source(), PUBLIC_HTML_URL, robots_rules=empty_robots()
        )
        assert not decision.allowed
        assert "emergency-disabled" in decision.reason

    def test_emergency_disable_domain_blocks_subdomain(self):
        gate = CollectionGate(policy=POLICY)
        gate.emergency_disable_domain("sailwave.com")
        assert gate.is_domain_disabled("www.sailwave.com") is True
        assert gate.is_domain_disabled("results.sailwave.com") is True

    def test_emergency_enable_domain_restores(self):
        gate = CollectionGate(policy=POLICY)
        gate.emergency_disable_domain("example.com")
        assert gate.is_domain_disabled("example.com") is True
        gate.emergency_enable_domain("example.com")
        assert gate.is_domain_disabled("example.com") is False

    def test_global_kill_switch(self):
        """Global COLLECTION_ENABLED=false halts all collection."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        gate.set_collection_enabled(False)
        decision = gate.check_url(
            public_html_source(), PUBLIC_HTML_URL, robots_rules=empty_robots()
        )
        assert not decision.allowed
        assert "disabled" in decision.reason.lower()


# ---------------------------------------------------------------------------
# Source classification tests
# ---------------------------------------------------------------------------


class TestSourceClassification:
    """AC: Classify public, authenticated, licensed, prohibited, unclear sources."""

    def test_classify_public_source(self):
        policy = CollectionPolicyDecisionV1()
        cls = policy.classify("sailsys")
        assert cls.source_class == SourceClass.PUBLIC
        assert cls.legal_status == LegalStatus.APPROVED
        assert cls.collectible is True

    def test_classify_pdf_source(self):
        policy = CollectionPolicyDecisionV1()
        cls = policy.classify("irc-certs")
        assert cls.source_class == SourceClass.PUBLIC
        assert cls.collectible is True

    def test_classify_unclear_source(self):
        policy = CollectionPolicyDecisionV1()
        cls = policy.classify("clubspot")
        assert cls.source_class == SourceClass.UNCLEAR
        assert cls.collectible is False

    def test_classify_unknown_source_is_unclear(self):
        policy = CollectionPolicyDecisionV1()
        cls = policy.classify("totally-unknown")
        assert cls.source_class == SourceClass.UNCLEAR
        assert cls.legal_status == LegalStatus.BLOCKED
        assert cls.collectible is False

    def test_classify_by_domain_prohibited(self):
        policy = CollectionPolicyDecisionV1()
        # Add a prohibited domain
        policy_with_block = CollectionPolicyDecisionV1(
            prohibited_domains=("evil-scrape-target.com",)
        )
        cls = policy_with_block.classify("sailsys", "evil-scrape-target.com")
        assert cls.source_class == SourceClass.PROHIBITED
        assert cls.legal_status == LegalStatus.BLOCKED

    def test_classify_by_domain_authenticated(self):
        policy = CollectionPolicyDecisionV1()
        cls = policy.classify("sailsys", "app.sailsys.com.au")
        assert cls.source_class == SourceClass.AUTHENTICATED

    def test_classify_all_five_source_classes_exist(self):
        """All five source classes must be defined."""
        classes = {SourceClass.PUBLIC, SourceClass.AUTHENTICATED,
                   SourceClass.LICENSED, SourceClass.PROHIBITED,
                   SourceClass.UNCLEAR}
        assert len(classes) == 5


# ---------------------------------------------------------------------------
# Robots.txt parser tests
# ---------------------------------------------------------------------------


class TestRobotsParser:
    """Robots.txt compliance — parse and enforce disallow/allow rules."""

    def test_parse_empty_robots_allows_everything(self):
        rules = parse_robots_txt("")
        assert rules.no_rules is True
        assert rules.is_allowed("/any/path") is True

    def test_parse_disallow_root(self):
        rules = parse_robots_txt("User-agent: *\nDisallow: /")
        assert rules.is_allowed("/any/path") is False
        assert rules.is_allowed("/") is False

    def test_parse_allow_results_disallow_admin(self):
        rules = parse_robots_txt(
            "User-agent: *\nAllow: /results/\nDisallow: /admin/"
        )
        assert rules.is_allowed("/results/123") is True
        assert rules.is_allowed("/admin/panel") is False

    def test_parse_specific_user_agent(self):
        rules = parse_robots_txt(
            "User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nAllow: /"
        )
        assert rules.is_allowed("/path", "BadBot") is False
        assert rules.is_allowed("/path", "SailRatings/1.0") is True
        assert rules.is_allowed("/path", "*") is True

    def test_parse_crawl_delay(self):
        rules = parse_robots_txt(
            "User-agent: *\nCrawl-delay: 5\nDisallow: /admin"
        )
        assert rules.crawl_delay() == 5.0

    def test_parse_comments_ignored(self):
        rules = parse_robots_txt(
            "# This is a comment\nUser-agent: *\nDisallow: /private # inline comment"
        )
        assert rules.is_allowed("/private/page") is False
        assert rules.is_allowed("/public/page") is True

    def test_disallow_paths_list(self):
        rules = parse_robots_txt(
            "User-agent: *\nDisallow: /admin\nDisallow: /private\nDisallow: /tmp"
        )
        paths = rules.disallow_paths()
        assert "/admin" in paths
        assert "/private" in paths
        assert "/tmp" in paths

    def test_wildcard_pattern(self):
        rules = parse_robots_txt(
            "User-agent: *\nDisallow: /private*/results"
        )
        assert rules.is_allowed("/private-abc/results") is False
        assert rules.is_allowed("/public/results") is True

    def test_end_anchor_pattern(self):
        rules = parse_robots_txt(
            "User-agent: *\nDisallow: /*.pdf$"
        )
        assert rules.is_allowed("/docs/report.pdf") is False
        assert rules.is_allowed("/docs/report.html") is True

    def test_check_url_against_robots_convenience(self):
        rules = parse_robots_txt("User-agent: *\nDisallow: /admin")
        assert check_url_against_robots(
            "https://example.com/admin/panel", rules
        ) is False
        assert check_url_against_robots(
            "https://example.com/results/123", rules
        ) is True


# ---------------------------------------------------------------------------
# URL-level gate tests (disallow, prohibited, etc.)
# ---------------------------------------------------------------------------


class TestUrlGate:
    """AC: Policy fixtures exercise public HTML, API, PDF, login wall, disallow, unclear cases."""

    def test_public_html_url_allowed(self):
        """Public HTML page — allowed (within collection window)."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        decision = gate.check_url(
            public_html_source(), PUBLIC_HTML_URL,
            robots_rules=public_html_robots(),
            is_health_check=True,  # bypass window for deterministic test
        )
        assert decision.allowed, f"Public HTML should be allowed: {decision.reason}"

    def test_api_url_allowed(self):
        """API endpoint — allowed."""
        gate = CollectionGate(policy=POLICY, sources=[api_source()])
        decision = gate.check_url(
            api_source(), API_URL, robots_rules=empty_robots(),
            is_health_check=True,
        )
        assert decision.allowed, f"API should be allowed: {decision.reason}"

    def test_pdf_url_allowed(self):
        """PDF endpoint — allowed."""
        gate = CollectionGate(policy=POLICY, sources=[pdf_source()])
        decision = gate.check_url(
            pdf_source(), PDF_URL, robots_rules=empty_robots(),
            is_health_check=True,
        )
        assert decision.allowed, f"PDF should be allowed: {decision.reason}"

    def test_login_wall_url_blocked(self):
        """Login wall URL — blocked (prohibited)."""
        gate = CollectionGate(policy=POLICY, sources=[login_wall_source()])
        # Source is blocked so resolve_source will fail; check at URL level
        decision = gate.check_url(
            login_wall_source(), LOGIN_WALL_URL, robots_rules=empty_robots()
        )
        # Either blocked by classification or by domain
        assert not decision.allowed

    def test_disallow_url_blocked_by_robots(self):
        """URL disallowed by robots.txt — blocked."""
        gate = CollectionGate(policy=POLICY, sources=[disallow_source()])
        decision = gate.check_url(
            disallow_source(), DISALLOW_URL, robots_rules=disallow_robots()
        )
        # Should be blocked by robots (before window check)
        assert not decision.allowed
        assert "robots" in decision.reason.lower() or "disallow" in decision.reason.lower()

    def test_disallow_url_blocked_by_all_disallow(self):
        """URL when robots.txt disallows everything — blocked."""
        gate = CollectionGate(policy=POLICY, sources=[disallow_source()])
        decision = gate.check_url(
            disallow_source(), DISALLOW_URL, robots_rules=all_disallowed_robots()
        )
        assert not decision.allowed
        assert "robots" in decision.reason.lower()

    def test_unclear_url_blocked(self):
        """Unclear source — blocked."""
        gate = CollectionGate(policy=POLICY, sources=[unclear_source()])
        decision = gate.check_url(
            unclear_source(), UNCLEAR_URL, robots_rules=empty_robots()
        )
        assert not decision.allowed

    def test_disallow_from_cached_source_record(self):
        """URL checked against cached disallow paths (no robots.txt rules object)."""
        gate = CollectionGate(policy=POLICY, sources=[disallow_source()])
        # No robots_rules passed → falls back to source.robots_disallow
        decision = gate.check_url(
            disallow_source(), DISALLOW_URL, robots_rules=None
        )
        # Should be blocked by cached disallow pattern "/private"
        assert not decision.allowed

    def test_health_check_bypasses_window(self):
        """Health checks are allowed outside the collection window."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        decision = gate.check_url(
            public_html_source(), PUBLIC_HTML_URL,
            robots_rules=public_html_robots(),
            is_health_check=True,
        )
        # Health check should not be blocked by window
        assert "window" not in decision.reason.lower()


# ---------------------------------------------------------------------------
# Collection window tests
# ---------------------------------------------------------------------------


class TestCollectionWindow:
    """Collection window enforcement — nightly 01:00–06:00 only."""

    def test_window_config(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.collection_window.start_hour_local == 1
        assert policy.collection_window.end_hour_local == 6

    def test_url_blocked_outside_window(self):
        """When not a health check, URL is blocked outside 01:00–06:00."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        # We can't control the real time, but we can check that if the
        # current hour is outside the window, collection is blocked.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        hour = now.hour
        window = POLICY.collection_window

        decision = gate.check_url(
            public_html_source(), PUBLIC_HTML_URL,
            robots_rules=public_html_robots(),
            is_health_check=False,
        )
        if window.start_hour_local <= hour < window.end_hour_local:
            # Inside window — should be allowed
            assert decision.allowed, f"Should be allowed inside window: {decision.reason}"
        else:
            # Outside window — should be blocked
            assert not decision.allowed
            assert "window" in decision.reason.lower()

    def test_health_check_allowed_outside_window(self):
        """Health checks bypass the collection window restriction."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        decision = gate.check_url(
            public_html_source(), PUBLIC_HTML_URL,
            robots_rules=public_html_robots(),
            is_health_check=True,
        )
        assert "window" not in decision.reason.lower()


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """AC: RateLimiter enforces ≤ 1 req/2s per domain with jitter."""

    def test_rate_limit_enforces_min_delay(self):
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        # First call — no wait needed (no previous request)
        sleep1 = gate.rate_limit_wait("example.com")
        # Second call immediately — should wait ~2s + jitter
        sleep2 = gate.rate_limit_wait("example.com")
        assert sleep2 >= gate.policy.rate.min_delay_seconds - 0.5  # allow small variance

    def test_rate_limit_per_domain_independent(self):
        """Rate limits are per-domain — different domains don't block each other."""
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        gate.rate_limit_wait("domain-a.com")
        # Different domain should not be delayed
        sleep = gate.rate_limit_wait("domain-b.com")
        assert sleep < 0.5  # no significant wait expected

    def test_rate_limit_config_from_policy(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.rate.min_delay_seconds == 2.0
        assert policy.rate.jitter_seconds == 1.0


# ---------------------------------------------------------------------------
# Object size enforcement
# ---------------------------------------------------------------------------


class TestObjectSizeEnforcement:
    """Hard caps: max object size 25 MB."""

    def test_small_object_allowed(self):
        gate = CollectionGate(policy=POLICY)
        assert gate.check_object_size(1024) is True

    def test_object_under_max_allowed(self):
        gate = CollectionGate(policy=POLICY)
        max_bytes = POLICY.retention.max_object_size_mb * 1024 * 1024
        assert gate.check_object_size(max_bytes) is True

    def test_object_over_max_rejected(self):
        gate = CollectionGate(policy=POLICY)
        max_bytes = POLICY.retention.max_object_size_mb * 1024 * 1024
        assert gate.check_object_size(max_bytes + 1) is False

    def test_max_object_size_is_25_mb(self):
        assert POLICY.retention.max_object_size_mb == 25

    def test_max_fetches_per_night_is_5000(self):
        assert POLICY.retention.max_fetches_per_night == 5000


# ---------------------------------------------------------------------------
# Policy rule block tests
# ---------------------------------------------------------------------------


class TestPolicyRules:
    """Verify all policy rule blocks are present and configured per INTERIM-POLICY.md."""

    def test_robots_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.robots.fetch_at_session_start is True
        assert policy.robots.cache_ttl_hours == 24
        assert policy.robots.stop_on_fetch_error is True

    def test_rate_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.rate.honour_retry_after is True
        assert len(policy.rate.backoff_sequence) == 4

    def test_attribution_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert "SailRatings" in policy.attribution.user_agent
        assert policy.attribution.attribution_header == "X-SailRatings-Source"

    def test_takedown_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.takedown.response_window_hours == 4
        assert policy.takedown.disable_immediate is True
        assert policy.takedown.quarantine_existing is True

    def test_personal_data_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert "owner_email" in policy.personal_data.prohibited_fields
        assert "owner_phone" in policy.personal_data.prohibited_fields
        assert policy.personal_data.no_auth_circumvention is True
        assert policy.personal_data.no_captcha_bypass is True
        assert policy.personal_data.no_paywall_circumvention is True

    def test_retention_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.retention.hash_algorithm == "sha256"
        assert policy.retention.skip_duplicate_hashes is True
        assert policy.retention.conditional_requests is True

    def test_collection_window_rule_present(self):
        policy = CollectionPolicyDecisionV1()
        assert policy.collection_window.start_hour_local == 1
        assert policy.collection_window.end_hour_local == 6

    def test_policy_summary(self):
        policy = CollectionPolicyDecisionV1()
        summary = policy.to_summary()
        assert summary["version"] == "interim-v0"
        assert "robots" in summary
        assert "rate" in summary
        assert "attribution" in summary
        assert "takedown" in summary
        assert "personal_data" in summary
        assert "retention" in summary
        assert "collection_window" in summary
        assert summary["source_count"] == 11


# ---------------------------------------------------------------------------
# Parametrised fixture tests
# ---------------------------------------------------------------------------


class TestAllFixtures:
    """AC: Policy fixtures exercise public HTML, API, PDF, login wall, disallow, unclear cases."""

    @pytest.mark.parametrize(
        "name,source_fn,robots_fn,url,expected_allowed",
        ALL_FIXTURES,
        ids=[f[0] for f in ALL_FIXTURES],
    )
    def test_fixture(self, name, source_fn, robots_fn, url, expected_allowed):
        """Each fixture must produce the expected gate decision."""
        source = source_fn()
        gate = CollectionGate(policy=POLICY, sources=[source])
        robots = robots_fn() if robots_fn else None

        try:
            resolved = gate.resolve_source(source.slug)
        except (SourceNotApprovedError, PolicyVersionMismatchError):
            # Source is not resolvable → cannot collect
            if not expected_allowed:
                return  # expected: blocked
            raise  # unexpected: should have been allowed

        # Use health_check=True to bypass the collection window for
        # deterministic testing — the window is tested separately.
        decision = gate.check_url(resolved, url, robots_rules=robots, is_health_check=True)

        if expected_allowed:
            assert decision.allowed, (
                f"Fixture '{name}' should be allowed but was blocked: {decision.reason}"
            )
        else:
            assert not decision.allowed, (
                f"Fixture '{name}' should be blocked but was allowed"
            )


# ---------------------------------------------------------------------------
# Full evaluate() convenience test
# ---------------------------------------------------------------------------


class TestEvaluate:
    """Test the one-shot evaluate() method."""

    def test_evaluate_approved_source(self):
        gate = CollectionGate(policy=POLICY, sources=[public_html_source()])
        gate.cache_robots("app.sailsys.com.au", public_html_robots())
        decision = gate.evaluate("sailsys", PUBLIC_HTML_URL, robots_rules=public_html_robots())
        # May be blocked by window; that's ok
        assert decision.source is not None
        assert decision.source.slug == "sailsys"

    def test_evaluate_unknown_source_blocked(self):
        gate = CollectionGate(policy=POLICY)
        decision = gate.evaluate("nonexistent", PUBLIC_HTML_URL)
        assert not decision.allowed

    def test_evaluate_stale_policy_blocked(self):
        gate = CollectionGate(policy=POLICY, sources=[stale_policy_source()])
        decision = gate.evaluate("sailsys", PUBLIC_HTML_URL)
        assert not decision.allowed
        assert "policy_version" in decision.reason.lower() or "mismatch" in decision.reason.lower()
