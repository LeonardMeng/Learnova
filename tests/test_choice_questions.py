"""Multiple choice and true/false: checks on the model's question, shuffling, local grading,
answer parsing, type-weighted mastery, what is stored, and CLI sessions with no grading call."""

import random
from collections import Counter
from datetime import UTC, datetime

import pytest
from test_quiz import setup_points
from typer.testing import CliRunner

from learnova.config import load_settings
from learnova.home import ensure_home
from learnova.models import QuestionType
from learnova.scheduler.mastery import MasteryConfig, replay
from learnova.scheduler.weights import WeightConfig
from learnova.services import quiz as quiz_service
from learnova.services.quiz import (
    InvalidQuestion,
    MCQuestion,
    TFStatement,
    checked_mc,
    checked_tf,
    grade_choice,
    parse_choice,
)
from learnova.storage.database import open_repositories

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def mc(options=("Snapshots", "A log of writes", "Replication", "Sharding"), correct=1):
    return MCQuestion(
        question="What does AOF record?", options=list(options), correct_index=correct,
        explanation="AOF appends every write.",
    )  # fmt: skip


def test_checked_mc_shuffles_but_keeps_the_right_answer():
    positions = Counter()
    for seed in range(200):
        q = checked_mc(mc(), random.Random(seed))
        assert q.options[q.correct] == "A log of writes"
        assert sorted(q.options) == sorted(mc().options)
        positions[q.correct] += 1
    assert set(positions) == {0, 1, 2, 3}  # the right answer moves around


def test_checked_mc_strips_labels_and_rejects_bad_questions():
    q = checked_mc(
        mc(options=("A) one", "B. two", "3: three", "d - four"), correct=0), random.Random(1)
    )
    assert sorted(q.options) == ["four", "one", "three", "two"]
    for bad in (
        mc(options=("a", "b", "c")),
        mc(options=("a", "b", "c", "")),
        mc(options=("same", "Same", "c", "d")),
        mc(correct=4),
    ):
        with pytest.raises(InvalidQuestion):
            checked_mc(bad, random.Random(1))


def test_checked_tf_and_parsing_and_grading():
    q = checked_tf(TFStatement(statement="RDB logs every write.", is_true=False, explanation="No."))
    assert (q.options, q.correct, q.type) == (["True", "False"], 1, QuestionType.TRUE_FALSE)
    assert [parse_choice(q, t) for t in ("t", "TRUE", "y", "f", "no", "x", "b")] == [
        0, 0, 0, 1, 1, None, None,
    ]  # fmt: skip
    m = checked_mc(mc(), random.Random(0))
    assert [parse_choice(m, t) for t in ("a", "D", "2", "4", "5", "e", "ab")] == [
        0, 3, 1, 3, None, None, None,
    ]  # fmt: skip
    assert grade_choice(q, 1) == 5.0 and grade_choice(q, 0) == 0.0
    assert q.data(0) == {"is_true": False, "chosen": True, "explanation": "No."}
    assert m.data(2) == {
        "options": m.options, "correct": m.correct, "chosen": 2,
        "explanation": "AOF appends every write.",
    }  # fmt: skip


class FakeModel:
    """with_structured_output(...).invoke(prompt) returns the next scripted reply."""

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def with_structured_output(self, schema):
        model = self

        class Runner:
            def invoke(self, prompt):
                model.prompts.append(prompt)
                return model.replies.pop(0)

        return Runner()


