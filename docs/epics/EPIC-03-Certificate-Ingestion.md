# EPIC-03-Certificate-Ingestion

## Problem Statement
The current process for extracting data from PDF certificates relies heavily on manual intervention or isolated scripts using Gemini and Firecrawl. To support the SailRatings Intelligence Platform at scale, we need to transition this extraction process into an automated, standard ingestion pipeline. Furthermore, we must clearly distinguish between historical and active certificates, while implementing rigorous data validation to prevent data corruption (e.g., preventing duplicate active certificates for the same boat on the same date).

## Technical Approach
1. **Pipeline Integration:**
   - Integrate Firecrawl for fetching and initial processing of PDF certificates from source authorities.
   - Utilize Gemini's multimodal capabilities within the ingestion pipeline to parse complex layouts and extract structured rating data accurately.
2. **Historical vs. Active Classification:**
   - Implement logic to classify incoming certificates based on their issue and expiration dates.
   - Design a versioning or validity-window model in the database to archive older certificates while keeping the latest one active.
3. **Data Validation Rules Engine:**
   - Implement constraint checks at the pipeline level before database insertion.
   - Specific Rule: Reject or flag any ingestion attempt where an active IRC certificate has the exact same issue date as an existing active certificate for the exact same boat.
   - Log rejected records to a Dead Letter Queue (DLQ) for human review.

## Acceptance Criteria
- [ ] Automated pipeline successfully ingests a batch of 100 varied PDF certificates using Gemini/Firecrawl without manual intervention.
- [ ] System accurately flags and archives previous certificates when a newer valid certificate is ingested for the same boat.
- [ ] The validation engine successfully blocks the insertion of a duplicate active IRC certificate with identical issue dates for the same boat, routing it to the DLQ.
- [ ] End-to-end latency for processing a single PDF certificate is under 30 seconds.
