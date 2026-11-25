from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import inspect

from learnova.storage.database import make_engine, open_repositories
from learnova.storage.repositories import WorkspaceNameTaken, WorkspaceNotFound


@pytest.fixture
def repos(tmp_path):
    return open_repositories(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def user(repos):
    return repos.users.get_or_create_local()


def test_migration_creates_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    open_repositories(url)
    open_repositories(url)  # second run is a no-op
    tables = set(inspect(make_engine(url)).get_table_names())
    assert {"users", "workspaces", "alembic_version"} <= tables


def test_local_user_is_created_once(repos):
    assert repos.users.get_or_create_local().id == repos.users.get_or_create_local().id


def test_first_workspace_becomes_default(repos, user):
    a = repos.workspaces.add(user.id, "a", "page-a", Path("/tmp/a"))
    b = repos.workspaces.add(user.id, "b", "page-b", Path("/tmp/b"))
    assert a.is_default and not b.is_default
    assert repos.workspaces.get_default(user.id).id == a.id


def test_names_are_unique_per_user(repos, user):
    repos.workspaces.add(user.id, "a", "p", Path("/tmp/a"))
    with pytest.raises(WorkspaceNameTaken):
        repos.workspaces.add(user.id, "a", "p", Path("/tmp/a2"))


def test_set_default_moves_the_flag(repos, user):
    repos.workspaces.add(user.id, "a", "p", Path("/tmp/a"))
    repos.workspaces.add(user.id, "b", "p", Path("/tmp/b"))
    repos.workspaces.set_default(user.id, "b")
    defaults = [w.name for w in repos.workspaces.list(user.id) if w.is_default]
    assert defaults == ["b"]
    with pytest.raises(WorkspaceNotFound):
        repos.workspaces.set_default(user.id, "nope")


def test_remove(repos, user):
    repos.workspaces.add(user.id, "a", "p", Path("/tmp/a"))
    removed = repos.workspaces.remove(user.id, "a")
    assert removed.name == "a" and removed.is_default
    assert repos.workspaces.list(user.id) == []
    assert repos.workspaces.get_default(user.id) is None
    with pytest.raises(WorkspaceNotFound):
        repos.workspaces.remove(user.id, "a")


def test_ids_are_uuids_and_times_are_utc_after_reload(repos, user):
    ws = repos.workspaces.add(user.id, "a", "p", Path("/tmp/a"))
    loaded = repos.workspaces.get(ws.id)
    assert loaded.id == ws.id and loaded.id.version == 4
    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.utcoffset() == UTC.utcoffset(None)
