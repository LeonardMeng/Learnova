"""Block anchors: matching an issue's quote to the Notion block it comes from, recording blocks
during sync, linking issues (new and older ones), and the links in reports."""

from collections import Counter
from datetime import UTC, datetime

import pytest
from test_review import Deduper, Script

from learnova.anchors import find_block, normalize, notion_url
from learnova.config import load_settings
from learnova.home import ensure_home
from learnova.models import BlockText, IssueStatus, NoteContent, NoteSummary
from learnova.scheduler.priority import IssueWeights
from learnova.services import issues as issues_service
from learnova.services.review import Issue, NoteReview, notes_to_review, review_notes
from learnova.sources.base import NoteSource
from learnova.sources.notion_markdown import MarkdownConverter
from learnova.storage.database import open_repositories
from learnova.sync import sync_workspace

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
PAGE = "3e6135e3-ecf0-4389-846c-e4126df126b0"

BLOCKS = [
    BlockText(block_id="b-head", type="heading_2", text="Persistence"),
    BlockText(block_id="b-rdb", type="paragraph", text="RDB takes snapshots of the dataset."),
    BlockText(
        block_id="b-aof", type="bulleted_list_item", text="AOF logs every write; it's safer."
    ),
    BlockText(block_id="b-mem", type="paragraph", text="Redis stores all data on disk only."),
    BlockText(block_id="b-dup", type="quote", text="Redis stores all data on disk only."),
]


# --- matching --------------------------------------------------------------------------------


def test_normalize_drops_markdown_marks_case_and_spacing():
    assert normalize("**AOF**  is `safer` [than RDB](https://x.y)") == "aof is safer than rdb"
    assert normalize("snake_case stays, _emphasis_ goes") == "snake_case stays, emphasis goes"
    assert normalize("“quoted” it’s") == '"quoted" it\'s'


@pytest.mark.parametrize(
    "quote, block",
    [
        ("RDB takes snapshots", "b-rdb"),  # part of a paragraph
        ("**AOF** logs every write", "b-aof"),  # Markdown marks from the cache
        ("redis STORES all data   on disk only", "b-mem"),  # the same text twice: first wins
        ("snapshots of the dataset. AOF logs every", "b-rdb"),  # runs into the next block
        ("AOF logs each write, it is safer", "b-aof"),  # small rewording: closest match
        ("Something the note never says at all", None),  # below the threshold
        ("", None),
    ],
)
def test_find_block(quote, block):
    assert find_block(quote, BLOCKS) == block


def test_threshold_is_respected():
    reworded = "AOF records each write, so safer"
    assert find_block(reworded, BLOCKS, threshold=0.6) == "b-aof"
    assert find_block(reworded, BLOCKS, threshold=0.95) is None


