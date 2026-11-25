"""Repository interfaces: the only storage API that services and the CLI know about. Swapping
SQLite for Postgres, or for a fake in tests, means providing another implementation of these."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from learnova.models import (
    ImageRef,
    KnowledgePoint,
    Note,
    NoteImage,
    NoteSummary,
    PointSource,
    User,
    Workspace,
)


class WorkspaceNotFound(LookupError):
    pass


class WorkspaceNameTaken(ValueError):
    pass


class UserRepository(ABC):
    @abstractmethod
    def get_or_create_local(self) -> User:
        """Version 1 has a single local user; create it on first use."""


class WorkspaceRepository(ABC):
    @abstractmethod
    def add(
        self,
        user_id: uuid.UUID,
        name: str,
        notion_page_id: str,
        path: Path,
        workspace_id: uuid.UUID | None = None,
    ) -> Workspace:
        """Register a workspace. The user's first workspace becomes the default.
        Raises WorkspaceNameTaken if the user already has one with this name."""

    @abstractmethod
    def list(self, user_id: uuid.UUID) -> list[Workspace]: ...

    @abstractmethod
    def get(self, workspace_id: uuid.UUID) -> Workspace | None: ...

    @abstractmethod
    def get_by_name(self, user_id: uuid.UUID, name: str) -> Workspace | None: ...

    @abstractmethod
    def get_default(self, user_id: uuid.UUID) -> Workspace | None: ...

    @abstractmethod
    def set_default(self, user_id: uuid.UUID, name: str) -> Workspace:
        """Raises WorkspaceNotFound."""

    @abstractmethod
    def remove(self, user_id: uuid.UUID, name: str) -> Workspace:
        """Unregister and return the removed workspace. Raises WorkspaceNotFound."""


class NoteRepository(ABC):
    @abstractmethod
    def upsert(
        self,
        workspace_id: uuid.UUID,
        summary: NoteSummary,
        content_hash: str,
        synced_at: datetime,
    ) -> Note:
        """Insert the note, or update it if this workspace already has one with that source_id."""

    @abstractmethod
    def list(self, workspace_id: uuid.UUID) -> list[Note]: ...

    @abstractmethod
    def get_by_source_id(self, workspace_id: uuid.UUID, source_id: str) -> Note | None: ...

    @abstractmethod
    def delete(self, workspace_id: uuid.UUID, source_id: str) -> None:
        """Remove the note if it exists."""

    @abstractmethod
    def mark_reviewed(self, note_id: uuid.UUID, content_hash: str) -> None: ...


class ImageRepository(ABC):
    @abstractmethod
    def list_for_note(self, note_id: uuid.UUID) -> list[NoteImage]: ...

    @abstractmethod
    def list_for_workspace(self, workspace_id: uuid.UUID) -> list[NoteImage]: ...

    @abstractmethod
    def save(
        self,
        note_id: uuid.UUID,
        ref: ImageRef,
        path: str | None,
        image_hash: str | None,
        mime_type: str | None,
    ) -> NoteImage:
        """Insert or update the image at (note, block). The description is kept when the image
        hash is unchanged and cleared when it changes."""

    @abstractmethod
    def delete(self, image_id: uuid.UUID) -> None: ...

    @abstractmethod
    def find_description(self, image_hash: str) -> tuple[str, str | None] | None:
        """(description, described_by) from any image with this hash, in any workspace."""

    @abstractmethod
    def set_description(self, image_id: uuid.UUID, description: str, described_by: str) -> None: ...


class PointNotFound(LookupError):
    pass


class KnowledgeRepository(ABC):
    @abstractmethod
    def get(self, point_id: uuid.UUID) -> KnowledgePoint | None: ...

    @abstractmethod
    def list_for_user(self, user_id: uuid.UUID) -> list[KnowledgePoint]: ...

    @abstractmethod
    def list_for_note(self, note_id: uuid.UUID) -> list[KnowledgePoint]: ...

    @abstractmethod
    def sources(self, user_id: uuid.UUID) -> list[PointSource]:
        """Every (point, note) link of the user's points, with note path and workspace name."""

    @abstractmethod
    def create(
        self, user_id: uuid.UUID, name: str, summary: str, importance: int
    ) -> KnowledgePoint: ...

    @abstractmethod
    def update(self, point_id: uuid.UUID, name: str, summary: str) -> KnowledgePoint:
        """Change name and summary; importance is left alone."""

    @abstractmethod
    def set_importance(self, point_id: uuid.UUID, importance: int) -> KnowledgePoint: ...

    @abstractmethod
    def link(self, point_id: uuid.UUID, note_id: uuid.UUID) -> None:
        """Idempotent."""

    @abstractmethod
    def unlink(self, point_id: uuid.UUID, note_id: uuid.UUID) -> None: ...

    @abstractmethod
    def merge(self, point_id: uuid.UUID, into_id: uuid.UUID) -> KnowledgePoint:
        """Move point_id's links onto into_id and delete point_id, in one transaction.
        Raises PointNotFound."""
