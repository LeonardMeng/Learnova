"""SQLAlchemy table definitions. Alembic compares these classes with the database to generate
migrations; application code never imports them directly, only the repositories do."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, MetaData, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Always timezone-aware UTC. Postgres keeps the timezone itself; SQLite drops it, so it is
    put back on read. Naive datetimes are rejected on write."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use datetime.now(UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    # Named constraints let Alembic alter tables on SQLite (batch mode) and keep names
    # identical on Postgres.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WorkspaceRow(Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    notion_page_id: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1024))
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NoteRow(Base):
    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("workspace_id", "source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Notes belong to a workspace: removing the workspace removes its notes (not knowledge points).
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(String(64))  # Notion page ID
    title: Mapped[str] = mapped_column(String(500))
    path: Mapped[list[str]] = mapped_column(JSON)  # hierarchy, top page first
    last_edited_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the Markdown body
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime)
