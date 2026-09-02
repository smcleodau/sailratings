"""Source change & breakage detection (DP-01-05 / SPEC-012 §6).

The monitor compares each source's current fetch against a stored
**baseline** fingerprint.  Five signals are tracked:

  * **fetch_success**   — did the HTTP request succeed at all?
  * **structure_signature** — SHA-256 of the HTML skeleton (tag names,
    table count, column counts, header text).  Ignores text content so
    that an ad-banner or copy swap is *not* a structure change.
  * **record_count**     — how many data rows the page contains.
  * **content_type**     — the HTTP ``Content-Type`` header.
  * **parser_yield**     — how many records the downstream parser
    actually extracted (may differ from ``record_count`` when the parser
    silently drops malformed rows).

A deviation is **material** when it threatens silent data loss:

  * fetch failure (new)
  * HTTP status error (4xx/5xx, new)
  * structure signature change
  * record-count collapse (≥ 50 % drop)
  * content-type change
  * parser-yield collapse (≥ 50 % drop)

A content hash change *alone* — with identical structure and record
counts — is **not** material.  That covers the harmless ad-banner /
copy-change scenario.

On a material deviation the monitor:

  1. quarantines the source's publication (so downstream consumers skip it)
  2. opens (or attaches to an existing) ``source_incident`` with
     representative artifacts — ``sample_records``, ``content_excerpt``,
     ``deviations``
  3. persists a :class:`SourceHealthEventV1` row

The module is DB-agnostic: it uses raw SQL via ``text()`` so the test
suite can run against in-memory SQLite as well as Postgres.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import JSON, bindparam, text
from sqlalchemy.engine import Engine


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"

# Thresholds ----------------------------------------------------------------
# A record-count or parser-yield drop >= this fraction of the baseline
# counts as a collapse (material).
COLLAPSE_RATIO = 0.50

# Status strings written into source_health_events.status
STATUS_CLEAN = "clean"
STATUS_CHANGED = "changed"
STATUS_MATERIAL = "material_deviation"

# Deviation identifiers (stored in the ``deviations`` JSON array)
DEV_FETCH_ERROR = "fetch_error"
DEV_HTTP_STATUS = "http_status"
DEV_CONTENT_TYPE = "content_type"
DEV_STRUCTURE = "structure_signature"
DEV_RECORD_COUNT = "record_count"
DEV_PARSER_YIELD = "parser_yield"

# Incident type (stored on source_incidents.incident_type)
INCIDENT_STRUCTURE = "structure_change"
INCIDENT_FETCH = "fetch_error"
INCIDENT_CONTENT_TYPE = "content_type_change"
INCIDENT_RECORD_COLLAPSE = "record_count_collapse"
INCIDENT_PARSER_COLLAPSE = "parser_yield_collapse"
INCIDENT_HASH_DELTA = "hash_delta"

# Environment variable that carries the health-check webhook URL
# (SPEC-012 §6.2: material deviations "alert via health-check webhook").
HEALTH_WEBHOOK_ENV = "SOURCE_MONITOR_WEBHOOK_URL"


# ---------------------------------------------------------------------------
# Fingerprint dataclass
# ---------------------------------------------------------------------------


@dataclass
class SourceFingerprint:
    """Structural + content fingerprint of a single source fetch."""

    fetch_success: bool = True
    http_status: int | None = None
    content_type: str | None = None
    content_hash: str = ""
    structure_signature: str = ""
    record_count: int = 0
    parser_yield: int = 0
    content_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# SourceHealthEventV1 — the handoff / output contract
# ---------------------------------------------------------------------------


@dataclass
class SourceHealthEventV1:
    """Normalised health-event contract consumed by downstream systems.

    Every ``check_source()`` call returns one of these (and persists it
    into ``source_health_events``).  The ``schema_version`` field is
    always ``"v1"`` so consumers can evolve the contract safely.
    """

    schema_version: str = SCHEMA_VERSION
    source_id: str = ""
    url: str = ""
    checked_at: str = ""
    status: str = STATUS_CLEAN  # clean | changed | material_deviation
    material: bool = False
    deviations: list[str] = field(default_factory=list)
    baseline: dict[str, Any] = field(default_factory=dict)
    current: dict[str, Any] = field(default_factory=dict)
    diff_ratio: float = 0.0
    sample_records: list[dict] | None = None
    content_excerpt: str | None = None
    incident_id: int | None = None
    quarantined: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Fingerprinting helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_structure_signature(html: str) -> str:
    """Return a SHA-256 of the page's *structural skeleton*.

    We strip all text and keep only the sequence of tag names plus
    table-level metadata (number of ``<table>`` elements, their column
    counts, and ``<th>`` header text).  This means that swapping an ad
    banner or rewriting body copy does **not** change the signature,
    but removing a table or renaming a column does.
    """
    if not html:
        return _sha256("")

    # Collect structural tokens.
    tokens: list[str] = []

    # Tag sequence — every opening/closing tag name in order.
    for m in _TAG_RE.finditer(html):
        tag = m.group()
        # Extract just the tag name (lowercased, no attributes).
        inner = tag.strip("<>").strip()
        if not inner:
            continue
        name = inner.split()[0].rstrip("/").lower()
        if name:
            tokens.append(name)

    # Table-level metadata.
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        for i, tbl in enumerate(tables):
            cols = len(tbl.find_all("tr")[0].find_all(["th", "td"])) if tbl.find_all("tr") else 0
            headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
            tokens.append(f"table{i}:cols={cols}:headers={'|'.join(headers)}")
    except Exception:
        # BeautifulSoup not available or parse error — fall back to
        # regex-based table counting.
        table_count = html.count("<table")
        tokens.append(f"table_count={table_count}")

    return _sha256("\n".join(tokens))


def compute_record_count(html: str) -> int:
    """Count data rows in the first HTML ``<table>`` on the page."""
    if not html:
        return 0
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            return 0
        # Count <tr> elements that contain at least one <td> (data row,
        # not a header-only row).
        rows = table.find_all("tr")
        count = 0
        for row in rows:
            if row.find("td"):
                count += 1
        return count
    except Exception:
        # Fallback: count <tr> tags.
        return html.count("<tr")


def fingerprint_source(
    *,
    content: str | bytes | None = None,
    fetch_success: bool = True,
    http_status: int | None = 200,
    content_type: str | None = "text/html",
    parser_yield: int | None = None,
) -> SourceFingerprint:
    """Build a :class:`SourceFingerprint` from a fetch result.

    ``content`` may be ``str`` or ``bytes``; ``None`` means the fetch
    failed (``fetch_success=False``).  When the fetch fails all derived
    fields are zeroed/empty.
    """
    if not fetch_success or content is None:
        return SourceFingerprint(
            fetch_success=False,
            http_status=http_status,
            content_type=content_type,
            content_hash="",
            structure_signature="",
            record_count=0,
            parser_yield=0,
            content_length=0,
        )

    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = content

    content_hash = _sha256(text)
    structure_sig = compute_structure_signature(text)
    record_count = compute_record_count(text)
    py = parser_yield if parser_yield is not None else record_count
    content_length = len(text.encode("utf-8"))

    return SourceFingerprint(
        fetch_success=True,
        http_status=http_status,
        content_type=content_type,
        content_hash=content_hash,
        structure_signature=structure_sig,
        record_count=record_count,
        parser_yield=py,
        content_length=content_length,
    )


def _content_excerpt(text: str, max_chars: int = 500) -> str:
    """Return a trimmed excerpt suitable for incident artifacts."""
    if not text:
        return ""
    # Strip excess whitespace but keep structure visible.
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:max_chars]


def _extract_sample_records(content: str, max_records: int = 5) -> list[dict]:
    """Extract up to ``max_records`` sample data rows from the page.

    Returns a list of dicts (column-name → cell-text) so they survive
    JSON serialisation into ``source_incidents.sample_records``.
    """
    if not content:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")
        table = soup.find("table")
        if not table:
            return []
        rows = table.find_all("tr")
        if not rows:
            return []

        # First row = headers.
        header_row = rows[0]
        headers = [
            (th.get_text(strip=True) or f"col_{i}")
            for i, th in enumerate(header_row.find_all(["th", "td"]))
        ]

        samples: list[dict] = []
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            record = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                record[key] = cell.get_text(strip=True)
            samples.append(record)
            if len(samples) >= max_records:
                break
        return samples
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Comparison & classification
# ---------------------------------------------------------------------------


def _collapse(current: int, baseline: int) -> bool:
    """True when ``current`` dropped by ≥ COLLAPSE_RATIO vs ``baseline``."""
    if baseline <= 0:
        return current <= 0 and baseline > 0
    ratio_dropped = 1.0 - (current / baseline)
    return ratio_dropped >= COLLAPSE_RATIO


def _compute_diff_ratio(baseline_text: str | None, current_text: str | None) -> float:
    """Estimate the fraction of content that changed.

    Uses a simple set-of-characters approach (Jaccard distance complement).
    Returns a value in ``[0, 1]`` where 0 = identical, 1 = completely
    different.
    """
    if not baseline_text and not current_text:
        return 0.0
    if not baseline_text or not current_text:
        return 1.0

    # Use bigram sets for a slightly more meaningful comparison than
    # raw character sets.
    def _bigrams(s: str) -> set[str]:
        s = s.lower()
        return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}

    a = _bigrams(baseline_text)
    b = _bigrams(current_text)
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    intersection = a & b
    return 1.0 - (len(intersection) / len(union))


def compare_and_classify(
    baseline: SourceFingerprint,
    current: SourceFingerprint,
    baseline_content: str | None = None,
    current_content: str | None = None,
) -> tuple[list[str], bool, float]:
    """Compare ``current`` against ``baseline`` and classify.

    Returns ``(deviations, material, diff_ratio)``.

    * ``deviations`` — list of deviation identifiers.
    * ``material`` — whether the change threatens silent data loss.
    * ``diff_ratio`` — estimated fraction of content that changed.
    """
    deviations: list[str] = []
    material = False

    # 1. Fetch error — always material.
    if not current.fetch_success and baseline.fetch_success:
        deviations.append(DEV_FETCH_ERROR)
        material = True

    # 2. HTTP status — material if the baseline was 2xx and now it's 4xx/5xx.
    if (
        baseline.http_status
        and 200 <= baseline.http_status < 300
        and current.http_status is not None
        and (current.http_status >= 400 or current.http_status < 200)
    ):
        deviations.append(DEV_HTTP_STATUS)
        material = True

    # 3. Content-type change — material (parser assumptions break).
    if (
        baseline.content_type
        and current.content_type
        and baseline.content_type != current.content_type
    ):
        deviations.append(DEV_CONTENT_TYPE)
        material = True

    # 4. Structure signature change — material (table/layout altered).
    if (
        baseline.structure_signature
        and current.structure_signature
        and baseline.structure_signature != current.structure_signature
    ):
        deviations.append(DEV_STRUCTURE)
        material = True

    # 5. Record-count collapse — material.
    if _collapse(current.record_count, baseline.record_count):
        deviations.append(DEV_RECORD_COUNT)
        material = True

    # 6. Parser-yield collapse — material.
    if _collapse(current.parser_yield, baseline.parser_yield):
        deviations.append(DEV_PARSER_YIELD)
        material = True

    # Diff ratio.
    diff_ratio = _compute_diff_ratio(baseline_content, current_content)

    # If the content hash changed but nothing else is material, it's a
    # harmless "changed" — not material.
    return deviations, material, diff_ratio


def _classify_incident_type(deviations: list[str]) -> str:
    """Map the first structural deviation to an incident type."""
    for dev in deviations:
        if dev == DEV_STRUCTURE:
            return INCIDENT_STRUCTURE
        if dev == DEV_FETCH_ERROR:
            return INCIDENT_FETCH
        if dev == DEV_HTTP_STATUS:
            return INCIDENT_FETCH
        if dev == DEV_CONTENT_TYPE:
            return INCIDENT_CONTENT_TYPE
        if dev == DEV_RECORD_COUNT:
            return INCIDENT_RECORD_COLLAPSE
        if dev == DEV_PARSER_YIELD:
            return INCIDENT_PARSER_COLLAPSE
    return INCIDENT_HASH_DELTA


# ---------------------------------------------------------------------------
# Health-check webhook alerting (SPEC-012 §6.2)
# ---------------------------------------------------------------------------

#: Injectable transport for tests.  Signature: ``(url, payload_dict) -> bool``.
#: Defaults to :func:`_post_webhook` (real HTTP via httpx).  Tests may
#: monkeypatch this to capture payloads without any network calls.
AlertTransport = Callable[[str, dict[str, Any]], bool]


def _post_webhook(url: str, payload: dict[str, Any]) -> bool:
    """POST ``payload`` to ``url``; return True on a 2xx response.

    All network/serialisation errors are swallowed — alerting must never
    crash the monitor.
    """
    try:  # pragma: no cover - thin httpx wrapper
        import httpx

        resp = httpx.post(url, json=payload, timeout=10)
        return resp.status_code < 300
    except Exception:
        return False


def send_source_alert(
    event: SourceHealthEventV1,
    webhook_url: str | None = None,
    *,
    transport: AlertTransport | None = None,
) -> bool:
    """Send a health-check webhook alert for a material source deviation.

    Builds a Discord/Slack-compatible payload describing the incident and
    posts it to ``webhook_url``.  When ``webhook_url`` is ``None`` the
    ``SOURCE_MONITOR_WEBHOOK_URL`` environment variable is consulted.

    Returns ``True`` when the alert was (attempted and) accepted by the
    transport, ``False`` when no webhook is configured or the transport
    failed.  Alerting is **best-effort** and never raises — a broken
    webhook must not break the monitor.
    """
    url = webhook_url or os.environ.get(HEALTH_WEBHOOK_ENV, "")
    if not url:
        return False

    post = transport or _post_webhook

    devs = ", ".join(event.deviations) if event.deviations else "unknown"
    title = f"Source incident: {event.source_id}"
    summary = (
        f"Material deviation on `{event.source_id}` "
        f"({event.url or 'no-url'})\n"
        f"deviations: {devs}\n"
        f"incident #{event.incident_id} — publication quarantined"
    )

    if "discord" in url.lower():
        embed = {
            "title": title,
            "color": 0xFF0000,
            "fields": [
                {"name": "Source", "value": str(event.source_id), "inline": True},
                {"name": "Status", "value": str(event.status), "inline": True},
                {"name": "Incident", "value": f"#{event.incident_id}", "inline": True},
                {"name": "Deviations", "value": devs, "inline": False},
            ],
            "timestamp": event.checked_at,
        }
        if event.url:
            embed["url"] = event.url
        payload: dict[str, Any] = {"embeds": [embed]}
    else:
        # Slack / generic incoming webhook.
        lines = [
            f"*:rotating_light: {title}*",
            f"*Source:* {event.source_id}   *Status:* {event.status}   "
            f"*Incident:* #{event.incident_id}",
            f"*Deviations:* {devs}",
        ]
        if event.url:
            lines.append(f"*URL:* {event.url}")
        payload = {"text": "\n".join(lines), "summary": summary}

    try:
        return bool(post(url, payload))
    except Exception:
        # Never let alerting break the monitor.
        return False


# ---------------------------------------------------------------------------
# DB schema mirror (for SQLite tests)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    fetch_success BOOLEAN DEFAULT 1,
    http_status INTEGER,
    content_type TEXT,
    content_hash TEXT,
    structure_signature TEXT,
    record_count INTEGER,
    parser_yield INTEGER,
    content_length INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, url)
);

CREATE TABLE IF NOT EXISTS source_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    url TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    material BOOLEAN DEFAULT 0,
    deviations TEXT,
    diff_ratio REAL,
    baseline_hash TEXT,
    current_hash TEXT,
    incident_id INTEGER,
    quarantined BOOLEAN DEFAULT 0,
    event_payload TEXT
);

CREATE TABLE IF NOT EXISTS source_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    url TEXT,
    incident_type TEXT NOT NULL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'open',
    deviations TEXT,
    sample_records TEXT,
    content_excerpt TEXT,
    previous_hash TEXT,
    current_hash TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS publication_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    incident_id INTEGER,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    released_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE(source_id)
);
"""


