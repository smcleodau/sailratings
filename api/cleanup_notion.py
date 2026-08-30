import os
from notion_client import Client

notion = Client(auth=os.environ["SAILRATINGS_NOTION_TOKEN"])
ROOT_PAGE_ID = "3a937ffef46780399a49ebe1178f409b"

def cleanup():
    allowed_folders = ["Epics", "Data Quality", "Architecture", "Specs"]
    response = notion.blocks.children.list(block_id=ROOT_PAGE_ID)
    for block in response.get("results", []):
        if block["type"] == "child_page":
            title = block["child_page"]["title"]
            if title not in allowed_folders:
                print(f"Archiving mess: {title}")
                notion.pages.update(page_id=block["id"], archived=True)

if __name__ == "__main__":
    cleanup()
