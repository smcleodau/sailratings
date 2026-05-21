"""Tests for the master cert-number index built from harvested TCC listings."""

from __future__ import annotations

from pathlib import Path

from irc_data.scrapers.cert_index import build_index_from_tcc_dir


def _write_tcc(path: Path, rows: list[dict]) -> None:
    """Write a TCC-listing CSV with the real ircrating.org column headers."""
    headers = ["Boat Name", "Sail No", "Cert No", "Issue Date", "TCC"]
    lines = [",".join(headers)]
    for r in rows:
        lines.append(
            ",".join(
                [
                    r.get("Boat Name", ""),
                    r.get("Sail No", ""),
                    r.get("Cert No", ""),
                    r.get("Issue Date", ""),
                    r.get("TCC", ""),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_index_contains_pre_2024_certs(tmp_path):
    """Index should:
       - deduplicate cert numbers across snapshot CSVs
       - keep the newest-year record when the same cert appears in multiple
         years (so we have the most recent boat name / sail number)
       - tag each entry with the snapshot year it was last seen in
    """
    _write_tcc(
        tmp_path / "tcc_2012_20120115101010.csv",
        [
            {"Boat Name": "ALPHA", "Sail No": "GBR1", "Cert No": "1001"},
            {"Boat Name": "BETA", "Sail No": "GBR2", "Cert No": "1002"},
        ],
    )
    _write_tcc(
        tmp_path / "tcc_2018_20180601000000.csv",
        [
            # ALPHA re-rated in 2018 — should win over 2012 record.
            {"Boat Name": "ALPHA", "Sail No": "GBR1", "Cert No": "1001"},
            {"Boat Name": "GAMMA", "Sail No": "GBR3", "Cert No": "1003"},
        ],
    )
    _write_tcc(
        tmp_path / "tcc_2023_20231201000000.csv",
        [{"Boat Name": "DELTA", "Sail No": "GBR4", "Cert No": "1004"}],
    )

    idx = build_index_from_tcc_dir(tmp_path)

    assert isinstance(idx, list)
    assert len(idx) == 4, f"expected 4 unique certs, got {len(idx)}"

    by_cert = {e["cert_number"]: e for e in idx}
    assert set(by_cert) == {"1001", "1002", "1003", "1004"}

    # ALPHA appears in both 2012 and 2018 — the index should keep the
    # latest seen year.
    assert by_cert["1001"]["year"] == 2018
    assert by_cert["1002"]["year"] == 2012
    assert by_cert["1003"]["year"] == 2018
    assert by_cert["1004"]["year"] == 2023

    # All entries carry boat_name / sail_number.
    for entry in idx:
        assert entry["boat_name"]
        assert entry["sail_number"]

    # At least one entry pre-dates 2024.
    assert any(e["year"] < 2024 for e in idx)
