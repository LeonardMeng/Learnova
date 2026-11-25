from datetime import UTC, datetime
from pathlib import Path

import pytest

from learnova.models import NoteSummary
from learnova.sources.base import NoteSource
from learnova.sources.notion import NotionSource
from learnova.storage.database import open_repositories
from learnova.sync import Change, cache_filename, content_hash, sync_workspace

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
ROOT = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"


class FakeSource(NoteSource):
    def __init__(self, notes: dict[str, tuple[NoteSummary, str]]):
        self.notes = notes
        self.fetched: list[str] = []

    def list_notes(self):
        return [s for s, _ in self.notes.values()]

    def fetch(self, note_id):
        self.fetched.append(note_id)
        return self.notes[note_id][1]


def summary(sid, title, path, edited=T1, parent=None):
    return NoteSummary(source_id=sid, title=title, path=path, last_edited=edited, parent_id=parent)


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
    result = sync_workspace(ws, source, repos.notes, on_change=seen.append)

    assert [(c.kind, c.path[-1]) for c in seen] == [
        (Change.ADDED, "Interview"),
        (Change.ADDED, "Redis"),
    ]
    text = (ws.path / "cache" / "redis-22222222.md").read_text()
    assert text.startswith('---\ntitle: "Redis"\nsource_id: ' + SUB)
    assert 'path: ["Interview", "Redis"]' in text
    assert text.endswith("# Redis\n\nIn-memory.\n")

    note = repos.notes.get_by_source_id(ws.id, SUB)
    assert note.content_hash == content_hash("# Redis\n\nIn-memory.\n")
    assert note.path == ("Interview", "Redis")
    assert note.last_edited == T1
    assert result.downloaded == len(repos.notes.list(ws.id)) == 2


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
    assert [n.parent_id for n in notes] == [None, "root", "a", "root"]  # toggle -> its page
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
    assert "+ Interview / Redis" in r.output
    assert "2 added, 0 changed, 0 renamed/moved, 0 deleted, 0 unchanged." in r.output
    assert "image (placeholder) 1" in r.output
    assert "Skipped 1 database(s): 'Cards'" in r.output
    assert (ws_dir / "cache" / "redis-22222222.md").is_file()
    assert (ws_dir / "cache" / "interview-11111111.md").is_file()


# --- change detection ----------------------------------------------------------------------

A = "33333333-3333-3333-3333-333333333333"
B = "44444444-4444-4444-4444-444444444444"
C = "55555555-5555-5555-5555-555555555555"


def kinds(result):
    return {c.path[-1]: c.kind for c in result.changes}


def baseline(setup):
    repos, ws = setup
    source = FakeSource(
        {
            A: (summary(A, "Redis", ("I", "Redis")), "redis v1\n"),
            B: (summary(B, "Database", ("I", "Database")), "db\n"),
            C: (summary(C, "Behaviour", ("I", "Behaviour")), "star\n"),
        }
    )
    sync_workspace(ws, source, repos.notes)
    source.fetched.clear()
    return repos, ws, source


def test_unchanged_notes_are_not_downloaded_again(setup):
    repos, ws, source = baseline(setup)
    result = sync_workspace(ws, source, repos.notes)
    assert set(kinds(result).values()) == {Change.UNCHANGED}
    assert source.fetched == [] and result.downloaded == 0


def test_added_changed_deleted(setup):
    repos, ws, source = baseline(setup)
    T2 = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
    new = "66666666-6666-6666-6666-666666666666"
    source.notes[A] = (summary(A, "Redis", ("I", "Redis"), T2), "redis v2\n")
    source.notes[new] = (summary(new, "Graphs", ("I", "Graphs"), T2), "bfs\n")
    del source.notes[C]

    result = sync_workspace(ws, source, repos.notes)
    assert kinds(result) == {
        "Redis": Change.CHANGED,
        "Graphs": Change.ADDED,
        "Database": Change.UNCHANGED,
        "Behaviour": Change.DELETED,
    }
    assert sorted(source.fetched) == sorted([A, new])  # Database was skipped
    assert repos.notes.get_by_source_id(ws.id, C) is None
    assert not (ws.path / "cache" / cache_filename("Behaviour", C)).exists()
    assert repos.notes.get_by_source_id(ws.id, A).content_hash == content_hash("redis v2\n")


