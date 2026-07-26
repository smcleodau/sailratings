import os
import asyncio
import logging
from notion_client import Client
from temporalio.client import Client as TemporalClient

# from .workflows import TaskExecutionWorkflow

logger = logging.getLogger(__name__)

class NotionPoller:
    def __init__(self):
        self.notion = Client(auth=os.environ.get("SAILRATINGS_NOTION_TOKEN"))
        self.db_id = os.environ.get("SAILRATINGS_NOTION_ISSUES_DB_ID")
        
    async def poll(self):
        if not self.db_id:
            logger.warning("SAILRATINGS_NOTION_ISSUES_DB_ID not set, skipping polling.")
            return

        temporal_client = await TemporalClient.connect("localhost:7233")

        while True:
            try:
                # Example query
                # response = self.notion.databases.query(
                #     database_id=self.db_id,
                #     filter={
                #         "property": "Status",
                #         "select": {"equals": "Ready for Agent"}
                #     }
                # )
                # for page in response.get("results", []):
                #     task_payload = {"id": page["id"], "url": page["url"]}
                #     await temporal_client.start_workflow(
                #         "TaskExecutionWorkflow",
                #         task_payload,
                #         id=f"agent-task-{page['id']}",
                #         task_queue="orchestrator-queue"
                #     )
                #     # Update Notion status to 'In Progress' to avoid re-triggering
                #     self.notion.pages.update(page_id=page["id"], properties={"Status": {"select": {"name": "In Progress"}}})
                pass
            except Exception as e:
                logger.error(f"Error polling notion: {e}")
                
            await asyncio.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    poller = NotionPoller()
    asyncio.run(poller.poll())
