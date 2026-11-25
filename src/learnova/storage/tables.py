"""SQLAlchemy table definitions. Alembic compares these classes with the database to generate
migrations; application code never imports them directly, only the repositories do."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
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
    reviewed_hash: Mapped[str | None] = mapped_column(String(64))  # content_hash at last review


class NoteImageRow(Base):
    __tablename__ = "note_images"
    __table_args__ = (UniqueConstraint("note_id", "block_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    block_id: Mapped[str] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(Text)  # identifies the file in the source
    caption: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str | None] = mapped_column(String(1024))  # relative to the workspace
    image_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # description cache key
    mime_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    described_by: Mapped[str | None] = mapped_column(String(200))


class KnowledgePointRow(Base):
    __tablename__ = "knowledge_points"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class KpSourceRow(Base):
    """Links a knowledge point to a note. Deleting either side removes only the link."""

    __tablename__ = "kp_sources"

    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class AttemptRow(Base):
    __tablename__ = "attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # `kp merge` moves attempts before deleting a point, so this cascade only ever removes
    # attempts of a point that is deleted on purpose.
    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(32))  # QuestionType value
    answer: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)  # 0–5, one decimal
    feedback: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Options shown, correct answer and choice, for multiple choice and true/false.
    question_data: Mapped[dict | None] = mapped_column(JSON)


class MasteryRow(Base):
    """Cache of replaying a point's attempts; rebuilt whenever its attempts change."""

    __tablename__ = "mastery"

    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    mastery: Mapped[float] = mapped_column(Float)
    review_count: Mapped[int] = mapped_column(Integer)
    last_reviewed: Mapped[datetime | None] = mapped_column(UTCDateTime)
