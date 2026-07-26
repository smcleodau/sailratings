from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.activities import scrape_activities

@workflow.defn
class DailyScrapeWorkflow:
    @workflow.run
    async def run(self) -> dict:
        """Runs the daily scrape pipeline including ORC, TCC, and Race Results."""
        
        # 1. ORC Pipeline
        orc_result = await workflow.execute_activity(
            scrape_activities.scrape_orc,
            start_to_close_timeout=timedelta(hours=1),
        )
        
        await workflow.execute_activity(
            scrape_activities.match_boats_orc_only,
            start_to_close_timeout=timedelta(minutes=30),
        )
        
        await workflow.execute_activity(
            scrape_activities.scrape_orc_detail,
            args=[500],
            start_to_close_timeout=timedelta(hours=2),
        )

        # 2. View Refresh
        await workflow.execute_activity(
            scrape_activities.refresh_views,
            start_to_close_timeout=timedelta(minutes=30),
        )

        # 3. IRC TCC Scrape
        tcc_result = await workflow.execute_activity(
            scrape_activities.scrape_tcc,
            start_to_close_timeout=timedelta(minutes=30),
        )

        # 4. Generate Event Feed
        await workflow.execute_activity(
            scrape_activities.generate_boat_events,
            start_to_close_timeout=timedelta(minutes=30),
        )

        return {
            "orc": "completed",
            "tcc": "completed",
            "events_feed": "completed"
        }

@workflow.defn
class IncrementalResultsWorkflow:
    @workflow.run
    async def run(self) -> dict:
        """Runs the frequent (incremental) result scraping."""
        
        sailsys_result = await workflow.execute_activity(
            scrape_activities.scrape_sailsys,
            start_to_close_timeout=timedelta(minutes=30),
        )
        
        topyacht_result = await workflow.execute_activity(
            scrape_activities.scrape_topyacht,
            start_to_close_timeout=timedelta(minutes=30),
        )
        
        rematch_result = await workflow.execute_activity(
            scrape_activities.rematch_results,
            start_to_close_timeout=timedelta(minutes=15),
        )

        await workflow.execute_activity(
            scrape_activities.generate_boat_events,
            start_to_close_timeout=timedelta(minutes=30),
        )

        return {
            "sailsys": "completed",
            "topyacht": "completed",
            "rematch": "completed",
            "events_feed": "completed"
        }

@workflow.defn
class MonthlyHistoryWorkflow:
    @workflow.run
    async def run(self) -> dict:
        """Runs the monthly exhaustive historical scrape."""
        
        await workflow.execute_activity(
            scrape_activities.scrape_wayback_tcc,
            start_to_close_timeout=timedelta(hours=6),
        )
        
        await workflow.execute_activity(
            scrape_activities.scrape_certs_exhaustive,
            start_to_close_timeout=timedelta(hours=6),
        )

        return {"status": "completed"}

@workflow.defn
class EventDiscoveryWorkflow:
    @workflow.run
    async def run(self) -> dict:
        """Runs the daily event crawler."""
        
        await workflow.execute_activity(
            scrape_activities.discover_events,
            start_to_close_timeout=timedelta(hours=2),
        )

        return {"status": "completed"}

@workflow.defn
class DailyNewsWorkflow:
    @workflow.run
    async def run(self) -> dict:
        """Runs the daily sailing news scrape."""
        
        await workflow.execute_activity(
            scrape_activities.scrape_boat_news,
            start_to_close_timeout=timedelta(hours=1),
        )

        return {"status": "completed"}
