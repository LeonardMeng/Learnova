"""The HTTP API with FastAPI's test client: every endpoint, the two-request quiz, the local-only
safeguards and the error format. Notion, models and the reviewer are faked."""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_quiz import Source, setup_points
from test_review import Deduper, Script, review

from learnova.api.app import create_app
from learnova.config import load_settings
from learnova.llm import factory
from learnova.models import QuestionType
from learnova.services import quiz as quiz_service
from learnova.services import review as review_service
from learnova.services.quiz import ChoiceQuestion, Grade
from learnova.sources import notion
from learnova.storage.database import open_repositories

BASE = "http://127.0.0.1"


@pytest.fixture
def repos(learnova_home):
    return open_repositories(load_settings().database_url)


@pytest.fixture
def points(repos, tmp_path):
    return setup_points(repos, tmp_path)


@pytest.fixture
def client(repos):
    return TestClient(create_app(lambda: repos), base_url=BASE)


@pytest.fixture
def fake_models(monkeypatch):
    calls = Counter()

    def main_model(settings):
        calls["main_model"] += 1
        return object()

    monkeypatch.setattr(factory, "main_model", main_model)
    monkeypatch.setattr(
        quiz_service, "llm_questioner", lambda model: lambda req: f"Explain {req.point.name}?"
    )

    def grader(model):
        def grade(req):
            calls["grade"] += 1
            return Grade(level=4, score=4.0, feedback="Good.", missing="", reference_answer="Ref.")

        return grade

    monkeypatch.setattr(quiz_service, "llm_grader", grader)
    monkeypatch.setattr(
        quiz_service, "llm_mc_questioner",
        lambda model, rng: lambda req: ChoiceQuestion(
            QuestionType.MULTIPLE_CHOICE, "Which one?", ["w", "x", "y", "z"], 1, "Because x."
        ),
    )  # fmt: skip
    monkeypatch.setattr(
        quiz_service, "llm_tf_questioner",
        lambda model, rng: lambda req: ChoiceQuestion(
            QuestionType.TRUE_FALSE, "It is fast.", ["True", "False"], 0, "It is."
        ),
    )  # fmt: skip
    return calls


# --- local-only safeguards -----------------------------------------------------------------


