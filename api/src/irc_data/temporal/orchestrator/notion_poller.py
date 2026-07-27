import os
import asyncio
import logging
import urllib.request
import json
from temporalio.client import Client as TemporalClient

logger = logging.getLogger(__name__)

class NotionPoller:
    def __init__(self):
        self.notion_token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
        self.db_id = '3a937ffe-f467-81b3-b888-d873def19261'
        self.headers = {
            'Authorization': f'Bearer {self.notion_token}',
            'Notion-Version': '2022-06-28',
            'Content-Type': 'application/json'
        }
        
    async def poll(self):
        temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
        temporal_client = await TemporalClient.connect(temporal_address, namespace="sailratings")

        # Query DB
        req = urllib.request.Request(
            f'https://api.notion.com/v1/databases/{self.db_id}/query',
            data=json.dumps({
                "filter": {
                    "property": "Status",
                    "select": {"equals": "Ready for Agent"}
                }
            }).encode(),
            method='POST',
            headers=self.headers
        )
        try:
            res = urllib.request.urlopen(req)
            results = json.loads(res.read()).get('results', [])
        except Exception as e:
            logger.error(f"Error querying Notion for Tasks: {e}")
            results = []
            
        logger.info(f"Found {len(results)} tasks ready for agent.")

        # Query DB for Epics that need Specifications
        req_specs = urllib.request.Request(
            f'https://api.notion.com/v1/databases/{self.db_id}/query',
            data=json.dumps({
                "filter": {
                    "property": "Status",
                    "select": {"equals": "Needs Spec"}
                }
            }).encode(),
            method='POST',
            headers=self.headers
        )
        try:
            res_specs = urllib.request.urlopen(req_specs)
            spec_results = json.loads(res_specs.read()).get('results', [])
        except Exception as e:
            logger.error(f"Error querying Notion for Epics needing specs: {e}")
            spec_results = []
            
        logger.info(f"Found {len(spec_results)} epics needing specification.")
        
        for page in results:
            try:
                # Extract title
                title_objs = page.get('properties', {}).get('Issue', {}).get('title', [])
                title = title_objs[0].get('text', {}).get('content', '') if title_objs else "Untitled"
                
                # Fetch children
                req_children = urllib.request.Request(f"https://api.notion.com/v1/blocks/{page['id']}/children", headers=self.headers)
                res_children = urllib.request.urlopen(req_children)
                children = json.loads(res_children.read()).get('results', [])
                
                description = f"Title: {title}\nURL: {page['url']}\n\nTask Details:\n"
                
                for child in children:
                    if child['type'] == 'paragraph':
                        rt = child['paragraph'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += text + "\n"
                    elif child['type'] == 'heading_3':
                        rt = child['heading_3'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += f"### {text}\n"

                task_payload = {"id": page["id"], "url": page["url"], "description": description, "title": title}
                
                logger.info(f"Starting workflow for task: {title}")
                await temporal_client.start_workflow(
                    "EpicExecutionWorkflow",
                    task_payload,
                    id=f"agent-task-{page['id']}",
                    task_queue="orchestrator-task-queue"
                )
                
                # Update Notion status
                req_update = urllib.request.Request(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    data=json.dumps({"properties": {"Status": {"select": {"name": "In Progress"}}}}).encode(),
                    method='PATCH',
                    headers=self.headers
                )
                urllib.request.urlopen(req_update)
            except Exception as e:
                logger.error(f"Error starting workflow for {page['id']}: {e}")

        # Trigger Specification Agents for Epics
        for page in spec_results:
            try:
                title_objs = page.get('properties', {}).get('Issue', {}).get('title', [])
                title = title_objs[0].get('text', {}).get('content', '') if title_objs else "Untitled"
                
                req_children = urllib.request.Request(f"https://api.notion.com/v1/blocks/{page['id']}/children", headers=self.headers)
                res_children = urllib.request.urlopen(req_children)
                children = json.loads(res_children.read()).get('results', [])
                
                description = f"Title: {title}\nURL: {page['url']}\n\nEpic Details:\n"
                for child in children:
                    if child['type'] == 'paragraph':
                        rt = child['paragraph'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += text + "\n"

                task_payload = {"id": page["id"], "url": page["url"], "description": description, "title": title}
                
                logger.info(f"Starting Sprint Manager for epic: {title}")
                await temporal_client.start_workflow(
                    "SprintManagerWorkflow",
                    task_payload,
                    id=f"sprint-manager-{page['id']}",
                    task_queue="orchestrator-task-queue"
                )
                
                # Update Notion status
                req_update = urllib.request.Request(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    data=json.dumps({"properties": {"Status": {"select": {"name": "Spec Writing In Progress"}}}}).encode(),
                    method='PATCH',
                    headers=self.headers
                )
                urllib.request.urlopen(req_update)
            except Exception as e:
                logger.error(f"Error starting SprintManager for {page['id']}: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    poller = NotionPoller()
    asyncio.run(poller.poll())
