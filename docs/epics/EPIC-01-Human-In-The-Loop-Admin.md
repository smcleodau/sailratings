# EPIC-01-Human-In-The-Loop-Admin

## Problem Statement
Automated identity matching for boats and sailors cannot be 100% accurate. Matches with a confidence score between 0.4 and 0.8 are ambiguous and require human validation. Additionally, platform administrators need a consolidated view to monitor scraper health and manage the overall data pipeline. A dedicated administration interface is required to streamline these operational tasks.

## Technical Approach
1. **Frontend Development:**
   - Build a specialized Admin portal within the Next.js frontend application.
   - Utilize the newly adopted "Paper" design system to ensure UI consistency and accessibility.
2. **Ambiguity Resolution Interface:**
   - Create a dashboard that queues identity matches with confidence scores between 0.4 and 0.8.
   - Display side-by-side comparisons of the disparate records to facilitate quick "Merge" or "Keep Separate" decisions by an admin.
3. **Pipeline & Scraper Monitoring:**
   - Integrate with the backend pipeline's metrics (e.g., Prometheus/Grafana or custom API endpoints) to display scraper health, success rates, and DLQ sizes.
   - Provide basic control actions (e.g., retry failed jobs, trigger manual scrape).

## Acceptance Criteria
- [ ] Admin UI is accessible via a secure, authenticated route in the Next.js application using the "Paper" design system.
- [ ] The resolution queue successfully fetches and displays all pending identity matches within the 0.4-0.8 confidence threshold.
- [ ] An admin can successfully merge two records or mark them as distinct, immediately reflecting the change in the core database.
- [ ] The dashboard accurately reflects real-time or near real-time status of all active scrapers (Up/Down, Last Run Time, Error Rate).
