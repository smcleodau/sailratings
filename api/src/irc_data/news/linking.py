"""Link LLM-extracted boat mentions to canonical boat entities.

Implements the "Graph Linkage" step of SPEC-14 Sec. 4: the extracted
``mentioned_boats`` are cross-referenced against the identities known for each
boat (the ``boat_identities`` table plus ``boats.boat_name`` /
``boats.sail_number``) with fuzzy matching, and linked in the
``boat_news_mentions`` join table.

Matching strategy (highest confidence first):
1. ``sail_exact``  - normalized sail number exact match (boats.sail_number or
                     any boat_identities.sail_number).
2. ``name_exact``  - exact normalized boat-name match (boats.boat_name or any
                     boat_identities.boat_name).
3. ``trigram``     - pg_trgm similarity against boat names / identities above a
                     threshold (fuzzy). Uses the pg_trgm extension enabled in
                     migration 0009.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.matching.identity import normalize_name, normalize_sail

# Minimum pg_trgm similarity for a fuzzy match (0..1).
TRIGRAM_THRESHOLD = 0.55


@dataclass
class BoatMatch:
    """A single resolved mention -> canonical boat link."""

    boat_id: int
    mention_text: str
    match_method: str
    match_confidence: float

    def as_row(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "mention_text": self.mention_text,
            "match_method": self.match_method,
            "match_confidence": round(float(self.match_confidence), 3),
        }


def _looks_like_sail(s: str) -> bool:
    """Heuristic: does this mention look like a sail number vs a boat name?"""
    norm = normalize_sail(s)
    if not norm:
        return False
    # Sail numbers contain digits (AUS1213, 1213, GBR8994R).
    return any(c.isdigit() for c in norm)


def resolve_mentions(engine: Engine, mentions: list[str]) -> list[BoatMatch]:
    """Resolve a list of raw boat mentions to canonical boat matches.

    Deduplicates the mention list first. Returns one BoatMatch per resolved
    mention (a mention that resolves to several boats is not linked to avoid
    false positives; instead the best single fuzzy match is taken).
    """
    seen: set[str] = set()
    unique: list[str] = []
    for m in mentions:
        key = (m or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(m.strip())

    matches: list[BoatMatch] = []
    if not unique:
        return matches

    for mention in unique:
        match = _resolve_one(engine, mention)
        if match:
            matches.append(match)
    return matches


def _resolve_one(engine: Engine, mention: str) -> BoatMatch | None:
    norm_sail = normalize_sail(mention)
    norm_name = normalize_name(mention)

    with engine.connect() as conn:
        # 1. Exact sail-number match (boats.sail_number or boat_identities).
        # Sail-number normalization (strip spaces/dashes/dots, uppercase) is not a
        # SQL function, so candidate sail strings are pulled and compared in
        # Python via normalize_sail().
        if norm_sail:
            boat_id = _match_sail_python(conn, norm_sail)
            if boat_id is not None:
                return BoatMatch(boat_id, mention, "sail_exact", 1.0)

        # 2. Exact name match (boats.boat_name or boat_identities.boat_name).
        if norm_name:
            rows = conn.execute(
                text(
                    """
                    SELECT b.id
                    FROM boats b
                    WHERE UPPER(btrim(boat_name)) = :name
                    LIMIT 2
                    """
                ),
                {"name": norm_name},
            ).fetchall()
            if len(rows) == 1:
                return BoatMatch(rows[0][0], mention, "name_exact", 1.0)
            if len(rows) > 1:
                # Ambiguous exact name -> do not auto-link at full confidence;
                # fall through to fuzzy but keep best candidate only if unique-ish.
                pass
            else:
                rows = conn.execute(
                    text(
                        """
                        SELECT DISTINCT bi.boat_id
                        FROM boat_identities bi
                        WHERE UPPER(btrim(bi.boat_name)) = :name
                        LIMIT 2
                        """
                    ),
                    {"name": norm_name},
                ).fetchall()
                if len(rows) == 1:
                    return BoatMatch(rows[0][0], mention, "name_exact", 1.0)

        # 3. Fuzzy trigram match on boat names + identities.
        if norm_name and len(norm_name) >= 3:
            return _match_trigram(conn, norm_name, mention)

    return None


def _match_sail_python(conn, norm_sail: str) -> int | None:
    """Find a boat whose sail_number (or any boat_identities.sail_number)
    normalizes to ``norm_sail``.

    Normalization strips spaces/dashes/dots and uppercases; that is not a SQL
    function, so we pre-filter candidate rows in SQL (boats whose sail_number
    shares a digit token with the mention) then normalize in Python.
    """
    if not norm_sail:
        return None
    digits = "".join(c for c in norm_sail if c.isdigit())
    if len(digits) < 3:
        # Very short sail numbers are ambiguous; require an exact normalized
        # hit but still scan the (small) candidate set sharing the same digits.
        like = f"%{digits}%" if digits else "%"
    else:
        like = f"%{digits}%"

    rows = conn.execute(
        text(
            """
            SELECT id, sail_number FROM boats
            WHERE sail_number ILIKE :like
            UNION
            SELECT DISTINCT boat_id, sail_number FROM boat_identities
            WHERE sail_number ILIKE :like
            """
        ),
        {"like": like},
    ).fetchall()
    for boat_id, sail in rows:
        if sail and normalize_sail(sail) == norm_sail:
            return int(boat_id)
    return None


def _match_trigram(conn, norm_name: str, mention: str) -> BoatMatch | None:
    """Best pg_trgm similarity match above the threshold."""
    rows = conn.execute(
        text(
            """
            SELECT b.id,
                   GREATEST(
                       similarity(UPPER(btrim(b.boat_name)), :name),
                       COALESCE((SELECT MAX(similarity(UPPER(btrim(bi.boat_name)), :name))
                                 FROM boat_identities bi WHERE bi.boat_id = b.id), 0)
                   ) AS score
            FROM boats b
            WHERE b.boat_name IS NOT NULL
              AND (
                  similarity(UPPER(btrim(b.boat_name)), :name) >= :thr
                  OR EXISTS (
                      SELECT 1 FROM boat_identities bi
                      WHERE bi.boat_id = b.id
                        AND bi.boat_name IS NOT NULL
                        AND similarity(UPPER(btrim(bi.boat_name)), :name) >= :thr
                  )
              )
            ORDER BY score DESC
            LIMIT 1
            """
        ),
        {"name": norm_name, "thr": TRIGRAM_THRESHOLD},
    ).fetchall()
    if not rows:
        return None
    boat_id, score = rows[0]
    if score is None or float(score) < TRIGRAM_THRESHOLD:
        return None
    return BoatMatch(int(boat_id), mention, "trigram", round(float(score), 3))


def write_mentions(
    engine: Engine, news_id: int, matches: list[BoatMatch]
) -> int:
    """Insert BoatMatch rows into boat_news_mentions (idempotent upsert).

    Returns the number of mentions written (newly inserted or updated).
    """
    if not matches:
        return 0
    inserted = 0
    with engine.begin() as conn:
        for m in matches:
            row = m.as_row()
            res = conn.execute(
                text(
                    """
                    INSERT INTO boat_news_mentions
                        (news_id, boat_id, mention_text, match_method, match_confidence)
                    VALUES (:news_id, :boat_id, :mention_text, :match_method, :match_confidence)
                    ON CONFLICT (news_id, boat_id) DO UPDATE
                    SET mention_text = EXCLUDED.mention_text,
                        match_method = EXCLUDED.match_method,
                        match_confidence = EXCLUDED.match_confidence
                    RETURNING id
                    """
                ),
                {"news_id": news_id, **row},
            ).first()
            if res:
                inserted += 1
    return inserted


def link_article_mentions(
    engine: Engine, news_id: int, mentioned_boats: list[str]
) -> list[BoatMatch]:
    """Resolve mentions for an article and persist the links.

    Returns the BoatMatch list that was written.
    """
    matches = resolve_mentions(engine, mentioned_boats)
    write_mentions(engine, news_id, matches)
    return matches
