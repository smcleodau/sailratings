#!/usr/bin/env python3
"""End-to-end verification for DP-01-05 — source change & breakage detection.

Drives the monitor against *mutated fixture pages* and prints hard evidence
that:

  1. Material deviations (table removed, record collapse, content-type swap,
     fetch failure) quarantine publication, open a source incident with
     representative artifacts, and fire a health-check webhook alert.
  2. Harmless content changes (ad-banner / footer copy swap) do NOT alert
     and do NOT quarantine.

Runs entirely against in-memory SQLite with an in-memory alert transport —
no network calls.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_01_05.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine

# Ensure the package is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.diagnostics.source_monitor import (  # noqa: E402
    check_source,
    fingerprint_source,
    init_monitor_tables,
    is_source_quarantined,
    list_incidents,
    set_baseline,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "source_monitor"
SOURCE_ID = "example-yacht-club"
URL = "https://example-yacht-club.test/results/race-3"


class AlertCapture:
    """In-memory webhook transport that records every alert."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, payload: dict) -> bool:
        self.calls.append((url, payload))
        return True


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_monitor_tables(engine)
    alerts = AlertCapture()

    baseline_html = (FIXTURES / "baseline_results.html").read_text()
    harmless_html = (FIXTURES / "harmless_change.html").read_text()
    table_gone_html = (FIXTURES / "mutated_table_removed.html").read_text()
    collapse_html = (FIXTURES / "mutated_record_collapse.html").read_text()

    # Establish the known-good baseline.
    fp = fingerprint_source(content=baseline_html, content_type="text/html")
    set_baseline(engine, SOURCE_ID, URL, fp)
    _banner("BASELINE ESTABLISHED")
    print(f"  source={SOURCE_ID}  records={fp.record_count}  "
          f"hash={fp.content_hash[:12]}…  struct={fp.structure_signature[:12]}…")

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures.append(label)

    # --- Scenario 1: harmless content change -----------------------------
    _banner("SCENARIO 1: harmless content change (ad banner + footer copy)")
    ev = check_source(
        engine, SOURCE_ID, URL, content=harmless_html, content_type="text/html",
        baseline_content=baseline_html,
        alert_webhook_url="https://hooks.slack.test/services/T/B/X",
        alert_transport=alerts,
    )
    print(f"  status={ev.status}  material={ev.material}  "
          f"deviations={ev.deviations}  diff_ratio={ev.diff_ratio:.4f}")
    expect(ev.status == "changed", "status is 'changed' (content differs)")
    expect(ev.material is False, "NOT material")
    expect(ev.quarantined is False, "NOT quarantined")
    expect(len(alerts.calls) == 0, "NO webhook alert fired")
    expect(not is_source_quarantined(engine, SOURCE_ID), "publication not quarantined")

    # --- Scenario 2: structure mutation (table removed) ------------------
    _banner("SCENARIO 2: MUTATION — results table removed")
    n_alerts_before = len(alerts.calls)
    ev = check_source(
        engine, SOURCE_ID, URL, content=table_gone_html, content_type="text/html",
        alert_webhook_url="https://hooks.slack.test/services/T/B/X",
        alert_transport=alerts,
    )
    print(f"  status={ev.status}  material={ev.material}  deviations={ev.deviations}")
    expect(ev.status == "material_deviation", "status is 'material_deviation'")
    expect(ev.material is True, "material")
    expect("structure_signature" in ev.deviations, "structure_signature deviation")
    expect("record_count" in ev.deviations, "record_count deviation")
    expect(ev.quarantined is True, "publication QUARANTINED")
    expect(is_source_quarantined(engine, SOURCE_ID), "quarantine flag set")
    expect(ev.incident_id is not None, "incident created")
    expect(len(alerts.calls) == n_alerts_before + 1, "webhook ALERT fired")

    incidents = list_incidents(engine, SOURCE_ID)
    expect(len(incidents) == 1, "exactly one open incident")
    inc = incidents[0]
    expect(inc["incident_type"] == "structure_change", "incident type = structure_change")
    expect(bool(inc.get("content_excerpt")), "incident carries content_excerpt artifact")

    # --- Scenario 3: rebaseline then record-count collapse ---------------
    _banner("SCENARIO 3: MUTATION — record-count collapse (5 → 1)")
    # Reset baseline to healthy page and release quarantine for a clean run.
    set_baseline(engine, SOURCE_ID, URL, fingerprint_source(content=baseline_html))
    from irc_data.diagnostics.source_monitor import release_quarantine
    release_quarantine(engine, SOURCE_ID)
    n_alerts_before = len(alerts.calls)

    ev = check_source(
        engine, SOURCE_ID, URL, content=collapse_html, content_type="text/html",
        alert_webhook_url="https://hooks.slack.test/services/T/B/X",
        alert_transport=alerts,
    )
    print(f"  status={ev.status}  material={ev.material}  deviations={ev.deviations}  "
          f"samples={len(ev.sample_records or [])}")
    expect(ev.material is True, "record collapse is material")
    expect("record_count" in ev.deviations, "record_count deviation present")
    expect(ev.sample_records and len(ev.sample_records) >= 1,
           "representative sample_records captured")
    expect(len(alerts.calls) == n_alerts_before + 1, "webhook ALERT fired")

    # --- Scenario 4: content-type swap -----------------------------------
    _banner("SCENARIO 4: MUTATION — content-type swap (text/html → json)")
    set_baseline(engine, SOURCE_ID, URL, fingerprint_source(content=baseline_html))
    release_quarantine(engine, SOURCE_ID)
    n_alerts_before = len(alerts.calls)
    ev = check_source(
        engine, SOURCE_ID, URL, content='{"error":"moved"}',
        content_type="application/json",
        alert_webhook_url="https://hooks.slack.test/services/T/B/X",
        alert_transport=alerts,
    )
    expect(ev.material is True, "content-type swap is material")
    expect("content_type" in ev.deviations, "content_type deviation present")
    expect(len(alerts.calls) == n_alerts_before + 1, "webhook ALERT fired")

    # --- Scenario 5: fetch failure ----------------------------------------
    _banner("SCENARIO 5: MUTATION — fetch failure (HTTP 500)")
    set_baseline(engine, SOURCE_ID, URL, fingerprint_source(content=baseline_html))
    release_quarantine(engine, SOURCE_ID)
    n_alerts_before = len(alerts.calls)
    ev = check_source(
        engine, SOURCE_ID, URL, content=None, fetch_success=False, http_status=500,
        alert_webhook_url="https://hooks.slack.test/services/T/B/X",
        alert_transport=alerts,
    )
    expect(ev.material is True, "fetch failure is material")
    expect("fetch_error" in ev.deviations, "fetch_error deviation present")
    expect(len(alerts.calls) == n_alerts_before + 1, "webhook ALERT fired")

    # --- Summary ----------------------------------------------------------
    _banner("SUMMARY")
    print(f"  total webhook alerts fired: {len(alerts.calls)} (expected 4)")
    print(f"  quarantine active: {is_source_quarantined(engine, SOURCE_ID)}")
    print(f"  open incidents: {len([i for i in list_incidents(engine, SOURCE_ID) if i['status']=='open'])}")

    if failures:
        print(f"\n  RESULT: FAIL — {len(failures)} expectation(s) failed")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("\n  RESULT: PASS — mutated fixtures alerted; harmless change silent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
