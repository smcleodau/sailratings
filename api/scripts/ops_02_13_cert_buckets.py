"""OPS-02-13 — close the extend.ai cert-parsing audit buckets.

Verifies (and with ``--apply``, repairs) the three certificate buckets the
extend.ai audit (OPS-02-01) opened, and records before/after counts into
``admin_metrics``:

  Bucket A — spinnaker sym/asym misclassification
      Evidence table: ``cert_sym_asym_reclassify`` (1,631 rows; the old
      symmetric values snapshot).  The parser now classifies the printed
      spinnaker block by geometry (SLU > SLE ⇒ asymmetric, SLU == SLE ⇒
      symmetric) — see ``irc_data.parsers.certificate_pdf.classify_spinnaker``.
      This runner re-parses every spinnaker certificate and checks the DB
      classification matches the parser.  ``--apply`` moves contradicting
      values from ``sym_*`` to ``asym_*`` and appends an audit row.

  Bucket B — SER / nodisp certificates
      Evidence table: ``cert_reparse_disp_draft`` (1,070 rows; displacement /
      draft recovered from FR/IT/ES-labelled PDFs).  The runner re-parses
      every audited cert and checks the DB still carries the recovered
      values, and counts any remaining NULL displacement/draft rows.

  Bucket C — LWP / DLR reparse
      Evidence table: ``cert_reparse_lwp_dlr`` (59 rows).  Same verification
      shape as bucket B.

  Bucket D — FL fields
      Certificates never print a literal ``FL`` label; the headsail luff
      perpendicular is printed as ``HLP``.  The parser now populates ``fl``
      from ``HLP``; ``--apply`` backfills ``irc_certificates.fl`` from
      ``hlp`` where ``fl IS NULL``.

Every bucket reports ``before`` (state recorded in the audit tables / live
NULL count) and ``after`` (current verified state) and the runner exits
non-zero if any verification fails, so CI can gate on it.

Usage:
    python3 scripts/ops_02_13_cert_buckets.py            # verify + report
    python3 scripts/ops_02_13_cert_buckets.py --apply    # repair drift
    python3 scripts/ops_02_13_cert_buckets.py --limit N  # cap re-parses
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # noqa: E402

from irc_data.db.connection import get_engine  # noqa: E402
from irc_data.ops.cert_paths import resolve_cert_pdf  # noqa: E402
from irc_data.parsers.certificate_pdf import (  # noqa: E402
    classify_spinnaker,
    parse_certificate_pdf,
)

TOL = Decimal("0.02")  # comparison tolerance ≈ 2 × print resolution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dec(v) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _close(a: Decimal | None, b: Decimal | None, tol: Decimal = TOL) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def record_metric(
    conn: Connection,
    metric: str,
    *,
    scope: str = "",
    phase: str = "",
    value_num: float | None = None,
    value_text: str | None = None,
    meta: dict | None = None,
    dry_run: bool = False,
) -> None:
    if dry_run:
        return
    conn.execute(
        text(
            """
            INSERT INTO admin_metrics
                (metric, scope, phase, value_num, value_text, meta)
            VALUES
                (:metric, :scope, :phase, :value_num, :value_text,
                 CAST(:meta AS jsonb))
            """
        ),
        {
            "metric": metric,
            "scope": scope,
            "phase": phase,
            "value_num": value_num,
            "value_text": value_text,
            "meta": json.dumps(meta or {}),
        },
    )


@dataclass
class BucketReport:
    name: str
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)
    ok: bool = True
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"--- {self.name} ---"]
        lines.append(f"  before: {self.before}")
        lines.append(f"  after:  {self.after}")
        status = "OK" if self.ok else "FAIL"
        lines.append(f"  status: {status}")
        for n in self.notes:
            lines.append(f"  note:   {n}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bucket A — spinnaker sym/asym
# ---------------------------------------------------------------------------


def _db_spin_kind(row) -> str | None:
    if row["sym_slu"] is not None or row["sym_sle"] is not None:
        return "sym"
    if row["asym_slu"] is not None or row["asym_sle"] is not None:
        return "asym"
    return None


def bucket_sym_asym(
    conn: Connection, *, apply: bool, limit: int | None, dry_run: bool
) -> BucketReport:
    rep = BucketReport("Bucket A — spinnaker sym/asym misclassification")

    audit_total = conn.execute(
        text("SELECT COUNT(*) FROM cert_sym_asym_reclassify")
    ).scalar_one()
    rep.before["misclassified_rows_fixed_2026_05_15"] = int(audit_total)

    rows = conn.execute(
        text(
            """
            SELECT id, cert_number, pdf_path,
                   sym_slu, sym_sle, sym_sf, sym_shw,
                   asym_slu, asym_sle, asym_sf, asym_shw
            FROM irc_certificates
            WHERE sym_slu IS NOT NULL OR asym_slu IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings().all()
    if limit:
        rows = rows[:limit]
    rep.before["db_spinnaker_certs"] = len(rows)

    n_verified = 0
    n_missing_pdf = 0
    n_parse_error = 0
    contradictions: list[dict] = []

    for i, row in enumerate(rows, start=1):
        path = resolve_cert_pdf(row["pdf_path"])
        if path is None:
            n_missing_pdf += 1
            continue
        try:
            parsed = parse_certificate_pdf(path)
        except Exception as e:  # noqa: BLE001 — count & continue
            n_parse_error += 1
            rep.notes.append(f"cert_id={row['id']} parse error: {e}")
            continue

        pdf_kind, _spin = _pdf_kind(parsed)
        db_kind = _db_spin_kind(row)

        if pdf_kind is None:
            # PDF has no spinnaker block but DB carries one — not drift this
            # bucket owns; note and move on.
            rep.notes.append(
                f"cert_id={row['id']} ({row['cert_number']}): PDF has no "
                f"spinnaker block, DB kind={db_kind}"
            )
            continue
        if db_kind == pdf_kind:
            n_verified += 1
        else:
            contradictions.append(
                {
                    "id": row["id"],
                    "cert_number": row["cert_number"],
                    "db_kind": db_kind,
                    "pdf_kind": pdf_kind,
                    "parsed": parsed,
                }
            )
        if i % 500 == 0:
            print(f"  [bucket A {i}/{len(rows)}] verified={n_verified} "
                  f"contradictions={len(contradictions)} missing={n_missing_pdf}",
                  flush=True)

    rep.after["verified_match"] = n_verified
    rep.after["contradictions"] = len(contradictions)
    rep.after["missing_pdf"] = n_missing_pdf
    rep.after["parse_errors"] = n_parse_error

    n_applied = 0
    if contradictions and apply:
        for c in contradictions:
            parsed = c["parsed"]
            kind, spin = classify_spinnaker(
                *_spin_block(parsed)
            )
            _apply_spin_reclassify(conn, c["id"], kind, spin, dry_run=dry_run)
            n_applied += 1
    rep.after["reclassified_now"] = n_applied
    if contradictions and not apply:
        rep.ok = False
        sample = ", ".join(str(c["cert_number"]) for c in contradictions[:10])
        rep.notes.append(
            f"{len(contradictions)} certs still contradict the parser "
            f"(run with --apply to fix). Sample: {sample}"
        )

    record_metric(
        conn,
        "cert.spinnaker.sym_asym",
        phase="before",
        value_num=float(audit_total),
        meta={"db_spinnaker_certs": len(rows)},
        dry_run=dry_run,
    )
    record_metric(
        conn,
        "cert.spinnaker.sym_asym",
        phase="after",
        value_num=float(len(contradictions) - n_applied),
        meta={
            "verified_match": n_verified,
            "contradictions_found": len(contradictions),
            "reclassified_now": n_applied,
            "missing_pdf": n_missing_pdf,
            "parse_errors": n_parse_error,
        },
        dry_run=dry_run,
    )
    return rep


