"""Source framework: policy, gate, robots parser, and adapter SDK.

This package implements:

* **DP-01-02** — the responsible-collection policy and enforcement gate.
  Every byte the platform collects must pass through the
  ``CollectionGate`` which asserts an approved policy version, a valid
  source record, robots.txt compliance, rate limits, and
  collection-window rules.

* **DP-01-03** — the reusable source adapter SDK.  Adapters inherit
  from :class:`SourceAdapter`, discover URLs, fetch them with rate-
  limiting / retry / conditional requests / content hashing, and emit
  raw :class:`RawCaptureRequestV1` envelopes.  The
  :class:`AdapterCheckpointV1` contract enables interrupted runs to
  resume.
"""

from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    CollectionPolicyDecisionV1,
    LegalStatus,
    SourceClass,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    ProhibitedCollectionError,
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
)
from irc_data.sources.adapter import (
    DiscoveredItem,
    HealthProbeResult,
    ParseHint,
    SourceAdapter,
)
from irc_data.sources.registry import (
    get_all_sources,
    get_source,
    seed_sources,
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
    "CollectionPolicyDecisionV1",
    "LegalStatus",
    "SourceClass",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "ProhibitedCollectionError",
    "CollectionGate",
    "GateDecision",
    "SourceRecord",
    "RobotsRules",
    "parse_robots_txt",
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
    # DP-01-03 fake adapter
    "FakeHttpServer",
    "FakeSourceAdapter",
    "StubSourceAdapter",
    "make_fake_adapter",
    "make_fake_server",
]
