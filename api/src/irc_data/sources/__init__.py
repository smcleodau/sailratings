"""Source framework — governed, observable data acquisition.

This package implements:

* **DP-01-02** — the responsible-collection policy and enforcement gate.
* **DP-01-03** — the reusable source adapter SDK.
* **DP-01-04** — acquisition primitives (DataSourceRecordV1, ORM DataSource,
  can_collect, can_discover, resolve_and_assert_approved).
* **DP-01-05** — source change/breakage detection (classify_source,
  resolve_source, CollectionRules, SourceDecisionV1).

Public API::

    from irc_data.sources import (
        # Policy + gate (DP-01-02)
        CURRENT_POLICY_VERSION,
        ACTIVE_POLICY,
        CollectionPolicyDecisionV1,
        LegalStatus,
        SourceClass,
        PolicyVersionMismatchError,
        SourceNotApprovedError,
        ProhibitedCollectionError,
        CollectionGate,
        GateDecision,
        SourceRecord,
        RobotsRules,
        parse_robots_txt,
        # DP-01-05 policy additions
        POLICY_APPROVED_DATE,
        POLICY_AUTHORITY,
        POLICY_AUTHORITY_EMAIL,
        POLICY_USER_AGENT,
        ContentType,
        CollectionRules,
        SourceDecisionV1,
        CollectionWindowClosedError,
        classify_source,
        get_policy_summary,
        resolve_source,
        is_within_collection_window,
        is_path_allowed,
        is_path_disallowed,
        is_current_policy_version,
        # Envelopes (DP-01-03)
        RawCaptureRequestV1,
        AdapterCheckpointV1,
        FetchResult,
        FetchStatus,
        sha256_hex,
        # HTTP client (DP-01-03)
        HttpClient,
        NotModified,
        ObjectTooLargeError,
        RetryExhaustedError,
        UserAgentMissingError,
        get_source_http_client,
        # Adapter SDK (DP-01-03)
        SourceAdapter,
        DiscoveredItem,
        HealthProbeResult,
        ParseHint,
        # Registry (DP-01-03 + DP-01-04)
        get_source,
        get_all_sources,
        seed_sources,
        # Acquisition primitives (DP-01-04)
        DataSourceRecordV1,
        DataSource,
        can_collect,
        can_discover,
        resolve_and_assert_approved,
        list_sources,
        # Fake adapter (testing)
        FakeHttpServer,
        FakeSourceAdapter,
        StubSourceAdapter,
        make_fake_adapter,
        make_fake_server,
    )
"""

from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    ACTIVE_POLICY,
    CollectionPolicyDecisionV1,
    LegalStatus,
    SourceClass,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    ProhibitedCollectionError,
    assert_policy_current,
    assert_source_approved,
    assert_source_collectable,
    # DP-01-05 additions
    POLICY_APPROVED_DATE,
    POLICY_AUTHORITY,
    POLICY_AUTHORITY_EMAIL,
    POLICY_USER_AGENT,
    ContentType,
    CollectionRules,
    SourceDecisionV1,
    CollectionWindowClosedError,
    classify_source,
    get_policy_summary,
    resolve_source,
    is_within_collection_window,
    is_path_allowed,
    is_path_disallowed,
    is_current_policy_version,
)
from irc_data.sources.gate import CollectionGate, GateDecision, SourceRecord
from irc_data.sources.robots import RobotsRules, parse_robots_txt

