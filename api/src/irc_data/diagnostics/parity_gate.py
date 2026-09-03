"""Firecrawl cutover parity gate (OPS-02-06).

Quantitative gate that decides whether a long-tail results source (ISORA,
SailRaceHQ, Cowes Week, …) may be **cut over** from its bespoke legacy
scraper to the Firecrawl discovery pipeline.

Two layers, both computed over a trailing window (default 14 days):

1. **Live parallel-run metrics** (primary, when present). When the source
   runs legacy + Firecrawl in parallel, both transports land in
   ``race_results`` tagged via the ``transport`` column. We compare, per
   event day:

   - ``row_capture``   = firecrawl_rows / legacy_rows   → must be ≥ 0.95
   - ``place1_agreement`` = fraction of shared event days where the
     place-1 (winner) boat name agrees between transports → must be ≥ 0.98

2. **firecrawl_diffs fallback**. When a source has not been run in parallel
   (no ``transport='firecrawl'`` rows in the window), the gate falls back to
   the ``firecrawl_diffs`` snapshot table populated by
   ``irc-data firecrawl-diff``. Row capture and place-1 agreement are then
   evaluated from those stored snapshots. ``firecrawl_diffs`` historically
   stores *recall* (``match_rate``); for the gate we persist the
   ratio-based ``firecrawl_rows / legacy_rows`` row-capture in the snapshot
   ``notes`` (``row_capture=…``) when captured via ``parity-gate --save`` so
   the gate can evaluate it directly. Place-1 agreement is not derivable
   from recall snapshots, so it is reported as ``None`` (skipped) unless a
   snapshot recorded ``place1_agreement=…``.

The command prints a JSON + human report and exits non-zero when the gate
does **not** pass, so it can be wired into a cron / CI as a guard::

    irc-data parity-gate --source isora
    irc-data parity-gate --source sailracehq --days 14 --json

Thresholds (SPEC-24 §5 / OPS-02-06 acceptance):

- window          14 days
- row capture     ≥ 0.95
- place-1 agr.    ≥ 0.98
- minimum sample  ≥ 5 shared event days (live) or ≥ 5 diff snapshots,
                    otherwise the gate reports ``insufficient_data`` and
                    does not pass (we never cut over on thin evidence).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import click
from sqlalchemy import text

from irc_data.db.connection import get_engine

# Gate thresholds (SPEC-24 §5).
ROW_CAPTURE_THRESHOLD = 0.95
PLACE1_AGREEMENT_THRESHOLD = 0.98
MIN_SAMPLE = 5
DEFAULT_DAYS = 14


def _norm_name(s: str | None) -> str:
    """Normalise a boat name for place-1 comparison.

    Mirrors the tolerant normalisation used by ``firecrawl-diff`` so the
    place-1 check isn't defeated by case / spacing / handedness suffixes.
    """
    if not s:
        return ""
    s = s.upper()
    s = re.sub(r"\s*\((DH|TH|DOUBLE.?HANDED|TWO.?HANDED)\)\s*", "", s)
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


@dataclass
class ParityGateResult:
    source: str
    window_days: int
    method: str                      # 'parallel' | 'diffs' | 'none'
    days_evaluated: int = 0
    legacy_rows: int = 0
    firecrawl_rows: int = 0
    row_capture: float | None = None
    place1_checks: int = 0
    place1_agreements: int = 0
    place1_agreement: float | None = None
    passed: bool = False
    reason: str = ""
    per_day: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Layer 1: live parallel-run comparison on race_results.transport
# ---------------------------------------------------------------------------

def _parallel_metrics(engine, source: str, days: int) -> dict[str, Any]:
    """Per-day legacy vs firecrawl row counts + place-1 agreement.

    Returns a dict with ``per_day`` rows and aggregate counts, or an empty
    dict when no parallel (firecrawl-transport) data exists in the window.

    Dialect-portable (Postgres + SQLite): the date cutoff is computed in
    Python, conditional aggregation uses ``SUM(CASE …)`` instead of
    ``FILTER (WHERE …)``, and place-1 winners are aggregated in Python rather
    than with Postgres ``ARRAY_AGG``.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows_sql = text("""
        SELECT
          event_date AS day,
          SUM(CASE WHEN transport = 'legacy'    THEN 1 ELSE 0 END) AS legacy_rows,
          SUM(CASE WHEN transport = 'firecrawl' THEN 1 ELSE 0 END) AS firecrawl_rows
        FROM race_results
        WHERE source = :source
          AND created_at >= :cutoff
          AND transport IN ('legacy', 'firecrawl')
        GROUP BY event_date
        ORDER BY day DESC;
    """)

    # place-1 (winner) boat per day per transport. Aggregated in Python for
    # portability (SQLite has no ARRAY_AGG and no ->> JSON operator).
    place1_sql = text("""
        SELECT event_date AS day, transport, raw_data
        FROM race_results
        WHERE source = :source
          AND created_at >= :cutoff
          AND transport IN ('legacy', 'firecrawl')
          AND place = 1;
    """)

    try:
        with engine.connect() as conn:
            all_day_rows = list(conn.execute(rows_sql, {"source": source, "cutoff": cutoff}))
            p1_rows = list(conn.execute(place1_sql, {"source": source, "cutoff": cutoff}))
    except Exception:  # noqa: BLE001 — e.g. race_results absent (diffs-only env)
        return {}

    # Keep only days where BOTH transports are present (a real parallel run).
    day_rows = [
        r for r in all_day_rows
        if int(r.legacy_rows or 0) > 0 and int(r.firecrawl_rows or 0) > 0
    ]
    if not day_rows:
        return {}

    # Build place-1 winners per day: {day_str: {'legacy': name, 'firecrawl': name}}
    winners: dict[str, dict[str, str]] = {}
    for r in p1_rows:
        day_str = str(r.day)
        winners.setdefault(day_str, {})
        raw = r.raw_data
        boat = ""
        if isinstance(raw, str):
            try:
                boat = (json.loads(raw) or {}).get("boat_name") or ""
            except Exception:
                boat = ""
        elif isinstance(raw, dict):
            boat = raw.get("boat_name") or ""
        # First place-1 boat seen for the (day, transport) wins.
        if not winners[day_str].get(r.transport):
            winners[day_str][r.transport] = boat

    per_day: list[dict[str, Any]] = []
    p1_checks = 0
    p1_agree = 0
    legacy_total = 0
    fc_total = 0
    for r in day_rows:
        day = r.day
        day_str = str(day)
        legacy_rows = int(r.legacy_rows)
        fc_rows = int(r.firecrawl_rows)
        legacy_total += legacy_rows
        fc_total += fc_rows
        w = winners.get(day_str, {})
        lw = _norm_name(w.get("legacy"))
        fw = _norm_name(w.get("firecrawl"))
        p1_match = None
        if lw and fw:
            p1_checks += 1
            p1_match = (lw == fw) or (lw in fw) or (fw in lw)
            if p1_match:
                p1_agree += 1
        per_day.append({
            "day": str(day),
            "legacy_rows": legacy_rows,
            "firecrawl_rows": fc_rows,
            "row_capture": round(fc_rows / legacy_rows, 4) if legacy_rows else None,
            "place1_legacy": w.get("legacy"),
            "place1_firecrawl": w.get("firecrawl"),
            "place1_match": p1_match,
        })

    return {
        "per_day": per_day,
        "days_evaluated": len(per_day),
        "legacy_rows": legacy_total,
        "firecrawl_rows": fc_total,
        "row_capture": (fc_total / legacy_total) if legacy_total else None,
        "place1_checks": p1_checks,
        "place1_agreements": p1_agree,
        "place1_agreement": (p1_agree / p1_checks) if p1_checks else None,
    }