def init_monitor_tables(engine: Engine) -> None:
    """Create the source-monitor tables (idempotent).

    On Postgres this is normally handled by the Alembic migration
    (0023_source_monitor).  This helper exists so tests can set up an
    in-memory SQLite schema without Alembic.
    """
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


# ---------------------------------------------------------------------------
# Baseline management
# ---------------------------------------------------------------------------


def get_baseline(engine: Engine, source_id: str, url: str) -> dict[str, Any] | None:
    """Return the stored baseline for ``(source_id, url)`` or ``None``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT source_id, url, fetch_success, http_status, content_type, "
                "       content_hash, structure_signature, record_count, "
                "       parser_yield, content_length "
                "FROM source_baselines WHERE source_id = :sid AND url = :url"
            ),
            {"sid": source_id, "url": url},
        ).first()
    if row is None:
        return None
    return dict(row._mapping)


def list_baselines(engine: Engine) -> list[dict[str, Any]]:
    """Return all stored baselines."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_id, url, fetch_success, http_status, content_type, "
                "       content_hash, structure_signature, record_count, "
                "       parser_yield, content_length, updated_at "
                "FROM source_baselines ORDER BY source_id"
            )
        ).fetchall()
    return [dict(r._mapping) for r in rows]


def set_baseline(
    engine: Engine,
    source_id: str,
    url: str,
    fingerprint: SourceFingerprint,
) -> None:
    """Insert or replace the baseline for ``(source_id, url)``."""
    fp = fingerprint.to_dict()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM source_baselines WHERE source_id = :sid AND url = :url"),
            {"sid": source_id, "url": url},
        ).first()

        if existing:
            conn.execute(
                text(
                    "UPDATE source_baselines SET "
                    "  fetch_success = :fs, http_status = :hs, content_type = :ct, "
                    "  content_hash = :ch, structure_signature = :ss, "
                    "  record_count = :rc, parser_yield = :py, content_length = :cl, "
                    "  updated_at = CURRENT_TIMESTAMP "
                    "WHERE source_id = :sid AND url = :url"
                ),
                {
                    "sid": source_id, "url": url,
                    "fs": fp["fetch_success"], "hs": fp["http_status"],
                    "ct": fp["content_type"], "ch": fp["content_hash"],
                    "ss": fp["structure_signature"], "rc": fp["record_count"],
                    "py": fp["parser_yield"], "cl": fp["content_length"],
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO source_baselines "
                    "  (source_id, url, fetch_success, http_status, content_type, "
                    "   content_hash, structure_signature, record_count, "
                    "   parser_yield, content_length) "
                    "VALUES (:sid, :url, :fs, :hs, :ct, :ch, :ss, :rc, :py, :cl)"
                ),
                {
                    "sid": source_id, "url": url,
                    "fs": fp["fetch_success"], "hs": fp["http_status"],
                    "ct": fp["content_type"], "ch": fp["content_hash"],
                    "ss": fp["structure_signature"], "rc": fp["record_count"],
                    "py": fp["parser_yield"], "cl": fp["content_length"],
                },
            )


