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

try:
    data = query_db()
    results = data.get('results', [])
    summary = []
    for page in results:
        props = page.get('properties', {})
        
        title_prop = props.get('Name', {}).get('title', [])
        name = title_prop[0].get('text', {}).get('content', '') if title_prop else ''
        
        type_prop = props.get('Type', {}).get('select', {})
        ptype = type_prop.get('name', '') if type_prop else ''
        
        status_prop = props.get('Status', {}).get('status', {})
        status = status_prop.get('name', '') if status_prop else ''
        
        spec_prop = props.get('Specification', {}).get('rich_text', [])
        parent_prop = props.get('Parent Epic', {}).get('relation', [])
        
        blocked_by_prop = props.get('Blocked By', {}).get('relation', [])
        
        summary.append({
            'id': page['id'],
            'name': name,
            'type': ptype,
            'status': status,
            'spec': spec_prop,
            'parent_epic': parent_prop,
            'blocked_by': blocked_by_prop
        })
    print(json.dumps(summary, indent=2))
except Exception as e:
    import traceback
    traceback.print_exc()
