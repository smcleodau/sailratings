"""Shared config + helpers for scraper supervision.

Both the /justin/scrapers dashboard endpoint and the scrape-watchdog CLI
read these — single source of truth for what's expected to run, how often,
and the budget after which the data is considered stale.

Budgets are intentionally generous compared to the cron cadence so a single
missed run doesn't page anyone. Tune them as cron evolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


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
        run_within=timedelta(hours=2),
        # AU autumn shoulder ~3 weeks between summer series end and winter
        # series start, so a 21-day budget covers the natural lull. Beyond
        # that, something is genuinely off — flag it.
        data_within=timedelta(days=21),
    ),
    SourceConfig(
        source="orc_api",
        label="ORC certificates",
        cadence_human="daily 03:00 UTC",
        run_within=timedelta(hours=30),
        # No data_within: orc_api writes to orc_certificates, not race_results,
        # so the current per-source data-tap query doesn't apply. Run-health
        # alone is sufficient signal here.
    ),
    SourceConfig(
        source="topyacht",
        label="TopYacht (AU/regattas)",
        cadence_human="daily 02:30 UTC",
        run_within=timedelta(hours=30),
        data_within=timedelta(days=21),
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
