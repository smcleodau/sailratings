"""Tests for the source change & breakage monitor (DP-01-05).

These tests use an in-memory SQLite engine with a hand-rolled schema
mirror of the four source-monitor tables so they don't depend on
Postgres or Alembic state.

The verification criterion from the issue:
    "Mutated fixture pages trigger expected alerts without alerting
     on harmless content changes."
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from irc_data.diagnostics.source_monitor import (
    SCHEMA_VERSION,
    STATUS_CHANGED,
    STATUS_CLEAN,
    STATUS_MATERIAL,
    SourceFingerprint,
    SourceHealthEventV1,
    check_source,
    fingerprint_source,
    get_baseline,
    get_recent_health_events,
    init_monitor_tables,
    is_source_quarantined,
    list_baselines,
    list_incidents,
    release_quarantine,
    rebaseline_source,
    set_baseline,
    compute_structure_signature,
    compare_and_classify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A canonical results page with a table of 5 boats.
BASELINE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Race Results</title></head>
<body>
  <div class="ad-banner">Sponsored by ACME Sails</div>
  <h1>Saturday Series — Race 3</h1>
  <table>
    <tr><th>Place</th><th>Boat</th><th>Sail</th><th>Corrected</th></tr>
    <tr><td>1</td><td>Wild Thing</td><td>AUS1234</td><td>01:23:45</td></tr>
    <tr><td>2</td><td>Speed Demon</td><td>AUS5678</td><td>01:24:02</td></tr>
    <tr><td>3</td><td>Sea Breeze</td><td>AUS9012</td><td>01:25:30</td></tr>
    <tr><td>4</td><td>Wind Dancer</td><td>AUS3456</td><td>01:26:10</td></tr>
    <tr><td>5</td><td>Slow Poke</td><td>AUS7890</td><td>01:28:00</td></tr>
  </table>
  <footer>© 2026 Example Yacht Club</footer>
</body>
</html>
"""

# Same structure + records but different ad copy and footer text.
# This is a *harmless content change* — must NOT be material.
HARMLESS_CHANGED_HTML = BASELINE_HTML.replace(
    "Sponsored by ACME Sails", "Sponsored by North Sails"
).replace(
    "© 2026 Example Yacht Club", "© 2026 Example Yacht Club — updated"
)

# Table removed entirely — material structure change.
TABLE_REMOVED_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Race Results</title></head>
<body>
  <div class="ad-banner">Sponsored by ACME Sails</div>
  <h1>Saturday Series — Race 3</h1>
  <p>Results are currently unavailable. Please check back later.</p>
  <footer>© 2026 Example Yacht Club</footer>
</body>
</html>
"""

# Same table structure but only 1 record (collapse from 5 → 1).
RECORD_COLLAPSE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Race Results</title></head>
<body>
  <div class="ad-banner">Sponsored by ACME Sails</div>
  <h1>Saturday Series — Race 3</h1>
  <table>
    <tr><th>Place</th><th>Boat</th><th>Sail</th><th>Corrected</th></tr>
    <tr><td>1</td><td>Wild Thing</td><td>AUS1234</td><td>01:23:45</td></tr>
  </table>
  <footer>© 2026 Example Yacht Club</footer>
</body>
</html>
"""

# Content type changed from text/html to application/json.
JSON_CONTENT = '{"error": "page moved"}'

SOURCE_ID = "example-source"
SOURCE_URL = "https://example-source.test/results"


@pytest.fixture()
def engine():
    """Fresh in-memory SQLite engine with source-monitor tables."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_monitor_tables(eng)
    return eng


@pytest.fixture()
def baselined_engine(engine):
    """Engine with a baseline already set from BASELINE_HTML."""
    fp = fingerprint_source(content=BASELINE_HTML, content_type="text/html")
    set_baseline(engine, SOURCE_ID, SOURCE_URL, fp)
    return engine


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_event_is_v1_contract():
    """SourceHealthEventV1 has the expected schema_version and fields."""
    event = SourceHealthEventV1(source_id="test", url="http://x")
    assert event.schema_version == SCHEMA_VERSION
    assert event.schema_version == "v1"
    d = event.to_dict()
    required_keys = {
        "schema_version", "source_id", "url", "checked_at", "status",
        "material", "deviations", "baseline", "current", "diff_ratio",
        "sample_records", "content_excerpt", "incident_id", "quarantined",
    }
    assert required_keys.issubset(d.keys())


def test_fingerprint_produces_consistent_hash():
    fp1 = fingerprint_source(content=BASELINE_HTML)
    fp2 = fingerprint_source(content=BASELINE_HTML)
    assert fp1.content_hash == fp2.content_hash
    assert fp1.structure_signature == fp2.structure_signature
    assert fp1.record_count == 5


def test_fingerprint_handles_failed_fetch():
    fp = fingerprint_source(content=None, fetch_success=False)
    assert fp.fetch_success is False
    assert fp.content_hash == ""
    assert fp.record_count == 0


def test_fingerprint_handles_bytes_content():
    fp = fingerprint_source(content=BASELINE_HTML.encode("utf-8"))
    assert fp.content_hash != ""
    assert fp.record_count == 5


# ---------------------------------------------------------------------------
# Harmless change — NOT material
# ---------------------------------------------------------------------------


def test_harmless_content_change_is_not_material(baselined_engine):
    """Ad banner / copy change with identical structure+records → not material."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=HARMLESS_CHANGED_HTML,
        content_type="text/html",
        baseline_content=BASELINE_HTML,
    )
    assert event.status == STATUS_CHANGED
    assert event.material is False
    assert event.deviations == []
    assert event.quarantined is False
    assert event.incident_id is None


