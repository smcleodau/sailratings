"""Admin chat endpoint — natural language data investigation and management.

Designed for non-technical users (like Justin) to investigate data quality
issues and propose changes. The LLM can READ the database freely but
proposes changes for confirmation before executing.
"""

import datetime as _dt
import decimal as _decimal
import json
import os
import uuid as _uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of a DB value to a JSON-serialisable form for the UI."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)

router = APIRouter(prefix="/admin", tags=["Admin"])

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

ADMIN_SYSTEM_PROMPT = """You are the data quality assistant for SailRatings.com — a platform that
tracks how IRC and ORC yacht racing handicap ratings evolve over time.

You're talking to Justin, a non-technical co-founder who knows sailing inside
out but doesn't write code. He uses you to investigate data quality issues,
look up boats, check race results, and fix problems in the database. Stuart
(the technical co-founder) reviews these conversations later.

YOU HAVE:
- READ access: run SELECT queries freely to investigate
- PROPOSE access: suggest UPDATE/INSERT changes for Justin to confirm
- You CANNOT directly modify data — propose changes and Justin clicks Confirm

────────────────────────────────────────────────────────────────
DATABASE SCHEMA
────────────────────────────────────────────────────────────────

boats (~9,400 rows)
  id, boat_name, sail_number, cert_number, design, design_canonical,
  country (3-letter: AUS/GBR/IRL/NZL/USA/etc), year_built,
  hull_id, builder, designer, loa, lwl, beam_max, displacement_kg

boat_identities — historical name/sail/owner observations
  id, boat_id, boat_name, sail_number, owner, flag, source, observed_date

tcc_snapshots (~24,700 rows) — IRC rating snapshots over time
  boat_id, snapshot_date, tcc, non_spi_tcc, lh, beam, draft,
  headsails, spinnakers, crew, dlr, ...

irc_certificates (~3,800 rows) — parsed IRC certificate PDFs
  boat_id, cert_number, lh, beam, draft, displacement_kg,
  p, e, j, hlu, hlp, ...  (rig measurements)

orc_certificates (~14,000 rows) — ORC certificate data
  boat_id, class_name, gph, cdl, triple_low/med/high,
  allowances (VPP polars, jsonb), tmf_offshore, tmf_inshore,
  mb, dspl_sailing, wss, dynamic_allowance, ...

race_results (267,269 rows) — scraped race results
  boat_id (NULL if unmatched), event_name, event_date, race_name,
  place, fleet_size, division, class_name, rating_type, rating_value,
  tcc_at_race, elapsed_time, corrected_time, status, source,
  organizing_club, event_type, raw_data (JSONB)

design_classes (1,980 rows) — canonical design names + aliases
  name_canonical, aliases, ...

orders — report purchases
  order_token, boat_id, status, email, amount_cents, currency

────────────────────────────────────────────────────────────────
WHAT JUSTIN TYPICALLY ASKS YOU TO DO
────────────────────────────────────────────────────────────────

LOOK UP BOATS:
- "Find Sun Fish" → search boats by name (use ILIKE for fuzzy matching)
- "What do we have on Ichi Ban?" → show boat details, TCC history, race results
- "Show me all the J/111s" → query by design/design_canonical
- When showing boats, include: name, sail number, design, country, latest TCC

CHECK DATA QUALITY:
- "Are there twilight results polluting the data?" → check event_name/race_name
- "How many unmatched results?" → race_results WHERE boat_id IS NULL
- "Show duplicate boats" → same hull appearing under different names
- "Which race results look wrong?" → suspicious places, missing data

FIX DATA:
- Wrong design class → UPDATE boats SET design_canonical = 'correct' WHERE ...
- Merge duplicate boats → UPDATE race_results SET boat_id = correct_id WHERE boat_id = wrong_id
- Set country → UPDATE boats SET country = 'AUS' WHERE id = ...
- Flag bad results → UPDATE race_results SET status = 'excluded' WHERE ...

EXPLORE:
- "How many boats by country?" → GROUP BY queries
- "What clubs have the most results?" → aggregate race_results
- "Show me the Sunfast 3300 fleet" → boats + their rating spread

────────────────────────────────────────────────────────────────
IMPORTANT RULES
────────────────────────────────────────────────────────────────

DATA INTEGRITY:
- NEVER propose DELETE statements — we preserve all raw data
- Changes are UPDATE/INSERT only (set flags, canonical fields, reassign boat_ids)
- Always show what you're about to change and how many rows are affected
- If uncertain, query first to show Justin what would be affected

SAILING LANGUAGE:
- Justin is a sailor. Use proper terms: "TCC", "rating", "headsails",
  "spinnaker", "non-spi", "sail wardrobe", "declared headsails"
- Never say "penalty" — say "costs rating" or "rating impact"
- Never rank by raw TCC position — a 1.025 and a 0.900 are different universes
- When discussing a boat's rating, compare within its design class or rating band
- Country codes: AUS = Australia, GBR = Great Britain, NZL = New Zealand, etc.

COMMUNICATION:
- Be concise and direct — Justin wants answers, not essays
- Show actual data: names, numbers, sail numbers
- When you run a query, explain what you found in plain English
- If something looks wrong in the data, flag it proactively
- If you're not sure what Justin means, ask — don't guess

SEARCHING FOR BOATS:
- Try multiple approaches: boat_name ILIKE, sail_number, design
- Boat names can have spaces, hyphens, apostrophes — use ILIKE '%%name%%'
- Some boats have changed names — check boat_identities too
- sail_number format varies: 'AUS 1234', '1234', 'GBR1234'"""


class AdminChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None


class AdminQueryRequest(BaseModel):
    sql: str
    conversation_id: int | None = None
    change_id: int | None = None


class ConversationSummary(BaseModel):
    id: int
    title: str | None
    created_at: str
    message_count: int


class MessageOut(BaseModel):
    id: int
    role: str
    content: str | None
    queries: list | None
    proposed_changes: list | None
    created_at: str


# ── Helpers ──────────────────────────────────────────────────────────────


def _verify_admin(authorization: str | None) -> None:
    expected = f"Bearer {ADMIN_PASSWORD}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _create_conversation(engine: Engine, title: str) -> int:
    """Create a new conversation and return its ID."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO admin_conversations (title) VALUES (:title) RETURNING id"
            ),
            {"title": title[:80] if title else "Untitled"},
        ).first()
    return row.id


def _save_message(
    engine: Engine,
    conversation_id: int,
    role: str,
    content: str | None = None,
    queries: list | None = None,
    proposed_changes: list | None = None,
) -> int:
    """Save a message and return its ID."""
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO admin_messages (conversation_id, role, content, queries, proposed_changes)
                VALUES (:cid, :role, :content, :queries, :changes)
                RETURNING id
            """),
            {
                "cid": conversation_id,
                "role": role,
                "content": content,
                "queries": json.dumps(queries) if queries else None,
                "changes": json.dumps(proposed_changes) if proposed_changes else None,
            },
        ).first()
        # Update conversation timestamp
        conn.execute(
            text("UPDATE admin_conversations SET updated_at = now() WHERE id = :id"),
            {"id": conversation_id},
        )
    return row.id


def _load_conversation_messages(engine: Engine, conversation_id: int) -> list[dict]:
    """Load prior messages for Claude context."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT role, content, queries, proposed_changes
                FROM admin_messages
                WHERE conversation_id = :cid
                ORDER BY id
            """),
            {"cid": conversation_id},
        ).fetchall()

    messages = []
    for row in rows:
        if row.role == "user":
            messages.append({"role": "user", "content": row.content or ""})
        else:
            # Reconstruct assistant message summary
            parts = []
            if row.queries:
                queries = row.queries if isinstance(row.queries, list) else json.loads(row.queries)
                for q in queries:
                    parts.append(f"[Ran query: {q.get('explanation', q.get('sql', ''))}]")
            if row.content:
                parts.append(row.content)
            if row.proposed_changes:
                changes = row.proposed_changes if isinstance(row.proposed_changes, list) else json.loads(row.proposed_changes)
                for c in changes:
                    status = c.get("status", "pending")
                    parts.append(f"[Proposed change ({status}): {c.get('explanation', '')}]")
            messages.append({"role": "assistant", "content": "\n".join(parts) or "(no response)"})
    return messages


# ── Streaming ────────────────────────────────────────────────────────────


async def _admin_stream(engine: Engine, message: str, conversation_id: int | None = None):
    """Stream admin chat response, executing read queries inline."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        yield f"data: {json.dumps({'type': 'error', 'data': 'GEMINI_API_KEY not configured'})}\n\n"
        return

    # Create or reuse conversation
    if conversation_id is None:
        conversation_id = _create_conversation(engine, message)

    # Emit conversation_id as meta event
    yield f"data: {json.dumps({'type': 'meta', 'data': {'conversation_id': conversation_id}})}\n\n"

    # Save user message
    _save_message(engine, conversation_id, "user", content=message)

    # Build context: run the user's question with database access
    with engine.connect() as conn:
        stats = {}
        stats["total_results"] = conn.execute(text("SELECT COUNT(*) FROM race_results")).scalar()
        stats["matched_results"] = conn.execute(text("SELECT COUNT(*) FROM race_results WHERE boat_id IS NOT NULL")).scalar()
        stats["boats"] = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar()
        stats["twilight_excluded"] = conn.execute(text(
            "SELECT COUNT(*) FROM race_results WHERE LOWER(COALESCE(event_name,'')) LIKE '%%twilight%%'"
        )).scalar()

    context = f"""Database stats:
- {stats['boats']:,} boats
- {stats['total_results']:,} race results ({stats['matched_results']:,} matched to boats)
- {stats['twilight_excluded']:,} twilight results (excluded from analytics)

