# Epic: Dependency-Aware Scheduling for OpenHands Orchestrator

## Problem Statement
Currently, the `notion_poller.py` script queries the Notion database for all issues with the status "Ready for Agent" and throws them into the Temporal queue simultaneously. When a multi-issue Epic is planned, this causes all tasks to run in parallel, which is problematic when tasks have dependencies (e.g. "Build Database Schema" must run before "Build API Endpoint"). We need the orchestrator to respect task execution order by evaluating the new `Blocked By` and `Blocking` relations in the Notion database.

## Technical Approach
1. **Schema Awareness**: We have added a `Blocked By` relation property (and its inverse, `Blocking`) to the Issues database in Notion.
2. **Poller Update**: Update `/home/irc-data/code/sailratings/api/src/irc_data/temporal/orchestrator/notion_poller.py` to:
   - Expand its Notion API query to include the `Blocked By` property in the response.
   - Filter the "Ready for Agent" issues: an issue should only be queued to Temporal if the `Blocked By` relation array is empty, OR if all the referenced issues in that array have a status of "Done" or "Merged".
   - This requires querying the status of the parent issues listed in the `Blocked By` field before executing the workflow.
3. **Queue Logic**: Leave the Temporal `TaskExecutionWorkflow` alone. It shouldn't care about dependencies; the polling trigger should handle the dependency resolution and only schedule tasks when they are truly unblocked.

## Acceptance Criteria
- [ ] `notion_poller.py` successfully retrieves the `Blocked By` property for issues in "Ready for Agent".
- [ ] If an issue is "Ready for Agent" but has incomplete dependencies in its `Blocked By` field, it is skipped and logged as "blocked".
- [ ] If an issue is "Ready for Agent" and all issues in its `Blocked By` field are "Done" or "Merged", it is dispatched to Temporal.
- [ ] If an issue is "Ready for Agent" and its `Blocked By` field is empty, it is dispatched to Temporal.