def test_clean_check_when_identical(baselined_engine):
    """Identical content → status=clean, no deviations."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
        content_type="text/html",
    )
    assert event.status == STATUS_CLEAN
    assert event.material is False
    assert event.deviations == []
    assert event.diff_ratio == 0.0
    assert event.quarantined is False


# ---------------------------------------------------------------------------
# Material deviation tests
# ---------------------------------------------------------------------------


def test_table_removal_is_material(baselined_engine):
    """Removing the table → structure_signature + record_count + parser_yield deviation."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert event.status == STATUS_MATERIAL
    assert event.material is True
    assert "structure_signature" in event.deviations
    assert "record_count" in event.deviations
    assert "parser_yield" in event.deviations


def test_record_count_collapse_is_material(baselined_engine):
    """Record count drops from 5 → 1 (≥50% collapse) → material."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=RECORD_COLLAPSE_HTML,
        content_type="text/html",
    )
    assert event.material is True
    assert "record_count" in event.deviations
    assert "parser_yield" in event.deviations


def test_fetch_error_is_material(baselined_engine):
    """Fetch failure → material (fetch_error deviation)."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=None,
        fetch_success=False,
        http_status=None,
    )
    assert event.material is True
    assert "fetch_error" in event.deviations


def test_http_error_status_is_material(baselined_engine):
    """HTTP 500 error → material (http_status deviation)."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=None,
        fetch_success=False,
        http_status=500,
    )
    assert event.material is True
    assert "fetch_error" in event.deviations


def test_content_type_change_is_material(baselined_engine):
    """Content-Type changes from text/html to application/json → material."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=JSON_CONTENT,
        content_type="application/json",
    )
    assert event.material is True
    assert "content_type" in event.deviations


def test_parser_yield_collapse_is_material(baselined_engine):
    """Parser yield drops ≥50% even though record_count is fine → material."""
    # Same HTML (5 records) but parser only extracts 1.
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
        content_type="text/html",
        parser_yield=1,  # baseline was 5, now 1 → collapse
    )
    assert event.material is True
    assert "parser_yield" in event.deviations


# ---------------------------------------------------------------------------
# Quarantine + incident artifacts
# ---------------------------------------------------------------------------


def test_table_removal_quarantines_and_creates_incident_with_artifacts(baselined_engine):
    """Material deviation quarantines publication and creates an incident
    with representative artifacts (deviations, content_excerpt, sample_records)."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert event.material is True
    assert event.quarantined is True
    assert event.incident_id is not None
    assert event.incident_id >= 1

    # Publication is quarantined.
    assert is_source_quarantined(baselined_engine, SOURCE_ID) is True

    # Incident carries deviations.
    incidents = list_incidents(baselined_engine, SOURCE_ID)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["status"] == "open"
    assert inc["incident_type"] == "structure_change"

    # Deviations are persisted.
    devs = inc["deviations"]
    if isinstance(devs, str):
        devs = json.loads(devs)
    assert "structure_signature" in devs
    assert "record_count" in devs

    # Content excerpt is present.
    assert inc["content_excerpt"] is not None
    assert len(inc["content_excerpt"]) > 0


def test_sample_records_captured_in_artifacts(baselined_engine):
    """When a material deviation occurs with data still present, sample
    records are captured in the incident artifacts."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=RECORD_COLLAPSE_HTML,
        content_type="text/html",
    )
    assert event.material is True
    assert event.sample_records is not None
    assert len(event.sample_records) >= 1
    # The first sample record should have boat data.
    assert "Boat" in event.sample_records[0] or "Place" in event.sample_records[0]


