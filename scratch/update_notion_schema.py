import os
import urllib.request
import json
import ssl

def main():
    token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
    if not token:
        print("Error: SAILRATINGS_NOTION_TOKEN not set")
        return

    # Use a specific known issue to find its database
    issue_id = "3a937ffe-f467-81f1-9fed-cf0d9b43ef2d" # One of our known issues
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28"
    }

    # 1. Retrieve the issue to find the Database ID
    ctx = ssl.create_default_context()
    req = urllib.request.Request(f"https://api.notion.com/v1/pages/{issue_id}", headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            page_data = json.loads(response.read())
            db_id = page_data["parent"]["database_id"]
            print(f"Found Database ID: {db_id}")
    except Exception as e:
        print(f"Error fetching page: {e}")
        return

    # 2. Update the Database schema to add the relation property
    update_data = {
        "properties": {
            "Blocked By": {
                "relation": {
                    "database_id": db_id,
                    "type": "dual_property",
                    "dual_property": {} # Let Notion automatically create the inverse "Blocking" property
                }
            }
        }
    }
    
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db_id}",
        data=json.dumps(update_data).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PATCH"
    )
    
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            result = json.loads(response.read())
            print(f"Success! Added 'Blocked By' and 'Blocking' relations to database {db_id}.")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"HTTP Error updating database: {e.code} - {error_body}")
    except Exception as e:
        print(f"Error updating database: {e}")

if __name__ == "__main__":
    main()
