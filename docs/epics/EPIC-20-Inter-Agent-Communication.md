# EPIC-20: Inter-Agent Communication & Handoff Protocol (IAC)

## 1. Executive Summary
Currently, our autonomous factory treats agents as isolated silos. The `SprintManager` creates issues, the `Worker` writes code, and the `PRReviewer` (EPIC-16) evaluates code. However, they cannot "talk" to each other. If a `Worker` is struggling with a UI component, it cannot ask the `PRReviewer` for early feedback. If the `SprintManager` writes an impossible spec, the `Worker` crashes rather than pushing back on the `SprintManager` to clarify the requirements. 

To achieve true autonomy, we must implement an **Inter-Agent Communication (IAC) Protocol** on top of Temporal. This allows agents to coordinate, prevent redundant overlap, and execute seamless handoffs.

## 2. Core Capabilities
1. **Asynchronous Messaging (The PubSub Bus):** 
   - Agents must be able to send asynchronous messages to a central Temporal Signal bus.
   - Example: A `Worker` agent broadcasts: "I am modifying the `Boat` DB schema." Other `Worker` agents listening on that bus know to avoid touching the `Boat` schema until the lock is released, avoiding merge conflicts.

2. **Synchronous Handoffs & Callbacks:**
   - A `Worker` must be able to pause its OpenHands loop and invoke another agent to perform a specialized sub-task.
   - Example: A `Worker` finishes a feature but is unsure if the UI meets the aesthetic guidelines. It invokes the `UI Tester Agent` via Temporal, waits for the critique matrix, and then resumes coding.

3. **Escalation & Pushback (The Feedback Loop):**
   - A `Worker` must be able to explicitly reject an issue back to the `SprintManager`.
   - Example: "This issue requires modifying a deprecated API. Please re-write the Specification."

## 3. Technical Architecture

### 3.1 The Temporal Signal Bus
We will update `api/src/irc_data/temporal/orchestrator/workflows.py` to add a dedicated `AgentCommunicationWorkflow`.
- It acts as a routing hub.
- Agents use a new OpenHands Python tool: `send_agent_message(recipient_role: str, message: str, context_payload: dict)`.
- Temporal handles routing the message to the correct running workflow via Temporal Signals (`@workflow.signal`).

### 3.2 OpenHands `enable_sub_agents` 
We will leverage OpenHands SDK's native sub-agent capabilities.
- When configuring the Worker agent in `activities.py` (`run_openhands_agent`), we will set `enable_sub_agents=True`.
- This allows a "Lead Developer Worker" to spawn a "Researcher Subagent" to read documentation while it continues to write code.

### 3.3 The Handoff Handshake
When an agent finishes its primary task, it executes the `handoff_task` tool.
- `handoff_task(next_role: str, payload: dict)`: This writes the final artifact (e.g., a spec or a PR diff) to the Corporate Memory Index (EPIC-19) and signals Temporal to trigger the next workflow in the pipeline.

## 4. Acceptance Criteria
- [ ] Temporal `AgentCommunicationWorkflow` is active and successfully routes signals between running workflows.
- [ ] Agents are equipped with `send_agent_message` and `handoff_task` custom OpenHands tools.
- [ ] `run_openhands_agent` is updated to allow sub-agent spawning for isolated research tasks.
- [ ] A Worker agent can successfully reject a flawed issue back to the SprintManager via the Notion API and Signal bus.
