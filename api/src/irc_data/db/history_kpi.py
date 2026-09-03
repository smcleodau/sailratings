"""OPS-02-12 acceptance KPI: TCC rating-history coverage.

The issue's acceptance criterion:

    >= 60% of boats that raced in the last 24 months have
    >= 3 years of TCC history.

"TCC history" lives in ``tcc_snapshots`` (one row per boat per snapshot
date).  "Years of history" is measured two ways and both are reported so
the number is auditable:

* ``span_years`` — calendar years between a boat's earliest and latest
  snapshot (``MAX - MIN`` year).  This is the "rating-history chart"
  depth the issue cares about: does the boat have a multi-year curve?
* ``distinct_years`` — count of distinct ``cert_year`` / snapshot years.
  Stricter; a boat present in 2010, 2015, 2024 snapshots still has
  span_years = 14 even though distinct_years = 3.

The headline KPI uses ``span_years >= 3`` — a boat "has 3 years of TCC
history" when its recorded rating curve covers a 3-year window.  This
matches how the moat is framed ("rating-history charts worth indexing"):
continuity over a window, not N discrete data points.

Both are returned so the acceptance query and the admin_metrics evidence
rows carry the full picture.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

#: Headline acceptance threshold (fraction, not percent).
ACCEPTANCE_THRESHOLD = 0.60

_RACERS_WINDOW_SQL = """
SELECT DISTINCT boat_id
  FROM race_results
 WHERE boat_id IS NOT NULL
   AND event_date IS NOT NULL
   AND event_date >= CURRENT_DATE - (:window_months || ' months')::interval
"""

_HISTORY_SQL = """
SELECT boat_id,
       (EXTRACT(YEAR FROM MAX(snapshot_date))
        - EXTRACT(YEAR FROM MIN(snapshot_date)))::int AS span_years,
       COUNT(DISTINCT COALESCE(cert_year,
                               EXTRACT(YEAR FROM snapshot_date)::int))
           AS distinct_years,
       COUNT(*) AS snapshots
  FROM tcc_snapshots
 GROUP BY boat_id
"""


def compute_tcc_history_kpi(engine: Engine, *, window_months: int = 24) -> dict:
    """Return the acceptance KPI as a dict.

    Keys:

    * ``window_months``       — the racer-recency window used.
    * ``racers``              — distinct boats that raced in the window.
    * ``with_3y_span``        — of those, boats whose TCC history spans
                                >= 3 calendar years.
    * ``with_3y_distinct``    — of those, boats with >= 3 distinct
                                snapshot years (stricter measure).
    * ``pct_span``            — ``with_3y_span / racers`` (0..1).
    * ``pct_distinct``        — ``with_3y_distinct / racers`` (0..1).
    * ``meets_acceptance``    — ``pct_span >= ACCEPTANCE_THRESHOLD``.
    """
    with engine.connect() as conn:
        racers = {
            r[0]
            for r in conn.execute(
                text(_RACERS_WINDOW_SQL), {"window_months": window_months}
            ).fetchall()
        }
        hist = {
            r[0]: (int(r[1] or 0), int(r[2] or 0))
            for r in conn.execute(text(_HISTORY_SQL)).fetchall()
        }

    n_racers = len(racers)
    with_span = sum(1 for b in racers if hist.get(b, (0, 0))[0] >= 3)
    with_distinct = sum(1 for b in racers if hist.get(b, (0, 0))[1] >= 3)

    pct_span = (with_span / n_racers) if n_racers else 0.0
    pct_distinct = (with_distinct / n_racers) if n_racers else 0.0

    return {
        "window_months": window_months,
        "racers": n_racers,
        "with_3y_span": with_span,
        "with_3y_distinct": with_distinct,
        "pct_span": pct_span,
        "pct_distinct": pct_distinct,
        "meets_acceptance": pct_span >= ACCEPTANCE_THRESHOLD,
    }


# A single self-contained SQL rendering of the KPI, for direct `psql`
# verification (the "KPI query" the issue names as its verification).
KPI_QUERY = """
WITH recent_racers AS (
  SELECT DISTINCT boat_id
    FROM race_results
   WHERE boat_id IS NOT NULL
     AND event_date IS NOT NULL
     AND event_date >= CURRENT_DATE - INTERVAL '24 months'
),
hist AS (
  SELECT boat_id,
         (EXTRACT(YEAR FROM MAX(snapshot_date))
          - EXTRACT(YEAR FROM MIN(snapshot_date)))::int AS span_years
    FROM tcc_snapshots
   GROUP BY boat_id
)
SELECT COUNT(*)                                   AS racers_24m,
       COUNT(*) FILTER (WHERE h.span_years >= 3)  AS with_3y_history,
       ROUND(100.0 * COUNT(*) FILTER (WHERE h.span_years >= 3)
             / NULLIF(COUNT(*), 0), 1)            AS pct_with_3y_history
  FROM recent_racers r
  LEFT JOIN hist h ON h.boat_id = r.boat_id;
"""
