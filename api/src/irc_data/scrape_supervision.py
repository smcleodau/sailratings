"""Shared config + helpers for scraper supervision.

Both the /justin/scrapers dashboard endpoint and the scrape-watchdog CLI
read these — single source of truth for what's expected to run, how often,
and the budget after which the data is considered stale.

Freshness budgets (OPS-02-03) are the contract the watchdog enforces and
the acceptance drill exercises:

* ORC ``orc_api``   — 26 h   (daily 03:00 UTC cron + 2 h slack)
* TCC ``irc_tcc``   — 26 h   (daily 06:00 UTC cron + 2 h slack)
* SailSys           — 2 h run (every-30-min cron) / 26 h data
* TopYacht          — 26 h   (daily 02:30 UTC cron + 2 h slack)
* weekly sources    — 8 d    (weekly cron + 1 d slack)

Budgets are intentionally ~1 cadence + a small slack so a single missed run
doesn't page anyone, but a *day-long* silent outage (the 37-day outage this
issue exists to prevent) always crosses the budget within one watchdog pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

# Terminal ingestion_log statuses that record why a run stopped.
ERROR_STATUSES = frozenset({"failed", "completed_with_errors"})


def require_error_message(
    status: str,
    error_message: str | None,
    context: str = "unspecified errors",
) -> str | None:
    """Guarantee a non-empty message whenever the status reports errors.

    OPS-02-02: 55 SailSys runs on 2026-09-02 ended ``completed_with_errors``
    with ``error_message = NULL``/'' — the row claimed something went wrong
    but carried no evidence, making the found>0/new=0 investigation blind.

    Contract enforced here (single place so every caller benefits):
      * ``status`` in {"failed", "completed_with_errors"} always yields a
        non-empty, non-whitespace message. An empty/blank/None message is
        replaced by a synthetic fallback built from ``context``.
      * Non-error statuses pass ``error_message`` through untouched (a
        ``completed`` row with an informational message is legal).

    ``context`` should name *what* failed, e.g. "3 errors importing results"
    or "club SASC" — it becomes the visible message when the caller failed
    to capture the underlying exception text.
    """
    if status in ERROR_STATUSES:
        if error_message and error_message.strip():
            return error_message
        return f"{status}: {context}"
    return error_message


@dataclass(frozen=True)
class SourceConfig:
    source: str  # matches `ingestion_log.source` values
    label: str
    cadence_human: str  # short human description of the cron schedule
    # Two distinct freshness signals — keep them separate. A scraper can be
    # behaving correctly (run_within fresh) yet legitimately bring back no new
    # races (data_within stale during a seasonal lull). Alerting on the wrong
    # one cries wolf.
    run_within: timedelta  # max gap between successful runs (cron health)
    data_within: timedelta | None = None  # max gap between new ingested rows; None disables this check
    optional: bool = False  # if True, never alert (annual events, manual sources)


# Order matters for the dashboard — most operationally critical first.
SOURCES: list[SourceConfig] = [
    SourceConfig(
        source="sailsys",
        label="SailSys (AU clubs)",
        cadence_human="every 30 min",
        # Run budget 2 h: a 30-min cron that hasn't succeeded in 2 h has
        # missed ~4 runs — worth a page.
        run_within=timedelta(hours=2),
        # Data budget 26 h: results flow in daily during the season, so a
        # full day with no new race rows is the "silent tap" signal. This is
        # deliberately tighter than a multi-week seasonal lull so a genuine
        # outage pages within a day; seasonal lulls are handled by the
        # optional/manual sources below, not by loosening this budget.
        data_within=timedelta(hours=26),
    ),
    SourceConfig(
        source="orc_api",
        label="ORC certificates",
        cadence_human="daily 03:00 UTC",
        run_within=timedelta(hours=26),
        # No data_within: orc_api writes to orc_certificates, not race_results,
        # so the current per-source data-tap query doesn't apply. Run-health
        # alone is sufficient signal here.
    ),
    SourceConfig(
        source="irc_tcc",
        label="IRC TCC Listings",
        cadence_human="daily 06:00 UTC",
        run_within=timedelta(hours=26),
        # No data_within: irc_tcc writes to tcc_snapshots, not race_results.
        # Run-health (via ingestion_log) is the correct signal here.
    ),
    SourceConfig(
        source="topyacht",
        label="TopYacht (AU/regattas)",
        cadence_human="daily 02:30 UTC",
        run_within=timedelta(hours=26),
        data_within=timedelta(hours=26),
    ),
    SourceConfig(
        source="sailracehq",
        label="SailRaceHQ (UK offshore)",
        cadence_human="weekly Tue 10:00 UTC",
        run_within=timedelta(days=8),
        # UK offshore season runs Apr-Sep; out-of-season expect long quiet.
        # No data_within budget — handle by hand.
    ),
    SourceConfig(
        source="isora",
        label="ISORA (Irish Sea)",
        cadence_human="weekly Tue 11:00 UTC",
        run_within=timedelta(days=8),
    ),
    SourceConfig(
        source="rhkyc",
        label="RHKYC (HK)",
        cadence_human="weekly Wed 10:00 UTC",
        run_within=timedelta(days=8),
    ),
    SourceConfig(
        source="cowesweek",
        label="Cowes Week (annual)",
        cadence_human="manual (annual, August)",
        run_within=timedelta(days=370),
        optional=True,
    ),
    SourceConfig(
        source="sydneyhobart",
        label="Sydney–Hobart (annual)",
        cadence_human="manual (annual, December)",
        run_within=timedelta(days=370),
        optional=True,
    ),
    SourceConfig(
        source="rorc",
        label="RORC (legacy, 2007–2022)",
        cadence_human="decommissioned",
        run_within=timedelta(days=3650),
        optional=True,
    ),
]


def by_source(source: str) -> SourceConfig | None:
    for s in SOURCES:
        if s.source == source:
            return s
    return None