def test_notion_url():
    assert notion_url(PAGE) == "https://www.notion.so/3e6135e3ecf04389846ce4126df126b0"
    assert notion_url(PAGE, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == (
        "https://www.notion.so/3e6135e3ecf04389846ce4126df126b0#aaaaaaaabbbbccccddddeeeeeeeeeeee"
    )


def test_threshold_in_config(learnova_home):
    ensure_home()
    assert load_settings().anchor_threshold == 0.6
    (learnova_home / "config.toml").write_text("[issues]\nanchor_threshold = 0.8\n")
    assert load_settings().anchor_threshold == 0.8
    (learnova_home / "config.toml").write_text("[issues]\nanchor_threshold = 1.5\n")
    with pytest.raises(ValueError, match="anchor_threshold"):
        load_settings()


# --- recording blocks ------------------------------------------------------------------------


def rt(text):
    return [{"type": "text", "plain_text": text, "annotations": {"bold": text.isupper()}}]


def test_converter_records_text_blocks_in_reading_order():
    tree = {  # fmt: skip
        "page": [
            {"id": "h", "type": "heading_4", "has_children": False,
             "heading_4": {"rich_text": rt("Title")}},
            {"id": "t", "type": "toggle", "has_children": True,
             "toggle": {"rich_text": rt("More")}},
            {"id": "e", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": []}},
            {"id": "img", "type": "image", "has_children": False,
             "image": {"type": "external", "external": {"url": "https://x/y.png"}, "caption": []}},
            {"id": "tb", "type": "table", "has_children": True, "table": {}},
        ],
        "t": [{"id": "li", "type": "bulleted_list_item", "has_children": False,
               "bulleted_list_item": {"rich_text": rt("NESTED item")}}],
        "tb": [{"id": "r1", "type": "table_row", "table_row": {"cells": [rt("k"), rt("v")]}}],
    }  # fmt: skip
    conv = MarkdownConverter(lambda bid: tree.get(bid, []), Counter())
    conv.page("page")
    assert [(b.block_id, b.type, b.text) for b in conv.blocks] == [
        ("h", "heading_4", "Title"),
        ("t", "toggle", "More"),
        ("li", "bulleted_list_item", "NESTED item"),  # plain text, without ** marks
        ("r1", "table_row", "k | v"),
    ]  # empty paragraphs and images are not recorded


class BlockSource(NoteSource):
    def __init__(self, text, blocks, edited=T1):
        self.text, self.blocks, self.edited = text, blocks, edited

    def list_notes(self):
        return [NoteSummary(source_id=PAGE, title="Redis", path=("Interview", "Redis"),
                            last_edited=self.edited)]  # fmt: skip

    def fetch(self, note_id):
        return NoteContent(markdown=self.text, blocks=tuple(self.blocks))

    def read_image(self, ref):
        raise AssertionError


@pytest.fixture
def setup(tmp_path, learnova_home):
    repos = open_repositories(load_settings().database_url)
    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, "w", PAGE, tmp_path / "w")
    ws.path.mkdir()
    text = "\n".join(b.text for b in BLOCKS) + "\n"
    source = BlockSource(text, BLOCKS)
    sync_workspace(ws, source, repos.notes, repos.images)
    return repos, ws, source


def test_sync_stores_and_replaces_blocks(setup):
    repos, ws, source = setup
    note = repos.notes.list(ws.id)[0]
    assert [b.block_id for b in repos.notes.blocks(note.id)] == [b.block_id for b in BLOCKS]

    sync_workspace(ws, source, repos.notes, repos.images)  # unchanged: skipped, blocks kept
    assert len(repos.notes.blocks(note.id)) == 5

    source.blocks, source.edited = BLOCKS[:2], T1.replace(hour=11)
    sync_workspace(ws, source, repos.notes, repos.images)
    assert [b.block_id for b in repos.notes.blocks(note.id)] == ["b-head", "b-rdb"]


# --- linking issues --------------------------------------------------------------------------


def review_with(repos, ws, *issues, missing=()):
    script = Script(Redis=lambda r: NoteReview(existing=[], new_points=[], issues=list(issues),
                                               missing=list(missing)))  # fmt: skip
    return review_notes(ws, repos, notes_to_review(repos, ws, True), script, Deduper())


def test_new_issues_get_their_block_and_a_link(setup):
    from learnova.services.review import Missing

    repos, ws, _ = setup
    review_with(
        repos, ws,
        Issue(kind="factual_error", quote="Redis stores **all data** on disk only", problem="x"),
        Issue(kind="unclear", quote="Words that appear nowhere in the note", problem="y"),
        missing=[Missing(topic="Replication", why="z")],
    )  # fmt: skip
    ranked = issues_service.ranked(repos, IssueWeights(), ws.id)
    by_kind = {r.issue.kind.value: r for r in ranked}
    page_url = "https://www.notion.so/3e6135e3ecf04389846ce4126df126b0"
    assert by_kind["factual_error"].issue.block_id == "b-mem"
    assert by_kind["factual_error"].url == f"{page_url}#bmem"  # dashes removed, as in real IDs
    assert by_kind["unclear"].issue.block_id is None and by_kind["unclear"].url == page_url
    assert by_kind["missing"].url == page_url

    from learnova.report import render_review

    run = review_with(repos, ws)
    user = repos.users.get_or_create_local()
    text = render_review(run, "m", repos.knowledge.list_for_user(user.id), ranked, T1)
    assert f"[Open in Notion]({page_url}#bmem)" in text


def test_older_issues_get_a_block_on_the_next_review(setup):
    repos, ws, source = setup
    user = repos.users.get_or_create_local()
    note = repos.notes.list(ws.id)[0]
    old = repos.issues.create(
        user.id, ws.id, note.id, note.path, "unclear", "AOF logs every write; it's safer.", "",
        "Safer than what?", None, issues_service.fingerprint("unclear", "AOF logs every write"),
        T1,
    )  # fmt: skip
    assert old.block_id is None  # recorded before blocks existed

    review_with(repos, ws)  # the model doesn't mention it: it stays open, and gets its block
    assert repos.issues.get(old.id).block_id == "b-aof"

    # The paragraph is replaced in Notion (new block id): the next review links the new one.
    moved = BlockText(block_id="b-aof-2", type="paragraph", text=BLOCKS[2].text)
    source.blocks, source.edited = [BLOCKS[0], BLOCKS[1], moved], T1.replace(hour=12)
    sync_workspace(ws, source, repos.notes, repos.images)
    review_with(repos, ws)
    assert repos.issues.get(old.id).block_id == "b-aof-2"
    assert repos.issues.get(old.id).status == IssueStatus.OPEN