# ---------------------------------------------------------------------------
# Incident management
# ---------------------------------------------------------------------------


def _get_open_incident(
    engine: Engine, source_id: str
) -> dict[str, Any] | None:
    """Return the most recent open incident for ``source_id`` or ``None``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, source_id, url, incident_type, status, deviations, "
                "       sample_records, content_excerpt, previous_hash, current_hash "
                "FROM source_incidents "
                "WHERE source_id = :sid AND status = 'open' "
                "ORDER BY detected_at DESC LIMIT 1"
            ),
            {"sid": source_id},
        ).first()
    return dict(row._mapping) if row else None


def _create_incident(
    engine: Engine,
    source_id: str,
    url: str | None,
    incident_type: str,
    deviations: list[str],
    sample_records: list[dict] | None,
    content_excerpt: str | None,
    previous_hash: str | None,
    current_hash: str | None,
) -> int:
    """Insert a new source_incident and return its id."""
    dev_json = _json_dumps(deviations)
    samples_json = _json_dumps(sample_records) if sample_records else None

    stmt = text(
        "INSERT INTO source_incidents "
        "  (source_id, url, incident_type, deviations, sample_records, "
        "   content_excerpt, previous_hash, current_hash, status) "
        "VALUES (:sid, :url, :itype, :dev, :sr, :ce, :ph, :ch, 'open')"
    ).bindparams(
        bindparam("dev", type_=JSON),
        bindparam("sr", type_=JSON),
    )
    with engine.begin() as conn:
        result = conn.execute(
            stmt,
            {
                "sid": source_id, "url": url, "itype": incident_type,
                "dev": dev_json, "sr": samples_json,
                "ce": content_excerpt, "ph": previous_hash, "ch": current_hash,
            },
        )
        # SQLAlchemy 2.x: lastrowid works on SQLite; on Postgres use RETURNING.
        if hasattr(result, "lastrowid") and result.lastrowid:
            return result.lastrowid
        # Postgres fallback.
        row = conn.execute(
            text("SELECT lastval()")
        ).first()
        return row[0] if row else 0


def _attach_to_incident(
    engine: Engine,
    incident_id: int,
    deviations: list[str],
    sample_records: list[dict] | None,
    content_excerpt: str | None,
) -> None:
    """Update an existing open incident with new deviation info."""
    dev_json = _json_dumps(deviations)
    samples_json = _json_dumps(sample_records) if sample_records else None

    stmt = text(
        "UPDATE source_incidents SET "
        "  deviations = :dev, sample_records = :sr, content_excerpt = :ce "
        "WHERE id = :iid"
    ).bindparams(
        bindparam("dev", type_=JSON),
        bindparam("sr", type_=JSON),
    )
    with engine.begin() as conn:
        conn.execute(
            stmt,
            {"iid": incident_id, "dev": dev_json, "sr": samples_json, "ce": content_excerpt},
        )


def list_incidents(engine: Engine, source_id: str | None = None) -> list[dict[str, Any]]:
    """Return incidents, optionally filtered by ``source_id``."""
    if source_id:
        sql = (
            "SELECT id, source_id, url, incident_type, detected_at, "
            "       resolved_at, status, deviations, content_excerpt "
            "FROM source_incidents WHERE source_id = :sid "
            "ORDER BY detected_at DESC"
        )
        params: dict[str, Any] = {"sid": source_id}
    else:
        sql = (
            "SELECT id, source_id, url, incident_type, detected_at, "
            "       resolved_at, status, deviations, content_excerpt "
            "FROM source_incidents ORDER BY detected_at DESC"
        )
        params = {}
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Quarantine management
# ---------------------------------------------------------------------------


def _is_quarantined(engine: Engine, source_id: str) -> bool:
    """True when an active quarantine exists for ``source_id``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM publication_quarantine "
                "WHERE source_id = :sid AND status = 'active'"
            ),
            {"sid": source_id},
        ).first()
    return row is not None


