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

    def _query_all(self, body: dict) -> list:
        """Query the roadmap database, following pagination to completion.

        Notion's REST API hard-caps page_size at 100 per request regardless
        of what's asked for — every call site here previously requested up
        to 200 (or omitted page_size, which the API also caps at 100) and
        took only the first page, silently dropping the rest whenever the
        database held more matching rows than that. Confirmed live: an
        unfiltered fetch against a 130-row database returned exactly 100
        rows with has_more=true, and the remaining 30 — including five
        whole epics (AD-01, DP-00, DP-01, DP-02, DP-04) — were invisible to
        every epic-selection decision this made. Silent, not an error: the
        exact failure mode of "no eligible epics" that this method exists
        to prevent from recurring.
        """
        results: list = []
        cursor = None
        while True:
            payload = dict(body)
            if cursor:
                payload["start_cursor"] = cursor
            req = urllib.request.Request(
                f'https://api.notion.com/v1/databases/{self.db_id}/query',
                data=json.dumps(payload).encode(),
                method='POST', headers=self.headers,
            )
            data = json.loads(urllib.request.urlopen(req).read())
            results.extend(data.get('results', []))
            if not data.get('has_more'):
                break
            cursor = data.get('next_cursor')
            if not cursor:
                break
        return results

    async def poll(self):
        temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
        temporal_client = await TemporalClient.connect(temporal_address, namespace="sailratings")

        # Count currently running EpicExecutionWorkflows; skip this poll if at
        # cap. Must scope by WorkflowType — an unscoped "ExecutionStatus=
        # 'Running'" query also counts unrelated long-lived workflows on this
        # namespace (the perpetual ScheduleSyncLoopWorkflow, in-flight
        # SourceRunWorkflow scraper runs), which silently starved epic
        # dispatch down to well under MAX_CONCURRENT actual factory slots.
        running_count = 0
        async for wf in temporal_client.list_workflows(
            query="WorkflowType='EpicExecutionWorkflow' AND ExecutionStatus='Running'",
        ):
            running_count += 1
        if running_count >= self.MAX_CONCURRENT:
            logger.info(f"Skipping poll: {running_count} workflows already running (cap={self.MAX_CONCURRENT})")
            return

        # Query DB — Ready tasks that haven't been dispatched yet
        try:
            results = self._query_all({
                "filter": {
                    "and": [
                        {"property": "Status", "select": {"equals": "Ready"}},
                        {"property": "Execution State", "select": {"equals": "Not Dispatched"}},
                    ]
                }
            })
        except Exception as e:
            logger.error(f"Error querying Notion for Tasks: {e}")
            results = []

        logger.info(f"Found {len(results)} tasks ready for agent.")

        # Query DB for Epics that need Specifications
        try:
            spec_results = self._query_all({
                "filter": {
                    "property": "Status",
                    "select": {"equals": "Needs Specification"}
                }
            })
        except Exception as e:
            logger.error(f"Error querying Notion for Epics needing specs: {e}")
            spec_results = []

        logger.info(f"Found {len(spec_results)} epics needing specification.")

        slots_available = self.MAX_CONCURRENT - running_count
        dispatched = 0

        # --- Dependency-aware epic selection ---
        # Fetch all rows to build the epic dependency graph
        try:
            all_pages = self._query_all({})
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

        # Walk eligible_epics in sprint-priority order and dispatch from the
        # first one that actually has a dispatchable task. Previously we
        # always picked eligible_epics[0] and stopped there — once that
        # epic's only remaining tasks were human-gated (its own epic-level
        # Status/Human Gate fields don't reflect that), the poller reported
        # "0 tasks eligible" and sat idle forever even with dozens of ready
        # tasks queued in other epics behind it.
        eligible = gated = []
        active_epic_id = None
        for active_epic in eligible_epics:
            candidate_id = _rt(active_epic, 'ID')

            def _in_epic(page, epic_id=candidate_id):
                rt = page.get('properties', {}).get('Parent Epic', {}).get('rich_text', [])
                return (rt[0]['text']['content'] if rt else '') == epic_id

            candidate_eligible = [p for p in results if _in_epic(p) and not human_gate(p)]
            candidate_gated = [p for p in results if _in_epic(p) and human_gate(p)]
            if candidate_eligible:
                active_epic_id = candidate_id
                eligible, gated = candidate_eligible, candidate_gated
                logger.info(
                    f"Active epic: {active_epic_id}  sprint={_rt(active_epic, 'Sprint') or 'Interim'}"
                    f"  blocked_by='{_rt(active_epic, 'Blocked By') or 'none'}'"
                )
                break
            if candidate_gated:
                logger.info(
                    f"Epic {candidate_id} has only human-gated tasks left "
                    f"({len(candidate_gated)}); trying next eligible epic."
                )

        if active_epic_id is None:
            logger.info(
                "No eligible epics have a dispatchable task "
                f"(checked {len(eligible_epics)}: all remaining tasks are human-gated)."
            )
            return

        off_epic = len(results) - len(eligible) - len(gated)
        if gated:
            ids = [(_rt(p, 'ID') or p['id']) for p in gated]
            logger.info(f"Skipping {len(gated)} human-gate tasks in {active_epic_id}: {ids}")
        if off_epic:
            logger.info(f"Skipping {off_epic} tasks from other epics (active: {active_epic_id})")
        logger.info(f"{len(eligible)} tasks eligible from active epic {active_epic_id}")

        # Task-level Blocked By (4 Sep 2026). Until now only the *epic's*
        # Blocked By was checked; every Ready task inside the active epic was
        # dispatched in parallel regardless of its own Blocked By text. That is
        # how AD-01-16 and AD-01-06 (and AD-01-03 vs a sibling) ended up editing
        # AdminSidebar.tsx / admin.py in the same hour and colliding at merge.
        # A task now waits until every ID it names is Done.
        def _task_blockers_met(page):
            blocked = _rt(page, 'Blocked By').strip()
            if not blocked:
                return True
            missing = [b.strip() for b in re.split(r'[;,]', blocked)
                       if b.strip() and b.strip() not in done_ids]
            if missing:
                logger.info(f"Holding {_rt(page, 'ID') or page['id']}: blocked by {missing}")
            return not missing

        eligible = [p for p in eligible if _task_blockers_met(p)]
        # Deterministic order: lowest ID first, so AD-01-17 goes before AD-01-24.
        eligible.sort(key=lambda p: _rt(p, 'ID'))

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
                # Roadmap schema property names (4 Sep 2026). The previous
                # names — 'Spec Reference', 'Deliverable', 'Handoff / Output
                # Contract' — were the Build Programme's; on the Roadmap they
                # do not exist, so Spec Ref and Output Contract never reached
                # the worker prompt. Fall back to the old names so a row that
                # still carries them is not silently emptied.
                def prop_first(page, *keys):
                    for k in keys:
                        v = prop_text(page, k)
                        if v:
                            return v
                    return ''

                issue_id = prop_text(page, 'ID')
                goal = prop_text(page, 'Goal')
                scope = prop_text(page, 'Scope')
                acceptance = prop_text(page, 'Acceptance Criteria')
                spec_ref = prop_first(page, 'Spec Ref', 'Spec Reference')
                verification = prop_text(page, 'Verification')
                blocked_by = prop_text(page, 'Blocked By')
                output_contract = prop_first(page, 'Output Contract', 'Handoff / Output Contract')
                parent_epic = prop_text(page, 'Parent Epic')
                autonomy = prop_text(page, 'Autonomy')
                agent_role = prop_text(page, 'Agent Role')

                description = (
                    f"Issue: {issue_id} — {title}\n"
                    f"URL: {page['url']}\n"
                    f"Epic: {parent_epic}    Agent Role: {agent_role}    Autonomy: {autonomy}\n"
                    f"Spec Ref: {spec_ref}\n\n"
                    f"Goal: {goal}\n\n"
                    f"Scope: {scope}\n\n"
                    f"Output Contract: {output_contract}\n\n"
                    f"Acceptance Criteria:\n{acceptance}\n\n"
                    f"Verification: {verification}\n\n"
                    f"Blocked By: {blocked_by}\n\n"
                    f"Page body (Files you own / must NOT touch / Prototype / Acceptance commands / Evidence):\n"
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
                    elif btype in ('callout', 'toggle', 'to_do'):
                        rt = child[btype].get('rich_text', [])
                        text = "".join([t.get('text', {}).get('content', '') for t in rt])
                        description += text + "\n"
                    # Nested children (indented list items, table rows, toggle
                    # bodies) are deliberately not fetched: cards must keep
                    # everything at the top level (TEMPLATE-01).

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
