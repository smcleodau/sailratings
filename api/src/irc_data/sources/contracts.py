"""Typed contracts for the source adapter SDK.

These dataclasses are the **handoff / output contract** for DP-01-03:

* :class:`RawCaptureRequestV1` — the envelope an adapter emits for every
  raw artifact it collects.  Parsing happens downstream (DP-02).
* :class:`AdapterCheckpointV1` — the resumable state an adapter
  persists between collection runs so an interrupted job can pick up
  where it left off.

They are intentionally plain ``@dataclass`` objects (no Pydantic) so the
SDK has zero hard runtime deps beyond the standard library — every
adapter in the tree can depend on it.  ``to_dict`` / ``from_dict``
helpers give JSON-serialisable shapes for the message bus / storage
layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Policy version — single source of truth.
#
# Matches `docs/INTERIM-POLICY.md` (interim-v0, approved 2026-08-30) and
# `docs/specs/SPEC-012-Source-Framework.md` §3.1.  When the policy is
# revised this constant must be bumped in lock-step with a migration
# that updates every ``data_sources.policy_version`` row.
# ---------------------------------------------------------------------------
CURRENT_POLICY_VERSION: str = "interim-v0"

# The standard, non-negotiable User-Agent (INTERIM-POLICY.md §6).
STANDARD_USER_AGENT: str = (
    "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)"
)

# Hard caps enforced by the HTTP layer (INTERIM-POLICY.md §3.6).
MAX_OBJECT_BYTES: int = 25 * 1024 * 1024        # 25 MB per object
MAX_FETCHES_PER_RUN: int = 5_000               # per source per night
MAX_BYTES_PER_RUN: int = 500 * 1024 * 1024      # 500 MB per source per night

# Politeness defaults (INTERIM-POLICY.md §3.2).
DEFAULT_MIN_DELAY_SECONDS: float = 2.0
DEFAULT_JITTER_SECONDS: float = 1.0
DEFAULT_MAX_RETRIES: int = 3


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class SourceAdapterError(Exception):
    """Base class for all source-SDK errors."""


class SourceNotApprovedError(SourceAdapterError):
    """Raised when a source is ``hold`` / ``blocked`` or disabled.

    No fallback, no silent skip — collection must abort (SPEC-012 §2.3).
    """

    def __init__(self, slug: str, reason: str = "not approved") -> None:
        self.slug = slug
        self.reason = reason
        super().__init__(f"source {slug!r} is not approved for collection: {reason}")


class PolicyVersionMismatchError(SourceAdapterError):
    """Raised when a source's ``policy_version`` != ``CURRENT_POLICY_VERSION``."""

    def __init__(self, slug: str, source_version: str, current_version: str) -> None:
        self.slug = slug
        self.source_version = source_version
        self.current_version = current_version
        super().__init__(
            f"source {slug!r} references policy {source_version!r}, "
            f"current is {current_version!r}"
        )


class UserAgentError(SourceAdapterError):
    """Raised when an HTTP transport refuses / overrides the standard UA."""


class RobotsDisallowedError(SourceAdapterError):
    """Raised when a target URL is blocked by robots.txt."""


class FetchCapExceededError(SourceAdapterError):
    """Raised when a hard cap (object size / fetch count / byte budget) is hit."""


class CheckpointError(SourceAdapterError):
    """Raised when a checkpoint cannot be loaded / parsed / stored."""


# ---------------------------------------------------------------------------
# Source record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataSource:
    """In-memory mirror of the ``data_sources`` row (SPEC-012 §2.1).

    Frozen so it is safe to share across coroutines; mutations go
    through the registry (which returns a *new* record).
    """

    slug: str
    display_name: str
    base_url: str
    category: str                       # results | ratings | certificates | news
    policy_version: str = CURRENT_POLICY_VERSION
    legal_status: str = "approved"     # approved | hold | blocked
    enabled: bool = True
    adapter_class: str | None = None
    robots_checked_at: str | None = None
    robots_disallow: tuple[str, ...] = ()
    contact_email: str | None = None
    notes: str | None = None

    @property
    def is_approved(self) -> bool:
        return self.legal_status == "approved" and self.enabled


# ---------------------------------------------------------------------------
# Fetch artefact
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FetchResult:
    """Raw HTTP artefact produced by the policy-aware HTTP client.

    Adapters wrap :class:`FetchResult` objects into
    :class:`RawCaptureRequestV1` envelopes; they never return bare bytes.
    """

    url: str
    content: bytes
    content_hash: str                  # SHA-256 hex
    status_code: int
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str = field(default_factory=lambda: _now_iso())
    policy_version: str = CURRENT_POLICY_VERSION
    not_modified: bool = False         # True when server returned 304

    @property
    def content_size(self) -> int:
        return len(self.content)