def test_health_and_local_only_guards(client, repos):
    assert client.get("/api/health").json() == {"status": "ok"}
    other_host = TestClient(create_app(lambda: repos), base_url="http://evil.example")
    assert other_host.get("/api/health").status_code == 400  # DNS rebinding guard

    r = client.post("/api/quiz/questions", content="type=short",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})  # fmt: skip
    assert r.status_code == 415 and r.json()["code"] == "JsonRequired"

    allowed = client.options(
        "/api/status",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    refused = client.options(
        "/api/status",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in refused.headers


# --- workspaces, sync, review, reports -----------------------------------------------------


def test_workspaces(client, points, tmp_path, monkeypatch):
    names = [w["name"] for w in client.get("/api/workspaces").json()]
    assert names == ["redis", "ml"]

    monkeypatch.setattr(notion, "make_client", lambda: None)
    monkeypatch.setattr(notion, "page_title", lambda client, page_id: "Graphs page")
    body = {"name": "graphs", "notion_page": "a" * 32, "path": str(tmp_path / "g")}
    r = client.post("/api/workspaces", json=body)
    assert r.status_code == 201 and r.json()["notion_title"] == "Graphs page"
    assert (tmp_path / "g" / "learnova.toml").is_file()
    r = client.post("/api/workspaces", json={**body, "path": str(tmp_path / "g2")})
    assert r.status_code == 409 and r.json()["code"] == "WorkspaceExists"

    assert client.put("/api/workspaces/graphs/default").json()["is_default"] is True
    r = client.delete("/api/workspaces/graphs")
    assert r.json() == {"removed": "graphs", "was_default": True}
    r = client.delete("/api/workspaces/graphs")
    assert r.status_code == 404 and r.json() == {
        "error": "No workspace named 'graphs'. See `learnova workspace list`.",
        "code": "NoSuchWorkspace",
    }


def test_sync_endpoint_and_busy_lock(client, repos, points, monkeypatch):
    from learnova.home import WorkspaceFile, write_workspace_file

    user = repos.users.get_or_create_local()
    ws = next(w for w in repos.workspaces.list(user.id) if w.name == "redis")
    write_workspace_file(ws.path, WorkspaceFile(ws.id, ws.name, ws.notion_page_id))
    source = Source({"11111111-1111-1111-1111-111111111111": ("Redis", "Changed text.\n")})
    source.block_types, source.skipped_databases = Counter({"paragraph": 1}), []
    monkeypatch.setattr(notion, "make_client", lambda: None)
    monkeypatch.setattr(notion, "NotionSource", lambda client, page: source)

    with client.app.state.locks.hold("redis", "sync"):
        assert client.post("/api/workspaces/redis/sync", json={}).status_code == 409

    r = client.post("/api/workspaces/redis/sync", json={"full": True, "describe": False})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["counts"]["changed"] == 1 and data["changes"][0]["kind"] == "changed"
    assert data["block_types"] == {"paragraph": 1} and data["notes_downloaded"] == 1


def test_review_endpoint_and_reports(client, repos, tmp_path, monkeypatch):
    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, "w", "p", tmp_path / "w")
    ws.path.mkdir()
    from learnova.sync import sync_workspace

    sync_workspace(ws, Source({"s1": ("Redis", "Redis keeps data in memory and persists it.\n")}),
                   repos.notes, repos.images)  # fmt: skip
    monkeypatch.setattr(factory, "main_model", lambda settings: object())
    monkeypatch.setattr(
        review_service,
        "llm_reviewer",
        lambda m: Script(Redis=lambda r: review(new=[("Persistence", 4)])),
    )
    monkeypatch.setattr(review_service, "llm_deduper", lambda m: Deduper())

    r = client.post("/api/workspaces/w/review", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["reviewed"], data["new_points"], data["nothing_to_review"]) == (1, 1, False)
    assert client.post("/api/workspaces/w/review", json={}).json()["nothing_to_review"] is True

    listed = client.get("/api/workspaces/w/reports").json()
    assert [f["name"] for f in listed] == [data["report"]]
    report = client.get(f"/api/workspaces/w/reports/{data['report']}").json()
    assert report["markdown"].startswith("# Review: w")
    (ws.path / "secret.md").write_text("not a report")
    for bad in ("..%2Fsecret.md", "x.txt", "nope.md"):
        assert client.get(f"/api/workspaces/w/reports/{bad}").status_code == 404


# --- points and status ---------------------------------------------------------------------


def test_points_and_status(client, points):
    all_points = client.get("/api/points").json()
    assert [p["name"] for p in all_points][:2] == ["Orphan", "RDB vs AOF"]  # importance 5 first
    ml = client.get("/api/points", params={"workspace": "ml"}).json()
    assert [p["name"] for p in ml] == ["Gradient descent"]
    assert ml[0]["sources"] == [{"workspace": "ml", "path": ["Notes", "Optimisation"]}]
    assert client.get("/api/points", params={"workspace": "nope"}).status_code == 404

    gd = ml[0]["id"]
    assert client.patch(f"/api/points/{gd}", json={"importance": 9}).status_code == 422
    assert client.patch(f"/api/points/{gd}", json={"importance": 2}).json()["importance"] == 2

    status = client.get("/api/status", params={"weakest": 2}).json()
    assert status["overall"]["points"] == 4 and len(status["weakest"]) == 2
    assert {s["name"] for s in status["workspaces"]} == {"redis", "ml"}


# --- quiz ----------------------------------------------------------------------------------


def test_short_answer_quiz_in_two_requests(client, repos, points, fake_models):
    r = client.post("/api/quiz/questions", json={"type": "short", "workspace": "ml"})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["text"] == "Explain Gradient descent?" and q["options"] is None
    assert q["source"] == "ml: Notes / Optimisation" and q["status"] == "open"

    r = client.post(f"/api/quiz/questions/{q['id']}/answer", json={"answer": "It descends."})
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["score"], a["correct"], a["point"]["name"]) == (4.0, None, "Gradient descent")
    assert a["mastery_after"] == pytest.approx(0.24) and a["reference_answer"] == "Ref."
    assert fake_models["grade"] == 1

    again = client.post(f"/api/quiz/questions/{q['id']}/answer", json={"answer": "x"})
    assert again.status_code == 409 and again.json()["code"] == "QuestionClosed"
    assert client.get(f"/api/quiz/questions/{q['id']}").json()["status"] == "answered"

    # The point was asked: excluding it leaves nothing in 'ml'.
    r = client.post("/api/quiz/questions", json={"workspace": "ml", "exclude": [q["point_id"]]})
    assert r.status_code == 404 and r.json()["code"] == "NoPointsLeft"

    # The attempt is the same as one made from the CLI.
    attempts = repos.attempts.list_for_point(points["Gradient descent"].id)
    assert [(x.answer, x.score) for x in attempts] == [("It descends.", 4.0)]


