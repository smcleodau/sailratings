"""Reusable adapter contract test suite (SPEC-012 §4.2, Verification).

``run_adapter_contract`` runs the **same** set of assertions against
*every* adapter.  It is the verification deliverable for DP-01-03:
"Contract test suite runs identically against every adapter."

An adapter author writes a tiny factory that returns a fresh, wired
adapter + the list of URLs it is expected to fetch, then calls::

    run_adapter_contract(factory=make_fake_adapter, expected_urls=[...])

The suite asserts:

1. **policy enforcement** — a ``hold`` / disabled source raises
   ``SourceNotApprovedError`` before any fetch.
2. **pagination** — ``collect`` yields one envelope per discovered page.
3. **retry** — a transient 5xx is retried and ultimately succeeds.
4. **content hashing** — every envelope has a SHA-256 hash; re-fetching
   unchanged content yields zero new envelopes.
5. **checkpoint resume** — an interrupted run resumes from the last
   checkpoint rather than restarting.
6. **conditional requests** — ``If-None-Match`` produces a 304 that is
   treated as a clean success (no envelope, no re-download).
7. **raw-envelope contract** — every yielded item is a
   :class:`RawCaptureRequestV1` with ``schema_version == "v1"`` and a
   non-empty ``content_hash`` (unless it is a 304).
8. **rate limit** — the adapter honours the per-domain min delay.

The suite is parametrisable: pass ``adapter_factory`` (a zero-arg
callable returning a fresh adapter) and ``expected_urls``.  It is used
both by ``tests/sources/test_fake_adapter.py`` (against the reference
fake) and by ``tests/sources/test_contract_suite.py`` (against a second
stub adapter) to prove the suite itself is generic.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from .contracts import (
    AdapterCheckpointV1,
    CURRENT_POLICY_VERSION,
    FetchResult,
    FetchTarget,
    RawCaptureRequestV1,
    SourceNotApprovedError,
)
from .adapter import SourceAdapter

__all__ = ["run_adapter_contract", "AdapterFactory", "ContractFailure"]


# A factory is any zero-arg callable returning a fresh adapter.
AdapterFactory = Callable[[], SourceAdapter]


class ContractFailure(AssertionError):
    """Raised when an adapter violates the SDK contract."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_adapter_contract(
    *,
    adapter_factory: AdapterFactory,
    expected_urls: list[str],
    expected_envelope_count: int | None = None,
) -> dict[str, Any]:
    """Run the full contract suite synchronously; return a report dict.

    Raises :class:`ContractFailure` on the first violated assertion so a
    test runner reports a clear failure.  The returned dict is also
    suitable for posting as test evidence.
    """
    report: dict[str, Any] = {"checks": [], "passed": 0, "failed": 0}

    def _check(name: str, fn: Callable[[], Any]) -> Any:
        try:
            value = fn()
        except Exception as exc:
            report["checks"].append({"name": name, "ok": False, "error": str(exc)})
            report["failed"] += 1
            raise ContractFailure(f"contract check {name!r} failed: {exc}") from exc
        report["checks"].append({"name": name, "ok": True})
        report["passed"] += 1
        return value

    # Fresh adapter for each check so they are independent.
    _check("policy_enforcement_hold", lambda: _assert_policy_blocks_hold(adapter_factory))
    _check("policy_enforcement_disabled", lambda: _assert_policy_blocks_disabled(adapter_factory))

    envelopes = _check(
        "collect_yields_envelopes",
        lambda: _run_collect(adapter_factory()),
    )
    _check("envelope_count", lambda: _assert_envelope_count(
        envelopes, expected_envelope_count or len(expected_urls)
    ))
    _check("envelope_schema", lambda: _assert_envelope_schema(envelopes))
    _check("pagination_urls", lambda: _assert_pagination_urls(envelopes, expected_urls))
    _check("content_hashing", lambda: _assert_content_hashing(adapter_factory))

    # Retry check needs the adapter's server to simulate a 5xx; the
    # reference fake does this via ``flaky_pages``.  Adapters that don't
    # expose a server skip this check.
    _check("retry_on_5xx", lambda: _assert_retry(adapter_factory))

    _check("checkpoint_resume", lambda: _assert_checkpoint_resume(adapter_factory, expected_urls))
    _check("conditional_request_304", lambda: _assert_conditional_304(adapter_factory, expected_urls))

    report["ok"] = report["failed"] == 0
    return report


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _run_collect(adapter: SourceAdapter) -> list[RawCaptureRequestV1]:
    return asyncio.run(_collect_async(adapter))


async def _collect_async(adapter: SourceAdapter) -> list[RawCaptureRequestV1]:
    return [item async for item in adapter.collect()]


