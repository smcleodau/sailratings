
import os
import httpx

# Get the Notion token from the environment variable
NOTION_TOKEN = os.environ.get("SAILRATINGS_NOTION_TOKEN")
DATABASE_ID = "3a937ffe-f467-81b3-b888-d873def19261"
EPIC_PAGE_ID = "3a937ffe-f467-8183-88df-e4cb1c575df7"

# Define the headers for the Notion API
headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Define the issue to be created
issue = {
    "parent": {"database_id": DATABASE_ID},
    "properties": {
        "Issue": {"title": [{"text": {"content": "[EPIC-05] API: Create Admin Dashboard Endpoints"}}]},
        "Status": {"select": {"name": "Ready for Agent"}},
        "type": {"select": {"name": "Issue"}},
        "Parent Epic": {"relation": [{"id": EPIC_PAGE_ID}]},
    },
}

# Create the issue in Notion
with httpx.Client() as client:
    response = client.post("https://api.notion.com/v1/pages", headers=headers, json=issue)
    if response.status_code == 200:
        print(f"Successfully created issue: {response.json()['url']}")
    else:
        print(f"Failed to create issue: {response.text}")
