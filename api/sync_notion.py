import os
import sys
import glob
from notion_client import Client

notion = Client(auth=os.environ["SAILRATINGS_NOTION_TOKEN"])
ROOT_PAGE_ID = "3a937ffef46780399a49ebe1178f409b"

def parse_markdown_to_blocks(md_text):
    blocks = []
    lines = md_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]}
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}
            })
        elif line.startswith("# "):
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}
            })
        elif line.startswith("- [ ] "):
            blocks.append({
                "object": "block",
                "type": "to_do",
                "to_do": {"rich_text": [{"type": "text", "text": {"content": line[6:]}}], "checked": False}
            })
        elif line.startswith("- [x] "):
            blocks.append({
                "object": "block",
                "type": "to_do",
                "to_do": {"rich_text": [{"type": "text", "text": {"content": line[6:]}}], "checked": True}
            })
        elif line.startswith("1. ") or line.startswith("2. ") or line.startswith("3. ") or line.startswith("4. ") or line.startswith("5. "):
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}
            })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]}
            })
    return blocks

def get_or_create_folder(folder_name):
    response = notion.blocks.children.list(block_id=ROOT_PAGE_ID)
    for block in response.get("results", []):
        if block["type"] == "child_page" and block["child_page"]["title"] == folder_name:
            return block["id"]
            
    new_page = notion.pages.create(
        parent={"page_id": ROOT_PAGE_ID},
        properties={
            "title": {
                "title": [{"type": "text", "text": {"content": folder_name}}]
            }
        }
    )
    return new_page["id"]

def get_existing_page(parent_id, title):
    response = notion.blocks.children.list(block_id=parent_id)
    for block in response.get("results", []):
        if block["type"] == "child_page" and block["child_page"]["title"] == title:
            return block["id"]
    return None

def sync_file(filepath, folder_id):
    with open(filepath, "r") as f:
        content = f.read()
    
    title = os.path.basename(filepath).replace(".md", "")
    print(f"Syncing {title} into folder...")
    
    existing_page_id = get_existing_page(folder_id, title)
    if existing_page_id:
        print(f"  -> Found existing page for {title}, archiving it...")
        notion.pages.update(page_id=existing_page_id, archived=True)
    
    new_page = notion.pages.create(
        parent={"page_id": folder_id},
        properties={
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        }
    )
    
    blocks = parse_markdown_to_blocks(content)
    for i in range(0, len(blocks), 100):
        notion.blocks.children.append(
            block_id=new_page["id"],
            children=blocks[i:i+100]
        )
    print(f"  -> Successfully synced {title}.")

if __name__ == "__main__":
    base_dir = "/home/irc-data/code/sailratings/docs"
    folders = ["epics", "data-quality", "architecture", "specs"]
    
    for folder in folders:
        folder_path = os.path.join(base_dir, folder)
        if not os.path.exists(folder_path):
            continue
            
        folder_title = folder.replace("-", " ").title()
        print(f"\\n--- Syncing Folder: {folder_title} ---")
        folder_id = get_or_create_folder(folder_title)
        
        md_files = glob.glob(os.path.join(folder_path, "*.md"))
        for f in md_files:
            sync_file(f, folder_id)