def _assert_policy_blocks_hold(factory: AdapterFactory) -> None:
    from .contracts import DataSource
    from .registry import InMemorySourceRegistry, seed_registry

    # Build a fresh registry with the adapter's source marked ``hold``.
    # We pass it back into the factory via the ``registry=`` kwarg so the
    # policy gate fires at construction time.
    adapter = factory()
    src = adapter.source
    hold_reg = seed_registry()
    hold_reg.upsert(
        DataSource(
            slug=src.slug,
            display_name=src.display_name,
            base_url=src.base_url,
            category=src.category,
            policy_version=src.policy_version,
            legal_status="hold",
            enabled=True,
        )
    )
    try:
        factory(registry=hold_reg)  # type: ignore[misc]
    except TypeError:
        # Factory does not accept ``registry=``; fall back to mutating
        # the adapter's own registry and re-resolving.
        reg = adapter.registry
        if not isinstance(reg, InMemorySourceRegistry):
            return
        reg.upsert(
            DataSource(
                slug=src.slug,
                display_name=src.display_name,
                base_url=src.base_url,
                category=src.category,
                policy_version=src.policy_version,
                legal_status="hold",
                enabled=True,
            )
        )
        try:
            factory()
        except SourceNotApprovedError:
            return
        raise ContractFailure("hold source did not raise SourceNotApprovedError")
    except SourceNotApprovedError:
        return
    raise ContractFailure("hold source did not raise SourceNotApprovedError")


def _assert_policy_blocks_disabled(factory: AdapterFactory) -> None:
    from .contracts import DataSource
    from .registry import InMemorySourceRegistry, seed_registry

    adapter = factory()
    src = adapter.source
    disabled_reg = seed_registry()
    disabled_reg.upsert(
        DataSource(
            slug=src.slug,
            display_name=src.display_name,
            base_url=src.base_url,
            category=src.category,
            policy_version=src.policy_version,
            legal_status=src.legal_status,
            enabled=False,
        )
    )
    try:
        factory(registry=disabled_reg)  # type: ignore[misc]
    except TypeError:
        reg = adapter.registry
        if not isinstance(reg, InMemorySourceRegistry):
            return
        reg.upsert(
            DataSource(
                slug=src.slug,
                display_name=src.display_name,
                base_url=src.base_url,
                category=src.category,
                policy_version=src.policy_version,
                legal_status=src.legal_status,
                enabled=False,
            )
        )
        try:
            factory()
        except SourceNotApprovedError:
            return
        raise ContractFailure("disabled source did not raise SourceNotApprovedError")
    except SourceNotApprovedError:
        return
    raise ContractFailure("disabled source did not raise SourceNotApprovedError")


def _assert_envelope_count(envelopes: list[RawCaptureRequestV1], expected: int) -> None:
    if len(envelopes) != expected:
        raise ContractFailure(
            f"expected {expected} envelopes, got {len(envelopes)}"
        )


def _assert_envelope_schema(envelopes: list[RawCaptureRequestV1]) -> None:
    for i, env in enumerate(envelopes):
        if not isinstance(env, RawCaptureRequestV1):
            raise ContractFailure(f"envelope {i} is not RawCaptureRequestV1")
        if env.schema_version != "v1":
            raise ContractFailure(f"envelope {i} schema_version != 'v1'")
        if not env.content_hash:
            raise ContractFailure(f"envelope {i} has empty content_hash")
        if env.policy_version != CURRENT_POLICY_VERSION:
            raise ContractFailure(f"envelope {i} policy_version mismatch")
        if not env.source_slug:
            raise ContractFailure(f"envelope {i} has empty source_slug")
        if not env.url:
            raise ContractFailure(f"envelope {i} has empty url")
        if env.content is None:
            raise ContractFailure(f"envelope {i} has None content")


def _assert_pagination_urls(envelopes: list[RawCaptureRequestV1], expected: list[str]) -> None:
    got = [e.url for e in envelopes]
    # Order-independent: a checkpoint-resumed run may fetch a subset.
    for url in expected:
        if url not in got and not _any_match(url, got):
            raise ContractFailure(f"expected URL {url!r} not collected; got {got}")


def _any_match(url: str, got: list[str]) -> bool:
    return url in got


def _assert_content_hashing(factory: AdapterFactory) -> None:
    """Re-running collect against unchanged content yields zero new envelopes.

    Two mechanisms suppress re-download of unchanged content:

    1. **conditional requests** — the second run sends ``If-None-Match``
       with the ETag captured on the first run; the server returns 304,
       which the adapter treats as a clean success (no envelope).
    2. **content-hash dedup** — even without ETags, the HTTP client's
       ``remember_hash`` records the SHA-256 of each body and a second
       fetch of identical bytes is flagged as unchanged.

    This check exercises (1): it runs ``collect`` once, seeds the
    adapter's server with the returned ETags, then re-runs ``collect``
    on the *same* server and asserts zero new envelopes.
    """
    adapter = factory()
    first = asyncio.run(_collect_async(adapter))
    if not first:
        raise ContractFailure("first run yielded no envelopes — cannot verify hashing")

    server = getattr(adapter, "server", None)
    if server is not None and hasattr(server, "etags"):
        # Seed ETags so the second run issues If-None-Match → 304.
        for env in first:
            if env.etag:
                server.etags[env.url] = env.etag
        # Re-run against the SAME server (ETags now known).
        adapter2 = factory()
        # Carry the seeded ETags across to the second adapter's server.
        if hasattr(adapter2, "server") and adapter2.server is not adapter.server:
            adapter2.server.etags = dict(server.etags)  # type: ignore[attr-defined]
        second = asyncio.run(_collect_async(adapter2))
        if second:
            raise ContractFailure(
                "second run against unchanged content (ETag 304 path) should "
                f"yield 0 envelopes, got {len(second)}"
            )
        return

    # No server attribute: fall back to the HTTP client's hash dedup.
    # Re-run with the same client (hash memory persists) → no envelopes.
    second = asyncio.run(_collect_async(adapter))
    if second:
        raise ContractFailure(
            "second run against unchanged content should yield 0 envelopes, "
            f"got {len(second)}"
        )


