from temporalio import workflow
from datetime import timedelta

with workflow.unsafe.imports_passed_through():
    from .activities import invoke_llm

@workflow.defn
class SwarmOrchestratorWorkflow:
    def __init__(self):
        self.chat_history = []
        self._pending_messages = []

    @workflow.signal
    async def receive_message(self, message: dict):
        """Receives a message from the user or the system."""
        self._pending_messages.append(message)

    @workflow.query
    def get_history(self) -> list:
        """Returns the full unified chat history."""
        return self.chat_history

    @workflow.run
    async def run(self):
        # Initial greeting
        self.chat_history.append({
            "role": "assistant",
            "content": "Swarm Orchestrator initialized. Waiting for user input."
        })

        system_prompt = """
        You are the Swarm Orchestrator. You are in a unified chat room with the user and 6 specialized agents.
        Your job is to read the user's messages and either answer them directly if it's general planning,
        or delegate the task to one of the 6 agents by responding with a specific command.

        The available agents are:
        - Sprint Manager: Product Owner. Grooms board, manages Epics.
        - Spec Writer: Architect. Writes docs/specs/*.md.
        - Data Engineer: Backend coder. Python, SQLAlchemy, Firecrawl.
        - UI Engineer: Frontend coder. Next.js, React.
        - QA Automator: Playwright, Pytest.
        - PR Reviewer: Evaluates diffs.

        If you want to invoke an agent, reply with ONLY this format:
        <INVOKE_AGENT name="Sprint Manager">instructions here</INVOKE_AGENT>

        Otherwise, just chat with the user in good English.
        """

        while True:
            # Wait for at least one new message
            await workflow.wait_condition(lambda: len(self._pending_messages) > 0)

            # Drain the queue into history
            while self._pending_messages:
                msg = self._pending_messages.pop(0)
                self.chat_history.append(msg)

            # Call the Orchestrator LLM to decide what to do
            response = await workflow.execute_activity(
                "invoke_llm",
                args=[system_prompt, self.chat_history],
                schedule_to_close_timeout=timedelta(minutes=5)
            )

            # Append the orchestrator's thought to the history
            self.chat_history.append({"role": "assistant", "agent": "Orchestrator", "content": response})

            # If the orchestrator decided to invoke the Sprint Manager, delegate to that agent's workflow
            if '<INVOKE_AGENT name="Sprint Manager">' in response:
                await workflow.execute_child_workflow(
                    "SprintManagerWorkflow",
                    {"description": "Groom the board"},
                )