def _spin_block(parsed) -> tuple[Decimal | None, ...]:
    """The printed spinnaker block, whichever side the parser put it on."""
    slu = parsed.sym_slu if parsed.sym_slu is not None else parsed.asym_slu
    sle = parsed.sym_sle if parsed.sym_sle is not None else parsed.asym_sle
    shw = parsed.sym_shw if parsed.sym_shw is not None else parsed.asym_shw
    sfl = parsed.sym_sf if parsed.sym_sf is not None else parsed.asym_sf
    return slu, sle, shw, sfl


def _pdf_kind(parsed) -> tuple[str | None, dict]:
    return classify_spinnaker(*_spin_block(parsed))


def _apply_spin_reclassify(
    conn: Connection, cert_id: int, kind: str | None, spin: dict, *, dry_run: bool
) -> None:
    """Move a cert's spinnaker values to the classified side, with audit row."""
    if dry_run or kind is None:
        return
    # Snapshot the pre-update sym values exactly once per (cert, old values)
    # so re-runs after convergence do not append duplicate audit rows.
    conn.execute(
        text(
            """
            INSERT INTO cert_sym_asym_reclassify
                (cert_id, old_sym_slu, old_sym_sle, old_sym_sf, old_sym_shw)
            SELECT id, sym_slu, sym_sle, sym_sf, sym_shw
            FROM irc_certificates
            WHERE id = :cid AND sym_slu IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM cert_sym_asym_reclassify r
                  WHERE r.cert_id = :cid
                    AND r.old_sym_slu IS NOT DISTINCT FROM irc_certificates.sym_slu
                    AND r.old_sym_sle IS NOT DISTINCT FROM irc_certificates.sym_sle
              )
            """
        ),
        {"cid": cert_id},
    )
    if kind == "asym":
        conn.execute(
            text(
                """
                UPDATE irc_certificates
                SET asym_slu = :slu, asym_sle = :sle,
                    asym_sf = :sf, asym_shw = :shw,
                    sym_slu = NULL, sym_sle = NULL,
                    sym_sf = NULL, sym_shw = NULL
                WHERE id = :cid
                """
            ),
            {"cid": cert_id, **spin},
        )
    else:
        conn.execute(
            text(
                """
                UPDATE irc_certificates
                SET sym_slu = :slu, sym_sle = :sle,
                    sym_sf = :sf, sym_shw = :shw,
                    asym_slu = NULL, asym_sle = NULL,
                    asym_sf = NULL, asym_shw = NULL
                WHERE id = :cid
                """
            ),
            {"cid": cert_id, **spin},
        )