User message: {message}"""

    from irc_data.api.services.analytics_service import get_gemini_client
    client = get_gemini_client(api_key)

    # Use tools to let the Gemini model run SELECT queries and propose changes
    tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="run_query",
                    description="Run a read-only SQL SELECT query against the database. Only SELECT statements are allowed.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "sql": types.Schema(
                                type="STRING",
                                description="A SELECT SQL query to run against the PostgreSQL database",
                            ),
                            "explanation": types.Schema(
                                type="STRING",
                                description="Brief explanation of what this query investigates",
                            ),
                        },
                        required=["sql", "explanation"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="propose_change",
                    description="Propose a data change for the user to confirm. This does NOT execute the change — it presents it for approval.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "sql": types.Schema(
                                type="STRING",
                                description="The UPDATE/INSERT SQL statement to propose",
                            ),
                            "explanation": types.Schema(
                                type="STRING",
                                description="Plain English explanation of what this change does and why",
                            ),
                            "affected_rows_estimate": types.Schema(
                                type="STRING",
                                description="Estimated number of rows affected",
                            ),
                        },
                        required=["sql", "explanation"],
                    ),
                ),
            ]
        )
    ]

    # Load prior conversation context
    prior_messages = _load_conversation_messages(engine, conversation_id)
    # Use prior messages (excluding the one we just saved) plus the new context
    # Remove the last user message since we're about to add it as context
    if prior_messages and prior_messages[-1]["role"] == "user":
        prior_messages = prior_messages[:-1]

    messages = prior_messages + [{"role": "user", "content": context}]

    # Convert messages to Gemini's types.Content objects
    gemini_messages = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        gemini_messages.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            )
        )

    # Collect assistant response for persistence
    collected_text = []
    collected_queries = []
    collected_changes = []

    try:
        # Agentic loop — let Gemini run queries and build up understanding
        max_turns = 8
        for turn in range(max_turns):
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    system_instruction=ADMIN_SYSTEM_PROMPT,
                    tools=tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    max_output_tokens=2000,
                )
            )

            # Process response
            if response.text:
                yield f"data: {json.dumps({'type': 'text', 'data': response.text})}\n\n"
                collected_text.append(response.text)

            has_tool_use = False
            function_calls = response.function_calls

            if function_calls:
                has_tool_use = True
                # Append model's response (containing function call parts) to history
                gemini_messages.append(response.candidates[0].content)

                response_parts = []
                for call in function_calls:
                    tool_name = call.name
                    tool_input = call.args

                    if tool_name == "run_query":
                        sql = tool_input.get("sql", "")
                        explanation = tool_input.get("explanation", "")

                        # Safety: only allow SELECT
                        sql_upper = sql.strip().upper()
                        columns: list[str] = []
                        display_rows: list[list] = []
                        total_rows = 0
                        error: str | None = None

                        if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
                            error = "Only SELECT queries are allowed."
                            result_text = f"Error: {error}"
                        else:
                            try:
                                with engine.connect() as conn:
                                    rows = conn.execute(text(sql)).fetchall()
                                if rows:
                                    columns = list(rows[0]._mapping.keys())
                                    total_rows = len(rows)
                                    for r in rows[:50]:
                                        display_rows.append([_jsonable(v) for v in r])
                                    result_lines = [" | ".join(columns)]
                                    for r in display_rows:
                                        result_lines.append(" | ".join("" if v is None else str(v) for v in r))
                                    result_text = f"({total_rows} rows)\n" + "\n".join(result_lines)
                                else:
                                    result_text = "(0 rows)"
                            except Exception as e:
                                error = str(e)
                                result_text = f"Query error: {e}"

                        query_event = {
                            "sql": sql,
                            "explanation": explanation,
                            "columns": columns,
                            "rows": display_rows,
                            "total_rows": total_rows,
                            "truncated": total_rows > len(display_rows),
                        }
                        if error:
                            query_event["error"] = error
                        yield f"data: {json.dumps({'type': 'query', 'data': query_event})}\n\n"

                        collected_queries.append(query_event)

                        response_parts.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": result_text}
                            )
                        )

                    elif tool_name == "propose_change":
                        yield f"data: {json.dumps({'type': 'proposed_change', 'data': tool_input})}\n\n"

                        collected_changes.append({
                            **tool_input,
                            "status": "pending",
                        })

                        response_parts.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": "Change proposed to user. Waiting for confirmation."}
                            )
                        )

                # Append user function responses to history
                gemini_messages.append(
                    types.Content(
                        role="user",
                        parts=response_parts
                    )
                )

            if not has_tool_use:
                break

        # Save assistant response
        _save_message(
            engine,
            conversation_id,
            "assistant",
            content="".join(collected_text) if collected_text else None,
            queries=collected_queries if collected_queries else None,
            proposed_changes=collected_changes if collected_changes else None,
        )

        yield f"data: {json.dumps({'type': 'done', 'data': {}})}\n\n"

    except Exception as e:
        # Still try to save what we have
        if collected_text or collected_queries or collected_changes:
            try:
                _save_message(
                    engine,
                    conversation_id,
                    "assistant",
                    content="".join(collected_text) + f"\n\n[Error: {e}]",
                    queries=collected_queries if collected_queries else None,
                    proposed_changes=collected_changes if collected_changes else None,
                )
            except Exception:
                pass
        yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"


# ── Endpoints ────────────────────────────────────────────────────────────


def _parse_dt(value: Any) -> _dt.datetime | None:
    """Coerce a ledger timestamp to an aware UTC datetime.

    Postgres returns aware ``datetime`` objects; SQLite (tests, the E2E
    fixture) returns ISO strings. The freshness arithmetic below needs one
    consistent shape.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


