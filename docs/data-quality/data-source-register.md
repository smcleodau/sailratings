# SailRatings Intelligence Platform - Data Source Register

## Core Rating Systems
* **ORC (Offshore Racing Congress)**
  * **Ingestion Method**: Web scraping/API downloads (`orc.py`), automated daily via `DailyScrapeWorkflow`
  * **Entities Produced**: `ORCCertificate`, `ORCSnapshot`, `Boat` (with identity matched)
* **IRC TCC Listings**
  * **Ingestion Method**: Scrapes official TCC listings (`tcc_listing.py`), automated daily via `DailyScrapeWorkflow`
  * **Entities Produced**: `TCCSnapshotModel`, `Boat`
* **IRC Certificates**
  * **Ingestion Method**: Bulk downloads, web search, PDF parsing via Gemini/Firecrawl (`cert_index.py`, `cert_probe.py`, `certificate_bulk.py`, `certificate_search.py`, `historical_certs.py`)
  * **Entities Produced**: `Certificate` (IRC), `Boat`

## Race Management & Results Platforms
* **SailSys & TopYacht**
  * **Ingestion Method**: Incremental web scraping API (`sailsys.py`, `topyacht.py`), automated via `IncrementalResultsWorkflow`
  * **Entities Produced**: `Event`, `EventEntry`, `RaceResultModel`
* **File-based & Other Platforms**
  * **Sources**: Sailwave (`sailwave.py`), Yacht Scoring (`yachtscoring.py`), SailRaceHQ (`sailracehq.py`)
  * **Ingestion Method**: Web scraping and generic HTML/file parsing
  * **Entities Produced**: `Event`, `EventEntry`, `RaceResultModel`
* **Event-Specific Scrapers**
  * **Sources**: Cowes Week, ISORA, RORC, Sydney Hobart
  * **Ingestion Method**: Bespoke per-source scrapers (`cowesweek.py`, `isora.py`, `rorc.py`, `sydneyhobart.py`)
  * **Entities Produced**: `Event`, `EventEntry`, `RaceResultModel`

## Other Sources
* **Sailing News**
  * **Ingestion Method**: Firecrawl and Gemini-based extraction (`news.py`), automated daily via `DailyNewsWorkflow`
  * **Entities Produced**: Raw SQL entries in `boat_news` and `boat_news_mentions`, linking to `Boat`
* **Wayback Machine (Historical TCCs)**
  * **Ingestion Method**: Archive.org scraping (`wayback.py`), automated monthly via `MonthlyHistoryWorkflow`
  * **Entities Produced**: Historical TCC data
