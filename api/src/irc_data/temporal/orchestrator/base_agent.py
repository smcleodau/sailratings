from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import invoke_llm

@workflow.defn
class ConversationalAgentWorkflow:
    def __init__(self):
        self.chat_history = []
        self._pending_user_reply = None

    @workflow.signal
    async def receive_message(self, message: str):
        """Receives a message from the user or another agent."""
        self._pending_user_reply = message

    @workflow.query
    def get_history(self) -> list:
        """Allows external UIs to read the agent's current thoughts."""
        return self.chat_history

    @workflow.run
    async def run(self, system_prompt: str, initial_task: str) -> dict:
        self.chat_history.append({"role": "user", "content": initial_task})
        
        while True:
            # 1. Call LLM (via Activity to avoid blocking workflow thread)
            response = await workflow.execute_activity(
                invoke_llm, 
                args=[system_prompt, self.chat_history],
                schedule_to_close_timeout=timedelta(minutes=15)
            )
            self.chat_history.append({"role": "assistant", "content": response})
            
            # 2. Check if the LLM asked a question that needs a human reply
            if "<ASK_USER>" in response:
                # Suspend the workflow until a Signal is received
                await workflow.wait_condition(lambda: self._pending_user_reply is not None)
                self.chat_history.append({"role": "user", "content": self._pending_user_reply})
                self._pending_user_reply = None
            else:
                # Task complete or moving to next step
                break
        return {"status": "completed", "history": self.chat_history}