@router.get("/scrapers")
async def list_scrapers(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Summary of every scraper source (AD-01-06). Reports TWO freshness
    signals:

    - `run_state` — has the scraper completed a run within its run_within
      budget? This is cron health. Stale = "the scraper isn't running."
    - `data_state` — have new race rows landed within its data_within
      budget? This is upstream health. Stale = "the tap is dry beyond what
      we'd expect from a seasonal lull."

    The overall `state` is the worst of the two.

    Ledger reads go through the OPS-01-03 run ledger (dialect-portable
    CASE WHEN aggregation, cutoffs bound as parameters) so the endpoint
    behaves identically on Postgres (production) and SQLite (tests / the
    E2E fixture rig). The data-tap signal keeps reading ``race_results``
    directly; when that table is absent (minimal fixture schemas) the
    signal degrades to "no rows on file" instead of taking the dashboard
    down.
    """
    _verify_admin(authorization)

    from irc_data.db.run_ledger import get_source_health_summary
    from irc_data.scrape_supervision import SOURCES

    now = _dt.datetime.now(_dt.timezone.utc)

    summary_rows = get_source_health_summary(engine, now=now)
    by_src = {r["source"]: r for r in summary_rows}

    # Last actually-imported race row per source. The "data tap" signal.
    by_src_data: dict[str, Any] = {}
    try:
        with engine.connect() as conn:
            data_rows = conn.execute(text("""
                SELECT source, MAX(created_at) AS last_new_data, MAX(event_date) AS latest_event_date
                FROM race_results
                GROUP BY source
            """)).fetchall()
        by_src_data = {r.source: r for r in data_rows}
    except Exception:
        # race_results absent (tests / minimal fixtures) — the data-tap
        # signal degrades to "no rows on file" rather than 500ing the page.
        by_src_data = {}

    def _state(age: _dt.timedelta | None, budget: _dt.timedelta | None) -> str:
        if budget is None:
            return "n/a"
        if age is None:
            return "never"
        return "fresh" if age <= budget else "stale"

    def _overall(run_s: str, data_s: str, optional: bool) -> str:
        if optional:
            return "optional"
        # Worst signal wins. "never" is worse than "stale".
        order = ["never", "stale", "fresh", "n/a", "optional"]
        return min((run_s, data_s), key=lambda s: order.index(s) if s in order else 5)

    out = []
    for cfg in SOURCES:
        r = by_src.get(cfg.source)
        dr = by_src_data.get(cfg.source)
        last_started = _parse_dt(r["last_started_at"]) if r else None
        last_success = _parse_dt(r["last_completed_at"]) if r else None
        last_new_data = _parse_dt(dr.last_new_data) if dr else None
        latest_event_date = dr.latest_event_date if dr else None
        run_age = (now - last_success) if last_success else None
        data_age = (now - last_new_data) if last_new_data else None

        run_state = _state(run_age, cfg.run_within)
        data_state = _state(data_age, cfg.data_within) if cfg.data_within else "n/a"
        state = _overall(run_state, data_state, cfg.optional)

        out.append({
            "source": cfg.source,
            "label": cfg.label,
            "cadence": cfg.cadence_human,
            "run_within_hours": cfg.run_within.total_seconds() / 3600.0,
            "data_within_hours": (cfg.data_within.total_seconds() / 3600.0) if cfg.data_within else None,
            "last_started": last_started.isoformat() if last_started else None,
            "last_success": last_success.isoformat() if last_success else None,
            "last_new_data": last_new_data.isoformat() if last_new_data else None,
            "latest_event_date": (
                latest_event_date.isoformat()
                if isinstance(latest_event_date, (_dt.datetime, _dt.date))
                else (str(latest_event_date) if latest_event_date else None)
            ),
            "run_age_seconds": int(run_age.total_seconds()) if run_age else None,
            "data_age_seconds": int(data_age.total_seconds()) if data_age else None,
            "run_state": run_state,
            "data_state": data_state,
            "state": state,
            "runs_7d": int(r["runs_7d"]) if r else 0,
            "failed_7d": int(r["failed_7d"]) if r else 0,
            "new_records_7d": int(r["rows_new_7d"] or 0) if r else 0,
            "optional": cfg.optional,
        })

    # Any sources that exist in ingestion_log but NOT in our config — surface
    # so we don't quietly miss something the scraper authors added.
    known = {s.source for s in SOURCES}
    for src, r in by_src.items():
        if src not in known:
            last_started = _parse_dt(r["last_started_at"])
            last_success = _parse_dt(r["last_completed_at"])
            run_age = (now - last_success) if last_success else None
            out.append({
                "source": src,
                "label": src + " (uncatalogued)",
                "cadence": "unknown",
                "run_within_hours": None,
                "data_within_hours": None,
                "last_started": last_started.isoformat() if last_started else None,
                "last_success": last_success.isoformat() if last_success else None,
                "last_new_data": None,
                "latest_event_date": None,
                "run_age_seconds": int(run_age.total_seconds()) if run_age else None,
                "data_age_seconds": None,
                "run_state": "n/a",
                "data_state": "n/a",
                "state": "uncatalogued",
                "runs_7d": int(r["runs_7d"]),
                "failed_7d": int(r["failed_7d"]),
                "new_records_7d": int(r["rows_new_7d"] or 0),
                "optional": False,
            })

    # Watchdog alert state (OPS-01-04): active alerts mirror what the
    # 15-minute watchdog has emailed about; history is the retained log.
    from irc_data.scrape_watchdog import (
        ensure_watchdog_table,
        get_active_alerts,
        get_alert_history,
    )

    alerts_active: list[dict] = []
    alerts_history: list[dict] = []
    try:
        with engine.begin() as conn:
            ensure_watchdog_table(conn)
            alerts_active = [
                {k: _jsonable(v) for k, v in a.items()}
                for a in get_active_alerts(conn)
            ]
            alerts_history = [
                {k: _jsonable(v) for k, v in a.items()}
                for a in get_alert_history(conn, limit=50)
            ]
    except Exception:
        # Alert log must never take the dashboard down with it.
        alerts_active, alerts_history = [], []

    return {
        "as_of": now.isoformat(),
        "sources": out,
        "alerts_active": alerts_active,
        "alerts_history": alerts_history,
    }


@router.get("/scrapers/{source}/runs")
async def scraper_runs(
    source: str,
    limit: int = 30,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Recent runs for a single source — drawer detail behind the dashboard row."""
    _verify_admin(authorization)
    limit = max(1, min(limit, 200))

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, started_at, completed_at, status,
                       records_found, records_new, records_updated,
                       error_message, metadata
                FROM ingestion_log
                WHERE source = :source
                ORDER BY started_at DESC
                LIMIT :limit
            """),
            {"source": source, "limit": limit},
        ).fetchall()

    runs = []
    for r in rows:
        started_at = _parse_dt(r.started_at)
        completed_at = _parse_dt(r.completed_at)
        runs.append({
            "id": r.id,
            "started_at": started_at.isoformat() if started_at else None,
            "completed_at": completed_at.isoformat() if completed_at else None,
            "duration_seconds": (
                (completed_at - started_at).total_seconds()
                if completed_at and started_at else None
            ),
            "status": r.status,
            "records_found": r.records_found,
            "records_new": r.records_new,
            "records_updated": r.records_updated,
            "error_message": r.error_message,
            "metadata": r.metadata,
        })

    return {"source": source, "runs": runs}


@router.get("/discovery")
async def list_discovery(
    status: str | None = None,
    platform: str | None = None,
    limit: int = 100,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """List event_discovery rows, most recent first.

    Query params:
    - status:   pending | confirmed | rejected | ingested | failed
    - platform: sailsys | topyacht | sailwave | yachtscoring | pdf | unknown
    """
    _verify_admin(authorization)
    limit = max(1, min(limit, 500))

    clauses = []
    params: dict = {"limit": limit}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if platform:
        clauses.append("scoring_platform = :platform")
        params["platform"] = platform
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, discovered_at, source_url, source_type, seed_url,
                   scoring_platform, platform_ids, title, event_date,
                   event_location, confidence, status, error_message,
                   confirmed_at, ingested_at, notes
            FROM event_discovery
            {where}
            ORDER BY discovered_at DESC
            LIMIT :limit
        """), params).fetchall()

    out = []
    for r in rows:
        d = dict(r._mapping)
        for k, v in list(d.items()):
            d[k] = _jsonable(v)
        out.append(d)
    return {"discoveries": out}


