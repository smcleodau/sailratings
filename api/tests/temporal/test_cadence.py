"""Unit tests for the OPS-01-02 cadence / jitter / concurrency helpers.

These cover the parts of the schedule registry that don't need a live
Temporal server: cadence parsing, per-domain concurrency caps, stable id
generation, and the jitter bound used by ``SourceRunWorkflow``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from irc_data.temporal.schedules import cadence as C
from irc_data.temporal.ledger.workflows import MAX_JITTER_SECONDS


class TestCadenceParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("nightly", timedelta(hours=24)),
            ("daily", timedelta(hours=24)),
            ("hourly", timedelta(hours=1)),
            ("weekly", timedelta(days=7)),
            ("monthly", timedelta(days=30)),
            ("30min", timedelta(minutes=30)),
            ("6h", timedelta(hours=6)),
            ("2d", timedelta(days=2)),
            ("15m", timedelta(minutes=15)),
            ("  45  mins ", timedelta(minutes=45)),
        ],
    )
    def test_named_and_compact_cadences(self, text, expected):
        assert C.cadence_to_timedelta(text) == expected

    @pytest.mark.parametrize("text", [None, "", "junk", "whenever", "nightly-ish"])
    def test_unknown_cadence_falls_back_to_nightly(self, text):
        assert C.cadence_to_timedelta(text) == timedelta(hours=24)


class TestJitterBound:
    def test_jitter_cap_is_bounded_for_nightly(self):
        """The per-run jitter must never stall a scheduled run for hours."""
        interval = C.cadence_to_timedelta("nightly").total_seconds()
        cap = min(MAX_JITTER_SECONDS, max(1.0, interval * C.MAX_JITTER_FRACTION))
        assert cap <= MAX_JITTER_SECONDS
        # A nightly run jitters at most MAX_JITTER_SECONDS, not ~45 minutes.
        assert cap < 3600

    def test_jitter_never_exceeds_fraction_of_short_cadence(self):
        """For a short cadence the fractional cap is the smaller one."""
        interval = C.cadence_to_timedelta("30min").total_seconds()  # 1800 s
        cap = min(MAX_JITTER_SECONDS, max(1.0, interval * C.MAX_JITTER_FRACTION))
        assert cap == pytest.approx(interval * C.MAX_JITTER_FRACTION)

    def test_max_jitter_fraction_is_sane(self):
        assert 0 < C.MAX_JITTER_FRACTION <= 0.10


class TestConcurrencyCaps:
    def test_known_sensitive_domains_are_capped_low(self):
        assert C.max_concurrency_for_domain("app.sailsys.com.au") == 2
        assert C.max_concurrency_for_domain("www.topyacht.net.au") == 2
        assert C.max_concurrency_for_domain("ircrating.org") == 3

    def test_unknown_domain_uses_default(self):
        assert C.max_concurrency_for_domain("unknown.example.com") == C.DEFAULT_DOMAIN_CONCURRENCY

    def test_empty_domain_uses_default(self):
        assert C.max_concurrency_for_domain("") == C.DEFAULT_DOMAIN_CONCURRENCY

    def test_domain_extracted_from_url_case_insensitively(self):
        assert C.domain_for_url("https://APP.SailSys.com.au/x") == "app.sailsys.com.au"
        assert C.domain_for_url("not a url") == ""
        assert C.domain_for_url(None) == ""

    def test_cap_lookup_matches_url_extraction(self):
        url = "https://app.sailsys.com.au/dashboard"
        assert C.max_concurrency_for_domain(C.domain_for_url(url)) == 2


class TestStableIds:
    def test_schedule_id_format(self):
        assert C.schedule_id_for_slug("sailsys") == "source-sailsys"

    def test_workflow_id_deterministic_and_sanitised(self):
        a = C.workflow_id_for_run("sailsys", "scheduled:source-sailsys:2026-09-02T00:00")
        b = C.workflow_id_for_run("sailsys", "scheduled:source-sailsys:2026-09-02T00:00")
        assert a == b
        assert a.startswith("source-run-sailsys-")
        assert " " not in a and ":" not in a.split("source-run-sailsys-", 1)[1]

    def test_workflow_id_differs_per_run_key(self):
        a = C.workflow_id_for_run("sailsys", "run-a")
        b = C.workflow_id_for_run("sailsys", "run-b")
        assert a != b
