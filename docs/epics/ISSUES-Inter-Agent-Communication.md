# ISSUES: EPIC-20 (Inter-Agent Communication & Handoff Protocol)

The following granular engineering issues must be executed by the worker agents to build the Inter-Agent Communication (IAC) protocol on top of the Temporal Orchestrator.

## ISSUE-20.1: Implement Temporal Signal Hub (`AgentCommunicationWorkflow`)
**Description:** 
We need a central message broker for agents. Create a new workflow in `api/src/irc_data/temporal/orchestrator/workflows.py` called `AgentCommunicationWorkflow`.
- This workflow should run indefinitely (`while True`) listening for incoming Temporal Signals (`@workflow.signal`).
- When it receives a signal (e.g., `broadcast_schema_lock`, `request_pr_review`), it must log the event and route the signal to the correct active agent workflow.
- Ensure the workflow handles dead-letter routing if the target agent workflow is not found or has already terminated.
**Acceptance Criteria:**
- `AgentCommunicationWorkflow` is registered and starts on worker initialization.
- It can successfully receive and log a `ping` signal from an external python script.

## ISSUE-20.2: Build OpenHands Communication Tools (`agent_tools.py`)
**Description:**
The OpenHands Agents need custom python tools to send signals to the Temporal Hub. Create `api/src/irc_data/temporal/orchestrator/agent_tools.py`.
- Implement `send_agent_message(recipient_role: str, message: str, context_payload: dict)`. This function will use the Temporal Client to send a signal to the `AgentCommunicationWorkflow`.
- Implement `handoff_task(next_role: str, payload: dict)`. This function is used when an agent finishes its work and needs to pass the baton (e.g., Worker -> PRReviewer).
**Blocked By:** ISSUE-20.1

## ISSUE-20.3: Implement the "Reject to SprintManager" Feedback Loop
**Description:**
A Worker Agent must not silently fail if a specification is impossible to build. 
- Update the `run_openhands_agent` prompt in `activities.py` to instruct the agent to use a new tool: `reject_issue(reason: str)`.
- The `reject_issue` tool must update the original Notion Issue status back to `Needs Spec`, append the `reason` as a comment on the Notion Epic, and throw an `ApplicationError("Issue Rejected")` to elegantly terminate the worker workflow.
**Blocked By:** ISSUE-20.2

## ISSUE-20.4: Enable OpenHands Native Sub-Agents
**Description:**
Workers should be able to delegate research tasks to sub-agents to avoid polluting their own context window with massive file reads.
- Modify the `run_openhands_agent` activity in `activities.py`.
- Update `tools = get_default_tools(enable_browser=False, enable_sub_agents=True)`.
- Update the system prompt to explicitly educate the Worker that it has the ability to spawn sub-agents for reading massive docs or searching the web.
**Blocked By:** None (Can be implemented immediately).
