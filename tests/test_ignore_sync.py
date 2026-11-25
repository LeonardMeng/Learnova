""".learnovaignore end to end: skipped subtrees aren't read, synced pages are hidden (not
deleted) from review, quiz and status, and come back when un-ignored."""

from datetime import UTC, datetime

import pytest
from test_sync import FakeNotion, block, page

from learnova.config import load_settings
from learnova.ignore import IGNORE_FILE, parse
from learnova.models import NoteContent, NoteSummary
from learnova.scheduler.weights import WeightConfig
from learnova.services import quiz as quiz_service
from learnova.services import status as status_service
from learnova.services.review import notes_to_review
from learnova.sources.base import IgnoredPage, NoteSource
from learnova.sources.notion import NotionSource
from learnova.storage.database import open_repositories
from learnova.sync import Change, cache_filename, sync_workspace

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
TOP, REDIS, BEH, STAR = "t0", "r1", "b1", "b2"


class Source(NoteSource):
    """Pages are (title, path, edited, markdown); `ignored` mimics what NotionSource reports."""

    def __init__(self, pages, ignored=()):
        self.pages, self.ignored, self.fetched = pages, list(ignored), []

    def list_notes(self):
        return [
            NoteSummary(source_id=sid, title=t, path=p, last_edited=e)
            for sid, (t, p, e, _) in self.pages.items()
        ]

    def fetch(self, note_id):
        self.fetched.append(note_id)
        return NoteContent(markdown=self.pages[note_id][3])

    def read_image(self, ref):
        raise AssertionError

    def ignored_pages(self):
        return self.ignored


ALL = {
    TOP: ("Interview", ("Interview",), T1, "Index of questions, long enough to be reviewed.\n"),
    REDIS: (
        "Redis",
        ("Interview", "Redis"),
        T1,
        "Redis keeps data in memory; RDB and AOF persist it.\n",
    ),
    BEH: (
        "Behaviour",
        ("Interview", "Behaviour"),
        T1,
        "Use STAR to structure behavioural answers.\n",
    ),
    STAR: (
        "STAR",
        ("Interview", "Behaviour", "STAR"),
        T1,
        "Situation, task, action, result: a method.\n",
    ),
}
HIDE_BEHAVIOUR = [IgnoredPage(BEH, ("Interview", "Behaviour"))]  # STAR is under it: not listed


def visible(pages, *hidden):
    return {k: v for k, v in pages.items() if k not in hidden}


@pytest.fixture
def setup(tmp_path, learnova_home):
    repos = open_repositories(load_settings().database_url)
    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, "w", TOP, tmp_path / "w")
    ws.path.mkdir()
    sync_workspace(ws, Source(dict(ALL)), repos.notes, repos.images)
    notes = {n.source_id: n for n in repos.notes.list(ws.id)}
    points = {}
    for sid, name in ((REDIS, "Persistence"), (BEH, "Behavioural answers"), (STAR, "STAR method")):
        p = repos.knowledge.create(user.id, name, "s", 4)
        repos.knowledge.link(p.id, notes[sid].id)
        points[name] = p
    return repos, ws, points


def kinds(result):
    return {c.path[-1]: c.kind for c in result.changes if c.kind != Change.UNCHANGED}


def test_ignoring_hides_instead_of_deleting(setup):
    repos, ws, points = setup
    source = Source(visible(ALL, BEH, STAR), HIDE_BEHAVIOUR)
    result = sync_workspace(ws, source, repos.notes, repos.images)

    assert kinds(result) == {"Behaviour": Change.IGNORED, "STAR": Change.IGNORED}
    assert result.ignored == tuple(HIDE_BEHAVIOUR)
    hidden = {n.title for n in repos.notes.list(ws.id) if n.ignored}
    assert hidden == {"Behaviour", "STAR"}  # still in the database
    assert (ws.path / "cache" / cache_filename("STAR", STAR)).is_file()  # cache kept

    # Hidden from review, quiz and status.
    assert {n.title for n in notes_to_review(repos, ws, True)} == {"Interview", "Redis"}
    quizzable = {i.point.name for i in quiz_service.candidates(repos, T2, WeightConfig())}
    assert quizzable == {"Persistence"}
    st = status_service.status(repos, T2, WeightConfig())
    orphans = {r.point.name for r in st.weakest if not r.sources}
    assert orphans == {"Behavioural answers", "STAR method"}

    # Syncing again reports nothing new and doesn't keep re-downloading the parent.
    source.fetched.clear()
    again = sync_workspace(ws, source, repos.notes, repos.images)
    assert kinds(again) == {} and source.fetched == []