# ---------------------------------------------------------------------------
# Repeat failures attach to existing incident
# ---------------------------------------------------------------------------


def test_repeat_failures_attach_to_existing_incident(baselined_engine):
    """A second material check should NOT create a duplicate incident."""
    # First check — material.
    event1 = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert event1.incident_id is not None

    # Second check — still material (same broken page).
    event2 = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert event2.incident_id is not None
    # No duplicate incident — same incident ID.
    assert event2.incident_id == event1.incident_id

    incidents = list_incidents(baselined_engine, SOURCE_ID)
    assert len(incidents) == 1


# ---------------------------------------------------------------------------
# Release quarantine resolves incident
# ---------------------------------------------------------------------------


def test_release_quarantine_resolves_incident(baselined_engine):
    """Releasing the quarantine resolves the incident."""
    # Trigger material deviation.
    check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert is_source_quarantined(baselined_engine, SOURCE_ID) is True

    # Release.
    released = release_quarantine(baselined_engine, SOURCE_ID)
    assert released == 1
    assert is_source_quarantined(baselined_engine, SOURCE_ID) is False

    # Incident is resolved.
    incidents = list_incidents(baselined_engine, SOURCE_ID)
    assert len(incidents) == 1
    assert incidents[0]["status"] == "resolved"
    assert incidents[0]["resolved_at"] is not None


# ---------------------------------------------------------------------------
# Rebaseline after fix stops alerting
# ---------------------------------------------------------------------------


def test_rebaseline_after_fix_stops_alerting(baselined_engine):
    """After fixing the source and rebaselining, checks return to clean."""
    # Trigger material deviation.
    event1 = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert event1.material is True

    # Release the quarantine.
    release_quarantine(baselined_engine, SOURCE_ID)

    # Rebaseline with the fixed page.
    rebaseline_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
    )

    # Now a clean check should not alert.
    event2 = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
        content_type="text/html",
    )
    assert event2.status == STATUS_CLEAN
    assert event2.material is False
    assert event2.quarantined is False


# ---------------------------------------------------------------------------
# Other sources unaffected by quarantine
# ---------------------------------------------------------------------------


def test_other_sources_unaffected_by_quarantine(baselined_engine):
    """Quarantining one source does not affect another."""
    OTHER_ID = "other-source"
    OTHER_URL = "https://other.test/results"

    # Baseline the other source.
    fp = fingerprint_source(content=BASELINE_HTML)
    set_baseline(baselined_engine, OTHER_ID, OTHER_URL, fp)

    # Quarantine the first source.
    check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    assert is_source_quarantined(baselined_engine, SOURCE_ID) is True
    assert is_source_quarantined(baselined_engine, OTHER_ID) is False

    # The other source can still be checked cleanly.
    event = check_source(
        baselined_engine,
        OTHER_ID,
        OTHER_URL,
        content=BASELINE_HTML,
        content_type="text/html",
    )
    assert event.status == STATUS_CLEAN
    assert event.quarantined is False


# ---------------------------------------------------------------------------
# First-run auto-baseline
# ---------------------------------------------------------------------------


def test_first_run_auto_baselines(engine):
    """When no baseline exists, check_source auto-creates one and returns clean."""
    event = check_source(
        engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
        content_type="text/html",
    )
    assert event.status == STATUS_CLEAN
    assert event.material is False

    # Baseline was stored.
    bl = get_baseline(engine, SOURCE_ID, SOURCE_URL)
    assert bl is not None
    assert bl["record_count"] == 5
    assert bl["content_hash"] != ""


# ---------------------------------------------------------------------------
# Diff ratio computation
# ---------------------------------------------------------------------------


