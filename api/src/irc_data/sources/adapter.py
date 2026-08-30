"""The source adapter base class (SPEC-012 §4).

Every source adapter inherits from :class:`SourceAdapter` and implements
the acquisition surface as async methods:

* ``discover``    — enumerate the URLs to fetch (paginated index walk)
* ``fetch``       — fetch a single URL via the policy-aware HTTP client
* ``enumerate``   — list :class:`FetchTarget` objects (with conditional tokens)
* ``checkpoint``  — persist / load resumable state
* ``parse_hint``   — tell DP-02 how to parse a given artefact
* ``health_probe`` — single-URL liveness / change check
* ``collect``     — async generator yielding :class:`RawCaptureRequestV1` envelopes
* ``run``         — drain ``collect`` into a list

Adapters emit **raw envelopes only**.  No parsing, no DB writes, no
side effects beyond checkpoint persistence.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .contracts import (
    AdapterCheckpointV1,
    CURRENT_POLICY_VERSION,
    DataSource,
    FetchResult,
    FetchTarget,
    HealthProbeResult,
    RawCaptureRequestV1,
    SourceNotApprovedError,
)
from .http import PolicyAwareHTTPClient
from .policy import assert_source_collectable
from .registry import InMemorySourceRegistry, SourceRegistry, get_source

__all__ = ["SourceAdapter", "CheckpointStore"]


@runtime_checkable
class CheckpointStore(Protocol):
    """Pluggable checkpoint persistence (file / DB / KV)."""

    def load(self, source_slug: str) -> AdapterCheckpointV1 | None:  # pragma: no cover
        ...

    def save(self, checkpoint: AdapterCheckpointV1) -> None:  # pragma: no cover
        ...


class FileCheckpointStore:
    """Filesystem-backed checkpoint store (default; test-friendly)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, source_slug: str) -> Path:
        return self.root / f"{source_slug}.checkpoint.json"

    def load(self, source_slug: str) -> AdapterCheckpointV1 | None:
        p = self._path(source_slug)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            return AdapterCheckpointV1.from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def save(self, checkpoint: AdapterCheckpointV1) -> None:
        p = self._path(checkpoint.source_slug)
        p.write_text(json.dumps(checkpoint.to_dict(), indent=2))


