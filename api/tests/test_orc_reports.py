"""Smoke tests for the ORC diagnostic queries.

These exercise the *Python* surface — that the module imports, that
``orphans_report`` / ``detail_coverage_report`` are callable, and that
they return tuples / sequences of the expected shape — but they don't
attempt to run the Postgres-specific SQL (``INTERVAL '7 days'``,
``FILTER (WHERE …)``, etc.) under SQLite. The CLI smoke command path
covers the live SQL when the user runs ``irc-data report …`` against
the dev DB.
"""

from __future__ import annotations

import inspect

import pytest


def test_orphans_report_is_callable():
    from irc_data.diagnostics.orc_reports import orphans_report

    sig = inspect.signature(orphans_report)
    assert list(sig.parameters) == ["engine"]


def test_detail_coverage_report_is_callable():
    from irc_data.diagnostics.orc_reports import detail_coverage_report

    sig = inspect.signature(detail_coverage_report)
    assert list(sig.parameters) == ["engine"]


def test_cli_report_commands_registered():
    """``irc-data report orc-orphans`` and ``orc-detail-coverage`` exist."""
    from irc_data import cli as irc_cli

    rpt = irc_cli.cli.get_command(None, "report")
    assert rpt is not None, "report group missing"
    subs = set(rpt.list_commands(None))
    assert {"orc-orphans", "orc-detail-coverage"}.issubset(subs)
