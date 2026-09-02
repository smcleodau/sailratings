"""Provider-agnostic crawl telemetry + credit budget gate (OPS-01-05).

Goal: never run out of crawl budget silently.

This module is the telemetry/budget layer that sits *under* the concrete
provider client (``firecrawl_client`` today). It is deliberately
provider-agnostic — everything is keyed off a ``provider`` string so a
second crawl provider can be added without touching the schema:

Per-call ledger
    ``log_call()`` — every crawl/map/scrape call is logged with mode, URL,
    domain, status, credits, latency and caller. Best-effort: a telemetry
    failure never breaks a scrape.

Aggregates
    ``window_aggregates()`` — today / 7-day / 30-day rollups.
    ``domain_aggregates()`` — per-domain rollup over a trailing window
    (7 days by default), with success rate and average latency.

Budget
    ``credit_balance()`` — balance, projected monthly burn and headroom.
    Balance is the provider-reported remaining credits when available
    (authoritative), else ``period_budget − period_spend`` from our ledger.
    Projected burn extrapolates the trailing 7-day average daily spend to
    a 30-day month; headroom is balance minus projection.

Caps / throttling
    ``check_throttle()`` — soft/hard cap gate evaluated *before* a provider
    call. Under the soft cap everything flows. At/over the soft cap,
    ``discovery``-class callers (bulk, deferrable work) are refused while
    interactive/manual calls continue with a warning. At/over the hard cap
    everything except ``manual`` is refused. Every decision (including
    allows once past the soft cap) is written to ``crawl_throttle_events``
    so throttling is never silent.

Env overrides (useful for ops experiments and tests):
    ``CRAWL_CREDIT_PERIOD_BUDGET`` — credits per billing period (default 100000)
    ``CRAWL_SOFT_CAP_FRAC``        — soft cap fraction (default 0.80)
    ``CRAWL_HARD_CAP_FRAC``        — hard cap fraction (default 0.95)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / configuration
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER = os.environ.get("CRAWL_PROVIDER", "firecrawl")
DEFAULT_PERIOD_CREDITS = int(os.environ.get("CRAWL_CREDIT_PERIOD_BUDGET", "100000"))
DEFAULT_SOFT_CAP_FRAC = float(os.environ.get("CRAWL_SOFT_CAP_FRAC", "0.80"))
DEFAULT_HARD_CAP_FRAC = float(os.environ.get("CRAWL_HARD_CAP_FRAC", "0.95"))

#: Callers matching these substrings do bulk, deferrable work — they are the
#: first thing refused at the soft cap so interactive/ingest flows keep
#: their headroom.
DISCOVERY_CALLER_HINTS: tuple[str, ...] = ("discover", "seed", "crawl", "map")

#: Callers that are never blocked — a human poking at the CLI should always
#: be able to spend the last few credits.
NEVER_BLOCK_CALLERS: frozenset[str] = frozenset({"manual", "admin", "justin"})


class CrawlBudgetExhausted(RuntimeError):
    """Raised before a provider call when the credit budget gate says no."""


@dataclass
class ThrottleDecision:
    """Outcome of one ``check_throttle`` evaluation."""

    allowed: bool
    action: str           # 'allow' | 'warn' | 'soft_block' | 'hard_block'
    reason: str
    provider: str
    used_credits: int
    soft_cap: int
    hard_cap: int
    utilization: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "reason": self.reason,
            "provider": self.provider,
            "used_credits": self.used_credits,
            "soft_cap": self.soft_cap,
            "hard_cap": self.hard_cap,
            "utilization": round(self.utilization, 4),
        }


# ---------------------------------------------------------------------------
# Engine resolution
# ---------------------------------------------------------------------------

# Test hook: set via ``set_engine()`` to avoid touching irc_data.config /
# the module-level engine singleton in ``db.connection``.
_engine_override: Engine | None = None


def set_engine(engine: Engine | None) -> None:
    """Force the engine used by every telemetry call (tests, scripts)."""
    global _engine_override
    _engine_override = engine


def _get_engine(engine: Engine | None) -> Engine:
    if engine is not None:
        return engine
    if _engine_override is not None:
        return _engine_override
    from irc_data.db.connection import get_engine

    return get_engine()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def domain_of(url: str) -> str:
    """Normalised host for group-by ('www.foo.com' -> 'foo.com')."""
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _credits_default(credits: int | None) -> int:
    """The provider usually reports credits_used; when it doesn't we count 1
    so 'cheap but uncounted' calls can never fly under the budget radar."""
    return int(credits) if credits is not None else 1


def _is_discovery_caller(caller: str | None) -> bool:
    c = (caller or "discovery").lower()
    return any(h in c for h in DISCOVERY_CALLER_HINTS)


def _is_never_block(caller: str | None) -> bool:
    return (caller or "").lower() in NEVER_BLOCK_CALLERS


# ---------------------------------------------------------------------------
# Per-call ledger
# ---------------------------------------------------------------------------

def log_call(
    engine: Engine | None = None,
    *,
    provider: str = DEFAULT_PROVIDER,
    mode: str,
    url: str,
    status: str,
    duration_ms: int,
    credits: int | None,
    response_chars: int | None = None,
    links_found: int | None = None,
    error_message: str | None = None,
    caller: str | None = None,
    called_at: datetime | None = None,
) -> None:
    """Best-effort write of one provider call to the ledger. Never raises.

    ``mode`` is 'scrape' | 'map' | 'crawl'; ``status`` is 'ok' | 'empty' |
    'error'. ``called_at`` is injectable for fixtures that backfill history.
    """
    try:
        eng = _get_engine(engine)
        row = {
            "mode": mode,
            "url": url,
            "domain": domain_of(url),
            "status": status,
            "credits": _credits_default(credits),
            "duration_ms": int(duration_ms),
            "response_chars": response_chars,
            "links_found": links_found,
            "error_message": (error_message or "")[:500] or None,
            "caller": caller or os.environ.get("FIRECRAWL_CALLER", "discovery"),
        }
        with eng.begin() as conn:
            if called_at is not None:
                conn.execute(text("""
                    INSERT INTO firecrawl_calls
                      (called_at, mode, url, domain, status, credits, duration_ms,
                       response_chars, links_found, error_message, caller)
                    VALUES
                      (:called_at, :mode, :url, :domain, :status, :credits, :duration_ms,
                       :response_chars, :links_found, :error_message, :caller)
                """), {**row, "called_at": called_at})
            else:
                conn.execute(text("""
                    INSERT INTO firecrawl_calls
                      (mode, url, domain, status, credits, duration_ms,
                       response_chars, links_found, error_message, caller)
                    VALUES
                      (:mode, :url, :domain, :status, :credits, :duration_ms,
                       :response_chars, :links_found, :error_message, :caller)
                """), row)
    except Exception:  # noqa: BLE001 — telemetry must never break a scrape
        logger.exception("crawl ledger insert failed (non-fatal)")


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

def window_aggregates(
    engine: Engine | None = None,
    *,
    provider: str = DEFAULT_PROVIDER,  # reserved: ledger is single-provider today
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Today / 7-day / 30-day call + credit rollups from the ledger.

    Returns {"today": {...}, "7d": {...}, "30d": {...}} with calls, credits,
    status/mode breakdowns, average latency and distinct-domain counts.
    ``now`` is injectable so tests can anchor the windows deterministically.
    """
    anchor = now or datetime.now(timezone.utc)
    try:
        eng = _get_engine(engine)
        with eng.connect() as conn:
            # Dialect-portable: CAST for Postgres labels, SUM(CASE …) works
            # everywhere (SQLite included) unlike FILTER (WHERE …).
            rows = conn.execute(text("""
                WITH windows AS (
                    SELECT CAST('today' AS TEXT) AS window, :t1 AS since
                    UNION ALL SELECT '7d',  :t7
                    UNION ALL SELECT '30d', :t30
                )
                SELECT w.window,
                       COUNT(c.id)                                AS calls,
                       COALESCE(SUM(c.credits), 0)                AS credits,
                       SUM(CASE WHEN c.status = 'ok'     THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN c.status = 'empty'  THEN 1 ELSE 0 END) AS empty,
                       SUM(CASE WHEN c.status = 'error'  THEN 1 ELSE 0 END) AS errored,
                       SUM(CASE WHEN c.mode = 'scrape'   THEN 1 ELSE 0 END) AS scrapes,
                       SUM(CASE WHEN c.mode = 'map'      THEN 1 ELSE 0 END) AS maps,
                       SUM(CASE WHEN c.mode = 'crawl'    THEN 1 ELSE 0 END) AS crawls,
                       COALESCE(AVG(c.duration_ms), 0)              AS avg_ms,
                       COUNT(DISTINCT c.domain)                     AS domains
                FROM windows w
                LEFT JOIN firecrawl_calls c ON c.called_at >= w.since
                GROUP BY w.window
            """), {
                "t1": anchor - timedelta(hours=24),
                "t7": anchor - timedelta(days=7),
                "t30": anchor - timedelta(days=30),
            }).fetchall()
        return {
            r.window: {
                "calls": int(r.calls),
                "credits": int(r.credits),
                "ok": int(r.ok),
                "empty": int(r.empty),
                "errored": int(r.errored),
                "scrapes": int(r.scrapes),
                "maps": int(r.maps),
                "crawls": int(r.crawls),
                "avg_ms": int(round(float(r.avg_ms))),
                "domains": int(r.domains),
            }
            for r in rows
        }
    except Exception:  # noqa: BLE001 — dashboards degrade, never crash
        logger.exception("window_aggregates failed")
        return {}


