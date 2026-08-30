import os
import httpx

NOTION_API_KEY = os.environ["SAILRATINGS_NOTION_TOKEN"]
DATABASE_ID = "3a937ffe-f467-81b3-b888-d873def19261"
EPIC_PAGE_ID = "3a937ffe-f467-8183-88df-e4cb1c575df7"

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

issues = [
    {"title": "[EPIC-05] Setup Basic Admin UI and Authentication", "epic": EPIC_PAGE_ID},
    {"title": "[EPIC-05] Create Backend for Ambiguity Resolution", "epic": EPIC_PAGE_ID},
    {"title": "[EPIC-05] Build Frontend for Ambiguity Resolution", "epic": EPIC_PAGE_ID},
    {"title": "[EPIC-05] Implement Merge/Keep Separate Logic", "epic": EPIC_PAGE_ID},
    {"title": "[EPIC-05] Create Backend for Pipeline Monitoring", "epic": EPIC_PAGE_ID},
    {"title": "[EPIC-05] Build Frontend for Pipeline Monitoring", "epic": EPIC_PAGE_ID},
]

created_issues = []

with httpx.Client() as client:
    for issue in issues:
        data = {
            "parent": {"database_id": DATABASE_ID},
            "properties": {
                "Name": {"title": [{"text": {"content": issue["title"]}}]},
                "Status": {"select": {"name": "Ready for Agent"}},
                "Type": {"select": {"name": "Issue"}},
                "Epic": {"relation": [{"id": issue["epic"]}]},
            },
        }
        response = client.post("https://api.notion.com/v1/pages", headers=headers, json=data)
        if response.status_code == 200:
            created_issue = response.json()
            created_issues.append(created_issue)
            print(f"Successfully created issue: {created_issue['url']}")
        else:
            print(f"Error creating issue: {response.text}")

    if created_issues:
        issue_list_content = ""
        for issue in created_issues:
            issue_list_content += f"- [{issue['properties']['Name']['title'][0]['text']['content']}]({issue['url']})\\n"

        update_data = {
            "children": [
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": "Related Issues"}}]
                    },
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": issue_list_content,
                                },
                            }
                        ]
                    },
                },
            ]
        }
        response = client.patch(
            f"https://api.notion.com/v1/blocks/{EPIC_PAGE_ID}/children",
            headers=headers,
            json=update_data,
        )
        if response.status_code == 200:
            print("Successfully updated the epic page with the list of created issues.")
        else:
            print(f"Error updating the epic page: {response.text}")
