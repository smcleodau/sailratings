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
    # This is a stub for the OpenHands SDK integration
    # It will initialize a DockerWorkspace mapped to worktree_path
    # and run the agent loop
    
    # from openhands.sdk.agent import Agent
    # from openhands.sdk.workspace import DockerWorkspace
    
    # workspace = DockerWorkspace(worktree_path)
    # agent = Agent(workspace=workspace, max_iterations=30, budget=5.0)
    # result = await agent.run(task["description"])
    # return result
    
    activity.logger.info(f"Running OpenHands agent in {worktree_path} for task {task.get('id')}")
    await asyncio.sleep(5) # Simulate agent work
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
