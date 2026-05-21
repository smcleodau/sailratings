"""Diagnostic queries for operator-facing CLI reports.

Read-only helpers that summarise the state of the ingestion pipeline:
which ORC certs failed to link to an IRC boat, how much detail-coverage
the backfill has achieved, etc. Intended for ad-hoc invocation via the
``irc-data report …`` CLI verbs.
"""