def domain_aggregates(
    engine: Engine | None = None,
    *,
    days: int = 7,
    limit: int = 100,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Per-domain rollup over the trailing ``days`` (default 7).

    Sorted by call count desc so the noisiest / most expensive domains
    surface first. Includes success rate and average latency.
    """
    days = max(1, min(int(days), 90))
    anchor = now or datetime.now(timezone.utc)
    since = anchor - timedelta(days=days)
    try:
        eng = _get_engine(engine)
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT domain,
                       COUNT(*)                                   AS calls,
                       COALESCE(SUM(credits), 0)                  AS credits,
                       SUM(CASE WHEN status = 'ok'    THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN status = 'empty' THEN 1 ELSE 0 END) AS empty,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errored,
                       COALESCE(AVG(duration_ms), 0)              AS avg_ms,
                       MAX(called_at)                             AS last_called
                FROM firecrawl_calls
                WHERE called_at > :since
                GROUP BY domain
                ORDER BY calls DESC, domain
                LIMIT :limit
            """), {"since": since, "limit": limit}).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            calls = int(r.calls)
            out.append({
                "domain": r.domain or "(unknown)",
                "calls": calls,
                "credits": int(r.credits),
                "ok": int(r.ok),
                "empty": int(r.empty),
                "errored": int(r.errored),
                "success_rate": (int(r.ok) / calls) if calls else 0.0,
                "avg_ms": int(round(float(r.avg_ms))),
                "last_called": (lc.isoformat() if (lc := _coerce_dt(r.last_called)) else None),
            })
        return out
    except Exception:  # noqa: BLE001
        logger.exception("domain_aggregates failed")
        return []


