"""Domain objects shared by all layers. Repositories return these, never database rows, so
nothing above the storage layer depends on SQLAlchemy."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    notion_page_id: str
    path: Path
    is_default: bool
    created_at: datetime


class NoteSummary(BaseModel):
    """What a Source knows about a note without reading its content."""

    model_config = ConfigDict(frozen=True)

    source_id: str  # e.g. the Notion page ID
    title: str
    path: tuple[str, ...]  # titles from the workspace's top page down to this note, inclusive
    last_edited: datetime


class Note(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_id: str
    title: str
    path: tuple[str, ...]
    last_edited: datetime
    content_hash: str
    synced_at: datetime
