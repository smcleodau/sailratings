import os
import requests
import json

NOTION_TOKEN = os.environ.get('SAILRATINGS_NOTION_TOKEN')
DB_ID = '3a937ffe-f467-81b3-b888-d873def19261'

headers = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}

def query_db():
    url = f'https://api.notion.com/v1/databases/{DB_ID}/query'
    res = requests.post(url, headers=headers, json={})
    return res.json()

print(json.dumps(query_db(), indent=2))
