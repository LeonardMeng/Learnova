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
    parent_id: str | None = None  # source_id of the note this one sits under; None for the top


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
    reviewed_hash: str | None = None  # content_hash at the last review; None = never reviewed


class ImageRef(BaseModel):
    """An image found in a note, before it is downloaded."""

    model_config = ConfigDict(frozen=True)

    block_id: str  # where the image sits in the note
    source_key: str  # identifies the file itself; stays the same while the image is unchanged
    url: str  # where to download it now (Notion's links expire after an hour)
    caption: str = ""


class NoteContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    markdown: str  # images appear as <!-- image:BLOCK_ID --> placeholders
    images: tuple[ImageRef, ...] = ()


class NoteImage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    note_id: uuid.UUID
    block_id: str
    source_key: str
    caption: str
    path: str | None  # relative to the workspace, e.g. images/3fa9c1d2e4b5a6f7.png; None if failed
    image_hash: str | None  # sha256 of the file
    mime_type: str | None
    description: str | None
    described_by: str | None  # "provider:model" that wrote the description


class KnowledgePoint(BaseModel):
    """The smallest testable unit. Belongs to the user; linked to one or more notes."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    summary: str
    importance: int  # 1-5; the AI suggests it once, re-reviews never change it
    created_at: datetime
    updated_at: datetime


class PointSource(BaseModel):
    """Where a knowledge point comes from: one note, in one workspace."""

    model_config = ConfigDict(frozen=True)

    point_id: uuid.UUID
    note_id: uuid.UUID
    note_path: tuple[str, ...]
    workspace_name: str
