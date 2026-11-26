"""Turns an article's HTML body into Notion blocks (the JSON objects the Notion API takes).

Covers headings, paragraphs, lists, quotes, tables, code (GeeksforGeeks' multi-language tabs
become one code block per language), equations (<gfg-tex>), images and dividers. Notion's limits
are respected here: at most 2000 characters per rich-text piece, 100 pieces per block, 100 rows
per table, and children nested at most two levels deep in one request."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from bs4 import NavigableString, Tag
from bs4.element import Comment

Block = dict[str, Any]
RichText = list[dict[str, Any]]

MAX_TEXT = 2000  # characters in one rich-text piece
MAX_PIECES = 100  # rich-text pieces in one block
MAX_ROWS = 100  # rows in one table (children per array)
MAX_EQUATION = 1000
MAX_DEPTH = 2  # a block at depth 2 may not have children of its own in one request

HEADINGS = {
    "h1": "heading_1",
    "h2": "heading_2",
    "h3": "heading_3",
    "h4": "heading_3",
    "h5": "heading_3",
    "h6": "heading_3",
}
BLOCK_TAGS = {
    "p",
    "ul",
    "ol",
    "li",
    "blockquote",
    "figure",
    "img",
    "table",
    "pre",
    "hr",
    "gfg-tabs",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "aside",
    "main",
    "dl",
    "dd",
    "dt",
    *HEADINGS,
}
LANGUAGES = {
    "python3": "python",
    "python": "python",
    "py": "python",
    "cpp": "c++",
    "c++": "c++",
    "c": "c",
    "java": "java",
    "csharp": "c#",
    "c#": "c#",
    "cs": "c#",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "go": "go",
    "rust": "rust",
    "kotlin": "kotlin",
    "php": "php",
    "ruby": "ruby",
    "swift": "swift",
    "sql": "sql",
    "bash": "bash",
    "shell": "shell",
    "sh": "shell",
    "r": "r",
    "scala": "scala",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "matlab": "matlab",
}


def html_to_blocks(body: Tag, base_url: str) -> list[Block]:
    return _Converter(base_url).blocks(body, depth=0)


class _Converter:
    def __init__(self, base_url: str):
        self.base_url = base_url

    # ---- block level -------------------------------------------------------------------

    def blocks(self, el: Tag, depth: int) -> list[Block]:
        out: list[Block] = []
        inline: list[Tag | NavigableString] = []

        def flush() -> None:
            if inline:
                out.extend(paragraphs(self.rich(inline)))
                inline.clear()

        for child in el.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                inline.append(child)
            elif isinstance(child, Tag):
                if child.name in BLOCK_TAGS or _has_block_child(child):
                    flush()
                    out.extend(self.block(child, depth))
                else:
                    inline.append(child)
        flush()
        return out

    def block(self, el: Tag, depth: int) -> list[Block]:
        name = el.name
        if name in HEADINGS:
            rich = self.rich(el.children)
            return [_rich_block(HEADINGS[name], rich[:MAX_PIECES])] if rich else []
        if name == "p":
            if _only_equation(el):
                return equation(_tex(el))
            return self.blocks(el, depth) if _has_block_child(el) else paragraphs(self.rich(el))
        if name in ("ul", "ol"):
            kind = "numbered_list_item" if name == "ol" else "bulleted_list_item"
            items: list[Block] = []
            for li in el.find_all("li", recursive=False):
                items.extend(self.list_item(li, kind, depth))
            return items
        if name == "li":
            return self.list_item(el, "bulleted_list_item", depth)
        if name == "blockquote":
            if _only_equation(el):
                return equation(_tex(el))
            rich = self.rich(_join_paragraphs(el))
            return [_rich_block("quote", part) for part in _chunks(rich)] if rich else []
        if name in ("figure", "img"):
            return self.figure(el, depth)
        if name == "table":
            return self.table(el, depth)
        if name == "gfg-tabs":
            return self.code_tabs(el)
        if name == "pre":
            return code(el.get_text(), _language(el))
        if name == "hr":
            return [{"object": "block", "type": "divider", "divider": {}}]
        return self.blocks(el, depth)  # div, section, …: just a container

    def list_item(self, li: Tag, kind: str, depth: int) -> list[Block]:
        inline: list[Tag | NavigableString] = []
        nested: list[Block] = []
        for child in li.children:
            if isinstance(child, Tag) and (child.name in BLOCK_TAGS and child.name != "p"):
                nested.extend(self.block(child, depth + 1))
            elif isinstance(child, Tag) and child.name == "p":
                if inline:
                    inline.append(NavigableString("\n"))
                inline.extend(child.children)
            elif not isinstance(child, Comment):
                inline.append(child)
        rich = self.rich(inline)
        item = _rich_block(kind, rich[:MAX_PIECES])
        if depth < MAX_DEPTH or not nested:
            if nested:
                item[kind]["children"] = nested[:MAX_ROWS]
            return [item]
        return [item, *nested]  # too deep for one request: keep them as siblings

    def figure(self, el: Tag, depth: int) -> list[Block]:
        out: list[Block] = []
        table = el.find("table")
        if table is not None:
            out.extend(self.table(table, depth))
        img = el if el.name == "img" else el.find("img")
        if isinstance(img, Tag) and img.get("src"):
            src = urljoin(self.base_url, str(img["src"]))
            if src.startswith(("http://", "https://")):
                caption_el = el.find("figcaption") if el.name == "figure" else None
                caption = self.rich(caption_el) if caption_el else []
                out.append(
                    {
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "external",
                            "external": {"url": src},
                            "caption": caption[:MAX_PIECES],
                        },
                    }
                )
        return out

    def table(self, el: Tag, depth: int) -> list[Block]:
        rows: list[list[RichText]] = []
        header = False
        for i, tr in enumerate(el.find_all("tr")):
            cells = tr.find_all(["td", "th"], recursive=False)
            if i == 0 and cells and all(c.name == "th" for c in cells):
                header = True
            rows.append([self.rich(c)[:MAX_PIECES] for c in cells])
        rows = [r for r in rows if r]
        if not rows:
            return []
        width = max(len(r) for r in rows)
        rows = [r + [[] for _ in range(width - len(r))] for r in rows]
        if depth >= MAX_DEPTH:  # a table needs children; too deep, so write the rows as text
            return [p for r in rows for p in paragraphs(_join_cells(r))]
        head, body = (rows[:1], rows[1:]) if header else ([], rows)
        per_table = MAX_ROWS - len(head)
        tables: list[Block] = []
        for start in range(0, max(len(body), 1), per_table):
            chunk = head + body[start : start + per_table]
            tables.append(
                {
                    "object": "block",
                    "type": "table",
                    "table": {
                        "table_width": width,
                        "has_column_header": header,
                        "has_row_header": False,
                        "children": [
                            {"object": "block", "type": "table_row", "table_row": {"cells": r}}
                            for r in chunk
                        ],
                    },
                }
            )
        return tables

    def code_tabs(self, el: Tag) -> list[Block]:
        names = [_clean(t.get_text()) for t in el.find_all("gfg-tab")]
        panels = el.find_all("gfg-panel")
        out: list[Block] = []
        for i, panel in enumerate(panels):
            lang = _language(panel)
            caption = names[i] if len(panels) > 1 and i < len(names) else ""
            out.extend(code(panel.get_text(), lang, caption))
        return out

    # ---- inline level ------------------------------------------------------------------

    def rich(self, nodes) -> RichText:
        if isinstance(nodes, Tag):
            nodes = nodes.children
        pieces: RichText = []
        for node in nodes:
            self._inline(node, {}, None, pieces)
        return _finish(pieces)

    def _inline(self, node, marks: dict[str, bool], link: str | None, out: RichText) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            text = " ".join(str(node).split(None)) if not marks.get("code") else str(node)
            if str(node)[:1].isspace() and text:
                text = " " + text
            if str(node)[-1:].isspace() and text and not text.endswith(" "):
                text += " "
            if not text.strip() and text:
                text = " "
            if text:
                out.append(_text(text, marks, link))
            return
        if not isinstance(node, Tag):
            return
        name = node.name
        if name == "br":
            out.append(_text("\n", marks, link))
            return
        if name == "gfg-tex":
            expr = _clean(node.get_text())
            if expr:
                out.append({"type": "equation", "equation": {"expression": expr[:MAX_EQUATION]}})
            return
        if name == "img":
            return
        marks = dict(marks)
        if name in ("b", "strong"):
            marks["bold"] = True
        elif name in ("i", "em"):
            marks["italic"] = True
        elif name == "u":
            marks["underline"] = True
        elif name in ("s", "del", "strike"):
            marks["strikethrough"] = True
        elif name in ("code", "kbd", "samp"):
            marks["code"] = True
        elif name == "a" and node.get("href"):
            href = urljoin(self.base_url, str(node["href"]))
            link = href if href.startswith(("http://", "https://")) else link
        for child in node.children:
            self._inline(child, marks, link, out)


# ---- helpers ----------------------------------------------------------------------------


def paragraphs(rich: RichText) -> list[Block]:
    if not rich or not any(_plain(p).strip() for p in rich):
        return []
    return [_rich_block("paragraph", part) for part in _chunks(rich)]


def equation(expr: str) -> list[Block]:
    if not expr:
        return []
    if len(expr) > MAX_EQUATION:
        return code(expr, "latex")
    return [{"object": "block", "type": "equation", "equation": {"expression": expr}}]


def code(text: str, language: str, caption: str = "") -> list[Block]:
    text = text.strip("\n")
    if not text.strip():
        return []
    pieces = [text[i : i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)]
    blocks = []
    for start in range(0, len(pieces), MAX_PIECES):
        blocks.append(
            {
                "object": "block",
                "type": "code",
                "code": {
                    "language": language,
                    "rich_text": [
                        {"type": "text", "text": {"content": p}}
                        for p in pieces[start : start + MAX_PIECES]
                    ],
                    "caption": [{"type": "text", "text": {"content": caption}}] if caption else [],
                },
            }
        )
    return blocks


def _rich_block(kind: str, rich: RichText) -> Block:
    return {"object": "block", "type": kind, kind: {"rich_text": rich}}


def _text(content: str, marks: dict[str, bool], link: str | None) -> dict[str, Any]:
    piece: dict[str, Any] = {"type": "text", "text": {"content": content}}
    if link:
        piece["text"]["link"] = {"url": link}
    if marks:
        piece["annotations"] = dict(marks)
    return piece


def _plain(piece: dict[str, Any]) -> str:
    if piece["type"] == "text":
        return piece["text"]["content"]
    return piece.get("equation", {}).get("expression", "")


def _finish(pieces: RichText) -> RichText:
    """Merge neighbours with the same style, trim the ends, split long pieces."""
    merged: RichText = []
    for p in pieces:
        last = merged[-1] if merged else None
        if (
            last
            and p["type"] == "text"
            and last["type"] == "text"
            and last.get("annotations") == p.get("annotations")
            and last["text"].get("link") == p["text"].get("link")
        ):
            before, after = last["text"]["content"], p["text"]["content"]
            if before.endswith(" ") and after.startswith(" "):
                after = after[1:]
            last["text"]["content"] = before + after
        else:
            merged.append({**p, "text": dict(p["text"])} if p["type"] == "text" else p)
    if merged and merged[0]["type"] == "text":
        merged[0]["text"]["content"] = merged[0]["text"]["content"].lstrip()
    if merged and merged[-1]["type"] == "text":
        merged[-1]["text"]["content"] = merged[-1]["text"]["content"].rstrip()
    out: RichText = []
    for p in merged:
        if p["type"] != "text":
            out.append(p)
            continue
        content = p["text"]["content"]
        if not content:
            continue
        for i in range(0, len(content), MAX_TEXT):
            out.append({**p, "text": {**p["text"], "content": content[i : i + MAX_TEXT]}})
    return out


def _chunks(rich: RichText) -> list[RichText]:
    return [rich[i : i + MAX_PIECES] for i in range(0, len(rich), MAX_PIECES)]


def _join_cells(row: list[RichText]) -> RichText:
    out: RichText = []
    for i, cell in enumerate(row):
        if i:
            out.append({"type": "text", "text": {"content": " | "}})
        out.extend(cell)
    return out


def _join_paragraphs(el: Tag) -> list:
    """The inline content of a quote, with its paragraphs separated by line breaks."""
    nodes: list = []
    for child in el.children:
        if isinstance(child, Tag) and child.name == "p":
            if nodes:
                nodes.append(NavigableString("\n"))
            nodes.extend(child.children)
        else:
            nodes.append(child)
    return nodes


def _has_block_child(el: Tag) -> bool:
    return any(isinstance(c, Tag) and (c.name in BLOCK_TAGS) for c in el.children)


def _only_equation(el: Tag) -> bool:
    tex = el.find_all("gfg-tex")
    if len(tex) != 1:
        return False
    rest = el.get_text().replace(tex[0].get_text(), "")
    return not rest.strip()


def _tex(el: Tag) -> str:
    found = el.find("gfg-tex")
    return _clean(found.get_text()) if found else ""


def _language(el: Tag) -> str:
    raw = str(el.get("data-code-lang") or "")
    if not raw:
        for node in (el, *el.find_all(["code", "pre"])):
            for cls in node.get("class") or []:
                if cls.startswith("language-"):
                    raw = cls[len("language-") :]
                    break
            if raw:
                break
    return LANGUAGES.get(raw.lower(), "plain text")


def _clean(text: str) -> str:
    return " ".join(text.split())