def _quarantine_source(
    engine: Engine,
    source_id: str,
    incident_id: int,
    reason: str,
) -> None:
    """Create (or reactivate) a publication quarantine for ``source_id``."""
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM publication_quarantine WHERE source_id = :sid"),
            {"sid": source_id},
        ).first()
        if existing:
            conn.execute(
                text(
                    "UPDATE publication_quarantine SET "
                    "  incident_id = :iid, reason = :reason, "
                    "  status = 'active', released_at = NULL, "
                    "  created_at = CURRENT_TIMESTAMP "
                    "WHERE source_id = :sid"
                ),
                {"sid": source_id, "iid": incident_id, "reason": reason},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO publication_quarantine "
                    "  (source_id, incident_id, reason, status) "
                    "VALUES (:sid, :iid, :reason, 'active')"
                ),
                {"sid": source_id, "iid": incident_id, "reason": reason},
            )


def release_quarantine(
    engine: Engine,
    source_id: str | None = None,
) -> int:
    """Release active quarantines.

    If ``source_id`` is given, release only that source's quarantine.
    Otherwise release all active quarantines.

    Resolves the associated incident (sets ``status = 'resolved'`` and
    ``resolved_at = now``).

    Returns the number of quarantines released.
    """
    released = 0
    with engine.begin() as conn:
        if source_id:
            rows = conn.execute(
                text(
                    "SELECT id, incident_id FROM publication_quarantine "
                    "WHERE source_id = :sid AND status = 'active'"
                ),
                {"sid": source_id},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    "SELECT id, incident_id FROM publication_quarantine "
                    "WHERE status = 'active'"
                )
            ).fetchall()

        for row in rows:
            qid = row[0]
            incident_id = row[1] if len(row) > 1 else None
            conn.execute(
                text(
                    "UPDATE publication_quarantine SET "
                    "  status = 'released', released_at = CURRENT_TIMESTAMP "
                    "WHERE id = :qid"
                ),
                {"qid": qid},
            )
            if incident_id:
                conn.execute(
                    text(
                        "UPDATE source_incidents SET "
                        "  status = 'resolved', resolved_at = CURRENT_TIMESTAMP "
                        "WHERE id = :iid"
                    ),
                    {"iid": incident_id},
                )
            released += 1
    return released


