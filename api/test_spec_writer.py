import asyncio
import os
import sys

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.workspace import LocalWorkspace

async def main():
    epic_file = "/home/irc-data/code/sailratings/docs/epics/EPIC-05-Human-In-The-Loop-Admin.md"
    if not os.path.exists(epic_file):
        print(f"Error: {epic_file} does not exist.")
        sys.exit(1)
        
    with open(epic_file, "r") as f:
        epic_content = f.read()

    # Need GEMINI_API_KEY to be set in 1Password / environment
    llm = LLM(model="gemini/gemini-2.5-pro", api_key=os.environ.get("GEMINI_API_KEY"))
    
    work_dir = "/home/irc-data/code/sailratings/scratch/spec-writer"
    os.makedirs(work_dir, exist_ok=True)
    
    workspace = LocalWorkspace(working_dir=work_dir)

    system_prompt = """
    You are the 'Spec Writer', a highly specialized Technical Architect.
    Your ONLY job is to read the provided Notion/GitHub issue and output a detailed Markdown 
    specification document containing Architecture, Data Models, and Acceptance Criteria.
    Do not write code. Do not run tests. Only write the specification.
    """
    
    agent = Agent(
        llm=llm,
        system_prompt=system_prompt,
    )
    
    task = f"Read the following Epic and generate a detailed Technical Specification document named 'SPEC-05.md'. Output the entire markdown content in your final message.\n\nEpic Content:\n{epic_content}"
    print("Running OpenHands Agent...")
    
    try:
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message(task)
        result = await conversation.run() if asyncio.iscoroutinefunction(conversation.run) else conversation.run()
        print("Agent Run Complete!")
        print("Final Message:")
        print(result)
    except Exception as e:
        print(f"Agent Run Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
