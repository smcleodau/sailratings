
import os
import httpx

# Get the Notion token from the environment variable
NOTION_TOKEN = os.environ.get("SAILRATINGS_NOTION_TOKEN")
DATABASE_ID = "3a937ffe-f467-81b3-b888-d873def19261"

# Define the headers for the Notion API
headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Get the database properties
with httpx.Client() as client:
    response = client.get(f"https://api.notion.com/v1/databases/{DATABASE_ID}", headers=headers)
    if response.status_code == 200:
        print(response.json()["properties"].keys())
    else:
        print(f"Failed to get database properties: {response.text}")