# ---------------------------------------------------------------------------
# Layer 2: firecrawl_diffs snapshot fallback
# ---------------------------------------------------------------------------

_ROW_CAPTURE_RE = re.compile(r"row_capture=([0-9.]+)")
_PLACE1_RE = re.compile(r"place1_agreement=([0-9.]+)")


def _diffs_metrics(engine, source: str, days: int) -> dict[str, Any]:
    """Compute gate metrics from stored firecrawl_diffs snapshots.

    Only snapshots where the legacy side actually named boats
    (``legacy_rows > 0``) are usable. Row capture is read from the snapshot
    ``notes`` (``row_capture=…`` written by ``parity-gate --save``); when a
    snapshot predates that field we derive it as
    ``firecrawl_rows / legacy_rows`` directly from the two row counts.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with engine.connect() as conn:
        rows = list(conn.execute(text("""
            SELECT source_url, event_date, legacy_rows, firecrawl_rows,
                   match_rate, confidence, notes, ran_at
            FROM firecrawl_diffs
            WHERE source = :source
              AND ran_at >= :cutoff
              AND legacy_rows IS NOT NULL AND legacy_rows > 0
            ORDER BY ran_at DESC;
        """), {"source": source, "cutoff": cutoff}))

    if not rows:
        return {}

    per_day: list[dict[str, Any]] = []
    captures: list[float] = []
    p1_vals: list[float] = []
    legacy_total = 0
    fc_total = 0
    for r in rows:
        legacy_rows = int(r.legacy_rows)
        fc_rows = int(r.firecrawl_rows)
        legacy_total += legacy_rows
        fc_total += fc_rows
        capture = (fc_rows / legacy_rows) if legacy_rows else None
        notes = r.notes or ""
        m = _ROW_CAPTURE_RE.search(notes)
        if m:
            try:
                capture = float(m.group(1))
            except ValueError:
                pass
        if capture is not None:
            captures.append(capture)
        p1 = None
        pm = _PLACE1_RE.search(notes)
        if pm:
            try:
                p1 = float(pm.group(1))
                p1_vals.append(p1)
            except ValueError:
                p1 = None
        ran_at = r.ran_at
        if ran_at is not None and hasattr(ran_at, "isoformat"):
            ran_at = ran_at.isoformat()
        per_day.append({
            "source_url": r.source_url,
            "event_date": str(r.event_date) if r.event_date else None,
            "legacy_rows": legacy_rows,
            "firecrawl_rows": fc_rows,
            "row_capture": round(capture, 4) if capture is not None else None,
            "match_rate": float(r.match_rate) if r.match_rate is not None else None,
            "place1_agreement": p1,
            "ran_at": ran_at,
        })

    return {
        "per_day": per_day,
        "days_evaluated": len(per_day),
        "legacy_rows": legacy_total,
        "firecrawl_rows": fc_total,
        # Mean of per-URL captures (each URL is one observation), which is
        # more robust to one huge event than the pooled ratio.
        "row_capture": (sum(captures) / len(captures)) if captures else None,
        "place1_checks": len(p1_vals),
        "place1_agreements": sum(1 for v in p1_vals if v >= 0.999),
        "place1_agreement": (sum(p1_vals) / len(p1_vals)) if p1_vals else None,
    }


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def evaluate_parity_gate(
    engine,
    source: str,
    *,
    days: int = DEFAULT_DAYS,
    row_capture_threshold: float = ROW_CAPTURE_THRESHOLD,
    place1_threshold: float = PLACE1_AGREEMENT_THRESHOLD,
    min_sample: int = MIN_SAMPLE,
) -> ParityGateResult:
    """Evaluate the cutover parity gate for one source.

    Tries the live parallel-run comparison first; falls back to
    ``firecrawl_diffs`` snapshots. Returns a :class:`ParityGateResult`.
    """
    result = ParityGateResult(source=source, window_days=int(days), method="none")

    metrics = _parallel_metrics(engine, source, int(days))
    if metrics:
        result.method = "parallel"
    else:
        metrics = _diffs_metrics(engine, source, int(days))
        if metrics:
            result.method = "diffs"

    if not metrics:
        result.passed = False
        result.reason = (
            f"no parallel-run rows and no firecrawl_diffs snapshots for "
            f"source={source!r} in the last {days} days — nothing to gate on"
        )
        return result

    result.per_day = metrics["per_day"]
    result.days_evaluated = metrics["days_evaluated"]
    result.legacy_rows = metrics["legacy_rows"]
    result.firecrawl_rows = metrics["firecrawl_rows"]
    result.row_capture = (
        round(metrics["row_capture"], 4)
        if metrics["row_capture"] is not None else None
    )
    result.place1_checks = metrics["place1_checks"]
    result.place1_agreements = metrics["place1_agreements"]
    result.place1_agreement = (
        round(metrics["place1_agreement"], 4)
        if metrics["place1_agreement"] is not None else None
    )

    # --- Minimum sample -------------------------------------------------
    if result.days_evaluated < min_sample:
        result.passed = False
        result.reason = (
            f"insufficient_data: {result.days_evaluated} comparable "
            f"observation(s) in window < required {min_sample}"
        )
        return result

    # --- Row capture ----------------------------------------------------
    if result.row_capture is None:
        result.passed = False
        result.reason = "row_capture could not be computed (no legacy rows)"
        return result
    if result.row_capture < row_capture_threshold:
        result.passed = False
        result.reason = (
            f"row_capture {result.row_capture:.3f} < "
            f"{row_capture_threshold:.2f} over {result.days_evaluated} "
            f"observation(s) ({result.firecrawl_rows} firecrawl vs "
            f"{result.legacy_rows} legacy rows)"
        )
        return result

    # --- Place-1 agreement ----------------------------------------------
    # Only enforced when we actually have place-1 observations; if a source
    # has no place=1 rows (or diffs snapshots without the field), we report
    # it as skipped rather than failing the gate on absent data.
    if result.place1_checks > 0 and result.place1_agreement is not None:
        if result.place1_agreement < place1_threshold:
            result.passed = False
            result.reason = (
                f"place1_agreement {result.place1_agreement:.3f} < "
                f"{place1_threshold:.2f} over {result.place1_checks} "
                f"check(s) ({result.place1_agreements} agreements)"
            )
            return result

    result.passed = True
    p1_txt = (
        f"{result.place1_agreement:.3f}" if result.place1_agreement is not None
        else "n/a"
    )
    result.reason = (
        f"gate PASS ({result.method}): row_capture={result.row_capture:.3f} "
        f">= {row_capture_threshold:.2f}; place1_agreement={p1_txt} "
        f"(>= {place1_threshold:.2f}); n={result.days_evaluated}"
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command(name="parity-gate")
@click.option("--source", required=True,
              help="race_results.source value to gate (e.g. isora, sailracehq)")
@click.option("--days", default=DEFAULT_DAYS, type=int,
              help="Look-back window in days (default 14).")
@click.option("--min-sample", default=MIN_SAMPLE, type=int,
              help="Minimum comparable observations required (default 5).")
@click.option("--row-capture-threshold", default=ROW_CAPTURE_THRESHOLD, type=float,
              help="Minimum firecrawl/legacy row-capture ratio (default 0.95).")
@click.option("--place1-threshold", default=PLACE1_AGREEMENT_THRESHOLD, type=float,
              help="Minimum place-1 winner agreement fraction (default 0.98).")
@click.option("--json", "as_json", is_flag=True,
              help="Emit the result as JSON (machine-readable, for cron/CI).")
@click.option("--save", is_flag=True,
              help="Persist a snapshot row to firecrawl_diffs for the report "
                   "(used to build the 14-day evidence trail).")
def parity_gate(source, days, min_sample, row_capture_threshold,
                place1_threshold, as_json, save):
    """Evaluate the Firecrawl cutover parity gate for a source.

    PASS requires, over the trailing window: row capture >= 95% and place-1
    winner agreement >= 98%, on a minimum number of comparable observations.
    Exits non-zero when the gate does not pass.
    """
    engine = get_engine()
    result = evaluate_parity_gate(
        engine, source,
        days=days,
        row_capture_threshold=row_capture_threshold,
        place1_threshold=place1_threshold,
        min_sample=min_sample,
    )

    if save:
        _save_snapshot(engine, result)

    if as_json:
        click.echo(json.dumps(result.as_dict(), indent=2, default=str))
    else:
        _print_report(result)

    raise SystemExit(0 if result.passed else 1)


def _print_report(result: ParityGateResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    click.echo(f"parity-gate: {result.source}  window={result.window_days}d  "
               f"method={result.method}  ->  {status}")
    click.echo(f"  observations      : {result.days_evaluated}")
    click.echo(f"  legacy rows       : {result.legacy_rows}")
    click.echo(f"  firecrawl rows    : {result.firecrawl_rows}")
    rc = f"{result.row_capture:.3f}" if result.row_capture is not None else "n/a"
    click.echo(f"  row_capture       : {rc}  (threshold {ROW_CAPTURE_THRESHOLD:.2f})")
    p1 = (f"{result.place1_agreement:.3f}"
          if result.place1_agreement is not None else "n/a")
    click.echo(f"  place1_agreement  : {p1}  (threshold {PLACE1_AGREEMENT_THRESHOLD:.2f}, "
               f"{result.place1_agreements}/{result.place1_checks} checks)")
    click.echo(f"  reason            : {result.reason}")
    if result.per_day:
        click.echo("  per-observation:")
        for d in result.per_day[:20]:
            if "day" in d:
                click.echo(
                    f"    {d['day']:<12} legacy={d['legacy_rows']:>4} "
                    f"fc={d['firecrawl_rows']:>4} "
                    f"capture={d.get('row_capture')} p1={d.get('place1_match')}"
                )
            else:
                click.echo(
                    f"    {str(d.get('event_date')):<12} legacy={d['legacy_rows']:>4} "
                    f"fc={d['firecrawl_rows']:>4} capture={d.get('row_capture')} "
                    f"match_rate={d.get('match_rate')}"
                )


def _save_snapshot(engine, result: ParityGateResult) -> None:
    """Persist a parity-gate snapshot into firecrawl_diffs for the trail.

    One summary row per run, keyed by a synthetic source_url, carrying the
    aggregate row_capture and place1_agreement in ``notes`` so future gate
    evaluations can read them back via the diffs fallback.
    """
    if result.row_capture is None:
        return  # nothing meaningful to store
    p1 = (
        f"{result.place1_agreement:.4f}"
        if result.place1_agreement is not None else "nan"
    )
    notes = (
        f"parity-gate snapshot; method={result.method}; "
        f"row_capture={result.row_capture:.4f}; place1_agreement={p1}; "
        f"n={result.days_evaluated}; passed={result.passed}"
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO firecrawl_diffs
                  (source, source_url, event_name, event_date, legacy_rows,
                   firecrawl_rows, matched, match_rate, confidence, notes)
                VALUES
                  (:source, :url, :event_name, NULL, :legacy, :fc,
                   :matched, :rate, NULL, :notes)
            """), {
                "source": result.source,
                "url": f"parity-gate://{result.source}/aggregate",
                "event_name": f"parity-gate {result.window_days}d aggregate",
                "legacy": result.legacy_rows,
                "fc": result.firecrawl_rows,
                "matched": result.place1_agreements,
                # match_rate stores the aggregate row-capture so the
                # existing dashboard gate query keeps working.
                "rate": round(result.row_capture, 3),
                "notes": notes,
            })
    except Exception as e:  # noqa: BLE001 — snapshot persistence must not break the gate
        click.echo(f"[warn] failed to persist parity snapshot: {e}", err=True)