class SourceAdapter:
    """Abstract base for every source adapter.

    Subclasses set ``source_slug`` (must resolve to an approved
    :class:`DataSource` in the registry) and implement the ``collect``
    async generator.  Sensible defaults are provided for every other
    hook so a minimal adapter is ~20 lines.
    """

    source_slug: str = ""

    def __init__(
        self,
        *,
        registry: SourceRegistry | None = None,
        http: PolicyAwareHTTPClient | None = None,
        checkpoint_store: CheckpointStore | None = None,
        correlation_id: str | None = None,
    ) -> None:
        if not self.source_slug:
            raise TypeError(
                f"{type(self).__name__} must set a class-level `source_slug`"
            )
        self.registry: SourceRegistry = registry or InMemorySourceRegistry()
        self.http: PolicyAwareHTTPClient = http or PolicyAwareHTTPClient()
        self.checkpoint_store: CheckpointStore | None = checkpoint_store
        self.correlation_id = correlation_id or str(uuid.uuid4())
        # ``_source`` resolution enforces policy *before* any fetch.
        self._source: DataSource = self._resolve_source()

    # ------------------------------------------------------------------
    # Source resolution + policy gate
    # ------------------------------------------------------------------
    def _resolve_source(self) -> DataSource:
        src = get_source(self.registry, self.source_slug)
        assert_source_collectable(src)
        return src

    @property
    def source(self) -> DataSource:
        return self._source

    # ------------------------------------------------------------------
    # Acquisition hooks (subclasses override)
    # ------------------------------------------------------------------
    async def discover(self) -> AsyncIterator[FetchTarget]:
        """Yield the :class:`FetchTarget` objects to collect.

        Default implementation yields the source's ``base_url`` as a
        single page.  Adapters with a paginated index override this.
        """
        yield FetchTarget(url=self._source.base_url, kind="page")

    async def fetch(self, target: FetchTarget) -> FetchResult:
        """Fetch one target via the policy-aware HTTP client."""
        return await self.http.fetch_target(
            target, robots_disallow=self._source.robots_disallow
        )

    async def enumerate_targets(self) -> list[FetchTarget]:
        """Eagerly materialise ``discover`` into a list."""
        out: list[FetchTarget] = []
        async for t in self.discover():
            out.append(t)
        return out

    def parse_hint(self, target: FetchTarget, result: FetchResult) -> str | None:
        """Suggest to DP-02 how to parse *result*.  Default: ``None``."""
        return None

    async def health_probe(self, url: str | None = None) -> HealthProbeResult:
        """Single-URL liveness / change probe (INTERIM-POLICY.md §8)."""
        target = url or self._source.base_url
        prev = self.http._seen_hashes.get(target)  # noqa: SLF001 (intentional)
        try:
            result = await self.http.fetch(
                target, robots_disallow=self._source.robots_disallow
            )
        except Exception as exc:  # pragma: no cover - defensive
            return HealthProbeResult(
                source_slug=self.source_slug,
                url=target,
                healthy=False,
                status_code=None,
                content_hash=None,
                previous_hash=prev,
                changed=False,
                error=str(exc),
            )
        changed = result.content_hash != prev
        return HealthProbeResult(
            source_slug=self.source_slug,
            url=target,
            healthy=result.status_code < 400,
            status_code=result.status_code,
            content_hash=result.content_hash or None,
            previous_hash=prev,
            changed=changed,
        )

    # ------------------------------------------------------------------
    # Checkpoint resume
    # ------------------------------------------------------------------
    def load_checkpoint(self) -> AdapterCheckpointV1:
        """Load the last checkpoint (or a fresh one) for this source."""
        if self.checkpoint_store is not None:
            cp = self.checkpoint_store.load(self.source_slug)
            if cp is not None:
                return cp
        return AdapterCheckpointV1(source_slug=self.source_slug)

    def save_checkpoint(self, checkpoint: AdapterCheckpointV1) -> None:
        if self.checkpoint_store is not None:
            self.checkpoint_store.save(checkpoint)

    # ------------------------------------------------------------------
    # Collect — the main entry point
    # ------------------------------------------------------------------
    async def collect(self) -> AsyncIterator[RawCaptureRequestV1]:
        """Yield raw :class:`RawCaptureRequestV1` envelopes.

        Default implementation walks ``discover``, fetches each target,
        dedupes by content hash, persists a checkpoint after each
        successful fetch, and wraps the result in an envelope.  Subclasses
        may override for source-specific flows but should reuse the same
        building blocks so policy enforcement stays uniform.
        """
        checkpoint = self.load_checkpoint()
        completed = set(checkpoint.completed_urls)
        for target in await self.enumerate_targets():
            if target.url in completed:
                continue
            result = await self.fetch(target)
            if result.not_modified:
                # 304 is a clean success — nothing new to store.
                checkpoint = checkpoint.with_progress(
                    url=target.url, bytes_fetched=0
                )
                self.save_checkpoint(checkpoint)
                continue

            is_new = self.http.remember_hash(target.url, result.content_hash)
            if not is_new:
                # Content unchanged since last run — skip storage.
                checkpoint = checkpoint.with_progress(
                    url=target.url, bytes_fetched=result.content_size
                )
                self.save_checkpoint(checkpoint)
                continue

            envelope = RawCaptureRequestV1(
                source_slug=self.source_slug,
                url=target.url,
                content=result.content,
                content_hash=result.content_hash,
                content_type=self._guess_content_type(target, result),
                fetched_at=result.fetched_at,
                etag=result.etag,
                last_modified=result.last_modified,
                parse_hint=self.parse_hint(target, result),
                correlation_id=self.correlation_id,
                meta={"status_code": result.status_code},
            )
            checkpoint = checkpoint.with_progress(
                url=target.url, bytes_fetched=result.content_size
            )
            self.save_checkpoint(checkpoint)
            yield envelope

    async def run(self) -> list[RawCaptureRequestV1]:
        """Drain :meth:`collect` into a list (convenience for sync callers)."""
        return [item async for item in self.collect()]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _guess_content_type(target: FetchTarget, result: FetchResult) -> str:
        kind = target.kind.lower()
        if kind == "pdf":
            return "application/pdf"
        if kind == "json":
            return "application/json"
        if kind == "feed":
            return "application/rss+xml"
        if kind == "file":
            return "application/octet-stream"
        return "text/html"

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} slug={self.source_slug!r}>"
