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
    expected_within: timedelta  # data older than this triggers an alert
    optional: bool = False  # if True, never alert (annual events, manual sources)


# Order matters for the dashboard — most operationally critical first.
SOURCES: list[SourceConfig] = [
    SourceConfig(
        source="sailsys",
        label="SailSys (AU clubs)",
        cadence_human="every 30 min",
        expected_within=timedelta(hours=2),
    ),
    SourceConfig(
        source="orc_api",
        label="ORC certificates",
        cadence_human="daily 03:00 UTC",
        expected_within=timedelta(hours=30),
    ),
    SourceConfig(
        source="topyacht",
        label="TopYacht (AU/regattas)",
        cadence_human="daily 02:30 UTC",
        expected_within=timedelta(hours=30),
    ),
    SourceConfig(
        source="sailracehq",
        label="SailRaceHQ (UK offshore)",
        cadence_human="weekly Tue 10:00 UTC",
        expected_within=timedelta(days=8),
    ),
    SourceConfig(
        source="isora",
        label="ISORA (Irish Sea)",
        cadence_human="weekly Tue 11:00 UTC",
        expected_within=timedelta(days=8),
    ),
    SourceConfig(
        source="rhkyc",
        label="RHKYC (HK)",
        cadence_human="weekly Wed 10:00 UTC",
        expected_within=timedelta(days=8),
    ),
    SourceConfig(
        source="cowesweek",
        label="Cowes Week (annual)",
        cadence_human="manual (annual, August)",
        expected_within=timedelta(days=370),
        optional=True,
    ),
    SourceConfig(
        source="sydneyhobart",
        label="Sydney–Hobart (annual)",
        cadence_human="manual (annual, December)",
        expected_within=timedelta(days=370),
        optional=True,
    ),
    SourceConfig(
        source="rorc",
        label="RORC (legacy, 2007–2022)",
        cadence_human="decommissioned",
        expected_within=timedelta(days=3650),
        optional=True,
    ),
]


def by_source(source: str) -> SourceConfig | None:
    for s in SOURCES:
        if s.source == source:
            return s
    return None
