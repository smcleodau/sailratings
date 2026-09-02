#!/usr/bin/env python3
"""DP-05-01 verification — review the first vertical-slice rules against
real historical distributions.

What this script does
---------------------

1. Loads the real historical snapshots from the raw lake
   (``api/data/raw`` → ``/home/irc-data/data-raw``):

   * ``tcc_listing`` — 11 IRC TCC listing CSV snapshots (2009-05-18 …
     2026-05-22).
   * ``orc_register`` — ORC country-XML register snapshots
     (2026-03-14 … 2026-09-02, 134 daily dirs).

2. **Builds drift baselines from an earlier window** (the "historical
   distribution") and evaluates every registered DP-05-01 rule for the
   dataset against **held-out later snapshots** — i.e. exactly the
   production situation: rules tuned on history, applied to new data.

3. Asserts:
   * the shipped registry baseline matches a baseline freshly rebuilt
     from the historical window (threshold grounding cannot rot);
   * every held-out snapshot passes every rule (no false positives) —
     except the metrics that are legitimately un-measurable offline
     (timeliness needs a run ledger; provenance/identity confidence need
     pipeline context) which must report ``skip``, never ``pass``;
   * **fault injection**: mutating a held-out snapshot (blanking an
     identity field, shifting the TCC mean, duplicating the unique key,
     collapsing the row count) fires the expected blocking rule.

Exit code 0 = the vertical-slice rules agree with reality.

Run::

    python api/scripts/verify_dp_05_01.py
"""

from __future__ import annotations

import copy
import csv
import glob
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate the package (api/src) when run as a script
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_API_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_API_ROOT / "src"))

from irc_data.quality.dimensions import (  # noqa: E402
    DQ_DATASET_RULES,
    FieldBaseline,
    QualityDimension,
    build_field_baseline,
    evaluate_dataset,
    evaluate_rule,
    rules_for_dataset,
)

RAW = Path("/home/irc-data/data-raw")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

failures: list[str] = []


def _report(line: str) -> None:
    print(line)


# ---------------------------------------------------------------------------
# Loaders — normalise raw snapshots into canonical field names
# ---------------------------------------------------------------------------

_TCC_HEADER_MAP = {
    # 2009-era headers → canonical
    "Boat Name": "boat_name",
    "Sail No": "sail_number",
    "Cert No": "cert_number",
    "Issue Date": "issue_date",
    "Valid Date": "issue_date",
    "Cert Year": "cert_year",
    "SYSCertYear": "cert_year",
    "TCC": "tcc",
    "Non Spi TCC": "non_spi_tcc",
    "TCC Non spi ": "non_spi_tcc",
    "Crew": "crew",
    "DLR": "dlr",
    "LH": "lh",
    "LOA": "lh",
    "Endorsed": "endorsed",
}


