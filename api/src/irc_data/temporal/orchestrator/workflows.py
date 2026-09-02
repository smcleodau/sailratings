from datetime import timedelta
from temporalio import workflow
from temporalio.exceptions import ApplicationError

# Import activities with a longer timeout since they interact with docker and git
with workflow.unsafe.imports_passed_through():
    from .activities import (
        provision_worktree,
        run_lane_worker_agent,
        commit_agent_work,
        run_reviewer_agent,
        run_sprint_manager_agent,
        run_playwright_e2e_tests,
        create_pull_request,
        notify_admin_hitl,
        route_to_dlq,
        teardown_worktree,
        add_notion_comment,
        fetch_board_state,
    )

from .base_agent import ConversationalAgentWorkflow

@workflow.defn
class SprintManagerWorkflow(ConversationalAgentWorkflow):
    @workflow.run
    async def run(self, task_payload: dict) -> dict:
        task_description = task_payload.get("description", "Manage the active sprint and backlog.")
        
        try:
            notion_state = await workflow.execute_activity(
                fetch_board_state,
                start_to_close_timeout=timedelta(minutes=1)
            )
            system_prompt = """
            You are the 'Sprint Manager', a high-level Technical Project Manager and Architect Agent.
            Your job is to manage the backlog of Epics and Issues in the Notion Database, draft detailed technical specifications based on the codebase, break them down into granular engineering issues, and organize them into active Sprints.
            
            CRITICAL RULES YOU MUST FOLLOW:
            1. NAMING CONVENTION: Every single issue you create MUST start with EXACTLY ONE prefix: "[ISSUE] " (e.g., "[ISSUE] Create DB Migration"). Any new Epics MUST start with "[EPIC] ". Do NOT double prefix issues like "[ISSUE] [EPIC-XX]".
            2. SPECIFICATION PROPERTY: For every issue you create in Notion, you MUST explicitly set the 'Specification' property (which is a rich_text type) to an inline @mention of the Parent Epic page. Do NOT use local file:/// links.
            3. MANDATORY FIELDS: Every item MUST have 'type' (Epic, Issue, Bug) and 'Status' (e.g., 'To Do') explicitly set.
            4. DEPENDENCIES: Link issues using the 'Blocked By' relation property ONLY if there is a real dependency. Do not force dependencies if there are none.
            
            If you need to ask the user a question to clarify requirements or get approval, include the exact string "<ASK_USER>" anywhere in your response. This will pause the workflow until the user replies.
            """
            
            initial_task = f"Here is the board: {notion_state}\nTask: {task_description}"
            
            return await super().run(
                system_prompt=system_prompt,
                initial_task=initial_task
            )
        except ApplicationError as e:
            await workflow.execute_activity(
                route_to_dlq, 
                {"error": str(e), "workflow": "SprintManagerWorkflow", "task": task_payload},
                start_to_close_timeout=timedelta(minutes=1)
            )
            raise e

@workflow.defn
class EpicExecutionWorkflow:
    @workflow.run
    async def run(self, task_payload: dict):
        notion_page_id = task_payload.get("id")
        
        if notion_page_id:
            await workflow.execute_activity(
                add_notion_comment,
                args=[notion_page_id, "🚀 Evidence-Gated Workflow started: Provisioning isolated git worktree..."],
                start_to_close_timeout=timedelta(minutes=1)
            )

        worktree_path = await workflow.execute_activity(
            provision_worktree,
            task_payload,
            start_to_close_timeout=timedelta(minutes=5)
        )
        
        try:
            feedback = None
            max_attempts = 3
            for attempt in range(max_attempts):
                if notion_page_id:
                    await workflow.execute_activity(
                        add_notion_comment,
                        args=[notion_page_id, f"✅ Provisioned. Spawning Lane Worker AI (Attempt {attempt+1}/{max_attempts})..."],
                        start_to_close_timeout=timedelta(minutes=1)
                    )

                # 1. Lane Worker
                await workflow.execute_activity(
                    run_lane_worker_agent,
                    args=[worktree_path, task_payload, feedback],
                    start_to_close_timeout=timedelta(hours=2)
                )

                # 1b. Auto-commit any uncommitted agent work
                await workflow.execute_activity(
                    commit_agent_work,
                    args=[worktree_path, f"feat: agent implementation (attempt {attempt+1})"],
                    start_to_close_timeout=timedelta(minutes=2)
                )

                # 2. E2E Tests — 45 min: npm install + playwright install + web npm install + tests
                test_passed = await workflow.execute_activity(
                    run_playwright_e2e_tests,
                    worktree_path,
                    start_to_close_timeout=timedelta(minutes=45)
                )
                
                if not test_passed:
                    feedback = "E2E Playwright tests failed locally. Please fix the broken tests."
                    continue # Loop back to worker
                
                # 3. Gatekeeper Reviewer
                if notion_page_id:
                    await workflow.execute_activity(
                        add_notion_comment,
                        args=[notion_page_id, "🔍 Lane Worker finished. Spawning Gatekeeper Reviewer to verify evidence..."],
                        start_to_close_timeout=timedelta(minutes=1)
                    )
                    
                review_result = await workflow.execute_activity(
                    run_reviewer_agent, 
                    args=[worktree_path, task_payload],
                    start_to_close_timeout=timedelta(minutes=30)
                )
                
                if review_result.get("passed"):
                    if notion_page_id:
                        await workflow.execute_activity(
                            add_notion_comment,
                            args=[notion_page_id, "✅ Gatekeeper APPROVED! Evidence verified. Opening Pull Request."],
                            start_to_close_timeout=timedelta(minutes=1)
                        )
                    await workflow.execute_activity(
                        create_pull_request, 
                        worktree_path,
                        start_to_close_timeout=timedelta(minutes=5)
                    )
                    return # Exit loop and finish
                else:
                    feedback = review_result.get("feedback")
                    if notion_page_id:
                        await workflow.execute_activity(
                            add_notion_comment,
                            args=[notion_page_id, f"❌ Gatekeeper REJECTED: {feedback}. Looping back to worker..."],
                            start_to_close_timeout=timedelta(minutes=1)
                        )
            
            # If we exhaust attempts
            if notion_page_id:
                await workflow.execute_activity(
                    add_notion_comment,
                    args=[notion_page_id, "❌ Exhausted all agent attempts. Routing to Human-In-The-Loop (HITL)."],
                    start_to_close_timeout=timedelta(minutes=1)
                )
            await workflow.execute_activity(
                notify_admin_hitl, 
                {"reason": "Exhausted all attempts or Reviewer repeatedly rejected", "task": task_payload},
                start_to_close_timeout=timedelta(minutes=1)
            )
            await workflow.wait_condition(lambda: False)
            
        except ApplicationError as e:
            if notion_page_id:
                await workflow.execute_activity(
                    add_notion_comment,
                    args=[notion_page_id, f"❌ FATAL ERROR:\n```\n{str(e)}\n```\nRouting to DLQ."],
                    start_to_close_timeout=timedelta(minutes=1)
                )
            await workflow.execute_activity(
                route_to_dlq, 
                {"error": str(e), "task": task_payload},
                start_to_close_timeout=timedelta(minutes=1)
            )
        finally:
            await workflow.execute_activity(
                teardown_worktree, 
                worktree_path,
                start_to_close_timeout=timedelta(minutes=5)
            )

@workflow.defn
class TaskExecutionWorkflow(EpicExecutionWorkflow):
    @workflow.run
    async def run(self, task_payload: dict):
        return await super().run(task_payload)
