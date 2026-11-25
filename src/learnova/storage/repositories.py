"""Repository interfaces: the only storage API that services and the CLI know about. Swapping
SQLite for Postgres, or for a fake in tests, means providing another implementation of these."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from learnova.models import User, Workspace


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
