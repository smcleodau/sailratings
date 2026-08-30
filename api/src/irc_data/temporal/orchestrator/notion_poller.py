import os
import asyncio
import logging
import urllib.request
import json
from datetime import timedelta
from temporalio.client import Client as TemporalClient

logger = logging.getLogger(__name__)

class NotionPoller:
    def __init__(self):
        self.notion_token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
        self.db_id = '3ac37ffe-f467-8059-855a-e4e5666e7e3d'
        self.headers = {
            'Authorization': f'Bearer {self.notion_token}',
            'Notion-Version': '2022-06-28',
            'Content-Type': 'application/json'
        }
        
    MAX_CONCURRENT = int(os.environ.get("FACTORY_MAX_CONCURRENT", "5"))
    MAX_PER_POLL = int(os.environ.get("FACTORY_MAX_PER_POLL", "5"))

    async def poll(self):
        temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
        temporal_client = await TemporalClient.connect(temporal_address, namespace="sailratings")

        # Count currently running workflows; skip this poll if at cap
        running_count = 0
        async for wf in temporal_client.list_workflows(
            query="ExecutionStatus='Running'",
        ):
            running_count += 1
        if running_count >= self.MAX_CONCURRENT:
            logger.info(f"Skipping poll: {running_count} workflows already running (cap={self.MAX_CONCURRENT})")
            return

        # Query DB
        req = urllib.request.Request(
            f'https://api.notion.com/v1/databases/{self.db_id}/query',
            data=json.dumps({
                "filter": {
                    "property": "Status",
                    "select": {"equals": "Ready"}
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

        slots_available = self.MAX_CONCURRENT - running_count
        dispatched = 0

        # Configurable epic allow-list (comma-separated, e.g. "DP-01,AF-00")
        # Empty string = all epics allowed
        allowed_epics_env = os.environ.get("FACTORY_ALLOWED_EPICS", "DP-01")
        allowed_epics = [e.strip() for e in allowed_epics_env.split(",") if e.strip()] if allowed_epics_env else []

        def epic_allowed(page):
            parent = (page.get('properties', {}).get('Parent Epic', {}).get('select') or {}).get('name', '')
            if not allowed_epics:
                return True
            return parent in allowed_epics

        eligible = [p for p in results if epic_allowed(p)]
        logger.info(f"{len(eligible)} of {len(results)} ready tasks are in allowed epics {allowed_epics}")

        for page in eligible[:min(len(eligible), self.MAX_PER_POLL, slots_available)]:
            try:
                # Extract title
                title_objs = page.get('properties', {}).get('Title', {}).get('rich_text', []) \
                    or page.get('properties', {}).get('ID', {}).get('title', [])
                title = title_objs[0].get('text', {}).get('content', '') if title_objs else "Untitled"
                
                # Fetch children
                req_children = urllib.request.Request(f"https://api.notion.com/v1/blocks/{page['id']}/children", headers=self.headers)
                res_children = urllib.request.urlopen(req_children)
                children = json.loads(res_children.read()).get('results', [])
                
                def prop_text(page, key):
                    p = page.get('properties', {}).get(key, {})
                    ptype = p.get('type')
                    if ptype == 'rich_text':
                        return "".join(t['text']['content'] for t in p.get('rich_text', []))
                    elif ptype == 'title':
                        return "".join(t['text']['content'] for t in p.get('title', []))
                    elif ptype == 'select':
                        sel = p.get('select')
                        return sel['name'] if sel else ''
                    return ''

                props = page.get('properties', {})
                issue_id = prop_text(page, 'ID')
                goal = prop_text(page, 'Goal')
                scope = prop_text(page, 'Scope')
                deliverable = prop_text(page, 'Deliverable')
                acceptance = prop_text(page, 'Acceptance Criteria')
                spec_ref = prop_text(page, 'Spec Reference')
                verification = prop_text(page, 'Verification')
                blocked_by = prop_text(page, 'Blocked By')
                handoff = prop_text(page, 'Handoff / Output Contract')

                description = (
                    f"Issue: {issue_id} — {title}\n"
                    f"URL: {page['url']}\n"
                    f"Spec Reference: {spec_ref}\n\n"
                    f"Goal: {goal}\n\n"
                    f"Scope: {scope}\n\n"
                    f"Deliverable: {deliverable}\n\n"
                    f"Handoff / Output Contract: {handoff}\n\n"
                    f"Acceptance Criteria:\n{acceptance}\n\n"
                    f"Verification: {verification}\n\n"
                    f"Blocked By: {blocked_by}\n\n"
                    f"Additional notes from page body:\n"
                )

                for child in children:
                    btype = child['type']
                    if btype == 'paragraph':
                        rt = child['paragraph'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += text + "\n"
                    elif btype == 'heading_1':
                        rt = child['heading_1'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += f"# {text}\n"
                    elif btype == 'heading_2':
                        rt = child['heading_2'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += f"## {text}\n"
                    elif btype == 'heading_3':
                        rt = child['heading_3'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += f"### {text}\n"
                    elif btype in ('bulleted_list_item', 'numbered_list_item'):
                        rt = child[btype].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += f"- {text}\n"
                    elif btype == 'code':
                        rt = child['code'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        lang = child['code'].get('language', '')
                        description += f"```{lang}\n{text}\n```\n"
                    elif btype == 'quote':
                        rt = child['quote'].get('rich_text', [])
                        text = "".join([t['text']['content'] for t in rt])
                        description += f"> {text}\n"

                task_payload = {"id": page["id"], "url": page["url"], "description": description, "title": title}
                
                logger.info(f"Starting workflow for task: {title}")
                await temporal_client.start_workflow(
                    "EpicExecutionWorkflow",
                    task_payload,
                    id=f"agent-task-{page['id']}",
                    task_queue="orchestrator-task-queue",
                    task_timeout=timedelta(seconds=60),
                )
                dispatched += 1
                
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
                title_objs = page.get('properties', {}).get('Title', {}).get('rich_text', []) \
                    or page.get('properties', {}).get('ID', {}).get('title', [])
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

async def main_loop():
    poller = NotionPoller()
    while True:
        try:
            await poller.poll()
        except Exception as e:
            logger.error(f"Error in poller loop: {e}")
        await asyncio.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_loop())
