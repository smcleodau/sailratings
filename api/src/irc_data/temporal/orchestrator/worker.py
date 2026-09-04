import asyncio
import os
import traceback
from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from .workflows import EpicExecutionWorkflow, SprintManagerWorkflow, TaskExecutionWorkflow
from .swarm import SwarmOrchestratorWorkflow
from .test_invoke_llm import TestLLMWorkflow
from ..replay.replay_workflows import ReplayWorkflow, BackfillWorkflow
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
    invoke_llm,
    fetch_board_state,
)
from ..replay.replay_activities import (
    init_replay_tables_activity,
    create_batch_activity,
    select_artifacts_activity,
    run_parser_activity,
    compare_batches_activity,
    count_batch_artifacts_activity,
    promote_batch_activity,
)
from irc_data.telemetry import setup_telemetry

async def main():
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    
    # Setup OpenTelemetry
    tracing_interceptor = setup_telemetry("temporal-worker")
    
    try:
        # Connect to Temporal server
        client = await Client.connect(
            temporal_address, 
            namespace="sailratings",
            interceptors=[tracing_interceptor]
        )

        # Run a worker for the orchestrator workflow
        # Cap concurrent activities: each OpenHands lane worker is very heavy
        # (API calls + subprocesses). Too many concurrent ones starve the asyncio
        # event loop so workflow tasks and provision_worktree can't execute.
        worker = Worker(
            client,
            task_queue="orchestrator-task-queue",
            max_concurrent_activities=5,
            max_concurrent_workflow_tasks=10,
            workflows=[
                EpicExecutionWorkflow,
                SprintManagerWorkflow,
                TaskExecutionWorkflow,
                SwarmOrchestratorWorkflow,
                TestLLMWorkflow,
                ReplayWorkflow,
                BackfillWorkflow,
            ],
            activities=[
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
                invoke_llm,
                fetch_board_state,
                init_replay_tables_activity,
                create_batch_activity,
                select_artifacts_activity,
                run_parser_activity,
                compare_batches_activity,
                count_batch_artifacts_activity,
                promote_batch_activity,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )

        print(f"Starting Temporal Orchestrator worker on {temporal_address}...")
        await worker.run()
    except Exception as e:
        print("WORKER FAILED")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
