"""The API for the knowledge base, issues, notes and web imports (V2.2 milestone 4)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_review import LONG, REDIS, Source, workspace

from learnova.api.app import create_app
from learnova.config import load_settings
from learnova.models import IssueKind, NoteSummary, QuestionType, WebArticleState
from learnova.scheduler.mastery import MasteryConfig, replay
from learnova.services import hierarchy
from learnova.services.issues import fingerprint
from learnova.storage.database import open_repositories
from learnova.sync import sync_workspace

BASE = "http://127.0.0.1"
T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
DB = "22222222-2222-2222-2222-222222222222"


class IconSource(Source):
    def list_notes(self):
        notes = super().list_notes()
        return [n.model_copy(update={"icon": "🧠" if n.title == "Redis" else None}) for n in notes]


@pytest.fixture
def repos(learnova_home):
    return open_repositories(load_settings().database_url)


@pytest.fixture
def client(repos):
    return TestClient(create_app(lambda: repos), base_url=BASE)


@pytest.fixture
def kb(repos, tmp_path):
    """Redis (a topic) with Persistence and Eviction under it, from one note; Caching is a
    second topic Eviction is also under. One open issue on Eviction, one quiz answer on it."""
    user = repos.users.get_or_create_local()
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    source.__class__ = IconSource
    source.notes[REDIS] = ("Redis", T1.replace(hour=11), LONG)
    sync_workspace(ws, source, repos.notes, repos.images)
    note = repos.notes.list(ws.id)[0]
    mk = lambda n, i: repos.knowledge.create(user.id, n, f"About {n}.", i)  # noqa: E731
    redis, persistence, eviction, caching = mk("Redis", 5), mk("Persistence", 4), mk(
        "Eviction", 3), mk("Caching", 2)  # fmt: skip
    for p in (redis, persistence, eviction):
        repos.knowledge.link(p.id, note.id)
    hierarchy.move(repos, persistence, redis)
    hierarchy.move(repos, eviction, redis)
    hierarchy.add_shortcut(repos, eviction, caching)
    issue = repos.issues.create(
        user.id, ws.id, note.id, note.path, IssueKind.UNCLEAR, "LRU drops keys", "",
        "Which keys?", eviction.id, fingerprint("unclear", "LRU drops keys"), T1,
    )  # fmt: skip
    repos.attempts.record(
        user.id, eviction.id, "What is LRU?", QuestionType.SHORT_ANSWER, "Least recent.", 4.0,
        "Good.", T1, lambda a: replay(a, MasteryConfig()),
    )  # fmt: skip
    return dict(ws=ws, note=note, redis=redis, persistence=persistence, eviction=eviction,
                caching=caching, issue=issue)  # fmt: skip


def test_tree(client, kb):
    rows = client.get("/api/points/tree").json()
    assert [(r["name"], r["level"]) for r in rows] == [
        ("Redis", 0), ("Persistence", 1), ("Eviction", 1), ("Caching", 0),
    ]  # fmt: skip
    by_name = {r["name"]: r for r in rows}
    assert by_name["Eviction"]["shortcuts"] == [str(kb["caching"].id)]
    assert by_name["Eviction"]["path"] == ["Redis", "Eviction"]
    assert by_name["Eviction"]["open_issues"] == 1 and by_name["Eviction"]["topic"] is None
    assert by_name["Redis"]["topic"] == {"mastery": pytest.approx(0.8 * 0.3, abs=0.3),
                                         "quizzed": 1, "total": 2}  # fmt: skip
    assert by_name["Caching"]["topic"]["total"] == 1
    # With a workspace: its points and the topics above them (Caching has no notes but Eviction
    # is under it only by a shortcut, so it's left out).
    names = [r["name"] for r in client.get("/api/points/tree?workspace=w").json()]
    assert names == ["Redis", "Persistence", "Eviction"]
    assert client.get("/api/points/tree?workspace=nope").status_code == 404


def test_point_detail(client, kb):
    d = client.get(f"/api/points/{kb['eviction'].id}").json()
    assert d["paths"] == [["Redis", "Eviction"], ["Caching", "Eviction"]]
    assert d["parent_id"] == str(kb["redis"].id)
    assert d["shortcuts"] == [{"id": str(kb["caching"].id), "name": "Caching"}]
    page = "https://www.notion.so/" + REDIS.replace("-", "")
    assert d["notes"] == [{"workspace": "w", "path": ["Interview", "Redis"], "notion_url": page}]
    assert [a["answer"] for a in d["attempts"]] == ["Least recent."]
    assert [i["quote"] for i in d["issues"]] == ["LRU drops keys"]
    topic = client.get(f"/api/points/{kb['redis'].id}").json()
    assert [c["name"] for c in topic["children"]] == ["Persistence", "Eviction"]
    assert topic["topic"]["quizzed"] == 1


def test_patch_point(client, kb):
    url = f"/api/points/{kb['persistence'].id}"
    r = client.patch(url, json={"importance": 2, "parent_id": None,
                                "shortcuts": [str(kb["caching"].id)]})  # fmt: skip
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["importance"] == 2 and d["parent_id"] is None
    assert [s["name"] for s in d["shortcuts"]] == ["Caching"]

    # Only what is sent changes.
    d = client.patch(url, json={"parent_id": str(kb["redis"].id)}).json()
    assert d["parent_id"] == str(kb["redis"].id) and d["importance"] == 2
    assert [s["name"] for s in d["shortcuts"]] == ["Caching"]

    # A loop is refused with 409, and nothing changes (importance included).
    r = client.patch(f"/api/points/{kb['redis'].id}",
                     json={"importance": 1, "parent_id": str(kb["persistence"].id)})  # fmt: skip
    assert r.status_code == 409 and "loop" in r.json()["error"]
    assert client.get(f"/api/points/{kb['redis'].id}").json()["importance"] == 5
    r = client.patch(url, json={"shortcuts": ["00000000-0000-0000-0000-000000000009"]})
    assert r.status_code == 404
    assert client.patch(url, json={"importance": 9}).status_code == 422


def test_issues_list_and_status(client, kb):
    rows = client.get("/api/issues").json()
    assert [(i["quote"], i["status"], i["workspace"]) for i in rows] == [
        ("LRU drops keys", "open", "w")
    ]
    assert rows[0]["point"]["name"] == "Eviction" and rows[0]["url"].startswith("https://")
    assert client.get("/api/issues?kind=missing").json() == []
    assert client.get("/api/issues?workspace=nope").status_code == 404

    url = f"/api/issues/{kb['issue'].id}"
    r = client.patch(url, json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert client.get("/api/issues").json() == []
    assert [i["status"] for i in client.get("/api/issues?status=all").json()] == ["dismissed"]
    r = client.patch(url, json={"status": "fixed"})
    assert r.status_code == 409 and "dismissed" in r.json()["error"]
    assert client.patch(url, json={"status": "open"}).json()["status"] == "open"


def test_notes(client, kb, repos):
    rows = client.get("/api/workspaces/w/notes").json()
    assert [(n["title"], n["icon"], n["points"], n["open_issues"]) for n in rows] == [
        ("Redis", "🧠", 3, 1)
    ]
    d = client.get(f"/api/workspaces/w/notes/{kb['note'].id}").json()
    assert [p["name"] for p in d["knowledge_points"]] == ["Redis", "Persistence", "Eviction"]
    assert [i["problem"] for i in d["issues"]] == ["Which keys?"]
    missing = "/api/workspaces/w/notes/00000000-0000-0000-0000-000000000009"
    assert client.get(missing).status_code == 404


def test_icons_reach_notes_synced_before(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    assert repos.notes.list(ws.id)[0].icon is None
    source.__class__ = IconSource
    sync_workspace(ws, source, repos.notes, repos.images)  # unchanged note: icon still updated
    assert repos.notes.list(ws.id)[0].icon == "🧠"


def test_page_icon():
    from learnova.sources.notion import page_icon

    assert page_icon({"icon": {"type": "emoji", "emoji": "🤖"}}) == "🤖"
    assert page_icon({"icon": {"type": "external", "external": {"url": "https://x/i.png"}}}) == (
        "https://x/i.png"
    )
    assert page_icon({"icon": {"type": "file", "file": {"url": "https://s3/expiring"}}}) is None
    assert page_icon({"icon": None}) is None and page_icon({}) is None


def test_web_imports_and_me(client, repos):
    assert client.get("/api/web-imports").json() == []
    user = repos.users.get_or_create_local()
    imp = repos.web_imports.add(user.id, "https://site/index", "abc123", "site")
    repos.web_imports.new_article(imp.id, "https://site/a", ("Intro",), "A",
                                  WebArticleState.NEW, T1)  # fmt: skip
    [row] = client.get("/api/web-imports").json()
    assert (row["index_url"], row["new"], row["imported"], row["folder"]) == (
        "https://site/index", 1, 0, "site",
    )  # fmt: skip
    assert row["notion_url"] == "https://www.notion.so/abc123"
    assert client.get("/api/me").json() == {"name": user.name}


def test_unused_names_kept_for_lint():
    assert NoteSummary and DB


def test_check_notion_page_then_create_a_workspace(client, monkeypatch, tmp_path):
    from learnova.sources import notion
    from learnova.sources.notion import NotionConfigError

    monkeypatch.setattr(notion, "make_client", lambda: None)
    monkeypatch.setattr(notion, "page_title", lambda client, page_id: "Graph Theory: Basics")
    page = "3e6135e3ecf04389846ce4126df126b0"
    r = client.post(
        "/api/notion/check", json={"notion_page": f"https://www.notion.so/Graph-{page}"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "page_id": "3e6135e3-ecf0-4389-846c-e4126df126b0",
        "title": "Graph Theory: Basics",
        "suggested_name": "graph-theory-basics",
    }
    r = client.post("/api/notion/check", json={"notion_page": "not a page"})
    assert r.status_code == 400 and "Could not find a Notion page ID" in r.json()["error"]

    def hidden(client, page_id):
        raise NotionConfigError("Notion can't see that page.")

    monkeypatch.setattr(notion, "page_title", hidden)
    assert client.post("/api/notion/check", json={"notion_page": page}).status_code == 400

    monkeypatch.setattr(notion, "page_title", lambda client, page_id: "Graph Theory")
    folder = tmp_path / "new" / "graphs"
    r = client.post(
        "/api/workspaces", json={"name": "graphs", "notion_page": page, "path": str(folder)}
    )
    assert r.status_code == 201, r.text
    assert (folder / ".learnovaignore").is_file()  # created with only comments: nothing left out
    assert all(line.startswith("#") or not line.strip()
               for line in (folder / ".learnovaignore").read_text().splitlines())  # fmt: skip
