"""Source adapter SDK (SPEC-012 §4–§5, deliverables DP-01-03 + DP-01-04).

A reusable, policy-aware adapter framework for the SailRatings data
platform.  Adapters inherit from :class:`SourceAdapter`, implement the
discover / fetch / enumerate / checkpoint / parse-hint / rate-limit /
conditional-request / health-probe surface, and emit **raw envelopes
only** (``RawCaptureRequestV1``).  No parsing or normalisation happens
inside an adapter — that is the job of the DP-02 pipeline.

The **acquisition primitive library** (DP-01-04) provides six bounded
fetchers — :func:`fetch_html`, :func:`fetch_pdf`, :func:`fetch_json`,
:func:`fetch_file`, :func:`paginate`, :func:`render_page` — that cover
every common sailing-source delivery format.  All return
:class:`FetchResult` and enforce the collection policy.

Design note
-----------
The SDK is intentionally DB-optional.  It ships its own
:dataclass:`DataSource` and a :class:`SourceRegistry` protocol so the
reference adapter and its tests run with **zero network and zero
database**.  When DP-01-01 lands the ``data_sources`` table a thin
DB-backed registry can plug in through the same protocol without
touching adapter code.

Public API::

    from irc_data.sources import (
        SourceAdapter,
        FakeSourceAdapter,
        RawCaptureRequestV1,
        AdapterCheckpointV1,
        FetchResult,
        FetchTarget,
        HealthProbeResult,
        InMemorySourceRegistry,
        run_adapter_contract,
        # Acquisition primitives (DP-01-04)
        fetch_html,
        fetch_pdf,
        fetch_json,
        fetch_file,
        paginate,
        render_page,
        RenderedFetchResult,
    )
"""

from __future__ import annotations

from .contracts import (
    CURRENT_POLICY_VERSION,
    AdapterCheckpointV1,
    DataSource,
    FetchResult,
    FetchTarget,
    HealthProbeResult,
    PolicyVersionMismatchError,
    RawCaptureRequestV1,
    SourceNotApprovedError,
    UserAgentError,
)
from .adapter import SourceAdapter
from .fake import FakeSourceAdapter, FakeSourceServer
from .registry import (
    InMemorySourceRegistry,
    SourceRegistry,
    get_source,
    seed_registry,
)
from .contract_suite import run_adapter_contract
from .primitives import (
    BrowserFactory,
    BrowserLike,
    MalformedResponseError,
    PageLike,
    RenderedFetchResult,
    fetch_file,
    fetch_html,
    fetch_json,
    fetch_pdf,
    paginate,
    render_page,
)

__all__ = [
    "CURRENT_POLICY_VERSION",
    "SourceAdapter",
    "FakeSourceAdapter",
    "FakeSourceServer",
    "RawCaptureRequestV1",
    "AdapterCheckpointV1",
    "FetchResult",
    "FetchTarget",
    "HealthProbeResult",
    "DataSource",
    "SourceRegistry",
    "InMemorySourceRegistry",
    "get_source",
    "seed_registry",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "UserAgentError",
    "run_adapter_contract",
    # Acquisition primitives (DP-01-04)
    "fetch_html",
    "fetch_pdf",
    "fetch_json",
    "fetch_file",
    "paginate",
    "render_page",
    "RenderedFetchResult",
    "MalformedResponseError",
    "BrowserFactory",
    "BrowserLike",
    "PageLike",
]
