"""Backfill `design_classes` from raw IRC + ORC certificate data.

Sources mined:
  * `orc_certificates` top-level columns (class_name, designer, builder, year_built, loa, draft, displacement)
  * `orc_certificates.raw_data` (Designer / Builder / Age_Year / LOA / Draft / Dspl_Sailing / Class)
  * `certificates.raw_data` -> `design_raw` joined with `boats` (design, designer, builder, year_built, loa, beam_max, displacement_kg)

For every distinct (normalized) design name we compute:
  * modal designer + builder (skipped if >= 5 distinct values -> ambiguous)
  * modal year built (skipped if >30% of certs disagree with the mode)
  * mean LOA / beam / draft / displacement (winsorized at p10/p90 to reject outliers)

For each design name found:
  * If the canonical name exists in `design_classes` (case-insensitive on
    `name_canonical`, plus the alias map in `matching.designs`), UPDATE that row
    -- ONLY overwriting NULL columns. Existing values are preserved.
  * Otherwise emit it to a list of new-design candidates (NOT inserted).

SAFETY: idempotent, never overwrites non-NULL values, never auto-inserts.

Run via:
    source .venv/bin/activate
    python -m scripts.backfill_design_classes [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine
from irc_data.matching.designs import (
    DESIGN_ALIASES,
    normalize_builder,
    normalize_design,
    normalize_designer,
)

# ---------------------------------------------------------------------------
# Tunables / constants
# ---------------------------------------------------------------------------

AMBIGUITY_THRESHOLD = 5  # >= this many distinct values -> ambiguous, skip text
YEAR_DISAGREEMENT_PCT = 0.30  # if >30% of certs disagree with modal year, skip
NEW_CANDIDATE_MIN_SUPPORT = 2  # cert mentions needed before we surface a candidate

TEXT_COLS = ("designer", "builder")
NUM_COLS = (
    "nominal_loa",
    "nominal_beam",
    "nominal_draft",
    "nominal_displacement",
)
NUM_TOLERANCE = {
    "nominal_loa": 0.5,  # metres
    "nominal_beam": 0.3,
    "nominal_draft": 0.3,
    "nominal_displacement": 500.0,  # kg
}

UPDATABLE_COLS = ("designer", "builder", "year_first") + NUM_COLS

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DesignSample:
    designer: str | None = None
    builder: str | None = None
    year_built: int | None = None
    loa: float | None = None
    beam: float | None = None
    draft: float | None = None
    displacement: float | None = None
    raw_name: str | None = None


@dataclass
class DesignAggregate:
    canonical: str
    raw_variants: Counter = field(default_factory=Counter)
    designers: Counter = field(default_factory=Counter)
    builders: Counter = field(default_factory=Counter)
    years: Counter = field(default_factory=Counter)
    loas: list[float] = field(default_factory=list)
    beams: list[float] = field(default_factory=list)
    drafts: list[float] = field(default_factory=list)
    displacements: list[float] = field(default_factory=list)
    n_certs: int = 0

    def add(self, s: DesignSample) -> None:
        self.n_certs += 1
        if s.raw_name:
            self.raw_variants[s.raw_name.strip()] += 1
        if s.designer:
            # Collapse spelling variants (Andrieu / DanielAndrieu / Daniel Andrieu)
            # via the DESIGNER_ALIASES map BEFORE counting, so the mode picks
            # the canonical spelling.
            d = normalize_designer(s.designer)
            if d:
                self.designers[d] += 1
        if s.builder:
            b = normalize_builder(s.builder)
            if b:
                self.builders[b] += 1
        if s.year_built and 1800 < s.year_built < 2100:
            self.years[int(s.year_built)] += 1
        if s.loa and 2.0 < s.loa < 50.0:
            self.loas.append(float(s.loa))
        if s.beam and 0.5 < s.beam < 12.0:
            self.beams.append(float(s.beam))
        if s.draft and 0.1 < s.draft < 8.0:
            self.drafts.append(float(s.draft))
        if s.displacement and 100 < s.displacement < 50_000:
            self.displacements.append(float(s.displacement))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_text(t: str | None) -> str | None:
    if t is None:
        return None
    t = re.sub(r"\s+", " ", str(t).strip())
    return t.title() if t and t.isupper() else t or None


def _canon_key(name: str) -> str:
    """Case- and punctuation-insensitive key for matching design names."""
    return re.sub(r"\s+", " ", name.strip().lower())


def _winsorized_mean(values: list[float], p_low: float = 0.10, p_high: float = 0.90) -> float | None:
    if not values:
        return None
    if len(values) < 4:
        return round(mean(values), 2)
    vs = sorted(values)
    lo = vs[int(len(vs) * p_low)]
    hi = vs[int(len(vs) * p_high)]
    clipped = [min(max(v, lo), hi) for v in vs]
    return round(mean(clipped), 2)


def _modal_text(counts: Counter) -> tuple[str | None, str]:
    """Return (mode, status). status: 'ok' | 'ambiguous' | 'empty'."""
    if not counts:
        return None, "empty"
    if len(counts) >= AMBIGUITY_THRESHOLD:
        return None, "ambiguous"
    most, _ = counts.most_common(1)[0]
    return most, "ok"


def _modal_year(years: Counter) -> tuple[int | None, str]:
    if not years:
        return None, "empty"
    total = sum(years.values())
    (mode_year, count) = years.most_common(1)[0]
    agreement = count / total
    if (1 - agreement) > YEAR_DISAGREEMENT_PCT:
        return None, "disputed"
    return mode_year, "ok"


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


def _iter_orc(engine: Engine) -> Iterable[tuple[str, DesignSample]]:
    sql = text(
        """
        SELECT class_name, designer, builder, year_built, loa, draft, displacement, raw_data
        FROM orc_certificates
        WHERE class_name IS NOT NULL AND class_name <> ''
        """
    )
    with engine.connect() as conn:
        for row in conn.execution_options(stream_results=True).execute(sql):
            class_name = row.class_name.strip()
            # Prefer top-level; fall back to raw_data where missing.
            rd = row.raw_data or {}
            designer = row.designer or rd.get("Designer")
            builder = row.builder or rd.get("Builder")
            year_built = row.year_built or rd.get("Age_Year")
            loa = float(row.loa) if row.loa is not None else (rd.get("LOA") or None)
            draft = float(row.draft) if row.draft is not None else (rd.get("Draft") or None)
            disp = float(row.displacement) if row.displacement is not None else (rd.get("Dspl_Sailing") or rd.get("Dspl_Measurement") or None)
            yield class_name, DesignSample(
                designer=designer,
                builder=builder,
                year_built=int(year_built) if year_built else None,
                loa=float(loa) if loa else None,
                draft=float(draft) if draft else None,
                displacement=float(disp) if disp else None,
                raw_name=class_name,
            )


def _strip_irc_design_suffix(s: str) -> str:
    """IRC `design_raw` is "<DESIGN> <DRAFT> [variant]" -- strip the trailing draft.

    Examples:
      'J 109 2.10'              -> 'J 109'
      'SUN FAST 3300 1.95 WB'   -> 'SUN FAST 3300'
      'FIRST 35 2.20 (09)'      -> 'FIRST 35'
      'FIRST 40 CR 2.45'        -> 'FIRST 40 CR'
    """
    s = s.strip()
    # match the first <space><number.number> token and chop the rest
    m = re.search(r"\s\d+\.\d+\b", s)
    if m:
        return s[: m.start()].strip()
    return s


def _iter_irc(engine: Engine) -> Iterable[tuple[str, DesignSample]]:
    sql = text(
        """
        SELECT c.raw_data->>'design_raw' AS design_raw,
               b.design AS boat_design,
               b.designer, b.builder, b.year_built, b.loa, b.beam_max, b.displacement_kg
        FROM irc_certificates c
        JOIN boats b ON b.id = c.boat_id
        WHERE (c.raw_data->>'design_raw' IS NOT NULL AND c.raw_data->>'design_raw' <> '')
           OR (b.design IS NOT NULL AND b.design <> '')
        """
    )
    with engine.connect() as conn:
        for row in conn.execution_options(stream_results=True).execute(sql):
            # Prefer boats.design (cleaner), fall back to raw_data design_raw with suffix stripped.
            name = (row.boat_design or "").strip()
            if not name and row.design_raw:
                name = _strip_irc_design_suffix(row.design_raw)
            if not name:
                continue
            yield name, DesignSample(
                designer=row.designer,
                builder=row.builder,
                year_built=row.year_built,
                loa=float(row.loa) if row.loa is not None else None,
                beam=float(row.beam_max) if row.beam_max is not None else None,
                displacement=float(row.displacement_kg) if row.displacement_kg is not None else None,
                raw_name=name,
            )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def collect_aggregates(engine: Engine) -> dict[str, DesignAggregate]:
    aggs: dict[str, DesignAggregate] = {}

    def _key(raw: str) -> str | None:
        canon = normalize_design(raw)
        if not canon:
            return None
        return canon

    n_orc = n_irc = 0
    for raw, sample in _iter_orc(engine):
        canon = _key(raw)
        if not canon:
            continue
        aggs.setdefault(canon, DesignAggregate(canonical=canon)).add(sample)
        n_orc += 1
    for raw, sample in _iter_irc(engine):
        canon = _key(raw)
        if not canon:
            continue
        aggs.setdefault(canon, DesignAggregate(canonical=canon)).add(sample)
        n_irc += 1

    print(f"  Mined {n_orc} ORC samples, {n_irc} IRC samples -> {len(aggs)} distinct designs.")
    return aggs


# ---------------------------------------------------------------------------
# Match + apply
# ---------------------------------------------------------------------------


def load_existing(engine: Engine) -> dict[str, dict[str, Any]]:
    """Map lowercased name_canonical -> row dict."""
    out: dict[str, dict[str, Any]] = {}
    sql = text(
        "SELECT id, name_canonical, designer, builder, year_first, year_last, "
        "nominal_loa, nominal_beam, nominal_draft, nominal_displacement "
        "FROM design_classes"
    )
    with engine.connect() as conn:
        for r in conn.execute(sql):
            out[_canon_key(r.name_canonical)] = dict(r._mapping)
    return out


def find_match(canonical: str, existing: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Try direct + alias matches against existing design_classes rows."""
    if (row := existing.get(_canon_key(canonical))):
        return row
    # Try via alias map: canonical may itself be the value, but raw may map to another canonical
    upper = canonical.strip().upper()
    if upper in DESIGN_ALIASES:
        target = DESIGN_ALIASES[upper]
        if (row := existing.get(_canon_key(target))):
            return row
    return None