def load_tcc_snapshot(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(path, encoding="latin-1") as fh:
        for raw in csv.DictReader(fh):
            row: dict[str, str] = {}
            for k, v in raw.items():
                canon = _TCC_HEADER_MAP.get(k)
                if canon and canon not in row:
                    row[canon] = (v or "").strip()
            rows.append(row)
    return rows


_ORC_TAG_MAP = {
    "YachtName": "boat_name",
    "SailNo": "sail_number",
    "RefNo": "ref_no",
    "CertName": "cert_name",
    "Expiry": "expiry",
    "CountryId": "country",
    "VPPYear": "vpp_year",
}


def load_orc_snapshot(dir_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for xml_file in sorted(dir_path.glob("*.xml")):
        root = ET.parse(xml_file).getroot()
        for el in root.findall(".//ROW"):
            row: dict[str, str] = {}
            for child in el:
                canon = _ORC_TAG_MAP.get(child.tag)
                if canon:
                    row[canon] = (child.text or "").strip()
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

#: Metrics that legitimately cannot be measured offline (no run ledger,
#: no pipeline context in a raw snapshot).  They must SKIP, not pass.
_OFFLINE_SKIP = {"freshness_lag_days", "provenance_gap_fraction",
                 "unmatched_fraction"}


def evaluate_snapshot(dataset: str, rows: list[dict[str, str]], label: str,
                      *, overrides: dict[str, dict] | None = None,
                      ) -> dict[str, str]:
    """Evaluate all rules for a dataset against one snapshot.

    ``overrides`` maps rule_id → params-dict replacements used to test
    with baselines rebuilt from the *historical* window rather than the
    shipped ones.  Returns rule_id → status.
    """
    statuses: dict[str, str] = {}
    for rule in rules_for_dataset(dataset):
        r = rule
        if overrides and rule.rule_id in overrides:
            r = copy.copy(rule)
            object.__setattr__(r, "params", {**rule.params, **overrides[rule.rule_id]})
        res = evaluate_rule(r, rows)
        statuses[rule.rule_id] = res.status
        yield_line = (
            f"  [{label}] {rule.rule_id:<55} {res.status:<5} "
            f"value={res.value if res.value is None else round(res.value, 5)} "
            f"warn={rule.warn_at} block={rule.block_at} "
            f"n={res.evaluated_count} fails={res.failing_count}"
        )
        _report(yield_line)
    return statuses


def check(condition: bool, message: str) -> None:
    mark = PASS if condition else FAIL
    _report(f"{mark} {message}")
    if not condition:
        failures.append(message)


# ---------------------------------------------------------------------------
# tcc_listing vertical slice
# ---------------------------------------------------------------------------


def verify_tcc_listing() -> None:
    _report("\n=== tcc_listing — real historical snapshots ===")
    files = sorted(RAW.glob("tcc_listings/tcc_listing_*.csv"))
    check(len(files) >= 8, f"loaded {len(files)} historical TCC snapshots (>= 8)")

    snapshots = [(f.stem.replace("tcc_listing_", ""), load_tcc_snapshot(f))
                 for f in files]

    # 2026 snapshots share the current schema — those are the vertical
    # slice for rule evaluation (2009 snapshots predate several columns).
    recent = [(label, rows) for label, rows in snapshots if label >= "2026"]
    check(len(recent) >= 6, f"{len(recent)} current-schema snapshots for evaluation")

    # --- baseline built from the *historical* window (all but last 2) ----
    hist, held_out = recent[:-2], recent[-2:]
    hist_means = [
        statistics.fmean(float(r["tcc"]) for r in rows if r.get("tcc"))
        for _, rows in hist
    ]
    hist_counts = [len(rows) for _, rows in hist]
    rebuilt_tcc = build_field_baseline(
        "tcc", hist_means, source=f"{hist[0][0]}..{hist[-1][0]} snapshot means",
    )
    _report(
        f"  rebuilt tcc baseline from {len(hist)} snapshots: "
        f"mean={rebuilt_tcc.mean:.4f} std={rebuilt_tcc.std:.5f} "
        f"range=[{rebuilt_tcc.minimum:.4f}, {rebuilt_tcc.maximum:.4f}]"
    )

    # The shipped registry baseline must agree with the rebuilt one —
    # this pins the thresholds to the real distribution.
    shipped = next(
        r for r in rules_for_dataset("tcc_listing")
        if r.rule_id == "tcc_listing.drift.tcc_mean_drift"
    ).params["baseline"]
    check(
        abs(shipped.mean - rebuilt_tcc.mean) < 0.01,
        f"shipped tcc baseline mean {shipped.mean:.4f} ≈ rebuilt "
        f"{rebuilt_tcc.mean:.4f} (±0.01)",
    )

    # --- evaluate held-out snapshots against *rebuilt-window* baselines --
    overrides = {
        "tcc_listing.drift.tcc_mean_drift": {"baseline": rebuilt_tcc},
        "tcc_listing.drift.row_count_drift": {"baseline_counts": hist_counts},
    }
    for label, rows in held_out:
        statuses = evaluate_snapshot("tcc_listing", rows, label,
                                     overrides=overrides)
        for rule in rules_for_dataset("tcc_listing"):
            st = statuses[rule.rule_id]
            if rule.metric.value in _OFFLINE_SKIP:
                check(st == "skip",
                      f"[{label}] {rule.rule_id} skips offline (no pipeline context)")
            else:
                check(st in ("pass",),
                      f"[{label}] {rule.rule_id} passes on real data (got {st})")

    # --- fault injection on the newest held-out snapshot ------------------
    _report("\n  -- fault injection (held-out snapshot) --")
    label, rows = held_out[-1]
    rows = [dict(r) for r in rows]

    def expect(rule_id: str, mutated: list[dict[str, str]], want: str,
               desc: str) -> None:
        rule = next(r for r in rules_for_dataset("tcc_listing")
                    if r.rule_id == rule_id)
        res = evaluate_rule(rule, mutated)
        check(res.status == want, f"{desc} → {rule_id} = {want} (got {res.status})")

    # 2% blank sail numbers → blocking completeness
    mut = [dict(r) for r in rows]
    for i in range(0, len(mut), 50):
        mut[i]["sail_number"] = ""
    expect("tcc_listing.completeness.sail_number_present", mut, "block",
           "2% blank sail_number")

    # mean TCC shifted +0.05 (≫6σ of the historical means) → drift block
    mut = [dict(r) for r in rows]
    for r in mut:
        if r.get("tcc"):
            r["tcc"] = str(float(r["tcc"]) + 0.05)
    rule = next(r for r in rules_for_dataset("tcc_listing")
                if r.rule_id == "tcc_listing.drift.tcc_mean_drift")
    shifted = copy.copy(rule)
    object.__setattr__(shifted, "params", {**rule.params, "baseline": rebuilt_tcc})
    res = evaluate_rule(shifted, mut)
    check(res.status == "block",
          f"TCC mean +0.05 shift → drift block (got {res.status}, |z|={res.value:.1f})")

    # duplicate 1% of cert numbers → uniqueness block
    mut = [dict(r) for r in rows]
    for i in range(0, 30):
        mut[i]["cert_number"] = mut[0]["cert_number"]
    expect("tcc_listing.uniqueness.cert_number_unique", mut, "block",
           "1% duplicated cert_number")

    # row count collapses 70% → count-drift block
    rule = next(r for r in rules_for_dataset("tcc_listing")
                if r.rule_id == "tcc_listing.drift.row_count_drift")
    collapsed = copy.copy(rule)
    object.__setattr__(collapsed, "params", {**rule.params, "baseline_counts": hist_counts})
    res = evaluate_rule(collapsed, rows[: len(rows) // 4])
    check(res.status == "block",
          f"row count collapse → count-drift block (got {res.status}, value={res.value:.2f})")

    # non-spi > tcc on 2% → consistency block
    mut = [dict(r) for r in rows]
    n_flip = 0
    for r in mut:
        if r.get("tcc") and r.get("non_spi_tcc"):
            if n_flip < len(mut) // 50:
                r["non_spi_tcc"] = str(float(r["tcc"]) + 0.01)
                n_flip += 1
    expect("tcc_listing.consistency.non_spi_le_tcc", mut, "block",
           "2% non_spi_tcc > tcc")


# ---------------------------------------------------------------------------
# orc_register vertical slice
# ---------------------------------------------------------------------------


def verify_orc_register() -> None:
    _report("\n=== orc_register — real historical snapshots ===")
    dirs = sorted(p for p in RAW.glob("orc/2026-*") if p.is_dir())
    check(len(dirs) >= 30, f"loaded {len(dirs)} ORC daily snapshot dirs (>= 30)")

    # Historical window: first of each month; held-out: the latest dir.
    hist_labels = ["2026-03-14", "2026-04-14", "2026-05-14", "2026-06-14"]
    hist_dirs = [RAW / "orc" / lb for lb in hist_labels if (RAW / "orc" / lb).is_dir()]
    held_dir = dirs[-1]

    hist = [(d.name, load_orc_snapshot(d)) for d in hist_dirs]
    held_label, held_rows = held_dir.name, load_orc_snapshot(held_dir)
    hist_counts = [len(rows) for _, rows in hist]
    _report(f"  historical window counts: {hist_counts}; held-out "
            f"{held_label}: {len(held_rows)} rows")

    overrides = {
        "orc_register.drift.row_count_drift": {"baseline_counts": hist_counts},
    }
    statuses = evaluate_snapshot("orc_register", held_rows, held_label,
                                 overrides=overrides)
    for rule in rules_for_dataset("orc_register"):
        st = statuses[rule.rule_id]
        if rule.metric.value in _OFFLINE_SKIP:
            check(st == "skip",
                  f"[{held_label}] {rule.rule_id} skips offline")
        elif rule.rule_id == "orc_register.drift.row_count_drift":
            # Seasonal growth is real: the latest snapshot legitimately
            # exceeds a March→June window.  Warning is acceptable (the
            # rule says 'investigate'), blocking is not.
            check(st in ("pass", "warn"),
                  f"[{held_label}] {rule.rule_id} tolerates seasonal growth (got {st})")
        else:
            check(st == "pass",
                  f"[{held_label}] {rule.rule_id} passes on real data (got {st})")

    # fault injection
    _report("\n  -- fault injection (held-out snapshot) --")
    mut = [dict(r) for r in held_rows]
    for i in range(0, len(mut), 20):
        mut[i]["sail_number"] = ""
    rule = next(r for r in rules_for_dataset("orc_register")
                if r.rule_id == "orc_register.completeness.sail_number_present")
    res = evaluate_rule(rule, mut)
    check(res.status == "block",
          f"5% blank SailNo → blocking completeness (got {res.status})")

    mut = [dict(r) for r in held_rows]
    # >0.5% duplicated RefNo (0 dups observed in all 134 snapshots)
    n_dup = int(len(mut) * 0.01)
    for i in range(n_dup):
        mut[i]["ref_no"] = mut[0]["ref_no"]
    rule = next(r for r in rules_for_dataset("orc_register")
                if r.rule_id == "orc_register.uniqueness.ref_no_unique")
    res = evaluate_rule(rule, mut)
    check(res.status == "block",
          f"1% duplicated RefNo → uniqueness block (got {res.status})")

    mut = [dict(r) for r in held_rows]
    for i in range(0, len(mut), 10):
        mut[i]["cert_name"] = "UltraLight"  # unregistered vocabulary
    rule = next(r for r in rules_for_dataset("orc_register")
                if r.rule_id == "orc_register.validity.cert_name_vocabulary")
    res = evaluate_rule(rule, mut)
    check(res.status == "block",
          f"10% unknown CertName → vocabulary block (got {res.status})")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    _report("DP-05-01 verification — vertical-slice rules vs real "
            "historical distributions")
    _report(f"raw lake: {RAW}")

    verify_tcc_listing()
    verify_orc_register()

    _report("\n=== summary ===")
    if failures:
        _report(f"{FAIL} {len(failures)} check(s) failed:")
        for f in failures:
            _report(f"  - {f}")
        return 1
    _report(f"{PASS} all checks passed — vertical-slice rules agree with "
            "real historical distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
