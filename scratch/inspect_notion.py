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
    
    # Query database
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    res = httpx.post(url, headers=headers, json={"page_size": 100})
    if res.status_code != 200:
        print("Error querying database:", res.status_code, res.text)
        return

    data = res.json()
    results = data.get("results", [])
    print(f"Total items in DB: {len(results)}")

    for item in results:
        props = item.get("properties", {})
        title_prop = props.get("Issue") or props.get("Name") or {}
        title = ""
        if title_prop.get("title"):
            title = "".join([t.get("plain_text", "") for t in title_prop["title"]])
        
        status_prop = props.get("Status", {})
        status = status_prop.get("select", {}).get("name") if status_prop.get("select") else "N/A"

        type_prop = props.get("type", {})
        item_type = type_prop.get("select", {}).get("name") if type_prop.get("select") else "N/A"

        print(f"- [{status}] [{item_type}] {title} (ID: {item['id']})")

if __name__ == "__main__":
    main()