# ---------------------------------------------------------------------------
# Health-event persistence
# ---------------------------------------------------------------------------


def _json_dumps(obj: Any) -> Any:
    """Return ``obj`` as-is; the JSON bind param handles serialisation."""
    return obj


def _persist_health_event(
    engine: Engine,
    event: SourceHealthEventV1,
) -> int:
    """Insert a row into ``source_health_events`` and return its id."""
    payload = event.to_dict()
    deviations = payload.get("deviations") or []
    sample_records = payload.get("sample_records")
    full_payload = {
        "baseline": payload.get("baseline"),
        "current": payload.get("current"),
        "sample_records": sample_records,
        "content_excerpt": payload.get("content_excerpt"),
    }

    stmt = text(
        "INSERT INTO source_health_events "
        "  (source_id, url, status, material, deviations, diff_ratio, "
        "   baseline_hash, current_hash, incident_id, quarantined, event_payload) "
        "VALUES (:sid, :url, :status, :material, :dev, :dr, :bh, :ch, :iid, :q, :ep)"
    ).bindparams(
        bindparam("dev", type_=JSON),
        bindparam("ep", type_=JSON),
    )
    with engine.begin() as conn:
        result = conn.execute(
            stmt,
            {
                "sid": event.source_id,
                "url": event.url,
                "status": event.status,
                "material": event.material,
                "dev": deviations,
                "dr": event.diff_ratio,
                "bh": event.baseline.get("content_hash"),
                "ch": event.current.get("content_hash"),
                "iid": event.incident_id,
                "q": event.quarantined,
                "ep": full_payload,
            },
        )
        if hasattr(result, "lastrowid") and result.lastrowid:
            return result.lastrowid
        row = conn.execute(text("SELECT lastval()")).first()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Public API: check_source
