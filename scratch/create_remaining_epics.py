import os
import urllib.request
import json
import ssl

def create_epic(token, db_id, ctx, epic_number, title):
    epic_data = {
        "parent": {"database_id": db_id},
        "properties": {
            "Issue": {"title": [{"text": {"content": f"EPIC-{epic_number} {title}"}}]},
            "Status": {"select": {"name": "To Do"}},
            "type": {"select": {"name": "Epic"}}
        }
    }
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(epic_data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, context=ctx) as response:
        epic = json.loads(response.read())
        print(f"Created Epic {epic_number}: {epic['id']}")
        return epic["id"]

def create_issue(token, db_id, ctx, epic_id, epic_number, title, desc):
    issue_data = {
        "parent": {"database_id": db_id},
        "properties": {
            "Issue": {"title": [{"text": {"content": f"[EPIC-{epic_number}] {title}"}}]},
            "Status": {"select": {"name": "Ready for Agent"}},
            "type": {"select": {"name": "Issue"}},
            "Parent Epic": {"relation": [{"id": epic_id}]}
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": desc}}]
                }
            }
        ]
    }
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(issue_data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, context=ctx) as response:
        issue = json.loads(response.read())
        print(f"  Created Issue: {issue['id']}")
        return issue["id"]

def main():
    token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
    if not token:
        print("Error: SAILRATINGS_NOTION_TOKEN not set")
        return

    ctx = ssl.create_default_context()
    
    db_id = "3a937ffe-f467-81b3-b888-d873def19261"
    
    epics = [
        {
            "number": "08",
            "title": "Sailing AI Domain Expert (System Prompts & Context)",
            "issues": [
                {
                    "title": "Research and document sailing domain knowledge",
                    "desc": "Research IRC, ORC, TCC ratings, design classes, sailing terminology, and data models. Create a consolidated `docs/domain/sailing-knowledge.md` file that models can ingest."
                },
                {
                    "title": "Draft Sailing LLM System Prompts",
                    "desc": "Using the domain knowledge, draft rigorous system prompts for agents and user-facing LLMs so they have 'serious sailing chops' and can answer complex questions about boat configurations and race data."
                }
            ]
        },
        {
            "number": "09",
            "title": "Authentication & User Accounts",
            "issues": [
                {
                    "title": "Integrate Clerk Authentication in Next.js",
                    "desc": "Install and configure Clerk in `web/`. Build the login, signup, and account management UI pages using the Paper design system."
                },
                {
                    "title": "Sync Clerk Users to Backend Postgres DB",
                    "desc": "Create a `users` table via Alembic. Create API endpoints to receive Clerk Webhooks and automatically provision/update users in the backend so they can be linked to boat portfolios."
                }
            ]
        },
        {
            "number": "10",
            "title": "Subscription & Payment Engine",
            "issues": [
                {
                    "title": "Stripe Integration: Recurring Subscriptions",
                    "desc": "Expand the current Stripe integration (which only handles $99 one-offs) to fully support $290/yr recurring subscriptions. Update the frontend pricing tables and checkout rails."
                },
                {
                    "title": "End-to-End Payment Testing",
                    "desc": "Write comprehensive automated tests using Playwright and Stripe test cards to ensure the entire checkout flow works flawlessly from signup to payment to portfolio access."
                }
            ]
        },
        {
            "number": "11",
            "title": "Sailing Intelligence Reports (New UI)",
            "issues": [
                {
                    "title": "Design System Implementation for Reports",
                    "desc": "Build the core UI components for the Living Report in Next.js. It needs to look AMAZING. Use modern, dynamic CSS to showcase boat data, stats, and graphs."
                },
                {
                    "title": "Connect Report UI to Backend APIs",
                    "desc": "Fetch live data from the backend to populate the Boat Report pages. Handle loading states, errors, and empty data gracefully while maintaining a premium aesthetic."
                }
            ]
        }
    ]

    for epic_plan in epics:
        try:
            epic_id = create_epic(token, db_id, ctx, epic_plan["number"], epic_plan["title"])
            for issue_plan in epic_plan["issues"]:
                create_issue(token, db_id, ctx, epic_id, epic_plan["number"], issue_plan["title"], issue_plan["desc"])
        except urllib.error.HTTPError as e:
            print(f"Failed on Epic {epic_plan['number']}: {e} - {e.read().decode('utf-8')}")
        except Exception as e:
            print(f"Failed on Epic {epic_plan['number']}: {e}")

if __name__ == "__main__":
    main()
