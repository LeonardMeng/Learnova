from datetime import UTC, datetime
from pathlib import Path

import pytest

from learnova.models import NoteSummary
from learnova.sources.base import NoteSource
from learnova.sources.notion import NotionSource
from learnova.storage.database import open_repositories
from learnova.sync import cache_filename, content_hash, sync_workspace

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
ROOT = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"


class FakeSource(NoteSource):
    def __init__(self, notes: dict[str, tuple[NoteSummary, str]]):
        self.notes = notes

    def list_notes(self):
        return [s for s, _ in self.notes.values()]

    def fetch(self, note_id):
        return self.notes[note_id][1]


def summary(sid, title, path, edited=T1):
    return NoteSummary(source_id=sid, title=title, path=path, last_edited=edited)


@pytest.fixture
def setup(tmp_path):
    repos = open_repositories(f"sqlite:///{tmp_path / 'db.sqlite'}")
    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, "w", ROOT, tmp_path / "ws")
    ws.path.mkdir()
    return repos, ws


def test_cache_filename():
    assert cache_filename("Redis: Persistence!", SUB) == "redis-persistence-22222222.md"
    assert cache_filename("数据结构", SUB) == "数据结构-22222222.md"
    assert cache_filename("???", SUB) == "untitled-22222222.md"


def test_sync_writes_cache_and_notes(setup):
    repos, ws = setup
    source = FakeSource(
        {
            ROOT: (summary(ROOT, "Interview", ("Interview",)), "→ Sub-page: Redis\n"),
            SUB: (summary(SUB, "Redis", ("Interview", "Redis")), "# Redis\n\nIn-memory.\n"),
        }
    )
    seen = []
    result = sync_workspace(ws, source, repos.notes, on_note=seen.append)

    assert [s.title for s in seen] == ["Interview", "Redis"]
    text = (ws.path / "cache" / "redis-22222222.md").read_text()
    assert text.startswith('---\ntitle: "Redis"\nsource_id: ' + SUB)
    assert 'path: ["Interview", "Redis"]' in text
    assert text.endswith("# Redis\n\nIn-memory.\n")

    note = repos.notes.get_by_source_id(ws.id, SUB)
    assert note.content_hash == content_hash("# Redis\n\nIn-memory.\n")
    assert note.path == ("Interview", "Redis")
    assert note.last_edited == T1
    assert len(result.notes) == len(repos.notes.list(ws.id)) == 2


def test_resync_updates_in_place_and_handles_rename(setup):
    repos, ws = setup
    source = FakeSource({SUB: (summary(SUB, "Redis", ("I", "Redis")), "v1\n")})
    sync_workspace(ws, source, repos.notes)
    first = repos.notes.get_by_source_id(ws.id, SUB)

    source.notes[SUB] = (summary(SUB, "Redis Basics", ("I", "Redis Basics")), "v2\n")
    sync_workspace(ws, source, repos.notes)

    files = sorted(p.name for p in (ws.path / "cache").iterdir())
    assert files == ["redis-basics-22222222.md"]
    note = repos.notes.get_by_source_id(ws.id, SUB)
    assert note.id == first.id and note.title == "Redis Basics"
    assert note.content_hash == content_hash("v2\n")
    assert len(repos.notes.list(ws.id)) == 1


def test_removing_workspace_removes_its_notes(setup):
    repos, ws = setup
    sync_workspace(ws, FakeSource({SUB: (summary(SUB, "R", ("R",)), "x\n")}), repos.notes)
    repos.workspaces.remove(ws.user_id, ws.name)
    assert repos.notes.list(ws.id) == []


# --- NotionSource against a fake Notion client -------------------------------------------


class FakeNotion:
    def __init__(self, pages: dict[str, dict], blocks: dict[str, list[dict]]):
        self.calls: list[str] = []
        outer = self

        class Pages:
            def retrieve(self, page_id):
                return pages[page_id]

        class Children:
            def list(self, block_id, start_cursor=None, **_):
                outer.calls.append(block_id)
                return {"results": blocks.get(block_id, []), "has_more": False, "next_cursor": None}

        class Blocks:
            children = Children()

        self.pages = Pages()
        self.blocks = Blocks()


def page(title, edited="2026-11-24T10:00:00.000Z"):
    return {
        "last_edited_time": edited,
        "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
    }


def block(id, type, has_children=False, **data):
    return {"id": id, "type": type, "has_children": has_children, type: data}


def test_notion_source_paths_databases_and_block_cache():
    para = {"rich_text": [{"type": "text", "plain_text": "hi", "annotations": {}}]}
    client = FakeNotion(
        pages={"root": page("Interview"), "a": page("Redis"), "a1": page("Persistence"),
               "t1": page("In toggle", "2026-11-25T01:02:00.000Z")},
        blocks={
            "root": [block("a", "child_page", True, title="Redis"),
                     block("tg", "toggle", True, rich_text=[]),
                     block("db", "child_database", title="Cards")],
            "a": [block("p", "paragraph", **para), block("a1", "child_page", title="Persistence")],
            "tg": [block("t1", "child_page", title="In toggle")],
        },
    )  # fmt: skip
    source = NotionSource(client, "root")
    notes = source.list_notes()
    assert [n.path for n in notes] == [
        ("Interview",),
        ("Interview", "Redis"),
        ("Interview", "Redis", "Persistence"),
        ("Interview", "In toggle"),
    ]
    assert notes[3].last_edited == datetime(2026, 11, 25, 1, 2, tzinfo=UTC)
    assert source.skipped_databases == ["Cards"]

    calls_after_listing = len(client.calls)
    assert source.fetch("a") == "hi\n\n→ Sub-page: Persistence\n"
    assert len(client.calls) == calls_after_listing  # served from the block cache
    assert source.block_types["paragraph"] == 1


def test_cache_dir_is_inside_workspace(setup):
    repos, ws = setup
    result = sync_workspace(ws, FakeSource({}), repos.notes)
    assert result.cache_dir == Path(ws.path) / "cache" and result.cache_dir.is_dir()


def test_sync_command_end_to_end(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from learnova.cli import workspace as workspace_cli
    from learnova.cli.main import app
    from learnova.sources import notion

    para = {"rich_text": [{"type": "text", "plain_text": "Redis is fast.", "annotations": {}}]}
    client = FakeNotion(
        pages={ROOT: page("Interview"), SUB: page("Redis")},
        blocks={
            ROOT: [block(SUB, "child_page", True, title="Redis"),
                   block("db", "child_database", title="Cards")],
            SUB: [block("p", "paragraph", **para), block("img", "image", caption=[])],
        },
    )  # fmt: skip
    monkeypatch.setattr(notion, "make_client", lambda: client)
    monkeypatch.setattr(workspace_cli, "check_notion_page", lambda page_id: "Interview")
    runner = CliRunner()
    ws_dir = tmp_path / "interview"
    assert runner.invoke(app, ["workspace", "new", "interview", "--notion", ROOT,
                               "--path", str(ws_dir)]).exit_code == 0  # fmt: skip

    monkeypatch.chdir(ws_dir)
    r = runner.invoke(app, ["sync"])
    print(r.output)
    assert r.exit_code == 0, r.output
    assert "✓ Interview / Redis" in r.output
    assert "Synced 2 note(s)" in r.output
    assert "image (placeholder) 1" in r.output
    assert "Skipped 1 database(s): 'Cards'" in r.output
    assert (ws_dir / "cache" / "redis-22222222.md").is_file()
    assert (ws_dir / "cache" / "interview-11111111.md").is_file()
