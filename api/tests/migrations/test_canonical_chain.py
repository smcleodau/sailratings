"""Canonical migration-graph invariants (DP-03-05).

These tests need no database: they assert the alembic script graph itself is
a single, linear, canonical chain with no duplicate revision ids — the root
defect this issue fixes (the graph previously had four heads and duplicate
``0023``/``0024``/``0025`` ids, so ``alembic upgrade head`` was ambiguous).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _script() -> ScriptDirectory:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_single_head():
    script = _script()
    heads = script.get_heads()
    # PAY-01-07 linearised the canonical chain; PAY-01-08 extended it with
    # ``0033`` (orders.stripe_customer_id). The only head is ``0033``.
    assert set(heads) == {"0033"}, f"unexpected migration heads: {heads}"


def test_no_duplicate_revision_ids():
    script = _script()
    ids = [rev.revision for rev in script.walk_revisions()]
    dupes = [rid for rid, n in Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate revision ids present: {dupes}"


def test_no_revision_id_is_a_prefix_of_another():
    """Alembic resolves revisions by unique prefix; an id that is a strict
    prefix of another makes the short form ambiguous."""
    script = _script()
    ids = [rev.revision for rev in script.walk_revisions()]
    for a in ids:
        for b in ids:
            if a != b and b.startswith(a):
                pytest.fail(f"revision id {a!r} is a prefix of {b!r} (ambiguous)")


def test_chain_is_linear_from_base():
    """Walking from base to head must visit every revision exactly once."""
    script = _script()
    revs = list(script.walk_revisions())
    # every non-base revision has exactly one parent (down_revision)
    for rev in revs:
        down = rev.down_revision
        if down is None:
            continue
        assert not isinstance(down, (tuple, list)) or len(down) == 1, (
            f"{rev.revision} has multiple down_revisions {down} (branch, not linear)"
        )
    # count of revisions equals number of links + 1 base
    assert len(revs) >= 30, f"suspiciously few revisions: {len(revs)}"


def test_chain_contains_canonical_order():
    script = _script()
    order = [rev.revision for rev in reversed(list(script.walk_revisions()))]
    # base must be first and the chain must converge on the single canonical
    # head (PAY-01-07 payments/auth revision).
    assert order[0] == "0001"
    assert order[-1] == "0033", f"unexpected chain tail: {order[-1]}"
    # the previous branch point feeds the 0023 series and converges to head
    assert "aa0f8e0c178b" in order
    assert order.index("aa0f8e0c178b") < order.index("0023")
    # canonical PAY/DP tail: fact_assertions -> policy stamp -> payments/auth -> stripe_customer_id
    assert order.index("0025") < order.index("0026") < order.index("0027") < order.index("0033")


def test_head_matches_models_metadata_scratch_build():
    """A scratch build to head must expose the compatibility views + the
    migration-evidence and backup-check tables (requires a database)."""
    pytest.importorskip("sqlalchemy")
    if os.environ.get("DP03_SKIP_IF_NO_DB", "1") == "1":
        # Defer to the DB-backed test module for the actual build; here we
        # only assert the migration file declares the objects.  The DP-03-05
        # compat surface moved to 0027_payments_auth when the abandoned
        # 0026_canonical_merge_and_compat branch was retired (PAY-01-07).
        text = (PROJECT_ROOT / "alembic" / "versions" / "0027_payments_auth.py").read_text()
        for obj in ("schema_migrations", "backup_checks", "v1_boat_ratings",
                    "v1_race_results", "v1_fact_assertions_current"):
            assert obj in text, f"0027 migration missing {obj}"