def test_diff_ratio_computed_on_change(baselined_engine):
    """diff_ratio is non-zero when content changes."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=HARMLESS_CHANGED_HTML,
        content_type="text/html",
        baseline_content=BASELINE_HTML,
    )
    assert event.diff_ratio > 0.0
    # Should be small since only the ad text changed.
    assert event.diff_ratio < 1.0


def test_diff_ratio_zero_on_identical(baselined_engine):
    """diff_ratio is 0.0 when content is identical."""
    event = check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
        content_type="text/html",
        baseline_content=BASELINE_HTML,
    )
    assert event.diff_ratio == 0.0


# ---------------------------------------------------------------------------
# Structure signature
# ---------------------------------------------------------------------------


def test_structure_signature_ignores_text_content():
    """Structure signature is the same when only text changes."""
    sig1 = compute_structure_signature(BASELINE_HTML)
    sig2 = compute_structure_signature(HARMLESS_CHANGED_HTML)
    assert sig1 == sig2


def test_structure_signature_detects_table_removal():
    """Structure signature changes when a table is removed."""
    sig1 = compute_structure_signature(BASELINE_HTML)
    sig2 = compute_structure_signature(TABLE_REMOVED_HTML)
    assert sig1 != sig2


# ---------------------------------------------------------------------------
# Health event persistence
# ---------------------------------------------------------------------------


def test_health_event_persisted(baselined_engine):
    """check_source persists a row in source_health_events."""
    check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=BASELINE_HTML,
        content_type="text/html",
    )
    events = get_recent_health_events(baselined_engine, SOURCE_ID)
    assert len(events) >= 1
    assert events[0]["source_id"] == SOURCE_ID
    assert events[0]["status"] == STATUS_CLEAN


def test_health_event_persisted_on_material(baselined_engine):
    """Material events persist with quarantined=True and incident_id set."""
    check_source(
        baselined_engine,
        SOURCE_ID,
        SOURCE_URL,
        content=TABLE_REMOVED_HTML,
        content_type="text/html",
    )
    events = get_recent_health_events(baselined_engine, SOURCE_ID)
    material_events = [e for e in events if e["material"] in (True, 1)]
    assert len(material_events) >= 1
    me = material_events[0]
    assert me["quarantined"] in (True, 1)
    assert me["incident_id"] is not None


# ---------------------------------------------------------------------------
# Baseline listing
# ---------------------------------------------------------------------------


def test_list_baselines(baselined_engine):
    """list_baselines returns all stored baselines."""
    baselines = list_baselines(baselined_engine)
    assert len(baselines) == 1
    assert baselines[0]["source_id"] == SOURCE_ID


# ---------------------------------------------------------------------------
# Compare & classify unit tests
# ---------------------------------------------------------------------------


def test_compare_clean_when_identical():
    """Identical fingerprints produce no deviations."""
    fp = fingerprint_source(content=BASELINE_HTML)
    devs, material, diff = compare_and_classify(fp, fp, BASELINE_HTML, BASELINE_HTML)
    assert devs == []
    assert material is False
    assert diff == 0.0


def test_compare_detects_content_type_change():
    """Content-type change is detected."""
    bl = fingerprint_source(content=BASELINE_HTML, content_type="text/html")
    cur = fingerprint_source(content=BASELINE_HTML, content_type="application/json")
    devs, material, _ = compare_and_classify(bl, cur)
    assert "content_type" in devs
    assert material is True


def test_compare_detects_record_collapse():
    """Record count collapse is detected."""
    bl = fingerprint_source(content=BASELINE_HTML)
    cur = fingerprint_source(content=RECORD_COLLAPSE_HTML)
    devs, material, _ = compare_and_classify(bl, cur)
    assert "record_count" in devs
    assert material is True


def test_compare_detects_structure_change():
    """Structure change is detected."""
    bl = fingerprint_source(content=BASELINE_HTML)
    cur = fingerprint_source(content=TABLE_REMOVED_HTML)
    devs, material, _ = compare_and_classify(bl, cur)
    assert "structure_signature" in devs
    assert material is True


def test_compare_harmless_change_not_material():
    """Content-only change (same structure) is not material."""
    bl = fingerprint_source(content=BASELINE_HTML)
    cur = fingerprint_source(content=HARMLESS_CHANGED_HTML)
    devs, material, diff = compare_and_classify(bl, cur, BASELINE_HTML, HARMLESS_CHANGED_HTML)
    assert devs == []
    assert material is False
    assert diff > 0.0


# ---------------------------------------------------------------------------
# Release all quarantines
# ---------------------------------------------------------------------------


def test_release_all_quarantines(baselined_engine):
    """Releasing without a source_id releases all active quarantines."""
    # Quarantine two sources.
    for sid, url in [(SOURCE_ID, SOURCE_URL), ("other", "https://o.test/r")]:
        fp = fingerprint_source(content=BASELINE_HTML)
        set_baseline(baselined_engine, sid, url, fp)
        check_source(
            baselined_engine, sid, url,
            content=TABLE_REMOVED_HTML, content_type="text/html",
        )

    assert is_source_quarantined(baselined_engine, SOURCE_ID) is True
    assert is_source_quarantined(baselined_engine, "other") is True

    released = release_quarantine(baselined_engine)
    assert released == 2
    assert is_source_quarantined(baselined_engine, SOURCE_ID) is False
    assert is_source_quarantined(baselined_engine, "other") is False
