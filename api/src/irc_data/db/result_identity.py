"""Natural key for ``race_results`` rows: decides when a re-scraped result is
the *same* published result as a row we already hold.

Why not the matched ``boat_id``: boat matching changes between scrapes (new
boats get added, a site corrects a sail number, ``rematch-results`` links a
previously unmatched row), so keying on it turned every rematch into a second
row. The key here is built only from what the *source* published.

Key
---
Scope (all must be equal, NULL-safe)::

    source, event_name, event_date, race_name, race_number,
    race_date_specific, class_name, division, canonical(source_url)

``source_url`` is part of the scope because several sources publish distinct
results that differ only by page (RHKYC sub-series pages share one
event/class name; RORC season pages share an event name with no race name).
URLs are canonicalised so known site re-organisations (RORC dropping
``/files``, Cowes Week presentation params) do not split a result. A missing
URL on either side is treated as compatible.

Within the scope the boat is identified by a normalised boat name and a
normalised sail number:

* **strong** match: name and sail both present and both equal. Always the
  same result, even if the place moved (series standings update).
* **weak** match: only one identifier agrees (the site renamed the boat but
  kept the sail, corrected the sail but kept the name, or publishes no sail
  at all). Accepted only with corroboration: the same place (and status),
  or the same matched boat; for a name-only match where a sail number is
  missing, an existing row that was never matched to a boat also counts.
  This keeps two genuinely different boats with the same name in one race
  (e.g. ``ABRACADABRA`` and ``ABRACADABRA ²`` in the 2025 Fastnet) apart.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

# Columns that scope a result. Everything here must be equal (NULL-safe) for
# two rows to be candidates for the same result.
SCOPE_COLUMNS = (
    "source",
    "event_name",
    "event_date",
    "race_name",
    "race_number",
    "race_date_specific",
    "class_name",
    "division",
)

# Per-host URL canonicalisation for site re-organisations that did not change
# the published result.
_URL_PATH_ALIASES: dict[str, list[tuple[str, str]]] = {
    # rorc.org moved /files/raceresults/... to /raceresults/...
    "rorc.org": [(r"^/files/raceresults/", "/raceresults/")],
}
_URL_VOLATILE_QUERY_KEYS: dict[str, set[str]] = {
    # Cowes Week: map/ui/style/override/section pick the site skin; the result
    # is identified by page + resultrequest.
    "cowesweek.co.uk": {"map", "ui", "style", "override", "section"},
}


def normalize_key_name(name: str | None) -> str:
    """Upper-case boat name keeping only letters and digits (Unicode-aware)."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKC", str(name)).upper()
    return "".join(ch for ch in name if ch.isalnum())


def normalize_key_sail(sail: str | None) -> str:
    """Upper-case sail number keeping only letters and digits."""
    return normalize_key_name(sail)


def canonical_source_url(url: str | None) -> str | None:
    """Canonical page identity for ``source_url`` (None when absent)."""
    if not url or not str(url).strip():
        return None
    parts = urlsplit(str(url).strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or "/"
    for pattern, repl in _URL_PATH_ALIASES.get(host, []):
        path = re.sub(pattern, repl, path)
    path = path.rstrip("/") or "/"
    volatile = _URL_VOLATILE_QUERY_KEYS.get(host, set())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in volatile
    )
    return host + path + ("?" + urlencode(query) if query else "")


def urls_compatible(a: str | None, b: str | None) -> bool:
    ca, cb = canonical_source_url(a), canonical_source_url(b)
    return ca is None or cb is None or ca == cb


@dataclass(frozen=True)
class ResultIdentity:
    """The parts of a result row used to recognise it within a scope."""

    id: int | None
    name: str  # normalised
    sail: str  # normalised
    place: int | None
    status: str | None
    boat_id: int | None
    source_url: str | None

    @classmethod
    def build(cls, *, id=None, boat_name=None, sail_number=None, place=None,
              status=None, boat_id=None, source_url=None) -> "ResultIdentity":
        return cls(
            id=id,
            name=normalize_key_name(boat_name),
            sail=normalize_key_sail(sail_number),
            place=place,
            status=(status or "").strip().upper() or None,
            boat_id=boat_id,
            source_url=source_url,
        )


def _same_place(a: ResultIdentity, b: ResultIdentity) -> bool:
    if a.place is not None or b.place is not None:
        return a.place == b.place
    # Both unplaced (DNF/DNS...): only corroborating if the status agrees.
    return a.status is not None and a.status == b.status


def match_strength(new: ResultIdentity, old: ResultIdentity) -> str | None:
    """'strong', 'weak' (corroborated) or None for an existing in-scope row."""
    if not urls_compatible(new.source_url, old.source_url):
        return None
    name_eq = bool(new.name) and new.name == old.name
    sail_eq = bool(new.sail) and new.sail == old.sail
    if name_eq and sail_eq:
        return "strong"
    if not (name_eq or sail_eq):
        # Nothing published agrees. Neither identifier present on either side
        # is not enough to call it the same result.
        return None
    same_boat = new.boat_id is not None and new.boat_id == old.boat_id
    if _same_place(new, old) or same_boat:
        return "weak"
    sail_missing = not new.sail or not old.sail
    if name_eq and sail_missing and old.boat_id is None:
        # e.g. ISORA overall standing for a boat with no sail number that was
        # unmatched last time: place moved, boat newly matched.
        return "weak"
    return None


def pick_existing(new: ResultIdentity, candidates: list[ResultIdentity]) -> ResultIdentity | None:
    """Choose the existing row ``new`` should update, or None to insert.

    Strong matches win over weak ones; among equals the newest row (highest
    id) is chosen, which is the row a dedup cleanup keeps.
    """
    best: ResultIdentity | None = None
    best_rank = 0
    for cand in candidates:
        strength = match_strength(new, cand)
        if strength is None:
            continue
        rank = 2 if strength == "strong" else 1
        if rank > best_rank or (rank == best_rank and (cand.id or 0) > (best.id or 0)):
            best, best_rank = cand, rank
    return best


def raw_boat_name(raw: dict | None, fallback: str | None = None) -> str | None:
    raw = raw or {}
    return raw.get("boat_name") or fallback


def raw_sail_number(raw: dict | None, fallback: str | None = None) -> str | None:
    raw = raw or {}
    return raw.get("sail_number") or raw.get("sail_no") or fallback
