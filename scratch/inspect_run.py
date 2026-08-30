import os
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.workspace import LocalWorkspace

def test():
    llm = LLM(model="gemini/gemini-2.5-pro", api_key=os.environ.get("GEMINI_API_KEY"))
    agent = Agent(llm=llm, system_prompt="You are a helpful agent. Say hi.")
    workspace = LocalWorkspace(working_dir="/tmp")
    conversation = Conversation(agent=agent, workspace=workspace)
    conversation.send_message("Say hi")
    
    import inspect
    print("Is run coroutine?", inspect.iscoroutinefunction(conversation.run))
    
    result = conversation.run()
    print("Result type:", type(result))
    print("Result:", result)

if __name__ == "__main__":
    test()