def derive_updates(agg: DesignAggregate) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (proposed_values, notes_per_field)."""
    proposed: dict[str, Any] = {}
    notes: dict[str, str] = {}

    designer, st = _modal_text(agg.designers)
    proposed["designer"] = designer
    notes["designer"] = st
    builder, st = _modal_text(agg.builders)
    proposed["builder"] = builder
    notes["builder"] = st

    year, st = _modal_year(agg.years)
    proposed["year_first"] = year
    notes["year_first"] = st

    proposed["nominal_loa"] = _winsorized_mean(agg.loas)
    notes["nominal_loa"] = "ok" if proposed["nominal_loa"] else "empty"
    proposed["nominal_beam"] = _winsorized_mean(agg.beams)
    notes["nominal_beam"] = "ok" if proposed["nominal_beam"] else "empty"
    proposed["nominal_draft"] = _winsorized_mean(agg.drafts)
    notes["nominal_draft"] = "ok" if proposed["nominal_draft"] else "empty"
    proposed["nominal_displacement"] = _winsorized_mean(agg.displacements)
    notes["nominal_displacement"] = "ok" if proposed["nominal_displacement"] else "empty"

    return proposed, notes


def apply_updates(
    engine: Engine,
    aggs: dict[str, DesignAggregate],
    existing: dict[str, dict[str, Any]],
    dry_run: bool,
) -> tuple[int, dict[str, int], list[tuple[str, int, dict]], list[tuple[str, DesignAggregate]]]:
    """Returns (rows_touched, cols_filled_counter, new_candidates, ambiguous_list)."""

    rows_touched = 0
    cols_filled: dict[str, int] = defaultdict(int)
    new_candidates: list[tuple[str, int, dict]] = []
    ambiguous: list[tuple[str, DesignAggregate]] = []

    update_sql = text(
        """
        UPDATE design_classes SET
          designer             = COALESCE(designer,             :designer),
          builder              = COALESCE(builder,              :builder),
          year_first           = COALESCE(year_first,           :year_first),
          nominal_loa          = COALESCE(nominal_loa,          :nominal_loa),
          nominal_beam         = COALESCE(nominal_beam,         :nominal_beam),
          nominal_draft        = COALESCE(nominal_draft,        :nominal_draft),
          nominal_displacement = COALESCE(nominal_displacement, :nominal_displacement)
        WHERE id = :id
        """
    )

    # batch updates
    batch: list[dict[str, Any]] = []
    BATCH_SIZE = 100

    def _flush(conn):
        nonlocal batch
        if not batch:
            return
        conn.execute(update_sql, batch)
        batch = []

    with engine.begin() as conn:
        for canon, agg in aggs.items():
            proposed, notes = derive_updates(agg)
            row = find_match(canon, existing)

            # Track ambiguity for reporting
            ambig_fields = [f for f, n in notes.items() if n == "ambiguous"]
            if ambig_fields and len(agg.designers) + len(agg.builders) >= AMBIGUITY_THRESHOLD * 2:
                ambiguous.append((canon, agg))

            if not row:
                # new candidate -- emit but don't insert
                if agg.n_certs >= NEW_CANDIDATE_MIN_SUPPORT:
                    new_candidates.append((canon, agg.n_certs, proposed))
                continue

            # Determine which columns we'd actually fill (currently NULL on the row)
            params = {"id": row["id"]}
            will_fill: list[str] = []
            for col in UPDATABLE_COLS:
                cur = row.get(col)
                new_val = proposed.get(col)
                if cur is None and new_val is not None:
                    will_fill.append(col)
                    params[col] = new_val
                else:
                    params[col] = None  # COALESCE keeps existing

            if not will_fill:
                continue

            for col in will_fill:
                cols_filled[col] += 1
            rows_touched += 1

            if not dry_run:
                batch.append(params)
                if len(batch) >= BATCH_SIZE:
                    _flush(conn)

        if not dry_run:
            _flush(conn)

    return rows_touched, dict(cols_filled), new_candidates, ambiguous


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def fill_rates(engine: Engine) -> dict[str, tuple[int, int]]:
    sql = text(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(designer) AS designer,
          COUNT(builder) AS builder,
          COUNT(year_first) AS year_first,
          COUNT(nominal_loa) AS nominal_loa,
          COUNT(nominal_beam) AS nominal_beam,
          COUNT(nominal_draft) AS nominal_draft,
          COUNT(nominal_displacement) AS nominal_displacement
        FROM design_classes
        """
    )
    with engine.connect() as conn:
        r = conn.execute(sql).one()
        total = r.total
        return {
            "designer": (r.designer, total),
            "builder": (r.builder, total),
            "year_first": (r.year_first, total),
            "nominal_loa": (r.nominal_loa, total),
            "nominal_beam": (r.nominal_beam, total),
            "nominal_draft": (r.nominal_draft, total),
            "nominal_displacement": (r.nominal_displacement, total),
        }


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "  0.0%"
    return f"{100*n/total:5.1f}%"


