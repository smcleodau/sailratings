import os
import json
import httpx

def main():
    token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
    if not token:
        print("SAILRATINGS_NOTION_TOKEN missing")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    db_id = "3a937ffe-f467-81b3-b888-d873def19261"

    # 1. Update existing issues to Done
    items_to_complete = [
        ("3a937ffe-f467-81d4-932b-d0fd71ed2b73", "Integrate Clerk Authentication in Next.js"),
        ("3a937ffe-f467-817a-b139-ff474c8c255e", "Design System Implementation for Reports"),
    ]

    for item_id, name in items_to_complete:
        url = f"https://api.notion.com/v1/pages/{item_id}"
        payload = {
            "properties": {
                "Status": {"select": {"name": "Done"}}
            }
        }
        res = httpx.patch(url, headers=headers, json=payload)
        if res.status_code == 200:
            print(f"Updated '{name}' ({item_id}) -> Done")
        else:
            print(f"Failed to update '{name}':", res.status_code, res.text)

    # 2. Create the Paper Design System Spec item in Notion
    print("Creating Paper Design System Spec page in Notion...")
    create_url = "https://api.notion.com/v1/pages"
    
    spec_content = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "SailRatings 'Paper' Design System Standard"}}]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text", 
                    "text": {"content": "The official SailRatings brand identity is the 'Paper' (Dossier) design system. It reflects a high-end physical surveyor's report or newspaper sub-editor's bench."}
                }]
            }
        },
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": "Background: Warm Cream / Sand (#F4F1E8) with subtle paper grain texture (.dossier-paper)"}}]
            }
        },
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": "Card Containers: Crisp White Paper (#FFFFFF) with paper border (#D1C8B7) and subtle drop shadow"}}]
            }
        },
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": "Primary Brand Color: Deep Navy (#0A2240) for titles, headers, and primary CTAs"}}]
            }
        },
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": "Secondary Accent Color: Metallic Brass / Gold (#C29B61) for rules, highlights, and status badges"}}]
            }
        },
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": "Typography Stack: Söhne font family (heading-display, body-text) and Roboto Mono (data-mono) for ratings and credentials"}}]
            }
        }
    ]

    create_payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Issue": {
                "title": [{"text": {"content": "[SPEC] SailRatings Paper Design System Guidelines"}}]
            },
            "Status": {
                "select": {"name": "Done"}
            },
            "type": {
                "select": {"name": "Issue"}
            }
        },
        "children": spec_content
    }

    res_create = httpx.post(create_url, headers=headers, json=create_payload)
    if res_create.status_code == 200:
        new_page_id = res_create.json()["id"]
        print(f"Successfully created Paper Design System Spec in Notion! (Page ID: {new_page_id})")
    else:
        print("Failed to create Spec in Notion:", res_create.status_code, res_create.text)

if __name__ == "__main__":
    main()
