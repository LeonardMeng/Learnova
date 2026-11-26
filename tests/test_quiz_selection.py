"""Quizzes on chosen points or a topic (V2.2 milestone 5): only the selection is asked, a small
selection is asked again until the count is reached, and mastery updates as usual."""

import random
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_quiz import grade, setup_points
from typer.testing import CliRunner

from learnova.api.app import create_app
from learnova.cli.main import app
from learnova.config import load_settings
from learnova.llm import factory
from learnova.models import QuestionType
from learnova.scheduler.mastery import MasteryConfig
from learnova.scheduler.weights import WeightConfig
from learnova.services import hierarchy
from learnova.services import quiz as quiz_service
from learnova.storage.database import open_repositories

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def repos(learnova_home):
    return open_repositories(load_settings().database_url)


@pytest.fixture
def points(repos, tmp_path):
    """Redis (a topic, no notes) with RDB vs AOF under it; Eviction under Caching and also
    under Redis by a shortcut; Gradient descent elsewhere."""
    pts = setup_points(repos, tmp_path)
    user = repos.users.get_or_create_local()
    for name in ("Redis", "Caching"):
        pts[name] = repos.knowledge.create(user.id, name, f"About {name}.", 3)
    hierarchy.move(repos, pts["RDB vs AOF"], pts["Redis"])
    hierarchy.move(repos, pts["Eviction"], pts["Caching"])
    hierarchy.add_shortcut(repos, pts["Eviction"], pts["Redis"])
    return pts


def models():
    return quiz_service.QuizModels(short_question=lambda req: f"Explain {req.point.name}?")


def ask(repos, n, selection):
    asked, names = [], []
    for _ in range(n):
        q = quiz_service.issue_question(
            repos, models(), QuestionType.SHORT_ANSWER, NOW, WeightConfig(), random.Random(n),
            None, asked, selection,
        )  # fmt: skip
        asked.append(q.point_id)
        names.append(repos.knowledge.get(q.point_id).name)
    return names


def test_select_expands_topics(repos, points):
    s = quiz_service.select(repos, topic_ref="Redis")
    assert s.points == {points["RDB vs AOF"].id, points["Eviction"].id}  # shortcut included
    assert s.label == "topic Redis (2 point(s))"
    s = quiz_service.select(repos, ["Eviction", "Gradient descent"])
    assert s.label == "2 chosen points" and len(s.points) == 2
    assert quiz_service.select(repos, ["Caching"]).label == "Caching (a topic)"
    with pytest.raises(quiz_service.QuizError):
        quiz_service.select(repos)


def test_a_topic_quiz_only_asks_points_under_it_and_repeats(repos, points):
    names = ask(repos, 6, quiz_service.select(repos, topic_ref="Redis"))
    assert set(names) == {"RDB vs AOF", "Eviction"}
    assert sorted(names[:2]) == ["Eviction", "RDB vs AOF"]  # each once before any repeat
    assert all(a != b for a, b in zip(names, names[1:], strict=False))  # never twice in a row


def test_two_chosen_points_and_mastery(repos, points):
    selection = quiz_service.select(repos, ["Gradient descent", "Eviction"])
    assert set(ask(repos, 4, selection)) == {"Gradient descent", "Eviction"}

    one = quiz_service.select(repos, ["Gradient descent"])
    assert ask(repos, 3, one) == ["Gradient descent"] * 3  # the only point, asked again
    q = quiz_service.issue_question(repos, models(), QuestionType.SHORT_ANSWER, NOW,
                                    WeightConfig(), random.Random(), None, [], one)  # fmt: skip
    graded = quiz_service.QuizModels(grader=lambda req: grade(4.0))
    result = quiz_service.answer_question(repos, graded, q.id, "an answer", NOW, MasteryConfig())
    assert result.attempt.score == 4.0
    assert repos.attempts.mastery(q.user_id)[q.point_id].review_count == 1  # as in any quiz


def test_a_selection_without_notes_says_so(repos, points):
    with pytest.raises(quiz_service.NoPointsLeft, match="Nothing to ask in Orphan"):
        ask(repos, 1, quiz_service.select(repos, ["Orphan"]))


def test_quiz_command_with_a_topic(repos, points, monkeypatch):
    monkeypatch.setattr(factory, "main_model", lambda settings: object())
    monkeypatch.setattr(quiz_service, "llm_questioner",
                        lambda model: lambda req: f"Explain {req.point.name}?")  # fmt: skip
    monkeypatch.setattr(quiz_service, "llm_grader", lambda model: lambda req: grade(3.0))
    runner = CliRunner()
    r = runner.invoke(app, ["quiz", "--topic", "Redis", "-n", "3"], input="s\ns\ns\n")
    assert r.exit_code == 0, r.output
    assert "Quiz on topic Redis (2 point(s)): 3 question(s)" in r.output
    assert r.output.count("Explain") == 3
    assert "Gradient descent" not in r.output
    r = runner.invoke(app, ["quiz", "--point", "Eviction", "-w", "redis"])
    assert r.exit_code != 0 and "can't be combined with --workspace" in r.output
    r = runner.invoke(app, ["quiz", "--topic", "Nope"])
    assert r.exit_code == 1 and "No knowledge point named" in r.output


def test_api_quiz_on_points(repos, points, monkeypatch):
    monkeypatch.setattr(factory, "main_model", lambda settings: object())
    monkeypatch.setattr(quiz_service, "llm_questioner",
                        lambda model: lambda req: f"Explain {req.point.name}?")  # fmt: skip
    client = TestClient(create_app(lambda: repos), base_url="http://127.0.0.1")
    topic = str(points["Redis"].id)
    exclude = []
    for _ in range(3):
        r = client.post("/api/quiz/questions", json={"topic": topic, "exclude": exclude})
        assert r.status_code == 201, r.text
        exclude.append(r.json()["point_id"])
    assert r.json()["scope"] == "topic Redis (2 point(s))"
    assert set(exclude) == {str(points["RDB vs AOF"].id), str(points["Eviction"].id)}

    r = client.post("/api/quiz/questions", json={"points": [str(points["Orphan"].id)]})
    assert r.status_code == 404 and "Nothing to ask" in r.json()["error"]
    r = client.post("/api/quiz/questions", json={"topic": topic, "workspace": "redis"})
    assert r.status_code == 400 and "not both" in r.json()["error"]
