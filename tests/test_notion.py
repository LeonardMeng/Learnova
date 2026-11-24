from typing import Any

import pytest

from learnova.sources import notion

ROOT = "3e6135e3ecf04389846ce4126df126b0"
ROOT_UUID = "3e6135e3-ecf0-4389-846c-e4126df126b0"


def block(id: str, type: str, has_children: bool = False, **payload: Any) -> dict[str, Any]:
    return {"object": "block", "id": id, "type": type, "has_children": has_children, type: payload}


class FakeChildren:
    """Mimics client.blocks.children.list, including pagination."""

    def __init__(self, tree: dict[str, list[dict]], page_size: int = 2):
        self.tree = tree
        self.page_size = page_size

    def list(self, block_id: str, start_cursor: str | None = None, **_: Any) -> dict:
        items = self.tree.get(block_id, [])
        start = int(start_cursor or 0)
        end = start + self.page_size
        return {
            "results": items[start:end],
            "has_more": end < len(items),
            "next_cursor": str(end) if end < len(items) else None,
        }


class FakeClient:
    def __init__(self, tree: dict[str, list[dict]]):
        self.blocks = type("Blocks", (), {})()
        self.blocks.children = FakeChildren(tree)


def test_walk_nested_pages_toggles_and_databases():
    tree = {
        ROOT: [
            block("a", "child_page", True, title="Python"),
            block("p", "paragraph", False),
            block("t", "toggle", True),
            block("db", "child_database", True, title="Flashcards"),
            block("c", "child_page", False, title=""),
        ],
        "a": [block("a1", "child_page", False, title="GIL")],
        "t": [block("t1", "child_page", False, title="Hidden in toggle")],
        "db": [block("should-not-appear", "child_page", False, title="nope")],
    }
    nodes = list(notion.walk_subpages(FakeClient(tree), ROOT))
    assert [(n.title, n.depth, n.kind) for n in nodes] == [
        ("Python", 0, "page"),
        ("GIL", 1, "page"),
        ("Hidden in toggle", 0, "page"),
        ("Flashcards", 0, "database"),
        ("(untitled)", 0, "page"),
    ]


def test_walk_follows_pagination():
    tree = {ROOT: [block(str(i), "child_page", title=f"P{i}") for i in range(5)]}
    titles = [n.title for n in notion.walk_subpages(FakeClient(tree), ROOT)]
    assert titles == ["P0", "P1", "P2", "P3", "P4"]


@pytest.mark.parametrize(
    "value",
    [
        ROOT,
        ROOT_UUID,
        f"https://app.notion.com/p/Interview-Questions-{ROOT}?source=copy_link",
        f"https://www.notion.so/myteam/Interview-Questions-{ROOT}",
    ],
)
def test_parse_page_id(value):
    assert notion.parse_page_id(value) == ROOT_UUID


def test_parse_page_id_rejects_garbage():
    with pytest.raises(notion.NotionConfigError):
        notion.parse_page_id("not a page")


def test_missing_token(monkeypatch):
    monkeypatch.delenv(notion.TOKEN_ENV, raising=False)
    with pytest.raises(notion.NotionConfigError, match=notion.TOKEN_ENV):
        notion.make_client()


def test_parse_page_id_ignores_view_query():
    url = f"https://www.notion.so/{ROOT}?v=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert notion.parse_page_id(url) == ROOT_UUID
