import os
import re
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
        self.db_id = '3b237ffe-f467-81b4-8aad-e4eb0d49f4da'
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

        # Query DB — Ready tasks that haven't been dispatched yet
        req = urllib.request.Request(
            f'https://api.notion.com/v1/databases/{self.db_id}/query',
            data=json.dumps({
                "filter": {
                    "and": [
                        {"property": "Status", "select": {"equals": "Ready"}},
                        {"property": "Execution State", "select": {"equals": "Not Dispatched"}},
                    ]
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
                    "select": {"equals": "Needs Specification"}
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

        # --- Dependency-aware epic selection ---
        # Fetch all rows to build the epic dependency graph
        try:
            all_req = urllib.request.Request(
                f'https://api.notion.com/v1/databases/{self.db_id}/query',
                data=json.dumps({"page_size": 200}).encode(),
                method='POST', headers=self.headers
            )
            all_pages = json.loads(urllib.request.urlopen(all_req).read()).get('results', [])
        except Exception as e:
            logger.error(f"Error fetching pages for dep graph: {e}")
            all_pages = []

        def _rt(page, key):
            rt = page.get('properties', {}).get(key, {}).get('rich_text', [])
            return rt[0]['text']['content'] if rt else ''

        def _sel(page, key):
            s = page.get('properties', {}).get(key, {}).get('select') or {}
            return s.get('name', '')

        # Epics: rows with no Parent Epic text
        epic_rows = [p for p in all_pages if not p.get('properties', {}).get('Parent Epic', {}).get('rich_text', [])]
        done_epics = {_rt(e, 'ID') for e in epic_rows if _sel(e, 'Status') == 'Done'}
        logger.info(f"Done epics: {done_epics or '(none)'}")

        # A "Blocked By" field can legitimately name a specific task (e.g.
        # OPS-01 was blocked by "DP-00-01", a single policy-approval issue,
        # not its whole parent epic "DP-00") as well as an epic. Checking
        # only done_epics meant a genuinely-completed task blocker could
        # never clear automatically — observed: OPS-01 stayed ineligible
        # indefinitely despite DP-00-01 being approved weeks earlier, which
        # in turn kept OPS-02 (and everything behind it) from ever
        # activating. done_ids covers both.
        done_ids = {_rt(p, 'ID') for p in all_pages if _sel(p, 'Status') == 'Done'}

        def _sprint_key(epic):
            s = _rt(epic, 'Sprint')
            if not s or s.lower() == 'interim':
                return 0
            m = re.search(r'\d+', s)
            return int(m.group()) if m else 999

        def _blockers_met(epic):
            blocked = _rt(epic, 'Blocked By').strip()
            if not blocked:
                return True
            return all(b.strip() in done_ids for b in re.split(r'[;,]', blocked) if b.strip())

        eligible_epics = sorted(
            [e for e in epic_rows
             if _sel(e, 'Status') == 'Ready'
             and _blockers_met(e)
             and not e.get('properties', {}).get('Human Gate', {}).get('checkbox', False)],
            key=_sprint_key
        )

        if not eligible_epics:
            logger.info("No eligible epics (all blocked or gated). Nothing to dispatch.")
            return

        active_epic = eligible_epics[0]
        active_epic_id = _rt(active_epic, 'ID')
        logger.info(
            f"Active epic: {active_epic_id}  sprint={_rt(active_epic, 'Sprint') or 'Interim'}"
            f"  blocked_by='{_rt(active_epic, 'Blocked By') or 'none'}'"
        )

        def _in_active_epic(page):
            rt = page.get('properties', {}).get('Parent Epic', {}).get('rich_text', [])
            return (rt[0]['text']['content'] if rt else '') == active_epic_id

        def human_gate(page):
            """Live-check Human Gate to avoid stale index."""
            cb = page.get('properties', {}).get('Human Gate', {})
            if not cb.get('checkbox', False):
                return False
            try:
                req = urllib.request.Request(
                    f"https://api.notion.com/v1/pages/{page['id']}", headers=self.headers)
                live = json.loads(urllib.request.urlopen(req).read())
                return live.get('properties', {}).get('Human Gate', {}).get('checkbox', True)
            except Exception:
                return True

        eligible = [p for p in results if _in_active_epic(p) and not human_gate(p)]
        gated = [p for p in results if _in_active_epic(p) and human_gate(p)]
        off_epic = len(results) - len([p for p in results if _in_active_epic(p)])
        if gated:
            ids = [(_rt(p, 'ID') or p['id']) for p in gated]
            logger.info(f"Skipping {len(gated)} human-gate tasks in {active_epic_id}: {ids}")
        if off_epic:
            logger.info(f"Skipping {off_epic} tasks from other epics (active: {active_epic_id})")
        logger.info(f"{len(eligible)} tasks eligible from active epic {active_epic_id}")

        for page in eligible[:min(len(eligible), self.MAX_PER_POLL, slots_available)]:
            try:
                # Extract title — Roadmap uses type:title on 'Title', type:rich_text on 'ID'
                title_objs = (page.get('properties', {}).get('Title', {}).get('title', [])
                    or page.get('properties', {}).get('ID', {}).get('rich_text', []))
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
                
                # Mark Execution State = Queued so we don't re-dispatch on the next poll
                req_update = urllib.request.Request(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    data=json.dumps({"properties": {"Execution State": {"select": {"name": "Queued"}}}}).encode(),
                    method='PATCH',
                    headers=self.headers
                )
                urllib.request.urlopen(req_update)
            except Exception as e:
                logger.error(f"Error starting workflow for {page['id']}: {e}")

        # Trigger Specification Agents for Epics
        for page in spec_results:
            try:
                title_objs = (page.get('properties', {}).get('Title', {}).get('title', [])
                    or page.get('properties', {}).get('ID', {}).get('rich_text', []))
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
                
                # Mark Execution State = Queued to prevent re-dispatch
                req_update = urllib.request.Request(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    data=json.dumps({"properties": {"Execution State": {"select": {"name": "Queued"}}}}).encode(),
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