def print_fill_table(before: dict, after: dict) -> None:
    print()
    print(f"  {'column':<22} {'before':>15} {'after':>15} {'delta':>10}")
    print(f"  {'-'*22} {'-'*15} {'-'*15} {'-'*10}")
    for col in (
        "designer",
        "builder",
        "year_first",
        "nominal_loa",
        "nominal_beam",
        "nominal_draft",
        "nominal_displacement",
    ):
        b_n, b_t = before[col]
        a_n, a_t = after[col]
        print(
            f"  {col:<22} {b_n:>5}/{b_t:<5} {_pct(b_n, b_t):>6}"
            f"  {a_n:>5}/{a_t:<5} {_pct(a_n, a_t):>6}"
            f"  +{a_n-b_n:<5}"
        )


def print_new_candidates(
    candidates: list[tuple[str, int, dict]], top_n: int = 20
) -> None:
    print()
    print(f"  Top {top_n} new-design candidates (NOT inserted -- review required):")
    candidates_sorted = sorted(candidates, key=lambda x: -x[1])
    print(f"  {'support':>8}  {'designer':<22}  {'builder':<20}  name")
    print(f"  {'-'*8}  {'-'*22}  {'-'*20}  {'-'*40}")
    for canon, sup, prop in candidates_sorted[:top_n]:
        d = (prop.get("designer") or "")[:22]
        b = (prop.get("builder") or "")[:20]
        print(f"  {sup:>8}  {d:<22}  {b:<20}  {canon}")


