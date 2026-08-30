from abc import ABC, abstractmethod
import os
import urllib.request
import json

class BoardOperator(ABC):
    @abstractmethod
    def append_test_evidence(self, issue_id: str, test_command: str, output: str) -> None:
        """Appends command output logs as evidence to the issue board."""
        pass

    @abstractmethod
    def append_visual_evidence(self, issue_id: str, image_url: str) -> None:
        """Appends a screenshot/visual asset as evidence to the issue board."""
        pass
        
    @abstractmethod
    def get_issue_content(self, issue_id: str) -> str:
        """Retrieves the full text/content body of the issue."""
        pass

class NotionAdapter(BoardOperator):
    def __init__(self, notion_token: str = None):
        self.notion_token = notion_token or os.environ.get("SAILRATINGS_NOTION_TOKEN")
        if not self.notion_token:
            raise ValueError("SAILRATINGS_NOTION_TOKEN is required for NotionAdapter")
        self.headers = {
            'Authorization': f'Bearer {self.notion_token}',
            'Notion-Version': '2022-06-28',
            'Content-Type': 'application/json'
        }

    def _request(self, url: str, method: str = 'GET', data: dict = None):
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data else None,
            method=method,
            headers=self.headers
        )
        res = urllib.request.urlopen(req)
        return json.loads(res.read())

    def append_test_evidence(self, issue_id: str, test_command: str, output: str) -> None:
        content = f"$ {test_command}\n{output}"
        # Notion code blocks max length is 2000 characters
        if len(content) > 2000:
            content = content[:1997] + "..."
            
        data = {
            "children": [
                {
                    "object": "block",
                    "type": "code",
                    "code": {
                        "rich_text": [{"type": "text", "text": {"content": content}}],
                        "language": "shell"
                    }
                }
            ]
        }
        self._request(f"https://api.notion.com/v1/blocks/{issue_id}/children", method='PATCH', data=data)

    def append_visual_evidence(self, issue_id: str, image_url: str) -> None:
        data = {
            "children": [
                {
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "external",
                        "external": {
                            "url": image_url
                        }
                    }
                }
            ]
        }
        self._request(f"https://api.notion.com/v1/blocks/{issue_id}/children", method='PATCH', data=data)
        
    def get_issue_content(self, issue_id: str) -> str:
        res = self._request(f"https://api.notion.com/v1/blocks/{issue_id}/children")
        content = ""
        for block in res.get('results', []):
            b_type = block.get('type')
            if b_type in ['paragraph', 'bulleted_list_item', 'numbered_list_item', 'heading_1', 'heading_2', 'heading_3']:
                for rt in block.get(b_type, {}).get('rich_text', []):
                    content += rt.get('text', {}).get('content', '')
                content += "\n"
            elif b_type == 'code':
                for rt in block.get(b_type, {}).get('rich_text', []):
                    content += rt.get('text', {}).get('content', '')
                content += "\n"
        return content
