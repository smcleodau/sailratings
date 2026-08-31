"""Tests for the responsible collection policy gate (DP-01-02).

Covers:
- Policy version gate (assert_policy_current, is_current)
- Source approval gate (resolve_source for approved / hold / blocked / disabled / quarantined / unknown)
- Emergency disable by source and domain
- Robots disallow enforcement
- Collection window checks
- CollectionPolicyDecisionV1 output contract
- Source classification (public, authenticated, licensed, prohibited, unclear)
- Policy fixtures: public HTML, API, PDF, login wall, disallow, unclear
- API router response shape
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

import pytest

from irc_data.sources import policy
from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    POLICY_APPROVED_DATE,
    POLICY_AUTHORITY,
    POLICY_AUTHORITY_EMAIL,
    POLICY_USER_AGENT,
    CollectionPolicyDecisionV1,
    CollectionRules,
    ContentType,
    DataSource,
    LegalStatus,
    SourceClass,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_policy_current,
    classify_source,
    emergency_disable_domain,
    emergency_disable_source,
    get_policy_summary,
    get_source,
    is_current_policy_version,
    is_domain_enabled,
    is_path_allowed,
    is_path_disallowed,
    is_source_enabled,
    is_within_collection_window,
    list_all_sources,
    list_sources,
    resolve_source,
)


# ── Autouse fixture: snapshot & restore source state ────────────────────────
#
# Several tests mutate the in-memory DataSource objects (emergency disable,
# quarantine toggles, etc.).  This fixture snapshots every source's mutable
# fields before each test and restores them after, so tests are isolated.


@pytest.fixture(autouse=True)
def _restore_source_state():
    """Snapshot all source fields and restore after each test."""
    snapshots: dict[str, dict] = {}
    for src in list_all_sources():
        snapshots[src.slug] = {
            f.name: (list(getattr(src, f.name)) if isinstance(getattr(src, f.name), list) else getattr(src, f.name))
            for f in dataclass_fields(src)
        }
    yield
    for src in list_all_sources():
        snap = snapshots[src.slug]
        for f in dataclass_fields(src):
            val = snap[f.name]
            if isinstance(val, list):
                setattr(src, f.name, list(val))
            else:
                setattr(src, f.name, val)


# ─────────────────────────────────────────────────────────────────────────────
# TestPolicyVersionGate
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyVersionGate:
    """Policy version constant and assertion logic."""

    def test_current_policy_version_is_interim_v0(self):
        assert CURRENT_POLICY_VERSION == "interim-v0"

    def test_policy_has_correct_version(self):
        assert policy.CURRENT_POLICY_VERSION == "interim-v0"

    def test_assert_version_passes_for_matching_version(self):
        source = get_source("orc")
        assert source.policy_version == CURRENT_POLICY_VERSION
        # Should not raise
        assert_policy_current(source)

    def test_assert_version_raises_on_mismatch(self):
        source = DataSource(
            slug="test-stale",
            display_name="Stale",
            base_url="https://example.com",
            category="results",
            policy_version="interim-v99",
        )
        with pytest.raises(PolicyVersionMismatchError, match="interim-v99"):
            assert_policy_current(source)

    def test_resolve_source_raises_on_stale_policy_version(self):
        with pytest.raises(PolicyVersionMismatchError):
            resolve_source("fixture-stale-version")

    def test_is_current_returns_true_for_matching(self):
        assert is_current_policy_version(CURRENT_POLICY_VERSION) is True

    def test_is_current_returns_false_for_mismatch(self):
        assert is_current_policy_version("v1") is False
        assert is_current_policy_version("") is False
        assert is_current_policy_version("interim-v1") is False

    def test_policy_version_mismatch_error_carries_fields(self):
        err = PolicyVersionMismatchError("orc", "v0", "interim-v0")
        assert err.slug == "orc"
        assert err.source_version == "v0"
        assert err.current_version == "interim-v0"

    def test_all_seed_sources_reference_current_version(self):
        for src in list_sources():
            assert src.policy_version == CURRENT_POLICY_VERSION, (
                f"{src.slug} has stale policy_version={src.policy_version}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# TestSourceApprovalGate
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceApprovalGate:
    """resolve_source must enforce approval, enable, and quarantine gates."""

    def test_approved_source_resolves(self):
        decision = resolve_source("orc")
        assert decision.allowed is True
        assert decision.slug == "orc"
        assert decision.legal_status == "approved"

    def test_hold_source_raises(self):
        with pytest.raises(SourceNotApprovedError, match="hold"):
            resolve_source("clubspot")

    def test_blocked_source_raises(self):
        with pytest.raises(SourceNotApprovedError, match="blocked"):
            resolve_source("fixture-blocked")

    def test_disabled_source_raises(self):
        with pytest.raises(SourceNotApprovedError, match="disabled"):
            resolve_source("fixture-disabled")

    def test_unknown_source_raises(self):
        with pytest.raises(KeyError):
            resolve_source("does-not-exist")

    def test_quarantined_source_raises(self):
        with pytest.raises(SourceNotApprovedError, match="quarantined"):
            resolve_source("fixture-quarantined")

    def test_source_not_approved_error_carries_slug(self):
        try:
            resolve_source("clubspot")
        except SourceNotApprovedError as e:
            assert e.slug == "clubspot"
            assert "hold" in e.reason
        else:
            pytest.fail("Should have raised")

    def test_approved_source_decision_has_rules(self):
        decision = resolve_source("sailsys")
        assert isinstance(decision.rules, CollectionRules)
        assert decision.rules.rate_limit_seconds == 2.0
        assert decision.rules.user_agent == POLICY_USER_AGENT


# ─────────────────────────────────────────────────────────────────────────────
# TestEmergencyDisable
# ─────────────────────────────────────────────────────────────────────────────


class TestEmergencyDisable:
    """Kill switch by source slug and by domain."""

    def test_emergency_disable_source_blocks_resolution(self):
        decision_before = resolve_source("orc")
        assert decision_before.allowed is True

        emergency_disable_source("orc")

        with pytest.raises(SourceNotApprovedError, match="disabled"):
            resolve_source("orc")

    def test_is_source_enabled_reflects_disable(self):
        assert is_source_enabled("sailsys") is True
        emergency_disable_source("sailsys")
        assert is_source_enabled("sailsys") is False

    def test_emergency_disable_source_returns_disabled_source(self):
        src = emergency_disable_source("yachtscoring")
        assert src.enabled is False
        assert src.slug == "yachtscoring"

    def test_emergency_disable_domain_blocks_all_matching(self):
        affected = emergency_disable_domain("example.com")
        assert len(affected) > 0
        for src in affected:
            assert src.enabled is False

        # Verify domain-level check
        assert is_domain_enabled("example.com") is False

    def test_emergency_disable_domain_returns_affected_sources(self):
        affected = emergency_disable_domain("orc.org")
        assert any(s.slug == "orc" for s in affected)

    def test_is_domain_enabled_true_when_source_active(self):
        assert is_domain_enabled("sailsys.com.au") is True

    def test_is_domain_enabled_false_when_no_match(self):
        assert is_domain_enabled("nonexistent.invalid") is False

    def test_disabled_source_cannot_resolve(self):
        with pytest.raises(SourceNotApprovedError, match="disabled"):
            resolve_source("fixture-disabled")


# ─────────────────────────────────────────────────────────────────────────────
# TestSourceClassification
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceClassification:
    """Sources are classified into public, authenticated, licensed, prohibited, unclear."""

    def test_classify_approved_public_source(self):
        src = get_source("sailsys")
        cls, label = classify_source(src)
        assert cls == "public"
        assert label == "approved"

    def test_classify_hold_source_as_unclear(self):
        src = get_source("clubspot")
        cls, label = classify_source(src)
        assert cls == "unclear"
        assert label == "hold"

    def test_classify_blocked_source_as_prohibited(self):
        src = get_source("fixture-blocked")
        cls, label = classify_source(src)
        assert cls == "prohibited"
        assert label == "blocked"

    def test_classify_licensed_source(self):
        src = get_source("fixture-licensed")
        cls, label = classify_source(src)
        assert cls == "licensed"
        assert label == "approved"

    def test_classify_authenticated_source(self):
        src = get_source("fixture-authenticated")
        cls, label = classify_source(src)
        assert cls == "authenticated"
        assert label == "approved"

    def test_source_class_enum_values(self):
        assert SourceClass.PUBLIC.value == "public"
        assert SourceClass.AUTHENTICATED.value == "authenticated"
        assert SourceClass.LICENSED.value == "licensed"
        assert SourceClass.PROHIBITED.value == "prohibited"
        assert SourceClass.UNCLEAR.value == "unclear"

    def test_legal_status_enum_values(self):
        assert LegalStatus.APPROVED.value == "approved"
        assert LegalStatus.HOLD.value == "hold"
        assert LegalStatus.BLOCKED.value == "blocked"

    def test_content_type_enum_values(self):
        assert ContentType.HTML.value == "html"
        assert ContentType.API.value == "api"
        assert ContentType.PDF.value == "pdf"
        assert ContentType.FILE.value == "file"
        assert ContentType.FEED.value == "feed"


# ─────────────────────────────────────────────────────────────────────────────
# TestPolicyFixtures — public HTML, API, PDF, login wall, disallow, unclear
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyFixtures:
    """Policy fixtures exercise the six required cases."""

    # ── Public HTML ──

    def test_public_html_source_sailsys_resolves(self):
        decision = resolve_source("sailsys")
        assert decision.content_type == "html"
        assert decision.source_class == "public"
        assert decision.allowed is True

    def test_public_html_source_topyacht_resolves(self):
        decision = resolve_source("topyacht")
        assert decision.content_type == "html"
        assert decision.source_class == "public"
        assert decision.allowed is True

    def test_public_html_source_yachtscoring_resolves(self):
        decision = resolve_source("yachtscoring")
        assert decision.content_type == "html"
        assert decision.source_class == "public"

    def test_public_html_source_manage2sail_resolves(self):
        decision = resolve_source("manage2sail")
        assert decision.content_type == "html"
        assert decision.source_class == "public"

    # ── API ──

    def test_api_source_orc_resolves(self):
        decision = resolve_source("orc")
        assert decision.content_type == "api"
        assert decision.source_class == "public"
        assert decision.allowed is True

    def test_api_source_has_json_content_type(self):
        src = get_source("orc")
        assert src.content_type == "api"

    # ── PDF ──

    def test_pdf_source_irc_certs_resolves(self):
        decision = resolve_source("irc-certs")
        assert decision.content_type == "pdf"
        assert decision.source_class == "public"
        assert decision.allowed is True

    def test_pdf_source_has_attribution_header(self):
        decision = resolve_source("irc-certs")
        assert decision.rules.attribution_header == "X-SailRatings-Source: irc-certs"

    def test_pdf_source_category_is_certificates(self):
        src = get_source("irc-certs")
        assert src.category == "certificates"

    # ── Login wall (prohibited) ──

    def test_login_wall_source_is_blocked(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-login-wall")

    def test_login_wall_source_class_is_prohibited(self):
        src = get_source("fixture-login-wall")
        assert src.source_class == "prohibited"
        assert src.legal_status == "blocked"

    def test_paywall_source_is_blocked(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-paywall")

    def test_paywall_source_class_is_prohibited(self):
        src = get_source("fixture-paywall")
        assert src.source_class == "prohibited"

    # ── Robots disallow ──

    def test_disallow_fixture_has_robots_rules(self):
        src = get_source("fixture-disallow")
        assert len(src.robots_disallow) > 0
        assert "/private" in src.robots_disallow

    def test_disallow_fixture_resolves_when_approved(self):
        decision = resolve_source("fixture-disallow")
        assert decision.allowed is True
        assert len(decision.robots_disallow) > 0

    def test_disallow_path_is_blocked(self):
        src = get_source("fixture-disallow")
        assert is_path_disallowed(src, "/private/data") is True
        assert is_path_disallowed(src, "/admin") is True
        assert is_path_disallowed(src, "/results/secret/page") is True

    def test_allowed_path_is_not_blocked(self):
        src = get_source("fixture-disallow")
        assert is_path_allowed(src, "/results/public") is True
        assert is_path_allowed(src, "/") is True

    def test_source_without_disallow_allows_all(self):
        src = get_source("orc")
        assert is_path_allowed(src, "/any/path") is True
        assert is_path_disallowed(src, "/any/path") is False

    # ── Unclear (hold) ──

    def test_unclear_source_clubspot_is_hold(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("clubspot")
        src = get_source("clubspot")
        assert src.source_class == "unclear"
        assert src.legal_status == "hold"

    def test_unclear_source_kwindoo_is_hold(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("kwindoo")
        src = get_source("kwindoo")
        assert src.source_class == "unclear"
        assert src.legal_status == "hold"

    def test_unclear_sources_produce_zero_fetch_attempts(self):
        """Hold sources must not pass the approval gate."""
        for slug in ("clubspot", "kwindoo"):
            with pytest.raises(SourceNotApprovedError):
                resolve_source(slug)


# ─────────────────────────────────────────────────────────────────────────────
# TestCollectionPolicyDecisionV1
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectionPolicyDecisionV1:
    """The output contract must carry all required fields."""

    def test_decision_has_required_fields(self):
        decision = resolve_source("orc")
        assert isinstance(decision, CollectionPolicyDecisionV1)
        assert decision.slug
        assert decision.display_name
        assert decision.base_url
        assert decision.category
        assert decision.policy_version == CURRENT_POLICY_VERSION
        assert decision.legal_status == "approved"
        assert decision.source_class
        assert decision.content_type
        assert decision.allowed is True

    def test_decision_to_dict(self):
        decision = resolve_source("orc")
        d = decision.to_dict()
        assert d["slug"] == "orc"
        assert d["allowed"] is True
        assert d["policy_version"] == CURRENT_POLICY_VERSION
        assert "rules" in d
        assert d["rules"]["user_agent"] == POLICY_USER_AGENT
        assert d["rules"]["rate_limit_seconds"] == 2.0

    def test_decision_rules_have_rate_limit(self):
        decision = resolve_source("sailsys")
        assert decision.rules.rate_limit_seconds == 2.0
        assert decision.rules.rate_jitter_seconds == 1.0

    def test_decision_rules_have_caps(self):
        decision = resolve_source("orc")
        assert decision.rules.max_object_size_mb == 25
        assert decision.rules.max_fetches_per_night == 5_000
        assert decision.rules.max_total_mb_per_night == 500

    def test_decision_rules_have_collection_window(self):
        decision = resolve_source("sailsys")
        assert decision.rules.collection_window_start == 1
        assert decision.rules.collection_window_end == 6

    def test_decision_rules_enforce_no_auth_circumvention(self):
        decision = resolve_source("orc")
        assert decision.rules.no_auth_circumvention is True

    def test_decision_rules_enforce_no_personal_data(self):
        decision = resolve_source("orc")
        assert decision.rules.no_personal_data is True

    def test_decision_rules_respect_robots(self):
        decision = resolve_source("sailsys")
        assert decision.rules.respect_robots is True

    def test_decision_rules_use_conditional_requests(self):
        decision = resolve_source("orc")
        assert decision.rules.use_conditional_requests is True

    def test_decision_rules_have_takedown_contact(self):
        decision = resolve_source("sailsys")
        assert decision.rules.takedown_contact == POLICY_AUTHORITY_EMAIL

    def test_decision_carries_robots_disallow(self):
        decision = resolve_source("fixture-disallow")
        assert "/private" in decision.robots_disallow

    def test_irc_certs_decision_has_attribution_header(self):
        decision = resolve_source("irc-certs")
        assert decision.rules.attribution_header == "X-SailRatings-Source: irc-certs"

    def test_non_irc_source_has_no_attribution_header(self):
        decision = resolve_source("orc")
        assert decision.rules.attribution_header is None


# ─────────────────────────────────────────────────────────────────────────────
# TestPolicyConstants
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyConstants:
    """The policy module exposes the correct constants."""

    def test_user_agent_string(self):
        assert "SailRatings/1.0" in POLICY_USER_AGENT
        assert "sailratings.com" in POLICY_USER_AGENT
        assert "stuart@sailratings.com" in POLICY_USER_AGENT

    def test_policy_authority(self):
        assert POLICY_AUTHORITY == "Stuart McLeod"

    def test_policy_authority_email(self):
        assert POLICY_AUTHORITY_EMAIL == "stuart@sailratings.com"

    def test_policy_approved_date(self):
        assert POLICY_APPROVED_DATE == "2026-08-30"


# ─────────────────────────────────────────────────────────────────────────────
# TestSeedSources
# ─────────────────────────────────────────────────────────────────────────────


class TestSeedSources:
    """The 11 seed sources from SPEC-012 §2.2."""

    def test_seed_source_count(self):
        assert len(list_sources()) == 11

    def test_approved_count(self):
        approved = [s for s in list_sources() if s.is_approved]
        assert len(approved) == 9

    def test_hold_count(self):
        hold = [s for s in list_sources() if s.is_hold]
        assert len(hold) == 2

    def test_blocked_count_in_seed(self):
        blocked = [s for s in list_sources() if s.is_blocked]
        assert len(blocked) == 0

    def test_all_seed_slugs(self):
        slugs = {s.slug for s in list_sources()}
        expected = {
            "sailsys", "topyacht", "irc-tcc", "orc", "yachtscoring",
            "manage2sail", "sailwave", "sailing-news", "irc-certs",
            "clubspot", "kwindoo",
        }
        assert slugs == expected

    def test_hold_slugs(self):
        hold = [s for s in list_sources() if s.is_hold]
        hold_slugs = {s.slug for s in hold}
        assert hold_slugs == {"clubspot", "kwindoo"}

    def test_all_seed_sources_have_policy_version(self):
        for src in list_sources():
            assert src.policy_version == CURRENT_POLICY_VERSION

    def test_all_seed_sources_have_base_url(self):
        for src in list_sources():
            assert src.base_url.startswith("https://")

    def test_all_seed_sources_have_category(self):
        categories = {s.category for s in list_sources()}
        assert "results" in categories
        assert "ratings" in categories
        assert "certificates" in categories
        assert "news" in categories

    def test_all_seed_sources_enabled_by_default(self):
        for src in list_sources():
            assert src.enabled is True


# ─────────────────────────────────────────────────────────────────────────────
# TestGetSource
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSource:
    """get_source retrieves from the in-memory registry."""

    def test_get_known_source(self):
        src = get_source("orc")
        assert src.slug == "orc"
        assert src.display_name == "ORC"

    def test_get_unknown_source_raises(self):
        with pytest.raises(KeyError, match="Unknown source"):
            get_source("nonexistent")

    def test_get_source_returns_same_instance(self):
        a = get_source("orc")
        b = get_source("orc")
        assert a is b

    def test_get_source_with_db_arg_ignored_gracefully(self):
        # db=None should work (in-memory registry)
        src = get_source("orc", db=None)
        assert src.slug == "orc"


# ─────────────────────────────────────────────────────────────────────────────
# TestListSources
# ─────────────────────────────────────────────────────────────────────────────


class TestListSources:
    """list_sources and list_all_sources."""

    def test_list_sources_returns_seed_only(self):
        sources = list_sources()
        assert len(sources) == 11
        assert all(s.slug in {x.slug for x in list_sources()} for s in sources)

    def test_list_sources_with_fixtures(self):
        sources = list_sources(include_fixtures=True)
        assert len(sources) > 11

    def test_list_all_sources_includes_fixtures(self):
        sources = list_all_sources()
        assert len(sources) > 11
        slugs = {s.slug for s in sources}
        assert "fixture-login-wall" in slugs
        assert "fixture-disallow" in slugs


# ─────────────────────────────────────────────────────────────────────────────
# TestRobotsEnforcement
# ─────────────────────────────────────────────────────────────────────────────


class TestRobotsEnforcement:
    """robots.txt disallow enforcement."""

    def test_is_path_disallowed_matches_prefix(self):
        src = get_source("fixture-disallow")
        assert is_path_disallowed(src, "/private") is True
        assert is_path_disallowed(src, "/private/deep") is True

    def test_is_path_allowed_for_non_matching(self):
        src = get_source("fixture-disallow")
        assert is_path_allowed(src, "/public/results") is True

    def test_empty_disallow_allows_all(self):
        src = get_source("orc")
        assert is_path_allowed(src, "/anything") is True

    def test_decision_carries_robots_disallow_list(self):
        decision = resolve_source("fixture-disallow")
        assert "/private" in decision.robots_disallow
        assert "/admin" in decision.robots_disallow
        assert "/results/secret" in decision.robots_disallow


# ─────────────────────────────────────────────────────────────────────────────
# TestCollectionWindow
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectionWindow:
    """Nightly collection window 01:00–06:00."""

    def test_hour_in_window(self):
        assert is_within_collection_window(3) is True

    def test_hour_at_start_boundary(self):
        assert is_within_collection_window(1) is True

    def test_hour_at_end_boundary(self):
        assert is_within_collection_window(6) is False

    def test_hour_outside_window(self):
        assert is_within_collection_window(12) is False
        assert is_within_collection_window(0) is False

    def test_custom_window(self):
        assert is_within_collection_window(10, start=9, end=17) is True
        assert is_within_collection_window(18, start=9, end=17) is False


# ─────────────────────────────────────────────────────────────────────────────
# TestPolicySummary
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicySummary:
    """get_policy_summary returns the correct shape for the API/UI."""

    def test_summary_has_version(self):
        s = get_policy_summary()
        assert s["version"] == CURRENT_POLICY_VERSION

    def test_summary_has_approved_date(self):
        s = get_policy_summary()
        assert s["approved_date"] == POLICY_APPROVED_DATE

    def test_summary_has_authority(self):
        s = get_policy_summary()
        assert s["authority"] == POLICY_AUTHORITY

    def test_summary_has_user_agent(self):
        s = get_policy_summary()
        assert s["user_agent"] == POLICY_USER_AGENT

    def test_summary_has_issue_label(self):
        s = get_policy_summary()
        assert s["issue_label"] == "DP-01-02"

    def test_summary_has_spec_reference(self):
        s = get_policy_summary()
        assert s["spec_reference"] == "SPEC-012"

    def test_summary_counts(self):
        s = get_policy_summary()
        assert s["counts"]["approved"] == 9
        assert s["counts"]["hold"] == 2
        assert s["counts"]["total"] == 11

    def test_summary_has_source_list(self):
        s = get_policy_summary()
        assert len(s["sources"]) == 11

    def test_summary_source_has_required_fields(self):
        s = get_policy_summary()
        src = s["sources"][0]
        for field in ("slug", "display_name", "base_url", "category",
                       "policy_version", "legal_status", "source_class",
                       "content_type", "classification", "enabled"):
            assert field in src, f"Missing field: {field}"

    def test_summary_source_has_classification(self):
        s = get_policy_summary()
        for src in s["sources"]:
            assert src["classification"] in ("approved", "hold", "blocked")


# ─────────────────────────────────────────────────────────────────────────────
# TestDataSourceModel
# ─────────────────────────────────────────────────────────────────────────────


class TestDataSourceModel:
    """DataSource dataclass properties."""

    def test_is_approved_property(self):
        src = get_source("orc")
        assert src.is_approved is True

    def test_is_hold_property(self):
        src = get_source("clubspot")
        assert src.is_hold is True

    def test_is_blocked_property(self):
        src = get_source("fixture-blocked")
        assert src.is_blocked is True

    def test_is_clear_for_approved_enabled(self):
        src = get_source("orc")
        assert src.is_clear is True

    def test_is_clear_false_for_hold(self):
        src = get_source("clubspot")
        assert src.is_clear is False

    def test_is_clear_false_for_disabled(self):
        src = get_source("fixture-disabled")
        assert src.is_clear is False

    def test_is_clear_false_for_quarantined(self):
        src = get_source("fixture-quarantined")
        assert src.is_clear is False

    def test_data_source_default_values(self):
        src = DataSource(
            slug="test",
            display_name="Test",
            base_url="https://example.com",
            category="results",
        )
        assert src.policy_version == CURRENT_POLICY_VERSION
        assert src.legal_status == "approved"
        assert src.source_class == "public"
        assert src.content_type == "html"
        assert src.enabled is True
        assert src.quarantined is False
        assert src.robots_disallow == []

    def test_data_source_robots_disallow_default_empty(self):
        src = DataSource(
            slug="test", display_name="T", base_url="https://x.com", category="x"
        )
        assert src.robots_disallow == []


# ─────────────────────────────────────────────────────────────────────────────
# TestCollectionRules
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectionRules:
    """CollectionRules default values match the policy spec."""

    def test_default_rate_limit(self):
        rules = CollectionRules()
        assert rules.rate_limit_seconds == 2.0

    def test_default_jitter(self):
        rules = CollectionRules()
        assert rules.rate_jitter_seconds == 1.0

    def test_default_window(self):
        rules = CollectionRules()
        assert rules.collection_window_start == 1
        assert rules.collection_window_end == 6

    def test_default_caps(self):
        rules = CollectionRules()
        assert rules.max_object_size_mb == 25
        assert rules.max_fetches_per_night == 5_000
        assert rules.max_total_mb_per_night == 500

    def test_default_user_agent(self):
        rules = CollectionRules()
        assert rules.user_agent == POLICY_USER_AGENT

    def test_default_prohibition_flags(self):
        rules = CollectionRules()
        assert rules.no_auth_circumvention is True
        assert rules.no_personal_data is True
        assert rules.respect_robots is True

    def test_default_conditional_requests(self):
        rules = CollectionRules()
        assert rules.use_conditional_requests is True

    def test_default_takedown_contact(self):
        rules = CollectionRules()
        assert rules.takedown_contact == POLICY_AUTHORITY_EMAIL


# ─────────────────────────────────────────────────────────────────────────────
# TestAllApprovedSourcesResolve
# ─────────────────────────────────────────────────────────────────────────────


class TestAllApprovedSourcesResolve:
    """Every approved seed source must resolve successfully."""

    @pytest.mark.parametrize("slug", [
        "sailsys", "topyacht", "irc-tcc", "orc", "yachtscoring",
        "manage2sail", "sailwave", "sailing-news", "irc-certs",
    ])
    def test_approved_source_resolves(self, slug):
        decision = resolve_source(slug)
        assert decision.allowed is True
        assert decision.policy_version == CURRENT_POLICY_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# TestAllHoldSourcesBlocked
# ─────────────────────────────────────────────────────────────────────────────


class TestAllHoldSourcesBlocked:
    """Every hold source must raise SourceNotApprovedError."""

    @pytest.mark.parametrize("slug", ["clubspot", "kwindoo"])
    def test_hold_source_blocked(self, slug):
        with pytest.raises(SourceNotApprovedError):
            resolve_source(slug)


# ─────────────────────────────────────────────────────────────────────────────
# TestProhibitedCollection
# ─────────────────────────────────────────────────────────────────────────────


class TestProhibitedCollection:
    """Login walls, paywalls, and CAPTCHA sources are prohibited."""

    def test_login_wall_source_blocked(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-login-wall")

    def test_paywall_source_blocked(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-paywall")

    def test_prohibited_sources_have_prohibited_class(self):
        for slug in ("fixture-login-wall", "fixture-paywall", "fixture-blocked"):
            src = get_source(slug)
            assert src.source_class == "prohibited"


# ─────────────────────────────────────────────────────────────────────────────
# TestAdapterCannotRunWithoutApprovedPolicy
# ─────────────────────────────────────────────────────────────────────────────


class TestAdapterCannotRunWithoutApprovedPolicy:
    """Acceptance: adapter cannot run without approved policy version."""

    def test_stale_version_prevents_resolution(self):
        with pytest.raises(PolicyVersionMismatchError):
            resolve_source("fixture-stale-version")

    def test_hold_prevents_resolution(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("clubspot")

    def test_disabled_prevents_resolution(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-disabled")

    def test_quarantined_prevents_resolution(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-quarantined")

    def test_blocked_prevents_resolution(self):
        with pytest.raises(SourceNotApprovedError):
            resolve_source("fixture-blocked")

    def test_unknown_prevents_resolution(self):
        with pytest.raises(KeyError):
            resolve_source("totally-unknown")
