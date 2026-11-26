"""The only code in Learnova that writes to Notion, used by the one-time `web import`. It creates
pages, appends blocks in batches the API accepts, and empties a page whose import was cut off
halfway so it can be written again. notion-client already retries rate-limited (429) requests."""

from __future__ import annotations

from typing import Any

from notion_client import Client
from notion_client.helpers import collect_paginated_api

Block = dict[str, Any]

MAX_BLOCKS = 100  # top-level blocks per append
MAX_TOTAL = 900  # all blocks, nested ones included, per append (Notion's limit is 1000)
KEEP = {"child_page", "child_database"}  # never deleted when a page is emptied


class NotionWriter:
    def __init__(self, client: Client):
        self.client = client

    def create_page(self, parent_id: str, title: str) -> str:
        page = self.client.pages.create(
            parent={"page_id": parent_id},
            properties={"title": {"title": [{"type": "text", "text": {"content": title[:2000]}}]}},
        )
        return page["id"]

    def append(self, block_id: str, blocks: list[Block]) -> list[str]:
        """Append in order; returns the IDs of the top-level blocks created."""
        ids: list[str] = []
        for batch in batches(blocks):
            result = self.client.blocks.children.append(block_id=block_id, children=batch)
            ids.extend(b["id"] for b in result["results"])
        return ids

    def clear(self, page_id: str) -> None:
        children = collect_paginated_api(self.client.blocks.children.list, block_id=page_id)
        for block in children:
            if block["type"] not in KEEP:
                self.client.blocks.delete(block_id=block["id"])


def batches(blocks: list[Block]) -> list[list[Block]]:
    out: list[list[Block]] = []
    current: list[Block] = []
    total = 0
    for block in blocks:
        size = count(block)
        if current and (len(current) >= MAX_BLOCKS or total + size > MAX_TOTAL):
            out.append(current)
            current, total = [], 0
        current.append(block)
        total += size
    if current:
        out.append(current)
    return out


def count(block: Block) -> int:
    children = block.get(block["type"], {}).get("children", [])
    return 1 + sum(count(c) for c in children)
