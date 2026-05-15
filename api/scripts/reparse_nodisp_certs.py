"""Re-parse displacement + draft for certificates where they are currently NULL.

Background: The original certificate PDF parser only matched the English labels
"Boat Weight:" / "Draft:". Roughly 1,070 IRC certificates in the DB had NULL
displacement and draft because their PDFs use the French / Italian / Spanish
labels ("Poids:", "Tirant d'eau :", "Peso:", "Calado :", "Pescaggio :", ...).

The parser regex in `irc_data.parsers.certificate_pdf` has been extended to
also match these variants. This script re-parses the affected certs and
updates the DB.

Safety guarantees:
  - Only rows where `displacement IS NULL` are considered.
  - Only the `displacement` and `draft` columns are touched, and each is
    written only if its current DB value is NULL (no overwrites).
  - Every UPDATE is recorded in `cert_reparse_disp_draft` for auditability.
  - Commits batched every 100 rows so a partial failure leaves a useful state.
  - Idempotent: re-running it after success is a no-op (because rows are now
    non-null and the WHERE filter excludes them).

USAGE:
    source .venv/bin/activate && source ~/.env
    python scripts/reparse_nodisp_certs.py            # apply changes
    python scripts/reparse_nodisp_certs.py --dry-run  # report only, no writes
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.parsers.certificate_pdf import parse_certificate_pdf

BATCH = 100

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS cert_reparse_disp_draft (
    id                SERIAL PRIMARY KEY,
    cert_id           INTEGER NOT NULL,
    parsed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    new_displacement  NUMERIC(8,1),
    new_draft         NUMERIC(6,2),
    pdf_path          TEXT,
    note              TEXT
)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="No DB writes; print plan.")
    ap.add_argument("--limit", type=int, default=None, help="Process at most N rows.")
    args = ap.parse_args()

    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(text(AUDIT_DDL))

    sql = text(
        """
        SELECT id, pdf_path
        FROM certificates
        WHERE displacement IS NULL
          AND pdf_path IS NOT NULL
        ORDER BY id
        """ + ("LIMIT :lim" if args.limit else "")
    )
    params = {"lim": args.limit} if args.limit else {}

    with engine.connect() as conn:
        rows = conn.execute(sql, params).all()

    total = len(rows)
    print(f"Found {total} candidate rows (displacement IS NULL AND pdf_path IS NOT NULL).")
    if total == 0:
        return 0

    n_updated_disp = 0
    n_updated_draft = 0
    n_no_change = 0
    n_missing_file = 0
    n_parse_error = 0

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            for i, (cert_id, pdf_path) in enumerate(rows, start=1):
                path = Path(pdf_path)
                if not path.exists():
                    n_missing_file += 1
                    if i % 50 == 0 or i == total:
                        print(f"  [{i}/{total}] (missing file count={n_missing_file})")
                    continue

                try:
                    parsed = parse_certificate_pdf(path)
                except Exception as e:  # noqa: BLE001 — log & continue
                    n_parse_error += 1
                    print(f"  cert_id={cert_id} parse error: {e}")
                    continue

                new_disp = parsed.displacement
                new_draft = parsed.draft

                if new_disp is None and new_draft is None:
                    n_no_change += 1
                    if not args.dry_run:
                        conn.execute(
                            text(
                                """
                                INSERT INTO cert_reparse_disp_draft
                                    (cert_id, new_displacement, new_draft, pdf_path, note)
                                VALUES (:cid, NULL, NULL, :pp, 'still-unparsed')
                                """
                            ),
                            {"cid": cert_id, "pp": str(path)},
                        )
                    continue

                # Build UPDATE that only writes columns currently NULL — belt &
                # braces: filter at SQL level too so a concurrent update can't be
                # clobbered.
                set_clauses = []
                update_params: dict[str, object] = {"cid": cert_id}
                if new_disp is not None:
                    set_clauses.append("displacement = :disp")
                    update_params["disp"] = new_disp
                if new_draft is not None:
                    set_clauses.append("draft = :draft")
                    update_params["draft"] = new_draft

                if set_clauses and not args.dry_run:
                    # Add per-column NULL guards so we only overwrite NULL cells.
                    guard_parts = []
                    if new_disp is not None:
                        guard_parts.append("displacement IS NULL")
                    if new_draft is not None:
                        guard_parts.append("draft IS NULL")
                    # Use OR so we still update whichever column is currently NULL
                    # even if the other has been filled. The SET clauses themselves
                    # become COALESCE-guarded so we never overwrite a non-null cell.
                    safe_set = []
                    if new_disp is not None:
                        safe_set.append("displacement = COALESCE(displacement, :disp)")
                    if new_draft is not None:
                        safe_set.append("draft = COALESCE(draft, :draft)")
                    upd_sql = text(
                        f"UPDATE certificates SET {', '.join(safe_set)} "
                        f"WHERE id = :cid AND ({' OR '.join(guard_parts)})"
                    )
                    result = conn.execute(upd_sql, update_params)
                    if result.rowcount:
                        if new_disp is not None:
                            n_updated_disp += 1
                        if new_draft is not None:
                            n_updated_draft += 1
                        conn.execute(
                            text(
                                """
                                INSERT INTO cert_reparse_disp_draft
                                    (cert_id, new_displacement, new_draft, pdf_path, note)
                                VALUES (:cid, :disp, :draft, :pp, 'updated')
                                """
                            ),
                            {
                                "cid": cert_id,
                                "disp": new_disp,
                                "draft": new_draft,
                                "pp": str(path),
                            },
                        )
                elif args.dry_run and set_clauses:
                    if new_disp is not None:
                        n_updated_disp += 1
                    if new_draft is not None:
                        n_updated_draft += 1

                if i % BATCH == 0:
                    trans.commit()
                    print(
                        f"  [{i}/{total}] committed batch — "
                        f"disp+={n_updated_disp} draft+={n_updated_draft} "
                        f"still-null={n_no_change} miss={n_missing_file} err={n_parse_error}"
                    )
                    trans = conn.begin()

            trans.commit()
        except Exception:
            trans.rollback()
            raise

    print()
    print("=== Summary ===")
    print(f"  Candidate rows:                {total}")
    print(f"  Displacement updated:          {n_updated_disp}")
    print(f"  Draft updated:                 {n_updated_draft}")
    print(f"  Still unparsed (no disp/draft):{n_no_change}")
    print(f"  Missing PDF file:              {n_missing_file}")
    print(f"  Parse errors:                  {n_parse_error}")
    if args.dry_run:
        print("  (dry-run — no writes performed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
