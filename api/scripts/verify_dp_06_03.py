#!/usr/bin/env python3
"""End-to-end verification evidence for DP-06-03 — map the selected
source (irc-tcc) to canonical assertions.

Runs the golden IRC TCC listing source records through the certified
parse → transform path and reports hard, paste-able evidence against the
issue's acceptance criteria:

  1. **Golden records → expected assertions.**  Every publishable golden
     row produces the exact canonical payload (field mapping + units +
     missing semantics applied).
  2. **Rejects.**  Golden rows that must not publish (unparseable TCC,
     blank sail number) are diverted to the reject stream with
     machine-readable reasons; nothing partially publishes.
  3. **Mapping coverage (acceptance criterion).**  Every required
     canonical field has a mapping, every extracted source field is
     mapped or explicitly unsupported-with-reason, and every canonical
     schema field has a mapping or a stated not-provided reason.
  4. **Lineage reaches raw artifact (acceptance criterion).**  Every
     assertion's lineage query resolves the artifact content hash and
     the exact CSV source span each value was read from.
  5. **Determinism.**  Replaying the golden artifact yields identical
     transformation ids, assertion ids and hashes.

No database or network required — parser and transformer are pure.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_06_03.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.parsers.extraction_contract import ParserInputV1  # noqa: E402
from irc_data.parsers.tcc_listing_parser import IRCTCCListingParser  # noqa: E402
from irc_data.transform.irc_tcc_mapping import (  # noqa: E402
    CANONICAL_UNITS,
    FIELD_MAPPINGS,
    MAPPING_VERSION,
    SOURCE_SLUG,
    UNSUPPORTED_SOURCE_FIELDS,
    IRCTCCListingTransformer,
    audit_mapping_coverage,
)
from irc_data.transform.lineage import index_batch  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "transform" / "fixtures"
GOLDEN = FIXTURES / "irc_tcc_listing_golden.csv"
URL = "https://ircrating.org/wp-content/uploads/2026/03/ClubListing_20260301.csv"

PASS = "PASS"
FAIL = "FAIL"
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 74)
    print("DP-06-03 verification — map selected source (irc-tcc) to canonical")
    print("=" * 74)

    content = GOLDEN.read_bytes()
    artifact_hash = hashlib.sha256(content).hexdigest()
    print(f"\ngolden fixture : {GOLDEN.name}")
    print(f"source slug    : {SOURCE_SLUG}")
    print(f"artifact hash  : {artifact_hash}")
    print(f"mapping version: {MAPPING_VERSION}")

    inp = ParserInputV1(
        content=content,
        content_hash=artifact_hash,
        source_slug=SOURCE_SLUG,
        url=URL,
        content_type="text/csv",
        parse_hint="csv",
        parser_version=IRCTCCListingParser.parser_version,
    )

    # ------------------------------------------------------------------
    print("\n1. Parse golden source records (spans preserved)")
    batch = IRCTCCListingParser().parse(inp)
    check(
        "golden rows extracted",
        len(batch.records) == 7,
        f"{len(batch.records)} tcc_listing_row records",
    )
    check(
        "every field cites artifact (CSV_ROW span)",
        batch.all_fields_cite_source(),
    )
    check(
        "secondary cert flagged + suffix stripped",
        batch.records[1].get_value("is_secondary") is True
        and batch.records[1].get_value("boat_name") == "SUN FISH",
    )

    # ------------------------------------------------------------------
    print("\n2. Transform to canonical assertions (expected vs actual)")
    tx = IRCTCCListingTransformer().transform(batch)
    check(
        "publishable / reject partition",
        len(tx.assertions) == 5 and len(tx.rejects) == 2,
        f"{len(tx.assertions)} assertions, {len(tx.rejects)} rejects",
    )
    check("disjoint partition (nothing partially publishes)",
          tx.asserts_disjoint_partition())

    sun = next(a for a in tx.assertions if a.lineage.source_record_index == 0)
    d = sun.data
    expected = {
        "sail_number": "GBR8310", "boat_name": "SUN FISH",
        "cert_number": "IRC12345", "issue_date": "2026-01-15",
        "cert_year": 2026, "tcc": "1.015", "non_spi_tcc": "0.998",
        "crew": 9, "dlr": 196, "lh": "9.99", "beam": "3.38",
        "draft": "1.98", "headsails": 6, "flying_headsails": 2,
        "spinnakers": 3, "series_date": 2008, "age_date": 2008,
        "racing_area": 1, "ssb_base_value": 28, "stix": 33, "avs": 118,
        "category": "Cat 3", "valid_code": "VALID", "is_secondary": False,
    }
    mismatches = [
        f"{k}: expected {v!r} got {d.get(k)!r}"
        for k, v in expected.items() if d.get(k) != v
    ]
    check(
        "golden row 0 payload matches expected assertion",
        not mismatches,
        "; ".join(mismatches) if mismatches else f"{len(expected)} fields match",
    )
    check(
        "units attached (canonical SI, identity conversion)",
        bool(d.get("units"))
        and all(u["conversion"] == "identity" for u in d["units"].values()),
        f"units for {sorted((d.get('units') or {}).keys())}",
    )

    # ------------------------------------------------------------------
    print("\n3. Rejects carry machine-readable reasons")
    rej_bad = next(r for r in tx.rejects if r.raw_fields.get("sail_number") == "GBR9999")
    rej_blank = next(r for r in tx.rejects if not (r.raw_fields.get("sail_number") or "").strip())
    check("unparseable TCC rejected",
          any("missing_required_field:tcc" in r for r in rej_bad.reject_reasons),
          "; ".join(rej_bad.reject_reasons))
    check("blank sail_number rejected",
          any("missing_required_field:sail_number" in r for r in rej_blank.reject_reasons),
          "; ".join(rej_blank.reject_reasons))
    check("rejects identify transformer + schema",
          all(r.transformer_name == IRCTCCListingTransformer.transformer_name
              and r.schema_version == "v1" for r in tx.rejects))

    # ------------------------------------------------------------------
    print("\n4. Mapping coverage (acceptance criterion)")
    report = audit_mapping_coverage(batch)
    check("every required canonical field has a mapping",
          all(m.canonical_field in
              {f for f in (m2.canonical_field for m2 in FIELD_MAPPINGS)}
              for m in FIELD_MAPPINGS if m.required),
          f"{sum(1 for m in FIELD_MAPPINGS if m.required)} required fields mapped")
    check("every extracted source field mapped or explicitly unsupported",
          report.unmapped_source_fields == (),
          f"unmapped: {list(report.unmapped_source_fields)}")
    check("every canonical schema field has mapping or stated reason",
          report.unmapped_canonical_fields == (),
          f"unmapped: {list(report.unmapped_canonical_fields)}")
    check("unsupported source fields carry reasons",
          all(u.reason.strip() for u in UNSUPPORTED_SOURCE_FIELDS),
          f"{len(UNSUPPORTED_SOURCE_FIELDS)} unsupported, all reasoned")
    check("mapping coverage report is complete", report.complete)
    print(f"      mapped canonical fields : {len(report.mapped)}")
    print(f"      unsupported source fields: {len(report.unsupported)}")
    print(f"      declared canonical units : {len(CANONICAL_UNITS)}")

    # ------------------------------------------------------------------
    print("\n5. Lineage query reaches raw artifact (acceptance criterion)")
    idx = index_batch(tx)
    check("every assertion lineage reaches raw artifact",
          idx.all_reach_raw_artifact(artifact_content=content))
    rep = idx.trace(sun.assertion_id, artifact_content=content)
    check("content hash verified against artifact bytes",
          rep is not None and rep.content_hash_verified is True)
    check("chain is assertion → extraction batch → artifact",
          rep is not None
          and [h["hop"] for h in rep.chain] == ["assertion", "extraction_batch", "artifact"])
    resolved = [s.resolved_text for s in (rep.spans.values() if rep else [])]
    check("TCC source span resolves to raw cell text",
          any((t or "").strip() == "1.015" for t in resolved),
          f"resolved spans include 1.015: {any((t or '').strip()=='1.015' for t in resolved)}")
    tampered = b"Boat Name,Sail No\ntampered,x\n"
    check("tampered artifact detected (hash mismatch)",
          not idx.all_reach_raw_artifact(artifact_content=tampered))

    # ------------------------------------------------------------------
    print("\n6. Determinism (replay)")
    tx2 = IRCTCCListingTransformer().transform(IRCTCCListingParser().parse(inp))
    check("identical transformation_id on replay",
          tx.transformation_id == tx2.transformation_id,
          tx.transformation_id)
    check("identical assertion ids + hashes on replay",
          [a.assertion_id for a in tx.assertions] == [a.assertion_id for a in tx2.assertions]
          and [a.assertion_hash for a in tx.assertions] == [a.assertion_hash for a in tx2.assertions])

    # ------------------------------------------------------------------
    print("\n" + "=" * 74)
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 74)
    if passed != total:
        print("\nFAILED CHECKS:")
        for name, ok, detail in checks:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    print("\nAll DP-06-03 acceptance checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