# ---------------------------------------------------------------------------
# Discovery / enumeration units
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FetchTarget:
    """A single addressable artefact discovered by an adapter.

    Carries the conditional-request tokens (``etag`` / ``last_modified``)
    so the HTTP client can issue ``If-None-Match`` / ``If-Modified-Since``
    on the next run and skip unchanged content.
    """

    url: str
    kind: str = "page"                 # page | pdf | json | file | feed
    etag: str | None = None
    last_modified: str | None = None
    meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthProbeResult:
    """Outcome of a single-URL health probe (SPEC-012 §6, INTERIM-POLICY §8).

    Health probes are the *only* fetches allowed outside the nightly
    collection window; they are deliberately lightweight (one URL).
    """

    source_slug: str
    url: str
    healthy: bool
    status_code: int | None
    content_hash: str | None
    previous_hash: str | None
    changed: bool
    probed_at: str = field(default_factory=lambda: _now_iso())
    error: str | None = None


# ---------------------------------------------------------------------------
# Output contract: RawCaptureRequestV1
# ---------------------------------------------------------------------------
@dataclass
class RawCaptureRequestV1:
    """The raw-envelope an adapter emits for every collected artefact.

    Version ``v1``.  Additive, backward-compatible fields only — bump the
    ``schema_version`` and ship a new dataclass if a breaking change is
    ever needed.

    The downstream DP-02 ingester reads ``content`` (bytes) plus the
    provenance fields and decides how to parse based on ``content_type``
    and ``parse_hint``.
    """

    source_slug: str
    url: str
    content: bytes
    content_hash: str                              # SHA-256 hex
    content_type: str                             # e.g. text/html, application/pdf
    fetched_at: str                               # ISO-8601
    schema_version: str = "v1"
    policy_version: str = CURRENT_POLICY_VERSION
    etag: str | None = None
    last_modified: str | None = None
    parse_hint: str | None = None                 # adapter's suggestion for DP-02
    correlation_id: str | None = None             # run / batch identifier
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # bytes are not JSON-serialisable; hand off as a base64 string so the
        # envelope can travel over a message bus / be stored as JSON.
        d["content_b64"] = _b64(self.content)
        del d["content"]
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RawCaptureRequestV1":
        d = dict(d)
        content = d.pop("content", None)
        if content is None:
            content = _unb64(d.pop("content_b64", b""))
        return cls(content=content, **{k: v for k, v in d.items() if k in cls._fields()})

    @staticmethod
    def _fields() -> set[str]:
        return {
            "source_slug", "url", "content", "content_hash", "content_type",
            "fetched_at", "schema_version", "policy_version", "etag",
            "last_modified", "parse_hint", "correlation_id", "meta",
        }


# ---------------------------------------------------------------------------
# Output contract: AdapterCheckpointV1
# ---------------------------------------------------------------------------
@dataclass
class AdapterCheckpointV1:
    """Resumable adapter state.

    An adapter writes a checkpoint after every successful page / batch
    so that an interrupted collection run can resume from the last known
    good position rather than restarting from scratch.  The shape is
    intentionally adapter-defined (``cursor`` is opaque to the SDK) —
    the SDK only guarantees it round-trips through ``to_dict`` /
    ``from_dict`` and carries the policy version that was in force.
    """

    source_slug: str
    cursor: str | None = None          # opaque, adapter-specific continuation token
    completed_urls: list[str] = field(default_factory=list)
    fetched_count: int = 0
    bytes_fetched: int = 0
    schema_version: str = "v1"
    policy_version: str = CURRENT_POLICY_VERSION
    created_at: str = field(default_factory=lambda: _now_iso())
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "AdapterCheckpointV1":
        return cls(**{k: v for k, v in d.items() if k in cls._fields()})

    @staticmethod
    def _fields() -> set[str]:
        return {
            "source_slug", "cursor", "completed_urls", "fetched_count",
            "bytes_fetched", "schema_version", "policy_version",
            "created_at", "meta",
        }

    def with_progress(self, *, url: str, bytes_fetched: int) -> "AdapterCheckpointV1":
        """Return a new checkpoint recording one more completed URL."""
        return AdapterCheckpointV1(
            source_slug=self.source_slug,
            cursor=self.cursor,
            completed_urls=[*self.completed_urls, url],
            fetched_count=self.fetched_count + 1,
            bytes_fetched=self.bytes_fetched + bytes_fetched,
            schema_version=self.schema_version,
            policy_version=self.policy_version,
            meta=dict(self.meta),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """UTC timestamp in ISO-8601 with a trailing ``Z``."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(tz=timezone.utc).microsecond:06d}Z"
    )


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _unb64(data: bytes | str) -> bytes:
    import base64

    if isinstance(data, str):
        data = data.encode("ascii")
    return base64.b64decode(data)
