import os
import urllib.request
import json
import ssl

def main():
    token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
    if not token:
        print("Error: SAILRATINGS_NOTION_TOKEN not set")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    ctx = ssl.create_default_context()
    
    # SailRatings Issue Tracker DB
    db_id = "3a937ffe-f467-81b3-b888-d873def19261" 

    # 1. Create the parent Epic
    epic_data = {
        "parent": {"database_id": db_id},
        "properties": {
            "Issue": {"title": [{"text": {"content": "EPIC-07 Dependency-Aware Scheduling"}}]},
            "Status": {"select": {"name": "To Do"}},
            "type": {"select": {"name": "Epic"}}
        }
    }

    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(epic_data).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            epic = json.loads(response.read())
            epic_id = epic["id"]
            print(f"Created Epic: {epic_id}")
    except Exception as e:
        print(f"Error creating epic: {e}")
        try:
            print(e.read().decode())
        except:
            pass
        return

    # 2. Create the Issue and link it to the Epic
    issue_data = {
        "parent": {"database_id": db_id},
        "properties": {
            "Issue": {"title": [{"text": {"content": "[EPIC-07] Update notion_poller.py to evaluate Blocked By relations"}}]},
            "Status": {"select": {"name": "Ready for Agent"}},
            "type": {"select": {"name": "Issue"}},
            "Parent Epic": {"relation": [{"id": epic_id}]}
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "Update `/home/irc-data/code/sailratings/api/src/irc_data/temporal/orchestrator/notion_poller.py` to evaluate the new `Blocked By` relation. When querying for 'Ready for Agent' tasks, iterate through any relations in the `Blocked By` field and query their statuses. If any of the blocking issues do not have a status of 'Done' or 'Merged', skip the task. Only trigger the Temporal TaskExecutionWorkflow if the `Blocked By` list is empty, or if all blocking issues are complete. See `docs/epics/EPIC-07-Dependency-Aware-Scheduling.md` for context."
                            }
                        }
                    ]
                }
            }
        ]
    }

    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(issue_data).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            issue = json.loads(response.read())
            print(f"Created Issue: {issue['id']}")
    except Exception as e:
        print(f"Error creating issue: {e}")

if __name__ == "__main__":
    main()