# ---------------------------------------------------------------------------
# Budget: balance, projected burn, headroom
# ---------------------------------------------------------------------------

def _period_start(engine: Engine, provider: str) -> datetime:
    """Start of the current budget period (settings row, else month start)."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT period_start FROM crawl_budget_settings WHERE provider = :p
        """), {"p": provider}).first()
    if row and row[0]:
        ps = row[0]
        if isinstance(ps, str):  # SQLite hands back 'YYYY-MM-DD HH:MM:SS'
            ps = datetime.fromisoformat(ps)
        return ps if ps.tzinfo else ps.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _period_spend(engine: Engine, provider: str, since: datetime) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("""
            SELECT COALESCE(SUM(credits), 0) FROM firecrawl_calls
            WHERE called_at >= :since
        """), {"since": since}).scalar() or 0)


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _coerce_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _get_settings(engine: Engine, provider: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT period_credits, soft_cap_frac, hard_cap_frac, period_start
            FROM crawl_budget_settings WHERE provider = :p
        """), {"p": provider}).first()
    if row:
        return {
            "period_credits": int(row[0]),
            "soft_cap_frac": float(row[1]),
            "hard_cap_frac": float(row[2]),
            "period_start": _coerce_dt(row[3]),
        }
    return {
        "period_credits": DEFAULT_PERIOD_CREDITS,
        "soft_cap_frac": DEFAULT_SOFT_CAP_FRAC,
        "hard_cap_frac": DEFAULT_HARD_CAP_FRAC,
        "period_start": None,
    }


def upsert_settings(
    engine: Engine | None = None,
    *,
    provider: str = DEFAULT_PROVIDER,
    period_credits: int | None = None,
    soft_cap_frac: float | None = None,
    hard_cap_frac: float | None = None,
    period_start: datetime | None = None,
) -> None:
    """Create/update the budget row for a provider (admin surface).

    Unspecified fields keep their current values (or the env-var defaults
    on first insert); ``period_start`` defaults to the start of the
    current month.
    """
    eng = _get_engine(engine)
    defaults = {
        "period_credits": period_credits
        if period_credits is not None else DEFAULT_PERIOD_CREDITS,
        "soft_cap_frac": soft_cap_frac
        if soft_cap_frac is not None else DEFAULT_SOFT_CAP_FRAC,
        "hard_cap_frac": hard_cap_frac
        if hard_cap_frac is not None else DEFAULT_HARD_CAP_FRAC,
        "period_start": period_start or _month_start(),
    }
    with eng.begin() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM crawl_budget_settings WHERE provider = :p
        """), {"p": provider}).first()
        if exists:
            # Update only the fields the caller actually supplied.
            sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: dict[str, Any] = {"p": provider}
            for field in ("period_credits", "soft_cap_frac", "hard_cap_frac",
                          "period_start"):
                val = {
                    "period_credits": period_credits,
                    "soft_cap_frac": soft_cap_frac,
                    "hard_cap_frac": hard_cap_frac,
                    "period_start": period_start,
                }[field]
                if val is not None:
                    sets.append(f"{field} = :{field}")
                    params[field] = val
            conn.execute(text(
                f"UPDATE crawl_budget_settings SET {', '.join(sets)} "
                "WHERE provider = :p"
            ), params)
        else:
            conn.execute(text("""
                INSERT INTO crawl_budget_settings
                  (provider, period_credits, soft_cap_frac, hard_cap_frac,
                   period_start)
                VALUES (:p, :pc, :sc, :hc, :ps)
            """), {
                "p": provider, "pc": defaults["period_credits"],
                "sc": defaults["soft_cap_frac"], "hc": defaults["hard_cap_frac"],
                "ps": defaults["period_start"],
            })


