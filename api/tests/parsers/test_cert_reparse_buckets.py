"""OPS-02-13 — regression tests for the extend.ai cert-parsing audit buckets.

One real sample certificate per bucket, pinned to the values evidenced in
the audit tables (``cert_sym_asym_reclassify``, ``cert_reparse_disp_draft``,
``cert_reparse_lwp_dlr``) and the parser contract that closes each bucket:

  Bucket A — spinnaker sym/asym misclassification
      The parser classifies the single printed spinnaker block by geometry:
      SLU > SLE ⇒ asymmetric (``asym_*`` fields), SLU == SLE ⇒ symmetric
      (``sym_*`` fields).
  Bucket B — SER / nodisp certs
      Displacement / draft labels in FR/IT/ES parse correctly.
  Bucket C — LWP / DLR reparse
      LWP and DLR parse (integer or decimal) from historical PDFs.
  Bucket D — FL fields
      Certs never print a literal ``FL`` label; ``fl`` is populated from
      the printed ``HLP`` (headsail luff perpendicular).

The PDF fixtures are the real certificates in ``data/raw/certificates``;
tests that need them skip cleanly when the data directory is absent (e.g. a
source-only checkout).  The DB consistency test skips when no database is
reachable.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from irc_data.parsers.certificate_pdf import (
    classify_spinnaker,
    parse_certificate_pdf,
)

CERT_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "certificates"
)


def _cert(name: str) -> Path:
    path = CERT_DIR / name
    if not path.exists():
        pytest.skip(f"certificate PDF not present: {path}")
    return path


# ---------------------------------------------------------------------------
# Unit-level discriminant behaviour
# ---------------------------------------------------------------------------


class TestClassifySpinnaker:
    def test_symmetric_when_luffs_equal(self):
        kind, vals = classify_spinnaker(
            Decimal("13.45"), Decimal("13.45"), Decimal("7.00"), Decimal("6.82")
        )
        assert kind == "sym"
        assert vals == {
            "slu": Decimal("13.45"),
            "sle": Decimal("13.45"),
            "sf": Decimal("6.82"),
            "shw": Decimal("7.00"),
        }

    def test_asymmetric_when_luff_longer_than_leech(self):
        kind, vals = classify_spinnaker(
            Decimal("33.50"), Decimal("28.00"), Decimal("17.70"), Decimal("17.19")
        )
        assert kind == "asym"
        assert vals["slu"] == Decimal("33.50")
        assert vals["sle"] == Decimal("28.00")

    def test_no_spinnaker_block(self):
        kind, vals = classify_spinnaker(None, None, None, None)
        assert kind is None
        assert all(v is None for v in vals.values())

    def test_partial_block_defaults_to_sym(self):
        """A partially-printed block keeps the legacy sym default rather
        than dropping data."""
        kind, vals = classify_spinnaker(Decimal("12.00"), None, None, None)
        assert kind == "sym"
        assert vals["slu"] == Decimal("12.00")


# ---------------------------------------------------------------------------
# Synthetic-text parser behaviour (no PDF fixtures needed)
# ---------------------------------------------------------------------------


def _parse_text(monkeypatch: pytest.MonkeyPatch, text: str):
    """Run parse_certificate_pdf with pdfplumber stubbed to return `text`."""
    import irc_data.parsers.certificate_pdf as cp

    class _Page:
        def __init__(self, t):
            self._t = t

        def extract_words(self, **_kw):
            words = []
            for y, line in enumerate(self._t.splitlines()):
                for i, tok in enumerate(line.split()):
                    words.append(
                        {"text": tok, "top": float(y * 10), "x0": float(i * 40)}
                    )
            return words

    class _Pdf:
        def __init__(self, t):
            self.pages = [_Page(t)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cp.pdfplumber, "open", lambda _p: _Pdf(text))
    return cp.parse_certificate_pdf(Path("synthetic.pdf"))


_SYN_BASE = """IRC Boat Data
Name: TEST
HULL RIG & SAIL NOTES
LH 10.00
{spin_lines}
"""


class TestParserSpinnakerRouting:
    def test_sym_block_lands_in_sym_fields(self, monkeypatch):
        data = _parse_text(
            monkeypatch, _SYN_BASE.format(spin_lines="SLU 13.45, SLE 13.45\nSHW 7.00, SFL 6.82")
        )
        assert data.sym_slu == Decimal("13.45")
        assert data.sym_sle == Decimal("13.45")
        assert data.sym_sf == Decimal("6.82")
        assert data.sym_shw == Decimal("7.00")
        assert data.asym_slu is None
        assert data.asym_sle is None
        assert data.raw_data["spinnaker_kind"] == "sym"

    def test_asym_block_lands_in_asym_fields(self, monkeypatch):
        data = _parse_text(
            monkeypatch, _SYN_BASE.format(spin_lines="SLU 33.50, SLE 28.00\nSHW 17.70, SFL 17.19")
        )
        assert data.asym_slu == Decimal("33.50")
        assert data.asym_sle == Decimal("28.00")
        assert data.asym_sf == Decimal("17.19")
        assert data.asym_shw == Decimal("17.70")
        assert data.sym_slu is None
        assert data.sym_sle is None
        assert data.raw_data["spinnaker_kind"] == "asym"

    def test_no_spinnaker_block(self, monkeypatch):
        data = _parse_text(monkeypatch, _SYN_BASE.format(spin_lines="Draft: 2.08 P 13.35"))
        assert data.sym_slu is None
        assert data.asym_slu is None
        assert data.raw_data["spinnaker_kind"] is None


class TestParserFlLwpDlr:
    def test_fl_populated_from_hlp(self, monkeypatch):
        data = _parse_text(
            monkeypatch, _SYN_BASE.format(spin_lines="HLP 5.25\nJ 4.79")
        )
        assert data.hlp == Decimal("5.25")
        assert data.fl == Decimal("5.25")

    def test_integer_lwp_and_decimal_dlr(self, monkeypatch):
        data = _parse_text(
            monkeypatch, _SYN_BASE.format(spin_lines="LWP 10\nDLR 150.5\nDraft: 2.08 P 13.35")
        )
        assert data.lwp == Decimal("10")
        assert data.dlr == 150

    def test_french_displacement_and_draft(self, monkeypatch):
        data = _parse_text(
            monkeypatch,
            _SYN_BASE.format(spin_lines="Poids: 6005\nTirant d'eau : 2.08"),
        )
        assert data.displacement == Decimal("6005")
        assert data.draft == Decimal("2.08")


# ---------------------------------------------------------------------------
# Bucket A — spinnaker sym/asym regression (real certs)
# ---------------------------------------------------------------------------


class TestBucketASpinnakerRegression:
    """Sample certs from the extend.ai gap-audit bucket A, cross-checked
    against the ``cert_sym_asym_reclassify`` audit rows."""

    def test_asym_historical_cert_jelik_10530(self):
        """JELIK (HKG600): audit row old_sym_slu=33.50 → asym."""
        data = parse_certificate_pdf(_cert("10530_JELIK_HKG600.pdf"))
        assert data.sym_slu is None
        assert data.sym_sle is None
        assert data.asym_slu == Decimal("33.50")
        assert data.asym_sle == Decimal("28.00")
        assert data.asym_sf == Decimal("17.19")
        assert data.asym_shw == Decimal("17.70")
        assert data.raw_data["spinnaker_kind"] == "asym"

    def test_asym_cert_centennial_v_13634(self):
        """CENTENNIAL V (PHI2018): extend.ai labelled this cert asymmetric."""
        data = parse_certificate_pdf(_cert("13634_CENTENNIAL V_PHI2018.pdf"))
        assert data.sym_slu is None
        assert data.asym_slu == Decimal("31.98")
        assert data.asym_sle == Decimal("28.55")
        assert data.raw_data["spinnaker_kind"] == "asym"

    def test_sym_cert_white_lotus_14683(self):
        """WHITE LOTUS (IRL1333): equal luffs — genuinely symmetric."""
        data = parse_certificate_pdf(_cert("14683_WHITE LOTUS_IRL1333.pdf"))
        assert data.asym_slu is None
        assert data.sym_slu == Decimal("13.45")
        assert data.sym_sle == Decimal("13.45")
        assert data.raw_data["spinnaker_kind"] == "sym"


# ---------------------------------------------------------------------------
# Bucket B — SER / nodisp regression (real certs)
# ---------------------------------------------------------------------------


class TestBucketBNodispRegression:
    def test_spanish_cert_puma_10088(self):
        """PUMA (GBR7383R): first row of cert_reparse_disp_draft
        (new_displacement=6108.0, new_draft=2.30)."""
        data = parse_certificate_pdf(_cert("10088_PUMA_GBR7383R.pdf"))
        assert data.displacement == Decimal("6108")
        assert data.draft == Decimal("2.30")

    def test_spanish_ser_cert_tonic3_50486(self):
        """TONIC 3 (COL4100): Spanish 'SER' sample from gap-audit bucket B —
        'Peso'/'Calado' labels must parse."""
        data = parse_certificate_pdf(_cert("50486_TONIC 3_COL4100.pdf"))
        assert data.displacement == Decimal("6005")
        assert data.draft == Decimal("2.08")
        # SER-style standard cert: no spinnaker block.
        assert data.sym_slu is None
        assert data.asym_slu is None


# ---------------------------------------------------------------------------
# Bucket C — LWP / DLR regression (real certs)
# ---------------------------------------------------------------------------


class TestBucketCLwpDlrRegression:
    def test_xrated_10717(self):
        """X RATED (IRL7066): first row of cert_reparse_lwp_dlr
        (new_lwp=8.82, new_dlr=205)."""
        data = parse_certificate_pdf(_cert("10717_X RATED_IRL7066.pdf"))
        assert data.lwp == Decimal("8.82")
        assert data.dlr == 205

    def test_jelik_10530_lwp_dlr(self):
        """JELIK: cert_reparse_lwp_dlr row new_lwp=19.25, new_dlr=59."""
        data = parse_certificate_pdf(_cert("10530_JELIK_HKG600.pdf"))
        assert data.lwp == Decimal("19.25")
        assert data.dlr == 59


# ---------------------------------------------------------------------------
# Bucket D — FL regression (real certs)
# ---------------------------------------------------------------------------


class TestBucketDFlRegression:
    def test_fl_from_hlp_trex_50249(self):
        """TREX (075): gap-audit bucket C sample.  The cert prints HLP 3.69
        and no literal FL — the parser must surface fl = 3.69."""
        data = parse_certificate_pdf(_cert("50249_TREX_075.pdf"))
        assert data.hlp == Decimal("3.69")
        assert data.fl == Decimal("3.69")

    def test_fl_from_hlp_tonic3_50486(self):
        data = parse_certificate_pdf(_cert("50486_TONIC 3_COL4100.pdf"))
        assert data.hlp == Decimal("5.25")
        assert data.fl == Decimal("5.25")


# ---------------------------------------------------------------------------
# DB consistency: parser discriminant vs the cert_sym_asym_reclassify audit
# ---------------------------------------------------------------------------


def _db_url() -> str:
    import os

    return os.environ.get(
        "IRC_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL", "postgresql://irc:irc@localhost:5433/irc_data"
        ),
    ).replace("+psycopg", "")


def test_discriminant_matches_audit_table_snapshot():
    """Every ``cert_sym_asym_reclassify`` row must satisfy the parser's
    discriminant on its snapshotted old values (old SLU > old SLE ⇒ the row
    was an asymmetric kite misclassified as symmetric).

    Baseline at OPS-02-13 close-out: 2,011 rows = 1,631 (2026-05-15 wave)
    + 380 residual rows the verify/apply runner reclassified from the same
    discriminant.  The count is allowed to grow (a re-parse can always
    uncover another misclassified cert) but must never shrink, and every
    row must agree with the discriminant.
    """
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(_db_url(), connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    with conn:
        cur = conn.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE old_sym_slu > old_sym_sle) "
            "FROM cert_sym_asym_reclassify"
        )
        total, agree = cur.fetchone()
        cur = conn.execute(
            "SELECT COUNT(*) FROM irc_certificates "
            "WHERE sym_slu IS NOT NULL AND asym_slu IS NOT NULL"
        )
        (both_sides,) = cur.fetchone()
    conn.close()

    assert total >= 1631, f"audit table lost rows: {total} < 1631"
    assert agree == total, (
        f"{total - agree} audit rows violate the SLU>SLE discriminant — "
        "the parser fix would contradict the extend.ai audit"
    )
    # A cert carries its spinnaker on at most one side.
    assert both_sides == 0, (
        f"{both_sides} certs have both sym_* and asym_* populated — "
        "classification must be exclusive"
    )