def print_ambiguous(
    ambiguous: list[tuple[str, DesignAggregate]], top_n: int = 10
) -> None:
    print()
    print(f"  Top {top_n} ambiguous designs (raw data disagrees with itself):")
    print(f"  {'#certs':>6}  {'#designers':>10}  {'#builders':>9}  name")
    print(f"  {'-'*6}  {'-'*10}  {'-'*9}  {'-'*40}")
    ambig_sorted = sorted(
        ambiguous,
        key=lambda x: -(len(x[1].designers) + len(x[1].builders)),
    )
    for canon, agg in ambig_sorted[:top_n]:
        print(
            f"  {agg.n_certs:>6}  {len(agg.designers):>10}  "
            f"{len(agg.builders):>9}  {canon}"
        )
        if agg.designers:
            ex = ", ".join(f"{d!r}x{c}" for d, c in agg.designers.most_common(3))
            print(f"          designers: {ex}")
        if agg.builders:
            ex = ", ".join(f"{b!r}x{c}" for b, c in agg.builders.most_common(3))
            print(f"          builders:  {ex}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB.")
    parser.add_argument(
        "--candidates-out",
        type=Path,
        default=Path("data/new_design_candidates.json"),
        help="Path to write new-design candidates JSON.",
    )
    parser.add_argument(
        "--ambiguous-out",
        type=Path,
        default=Path("data/ambiguous_designs.json"),
        help="Path to write ambiguous-design report JSON.",
    )
    args = parser.parse_args()

    engine = get_engine()

    print("Step 1: fill-rate baseline...")
    before = fill_rates(engine)

    print("Step 2: mining IRC + ORC sources...")
    aggs = collect_aggregates(engine)

    print("Step 3: matching against existing design_classes...")
    existing = load_existing(engine)
    print(f"  {len(existing)} existing canonical names loaded.")

    print(f"Step 4: applying updates (dry_run={args.dry_run})...")
    touched, cols_filled, new_candidates, ambiguous = apply_updates(
        engine, aggs, existing, dry_run=args.dry_run
    )
    print(f"  Rows touched: {touched}")
    print(f"  Columns filled: {dict(cols_filled)}")

    print("Step 5: post-run fill rates...")
    after = fill_rates(engine)
    print_fill_table(before, after)

    # Persist candidate + ambiguous reports for review
    args.candidates_out.parent.mkdir(parents=True, exist_ok=True)
    args.candidates_out.write_text(
        json.dumps(
            [
                {
                    "name": n,
                    "cert_support": sup,
                    "proposed": {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in p.items()},
                }
                for n, sup, p in sorted(new_candidates, key=lambda x: -x[1])
            ],
            indent=2,
            default=str,
        )
    )
    args.ambiguous_out.write_text(
        json.dumps(
            [
                {
                    "name": n,
                    "n_certs": a.n_certs,
                    "designers": a.designers.most_common(),
                    "builders": a.builders.most_common(),
                    "years": a.years.most_common(),
                }
                for n, a in sorted(
                    ambiguous,
                    key=lambda x: -(len(x[1].designers) + len(x[1].builders)),
                )
            ],
            indent=2,
            default=str,
        )
    )
    print(
        f"  Wrote {len(new_candidates)} new-design candidates -> {args.candidates_out}"
    )
    print(
        f"  Wrote {len(ambiguous)} ambiguous designs -> {args.ambiguous_out}"
    )

    print_new_candidates(new_candidates, top_n=20)
    print_ambiguous(ambiguous, top_n=10)

    return 0


if __name__ == "__main__":
    sys.exit(main())
