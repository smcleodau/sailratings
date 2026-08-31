import asyncio
import os
import traceback
from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from .workflows import EpicExecutionWorkflow, SprintManagerWorkflow, TaskExecutionWorkflow
from .swarm import SwarmOrchestratorWorkflow
from .test_invoke_llm import TestLLMWorkflow
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

async def main():
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    
    try:
        # Connect to Temporal server
        client = await Client.connect(temporal_address, namespace="sailratings")

        # Run a worker for the orchestrator workflow
        worker = Worker(
            client,
            task_queue="orchestrator-task-queue",
            workflows=[
                EpicExecutionWorkflow,
                SprintManagerWorkflow,
                TaskExecutionWorkflow,
                SwarmOrchestratorWorkflow,
                TestLLMWorkflow,
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