def credit_balance(
    engine: Engine | None = None,
    *,
    provider: str = DEFAULT_PROVIDER,
    provider_balance: Callable[[], dict[str, Any] | None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Balance, projected monthly burn and headroom for one provider.

    - ``balance_credits`` — provider-reported remaining credits when a
      ``provider_balance`` probe is supplied and answers (authoritative);
      otherwise ``period_credits − period_spend`` from our own ledger.
    - ``projected_monthly_burn`` — trailing 7-day average daily credits × 30.
    - ``headroom_credits`` — balance minus projection (negative = on current
      trajectory we exhaust the budget before the month is out).
    """
    eng = _get_engine(engine)
    settings = _get_settings(eng, provider)
    period_start = settings["period_start"] or _period_start(eng, provider)
    period_spend = _period_spend(eng, provider, period_start)
    windows = window_aggregates(eng, provider=provider, now=now)
    burn_7d = windows.get("7d", {}).get("credits", 0)

    probe: dict[str, Any] | None = None
    if provider_balance is not None:
        try:
            probe = provider_balance()
        except Exception as e:  # noqa: BLE001
            logger.warning("provider balance probe failed: %s", e)

    ledger_balance = settings["period_credits"] - period_spend
    if probe and probe.get("remaining_credits") is not None:
        balance = int(probe["remaining_credits"])
        source = "provider"
    else:
        balance = ledger_balance
        source = "ledger"

    projected = int(round(burn_7d * (30.0 / 7.0)))
    headroom = balance - projected
    return {
        "provider": provider,
        "balance_credits": balance,
        "balance_source": source,
        "provider_reported": probe,
        "period_credits": settings["period_credits"],
        "period_start": period_start.isoformat() if period_start else None,
        "period_spend": period_spend,
        "ledger_balance": ledger_balance,
        "burn_7d": burn_7d,
        "burn_30d": windows.get("30d", {}).get("credits", 0),
        "projected_monthly_burn": projected,
        "headroom_credits": headroom,
        "soft_cap": int(settings["period_credits"] * settings["soft_cap_frac"]),
        "hard_cap": int(settings["period_credits"] * settings["hard_cap_frac"]),
        "as_of": (now or datetime.now(timezone.utc)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Caps / throttling gate
# ---------------------------------------------------------------------------

def _record_throttle(
    engine: Engine | None,
    decision: ThrottleDecision,
    *,
    caller: str | None,
    mode: str | None,
    url: str | None,
) -> None:
    """Ledger of throttle decisions — the 'not silent' half of the budget."""
    try:
        eng = _get_engine(engine)
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO crawl_throttle_events
                  (provider, action, caller, mode, url,
                   used_credits, soft_cap, hard_cap, utilization, message)
                VALUES
                  (:provider, :action, :caller, :mode, :url,
                   :used, :soft, :hard, :util, :msg)
            """), {
                "provider": decision.provider,
                "action": decision.action,
                "caller": caller,
                "mode": mode,
                "url": url,
                "used": decision.used_credits,
                "soft": decision.soft_cap,
                "hard": decision.hard_cap,
                "util": decision.utilization,
                "msg": decision.reason,
            })
    except Exception:  # noqa: BLE001
        logger.exception("crawl_throttle_events insert failed (non-fatal)")


