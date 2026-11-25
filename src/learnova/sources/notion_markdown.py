"""Turns a Notion page's block tree into Markdown. It only needs a function that returns a block's
children, so it can be tested with plain dicts and no API calls. Images become placeholders and
are collected in `images` for sync to download; other attachments get a placeholder line."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from learnova.models import ImageRef

Block = dict[str, Any]
ChildrenFn = Callable[[str], list[Block]]

LIST_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do"}
PLACEHOLDER_TYPES = {"image", "file", "pdf", "video", "audio", "child_database"}
SKIPPED_TYPES = {"table_of_contents", "breadcrumb"}
HEADING_TYPES = {f"heading_{n}" for n in range(1, 7)}  # Notion has heading_1 to heading_4
SUPPORTED_TYPES = HEADING_TYPES | {
    "paragraph", "quote", "callout", "toggle", "code",
    "equation", "divider", "table", "table_row", "column_list", "column", "synced_block",
    "child_page", "link_to_page", "bookmark", "embed", "link_preview",
} | LIST_TYPES | PLACEHOLDER_TYPES | SKIPPED_TYPES  # fmt: skip


def image_placeholder(block_id: str) -> str:
    """Marker that sync expands into the image link and its description."""
    return f"<!-- image:{block_id} -->"


def rich_text(items: list[dict[str, Any]]) -> str:
    """Notion rich text (a list of styled runs) to inline Markdown."""
    out = []
    for item in items:
        if item.get("type") == "equation":
            out.append(f"${item['equation']['expression']}$")
            continue
        text = item.get("plain_text", "")
        ann = item.get("annotations", {})
        if ann.get("code"):
            text = f"`{text}`"
        for flag, mark in (("bold", "**"), ("italic", "*"), ("strikethrough", "~~")):
            if ann.get(flag):
                text = _wrap(text, mark)
        if item.get("href"):
            text = f"[{text}]({item['href']})"
        out.append(text)
    return "".join(out)


def _wrap(text: str, mark: str) -> str:
    # "**bold **" is not valid Markdown, so keep surrounding spaces outside the marks.
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{mark}{core}{mark}{trail}"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


def _cell(cell: list[dict[str, Any]]) -> str:
    return rich_text(cell).replace("|", "\\|").replace("\n", "<br>")


class MarkdownConverter:
    def __init__(self, children: ChildrenFn, block_types: Counter[str] | None = None):
        self._children = children
        self.block_types = block_types if block_types is not None else Counter()
        self.images: list[ImageRef] = []

    def page(self, page_id: str) -> str:
        return self._render_all(self._children(page_id)).strip() + "\n"

    def _render_all(self, blocks: list[Block]) -> str:
        parts: list[str] = []
        prev_type = None
        for block in blocks:
            text = self._render(block)
            if text is None:
                continue
            btype = block["type"]
            # Consecutive list items stay together; everything else is separated by a blank line.
            sep = "\n" if btype in LIST_TYPES and prev_type in LIST_TYPES else "\n\n"
            parts.append((sep if parts else "") + text)
            prev_type = btype
        return "".join(parts)

    def _kids(self, block: Block) -> str:
        return self._render_all(self._children(block["id"])) if block.get("has_children") else ""

    def _render(self, block: Block) -> str | None:
        btype = block["type"]
        self.block_types[btype] += 1
        data = block.get(btype, {})
        text = rich_text(data.get("rich_text", []))

        if btype == "paragraph":
            kids = self._kids(block)
            if not text and not kids:
                return None
            return "\n\n".join(p for p in (text, kids) if p)
        if btype in HEADING_TYPES:
            heading = "#" * int(btype[-1]) + " " + text
            kids = self._kids(block)  # toggleable headings have children
            return f"{heading}\n\n{kids}" if kids else heading
        if btype in LIST_TYPES:
            if btype == "bulleted_list_item":
                marker = "- "
            elif btype == "numbered_list_item":
                marker = "1. "  # Markdown renumbers
            else:
                marker = "- [x] " if data.get("checked") else "- [ ] "
            kids = self._kids(block)
            item = marker + text
            return f"{item}\n{_indent(kids, ' ' * len(marker))}" if kids else item
        if btype in ("quote", "callout"):
            icon = (data.get("icon") or {}).get("emoji", "") if btype == "callout" else ""
            body = "\n\n".join(p for p in (f"{icon} {text}".strip(), self._kids(block)) if p)
            return _indent(body, "> ").replace("\n\n", "\n>\n")
        if btype == "toggle":
            kids = self._kids(block)
            return f"<details>\n<summary>{text}</summary>\n\n{kids}\n\n</details>"
        if btype == "code":
            code = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))
            lang = data.get("language", "")
            lang = "" if lang == "plain text" else lang
            return f"```{lang}\n{code}\n```"
        if btype == "equation":
            return f"$$\n{data.get('expression', '')}\n$$"
        if btype == "divider":
            return "---"
        if btype == "table":
            rows = [r for r in self._children(block["id"]) if r["type"] == "table_row"]
            if not rows:
                return None
            lines = []
            for i, row in enumerate(rows):
                self.block_types["table_row"] += 1
                lines.append("| " + " | ".join(_cell(c) for c in row["table_row"]["cells"]) + " |")
                if i == 0:  # Markdown tables always need a header row
                    lines.append("|" + " --- |" * len(row["table_row"]["cells"]))
            return "\n".join(lines)
        if btype in ("column_list", "column", "synced_block"):
            return self._kids(block) or None  # flattened into reading order
        if btype == "child_page":
            return f"→ Sub-page: {data.get('title') or '(untitled)'}"
        if btype == "child_database":
            return f"→ Database (not synced): {data.get('title') or '(untitled)'}"
        if btype == "link_to_page":
            target = data.get(data.get("type", ""), "")
            return f"→ Linked page: {target}"
        if btype in ("bookmark", "embed", "link_preview"):
            url = data.get("url", "")
            label = rich_text(data.get("caption", [])) or url
            return f"[{label}]({url})"
        if btype == "image":
            caption = rich_text(data.get("caption", []))
            ref = _image_ref(block["id"], data, caption)
            if ref is None:
                return f"*[image not available: {caption or 'no link'}]*"
            self.images.append(ref)
            return image_placeholder(block["id"])
        if btype in ("file", "pdf", "video", "audio"):
            name = data.get("name") or rich_text(data.get("caption", [])) or "(unnamed)"
            return f"[{btype} attachment not supported in v1: {name}]"
        if btype in SKIPPED_TYPES:
            return None
        return f"<!-- unsupported: {json.dumps(btype)} -->"


def _image_ref(block_id: str, data: dict[str, Any], caption: str) -> ImageRef | None:
    kind = data.get("type")
    url = (data.get(kind) or {}).get("url") if kind in ("file", "external") else None
    if not url:
        return None
    # Notion-hosted files get a freshly signed link on every request; the part before "?"
    # names the file and stays the same. External links are used as they are.
    key = url.split("?", 1)[0] if kind == "file" else url
    return ImageRef(block_id=block_id, source_key=key, url=url, caption=caption)
