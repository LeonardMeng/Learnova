"""End-to-end: the real CLI against a temporary user home; only the Notion check is faked."""

import pytest
from typer.testing import CliRunner

from learnova.cli import workspace as workspace_cli
from learnova.cli.main import app
from learnova.home import WORKSPACE_FILE
from learnova.sources.notion import NotionConfigError

PAGE_A = "3e6135e3ecf04389846ce4126df126b0"
PAGE_B = "aaaaaaaabbbbccccddddeeeeeeeeeeee"

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_notion(monkeypatch):
    monkeypatch.setattr(workspace_cli, "check_notion_page", lambda page_id: "A page")


def marks(output: str, name: str) -> str:
    """The two marker characters (→ current, ★ default) on the row for `name`."""
    line = next(line for line in output.splitlines() if name in line.split())
    return line[:2]


def run(*args, cwd=None, monkeypatch=None):
    if cwd is not None:
        monkeypatch.chdir(cwd)
    return runner.invoke(app, list(args))


def test_two_workspaces_in_unrelated_directories(tmp_path, monkeypatch):
    dir_a = tmp_path / "study" / "interview"
    dir_b = tmp_path / "elsewhere" / "ml-notes"
    other = tmp_path / "somewhere-else"
    other.mkdir()

    r = run("workspace", "new", "interview", "--notion", PAGE_A, "--path", str(dir_a))
    assert r.exit_code == 0, r.output
    assert "default" in r.output
    assert (dir_a / WORKSPACE_FILE).is_file()

    dir_b.mkdir(parents=True)
    r = run("workspace", "new", "ml", "--notion", PAGE_B, cwd=dir_b, monkeypatch=monkeypatch)
    assert r.exit_code == 0, r.output
    assert "default" not in r.output

    # From an unrelated directory the default is current; from inside dir_b, ml is.
    r = run("workspace", "list", cwd=other, monkeypatch=monkeypatch)
    assert marks(r.output, "interview") == "→★"
    assert marks(r.output, "ml") == "  "

    sub = dir_b / "cache"
    sub.mkdir()
    r = run("workspace", "list", cwd=sub, monkeypatch=monkeypatch)
    assert marks(r.output, "ml") == "→ "
    assert marks(r.output, "interview") == " ★"

    r = run("workspace", "use", "ml")
    assert r.exit_code == 0 and "'ml'" in r.output
    r = run("workspace", "list", cwd=other, monkeypatch=monkeypatch)
    assert marks(r.output, "ml") == "→★"
    assert marks(r.output, "interview") == "  "
    assert "3e6135e3-ecf0-4389-846c-e4126df126b0" in r.output  # full ID, not truncated


def test_list_marks_missing_directory(tmp_path):
    d = tmp_path / "gone"
    run("workspace", "new", "gone", "--notion", PAGE_A, "--path", str(d))
    (d / WORKSPACE_FILE).unlink()
    r = run("workspace", "list")
    assert "not found" in r.output


def test_remove_keeps_files(tmp_path):
    d = tmp_path / "w"
    run("workspace", "new", "w", "--notion", PAGE_A, "--path", str(d))
    r = run("workspace", "remove", "w")
    assert r.exit_code == 0
    assert "was the default" in r.output
    assert (d / WORKSPACE_FILE).is_file()
    assert "No workspaces yet" in run("workspace", "list").output


def test_errors(tmp_path):
    d = tmp_path / "w"
    assert run("workspace", "new", "w", "--notion", PAGE_A, "--path", str(d)).exit_code == 0

    r = run("workspace", "new", "w", "--notion", PAGE_A, "--path", str(tmp_path / "x"))
    assert r.exit_code == 1 and "already exists" in r.output

    r = run("workspace", "new", "w2", "--notion", PAGE_A, "--path", str(d))
    assert r.exit_code == 1 and "already contains" in r.output

    r = run("workspace", "use", "nope")
    assert r.exit_code == 1 and "No workspace named" in r.output

    r = run("workspace", "new", "x", "--notion", "not-a-page", "--path", str(tmp_path / "y"))
    assert r.exit_code == 1 and not (tmp_path / "y").exists()


def test_notion_check_failure_creates_nothing(tmp_path, monkeypatch):
    def deny(page_id):
        raise NotionConfigError("NOTION_API_KEY is not set.")

    monkeypatch.setattr(workspace_cli, "check_notion_page", deny)
    d = tmp_path / "w"
    r = run("workspace", "new", "w", "--notion", PAGE_A, "--path", str(d))
    assert r.exit_code == 1 and "NOTION_API_KEY" in r.output
    assert not d.exists()
    assert "No workspaces yet" in run("workspace", "list").output
