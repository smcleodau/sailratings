from datetime import timedelta
from temporalio import workflow
from temporalio.exceptions import ApplicationError

# Import activities with a longer timeout since they interact with docker and git
with workflow.unsafe.imports_passed_through():
    from .activities import (
        provision_worktree,
        run_openhands_agent,
        run_playwright_e2e_tests,
        create_pull_request,
        notify_admin_hitl,
        route_to_dlq,
        teardown_worktree,
    )

@workflow.defn
class TaskExecutionWorkflow:
    @workflow.run
    async def run(self, task_payload: dict):
        worktree_path = await workflow.execute_activity(
            provision_worktree,
            task_payload,
            start_to_close_timeout=timedelta(minutes=5)
        )
        
        try:
            # Spawn Agent with budget limit ($5 max) and iteration limit (30)
            # The agent handles delegation to sub-agents (spec writer -> coder -> reviewer)
            agent_result = await workflow.execute_activity(
                run_openhands_agent, 
                args=[worktree_path, task_payload],
                start_to_close_timeout=timedelta(hours=2)
            )
            
            # Evaluate Result & Run GUI/UX Playwright Tests
            test_passed = await workflow.execute_activity(
                run_playwright_e2e_tests, 
                worktree_path,
                start_to_close_timeout=timedelta(minutes=15)
            )
            
            if test_passed:
                await workflow.execute_activity(
                    create_pull_request, 
                    worktree_path,
                    start_to_close_timeout=timedelta(minutes=5)
                )
            else:
                # Route to Human in the Loop for test failures
                await workflow.execute_activity(
                    notify_admin_hitl, 
                    {"reason": "E2E Tests Failed", "task": task_payload},
                    start_to_close_timeout=timedelta(minutes=1)
                )
                # Workflow suspends here waiting for an external signal from Admin UI
                await workflow.wait_condition(lambda: False) # Will be replaced by signal
                
        except ApplicationError as e: # Catch budget limits or agent crashes
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
