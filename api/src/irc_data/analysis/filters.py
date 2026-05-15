"""Race result filtering for analytics engines.

Centralised filtering rules so all engines apply the same criteria.
These filters exclude results that would pollute competitive analysis:
- Twilight/casual races (not representative of competitive performance)
- Non-IRC divisions (PHS, ORC-only results have different dynamics)
- Results where a boat was racing on non-spinnaker TCC

These filters do NOT delete data — they control what feeds into the
analytics engines. All raw data is preserved in the database.
"""

# SQL WHERE clause fragments for filtering race results.
# Use these in any query that feeds analytics engines.

# Exclude twilight/casual races
EXCLUDE_TWILIGHT = """
    AND LOWER(COALESCE(r.event_name, '')) NOT LIKE '%%twilight%%'
    AND LOWER(COALESCE(r.race_name, ''))  NOT LIKE '%%twilight%%'
"""

# Only include results where the boat had an IRC rating
REQUIRE_IRC_RATING = """
    AND r.rating_value IS NOT NULL
"""

# Exclude results where the boat was racing on non-spinnaker TCC.
# Detected by comparing rating_value to the boat's known non_spi_tcc —
# if they match (and differ from spi TCC), the boat was racing non-spi.
# This is a heuristic; we can't always tell from the data alone.
EXCLUDE_NON_SPI_RACING = """
    AND NOT EXISTS (
        SELECT 1 FROM tcc_snapshots ts
        WHERE ts.boat_id = r.boat_id
          AND ts.non_spi_tcc IS NOT NULL
          AND ts.tcc IS NOT NULL
          AND ABS(r.rating_value - ts.non_spi_tcc) < 0.001
          AND ABS(r.rating_value - ts.tcc) > 0.003
          AND ts.snapshot_date = (
              SELECT MAX(snapshot_date) FROM tcc_snapshots
              WHERE boat_id = r.boat_id
                AND snapshot_date <= COALESCE(r.event_date, r.race_date_specific, '2099-01-01')
          )
    )
"""

# Combined filter for competitive IRC spinnaker racing
COMPETITIVE_IRC_FILTER = (
    EXCLUDE_TWILIGHT
    + REQUIRE_IRC_RATING
    + EXCLUDE_NON_SPI_RACING
)

# Lighter filter — just exclude twilight and require IRC
# (doesn't check non-spi, faster for large queries)
BASIC_IRC_FILTER = (
    EXCLUDE_TWILIGHT
    + REQUIRE_IRC_RATING
)


def competitive_race_filter() -> str:
    """Return the full competitive IRC filter clause.

    Use in SQL queries like:
        SELECT ... FROM race_results r
        WHERE r.status = 'finished'
        {competitive_race_filter()}
    """
    return COMPETITIVE_IRC_FILTER


def basic_race_filter() -> str:
    """Return the basic IRC filter (no non-spi check)."""
    return BASIC_IRC_FILTER