@router.post("/discovery/seed")
async def queue_discovery_seed(
    body: dict,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Kick off a discovery crawl from a seed URL. Returns the new rows.

    Body: {url: str, limit?: int (default 20)}

    Synchronous for now (small batches feel snappy enough). If we start
    seeding 100+ URLs at once, push this to a background task.
    """
    _verify_admin(authorization)
    from irc_data.discovery.service import discover_seed, discover_url

    url = (body or {}).get("url")
    if not url:
        raise HTTPException(status_code=400, detail="missing 'url'")
    limit = int((body or {}).get("limit") or 20)
    single = bool((body or {}).get("single"))

    try:
        if single:
            rows = [discover_url(engine, url)]
        else:
            rows = discover_seed(engine, url, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"discovery failed: {e}")

    return {"processed": len(rows), "rows": [_jsonable(r) for r in rows]}


@router.post("/discovery/{discovery_id}/confirm")
async def confirm_discovery(
    discovery_id: int,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Confirm + ingest. Returns the ingestion result."""
    _verify_admin(authorization)
    from irc_data.discovery.service import ingest_confirmed

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE event_discovery
            SET status = 'confirmed',
                confirmed_at = now(),
                confirmed_by = 'admin'
            WHERE id = :id AND status IN ('pending','failed')
        """), {"id": discovery_id})

    try:
        result = ingest_confirmed(engine, discovery_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.post("/discovery/{discovery_id}/reject")
async def reject_discovery(
    discovery_id: int,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Mark a discovery as rejected — won't show in the pending list."""
    _verify_admin(authorization)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE event_discovery SET status='rejected' WHERE id=:id"),
            {"id": discovery_id},
        )
    return {"status": "rejected"}


