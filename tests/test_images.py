"""Images: download, dedupe, change detection, cleanup, descriptions and the Markdown blocks.
Everything is faked: no Notion, network or model calls."""

from datetime import UTC, datetime

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from learnova.models import ImageRef, NoteContent, NoteImage, NoteSummary
from learnova.services import images as images_service
from learnova.sources.base import NoteSource
from learnova.storage.database import open_repositories
from learnova.sync import (
    Change,
    cache_filename,
    content_hash,
    render_images,
    sniff_mime,
    sync_workspace,
)

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
N1 = "11111111-1111-1111-1111-111111111111"
N2 = "22222222-2222-2222-2222-222222222222"
PNG_A = b"\x89PNG\r\n\x1a\n" + b"tree"
PNG_B = b"\x89PNG\r\n\x1a\n" + b"graph"
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'


def ref(block, key, caption=""):
    return ImageRef(block_id=block, source_key=key, url=f"{key}?sig=fresh", caption=caption)


class ImageSource(NoteSource):
    """Notes are (title, edited, markdown, refs); files maps source_key -> bytes."""

    def __init__(self, notes, files):
        self.notes, self.files = notes, files
        self.image_reads: list[str] = []

    def list_notes(self):
        return [
            NoteSummary(source_id=sid, title=t, path=(t,), last_edited=e)
            for sid, (t, e, _, _) in self.notes.items()
        ]

    def fetch(self, note_id):
        _, _, md, refs = self.notes[note_id]
        return NoteContent(markdown=md, images=tuple(refs))

    def read_image(self, r):
        self.image_reads.append(r.source_key)
        if r.source_key not in self.files:
            raise OSError("404 Not Found")
        return self.files[r.source_key]


class CountingModel(GenericFakeChatModel):
    calls: int = 0

    def invoke(self, *args, **kwargs):
        object.__setattr__(self, "calls", self.calls + 1)
        return AIMessage(content=f"A binary tree, call {self.calls}.\n\n- root: 8")


@pytest.fixture
def repos(tmp_path):
    return open_repositories(f"sqlite:///{tmp_path / 'db.sqlite'}")


def make_ws(repos, tmp_path, name):
    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, name, N1, tmp_path / name)
    ws.path.mkdir()
    return ws


def run(ws, source, repos, **kw):
    return sync_workspace(ws, source, repos.notes, repos.images, **kw)


def cache_text(ws, title, sid=N1):
    return (ws.path / "cache" / cache_filename(title, sid)).read_text()


def test_sniff_mime():
    assert sniff_mime(PNG_A) == "image/png"
    assert sniff_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_mime(SVG) == "image/svg+xml"
    assert sniff_mime(b"hello") == "application/octet-stream"


def test_render_images_is_repeatable():
    img = NoteImage(
        id="00000000-0000-0000-0000-000000000001", note_id="00000000-0000-0000-0000-000000000002",
        block_id="b1", source_key="k", caption="tree", path="images/abc.png", image_hash="h",
        mime_type="image/png", description=None, described_by=None,
    )  # fmt: skip
    raw = "Intro\n\n<!-- image:b1 -->\n\nOutro\n"
    first = render_images(raw, {"b1": img})
    assert "![tree](../images/abc.png)\n*(not described yet)*\n<!-- /image:b1 -->" in first
    assert render_images(first, {"b1": img}) == first

    described = img.model_copy(update={"description": "Line 1\n\nLine 2", "described_by": "m:x"})
    second = render_images(first, {"b1": described})
    assert "> **Image description** (m:x):\n> Line 1\n>\n> Line 2\n<!-- /image:b1 -->" in second
    assert "not described yet" not in second
    assert second.startswith("Intro\n\n") and second.endswith("\n\nOutro\n")
    assert render_images(second, {"b1": described}) == second


