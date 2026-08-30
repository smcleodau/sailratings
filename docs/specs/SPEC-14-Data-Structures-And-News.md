# SPEC-14: Data Structures, Localization & News Processing

## 1. Overview
This specification enforces strict data schema standards across the SailRatings platform. It dictates how timestamps are stored, how race results are granulated, and how unstructured text (news) is processed into structured graph relationships.

## 2. Localization & Timezones
- **Database Rule:** Every single `datetime` column in Postgres MUST be strictly normalized to UTC (`TIMESTAMP WITH TIME ZONE`).
- **Frontend Rule:** The Next.js frontend is responsible for localizing UTC timestamps into the user's browser timezone (using `Intl.DateTimeFormat` or a library like `date-fns`). The API must only emit UTC ISO-8601 strings.

## 3. Race Results Granularity
- **Database Schema:** We do not just store top-level Regatta/Series results. The `boat_events` (or `race_results`) table must store data at the **Individual Race Level**.
- **Schema Requirements:**
  - `event_id`: UUID (e.g., "Sydney Hobart 2026")
  - `race_name`: String (e.g., "Race 1 - Windward/Leeward")
  - `boat_id`: UUID
  - `corrected_time`: Integer (seconds)
  - `elapsed_time`: Integer (seconds)
  - `fleet_size`: Integer

## 4. Unstructured News Processing (LLM Extraction)
- **Ingestion:** When a news article (e.g., from Scuttlebutt or an RSS feed) is ingested, it is temporarily stored in an unstructured `raw_news` table.
- **Processing:** A Temporal workflow must pass the article text to the Sailing LLM (GLM 5.2).
- **Extraction Schema:** The LLM must be prompted with a strict JSON Schema output format to extract:
  ```json
  {
    "mentioned_boats": ["WOXI", "Comanche"],
    "mentioned_events": ["Rolex Sydney Hobart"],
    "sentiment": "positive",
    "key_takeaway": "Comanche breaks race record."
  }
  ```
- **Graph Linkage:** The extracted `mentioned_boats` are then cross-referenced against the `identities` column in the `boats` table (handling the fuzzy matching), and linked to the canonical boat entities in a `boat_news_mentions` join table.

## 5. Acceptance Criteria
- [ ] All Alembic migrations enforce `TIMESTAMP WITH TIME ZONE` and default to UTC.
- [ ] Next.js components cleanly localize dates to the client's browser.
- [ ] The `race_results` schema correctly stores data at the individual race level.
- [ ] The News Ingestion workflow successfully uses the LLM to extract structured entities and creates relational links to the canonical boats.
