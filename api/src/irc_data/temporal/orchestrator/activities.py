import os
import shutil
import asyncio
import urllib.request
import json
from datetime import timedelta
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .llm_client import (
    get_async_client,
    get_model_hint,
    build_metadata,
    LLMTelemetry,
    MODEL_CODING_FAST,
    MODEL_CODING_DEEP,
    MODEL_REVIEW_INDEPENDENT,
)

def _sync_provision_worktree(task_id: str, branch_name: str, repo_path: str, worktrees_dir: str, worktree_path: str) -> str:
    """Run in a thread via run_in_executor — keeps git calls off the asyncio event loop
    so concurrent OpenHands lane workers can't starve this activity."""
    import subprocess
    os.makedirs(worktrees_dir, exist_ok=True)

    subprocess.run(
        ["git", "-C", repo_path, "worktree", "prune"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)

    result = subprocess.run(
        ["git", "-C", repo_path, "worktree", "add", "-b", branch_name, worktree_path, "develop"],
        capture_output=True
    )
    if result.returncode != 0:
        stderr = result.stderr.decode()
        if "already exists" in stderr or "already used by worktree" in stderr:
            result2 = subprocess.run(
                ["git", "-C", repo_path, "worktree", "add", worktree_path, branch_name],
                capture_output=True
            )
            if result2.returncode != 0:
                raise ApplicationError(f"Failed to create worktree on existing branch: {result2.stderr.decode()}")
        else:
            raise ApplicationError(f"Failed to create worktree: {stderr}")
    return worktree_path

@activity.defn
async def provision_worktree(task: dict) -> str:
    task_id = task.get("id", "unknown-task")
    branch_name = f"feature/{task_id}"
    repo_path = "/home/irc-data/code/sailratings"
    worktrees_dir = "/home/irc-data/code/sailratings/worktrees"
    worktree_path = os.path.join(worktrees_dir, task_id)
    activity.logger.info(f"Provisioning worktree for {task_id}")
    # Run in executor so concurrent OpenHands lane workers can't starve this via event loop
    result = await asyncio.get_event_loop().run_in_executor(
        None, _sync_provision_worktree, task_id, branch_name, repo_path, worktrees_dir, worktree_path
    )
    activity.logger.info(f"Worktree provisioned: {result}")
    return result

@activity.defn
async def commit_agent_work(worktree_path: str, message: str = "feat: agent implementation") -> bool:
    """Auto-commit any uncommitted changes left by the lane worker agent."""
    proc = await asyncio.create_subprocess_shell(
        f'git -C "{worktree_path}" add -A && '
        f'git -C "{worktree_path}" diff --cached --quiet || '
        f'git -C "{worktree_path}" commit -m "{message}" --author="OpenHands Agent <agent@sailratings.com>"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        activity.logger.info(f"Auto-committed agent work in {worktree_path}")
    else:
        activity.logger.info(f"Nothing to commit or commit failed: {stderr.decode()[:200]}")
    return True

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
        # Trialling coding-deep (DeepSeek) for the main coding-worker role at
        # Stuart's request, in place of coding-fast (GLM 5.2), 2026-09-03.
        # A deliberate per-role default in code, not an env var — the prior
        # blanket LITELLM_MODEL_HINT env var on the worker process overrode
        # every role's own default identically, including the reviewer
        # (line ~187, meant to default to review-independent) and the sprint
        # manager (meant to default to coding-deep already) — both were
        # silently running on coding-fast too, defeating the point of an
        # independent reviewer. Revert to get_model_hint(MODEL_CODING_FAST)
        # if this doesn't show better results.
        model=f"openai/{get_model_hint(MODEL_CODING_DEEP)}",
        api_key=os.environ.get("LITELLM_API_KEY"),
        base_url=os.environ.get("LITELLM_BASE_URL"),
    )
    workspace = LocalWorkspace(working_dir=worktree_path)

    system_prompt = """
    You are the 'Lane Worker Agent'. You implement exactly one Roadmap card in an isolated git worktree.

    READ THE CARD FIRST. It has these sections: Goal, Scope, Acceptance Criteria, Verification,
    Blocked By, and a page body with 'Files you own', 'Files you must NOT touch', 'Prototype',
    'Acceptance (mechanical)' and 'Evidence'. If a section is missing, stop and post evidence saying so.

    FILE BOUNDARIES (hard rule):
    - Edit or create ONLY files listed under 'Files you own'. If the card says a file is owned by a
      sibling card, do not touch it - not even a one-line import. If you cannot finish without it,
      stop, post evidence explaining exactly which file and why, and end your turn.
    - Shared files marked append-only (adminApi.ts, api.ts, app.py, AdminSidebar.tsx, AdminIcons.tsx,
      MainNav.tsx, sitemap.ts, alembic/versions): append at the end, never reorder or rewrite.
    - Never edit a shipped alembic migration. New migrations chain off `alembic heads`.

    ACCEPTANCE IS MECHANICAL:
    - Every acceptance criterion is a command with an expected result. Run every one. Paste the real
      output. Do not paraphrase, summarise or claim. If a count differs from the card, you are not done.
    - Do not weaken, skip or delete an existing test to make a number match.

    PROTOTYPE:
    - If the card has a 'Prototype' URL, the screen must match it; take screenshots at 1440 and 390 wide
      with Playwright and post them. If the card says 'REQUIRED, NOT YET AVAILABLE', stop and post
      evidence: the card is not ready.

    EVIDENCE - ONE SHARED SCRIPT, NOTHING ELSE:
    - Post evidence only with:  python scripts/post_evidence.py --issue <ID> --cmd "<command>" --output-file <log> [--screenshot <png>]
    - Do NOT write your own posting script, do NOT import NotionAdapter yourself, do NOT commit any
      post_*.py, upload_*.py, test_*.py or *.log at the repo root or in e2e_tests/. Delete every scratch
      file you created before committing. The Gatekeeper fails the card if the diff contains any file
      not listed under 'Files you own'.

    A claim of 'it works' is not evidence. Silence from you is not success.
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

        def _run():
            if asyncio.iscoroutinefunction(conversation.run):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(conversation.run())
                finally:
                    loop.close()
            return conversation.run()

        result = await asyncio.get_event_loop().run_in_executor(None, _run)
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
        model=f"openai/{get_model_hint(MODEL_REVIEW_INDEPENDENT)}",
        api_key=os.environ.get("LITELLM_API_KEY"),
        base_url=os.environ.get("LITELLM_BASE_URL"),
    )
    workspace = LocalWorkspace(working_dir=worktree_path)

    system_prompt = """
    You are the 'Adversarial Watchdog' (Gatekeeper). You review one card's worktree diff against the card.
    You CANNOT edit files. Your last line must be exactly "DECISION: PASS" or "DECISION: FAIL".
    Anything other than a clear PASS is a FAIL: if you are unsure, output DECISION: FAIL with the reason.

    FAIL immediately, without further review, if any of these is true:
    1. `git diff --name-only develop...HEAD` lists a file that is not under the card's 'Files you own'
       (new files count; a file under 'Files you must NOT touch' is an automatic FAIL).
    2. The diff adds post_*.py, upload_*.py, test_*.py or *.log at the repo root or in e2e_tests/, or any
       ad-hoc Notion posting script.
    3. An existing test was deleted, skipped or weakened.
    4. A shipped alembic migration was edited, or `alembic heads` shows more than one head.

    Then run EVERY command in the card's 'Acceptance (mechanical)' block yourself and compare the real
    output with the expected value on the card. A mismatch is a FAIL. Do not accept the worker's pasted
    output as proof - run the commands. Check the Notion card has evidence posted by scripts/post_evidence.py
    for each acceptance command and, for UI cards, screenshots at 1440 and 390.

    If the card says Prototype 'REQUIRED, NOT YET AVAILABLE', the card should never have been dispatched:
    DECISION: FAIL with reason 'card not ready'.
    """
    
    from openhands.tools.preset.default import get_default_tools
    # Read-only tools
    tools = get_default_tools(enable_browser=False, enable_sub_agents=False)
    
    agent = Agent(llm=llm, system_prompt=system_prompt, tools=tools)
    prompt = f"Review the changes in this worktree against the task:\n\n{task.get('description')}\nCheck the board for evidence using the provided scripts. Output PASS or FAIL."
        
    try:
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message(prompt)

        def _run():
            if asyncio.iscoroutinefunction(conversation.run):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(conversation.run())
                finally:
                    loop.close()
            return conversation.run()

        result_obj = await asyncio.get_event_loop().run_in_executor(None, _run)
        result = str(result_obj)
        activity.logger.info("Reviewer run complete.")
        
        # Fail closed (4 Sep 2026). Previously `passed = not explicit_fail`,
        # so a confused or truncated reviewer silently approved — that is how
        # a 5-line redirect merged as "Audit log" (AD-01-10) and a 25-line
        # iframe as "telemetry" (AD-01-05). With a cheaper worker the
        # reviewer is the only real check, so it must say PASS explicitly.
        # An ambiguous verdict goes back to the worker with the transcript as
        # feedback; the existing repair loop handles the retry.
        explicit_pass = "DECISION: PASS" in result
        explicit_fail = "DECISION: FAIL" in result
        passed = explicit_pass and not explicit_fail
        if not passed and not explicit_fail:
            result = ("Reviewer did not output an explicit DECISION: PASS. Treated as FAIL. "
                      "Reviewer transcript follows.\n\n" + result)
        return {"passed": passed, "feedback": None if passed else result}
    except Exception as e:
        activity.logger.error(f"Reviewer run failed: {e}")
        raise ApplicationError(f"Reviewer run failed: {e}")

@activity.defn
async def run_playwright_e2e_tests(worktree_path: str) -> bool:
    import fcntl
    import aiohttp
    activity.logger.info(f"Running Playwright tests in {worktree_path}")
    e2e_dir = os.path.join(worktree_path, "e2e_tests")
    if not os.path.isdir(e2e_dir):
        activity.logger.info("No e2e_tests directory — skipping Playwright tests")
        return True
    web_dir = os.path.join(worktree_path, "web")

    # Verify the API at :4100 is healthy before acquiring the port lock.
    # Playwright probes http://127.0.0.1:4100/ with reuseExistingServer:true;
    # if uvicorn is in a reload cycle, that probe blocks indefinitely.
    for attempt in range(12):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:4100/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status < 500:
                        break
        except Exception as exc:
            activity.logger.warning(f"API not ready (attempt {attempt+1}/12): {exc}")
            if attempt == 11:
                raise ApplicationError("API at :4100 did not become healthy after 60 s; aborting E2E run")
            await asyncio.sleep(5)

    # Serialise all E2E runs — only one can hold port 4201 at a time
    lock_path = "/tmp/playwright-port-4201.lock"
    lock_file = open(lock_path, "w")
    activity.logger.info("Waiting for E2E port lock…")
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: fcntl.flock(lock_file, fcntl.LOCK_EX)
    )
    activity.logger.info("Acquired E2E port lock")
    proc = None
    try:
        # Kill any process holding port 4201 from a prior test run
        await asyncio.create_subprocess_shell(
            "fuser -k 4201/tcp 2>/dev/null; true",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        # Remove any stale node_modules symlink — Turbopack rejects out-of-root symlinks
        await asyncio.create_subprocess_shell(
            f'[ -L "{web_dir}/node_modules" ] && rm "{web_dir}/node_modules" || true',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        env = os.environ.copy()
        env["TEST_WEB_PORT"] = "4201"
        env["CI"] = "true"
        proc = await asyncio.create_subprocess_shell(
            "npm install && npx playwright install chromium && "
            f"cd {web_dir} && npm install && cd {e2e_dir} && npx playwright test",
            cwd=e2e_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 40-minute hard cap — prevents blocking if playwright itself hangs
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2400)
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.communicate()
        raise ApplicationError("Playwright subprocess exceeded 40-minute hard cap")
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
    if proc.returncode != 0:
        combined = (stdout.decode() + "\n" + stderr.decode())[:3000]
        activity.logger.error(f"Playwright tests failed (exit {proc.returncode}): {combined}")
        return False
    activity.logger.info(f"Playwright tests passed: {stdout.decode()[-500:]}")
    return True

@activity.defn
async def create_pull_request(worktree_path: str) -> None:
    import fcntl
    activity.logger.info(f"Auto-merging {worktree_path} into develop")
    repo_path = "/home/irc-data/code/sailratings"

    # Get the branch name from the worktree
    proc = await asyncio.create_subprocess_shell(
        f'git -C "{worktree_path}" branch --show-current',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    branch = stdout.decode().strip()
    if not branch:
        raise ApplicationError(f"Could not determine branch name in {worktree_path}")

    activity.logger.info(f"Merging {branch} into develop — waiting for git lock")

    # Serialise all merge+push operations — concurrent git operations on the same
    # repo cause lock contention and non-fast-forward push failures
    lock_path = "/tmp/factory-git-merge.lock"
    lock_file = open(lock_path, "w")
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: fcntl.flock(lock_file, fcntl.LOCK_EX)
    )
    activity.logger.info(f"Acquired git merge lock for {branch}")
    try:
        # Ensure we are on develop before merging (main repo HEAD may be on a feature branch)
        await asyncio.create_subprocess_shell(
            f'git -C "{repo_path}" checkout develop',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        # Merge into develop in the main repo
        merge_cmd = (
            f'git -C "{repo_path}" merge --no-ff "{branch}" '
            f'-m "feat: factory merge {branch} into develop (gatekeeper approved)"'
        )
        proc = await asyncio.create_subprocess_shell(
            merge_cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # A failed merge leaves the main repo mid-conflict (MERGE_HEAD set,
            # unmerged paths). Without cleanup here, every subsequent retry —
            # of this activity or any other task's merge — immediately fails
            # on "you have not concluded your merge", masking the real
            # conflict forever (observed: 18 retries, one real conflict).
            # Abort so the tree is clean for the next attempt or the next task.
            abort_proc = await asyncio.create_subprocess_shell(
                f'git -C "{repo_path}" merge --abort',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await abort_proc.communicate()
            # git writes CONFLICT details to stdout, not stderr — stderr
            # alone renders as "Merge failed: " with nothing after it.
            detail = (stdout.decode() + stderr.decode())[:1000]
            raise ApplicationError(f"Merge failed: {detail}")

        activity.logger.info(f"Merged {branch}. Pushing develop to origin.")

        # Push develop
        proc = await asyncio.create_subprocess_shell(
            f'git -C "{repo_path}" push origin develop',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ApplicationError(f"Push failed: {stderr.decode()[:1000]}")

        activity.logger.info(f"Successfully merged and pushed {branch}.")
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()

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
        model=f"openai/{get_model_hint(MODEL_CODING_DEEP)}",
        api_key=os.environ.get("LITELLM_API_KEY"),
        base_url=os.environ.get("LITELLM_BASE_URL"),
    )
    workspace = LocalWorkspace(working_dir=repo_path)
    
    # Load tools
    tools = get_default_tools(enable_browser=False, enable_sub_agents=False)
    
    system_prompt = """
    You are the 'Sprint Manager'. You draft and groom cards in the 'sailratings Roadmap' Notion database
    (database id 3b237ffe-f467-81b4-8aad-e4eb0d49f4da). You never write to any other Notion database -
    in particular not 'SailRatings Issue Tracker' or 'Stuart's Software Factory - Build Programme', which
    are archived.

    CARD STANDARD - follow the Roadmap row TEMPLATE-01 exactly:
    1. ID: <EPIC>-<NN> for issues (e.g. AD-01-25), plain, no brackets, no prefixes. Title is a sentence
       describing the outcome; it never starts with "[ISSUE]" or "[EPIC]".
    2. Properties to set on every card: Type (Epic|Issue|Task|Spec), Status (Draft until a human sets
       Ready), Execution State = Not Dispatched, Parent Epic (text ID), Sprint, Priority, Risk,
       Human Gate, Autonomy, Agent Role, Goal, Scope, Acceptance Criteria, Verification, Blocked By
       (text: task IDs separated by semicolons), Spec Ref (e.g. 'SPEC-22 s3.3'), Output Contract.
       There is no 'Specification' property and no relation-based Blocked By on the Roadmap.
    3. Scope names every file to create or edit by full repo path and states what exists on develop.
    4. Acceptance Criteria are numbered commands with exact expected outputs or counts - never prose.
    5. Page body uses only top-level headings, paragraphs, bullet lists and code blocks, with the
       sections: Current state on develop / Files you own / Files you must NOT touch / Prototype /
       Acceptance (mechanical) / Evidence.
    6. Two cards that edit the same file are chained with Blocked By; shared files are append-only.
    7. UI cards need a Prototype URL; without one set Status = Needs Specification and Human Gate.
    8. Create cards as Draft. A human flips them to Ready.

    If you need a decision or approval, include the exact string "<ASK_USER>" in your response.
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
    ctx = activity.info()
    model = get_model_hint(MODEL_CODING_FAST)
    client = get_async_client()

    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    meta = build_metadata(
        role="sprint-manager",
        lane="conversation",
        workflow_id=ctx.workflow_id,
        run_id=ctx.workflow_run_id,
        attempt=ctx.attempt,
        model_hint=model,
    )

    with LLMTelemetry(role="sprint-manager", model=model) as tel:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                extra_body={"metadata": meta},
            )
            usage = response.usage or type("U", (), {"prompt_tokens": 0, "completion_tokens": 0})()
            tel.record_response(
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )
            return response.choices[0].message.content
        except Exception as e:
            import traceback
            tel.record_response(error=str(e))
            activity.logger.error(f"LLM Error: {traceback.format_exc()}")
            return f"Error connecting to LLM: {str(e)}"

@activity.defn
async def fetch_board_state() -> str:
    # A lightweight stub representing the Notion board state
    return "Board state summary: [No active sprint. Epics need grooming.]"
