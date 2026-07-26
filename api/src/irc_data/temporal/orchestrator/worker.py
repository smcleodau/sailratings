import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker

from .workflows import TaskExecutionWorkflow
from .activities import (
    provision_worktree,
    run_openhands_agent,
    run_playwright_e2e_tests,
    create_pull_request,
    notify_admin_hitl,
    route_to_dlq,
    teardown_worktree,
)

async def main():
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    
    # Connect to Temporal server
    client = await Client.connect(temporal_address, namespace="sailratings")

    # Run a worker for the orchestrator workflow
    worker = Worker(
        client,
        task_queue="orchestrator-task-queue",
        workflows=[
            TaskExecutionWorkflow,
        ],
        activities=[
            provision_worktree,
            run_openhands_agent,
            run_playwright_e2e_tests,
            create_pull_request,
            notify_admin_hitl,
            route_to_dlq,
            teardown_worktree,
        ],
    )

    print(f"Starting Temporal Orchestrator worker on {temporal_address}...")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