@router.get("/firecrawl/summary")
async def firecrawl_summary(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Aggregate Firecrawl usage — today / 7-day / 30-day, plus the
    authoritative remaining-credits number off the Firecrawl account.

    Built for the /justin/firecrawl dashboard. Two cost-control signals:
    - `remaining_credits` is the truth — what Firecrawl will bill against.
    - The per-window aggregates are *ours*, computed from the calls log,
      and double as a cross-check that our logging is sane.
    """
    _verify_admin(authorization)

    with engine.connect() as conn:
        # Aggregate over three windows in one query.
        rows = conn.execute(text("""
            WITH windows AS (
                SELECT 'today'::text  AS window, now() - interval '24 hours' AS since UNION ALL
                SELECT '7d',     now() - interval '7 days'  UNION ALL
                SELECT '30d',    now() - interval '30 days'
            )
            SELECT w.window,
                   COUNT(c.id)::int                                  AS calls,
                   COALESCE(SUM(c.credits), 0)::int                  AS credits,
                   COUNT(*) FILTER (WHERE c.status = 'ok')::int      AS ok,
                   COUNT(*) FILTER (WHERE c.status = 'empty')::int   AS empty,
                   COUNT(*) FILTER (WHERE c.status = 'error')::int   AS errored,
                   COUNT(*) FILTER (WHERE c.mode   = 'scrape')::int  AS scrapes,
                   COUNT(*) FILTER (WHERE c.mode   = 'map')::int     AS maps,
                   COALESCE(AVG(c.duration_ms), 0)::int              AS avg_ms,
                   COUNT(DISTINCT c.domain)::int                     AS domains
            FROM windows w
            LEFT JOIN firecrawl_calls c ON c.called_at >= w.since
            GROUP BY w.window
        """)).fetchall()

    by_window = {r.window: dict(r._mapping) for r in rows}

    # Remaining credits from Firecrawl directly (authoritative source).
    from irc_data.discovery.firecrawl_client import get_credit_usage
    remaining = get_credit_usage()  # may be None if API down or key missing

    # OPS-02-06 daily hard-stop state, surfaced on the admin Firecrawl page
    # (AD-01-08): how many credits have been spent since UTC midnight vs the
    # configured daily cap, and whether the cap is currently refusing calls.
    from irc_data.discovery import crawl_telemetry
    try:
        daily = {
            "daily_credit_cap": (s := crawl_telemetry._get_settings(engine, crawl_telemetry.DEFAULT_PROVIDER)).get("daily_credit_cap"),
            "used_today": crawl_telemetry._day_spend(engine, crawl_telemetry.DEFAULT_PROVIDER),
        }
        daily["daily_capped"] = bool(
            daily["daily_credit_cap"]
            and daily["daily_credit_cap"] > 0
            and daily["used_today"] >= daily["daily_credit_cap"]
        )
    except Exception:  # noqa: BLE001 — dashboard must never 500 on budget state
        daily = {"daily_credit_cap": None, "used_today": None, "daily_capped": False}

    return {
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "remaining": remaining,            # {"remaining_credits": …, "plan_credits": …} | null
        "windows": by_window,              # {"today": {...}, "7d": {...}, "30d": {...}}
        "daily": daily,                    # OPS-02-06 daily hard-stop state
    }


@router.get("/firecrawl/recent")
async def firecrawl_recent(
    limit: int = 100,
    status: str | None = None,
    mode: str | None = None,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Last N Firecrawl calls. Default 100, max 500.

    Optional filters: status=ok|empty|error, mode=scrape|map.
    """
    _verify_admin(authorization)
    limit = max(1, min(limit, 500))

    clauses: list[str] = []
    params: dict = {"limit": limit}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if mode:
        clauses.append("mode = :mode")
        params["mode"] = mode
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, called_at, mode, url, domain, status, credits,
                   duration_ms, response_chars, links_found, error_message, caller
            FROM firecrawl_calls
            {where}
            ORDER BY called_at DESC
            LIMIT :limit
        """), params).fetchall()

    return {
        "calls": [
            {
                "id": r.id,
                "called_at": r.called_at.isoformat() if r.called_at else None,
                "mode": r.mode,
                "url": r.url,
                "domain": r.domain,
                "status": r.status,
                "credits": r.credits,
                "duration_ms": r.duration_ms,
                "response_chars": r.response_chars,
                "links_found": r.links_found,
                "error_message": r.error_message,
                "caller": r.caller,
            }
            for r in rows
        ],
    }


@router.get("/firecrawl/by-domain")
async def firecrawl_by_domain(
    days: int = 7,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Per-domain rollup over the last N days (default 7, max 90).

    Sorted by call count, descending. Shows success rate so problem
    domains (anti-bot blocks, dead URLs) bubble to the top.
    """
    _verify_admin(authorization)
    days = max(1, min(days, 90))

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT domain,
                   COUNT(*)::int                                       AS calls,
                   COALESCE(SUM(credits), 0)::int                      AS credits,
                   COUNT(*) FILTER (WHERE status = 'ok')::int          AS ok,
                   COUNT(*) FILTER (WHERE status = 'empty')::int       AS empty,
                   COUNT(*) FILTER (WHERE status = 'error')::int       AS errored,
                   COALESCE(AVG(duration_ms), 0)::int                  AS avg_ms,
                   MAX(called_at)                                      AS last_called
            FROM firecrawl_calls
            WHERE called_at > now() - make_interval(days => :days)
            GROUP BY domain
            ORDER BY calls DESC, domain
            LIMIT 100
        """), {"days": days}).fetchall()

    return {
        "days": days,
        "domains": [
            {
                "domain": r.domain or "(unknown)",
                "calls": r.calls,
                "credits": r.credits,
                "ok": r.ok,
                "empty": r.empty,
                "errored": r.errored,
                "success_rate": (r.ok / r.calls) if r.calls else 0,
                "avg_ms": r.avg_ms,
                "last_called": r.last_called.isoformat() if r.last_called else None,
            }
            for r in rows
        ],
    }


@router.get("/firecrawl/budget")
async def firecrawl_budget(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Credit budget state for the crawl provider (OPS-01-05).

    Balance (provider-reported when reachable, else ledger-derived),
    projected monthly burn from the trailing 7-day average, headroom
    (balance − projection), the configured soft/hard caps, and the most
    recent throttle decisions so the onset of throttling is never silent.
    """
    _verify_admin(authorization)

    from irc_data.discovery import crawl_telemetry
    from irc_data.discovery.firecrawl_client import get_credit_usage

    budget = crawl_telemetry.credit_balance(
        engine, provider_balance=get_credit_usage
    )
    budget["throttle_events"] = crawl_telemetry.recent_throttle_events(
        engine, limit=50, blocked_only=False
    )
    return budget


@router.get("/firecrawl/diffs")
async def firecrawl_diffs(
    source: str | None = None,
    limit: int = 100,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Recent Firecrawl-vs-legacy comparisons from the parallel-run harness.

    One row per replayed event. Used to decide when a legacy source can
    be retired — when a source's last 5 diffs are all ≥95% match, the
    scraper code is safe to delete.

    Optional filter: source=cowesweek | sydneyhobart | rhkyc | isora | …
    """
    _verify_admin(authorization)
    limit = max(1, min(limit, 500))

    clauses = []
    params: dict = {"limit": limit}
    if source:
        clauses.append("source = :source")
        params["source"] = source
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, ran_at, source, source_url, event_name, event_date,
                   legacy_rows, firecrawl_rows, matched, match_rate, confidence,
                   missing_names, extra_names, notes
            FROM firecrawl_diffs
            {where}
            ORDER BY ran_at DESC
            LIMIT :limit
        """), params).fetchall()

        # Per-source rollup (last 30 days) for the at-a-glance traffic-light row
        # Exclude "hollow" legacy pages (legacy_rows = 0) to avoid under-extraction skew.
        per_source = conn.execute(text("""
            SELECT source,
                   COUNT(*)::int                                        AS runs,
                   AVG(match_rate)::numeric(4,3)                        AS avg_rate,
                   MIN(match_rate)::numeric(4,3)                        AS min_rate,
                   COUNT(*) FILTER (WHERE match_rate >= 0.95)::int      AS green,
                   COUNT(*) FILTER (WHERE match_rate >= 0.85
                                    AND match_rate < 0.95)::int         AS amber,
                   COUNT(*) FILTER (WHERE match_rate < 0.85)::int       AS red,
                   MAX(ran_at)                                          AS last_run
            FROM firecrawl_diffs
            WHERE ran_at > now() - interval '30 days'
              AND legacy_rows IS NOT NULL AND legacy_rows > 0
            GROUP BY source
            ORDER BY source
        """)).fetchall()

    return {
        "diffs": [
            {
                "id": r.id,
                "ran_at": r.ran_at.isoformat() if r.ran_at else None,
                "source": r.source,
                "source_url": r.source_url,
                "event_name": r.event_name,
                "event_date": r.event_date.isoformat() if r.event_date else None,
                "legacy_rows": r.legacy_rows,
                "firecrawl_rows": r.firecrawl_rows,
                "matched": r.matched,
                "match_rate": float(r.match_rate),
                "confidence": float(r.confidence) if r.confidence else None,
                "missing_names": r.missing_names or [],
                "extra_names": r.extra_names or [],
                "notes": r.notes,
            }
            for r in rows
        ],
        "rollup": [
            {
                "source": r.source,
                "runs": r.runs,
                "avg_rate": float(r.avg_rate) if r.avg_rate else 0.0,
                "min_rate": float(r.min_rate) if r.min_rate else 0.0,
                "green": r.green,
                "amber": r.amber,
                "red": r.red,
                "last_run": r.last_run.isoformat() if r.last_run else None,
            }
            for r in per_source
        ],
    }