# ---------------------------------------------------------------------------
# Bucket B/C — value recovery verification
# ---------------------------------------------------------------------------


def _verify_value_bucket(
    conn: Connection,
    *,
    name: str,
    audit_table: str,
    audit_cols: tuple[str, ...],
    db_cols: tuple[str, ...],
    parse_attrs: tuple[str, ...],
    null_metric: str,
    dry_run: bool,
) -> BucketReport:
    rep = BucketReport(name)

    audit_total = conn.execute(
        text(f"SELECT COUNT(*) FROM {audit_table}")
    ).scalar_one()
    rep.before["rows_recovered_2026_05_15"] = int(audit_total)

    col_list = ", ".join(f"r.{c}" for c in audit_cols)
    rows = conn.execute(
        text(
            f"""
            SELECT r.cert_id, c.cert_number, c.pdf_path, {col_list},
                   {", ".join(f"c.{c} AS db_{c}" for c in db_cols)}
            FROM {audit_table} r
            JOIN irc_certificates c ON c.id = r.cert_id
            ORDER BY r.cert_id
            """
        )
    ).mappings().all()

    n_verified = 0
    n_missing_pdf = 0
    n_parse_error = 0
    mismatches: list[str] = []

    for row in rows:
        path = resolve_cert_pdf(row["pdf_path"])
        if path is None:
            n_missing_pdf += 1
            continue
        try:
            parsed = parse_certificate_pdf(path)
        except Exception as e:  # noqa: BLE001
            n_parse_error += 1
            rep.notes.append(f"cert_id={row['cert_id']} parse error: {e}")
            continue

        ok = True
        for audit_col, db_col, attr in zip(audit_cols, db_cols, parse_attrs):
            audit_val = _dec(row[audit_col])
            db_val = _dec(row[f"db_{db_col}"])
            pdf_val = _dec(getattr(parsed, attr))
            if audit_val is None:
                continue
            if not _close(db_val, audit_val) or not _close(pdf_val, audit_val):
                ok = False
                mismatches.append(
                    f"cert_id={row['cert_id']} ({row['cert_number']}) {attr}: "
                    f"audit={audit_val} db={db_val} pdf={pdf_val}"
                )
        if ok:
            n_verified += 1

    nulls_now = conn.execute(
        text(
            f"SELECT COUNT(*) FROM irc_certificates WHERE {db_cols[0]} IS NULL"
        )
    ).scalar_one()
    rep.after["verified_match"] = n_verified
    rep.after["mismatches"] = len(mismatches)
    rep.after["missing_pdf"] = n_missing_pdf
    rep.after["parse_errors"] = n_parse_error
    rep.after[f"{db_cols[0]}_null_now"] = int(nulls_now)
    if mismatches:
        rep.ok = False
        rep.notes.extend(mismatches[:10])

    record_metric(
        conn, null_metric, phase="before", value_num=float(audit_total),
        dry_run=dry_run,
    )
    record_metric(
        conn, null_metric, phase="after", value_num=float(nulls_now),
        meta={
            "verified_match": n_verified,
            "mismatches": len(mismatches),
            "missing_pdf": n_missing_pdf,
            "parse_errors": n_parse_error,
        },
        dry_run=dry_run,
    )
    return rep


