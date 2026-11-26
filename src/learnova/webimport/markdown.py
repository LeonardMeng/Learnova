"""The local cache: each article as Markdown, made from the same Notion blocks that went into
Notion, so the cache and the Notion page start out identical. Uses sync's block-to-Markdown
converter; images become ordinary Markdown image links."""

from __future__ import annotations

import copy
import itertools
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from learnova.sources.notion_markdown import MarkdownConverter, image_placeholder
from learnova.webimport.html_blocks import Block


def blocks_to_markdown(blocks: list[Block]) -> str:
    """Notion blocks as sent to the API (children inline, no IDs) to Markdown."""
    ids = (f"b{n}" for n in itertools.count())
    children: dict[str, list[dict[str, Any]]] = {}

    def prepare(block: Block) -> dict[str, Any]:
        block = copy.deepcopy(block)
        block["id"] = next(ids)
        data = block.get(block["type"], {})
        for key in ("rich_text", "caption"):
            for piece in data.get(key, []):
                _as_response(piece)
        for row in data.get("cells", []):
            for piece in row:
                _as_response(piece)
        kids = [prepare(k) for k in data.pop("children", [])]
        block["has_children"] = bool(kids)
        children[block["id"]] = kids
        return block

    children["root"] = [prepare(b) for b in blocks]
    converter = MarkdownConverter(lambda block_id: children.get(block_id, []))
    text = converter.page("root")
    for image in converter.images:
        alt = image.caption.replace("]", "\\]") or "image"
        text = text.replace(image_placeholder(image.block_id), f"![{alt}]({image.url})")
    return text


def _as_response(piece: dict[str, Any]) -> None:
    """The converter reads `plain_text` and `href`, which only API responses carry."""
    if piece.get("type") == "text":
        piece["plain_text"] = piece["text"]["content"]
        link = piece["text"].get("link")
        if link:
            piece["href"] = link["url"]


def cache_path(url: str) -> Path:
    """cache/<category>/<slug>.md, from the article URL."""
    parts = [p for p in urlsplit(url).path.split("/") if p] or ["index"]
    return Path("cache", *parts[:-1], f"{parts[-1]}.md")