# ---------------------------------------------------------------------------


def check_source(
    engine: Engine,
    source_id: str,
    url: str,
    *,
    content: str | bytes | None = None,
    fetch_success: bool = True,
    http_status: int | None = 200,
    content_type: str | None = "text/html",
    parser_yield: int | None = None,
    baseline_content: str | None = None,
    alert_webhook_url: str | None = None,
    alert_transport: AlertTransport | None = None,
) -> SourceHealthEventV1:
    """Compare a source's current fetch against its stored baseline.

    Parameters
    ----------
    engine
        SQLAlchemy engine (Postgres or in-memory SQLite for tests).
    source_id
        Stable identifier for the source (e.g. ``"sailsys"``).
    url
        The canonical URL that was fetched.
    content
        The fetched page body (HTML / text).  ``None`` means the fetch
        failed.
    fetch_success, http_status, content_type, parser_yield
        Fetch metadata.
    baseline_content
        The *baseline* page body, if available, used to compute the diff
        ratio.  When not supplied the diff ratio is estimated from the
        fingerprints alone.
    alert_webhook_url
        Optional health-check webhook URL.  On a *material* deviation an
        alert is posted to this URL.  When ``None`` the
        ``SOURCE_MONITOR_WEBHOOK_URL`` environment variable is used; when
        neither is set no alert is sent.  Alerting is best-effort and
        never raises.
    alert_transport
        Injectable transport used for tests (no network calls).

    Returns
    -------
    SourceHealthEventV1
        The persisted health event.  On material deviation, the
        ``incident_id`` and ``quarantined`` fields are populated.
    """
    # Build the current fingerprint.
    current_fp = fingerprint_source(
        content=content,
        fetch_success=fetch_success,
        http_status=http_status,
        content_type=content_type,
        parser_yield=parser_yield,
    )

    # Convert content for excerpt / diff.
    if isinstance(content, bytes):
        current_text = content.decode("utf-8", errors="replace")
    else:
        current_text = content

    # Load the baseline.
    baseline_dict = get_baseline(engine, source_id, url)

    # First-run auto-baseline: no baseline yet → store and return clean.
    if baseline_dict is None:
        set_baseline(engine, source_id, url, current_fp)
        event = SourceHealthEventV1(
            source_id=source_id,
            url=url,
            checked_at=datetime.now(timezone.utc).isoformat(),
            status=STATUS_CLEAN,
            material=False,
            deviations=[],
            baseline=current_fp.to_dict(),
            current=current_fp.to_dict(),
            diff_ratio=0.0,
        )
        _persist_health_event(engine, event)
        return event

    baseline_fp = SourceFingerprint(
        fetch_success=baseline_dict.get("fetch_success") in (True, 1, "1", "true"),
        http_status=baseline_dict.get("http_status"),
        content_type=baseline_dict.get("content_type"),
        content_hash=baseline_dict.get("content_hash") or "",
        structure_signature=baseline_dict.get("structure_signature") or "",
        record_count=baseline_dict.get("record_count") or 0,
        parser_yield=baseline_dict.get("parser_yield") or 0,
        content_length=baseline_dict.get("content_length") or 0,
    )

    # Compare & classify.
    deviations, material, diff_ratio = compare_and_classify(
        baseline_fp,
        current_fp,
        baseline_content=baseline_content,
        current_content=current_text,
    )

    # When the caller didn't supply baseline_content for a diff-ratio
    # computation, derive it from the fingerprints: if the content hashes
    # match the content is identical (ratio 0); otherwise estimate from
    # the structural/record delta.
    if baseline_content is None:
        if (
            baseline_fp.content_hash
            and current_fp.content_hash
            and baseline_fp.content_hash == current_fp.content_hash
        ):
            diff_ratio = 0.0
        elif not current_fp.content_hash and not baseline_fp.content_hash:
            diff_ratio = 0.0

    # Determine status.
    if material:
        status = STATUS_MATERIAL
    elif current_fp.content_hash != baseline_fp.content_hash and current_fp.content_hash:
        status = STATUS_CHANGED
    else:
        status = STATUS_CLEAN

    event = SourceHealthEventV1(
        source_id=source_id,
        url=url,
        checked_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        material=material,
        deviations=deviations,
        baseline=baseline_fp.to_dict(),
        current=current_fp.to_dict(),
        diff_ratio=diff_ratio,
    )

    # On material deviation: quarantine + create/attach incident.
    if material:
        sample_records = _extract_sample_records(current_text or "") if current_text else None
        content_excerpt = _content_excerpt(current_text or "") if current_text else None
        incident_type = _classify_incident_type(deviations)

        # Attach to existing open incident if one exists (no duplicates).
        existing = _get_open_incident(engine, source_id)
        if existing:
            incident_id = existing["id"]
            _attach_to_incident(
                engine,
                incident_id,
                deviations,
                sample_records,
                content_excerpt,
            )
        else:
            incident_id = _create_incident(
                engine,
                source_id,
                url,
                incident_type,
                deviations,
                sample_records,
                content_excerpt,
                previous_hash=baseline_fp.content_hash or None,
                current_hash=current_fp.content_hash or None,
            )

        # Quarantine publication.
        _quarantine_source(engine, source_id, incident_id, incident_type)

        event.incident_id = incident_id
        event.quarantined = True
        event.sample_records = sample_records
        event.content_excerpt = content_excerpt

        # Alert via the health-check webhook (SPEC-012 §6.2).  Only a
        # *material* deviation alerts — harmless content changes do not.
        send_source_alert(
            event,
            webhook_url=alert_webhook_url,
            transport=alert_transport,
        )

    # Persist the health event.
    _persist_health_event(engine, event)

    return event


