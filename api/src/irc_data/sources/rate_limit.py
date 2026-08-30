"""Per-domain rate limiter (INTERIM-POLICY.md §3.2, SPEC-012 §3.2).

Guarantees **at most one request per ``min_delay`` seconds per domain**,
plus up to ``jitter`` seconds of randomised slack so we don't hammer a
host in lock-step.  Backs off exponentially on transient 5xx and honours
``Retry-After`` on 429 responses.

The limiter is async-first (``await limiter.wait(domain)``) because every
adapter is an async iterator.  A synchronous helper is provided for the
rare sync call-sites that still exist in legacy scrapers.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict

from .contracts import (
    DEFAULT_JITTER_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_DELAY_SECONDS,
)

__all__ = ["RateLimiter"]


class RateLimiter:
    """Token-bucket-style per-domain min-interval limiter with jitter.

    Parameters
    ----------
    min_delay:
        Minimum seconds between two requests to the *same* domain.
    jitter:
        Upper bound on extra random delay added to every wait.
    """

    def __init__(
        self,
        min_delay: float = DEFAULT_MIN_DELAY_SECONDS,
        jitter: float = DEFAULT_JITTER_SECONDS,
    ) -> None:
        if min_delay < 0:
            raise ValueError("min_delay must be non-negative")
        if jitter < 0:
            raise ValueError("jitter must be non-negative")
        self.min_delay = min_delay
        self.jitter = jitter
        self._last_request: dict[str, float] = defaultdict(float)
        # ``_now`` is overridable in tests so we don't actually sleep.
        self._now = time.monotonic
        self._sleep = asyncio.sleep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def wait(self, domain: str) -> float:
        """Block until a request to *domain* is permitted.

        Returns the number of seconds actually slept (0.0 if no wait
        was needed).  Tests typically monkey-patch ``_sleep`` so this
        is effectively free.
        """
        delay = self._compute_delay(domain)
        if delay > 0:
            await self._sleep(delay)
        return delay

    def wait_sync(self, domain: str) -> float:
        """Synchronous variant for legacy call-sites."""
        delay = self._compute_delay(domain)
        if delay > 0:
            time.sleep(delay)
        return delay

    def reset(self, domain: str | None = None) -> None:
        """Forget the last-request timestamp for *domain* (or all)."""
        if domain is None:
            self._last_request.clear()
        else:
            self._last_request.pop(domain, None)

    # ------------------------------------------------------------------
    # Retry back-off
    # ------------------------------------------------------------------
    @staticmethod
    def backoff_seconds(attempt: int) -> float:
        """Exponential back-off for the *n*th retry (2s → 4s → 8s → 16s)."""
        if attempt < 0:
            attempt = 0
        # 2 ** (attempt + 1) → 2, 4, 8, 16 (INTERIM-POLICY.md §3.2).
        return float(2 ** (attempt + 1))

    @staticmethod
    def retry_after_seconds(retry_after: str | None) -> float | None:
        """Parse a ``Retry-After`` header value (seconds only, per RFC)."""
        if not retry_after:
            return None
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            # HTTP-date form is allowed by RFC 7231 but our sources are
            # all known to use the delta-seconds form; treat the rest as
            # "no guidance" so we fall back to exponential back-off.
            return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _compute_delay(self, domain: str) -> float:
        now = self._now()
        last = self._last_request.get(domain, 0.0)
        elapsed = now - last
        target = self.min_delay + (random.uniform(0, self.jitter) if self.jitter else 0.0)
        delay = target - elapsed
        # Record the *scheduled* next slot so concurrent waiters queue up
        # rather than all racing past the gate together.
        self._last_request[domain] = max(last + target, now + max(delay, 0.0))
        return max(delay, 0.0)
