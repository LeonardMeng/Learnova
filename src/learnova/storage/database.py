"""Opens the database from a URL, brings its schema up to date with Alembic, and hands back the
repositories. This is the one place that wires a URL to concrete implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from learnova.storage.repositories import (
    AttemptRepository,
    ImageRepository,
    IssuedQuestionRepository,
    KnowledgeRepository,
    NoteRepository,
    UserRepository,
    WebImportRepository,
    WorkspaceRepository,
)
from learnova.storage.sqlalchemy_repos import (
    SqlAttemptRepository,
    SqlImageRepository,
    SqlIssuedQuestionRepository,
    SqlKnowledgeRepository,
    SqlNoteRepository,
    SqlUserRepository,
    SqlWebImportRepository,
    SqlWorkspaceRepository,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@dataclass(frozen=True)
class Repositories:
    users: UserRepository
    workspaces: WorkspaceRepository
    notes: NoteRepository
    images: ImageRepository
    knowledge: KnowledgeRepository
    attempts: AttemptRepository
    issued: IssuedQuestionRepository
    web_imports: WebImportRepository


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # The API serves requests on several threads: let connections move between threads
        # (one thread at a time) and wait up to 30 s for another writer instead of failing.
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    else:
        engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # SQLite ignores foreign keys unless asked, per connection.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def migrate(url: str) -> None:
    """Apply any migrations the database hasn't seen yet (a no-op when up to date).

    Uses its own connection with foreign keys left OFF: on SQLite, Alembic changes a table by
    rebuilding it, and with foreign keys on, dropping the old table would cascade-delete rows
    in other tables (e.g. rebuilding `workspaces` would wipe `notes`).
    """
    engine = create_engine(url, poolclass=NullPool)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def open_repositories(url: str) -> Repositories:
    migrate(url)
    sessions = sessionmaker(make_engine(url), expire_on_commit=False)
    return Repositories(
        users=SqlUserRepository(sessions),
        workspaces=SqlWorkspaceRepository(sessions),
        notes=SqlNoteRepository(sessions),
        images=SqlImageRepository(sessions),
        knowledge=SqlKnowledgeRepository(sessions),
        attempts=SqlAttemptRepository(sessions),
        issued=SqlIssuedQuestionRepository(sessions),
        web_imports=SqlWebImportRepository(sessions),
    )