def test_rename_and_move_with_same_content(setup):
    repos, ws, source = baseline(setup)
    T2 = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
    source.notes[B] = (summary(B, "Databases", ("I", "Storage", "Databases"), T2), "db\n")
    result = sync_workspace(ws, source, repos.notes)
    change = next(c for c in result.changes if c.source_id == B)
    assert change.kind == Change.RENAMED
    assert change.previous_path == ("I", "Database")
    files = {p.name for p in (ws.path / "cache").iterdir()}
    assert cache_filename("Databases", B) in files and cache_filename("Database", B) not in files


def test_edit_in_the_same_minute_as_last_sync_is_still_caught(setup):
    repos, ws = setup
    this_minute = datetime.now(UTC).replace(second=0, microsecond=0)
    source = FakeSource({A: (summary(A, "Redis", ("Redis",), this_minute), "v1\n")})
    sync_workspace(ws, source, repos.notes)

    # Edited seconds later: Notion still reports the same (rounded) last-edited time.
    source.notes[A] = (summary(A, "Redis", ("Redis",), this_minute), "v2\n")
    result = sync_workspace(ws, source, repos.notes)
    assert kinds(result) == {"Redis": Change.CHANGED}


def test_missing_cache_file_or_full_forces_download(setup):
    repos, ws, source = baseline(setup)
    (ws.path / "cache" / cache_filename("Redis", A)).unlink()
    sync_workspace(ws, source, repos.notes)
    assert source.fetched == [A]
    assert (ws.path / "cache" / cache_filename("Redis", A)).is_file()

    source.fetched.clear()
    result = sync_workspace(ws, source, repos.notes, full=True)
    assert sorted(source.fetched) == sorted([A, B, C])
    assert set(kinds(result).values()) == {Change.UNCHANGED}


def test_repository_delete(setup):
    repos, ws, _ = baseline(setup)
    repos.notes.delete(ws.id, A)
    repos.notes.delete(ws.id, A)  # deleting twice is fine
    assert {n.source_id for n in repos.notes.list(ws.id)} == {B, C}


def test_parent_is_refreshed_when_a_sub_page_is_renamed_added_or_deleted(setup):
    """Notion doesn't bump a parent's last-edited time when a sub-page is renamed, but the
    parent's Markdown lists sub-page titles, so the parent must be downloaded again."""
    repos, ws = setup
    P, X, Y = A, B, C
    source = FakeSource(
        {
            P: (summary(P, "Top", ("Top",)), "→ Sub-page: DSA\n"),
            X: (summary(X, "DSA", ("Top", "DSA"), parent=P), "trees\n"),
        }
    )
    sync_workspace(ws, source, repos.notes)

    # Rename X; the parent's timestamp stays the same but its content now shows the new title.
    T2 = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
    source.notes[X] = (summary(X, "DSA 2", ("Top", "DSA 2"), T2, parent=P), "trees\n")
    source.notes[P] = (summary(P, "Top", ("Top",)), "→ Sub-page: DSA 2\n")
    result = sync_workspace(ws, source, repos.notes)
    assert kinds(result) == {"Top": Change.CHANGED, "DSA 2": Change.RENAMED}

    # Nothing changed: nothing is downloaded.
    source.fetched.clear()
    sync_workspace(ws, source, repos.notes)
    assert source.fetched == []

    # Add Y, then delete it: the parent is downloaded both times.
    source.notes[Y] = (summary(Y, "Graphs", ("Top", "Graphs"), T2, parent=P), "bfs\n")
    sync_workspace(ws, source, repos.notes)
    assert sorted(source.fetched) == sorted([P, Y])

    source.fetched.clear()
    del source.notes[Y]
    result = sync_workspace(ws, source, repos.notes)
    assert source.fetched == [P]
    assert kinds(result)["Graphs"] == Change.DELETED


def test_moving_a_page_refreshes_old_and_new_parent(setup):
    repos, ws = setup
    P1, P2, X = A, B, C
    source = FakeSource(
        {
            P1: (summary(P1, "One", ("One",)), "x here\n"),
            P2: (summary(P2, "Two", ("Two",)), "empty\n"),
            X: (summary(X, "X", ("One", "X"), parent=P1), "x\n"),
        }
    )
    sync_workspace(ws, source, repos.notes)
    source.fetched.clear()

    source.notes[X] = (summary(X, "X", ("Two", "X"), parent=P2), "x\n")
    result = sync_workspace(ws, source, repos.notes)
    assert sorted(source.fetched) == sorted([P1, P2, X])
    assert kinds(result)["X"] == Change.RENAMED
