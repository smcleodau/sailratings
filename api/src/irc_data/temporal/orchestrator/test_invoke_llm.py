import asyncio
import os
import sys
sys.path.append("/home/irc-data/code/sailratings/api/src")
from temporalio.client import Client
from temporalio import workflow
from datetime import timedelta

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.orchestrator.activities import invoke_llm

@workflow.defn
class TestLLMWorkflow:
    @workflow.run
    async def run(self):
        return await workflow.execute_activity(
            invoke_llm,
            args=["You are a helpful assistant.", [{"role": "user", "content": "Hello!"}]],
            schedule_to_close_timeout=timedelta(minutes=1)
        )

async def main():
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(temporal_address, namespace="sailratings")
    
    print("Starting workflow...")
    result = await client.execute_workflow(
        TestLLMWorkflow.run,
        id="test-llm-workflow",
        task_queue="orchestrator-task-queue"
    )
    print("Result:", result)

if __name__ == "__main__":
    asyncio.run(main())