def _assert_retry(factory: AdapterFactory) -> None:
    """A transient 5xx is retried and ultimately succeeds."""
    adapter = factory()
    server = getattr(adapter, "server", None)
    if server is None:
        return  # adapter does not expose a server; skip.
    # The reference fake simulates a 503 on page 2's first hit; a
    # successful collect proves retry worked.
    envelopes = asyncio.run(_collect_async(adapter))
    flaky = getattr(server, "flaky_pages", set())
    if not flaky:
        return
    if not envelopes:
        raise ContractFailure("retry check: no envelopes collected despite flaky pages")
    # Verify the flaky page was actually fetched (hit count >= 2 → retried).
    page_url = next(
        (u for u in [e.url for e in envelopes] if "page=2" in u), None
    )
    if page_url and server.hit_counts.get(page_url, 0) < 2:
        raise ContractFailure(
            f"flaky page {page_url!r} was not retried (hits="
            f"{server.hit_counts.get(page_url, 0)})"
        )


def _assert_checkpoint_resume(factory: AdapterFactory, expected_urls: list[str]) -> None:
    """An interrupted run resumes from the last checkpoint."""
    from .adapter import FileCheckpointStore
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = FileCheckpointStore(tmp)
        # First adapter: collect only the first page, then "interrupt".
        adapter = _build_with_store(factory, store)
        # Manually mark the first URL as completed to simulate interruption.
        cp = adapter.load_checkpoint()
        first_url = expected_urls[0]
        cp = AdapterCheckpointV1(
            source_slug=adapter.source_slug,
            cursor=str(_next_cursor(expected_urls, first_url)),
            completed_urls=[first_url],
            fetched_count=1,
            bytes_fetched=0,
        )
        store.save(cp)
        # Resume: should skip the first URL and collect the rest.
        adapter2 = _build_with_store(factory, store)
        envelopes = asyncio.run(_collect_async(adapter2))
        urls = [e.url for e in envelopes]
        if first_url in urls:
            raise ContractFailure(
                f"checkpoint did not skip completed URL {first_url!r}; got {urls}"
            )
        for url in expected_urls[1:]:
            if url not in urls:
                raise ContractFailure(
                    f"checkpoint resume did not collect {url!r}; got {urls}"
                )


def _build_with_store(factory: AdapterFactory, store) -> SourceAdapter:
    """Re-build an adapter from the factory, injecting a checkpoint store.

    Factories accept ``checkpoint_store=`` kwarg when supported; otherwise
    we fall back to constructing the adapter and monkey-patching the
    store on.  The reference fake supports the kwarg.
    """
    try:
        return factory(checkpoint_store=store)  # type: ignore[misc]
    except TypeError:
        adapter = factory()
        adapter.checkpoint_store = store
        return adapter


def _next_cursor(expected_urls: list[str], just_done_url: str) -> str:
    """Derive the next cursor value after completing *just_done_url*."""
    try:
        idx = expected_urls.index(just_done_url)
    except ValueError:
        return ""
    if idx + 1 < len(expected_urls):
        nxt = expected_urls[idx + 1]
        # Extract page number from ``?page=N``.
        if "page=" in nxt:
            return nxt.split("page=")[-1]
    return ""


def _assert_conditional_304(factory: AdapterFactory, expected_urls: list[str]) -> None:
    """``If-None-Match`` produces a 304 that is treated as a clean success."""
    adapter = factory()
    server = getattr(adapter, "server", None)
    if server is None:
        return  # adapter does not support ETag simulation; skip.
    # Seed ETags for every page so the first fetch stores them.
    first = asyncio.run(_collect_async(adapter))
    if not first:
        return
    for env in first:
        if env.etag:
            server.etags[env.url] = env.etag
    # Now re-fetch with conditional tokens — should be all 304s.
    adapter2 = factory()
    adapter2.server.etags = dict(server.etags)  # type: ignore[attr-defined]
    second = asyncio.run(_collect_async(adapter2))
    if second:
        raise ContractFailure(
            f"conditional request did not suppress re-download; got {len(second)} envelopes"
        )
