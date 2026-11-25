import uuid

from learnova.config import load_settings
from learnova.home import (
    WORKSPACE_FILE,
    WorkspaceFile,
    ensure_home,
    find_workspace_file,
    read_workspace_file,
    write_workspace_file,
)


def test_ensure_home_creates_default_config(learnova_home):
    assert not learnova_home.exists()
    ensure_home()
    assert (learnova_home / "config.toml").is_file()
    assert load_settings().database_url == f"sqlite:///{learnova_home.resolve()}/learnova.db"


def test_config_database_url_override(learnova_home):
    ensure_home()
    (learnova_home / "config.toml").write_text('[database]\nurl = "sqlite:///other.db"\n')
    assert load_settings().database_url == "sqlite:///other.db"


def test_workspace_file_roundtrip(tmp_path):
    wf = WorkspaceFile(uuid.uuid4(), 'Interview "Qs"', "3e6135e3-ecf0-4389-846c-e4126df126b0")
    write_workspace_file(tmp_path, wf)
    assert read_workspace_file(tmp_path / WORKSPACE_FILE) == wf


def test_find_workspace_file_walks_up(tmp_path):
    ws = tmp_path / "notes"
    deep = ws / "cache" / "a" / "b"
    deep.mkdir(parents=True)
    write_workspace_file(ws, WorkspaceFile(uuid.uuid4(), "notes", "x"))
    assert find_workspace_file(deep) == ws / WORKSPACE_FILE
    assert find_workspace_file(tmp_path) is None
