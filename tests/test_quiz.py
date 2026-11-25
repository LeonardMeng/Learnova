"""Quiz and status with fake question writing and grading: drawing across workspaces, grade
normalisation, recording, the rubric file, a full CLI session, and merging points with history."""

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from learnova.config import load_settings
from learnova.models import NoteContent, NoteSummary
from learnova.scheduler.mastery import MasteryConfig, replay
from learnova.scheduler.weights import WeightConfig
from learnova.services import knowledge
from learnova.services import quiz as quiz_service
from learnova.services import status as status_service
from learnova.services.quiz import Grade
from learnova.sources.base import NoteSource
from learnova.storage.database import open_repositories
from learnova.sync import sync_workspace

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
TEXT = "Redis persists data with RDB snapshots and an AOF log; they trade speed for durability.\n"


class Source(NoteSource):
    def __init__(self, notes):
        self.notes = notes

    def list_notes(self):
        return [
            NoteSummary(source_id=sid, title=t, path=("Notes", t), last_edited=T1)
            for sid, (t, _) in self.notes.items()
        ]

    def fetch(self, note_id):
        return NoteContent(markdown=self.notes[note_id][1])

    def read_image(self, ref):
        raise AssertionError


@pytest.fixture
def repos(learnova_home):
    return open_repositories(load_settings().database_url)  # the same database as the CLI


def setup_points(repos, tmp_path):
    """Two workspaces; 'redis' has two points, 'ml' one, plus one point with no notes."""
    user = repos.users.get_or_create_local()
    points = {}
    for name, sid, title, kps in (
        ("redis", A, "Redis", [("RDB vs AOF", 5), ("Eviction", 2)]),
        ("ml", B, "Optimisation", [("Gradient descent", 4)]),
    ):
        ws = repos.workspaces.add(user.id, name, sid, tmp_path / name)
        ws.path.mkdir()
        sync_workspace(ws, Source({sid: (title, TEXT)}), repos.notes, repos.images)
        note = repos.notes.list(ws.id)[0]
        for kp_name, importance in kps:
            p = repos.knowledge.create(user.id, kp_name, f"About {kp_name}.", importance)
            repos.knowledge.link(p.id, note.id)
            points[kp_name] = p
    points["Orphan"] = repos.knowledge.create(user.id, "Orphan", "No notes.", 5)
    return points


def grade(score, level=None, missing=""):
    level = round(score) if level is None else level
    return Grade(
        level=level, score=score, feedback="Fine.", missing=missing, reference_answer="Ref."
    )


def test_candidates_across_workspaces_and_filter(repos, tmp_path):
    setup_points(repos, tmp_path)
    items = quiz_service.candidates(repos, NOW, WeightConfig())
    assert {i.point.name for i in items} == {"RDB vs AOF", "Eviction", "Gradient descent"}
    ml = quiz_service.candidates(repos, NOW, WeightConfig(), "ml")
    assert [i.point.name for i in ml] == ["Gradient descent"]
    with pytest.raises(quiz_service.NoSuchWorkspace):
        quiz_service.candidates(repos, NOW, WeightConfig(), "nope")

    drawn = quiz_service.draw(items, 5, random.Random(3))
    assert len(drawn) == 3  # only 3 candidates
    counts = Counter(
        quiz_service.draw(items, 1, rng)[0].point.name
        for rng in [random.Random(i) for i in range(3000)]
    )
    assert counts["RDB vs AOF"] > counts["Gradient descent"] > counts["Eviction"]  # 5 > 4 > 2

    notes = quiz_service.note_texts(repos, ml[0])
    assert notes[0].path == "ml: Notes / Optimisation" and "RDB snapshots" in notes[0].text


@pytest.mark.parametrize(
    "level, score, expected",
    [(3, 3.46, (3, 3.5)), (3, 4.6, (3, 3.5)), (3, 1.0, (3, 2.5)), (5, 5.3, (5, 5.0)),
     (0, 0.0, (0, 0.0)), (7, 4.9, (5, 4.9)), (2, 2.04, (2, 2.0))],
)  # fmt: skip
def test_normalize_keeps_score_within_the_level(level, score, expected):
    g = quiz_service.normalize(grade(score, level))
    assert (g.level, g.score) == expected


def test_record_rebuilds_mastery_and_affects_weight(repos, tmp_path):
    setup_points(repos, tmp_path)
    item = next(
        i for i in quiz_service.candidates(repos, NOW, WeightConfig()) if i.point.name == "Eviction"
    )
    attempt, mastery = quiz_service.record(
        repos, item, "Q?", "my answer", grade(5.0, missing="LFU"), NOW, MasteryConfig()
    )
    assert attempt.score == 5.0 and attempt.question_type == "short_answer"
    assert attempt.feedback == "Fine.\n\nMissing: LFU"
    assert (mastery.mastery, mastery.review_count, mastery.last_reviewed) == (
        pytest.approx(0.3),
        1,
        NOW,
    )
    again = next(
        i for i in quiz_service.candidates(repos, NOW, WeightConfig()) if i.point.name == "Eviction"
    )
    assert again.weight < item.weight  # higher mastery and just reviewed