def request():
    point = quiz_service.KnowledgePoint(
        id="00000000-0000-0000-0000-000000000001", user_id="00000000-0000-0000-0000-000000000002",
        name="AOF", summary="Append-only file.", importance=3, created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    return quiz_service.QuestionRequest(point, [])


def test_tf_target_is_chosen_locally_50_50():
    rng = random.Random(5)
    model = FakeModel([TFStatement(statement="s", is_true=True, explanation="e")] * 400)
    ask = quiz_service.llm_tf_questioner(model, rng)
    for _ in range(400):
        ask(request())
    wants_true = sum("must be TRUE." in p for p in model.prompts)
    assert 160 < wants_true < 240
    assert all("must be FALSE" in p or "must be TRUE" in p for p in model.prompts)


def test_invalid_question_is_retried_once():
    good, bad = mc(), mc(options=("x", "x", "y", "z"))
    ask = quiz_service.llm_mc_questioner(FakeModel([bad, good]), random.Random(1))
    assert ask(request()).text == "What does AOF record?"
    ask = quiz_service.llm_mc_questioner(FakeModel([bad, bad]), random.Random(1))
    with pytest.raises(InvalidQuestion, match="invalid twice"):
        ask(request())


def test_type_weights_in_mastery_and_config(learnova_home):
    t = datetime(2026, 11, 25, tzinfo=UTC)
    assert replay([(t, 5.0, "short_answer")]).mastery == pytest.approx(0.30)
    assert replay([(t, 5.0, "multiple_choice")]).mastery == pytest.approx(0.18)
    assert replay([(t, 5.0, "true_false")]).mastery == pytest.approx(0.09)
    # A wrong true/false answer pulls mastery down less than a wrong short answer.
    start = [(t.replace(hour=1), 5.0, "short_answer")]
    tf_wrong = replay([*start, (t.replace(hour=2), 0.0, "true_false")]).mastery
    sa_wrong = replay([*start, (t.replace(hour=2), 0.0, "short_answer")]).mastery
    assert sa_wrong < tf_wrong < 0.30

    ensure_home()
    (learnova_home / "config.toml").write_text("[mastery.type_weights]\ntrue_false = 0.2\n")
    weights = load_settings().mastery.type_weights
    assert weights == {"short_answer": 1.0, "multiple_choice": 0.6, "true_false": 0.2}
    with pytest.raises(ValueError):
        MasteryConfig(type_weights={"true_false": 1.5})


def test_record_choice_stores_what_was_shown(learnova_home, tmp_path):
    repos = open_repositories(load_settings().database_url)
    points = setup_points(repos, tmp_path)
    item = next(
        i for i in quiz_service.candidates(repos, NOW, WeightConfig()) if i.point.name == "Eviction"
    )
    q = checked_mc(mc(), random.Random(0))
    wrong = (q.correct + 1) % 4
    attempt, mastery = quiz_service.record_choice(repos, item, q, wrong, NOW, MasteryConfig())
    assert attempt.question_type == QuestionType.MULTIPLE_CHOICE and attempt.score == 0.0
    assert attempt.question.startswith("What does AOF record?\nA) ")
    assert attempt.answer == f"{'ABCD'[wrong]}) {q.options[wrong]}"
    assert attempt.question_data == q.data(wrong)
    stored = repos.attempts.list_for_point(points["Eviction"].id)[0]
    assert stored.question_data["correct"] == q.correct  # read back from the database
    assert mastery.mastery == 0.0 and mastery.review_count == 1


def test_mc_and_tf_sessions_make_no_grading_call(learnova_home, tmp_path, monkeypatch):
    from learnova.cli.main import app
    from learnova.llm import factory

    repos = open_repositories(load_settings().database_url)
    setup_points(repos, tmp_path)
    monkeypatch.setattr(factory, "main_model", lambda settings: object())

    def no_grading(model):
        raise AssertionError("multiple choice and true/false must not call the grader")

    monkeypatch.setattr(quiz_service, "llm_grader", no_grading)
    monkeypatch.setattr(
        quiz_service, "llm_mc_questioner",
        lambda model, rng: lambda req: quiz_service.ChoiceQuestion(
            QuestionType.MULTIPLE_CHOICE, f"About {req.point.name}?", list("wxyz"), 1, "Why."
        ),
    )  # fmt: skip
    monkeypatch.setattr(
        quiz_service, "llm_tf_questioner",
        lambda model, rng: lambda req: quiz_service.ChoiceQuestion(
            QuestionType.TRUE_FALSE, f"{req.point.name} is fast.", ["True", "False"], 0, "It is."
        ),
    )  # fmt: skip
    runner = CliRunner()

    r = runner.invoke(app, ["quiz", "--type", "mc", "-n", "2"], input="e\nB\nA\n")
    assert r.exit_code == 0, r.output
    assert "Answer with A, B, C or D." in r.output
    assert "  B) x" in r.output and "Answer A, B, C or D." in r.output  # "e" rejected
    assert "Correct." in r.output and "Not quite: the answer is B) x." in r.output
    assert "mastery 0.00 → 0.18" in r.output

    r = runner.invoke(app, ["quiz", "-t", "tf", "-n", "1"], input="t\n")
    assert r.exit_code == 0, r.output
    assert "True or false: " in r.output and "Correct." in r.output

    user = repos.users.get_or_create_local()
    attempts = [a for p in repos.knowledge.list_for_user(user.id)
                for a in repos.attempts.list_for_point(p.id)]  # fmt: skip
    types = Counter(a.question_type for a in attempts)
    assert types == {QuestionType.MULTIPLE_CHOICE: 2, QuestionType.TRUE_FALSE: 1}
    assert all(a.question_data for a in attempts)

    r = runner.invoke(app, ["quiz", "--type", "essay"])
    assert r.exit_code == 2  # not a valid type
