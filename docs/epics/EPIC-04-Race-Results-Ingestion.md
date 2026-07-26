# EPIC-04-Race-Results-Ingestion

## Problem Statement
Race results are currently scattered across multiple disparate platforms including SailSys, TopYacht, and Sailwave. Manual aggregation is error-prone and slow. We need a unified ingestion service that can scrape, normalize, and validate race results from these different sources. Crucially, we must enforce data quality so that orphaned results do not pollute the database.

## Technical Approach
1. **Source-Specific Adapters:**
   - Develop modular scraper/API adapters for SailSys, TopYacht, and Sailwave to extract race results and event metadata.
2. **Data Normalization:**
   - Map platform-specific data models into a unified `RaceResult` and `Event` schema within the SailRatings data model.
3. **Data Quality & Integrity Tests:**
   - Enforce strict foreign key constraints or application-level checks ensuring every `RaceResult` points to a valid `Event`.
   - Implement fuzzy or exact matching algorithms to verify that the event name in the result payload corresponds to the stored event.
   - Any result lacking a corresponding event (or failing the name match) must trigger an automatic event creation workflow or be quarantined.

## Acceptance Criteria
- [ ] Ingestion adapters are implemented and verified for SailSys, TopYacht, and Sailwave.
- [ ] Integration tests prove that a race result without a valid, pre-existing event in the database is automatically quarantined and not published.
- [ ] The system accurately matches and links race results to their respective events using event IDs and normalized event names.
- [ ] A run of historical data ingestion yields zero orphaned `RaceResult` records.
