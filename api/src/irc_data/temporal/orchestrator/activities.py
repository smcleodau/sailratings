import os
import shutil
import asyncio
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
    
    # If it already exists, remove it
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
        
    # Execute git worktree add
    # We will use git command directly or gitpython. Here using asyncio.create_subprocess_shell
    cmd = f"git -C {repo_path} worktree add -b {branch_name} {worktree_path} main"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        # Check if branch already exists and worktree creation failed because of that
        if b"already exists" in stderr:
            cmd = f"git -C {repo_path} worktree add {worktree_path} {branch_name}"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise ApplicationError(f"Failed to create worktree: {stderr.decode()}")
        else:
            raise ApplicationError(f"Failed to create worktree: {stderr.decode()}")
    
    return worktree_path

@activity.defn
async def teardown_worktree(worktree_path: str) -> None:
    if not os.path.exists(worktree_path):
        return
        
    repo_path = "/home/irc-data/code/sailratings"
    cmd = f"git -C {repo_path} worktree remove --force {worktree_path}"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ApplicationError(f"Failed to remove worktree: {stderr.decode()}")

@activity.defn
async def run_openhands_agent(args: list) -> dict:
    worktree_path, task = args
    activity.logger.info(f"Initializing OpenHands Lead Agent in {worktree_path} for task {task.get('id')}")
    
    # Example of how the Spec Writer is defined and invoked using the SDK:
    # 
    # from openhands.sdk.agent import Agent, LLM
    # from openhands.sdk.workspace import DockerWorkspace
    #
    # llm = LLM(model="anthropic/claude-3-5-sonnet-20241022")
    # workspace = DockerWorkspace(worktree_path, custom_image="sailratings-agent:latest")
    #
    # spec_writer_prompt = \"\"\"
    # You are the 'Spec Writer', a highly specialized Technical Architect.
    # Your ONLY job is to read the provided Notion/GitHub issue and output a detailed Markdown 
    # specification document containing Architecture, Data Models, and Acceptance Criteria.
    # Do not write code. Do not run tests. Only write the specification.
    # \"\"\"
    #
    # spec_agent = Agent(llm=llm, workspace=workspace, system_prompt=spec_writer_prompt)
    # result = await spec_agent.run(task["description"])
    
    await asyncio.sleep(2) # Simulate agent work
    return {"status": "success", "commits": 1}

@activity.defn
async def run_playwright_e2e_tests(worktree_path: str) -> bool:
    activity.logger.info(f"Running Playwright tests in {worktree_path}")
    # Execute npm run test:e2e or similar
    # For now, return True
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