def test_rubric_is_read_from_the_file_on_every_grading_call(tmp_path, monkeypatch):
    rubric = tmp_path / "docs" / "grading-rubric.md"
    rubric.parent.mkdir()
    rubric.write_text("RUBRIC v1")
    monkeypatch.setattr(quiz_service, "find_rubric", lambda: rubric)
    prompts_seen = []

    class FakeModel:
        def with_structured_output(self, schema):
            class Runner:
                def invoke(self, prompt):
                    prompts_seen.append(prompt)
                    return grade(4.2)

            return Runner()

    grader = quiz_service.llm_grader(FakeModel())
    point = knowledge.KnowledgePoint(
        id="00000000-0000-0000-0000-000000000001", user_id="00000000-0000-0000-0000-000000000002",
        name="P", summary="S", importance=3, created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    request = quiz_service.GradeRequest(point, [], "Q?", "A.")
    assert grader(request).score == 4.2
    rubric.write_text("RUBRIC v2")
    grader(request)
    assert "RUBRIC v1" in prompts_seen[0] and "RUBRIC v2" in prompts_seen[1]


def test_real_rubric_file_has_all_levels():
    text = quiz_service.find_rubric().read_text()
    for level in range(6):
        assert f"\n## {level}: " in text


def test_merge_moves_attempts_and_recomputes_mastery(repos, tmp_path):
    points = setup_points(repos, tmp_path)
    items = {i.point.name: i for i in quiz_service.candidates(repos, NOW, WeightConfig())}
    # Kept point: 5.0 then (much later) the duplicate's 0.0 must land in time order.
    quiz_service.record(repos, items["RDB vs AOF"], "q", "a", grade(5.0), NOW, MasteryConfig())
    quiz_service.record(
        repos, items["Gradient descent"], "q", "a", grade(5.0), NOW - timedelta(days=1),
        MasteryConfig(),
    )  # fmt: skip
    quiz_service.record(
        repos, items["Gradient descent"], "q", "a", grade(0.0), NOW + timedelta(hours=1),
        MasteryConfig(),
    )  # fmt: skip

    knowledge.merge_points(repos, "Gradient descent", "RDB vs AOF")
    kept = points["RDB vs AOF"]
    attempts = repos.attempts.list_for_point(kept.id)
    assert [a.score for a in attempts] == [5.0, 5.0, 0.0]  # oldest first, all three
    user = repos.users.get_or_create_local()
    mastery = repos.attempts.mastery(user.id)
    expected = replay([(a.created_at, a.score) for a in attempts])
    assert mastery[kept.id].mastery == pytest.approx(expected.mastery)  # 0.3, 0.51, 0.357
    assert mastery[kept.id].review_count == 3
    assert mastery[kept.id].last_reviewed == NOW + timedelta(hours=1)
    assert points["Gradient descent"].id not in mastery
    assert repos.attempts.list_for_point(points["Gradient descent"].id) == []


def test_status_figures(repos, tmp_path):
    setup_points(repos, tmp_path)
    items = {i.point.name: i for i in quiz_service.candidates(repos, NOW, WeightConfig())}
    quiz_service.record(repos, items["RDB vs AOF"], "q", "a", grade(5.0), NOW, MasteryConfig())
    st = status_service.status(repos, NOW, WeightConfig())
    assert (st.overall.points, st.overall.quizzed, st.overall.last_quiz) == (4, 1, NOW)
    assert st.overall.average_mastery == pytest.approx(0.3 / 4)
    assert {s.name: (s.points, s.quizzed) for s in st.workspaces} == {
        "ml": (1, 0),
        "redis": (2, 1),
    }
    assert st.weakest[-1].point.name == "RDB vs AOF"  # the only point with mastery > 0
    ml = status_service.status(repos, NOW, WeightConfig(), "ml")
    assert ml.overall.points == 1 and [s.name for s in ml.workspaces] == ["ml"]


def test_quiz_and_status_commands(repos, tmp_path, monkeypatch):
    from learnova.cli.main import app
    from learnova.llm import factory

    setup_points(repos, tmp_path)
    monkeypatch.setattr(factory, "main_model", lambda settings: object())
    monkeypatch.setattr(
        quiz_service, "llm_questioner", lambda model: lambda req: f"Explain {req.point.name}?"
    )
    graded = []

    def fake_grader(model):
        def grade_it(req):
            graded.append(req)
            return grade(4.5 if "snapshot" in req.answer else 1.0)

        return grade_it

    monkeypatch.setattr(quiz_service, "llm_grader", fake_grader)
    monkeypatch.setattr(random, "Random", lambda *a: _Seeded())  # repeatable draw

    runner = CliRunner()
    # Answer 1: two lines then an empty line. Answer 2: skip. Then quit.
    r = runner.invoke(app, ["quiz", "--count", "3"], input="RDB is a snapshot\nAOF logs\n\ns\nq\n")
    assert r.exit_code == 0, r.output
    assert "Quiz: 3 question(s)" in r.output
    assert "Summary (2 question(s) from 2 workspace(s))" in r.output
    assert "Question 1/3 · " in r.output and "Score 4.5 / 5" in r.output
    assert "Skipped." in r.output and "Question 3/3" in r.output
    assert len(graded) == 1 and graded[0].answer == "RDB is a snapshot\nAOF logs"
    assert "4.5/5" in r.output and "skipped" in r.output

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    assert "All workspaces: 4 point(s) · 1 quizzed" in r.output
    assert "redis: " in r.output and "ml: " in r.output
    assert "(no notes)" in r.output  # the orphan point

    r = runner.invoke(app, ["quiz", "-w", "nope"])
    assert r.exit_code == 1 and "No workspace named 'nope'" in r.output


class _Seeded(random.Random):
    def __init__(self):
        super().__init__(1)
