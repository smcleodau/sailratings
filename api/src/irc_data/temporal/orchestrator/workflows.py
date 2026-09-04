from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy
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
            You are the 'Sprint Manager' for the 'sailratings Roadmap' Notion database
            (3b237ffe-f467-81b4-8aad-e4eb0d49f4da). Follow the Roadmap row TEMPLATE-01: plain IDs
            (AD-01-25), no [ISSUE]/[EPIC] prefixes, no 'Specification' property, Blocked By as text IDs,
            mechanical acceptance criteria, cards created as Draft. Never write to the archived
            'SailRatings Issue Tracker' or 'Build Programme' databases.
            If you need a decision or approval, include the exact string "<ASK_USER>" in your response.
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
            start_to_close_timeout=timedelta(minutes=10)
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
                    # No retry_policy here previously meant Temporal's default
                    # (unlimited attempts) — a genuine merge conflict or a
                    # GitHub push-protection block on a leaked secret can
                    # never resolve itself, so the activity retried forever
                    # (observed: attempts into the 90s over multiple hours),
                    # permanently pinning a factory concurrency slot with the
                    # workflow never reaching the except/finally below that
                    # would have DLQ'd it and torn down its worktree. Cap
                    # attempts so a genuinely unrecoverable failure surfaces.
                    await workflow.execute_activity(
                        create_pull_request,
                        worktree_path,
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=10),
                            backoff_coefficient=2.0,
                            maximum_interval=timedelta(minutes=5),
                            maximum_attempts=5,
                        ),
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
