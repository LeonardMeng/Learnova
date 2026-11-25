"""SQLAlchemy implementation of the repository interfaces. Each method runs in its own
transaction (`with self._sessions.begin()` commits on success, rolls back on error) and
converts rows to domain models before returning."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from learnova.models import User, Workspace
from learnova.storage.repositories import (
    UserRepository,
    WorkspaceNameTaken,
    WorkspaceNotFound,
    WorkspaceRepository,
)
from learnova.storage.tables import UserRow, WorkspaceRow

LOCAL_USER_NAME = "local"


def _user(row: UserRow) -> User:
    return User(id=row.id, name=row.name, created_at=row.created_at)


def _workspace(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        notion_page_id=row.notion_page_id,
        path=Path(row.path),
        is_default=row.is_default,
        created_at=row.created_at,
    )


class SqlUserRepository(UserRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def get_or_create_local(self) -> User:
        with self._sessions.begin() as s:
            row = s.scalars(select(UserRow).where(UserRow.name == LOCAL_USER_NAME)).first()
            if row is None:
                row = UserRow(name=LOCAL_USER_NAME)
                s.add(row)
                s.flush()  # fills in id and created_at
            return _user(row)


class SqlWorkspaceRepository(WorkspaceRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def add(self, user_id, name, notion_page_id, path, workspace_id=None) -> Workspace:
        with self._sessions.begin() as s:
            if self._by_name(s, user_id, name) is not None:
                raise WorkspaceNameTaken(name)
            count = s.scalar(
                select(func.count())
                .select_from(WorkspaceRow)
                .where(WorkspaceRow.user_id == user_id)
            )
            row = WorkspaceRow(
                id=workspace_id or uuid.uuid4(),
                user_id=user_id,
                name=name,
                notion_page_id=notion_page_id,
                path=str(path),
                is_default=count == 0,
            )
            s.add(row)
            s.flush()
            return _workspace(row)

    def list(self, user_id) -> list[Workspace]:
        with self._sessions() as s:
            rows = s.scalars(
                select(WorkspaceRow)
                .where(WorkspaceRow.user_id == user_id)
                .order_by(WorkspaceRow.created_at)
            )
            return [_workspace(r) for r in rows]

    def get(self, workspace_id) -> Workspace | None:
        with self._sessions() as s:
            row = s.get(WorkspaceRow, workspace_id)
            return _workspace(row) if row else None

    def get_by_name(self, user_id, name) -> Workspace | None:
        with self._sessions() as s:
            row = self._by_name(s, user_id, name)
            return _workspace(row) if row else None

    def get_default(self, user_id) -> Workspace | None:
        with self._sessions() as s:
            row = s.scalars(
                select(WorkspaceRow).where(
                    WorkspaceRow.user_id == user_id, WorkspaceRow.is_default.is_(True)
                )
            ).first()
            return _workspace(row) if row else None

    def set_default(self, user_id, name) -> Workspace:
        with self._sessions.begin() as s:
            row = self._by_name(s, user_id, name)
            if row is None:
                raise WorkspaceNotFound(name)
            s.execute(
                update(WorkspaceRow).where(WorkspaceRow.user_id == user_id).values(is_default=False)
            )
            row.is_default = True
            s.flush()
            return _workspace(row)

    def remove(self, user_id, name) -> Workspace:
        with self._sessions.begin() as s:
            row = self._by_name(s, user_id, name)
            if row is None:
                raise WorkspaceNotFound(name)
            removed = _workspace(row)
            s.delete(row)
            return removed

    @staticmethod
    def _by_name(s: Session, user_id: uuid.UUID, name: str) -> WorkspaceRow | None:
        return s.scalars(
            select(WorkspaceRow).where(WorkspaceRow.user_id == user_id, WorkspaceRow.name == name)
        ).first()
