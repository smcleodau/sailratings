# SPEC-13: Global Data Acquisition (Scraping Pipeline)

## 1. Overview
This specification details the architecture for the Global Data Acquisition pipeline. The system relies on pulling data from disparate platforms (SailSys, TopYacht, IRC/ORC registries) and unifying it into our Postgres database.

## 2. Operational Cadence
- **Nightly Batch Jobs:** Scrapers MUST NOT be triggered on-demand by users to avoid rate-limiting and slow page loads.
- **Temporal Orchestration:** All scrapers will run as massively parallel Temporal workflows scheduled to execute every night at `02:00 UTC`.
- **Workflow Names:** 
  - `SyncSailSysWorkflow`
  - `SyncTopYachtWorkflow`
  - `SyncIRCCertificatesWorkflow`

## 3. Architecture & Error Handling
### 3.1 Idempotency
All scraping tasks and database insertions must be strictly idempotent. If the nightly job fails halfway through and restarts, it must not create duplicate certificates or race entries.
- Use `INSERT ... ON CONFLICT (source_id) DO UPDATE` for all external data ingestion.

### 3.2 Rate Limiting
- External APIs/websites often employ anti-scraping measures. Temporal Activities fetching external pages must use bounded retries with exponential backoff.
- Set a maximum concurrency limit per domain (e.g., max 5 concurrent requests to SailSys) to avoid being IP banned.

## 4. Acceptance Criteria
- [ ] Temporal schedules are created for `02:00 UTC` for all major scrapers.
- [ ] All database insertions in the scraping workflows utilize Postgres `ON CONFLICT` constraints to ensure idempotency.
- [ ] Temporal activities implement exponential backoff and domain-level rate limiting.
