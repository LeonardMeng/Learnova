"""Notion access. For Milestone 1 this only walks the sub-page tree; Milestone 3 grows it into
NotionSource (list_notes / fetch)."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from notion_client import Client
from notion_client.helpers import iterate_paginated_api

TOKEN_ENV = "NOTION_API_KEY"

# 32 hex characters, optionally written as a dashed UUID.
_ID_RE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)


class NotionConfigError(Exception):
    """Raised when the token or page ID is missing or malformed."""


@dataclass(frozen=True)
class PageNode:
    id: str
    title: str
    depth: int  # 0 = direct sub-page of the root page
    kind: Literal["page", "database"] = "page"


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
    page = client.pages.retrieve(page_id=page_id)
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"]) or "(untitled)"
    return "(untitled)"


def walk_subpages(client: Client, page_id: str, depth: int = 0) -> Iterator[PageNode]:
    """Yield every sub-page under page_id, depth first, in the order they appear on the page.

    Sub-pages nested inside toggles, columns and other container blocks are included at the
    same depth as the page that contains them. Databases are listed but not entered.
    """
    for block in iterate_paginated_api(client.blocks.children.list, block_id=page_id):
        btype = block.get("type")
        if btype == "child_page":
            title = block["child_page"].get("title") or "(untitled)"
            yield PageNode(id=block["id"], title=title, depth=depth)
            yield from walk_subpages(client, block["id"], depth + 1)
        elif btype == "child_database":
            title = block["child_database"].get("title") or "(untitled)"
            yield PageNode(id=block["id"], title=title, depth=depth, kind="database")
        elif block.get("has_children"):
            # A toggle, column, callout, etc. can hold sub-pages: search inside it.
            yield from walk_subpages(client, block["id"], depth)
