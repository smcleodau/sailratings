"""Cadence / jitter / concurrency-cap helpers for the schedule registry.

The Data Source Register stores a human-ish cadence string (``nightly``,
``30min``, ``weekly`` …).  This module converts that into a concrete
``timedelta`` the Temporal schedule spec can consume, and exposes the
per-domain concurrency caps required by SPEC-13 §3.2.
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Cadence parsing
# ---------------------------------------------------------------------------

#: Named cadences → interval.  ``nightly`` is the register default and maps
#: to a 24 h interval; the actual wall-clock time is *jittered* by the
#: workflow so we don't thundering-herd at 02:00 UTC.
_NAMED_CADENCES: dict[str, timedelta] = {
    "manual": timedelta(days=365 * 10),  # effectively "off" unless triggered
    "hourly": timedelta(hours=1),
    "nightly": timedelta(hours=24),
    "daily": timedelta(hours=24),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    "quarterhourly": timedelta(minutes=15),
}

_CADENCE_RE = re.compile(r"^\s*(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|w|week|weeks)\s*$", re.I)

_UNIT_SECONDS = {
    "s": 1, "sec": 1, "secs": 1,
    "m": 60, "min": 60, "mins": 60,
    "h": 3600, "hr": 3600, "hrs": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}


def cadence_to_timedelta(cadence: str | None) -> timedelta:
    """Convert a register cadence string into a ``timedelta``.

    Accepts named cadences (``nightly``, ``weekly``, …) or compact
    durations (``30min``, ``6h``, ``2d``).  Falls back to ``nightly``
    (24 h) when the string is unrecognised — a safe default that keeps
    the register the source of truth without breaking the sync loop.
    """
    if not cadence:
        return _NAMED_CADENCES["nightly"]
    key = cadence.strip().lower()
    if key in _NAMED_CADENCES:
        return _NAMED_CADENCES[key]
    m = _CADENCE_RE.match(key)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        return timedelta(seconds=n * _UNIT_SECONDS[unit])
    # Unknown cadence — be conservative.
    return _NAMED_CADENCES["nightly"]


# ---------------------------------------------------------------------------
# Jitter
# ---------------------------------------------------------------------------

#: Maximum random jitter applied at workflow start, expressed as a fraction
#: of the cadence interval.  A nightly job jitters up to ~1.2 h; a 30 min
#: job up to ~1.5 min.  This is deterministic per (schedule, nominal) so
#: replays are stable, but varies across schedules.
MAX_JITTER_FRACTION = 0.05


# ---------------------------------------------------------------------------
# Per-domain concurrency caps (SPEC-13 §3.2)
# ---------------------------------------------------------------------------

#: Domains with known rate-limit / anti-scrape sensitivity.  Max number of
#: *concurrent in-flight* activities hitting the domain.  Sources not on the
#: map fall back to :data:`DEFAULT_DOMAIN_CONCURRENCY`.
DOMAIN_CONCURRENCY_CAPS: dict[str, int] = {
    "app.sailsys.com.au": 2,
    "sailsys.com.au": 2,
    "www.topyacht.net.au": 2,
    "topyacht.net.au": 2,
    "ircrating.org": 3,
    "data.orc.org": 3,
    "web.archive.org": 2,
    "www.sailwave.com": 3,
    "www.yachtscoring.com": 3,
    "www.sailracehq.com": 3,
    "www.cyca.com.au": 2,
}

#: Default cap when the domain isn't in :data:`DOMAIN_CONCURRENCY_CAPS`.
DEFAULT_DOMAIN_CONCURRENCY = 5


def domain_for_url(url: str | None) -> str:
    """Extract the hostname used for concurrency capping."""
    if not url:
        return ""
    host = urlparse(url).hostname or ""
    return host.lower()


def max_concurrency_for_domain(domain: str) -> int:
    """Return the concurrency cap for *domain*.

    Unknown domains get the default.  The empty-string domain (used by the
    in-process semaphore fallback for sources without a URL) is treated as
    its own bucket and given the default cap.
    """
    return DOMAIN_CONCURRENCY_CAPS.get((domain or "").lower(), DEFAULT_DOMAIN_CONCURRENCY)


# ---------------------------------------------------------------------------
# Stable schedule / workflow ids
# ---------------------------------------------------------------------------


def schedule_id_for_slug(slug: str) -> str:
    """Canonical Temporal schedule id for a source slug."""
    return f"source-{slug}"


def workflow_id_for_run(source_slug: str, run_key: str) -> str:
    """Canonical Temporal workflow id for a single source run.

    Deterministic on (source_slug, run_key) so retries of the same run are
    idempotent — a duplicate schedule fire re-uses the same workflow id and
    Temporal's workflow-id reuse policy deduplicates it.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_key)[:120]
    return f"source-run-{source_slug}-{safe}"