def test_download_dedupe_and_no_redownload(repos, tmp_path):
    ws = make_ws(repos, tmp_path, "w")
    source = ImageSource(
        {
            N1: ("Trees", T1, "<!-- image:b1 -->\n", [ref("b1", "s3/tree", "tree")]),
            N2: ("Again", T1, "<!-- image:b2 -->\n", [ref("b2", "s3/tree-copy")]),
        },
        {"s3/tree": PNG_A, "s3/tree-copy": PNG_A},  # the same picture uploaded twice
    )
    result = run(ws, source, repos)
    assert result.images_downloaded == 2
    files = list((ws.path / "images").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == PNG_A  # stored once, named by hash
    assert f"![tree](../images/{files[0].name})" in cache_text(ws, "Trees")

    source.image_reads.clear()
    result = run(ws, source, repos, full=True)
    assert source.image_reads == [] and result.images_downloaded == 0
    assert set(c.kind for c in result.changes) == {Change.UNCHANGED}


def test_text_only_notes_keep_their_old_hash():
    assert content_hash("same text") == content_hash("same text", ())


def test_replaced_image_is_a_change_and_old_file_is_removed(repos, tmp_path):
    ws = make_ws(repos, tmp_path, "w")
    source = ImageSource(
        {N1: ("Trees", T1, "<!-- image:b1 -->\n", [ref("b1", "s3/tree")])},
        {"s3/tree": PNG_A, "s3/graph": PNG_B},
    )
    run(ws, source, repos)
    old_files = {p.name for p in (ws.path / "images").iterdir()}

    source.notes[N1] = ("Trees", T2, "<!-- image:b1 -->\n", [ref("b1", "s3/graph")])
    result = run(ws, source, repos)
    assert [c.kind for c in result.changes] == [Change.CHANGED]  # same text, new picture
    new_files = {p.name for p in (ws.path / "images").iterdir()}
    assert len(new_files) == 1 and new_files != old_files

    source.notes[N1] = ("Trees", T2.replace(hour=12), "No pictures now.\n", [])
    run(ws, source, repos)
    assert list((ws.path / "images").iterdir()) == []
    assert repos.images.list_for_workspace(ws.id) == []


def test_failed_download_is_reported_and_retried(repos, tmp_path):
    ws = make_ws(repos, tmp_path, "w")
    source = ImageSource({N1: ("Trees", T1, "<!-- image:b1 -->\n", [ref("b1", "gone")])}, {})
    result = run(ws, source, repos)
    assert len(result.image_errors) == 1 and "404" in result.image_errors[0]
    assert "*[image could not be downloaded]*" in cache_text(ws, "Trees")

    source.files["gone"] = PNG_A  # back again
    result = run(ws, source, repos, full=True)
    assert result.images_downloaded == 1 and result.image_errors == ()
    assert "../images/" in cache_text(ws, "Trees")


def test_deleting_a_note_removes_its_images(repos, tmp_path):
    ws = make_ws(repos, tmp_path, "w")
    source = ImageSource(
        {N1: ("Trees", T1, "<!-- image:b1 -->\n", [ref("b1", "s3/tree")])}, {"s3/tree": PNG_A}
    )
    run(ws, source, repos)
    del source.notes[N1]
    run(ws, source, repos)
    assert list((ws.path / "images").iterdir()) == []
    assert repos.images.list_for_workspace(ws.id) == []


def test_describe_once_and_reuse_across_workspaces(repos, tmp_path):
    model = CountingModel(messages=iter([]))
    files = {"s3/tree": PNG_A, "s3/logo": SVG}
    notes = {
        N1: (
            "Trees",
            T1,
            "<!-- image:b1 -->\n\n<!-- image:b2 -->\n",
            [ref("b1", "s3/tree"), ref("b2", "s3/logo")],
        )
    }
    ws1 = make_ws(repos, tmp_path, "one")
    run(ws1, ImageSource(dict(notes), files), repos)
    # SVG can't be sent to a vision model, so only the PNG is pending.
    assert [i.mime_type for i in images_service.pending_images(repos, ws1)] == ["image/png"]

    result = images_service.describe_pending(ws1, repos, model, "openai:test")
    assert (result.described, result.reused, model.calls) == (1, 0, 1)
    text = cache_text(ws1, "Trees")
    assert "> **Image description** (openai:test):\n> A binary tree, call 1." in text
    assert images_service.pending_images(repos, ws1) == []

    # The same picture in another workspace: reused, no second model call.
    ws2 = make_ws(repos, tmp_path, "two")
    run(ws2, ImageSource(dict(notes), files), repos)
    result = images_service.describe_pending(ws2, repos, model, "openai:test")
    assert (result.described, result.reused, model.calls) == (0, 1, 1)
    assert "A binary tree, call 1." in cache_text(ws2, "Trees")

    # Re-syncing keeps the description (same file), so nothing becomes pending again.
    run(ws1, ImageSource(dict(notes), files), repos, full=True)
    assert images_service.pending_images(repos, ws1) == []
    assert "A binary tree, call 1." in cache_text(ws1, "Trees")


def test_failed_description_stays_pending(repos, tmp_path):
    class Broken(GenericFakeChatModel):
        def invoke(self, *args, **kwargs):
            raise RuntimeError("rate limited")

    ws = make_ws(repos, tmp_path, "w")
    source = ImageSource(
        {N1: ("Trees", T1, "<!-- image:b1 -->\n", [ref("b1", "s3/tree")])}, {"s3/tree": PNG_A}
    )
    run(ws, source, repos)
    result = images_service.describe_pending(ws, repos, Broken(messages=iter([])), "m:x")
    assert result.described == 0 and "rate limited" in result.errors[0]
    assert len(images_service.pending_images(repos, ws)) == 1
    assert "not described yet" in cache_text(ws, "Trees")


def test_sync_command_without_vision_model(tmp_path, monkeypatch, learnova_home):
    from typer.testing import CliRunner

    from learnova.cli import workspace as workspace_cli
    from learnova.cli.main import app
    from learnova.sources import notion

    source = ImageSource(
        {N1: ("Trees", T1, "<!-- image:b1 -->\n", [ref("b1", "s3/tree")])}, {"s3/tree": PNG_A}
    )
    monkeypatch.setattr(workspace_cli, "check_notion_page", lambda page_id: "Trees")
    monkeypatch.setattr(notion, "make_client", lambda: None)
    monkeypatch.setattr(notion, "NotionSource", lambda client, page: _with_stats(source))
    runner = CliRunner()
    ws_dir = tmp_path / "ws"
    runner.invoke(app, ["workspace", "new", "w", "--notion", N1, "--path", str(ws_dir)])
    monkeypatch.chdir(ws_dir)

    r = runner.invoke(app, ["sync"])
    assert r.exit_code == 0, r.output
    assert "Images: 1 downloaded" in r.output
    assert "1 image(s) waiting for a description. No vision model configured" in r.output

    r = runner.invoke(app, ["sync", "--no-describe", "--full"])
    assert r.exit_code == 0 and "waiting for a description" not in r.output


def _with_stats(source):
    from collections import Counter

    source.block_types = Counter({"image": 1})
    source.skipped_databases = []
    return source