def test_unignoring_restores_links_without_new_review(setup):
    repos, ws, points = setup
    for note in repos.notes.list(ws.id):  # everything was reviewed before it was hidden
        repos.notes.mark_reviewed(note.id, note.content_hash)
    sync_workspace(ws, Source(visible(ALL, BEH, STAR), HIDE_BEHAVIOUR), repos.notes, repos.images)

    edited = dict(ALL)
    edited[STAR] = ("STAR", ("Interview", "Behaviour", "STAR"), T2, "Changed while hidden.\n")
    source = Source(edited)
    result = sync_workspace(ws, source, repos.notes, repos.images)
    assert kinds(result) == {"Behaviour": Change.RESTORED, "STAR": Change.RESTORED}
    assert source.fetched == [STAR]  # only the page that changed while hidden is downloaded
    assert not any(n.ignored for n in repos.notes.list(ws.id))
    quizzable = {i.point.name for i in quiz_service.candidates(repos, T2, WeightConfig())}
    assert quizzable == {"Persistence", "Behavioural answers", "STAR method"}  # links back
    # Only the note that changed needs a review; the restored unchanged one doesn't.
    assert [n.title for n in notes_to_review(repos, ws, False)] == ["STAR"]


def test_page_deleted_while_hidden_is_deleted(setup):
    repos, ws, _ = setup
    sync_workspace(ws, Source(visible(ALL, BEH, STAR), HIDE_BEHAVIOUR), repos.notes, repos.images)
    result = sync_workspace(ws, Source(visible(ALL, BEH, STAR)), repos.notes, repos.images)
    assert kinds(result) == {"Behaviour": Change.DELETED, "STAR": Change.DELETED}
    assert {n.title for n in repos.notes.list(ws.id)} == {"Interview", "Redis"}


def test_notion_source_skips_ignored_subtrees_without_reading_them():
    client = FakeNotion(
        pages={"root": page("Interview"), "r": page("Redis"), "b": page("Behaviour"),
               "s": page("STAR"), "x": page("Draft – x")},
        blocks={
            "root": [block("r", "child_page", True, title="Redis"),
                     block("b", "child_page", True, title="Behaviour"),
                     block("db", "child_database", title="Cards")],
            "r": [block("x", "child_page", title="Draft – x")],
            "b": [block("s", "child_page", title="STAR")],
        },
    )  # fmt: skip
    source = NotionSource(client, "root", parse("behaviour\nDraft*\nCards"))
    notes = source.list_notes()
    assert [n.title for n in notes] == ["Interview", "Redis"]
    assert "b" not in client.calls  # Behaviour's children were never requested
    assert [(p.source_id, p.path) for p in source.ignored_pages()] == [
        ("x", ("Interview", "Redis", "Draft – x")),
        ("b", ("Interview", "Behaviour")),
        ("db", ("Interview", "Cards")),
    ]
    assert source.skipped_databases == []  # an ignored database isn't reported as skipped

    by_id = NotionSource(client, "root", parse("bbbbbbbb-0000-0000-0000-000000000000"))
    assert len(by_id.list_notes()) == 5  # no page has that id: all five pages


def test_bad_ignore_file_stops_sync_before_any_change(tmp_path, learnova_home, monkeypatch):
    from typer.testing import CliRunner

    from learnova.cli import workspace as workspace_cli
    from learnova.cli.main import app
    from learnova.sources import notion

    monkeypatch.setattr(workspace_cli, "check_notion_page", lambda page_id: "Interview")
    runner = CliRunner()
    ws_dir = tmp_path / "w"
    runner.invoke(app, ["workspace", "new", "w", "--notion", "a" * 32, "--path", str(ws_dir)])
    assert (ws_dir / IGNORE_FILE).read_text().startswith("# Sub-pages this workspace leaves out")

    (ws_dir / IGNORE_FILE).write_text("Behaviour\n!\n")

    def no_notion():
        raise AssertionError("Notion must not be contacted with a bad ignore file")

    monkeypatch.setattr(notion, "make_client", no_notion)
    monkeypatch.chdir(ws_dir)
    r = runner.invoke(app, ["sync"])
    assert r.exit_code == 1 and ".learnovaignore line 2: nothing after '!'" in r.output


def test_sync_command_shows_ignored_pages(setup, monkeypatch):
    from collections import Counter

    from typer.testing import CliRunner

    from learnova.cli.main import app
    from learnova.home import WorkspaceFile, write_workspace_file
    from learnova.sources import notion

    repos, ws, _ = setup
    write_workspace_file(ws.path, WorkspaceFile(ws.id, ws.name, ws.notion_page_id))
    source = Source(visible(ALL, BEH, STAR), HIDE_BEHAVIOUR)
    source.block_types, source.skipped_databases = Counter(), []
    monkeypatch.setattr(notion, "make_client", lambda: None)
    monkeypatch.setattr(notion, "NotionSource", lambda client, page, rules=None: source)
    monkeypatch.chdir(ws.path)
    r = CliRunner().invoke(app, ["sync", "--no-describe"])
    assert r.exit_code == 0, r.output
    assert "  × Interview / Behaviour " in r.output and "ignored" in r.output
    assert "0 deleted, 2 ignored, 2 unchanged." in r.output
    assert "Ignored by .learnovaignore: Behaviour" in r.output
