import os
import shutil
import asyncio
import urllib.request
import json
from datetime import timedelta
from temporalio import activity
from temporalio.exceptions import ApplicationError

@activity.defn
async def provision_worktree(task: dict) -> str:
    # Extract task id and branch name
    task_id = task.get("id", "unknown-task")
    branch_name = f"feature/{task_id}"
    
    repo_path = "/home/irc-data/code/sailratings"
    worktrees_dir = "/home/irc-data/code/sailratings/worktrees"
    os.makedirs(worktrees_dir, exist_ok=True)
    worktree_path = os.path.join(worktrees_dir, task_id)
    
    # Prune any broken or missing worktrees to avoid "already registered" fatal errors
    await asyncio.create_subprocess_shell(
        f"git -C {repo_path} worktree prune",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    
    # Force remove if it's somehow still registered
    await asyncio.create_subprocess_shell(
        f"git -C {repo_path} worktree remove --force {worktree_path}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )

    # If directory already exists, remove it
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
        
    # Execute git worktree add
    cmd = f"git -C {repo_path} worktree add -b {branch_name} {worktree_path} develop"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        # Check if branch already exists
        if b"already exists" in stderr or b"already used by worktree" in stderr:
            cmd = f"git -C {repo_path} worktree add {worktree_path} {branch_name}"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise ApplicationError(f"Failed to create worktree on existing branch: {stderr.decode()}")
        else:
            raise ApplicationError(f"Failed to create worktree: {stderr.decode()}")
    
    return worktree_path

@activity.defn
async def teardown_worktree(worktree_path: str) -> None:
    repo_path = "/home/irc-data/code/sailratings"
    
    # Try to gracefully remove the worktree
    cmd = f"git -C {repo_path} worktree remove --force {worktree_path}"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    
    # Also explicitly prune to ensure git metadata is entirely clean
    await asyncio.create_subprocess_shell(
        f"git -C {repo_path} worktree prune",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    
    # And forcefully clean up the directory if git missed it
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)

@activity.defn
async def run_lane_worker_agent(worktree_path: str, task: dict, feedback: str = None) -> dict:
    activity.logger.info(f"Initializing OpenHands Lane Worker Agent in {worktree_path} for task {task.get('id')}")
    
    from openhands.sdk import LLM, Agent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.workspace import LocalWorkspace
    import asyncio
    
    llm = LLM(
        model=os.environ.get("LLM_MODEL", "openai/glm-5.2"),
        api_key=os.environ.get("LLM_API_KEY", os.environ.get("GEMINI_API_KEY", "dummy")),
        base_url=os.environ.get("LLM_BASE_URL", "http://100.93.15.38:10006/api/worker-router")
    )
    workspace = LocalWorkspace(working_dir=worktree_path)

    system_prompt = """
    You are the 'Lane Worker Agent', responsible for implementing features according to technical specifications.
    Read the provided task description, inspect the repository if needed, and write the code to fulfill the requirements.
    
    CRITICAL (NO FALSE-DONE):
    You MUST NOT consider the task done until you have verifiable evidence. You must use the `BoardOperator` interface to upload screenshots or test logs to the issue board. 
    A claim of 'it works' is not evidence. If you do not provide hard evidence, the Gatekeeper will reject your PR.
    
    HOW TO POST EVIDENCE:
    Use the `NotionAdapter` in python to post your test logs or image URLs to the Notion issue you are working on.
    ```python
    from src.irc_data.temporal.orchestrator.board_operator import NotionAdapter
    adapter = NotionAdapter()
    
    # To post a test log:
    adapter.append_test_evidence(issue_id="YOUR_TASK_ID", test_command="npm run test", output="PASS")
    
    # To post a screenshot URL:
    adapter.append_visual_evidence(issue_id="YOUR_TASK_ID", image_url="https://...")
    ```
    """
    
    from openhands.tools.preset.default import get_default_tools
    tools = get_default_tools(enable_browser=False, enable_sub_agents=False)
    
    agent = Agent(llm=llm, system_prompt=system_prompt, tools=tools)
    task_description = task.get("description", "Perform the necessary tasks in the repository.")
    prompt = f"Implement the following task:\n\n{task_description}"
    if feedback:
        prompt += f"\n\nTHE GATEKEEPER REJECTED YOUR PREVIOUS ATTEMPT WITH THIS FEEDBACK:\n{feedback}\nPlease address these issues."
        
    try:
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message(prompt)
        result = await conversation.run() if asyncio.iscoroutinefunction(conversation.run) else conversation.run()
        activity.logger.info("Lane Worker run complete.")
        return {"status": "success", "result": str(result)}
    except Exception as e:
        activity.logger.error(f"Lane Worker run failed: {e}")
        raise ApplicationError(f"Lane Worker run failed: {e}")

@activity.defn
async def run_reviewer_agent(worktree_path: str, task: dict) -> dict:
    activity.logger.info(f"Initializing OpenHands Reviewer Gatekeeper in {worktree_path} for task {task.get('id')}")
    
    from openhands.sdk import LLM, Agent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.workspace import LocalWorkspace
    import asyncio
    
    llm = LLM(
        model=os.environ.get("LLM_MODEL", "openai/glm-5.2"),
        api_key=os.environ.get("LLM_API_KEY", os.environ.get("GEMINI_API_KEY", "dummy")),
        base_url=os.environ.get("LLM_BASE_URL", "http://100.93.15.38:10006/api/worker-router")
    )
    workspace = LocalWorkspace(working_dir=worktree_path)

    system_prompt = """
    You are the 'Adversarial Watchdog'. You review code changes and issue boards.
    1. Verify that the exact AC from the spec is met.
    2. Check the Notion Issue board to verify that undeniable evidence (logs/screenshots) has been posted.
    You CANNOT edit files. You only output a final decision:
    If it passes, your last line must be "DECISION: PASS".
    If it fails, provide your feedback and end with "DECISION: FAIL".
    """
    
    from openhands.tools.preset.default import get_default_tools
    # Read-only tools
    tools = get_default_tools(enable_browser=False, enable_sub_agents=False)
    
    agent = Agent(llm=llm, system_prompt=system_prompt, tools=tools)
    prompt = f"Review the changes in this worktree against the task:\n\n{task.get('description')}\nCheck the board for evidence using the provided scripts. Output PASS or FAIL."
        
    try:
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message(prompt)
        result_obj = await conversation.run() if asyncio.iscoroutinefunction(conversation.run) else conversation.run()
        result = str(result_obj)
        activity.logger.info("Reviewer run complete.")
        
        passed = "DECISION: PASS" in result
        return {"passed": passed, "feedback": result}
    except Exception as e:
        activity.logger.error(f"Reviewer run failed: {e}")
        raise ApplicationError(f"Reviewer run failed: {e}")

@activity.defn
async def run_playwright_e2e_tests(worktree_path: str) -> bool:
    activity.logger.info(f"Running Playwright tests in {worktree_path}")
    web_dir = os.path.join(worktree_path, "web")
    proc = await asyncio.create_subprocess_shell(
        "npm install && npx playwright test",
        cwd=web_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        activity.logger.error(
            f"Playwright tests failed (exit {proc.returncode}): {stderr.decode()}"
        )
        return False
    return True

@activity.defn
async def create_pull_request(worktree_path: str) -> None:
    activity.logger.info(f"Creating pull request for {worktree_path}")
    pass

@activity.defn
async def notify_admin_hitl(details: dict) -> None:
    activity.logger.info(f"Notifying admin for HITL: {details}")
    pass

@activity.defn
async def route_to_dlq(details: dict) -> None:
    activity.logger.error(f"Routing to DLQ: {details}")
    pass

@activity.defn
async def add_notion_comment(page_id: str, comment: str) -> None:
    activity.logger.info(f"Adding Notion comment to {page_id}: {comment}")
    notion_token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
    if not notion_token:
        return

    headers = {
        'Authorization': f'Bearer {notion_token}',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json'
    }
    
    data = {
        "parent": {
            "page_id": page_id
        },
        "rich_text": [
            {
                "text": {
                    "content": comment
                }
            }
        ]
    }
    
    req = urllib.request.Request(
        "https://api.notion.com/v1/comments",
        data=json.dumps(data).encode(),
        method='POST',
        headers=headers
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        activity.logger.error(f"Failed to add Notion comment: {e}")

@activity.defn
async def run_sprint_manager_agent(task_description: str = "Review the backlog and plan the next sprint.") -> dict:
    repo_path = "/home/irc-data/code/sailratings"
    activity.logger.info(f"Initializing OpenHands Sprint Manager Agent in {repo_path}")
    
    from openhands.sdk import LLM, Agent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.workspace import LocalWorkspace
    from openhands.tools.preset.default import get_default_tools
    import asyncio
    
    llm = LLM(
        model="openai/glm-5.2", 
        api_key=os.environ.get("GEMINI_API_KEY", "dummy"),
        base_url="http://100.93.15.38:10006/api/worker-router"
    )
    workspace = LocalWorkspace(working_dir=repo_path)
    
    # Load tools
    tools = get_default_tools(enable_browser=False, enable_sub_agents=False)
    
    system_prompt = """
    You are the 'Sprint Manager', a high-level Technical Project Manager and Architect Agent.
    Your job is to manage the backlog of Epics and Issues in the Notion Database, draft detailed technical specifications based on the codebase, break them down into granular engineering issues, and organize them into active Sprints.
    
    CRITICAL RULES YOU MUST FOLLOW:
    1. NAMING CONVENTION: Every single issue you create MUST start with EXACTLY ONE prefix: "[ISSUE] " (e.g., "[ISSUE] Create DB Migration"). Any new Epics MUST start with "[EPIC] ". Do NOT double prefix issues like "[ISSUE] [EPIC-XX]".
    2. SPECIFICATION PROPERTY: For every issue you create in Notion, you MUST explicitly set the 'Specification' property (which is a rich_text type) to an inline @mention of the Parent Epic page. Do NOT use local file:/// links.
    3. MANDATORY FIELDS: Every item MUST have 'type' (Epic, Issue, Bug) and 'Status' (e.g., 'To Do') explicitly set.
    4. DEPENDENCIES: Link issues using the 'Blocked By' relation property ONLY if there is a real dependency. Do not force dependencies if there are none.
    """
    
    agent = Agent(llm=llm, system_prompt=system_prompt, tools=tools)
    
    
    
    try:
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message(task_description)
        result = await conversation.run() if asyncio.iscoroutinefunction(conversation.run) else conversation.run()
        activity.logger.info("Sprint Manager run complete.")
        return {"status": "success", "result": str(result)}
    except Exception as e:
        activity.logger.error(f"Sprint Manager run failed: {e}")
        raise ApplicationError(f"Sprint Manager run failed: {e}")

@activity.defn
async def invoke_llm(system_prompt: str, chat_history: list) -> str:
    from openai import AsyncOpenAI
    import os
    
    internal_key = (
        os.environ.get("MARTHA_ROUTER_SERVICE_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or "dummy"
    )
    client = AsyncOpenAI(
        api_key=internal_key,
        base_url="http://100.93.15.38:10006/api/worker-router"
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    try:
        response = await client.chat.completions.create(
            model="openai/glm-5.2",
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        import traceback
        activity.logger.error(f"LLM Error: {traceback.format_exc()}")
        return f"Error connecting to LLM: {str(e)}"

@activity.defn
async def fetch_board_state() -> str:
    # A lightweight stub representing the Notion board state
    return "Board state summary: [No active sprint. Epics need grooming.]"
