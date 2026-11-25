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