# DP-01-03 — adapter SDK
from irc_data.sources.envelope import (
    AdapterCheckpointV1,
    FetchResult,
    FetchStatus,
    RawCaptureRequestV1,
    sha256_hex,
)
from irc_data.sources.http_client import (
    HttpClient,
    NotModified,
    ObjectTooLargeError,
    RetryExhaustedError,
    UserAgentMissingError,
    get_source_http_client,
    # backward-compat
    PolicyAwareHttpClient,
    RateLimiter,
    STANDARD_USER_AGENT,
)
from irc_data.sources.adapter import (
    DiscoveredItem,
    HealthProbeResult,
    ParseHint,
    SourceAdapter,
    Checkpoint,  # backward-compat alias for AdapterCheckpointV1
)
from irc_data.sources.registry import (
    get_all_sources,
    get_source,
    seed_sources,
    get_in_memory_source,
    get_in_memory_sources,
    all_sources,
    approved_sources,
    register_source,
    # DP-01-04 acquisition primitives
    assert_approved,
    can_collect,
    can_discover,
    list_sources,
    resolve_and_assert_approved,
    DataSource,
    LEGAL_STATUSES,
    SEED_COUNT,
    HOLD_SOURCES,
)

# DP-01-04 — Pydantic schema
from irc_data.sources.models import DataSourceRecordV1

# DP-01-04 — acquisition primitives
from irc_data.sources.primitives import (
    PDF_MAGIC,
    fetch_file,
    fetch_html,
    fetch_json,
    fetch_pdf,
    fetch_xml,
    paginate,
    render_page,
)

from irc_data.sources.fake_adapter import (
    FakeHttpServer,
    FakeSourceAdapter,
    StubSourceAdapter,
    make_fake_adapter,
    make_fake_server,
)

__all__ = [
    # DP-01-02 policy + gate
    "CURRENT_POLICY_VERSION",
    "ACTIVE_POLICY",
    "CollectionPolicyDecisionV1",
    "LegalStatus",
    "SourceClass",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "ProhibitedCollectionError",
    "assert_policy_current",
    "assert_source_approved",
    "assert_source_collectable",
    "CollectionGate",
    "GateDecision",
    "SourceRecord",
    "RobotsRules",
    "parse_robots_txt",
    # DP-01-05 policy additions
    "POLICY_APPROVED_DATE",
    "POLICY_AUTHORITY",
    "POLICY_AUTHORITY_EMAIL",
    "POLICY_USER_AGENT",
    "ContentType",
    "CollectionRules",
    "SourceDecisionV1",
    "CollectionWindowClosedError",
    "classify_source",
    "get_policy_summary",
    "resolve_source",
    "is_within_collection_window",
    "is_path_allowed",
    "is_path_disallowed",
    "is_current_policy_version",
    # DP-01-03 envelopes
    "RawCaptureRequestV1",
    "AdapterCheckpointV1",
    "FetchResult",
    "FetchStatus",
    "sha256_hex",
    # DP-01-03 HTTP client
    "HttpClient",
    "NotModified",
    "ObjectTooLargeError",
    "RetryExhaustedError",
    "UserAgentMissingError",
    "get_source_http_client",
    # DP-01-03 adapter SDK
    "SourceAdapter",
    "DiscoveredItem",
    "HealthProbeResult",
    "ParseHint",
    # DP-01-03 registry
    "get_source",
    "get_all_sources",
    "seed_sources",
    "get_in_memory_source",
    "get_in_memory_sources",
    # DP-01-03 fake adapter
    "FakeHttpServer",
    "FakeSourceAdapter",
    "StubSourceAdapter",
    "make_fake_adapter",
    "make_fake_server",
    # DP-01-04 acquisition primitives
    "DataSourceRecordV1",
    "DataSource",
    "LEGAL_STATUSES",
    "assert_approved",
    "can_collect",
    "can_discover",
    "list_sources",
    "resolve_and_assert_approved",
    "PDF_MAGIC",
    "fetch_html",
    "fetch_pdf",
    "fetch_json",
    "fetch_xml",
    "fetch_file",
    "paginate",
    "render_page",
    # Seed constants
    "SEED_COUNT",
    "HOLD_SOURCES",
    # Backward-compat
    "Checkpoint",
    "PolicyAwareHttpClient",
    "RateLimiter",
    "STANDARD_USER_AGENT",
    "all_sources",
    "approved_sources",
    "register_source",
]