# ---------------------------------------------------------------------------
# Rebaseline helper
# ---------------------------------------------------------------------------


def rebaseline_source(
    engine: Engine,
    source_id: str,
    url: str,
    *,
    content: str | bytes | None = None,
    fetch_success: bool = True,
    http_status: int | None = 200,
    content_type: str | None = "text/html",
    parser_yield: int | None = None,
) -> SourceFingerprint:
    """Store a new baseline for ``(source_id, url)`` from fresh content.

    After fixing a source breakage, call this to establish the new
    known-good fingerprint so subsequent checks stop alerting.
    """
    fp = fingerprint_source(
        content=content,
        fetch_success=fetch_success,
        http_status=http_status,
        content_type=content_type,
        parser_yield=parser_yield,
    )
    set_baseline(engine, source_id, url, fp)
    return fp


# ---------------------------------------------------------------------------
# Utility queries
# ---------------------------------------------------------------------------


def is_source_quarantined(engine: Engine, source_id: str) -> bool:
    """Public wrapper for checking quarantine status."""
    return _is_quarantined(engine, source_id)


def get_recent_health_events(
    engine: Engine, source_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the most recent health events for a source."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, source_id, url, checked_at, status, material, "
                "       deviations, diff_ratio, incident_id, quarantined "
                "FROM source_health_events WHERE source_id = :sid "
                "ORDER BY checked_at DESC LIMIT :lim"
            ),
            {"sid": source_id, "lim": limit},
        ).fetchall()
    return [dict(r._mapping) for r in rows]