def test_choice_quiz_hides_the_answer_and_needs_no_model_to_grade(
    client, points, fake_models, monkeypatch
):
    q = client.post("/api/quiz/questions", json={"type": "mc"}).json()
    raw = json.dumps(q)
    assert q["options"] == ["w", "x", "y", "z"]
    assert "correct" not in raw and "Because x" not in raw  # nothing leaks before answering

    def no_model(settings):
        raise AssertionError("multiple choice must be graded without a model")

    monkeypatch.setattr(factory, "main_model", no_model)
    r = client.post(f"/api/quiz/questions/{q['id']}/answer", json={"answer": "e"})
    assert r.status_code == 400 and r.json()["code"] == "InvalidAnswer"
    a = client.post(f"/api/quiz/questions/{q['id']}/answer", json={"answer": "b"}).json()
    assert (a["correct"], a["score"], a["correct_option"]) == (True, 5.0, "B) x")
    assert a["explanation"] == "Because x." and a["mastery_after"] == pytest.approx(0.18)


def test_true_false_skip_and_unknown_question(client, points, fake_models):
    q = client.post("/api/quiz/questions", json={"type": "tf"}).json()
    assert q["options"] == ["True", "False"] and q["type"] == "tf"
    r = client.post(f"/api/quiz/questions/{q['id']}/skip", json={})
    assert r.json() == {"question_id": q["id"], "status": "skipped"}
    assert client.post(f"/api/quiz/questions/{q['id']}/skip", json={}).status_code == 409
    missing = "00000000-0000-0000-0000-000000000000"
    assert (
        client.post(f"/api/quiz/questions/{missing}/answer", json={"answer": "t"}).status_code
        == 404
    )
    assert client.post("/api/quiz/questions", json={"type": "essay"}).status_code == 422


def test_model_errors_map_to_status_codes(client, points, monkeypatch):
    def unconfigured(settings):
        raise factory.LLMConfigError("No main model configured.")

    monkeypatch.setattr(factory, "main_model", unconfigured)
    r = client.post("/api/quiz/questions", json={})
    assert r.status_code == 400 and r.json() == {
        "error": "No main model configured.", "code": "LLMConfigError",
    }  # fmt: skip

    monkeypatch.setattr(factory, "main_model", lambda settings: object())

    def failing(model):
        def ask(req):
            raise factory.LLMCallError("RateLimitError: slow down")

        return ask

    monkeypatch.setattr(quiz_service, "llm_questioner", failing)
    r = client.post("/api/quiz/questions", json={})
    assert r.status_code == 502
    assert r.json()["error"] == "The model call failed. RateLimitError: slow down"


def test_merge_moves_open_questions_and_old_ones_expire(client, repos, points, fake_models):
    q = client.post("/api/quiz/questions", json={"workspace": "ml"}).json()  # Gradient descent
    into = str(points["RDB vs AOF"].id)
    r = client.post(f"/api/points/{q['point_id']}/merge", json={"into": into})
    assert r.status_code == 200 and r.json()["kept"]["id"] == into
    a = client.post(f"/api/quiz/questions/{q['id']}/answer", json={"answer": "x"}).json()
    assert a["point"]["id"] == into  # the open question now belongs to the kept point

    user = repos.users.get_or_create_local()
    old = repos.issued.create(user.id, points["Eviction"].id, QuestionType.SHORT_ANSWER, "old?",
                              None, {}, [], datetime.now(UTC) - timedelta(days=8))  # fmt: skip
    client.post("/api/quiz/questions", json={})  # issuing purges week-old open questions
    assert repos.issued.get(old.id) is None