def recent_throttle_events(
    engine: Engine | None = None,
    *,
    limit: int = 100,
    blocked_only: bool = False,
) -> list[dict[str, Any]]:
    """Most recent throttle decisions, newest first."""
    try:
        eng = _get_engine(engine)
        where = "WHERE action IN ('soft_block', 'hard_block')" if blocked_only else ""
        with eng.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT id, created_at, provider, action, caller, mode, url,
                       used_credits, soft_cap, hard_cap, utilization, message
                FROM crawl_throttle_events
                {where}
                ORDER BY created_at DESC
                LIMIT :limit
            """), {"limit": max(1, min(int(limit), 500))}).fetchall()
        return [
            {
                "id": r.id,
                "created_at": (ca.isoformat() if (ca := _coerce_dt(r.created_at)) else None),
                "provider": r.provider,
                "action": r.action,
                "caller": r.caller,
                "mode": r.mode,
                "url": r.url,
                "used_credits": r.used_credits,
                "soft_cap": r.soft_cap,
                "hard_cap": r.hard_cap,
                "utilization": r.utilization,
                "message": r.message,
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001
        logger.exception("recent_throttle_events failed")
        return []


def check_throttle(
    engine: Engine | None = None,
    *,
    provider: str = DEFAULT_PROVIDER,
    caller: str | None = None,
    mode: str | None = None,
    url: str | None = None,
    reserve_credits: int = 1,
    record: bool = True,
) -> ThrottleDecision:
    """Evaluate soft/hard credit caps *before* a provider call.

    Thresholds are fractions of the period budget (from
    ``crawl_budget_settings``, falling back to the env-var defaults):

    - used < soft                        → allow
    - soft ≤ used < hard                 → discovery-class callers are
      refused (``soft_block``); others proceed with ``warn``
    - used ≥ hard (or reserve would tip) → everything except ``manual``-class
      callers is refused (``hard_block``)

    Decisions at/above the soft cap — including allows — are written to
    ``crawl_throttle_events`` so the throttling onset is auditable rather
    than silent. Telemetry failures fail-open (allow) — a broken ledger must
    not take discovery down with it.
    """
    caller_norm = caller or os.environ.get("FIRECRAWL_CALLER", "discovery")
    try:
        eng = _get_engine(engine)
        settings = _get_settings(eng, provider)
        period_credits = settings["period_credits"]
        soft_cap = int(period_credits * settings["soft_cap_frac"])
        hard_cap = int(period_credits * settings["hard_cap_frac"])
        period_start = settings["period_start"] or _period_start(eng, provider)
        used = _period_spend(eng, provider, period_start)
    except Exception as e:  # noqa: BLE001 — fail-open
        logger.exception("check_throttle: budget state unavailable, allowing")
        return ThrottleDecision(
            allowed=True, action="allow",
            reason=f"budget state unavailable ({e}); failing open",
            provider=provider, used_credits=0, soft_cap=0, hard_cap=0,
            utilization=0.0,
        )

    util = (used / period_credits) if period_credits else 1.0
    discovery = _is_discovery_caller(caller_norm)
    never_block = _is_never_block(caller_norm)

    if used >= hard_cap or (used + max(0, reserve_credits)) > hard_cap:
        if never_block:
            action, allowed, why = (
                "warn", True,
                f"manual caller over hard cap ({used}/{hard_cap}); allowed",
            )
        else:
            action, allowed, why = (
                "hard_block", False,
                f"hard credit cap reached: {used}/{hard_cap} period credits used "
                f"({util:.1%}); refusing {caller_norm!r} until the period rolls",
            )
    elif used >= soft_cap:
        if discovery:
            action, allowed, why = (
                "soft_block", False,
                f"soft credit cap reached: {used}/{soft_cap} soft-cap credits used "
                f"({util:.1%} of period); throttling discovery caller {caller_norm!r} "
                "before exhaustion",
            )
        else:
            action, allowed, why = (
                "warn", True,
                f"over soft cap ({used}/{soft_cap}); non-discovery caller "
                f"{caller_norm!r} allowed",
            )
    else:
        action, allowed, why = (
            "allow", True,
            f"under caps ({used}/{soft_cap} soft, {hard_cap} hard)",
        )

    decision = ThrottleDecision(
        allowed=allowed, action=action, reason=why, provider=provider,
        used_credits=used, soft_cap=soft_cap, hard_cap=hard_cap,
        utilization=util,
    )
    if record and action != "allow":
        _record_throttle(engine, decision, caller=caller_norm, mode=mode, url=url)
        log = logger.warning if allowed else logger.error
        log("crawl budget %s: %s (mode=%s url=%s)", action, why, mode, url)
    return decision
