"""Opens the database from a URL, brings its schema up to date with Alembic, and hands back the
repositories. This is the one place that wires a URL to concrete implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker

from learnova.storage.repositories import UserRepository, WorkspaceRepository
from learnova.storage.sqlalchemy_repos import SqlUserRepository, SqlWorkspaceRepository

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@dataclass(frozen=True)
class Repositories:
    users: UserRepository
    workspaces: WorkspaceRepository


def make_engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # SQLite ignores foreign keys unless asked, per connection.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def migrate(engine: Engine) -> None:
    """Apply any migrations the database hasn't seen yet (a no-op when up to date)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def open_repositories(url: str) -> Repositories:
    engine = make_engine(url)
    migrate(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return Repositories(
        users=SqlUserRepository(sessions), workspaces=SqlWorkspaceRepository(sessions)
    )
