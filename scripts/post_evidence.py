#!/usr/bin/env python3
"""Post factory evidence to a sailratings Roadmap card.

The ONE sanctioned way for a Lane Worker (or a human) to attach evidence to a
Roadmap card. Replaces the per-task post_evidence*.py / upload_evidence*.py
scripts that used to get committed at the repo root.

Usage:
    python scripts/post_evidence.py --issue AD-01-18 \
        --cmd "pytest tests/test_admin_audit_log.py -q" --output-file /tmp/ad-01-18.log
    python scripts/post_evidence.py --issue AD-01-20 \
        --cmd "npx playwright test tests/admin-scrapers.spec.ts" --output-file /tmp/run.log \
        --screenshot /tmp/ad-01-20-1440.png --screenshot /tmp/ad-01-20-390.png
    python scripts/post_evidence.py --issue AD-01-18 --note "Stopped: card needs app.py which is owned by AD-01-19"

--issue accepts either the Roadmap ID (AD-01-18) or a Notion page id/URL.
Requires SAILRATINGS_NOTION_TOKEN in the environment (same token the poller uses).
Exit code 0 on success, 2 on argument error, 1 on Notion error. Never edits files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROADMAP_DB_ID = "3b237ffe-f467-81b4-8aad-e4eb0d49f4da"
NOTION_VERSION = "2022-06-28"
MAX_CODE_BLOCK = 1900  # Notion caps rich_text at 2000 chars


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(url: str, token: str, method: str = "GET", data: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        method=method, headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:  # pragma: no cover - network
        body = e.read().decode(errors="replace")
        raise SystemExit(f"Notion {e.code} on {method} {url}: {body}") from e


def resolve_page_id(issue: str, token: str) -> str:
    """Return a Notion page id for a Roadmap ID like AD-01-18, or pass a page id/URL through."""
    m = re.search(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", issue)
    if m:
        return m.group(1)
    body = {"filter": {"property": "ID", "rich_text": {"equals": issue}}, "page_size": 2}
    data = _request(f"https://api.notion.com/v1/databases/{ROADMAP_DB_ID}/query", token, "POST", body)
    results = data.get("results", [])
    if len(results) != 1:
        raise SystemExit(f"Expected exactly one Roadmap row with ID={issue}, found {len(results)}")
    return results[0]["id"]


def _text_block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind,
            kind: {"rich_text": [{"type": "text", "text": {"content": text}}], **extra}}


def _chunks(s: str, n: int) -> list[str]:
    return [s[i:i + n] for i in range(0, len(s), n)] or [""]


def build_blocks(cmd: str | None, output: str | None, note: str | None, screenshots: list[str]) -> list[dict]:
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks: list[dict] = [_text_block("heading_3", f"Evidence — {stamp}")]
    if note:
        blocks.append(_text_block("paragraph", note[:1900]))
    if cmd is not None:
        body = f"$ {cmd}\n{output or ''}"
        # Keep the tail: the pass/fail summary is at the end of pytest/playwright output.
        if len(body) > MAX_CODE_BLOCK * 3:
            body = body[:MAX_CODE_BLOCK] + "\n... [truncated] ...\n" + body[-MAX_CODE_BLOCK * 2:]
        for chunk in _chunks(body, MAX_CODE_BLOCK):
            blocks.append(_text_block("code", chunk, language="shell"))
    for path in screenshots:
        blocks.append(_text_block("paragraph", f"Screenshot: {os.path.basename(path)}"))
    return blocks


def upload_screenshot(page_id: str, path: str, token: str) -> None:
    """Upload a PNG via the Notion file-upload API and attach it as an image block."""
    import mimetypes
    import uuid
    ctype = mimetypes.guess_type(path)[0] or "image/png"
    up = _request("https://api.notion.com/v1/file_uploads", token, "POST",
                  {"filename": os.path.basename(path), "content_type": ctype})
    boundary = uuid.uuid4().hex
    with open(path, "rb") as fh:
        payload = fh.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(up["upload_url"], data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=60):
        pass
    _request(f"https://api.notion.com/v1/blocks/{page_id}/children", token, "PATCH", {
        "children": [{"object": "block", "type": "image",
                      "image": {"type": "file_upload", "file_upload": {"id": up["id"]}}}]})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--issue", required=True, help="Roadmap ID (AD-01-18) or Notion page id/URL")
    ap.add_argument("--cmd", help="The exact command that was run")
    ap.add_argument("--output-file", help="File holding that command's stdout+stderr")
    ap.add_argument("--note", help="Free-text note (e.g. why you stopped)")
    ap.add_argument("--screenshot", action="append", default=[], help="PNG to attach (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="Print the blocks, post nothing")
    args = ap.parse_args(argv)

    if args.cmd is None and not args.note and not args.screenshot:
        ap.error("give --cmd/--output-file, --note or --screenshot")
    if (args.cmd is None) != (args.output_file is None):
        ap.error("--cmd and --output-file go together")
    output = None
    if args.output_file:
        with open(args.output_file, "r", errors="replace") as fh:
            output = fh.read()
    for s in args.screenshot:
        if not os.path.isfile(s):
            ap.error(f"screenshot not found: {s}")

    blocks = build_blocks(args.cmd, output, args.note, args.screenshot)
    if args.dry_run:
        print(json.dumps(blocks, indent=2))
        return 0

    token = os.environ.get("SAILRATINGS_NOTION_TOKEN")
    if not token:
        print("SAILRATINGS_NOTION_TOKEN is not set", file=sys.stderr)
        return 1
    page_id = resolve_page_id(args.issue, token)
    _request(f"https://api.notion.com/v1/blocks/{page_id}/children", token, "PATCH", {"children": blocks})
    for s in args.screenshot:
        upload_screenshot(page_id, s, token)
    print(f"posted {len(blocks)} block(s) and {len(args.screenshot)} screenshot(s) to {args.issue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
