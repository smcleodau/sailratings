import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker

from irc_data.temporal.activities import scrape_activities
from irc_data.temporal.workflows import (
    DailyScrapeWorkflow, 
    IncrementalResultsWorkflow,
    MonthlyHistoryWorkflow,
    EventDiscoveryWorkflow,
    DailyNewsWorkflow
)

async def main():
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    
    # Connect to Temporal server
    client = await Client.connect(temporal_address)

    # Run a worker for the workflow
    worker = Worker(
        client,
        task_queue="scrape-task-queue",
        workflows=[
            DailyScrapeWorkflow, 
            IncrementalResultsWorkflow,
            MonthlyHistoryWorkflow,
            EventDiscoveryWorkflow,
            DailyNewsWorkflow
        ],
        activities=[
            scrape_activities.scrape_orc,
            scrape_activities.match_boats_orc_only,
            scrape_activities.scrape_orc_detail,
            scrape_activities.refresh_views,
            scrape_activities.scrape_tcc,
            scrape_activities.scrape_sailsys,
            scrape_activities.rematch_results,
            scrape_activities.scrape_topyacht,
            scrape_activities.scrape_certs_exhaustive,
            scrape_activities.scrape_wayback_tcc,
            scrape_activities.discover_events,
            scrape_activities.generate_boat_events,
            scrape_activities.scrape_boat_news,
        ],
    )

    print(f"Starting Temporal worker on {temporal_address}...")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
