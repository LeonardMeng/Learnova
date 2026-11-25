"""Workspace use cases: create, list, set default, remove, and work out which workspace a command
should act on. The CLI (and a future HTTP API) call this; it never prints anything."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from learnova.errors import ConflictError, LearnovaError, NotFoundError
from learnova.home import (
    WORKSPACE_FILE,
    WorkspaceFile,
    find_workspace_file,
    read_workspace_file,
    write_workspace_file,
)
from learnova.models import User, Workspace
from learnova.storage.database import Repositories
from learnova.storage.repositories import WorkspaceNameTaken, WorkspaceNotFound


class WorkspaceError(LearnovaError):
    pass


class NoSuchWorkspace(WorkspaceError, NotFoundError):
    pass


class WorkspaceExists(WorkspaceError, ConflictError):
    pass


@dataclass(frozen=True)
class WorkspaceListing:
    workspace: Workspace
    found: bool  # directory and its learnova.toml still exist and point at this workspace
    current: bool  # the workspace a command run from `cwd` would use


class WorkspaceService:
    def __init__(self, repos: Repositories):
        self._repos = repos
        self._user: User | None = None

    @property
    def user(self) -> User:
        if self._user is None:
            self._user = self._repos.users.get_or_create_local()
        return self._user

    def create(self, name: str, notion_page_id: str, path: Path) -> Workspace:
        name = name.strip()
        if not name:
            raise WorkspaceError("Workspace name can't be empty.")
        directory = path.expanduser().resolve()
        marker = directory / WORKSPACE_FILE
        if marker.exists():
            raise WorkspaceError(
                f"{directory} already contains {WORKSPACE_FILE}. If that workspace was removed, "
                "delete the file first."
            )
        if directory.exists() and not directory.is_dir():
            raise WorkspaceError(f"{directory} exists and is not a directory.")
        if self._repos.workspaces.get_by_name(self.user.id, name) is not None:
            raise WorkspaceExists(f"A workspace named {name!r} already exists.")

        workspace_id = uuid.uuid4()
        directory.mkdir(parents=True, exist_ok=True)
        write_workspace_file(directory, WorkspaceFile(workspace_id, name, notion_page_id))
        try:
            return self._repos.workspaces.add(
                self.user.id, name, notion_page_id, directory, workspace_id=workspace_id
            )
        except WorkspaceNameTaken:
            marker.unlink(missing_ok=True)
            raise WorkspaceExists(f"A workspace named {name!r} already exists.") from None
        except Exception:
            marker.unlink(missing_ok=True)
            raise

    def list(self, cwd: Path | None = None) -> list[WorkspaceListing]:
        try:
            current = self.current(cwd)
        except WorkspaceError:
            current = None
        return [
            WorkspaceListing(
                ws, found=_is_found(ws), current=current is not None and ws.id == current.id
            )
            for ws in self._repos.workspaces.list(self.user.id)
        ]

    def use(self, name: str) -> Workspace:
        try:
            return self._repos.workspaces.set_default(self.user.id, name)
        except WorkspaceNotFound:
            raise NoSuchWorkspace(_not_found(name)) from None

    def remove(self, name: str) -> Workspace:
        try:
            return self._repos.workspaces.remove(self.user.id, name)
        except WorkspaceNotFound:
            raise NoSuchWorkspace(_not_found(name)) from None

    def current(self, cwd: Path | None = None, name: str | None = None) -> Workspace:
        """Which workspace a working command uses: --workspace NAME if given, else the one whose
        learnova.toml is in cwd or a parent directory, else the default."""
        if name is not None:
            ws = self._repos.workspaces.get_by_name(self.user.id, name)
            if ws is None:
                raise NoSuchWorkspace(_not_found(name))
            return ws
        marker = find_workspace_file(cwd)
        if marker is not None:
            ws = self._repos.workspaces.get(read_workspace_file(marker).id)
            if ws is None:
                raise WorkspaceError(f"{marker} belongs to a workspace that isn't registered.")
            return ws
        ws = self._repos.workspaces.get_default(self.user.id)
        if ws is None:
            raise WorkspaceError(
                "Not inside a workspace and no default is set. "
                "Run `learnova workspace new` or `learnova workspace use <name>`."
            )
        return ws


def _is_found(ws: Workspace) -> bool:
    marker = ws.path / WORKSPACE_FILE
    try:
        return marker.is_file() and read_workspace_file(marker).id == ws.id
    except (OSError, KeyError, ValueError):
        return False


def _not_found(name: str) -> str:
    return f"No workspace named {name!r}. See `learnova workspace list`."
