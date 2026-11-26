"""Notion access: the client, the sub-page walker used by `ping`, and NotionSource, which
implements the NoteSource interface for sync."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx
from notion_client import Client
from notion_client.helpers import collect_paginated_api, iterate_paginated_api

from learnova.errors import LearnovaError
from learnova.ignore import IgnoreRules
from learnova.models import ImageRef, NoteContent, NoteSummary
from learnova.sources.base import IgnoredPage, NoteSource
from learnova.sources.notion_markdown import MarkdownConverter

TOKEN_ENV = "NOTION_API_KEY"

# 32 hex characters, optionally written as a dashed UUID.
_ID_RE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)


class NotionConfigError(LearnovaError):
    """Raised when the token or page ID is missing or malformed."""


@dataclass(frozen=True)
class PageNode:
    id: str
    title: str
    depth: int  # 0 = direct sub-page of the root page
    kind: Literal["page", "database"] = "page"
    parent_id: str | None = None  # the page this one sits in (even when inside a toggle)


def make_client(token: str | None = None) -> Client:
    token = token or os.environ.get(TOKEN_ENV)
    if not token:
        raise NotionConfigError(
            f"{TOKEN_ENV} is not set. Put it in .env and run "
            "`uv run --env-file .env learnova ...`, or export it in your shell."
        )
    return Client(auth=token)


def parse_page_id(page: str) -> str:
    """Accept a raw page ID (with or without dashes) or a Notion page URL.

    Works for both notion.so and app.notion.com links. Only the URL path is searched, so view
    IDs in the query string (?v=...) are ignored; the last ID in the path is the page.
    """
    path = page.strip().split("?", 1)[0].split("#", 1)[0]
    matches = _ID_RE.findall(path)
    if not matches:
        raise NotionConfigError(f"Could not find a Notion page ID in {page!r}.")
    h = matches[-1].replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def page_title(client: Client, page_id: str) -> str:
    return _title_of(client.pages.retrieve(page_id=page_id))


def _title_of(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"]) or "(untitled)"
    return "(untitled)"


def walk_subpages(client: Client, page_id: str) -> Iterator[PageNode]:
    """Yield every sub-page under page_id, depth first, in the order they appear on the page.

    Sub-pages nested inside toggles, columns and other container blocks are included at the
    same depth as the page that contains them. Databases are listed but not entered.
    """

    def children(block_id: str) -> Iterable[dict[str, Any]]:
        return iterate_paginated_api(client.blocks.children.list, block_id=block_id)

    return _walk(children, page_id, 0)


def _walk(
    children: Callable[[str], Iterable[dict[str, Any]]],
    block_id: str,
    depth: int,
    page_id: str | None = None,
    prune: Callable[[PageNode], bool] | None = None,
) -> Iterator[PageNode]:
    """`prune(node)` is asked after each page is yielded; True skips everything under it."""
    page_id = page_id or block_id  # the page that contains the blocks being read
    for block in children(block_id):
        btype = block.get("type")
        if btype == "child_page":
            title = block["child_page"].get("title") or "(untitled)"
            node = PageNode(id=block["id"], title=title, depth=depth, parent_id=page_id)
            yield node
            if prune is None or not prune(node):
                yield from _walk(children, block["id"], depth + 1, prune=prune)
        elif btype == "child_database":
            title = block["child_database"].get("title") or "(untitled)"
            yield PageNode(block["id"], title, depth, kind="database", parent_id=page_id)
        elif block.get("has_children"):
            # A toggle, column, callout, etc. can hold sub-pages: search inside it.
            yield from _walk(children, block["id"], depth, page_id, prune)


class NotionSource(NoteSource):
    """Reads the top page and every sub-page under it as notes.

    Block lists are cached for the lifetime of the object (one sync), so listing the tree and
    then fetching each page doesn't download the same blocks twice.
    """

    def __init__(self, client: Client, root_page_id: str, ignore: IgnoreRules | None = None):
        self._client = client
        self._root = root_page_id
        self._ignore = ignore or IgnoreRules()
        self._ignored: list[IgnoredPage] = []
        self._blocks: dict[str, list[dict[str, Any]]] = {}
        self.block_types: Counter[str] = Counter()  # filled in by fetch()
        self.skipped_databases: list[str] = []  # filled in by list_notes()

    def list_notes(self) -> list[NoteSummary]:
        root = self._client.pages.retrieve(page_id=self._root)
        root_title = _title_of(root)
        notes = [_summary(self._root, root_title, (root_title,), root)]
        self.skipped_databases = []
        self._ignored = []
        pruned: set[str] = set()
        stack: list[str] = []  # titles of the pages above the current one
        # _walk is lazy: it asks `prune` about a page only after we've handled it below.
        for node in _walk(self._children, self._root, 0, prune=lambda n: n.id in pruned):
            stack = [*stack[: node.depth], node.title]
            if self._ignore.ignores(tuple(stack), node.id):
                pruned.add(node.id)
                self._ignored.append(IgnoredPage(node.id, (root_title, *stack)))
                continue
            if node.kind == "database":
                self.skipped_databases.append(node.title)
                continue
            page = self._client.pages.retrieve(page_id=node.id)
            notes.append(
                _summary(node.id, node.title, (root_title, *stack), page, parent_id=node.parent_id)
            )
        return notes

    def ignored_pages(self) -> list[IgnoredPage]:
        return list(self._ignored)

    def fetch(self, note_id: str) -> NoteContent:
        converter = MarkdownConverter(self._children, self.block_types)
        markdown = converter.page(note_id)
        return NoteContent(
            markdown=markdown, images=tuple(converter.images), blocks=tuple(converter.blocks)
        )

    def read_image(self, ref: ImageRef) -> bytes:
        response = httpx.get(ref.url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        return response.content

    def _children(self, block_id: str) -> list[dict[str, Any]]:
        if block_id not in self._blocks:
            self._blocks[block_id] = collect_paginated_api(
                self._client.blocks.children.list, block_id=block_id
            )
        return self._blocks[block_id]


def _summary(
    page_id: str,
    title: str,
    path: tuple[str, ...],
    page: dict[str, Any],
    parent_id: str | None = None,
) -> NoteSummary:
    return NoteSummary(
        source_id=page_id,
        title=title,
        path=path,
        last_edited=datetime.fromisoformat(page["last_edited_time"]),
        parent_id=parent_id,
    )