@router.get("/conversations")
async def list_conversations(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """List all admin conversations, most recent first."""
    _verify_admin(authorization)

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.id, c.title, c.created_at,
                   COUNT(m.id) as message_count
            FROM admin_conversations c
            LEFT JOIN admin_messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
        """)).fetchall()

    return [
        {
            "id": row.id,
            "title": row.title,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "message_count": row.message_count,
        }
        for row in rows
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Get all messages for a conversation."""
    _verify_admin(authorization)

    with engine.connect() as conn:
        conv = conn.execute(
            text("SELECT id, title FROM admin_conversations WHERE id = :id"),
            {"id": conversation_id},
        ).first()

        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        rows = conn.execute(
            text("""
                SELECT id, role, content, queries, proposed_changes, created_at
                FROM admin_messages
                WHERE conversation_id = :cid
                ORDER BY id
            """),
            {"cid": conversation_id},
        ).fetchall()

    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "queries": row.queries,
                "proposed_changes": row.proposed_changes,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Delete a conversation and its messages."""
    _verify_admin(authorization)

    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM admin_conversations WHERE id = :id"),
            {"id": conversation_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted"}


@router.post("/chat")
async def admin_chat(
    body: AdminChatRequest,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Admin chat — investigate and manage data quality.

    Requires Authorization header with the admin password.
    """
    _verify_admin(authorization)

    return StreamingResponse(
        _admin_stream(engine, body.message, body.conversation_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/execute")
async def admin_execute(
    body: AdminQueryRequest,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Execute a confirmed admin change.

    Only UPDATE and INSERT statements are allowed (no DELETE/DROP/TRUNCATE).
    Requires Authorization header with the admin password.
    """
    _verify_admin(authorization)

    sql_upper = body.sql.strip().upper()
    forbidden = ["DELETE", "DROP", "TRUNCATE", "ALTER"]
    for word in forbidden:
        if sql_upper.startswith(word):
            raise HTTPException(status_code=400, detail=f"{word} statements are not allowed")

    if not (sql_upper.startswith("UPDATE") or sql_upper.startswith("INSERT") or sql_upper.startswith("WITH")):
        raise HTTPException(status_code=400, detail="Only UPDATE and INSERT statements are allowed")

    try:
        with engine.begin() as conn:
            result = conn.execute(text(body.sql))
            rows_affected = result.rowcount

        # Update the proposed change status in the conversation if provided
        if body.conversation_id and body.change_id is not None:
            try:
                with engine.connect() as conn:
                    row = conn.execute(
                        text("SELECT proposed_changes FROM admin_messages WHERE id = :id"),
                        {"id": body.change_id},
                    ).first()
                if row and row.proposed_changes:
                    changes = row.proposed_changes if isinstance(row.proposed_changes, list) else json.loads(row.proposed_changes)
                    # Find and update the matching change
                    for c in changes:
                        if c.get("sql") == body.sql:
                            c["status"] = "executed"
                            c["result"] = {"rows_affected": rows_affected}
                            break
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE admin_messages SET proposed_changes = :changes WHERE id = :id"),
                            {"changes": json.dumps(changes), "id": body.change_id},
                        )
            except Exception:
                pass  # Don't fail the execution if persistence update fails

        return {"status": "executed", "rows_affected": rows_affected}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Stripe webhook events (PAY-01-09) ────────────────────────────────────


@router.get("/stripe-events")
async def list_stripe_events(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    parked_only: bool = False,
    limit: int = 100,
):
    """List received Stripe webhook events, newest first.

    Parked events (``error`` starting with 'parked:') are subscriptions that
    could not be matched to a user — they need manual attention. Use
    ``parked_only=true`` to show just those.
    """
    _verify_admin(authorization)

    limit = max(1, min(limit, 500))
    where = "WHERE error LIKE 'parked:%'" if parked_only else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT id, event_id, type, livemode, created_at, processed_at, error
                FROM stripe_events
                {where}
                ORDER BY id DESC
                LIMIT :limit
            """),
            {"limit": limit},
        ).mappings().all()
        counts = conn.execute(
            text("""
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE error LIKE 'parked:%') AS parked,
                    count(*) FILTER (WHERE error IS NOT NULL
                                     AND error NOT LIKE 'parked:%') AS failed
                FROM stripe_events
            """)
        ).mappings().first()

    return {
        "counts": dict(counts) if counts else {"total": 0, "parked": 0, "failed": 0},
        "events": [{k: _jsonable(v) for k, v in row.items()} for row in rows],
    }
