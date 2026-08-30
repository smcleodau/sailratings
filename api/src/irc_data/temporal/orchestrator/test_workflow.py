from temporalio import workflow

@workflow.defn
class DummyWorkflow:
    @workflow.run
    async def run(self):
        return "Hello World"