# ---------------------------------------------------------------------------
# Bucket D — FL
# ---------------------------------------------------------------------------


def bucket_fl(conn: Connection, *, apply: bool, dry_run: bool) -> BucketReport:
    rep = BucketReport("Bucket D — FL fields (printed as HLP on certs)")

    row = conn.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE fl IS NULL AND hlp IS NOT NULL) AS backfillable,
              COUNT(*) FILTER (WHERE fl IS NOT NULL)                 AS fl_present,
              COUNT(*) FILTER (WHERE hlp IS NOT NULL)                AS hlp_present
            FROM irc_certificates
            """
        )
    ).one()
    rep.before["fl_null_but_hlp_present"] = int(row.backfillable)
    rep.before["fl_present"] = int(row.fl_present)

    n_applied = 0
    if apply and row.backfillable:
        if not dry_run:
            res = conn.execute(
                text(
                    """
                    UPDATE irc_certificates
                    SET fl = hlp
                    WHERE fl IS NULL AND hlp IS NOT NULL
                    """
                )
            )
            n_applied = res.rowcount
        else:
            n_applied = int(row.backfillable)

    after = conn.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE fl IS NULL AND hlp IS NOT NULL) AS backfillable,
              COUNT(*) FILTER (WHERE fl IS NOT NULL)                 AS fl_present
            FROM irc_certificates
            """
        )
    ).one()
    rep.after["fl_null_but_hlp_present"] = int(after.backfillable)
    rep.after["fl_present"] = int(after.fl_present)
    rep.after["backfilled_now"] = n_applied
    rep.ok = int(after.backfillable) == 0 or not apply

    record_metric(
        conn, "cert.fl.null_rate", phase="before",
        value_num=float(row.backfillable),
        meta={"hlp_present": int(row.hlp_present)}, dry_run=dry_run,
    )
    record_metric(
        conn, "cert.fl.null_rate", phase="after",
        value_num=float(after.backfillable),
        meta={"backfilled_now": n_applied}, dry_run=dry_run,
    )
    return rep


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Repair contradictions / backfill fl (default: verify only).")
    ap.add_argument("--dry-run", action="store_true",
                    help="No DB writes at all (implies verify-only for metrics).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap bucket-A re-parses (debugging).")
    args = ap.parse_args()

    engine: Engine = get_engine()
    t0 = time.time()
    reports: list[BucketReport] = []

    with engine.begin() as conn:
        reports.append(
            bucket_sym_asym(conn, apply=args.apply, limit=args.limit,
                            dry_run=args.dry_run)
        )
        reports.append(
            _verify_value_bucket(
                conn,
                name="Bucket B — SER / nodisp (displacement & draft)",
                audit_table="cert_reparse_disp_draft",
                audit_cols=("new_displacement", "new_draft"),
                db_cols=("displacement_kg", "draft"),
                parse_attrs=("displacement", "draft"),
                null_metric="cert.nodisp",
                dry_run=args.dry_run,
            )
        )
        reports.append(
            _verify_value_bucket(
                conn,
                name="Bucket C — LWP / DLR reparse",
                audit_table="cert_reparse_lwp_dlr",
                audit_cols=("new_lwp", "new_dlr"),
                db_cols=("lwp", "dlr"),
                parse_attrs=("lwp", "dlr"),
                null_metric="cert.lwp_dlr",
                dry_run=args.dry_run,
            )
        )
        reports.append(bucket_fl(conn, apply=args.apply, dry_run=args.dry_run))

    print()
    print("=" * 68)
    print(f"OPS-02-13 cert bucket report ({time.time() - t0:.1f}s)")
    print("=" * 68)
    ok = True
    for rep in reports:
        print(rep.render())
        print()
        ok = ok and rep.ok
    print("OVERALL:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
