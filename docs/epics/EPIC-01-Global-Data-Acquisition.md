# EPIC-01: Global Data Acquisition Pipeline

## Problem Statement
Currently, the SailRatings platform relies on fragmented, standalone scrapers for different platforms (SailSys, TopYacht, Sailwave, specific regattas). This approach leads to duplicated logic, fragile ingestion methods, and inconsistent data mappings (e.g., legacy custom columns vs generic extractions). The lack of a unified pipeline means maintenance is difficult, error handling is inconsistent, and scaling to new data sources requires writing new bespoke code from scratch.

## Technical Approach
Migrate from standalone, source-specific scrapers to a unified data pipeline with the following stages:
1. **Acquisition**: Use robust generic crawlers (e.g., Firecrawl) and feed-discovery mechanisms to fetch raw HTML/JSON/PDFs from target sources and queue them.
2. **Normalization**: Implement a standard internal schema. Use LLMs or standardized extraction patterns to parse raw source data into this generic format, decoupling acquisition from business logic.
3. **Identity Matching**: Defer boat matching to a centralized service rather than resolving inline during parsing.
4. **Validation**: Assert constraints on normalized data (e.g., valid rating bounds, correct data types).
5. **Enrichment**: Enhance the data with external metadata and queue it for downstream storage (`Event`, `EventEntry`, `RaceResultModel`).

## Acceptance Criteria
- [ ] Implement a unified queue system (e.g., Temporal task queues) for raw data acquisition.
- [ ] Define generic JSON schemas for `Event`, `EventEntry`, and `RaceResultModel` ingestion.
- [ ] Rewrite at least two major scrapers (e.g., SailSys, TopYacht) to use the new acquisition-normalization separation.
- [ ] Establish telemetry and observability for the pipeline to track ingestion success, failure rates, and volume metrics.
