"""Unit tests for token-set sail matching in irc_data.matching.results.

Focus: the duplicate-prevention path. The previous matcher keyed boats by a
single normalised sail string, so SailSys/SailWave scrapers re-delivering the
same hull under a slightly different sail format (``EAUS1213`` vs ``AUS1213``)
created fresh ``boats`` rows. The token-set approach in
``normalize_sail_tokens`` + ``_build_sail_index`` + ``_lookup_by_tokens``
collapses those variants onto one boat.

These tests exercise the index/lookup pair directly with synthetic boat
records, bypassing the database. Integration of the new logic with the
write-path live-DB check in ``_create_boats_from_results`` is covered by the
``rematch-results`` CLI smoke run documented in the development plan; it
requires PostgreSQL-specific features (``ANY(:array)``) that aren't worth
mocking.
"""

from __future__ import annotations

from collections import defaultdict

from irc_data.matching.identity import normalize_sail_tokens
from irc_data.matching.results import _lookup_by_tokens


def _build_index_from_dicts(boats: list[dict]) -> dict[str, list[dict]]:
    """Mirror ``_build_sail_index`` but seed from in-memory dicts instead
    of a SQLAlchemy connection. Returns the same shape: token -> [boat]."""
    index: dict[str, list[dict]] = defaultdict(list)
    for b in boats:
        tokens = normalize_sail_tokens(b["sail_number"])
        rec = dict(b)
        rec["tokens"] = tokens
        for tok in tokens:
            index[tok].append(rec)
    return index


# ---------------------------------------------------------------------------
# normalize_sail_tokens sanity checks
# ---------------------------------------------------------------------------


def test_normalize_tokens_class_prefix():
    """``EAUS1213`` expands to the prefix-stripped variants."""
    toks = normalize_sail_tokens("EAUS1213")
    assert "EAUS1213" in toks
    assert "AUS1213" in toks
    assert "1213" in toks


def test_normalize_tokens_concatenation():
    """``2561&011`` is split into both halves AND the concat is preserved."""
    toks = normalize_sail_tokens("2561&011")
    assert "2561" in toks
    assert "011" in toks
    # The concat itself shouldn't be in there as a distinct token because
    # `&` is a split character, so the raw string falls away. The combined
    # ``2561011`` token only appears if the source had no separator.
    assert "2561&011" not in toks


def test_normalize_tokens_none_returns_empty():
    assert normalize_sail_tokens(None) == set()
    assert normalize_sail_tokens("") == set()


# ---------------------------------------------------------------------------
# _lookup_by_tokens — the canonical EAUS1213 <-> AUS1213 case
# ---------------------------------------------------------------------------


def test_match_eaus1213_to_existing_aus1213():
    """Spec case 1: incoming SailSys sail ``EAUS1213`` finds existing
    boat row with sail ``AUS1213``. This is THE bug we're stopping."""
    boats = [
        {"id": 1, "sail_number": "AUS1213", "boat_name": "Foo",   "country": "AUS"},
        {"id": 2, "sail_number": "AUS9999", "boat_name": "Bar",   "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    candidates = _lookup_by_tokens(index, "EAUS1213")
    assert len(candidates) == 1
    assert candidates[0]["id"] == 1


def test_match_2561_to_existing_concatenation():
    """Spec case 2: incoming sail ``2561`` finds boat stored as ``2561&011``.
    The stored sail is split into tokens at index-build time."""
    boats = [
        {"id": 10, "sail_number": "2561&011", "boat_name": "Concat Boat", "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    candidates = _lookup_by_tokens(index, "2561")
    assert len(candidates) == 1
    assert candidates[0]["id"] == 10


def test_no_match_7085_against_2561_011():
    """Spec case 3: incoming ``7085`` does NOT match ``2561&011``. The
    tokens don't intersect, so no candidate is returned."""
    boats = [
        {"id": 10, "sail_number": "2561&011", "boat_name": "Concat Boat", "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    candidates = _lookup_by_tokens(index, "7085")
    assert candidates == []


def test_aus291_picks_right_one_among_eaus291_and_eaus292():
    """Spec case 4: incoming ``AUS291`` resolves to ``EAUS291``, not
    ``EAUS292``. Both stored boats use a class-prefixed sail; the
    suffix-numeric token distinguishes them."""
    boats = [
        {"id": 100, "sail_number": "EAUS291", "boat_name": "Right",   "country": "AUS"},
        {"id": 200, "sail_number": "EAUS292", "boat_name": "Wrong",   "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    candidates = _lookup_by_tokens(index, "AUS291")
    ids = {c["id"] for c in candidates}
    assert ids == {100}, f"expected only id=100, got {ids}"


def test_none_sail_no_false_matches():
    """Spec case 5: incoming sail of ``None`` returns no candidates —
    matchers must fall through to name-based logic without ever
    selecting an arbitrary boat."""
    boats = [
        {"id": 1, "sail_number": "AUS1213", "boat_name": "Foo", "country": "AUS"},
        {"id": 2, "sail_number": "GBR9999", "boat_name": "Bar", "country": "GBR"},
    ]
    index = _build_index_from_dicts(boats)
    assert _lookup_by_tokens(index, None) == []
    assert _lookup_by_tokens(index, "") == []


# ---------------------------------------------------------------------------
# Additional regression checks
# ---------------------------------------------------------------------------


def test_lookup_dedupes_when_two_tokens_hit_same_boat():
    """An incoming sail string that shares two tokens with a stored boat
    must still produce one candidate, not two."""
    boats = [
        {"id": 42, "sail_number": "EAUS1213", "boat_name": "DoubleHit", "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    # Incoming "EAUS1213" produces tokens {EAUS1213, AUS1213, 1213} —
    # every one of those tokens points back to boat id=42. The lookup
    # must dedup to a single candidate.
    candidates = _lookup_by_tokens(index, "EAUS1213")
    assert len(candidates) == 1
    assert candidates[0]["id"] == 42


def test_eaus1213_to_eaus1213_no_dupes():
    """When the lookup string is identical to a stored value the match
    is exact and returns just the one boat (idempotency check — running
    ``rematch-results`` repeatedly must be a no-op)."""
    boats = [
        {"id": 5, "sail_number": "EAUS1213", "boat_name": "Same",  "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    candidates = _lookup_by_tokens(index, "EAUS1213")
    assert [c["id"] for c in candidates] == [5]


def test_class_prefix_lookup_returns_all_distinct_candidates():
    """When two boats both expand to share a token (here ``1213`` and
    ``AUS1213``) via SailSys class-prefix stripping, both come back so
    the matcher can disambiguate by name/TCC/country downstream."""
    boats = [
        # YAUS1213 expands to {YAUS1213, AUS1213, 1213}
        {"id": 1, "sail_number": "YAUS1213",  "boat_name": "First",  "country": "AUS"},
        # EAUS1213 expands to {EAUS1213, AUS1213, 1213}
        {"id": 2, "sail_number": "EAUS1213",  "boat_name": "Second", "country": "AUS"},
    ]
    index = _build_index_from_dicts(boats)
    candidates = _lookup_by_tokens(index, "1213")
    ids = {c["id"] for c in candidates}
    assert ids == {1, 2}
