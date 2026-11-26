"""Quiz: draw knowledge points by weight (across all workspaces unless one is named), have the
model write a question from the notes, grade the answer, and record the attempt, which rebuilds
the point's mastery.

Short answers are graded by the model against docs/grading-rubric.md. Multiple choice and
true/false questions are graded here, with no model call: the model only writes the question,
the options and the correct answer.

Writing questions and grading are passed in as functions, so tests can replace them with fakes.
The interactive part (asking and reading answers) lives in the CLI."""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from learnova.errors import ConflictError, LearnovaError, NotFoundError
from learnova.llm import factory, prompts
from learnova.models import (
    Attempt,
    IssuedQuestion,
    IssuedStatus,
    KnowledgePoint,
    Mastery,
    PointSource,
    QuestionType,
)
from learnova.scheduler import weights as weights_mod
from learnova.scheduler.mastery import MasteryConfig, replay, round_score
from learnova.services.review import read_note
from learnova.services.workspaces import NoSuchWorkspace
from learnova.storage.database import Repositories
from learnova.storage.repositories import QuestionAlreadyClosed

MAX_NOTES = 2  # notes shown to the model per question
QUESTION_TTL = timedelta(days=7)  # open questions older than this are deleted
MAX_NOTE_CHARS = 6000
RUBRIC_FILE = Path("docs") / "grading-rubric.md"


class QuizError(LearnovaError):
    pass


class RubricMissing(QuizError):
    status = 500


class NoPointsLeft(QuizError, NotFoundError):
    """No knowledge point left to ask (none at all, or all excluded)."""


class QuestionNotFound(QuizError, NotFoundError):
    pass


class QuestionClosed(QuizError, ConflictError):
    """The question was answered or skipped already."""


class InvalidAnswer(QuizError):
    pass


# --- what the model returns ----------------------------------------------------------------


class GeneratedQuestion(BaseModel):
    question: str


class MCQuestion(BaseModel):
    question: str
    options: list[str]
    correct_index: int  # 0–3
    explanation: str


class TFStatement(BaseModel):
    statement: str
    is_true: bool
    explanation: str


class Grade(BaseModel):
    level: int  # rubric level 0–5
    score: float  # 0–5, one decimal, within the level
    feedback: str
    missing: str
    reference_answer: str


# --- inputs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuizItem:
    point: KnowledgePoint
    sources: list[PointSource]
    mastery: Mastery | None
    weight: float


@dataclass(frozen=True)
class NoteText:
    path: str  # "interview: Interview Questions / Redis"
    text: str


@dataclass(frozen=True)
class QuestionRequest:
    point: KnowledgePoint
    notes: list[NoteText]


@dataclass(frozen=True)
class GradeRequest:
    point: KnowledgePoint
    notes: list[NoteText]
    question: str
    answer: str


@dataclass(frozen=True)
class ChoiceQuestion:
    """A multiple-choice or true/false question, ready to show and to grade locally."""

    type: QuestionType  # MULTIPLE_CHOICE or TRUE_FALSE
    text: str  # the question, or the statement to judge
    options: list[str]  # in the order shown; ["True", "False"] for true/false
    correct: int  # index into options
    explanation: str

    def data(self, chosen: int) -> dict:
        """What is stored with the attempt."""
        if self.type == QuestionType.TRUE_FALSE:
            return {
                "is_true": self.correct == 0,
                "chosen": self.options[chosen].lower() == "true",
                "explanation": self.explanation,
            }
        return {
            "options": self.options,
            "correct": self.correct,
            "chosen": chosen,
            "explanation": self.explanation,
        }


class InvalidQuestion(QuizError):
    """The model's question didn't pass the checks, even after a retry."""

    status = 502


Questioner = Callable[[QuestionRequest], str]
ChoiceQuestioner = Callable[[QuestionRequest], ChoiceQuestion]
Grader = Callable[[GradeRequest], Grade]

LETTERS = "ABCD"


# --- drawing -------------------------------------------------------------------------------


def candidates(
    repos: Repositories,
    now: datetime,
    config: weights_mod.WeightConfig,
    workspace_name: str | None = None,
) -> list[QuizItem]:
    """Points that come from at least one note (in that workspace, if named), with weights.
    Topics (points with other points under them) are not quizzed."""
    user = repos.users.get_or_create_local()
    if workspace_name is not None and workspace_name not in {
        w.name for w in repos.workspaces.list(user.id)
    }:
        raise NoSuchWorkspace(
            f"No workspace named {workspace_name!r}. See `learnova workspace list`."
        )
    by_point: dict = {}
    for s in repos.knowledge.sources(user.id):
        by_point.setdefault(s.point_id, []).append(s)
    mastery = repos.attempts.mastery(user.id)
    topics = {link.parent_id for link in repos.knowledge.parents(user.id)}
    items = []
    for point in repos.knowledge.list_for_user(user.id):
        if point.id in topics:
            continue
        sources = by_point.get(point.id, [])
        if workspace_name is not None:
            sources = [s for s in sources if s.workspace_name == workspace_name]
        if not sources:
            continue
        m = mastery.get(point.id)
        state = weights_mod.PointState(
            importance=point.importance,
            mastery=m.mastery if m else 0.0,
            last_reviewed=m.last_reviewed if m else None,
        )
        items.append(QuizItem(point, sources, m, weights_mod.weight(state, now, config)))
    return items


def draw(items: list[QuizItem], count: int, rng: random.Random) -> list[QuizItem]:
    return weights_mod.draw(items, [i.weight for i in items], count, rng)


def note_texts(repos: Repositories, item: QuizItem) -> list[NoteText]:
    """The text of up to MAX_NOTES notes the point comes from, read from the cache."""
    user = repos.users.get_or_create_local()
    workspaces = {w.name: w for w in repos.workspaces.list(user.id)}
    out = []
    for source in item.sources[:MAX_NOTES]:
        ws = workspaces.get(source.workspace_name)
        note = (
            next((n for n in repos.notes.list(ws.id) if n.id == source.note_id), None)
            if ws
            else None
        )
        text = read_note(ws, note) if note else ""
        if text:
            if len(text) > MAX_NOTE_CHARS:
                text = text[:MAX_NOTE_CHARS] + "\n\n[…note shortened…]"
            out.append(NoteText(f"{source.workspace_name}: {' / '.join(source.note_path)}", text))
    return out


# --- grading -------------------------------------------------------------------------------


def normalize(grade: Grade) -> Grade:
    """Keep the score consistent with the rubric: it must round to the chosen level."""
    level = max(0, min(5, int(grade.level)))
    score = min(level + 0.5, max(level - 0.5, float(grade.score)))
    return grade.model_copy(update={"level": level, "score": round_score(score)})


def record(
    repos: Repositories,
    item: QuizItem,
    question: str,
    answer: str,
    grade: Grade,
    now: datetime,
    config: MasteryConfig,
    issued_question_id=None,
) -> tuple[Attempt, Mastery]:
    user = repos.users.get_or_create_local()
    feedback = grade.feedback.strip()
    if grade.missing.strip():
        feedback += f"\n\nMissing: {grade.missing.strip()}"
    return repos.attempts.record(
        user.id,
        item.point.id,
        question,
        QuestionType.SHORT_ANSWER,
        answer,
        grade.score,
        feedback,
        now,
        lambda attempts: replay(attempts, config),
        issued_question_id=issued_question_id,
    )


# --- multiple choice and true/false ----------------------------------------------------------

_LABEL = re.compile(r"^\s*(?:[A-Da-d]|[1-4])\s*[).:\-]\s+")


def checked_mc(raw: MCQuestion, rng: random.Random) -> ChoiceQuestion:
    """Validate the model's question and shuffle its options (models favour one position)."""
    options = [_LABEL.sub("", o).strip() for o in raw.options]
    if len(options) != 4 or any(not o for o in options):
        raise InvalidQuestion("needs exactly 4 non-empty options")
    if len({o.casefold() for o in options}) != 4:
        raise InvalidQuestion("options must all be different")
    if not 0 <= raw.correct_index < 4:
        raise InvalidQuestion(f"correct_index {raw.correct_index} is not 0–3")
    if not raw.question.strip():
        raise InvalidQuestion("empty question")
    order = list(range(4))
    rng.shuffle(order)
    return ChoiceQuestion(
        type=QuestionType.MULTIPLE_CHOICE,
        text=raw.question.strip(),
        options=[options[i] for i in order],
        correct=order.index(raw.correct_index),
        explanation=raw.explanation.strip(),
    )


def checked_tf(raw: TFStatement) -> ChoiceQuestion:
    if not raw.statement.strip():
        raise InvalidQuestion("empty statement")
    return ChoiceQuestion(
        type=QuestionType.TRUE_FALSE,
        text=raw.statement.strip(),
        options=["True", "False"],
        correct=0 if raw.is_true else 1,
        explanation=raw.explanation.strip(),
    )


def parse_choice(question: ChoiceQuestion, text: str) -> int | None:
    """The option index the user picked, or None if the input isn't a valid choice."""
    t = text.strip().lower()
    if question.type == QuestionType.TRUE_FALSE:
        return {"t": 0, "true": 0, "y": 0, "yes": 0, "f": 1, "false": 1, "n": 1, "no": 1}.get(t)
    if len(t) == 1 and t.upper() in LETTERS:
        return LETTERS.index(t.upper())
    if t in {"1", "2", "3", "4"}:
        return int(t) - 1
    return None


def grade_choice(question: ChoiceQuestion, chosen: int) -> float:
    """Local grading: right is 5, wrong is 0."""
    return 5.0 if chosen == question.correct else 0.0


def record_choice(
    repos: Repositories,
    item: QuizItem,
    question: ChoiceQuestion,
    chosen: int,
    now: datetime,
    config: MasteryConfig,
    issued_question_id=None,
) -> tuple[Attempt, Mastery]:
    user = repos.users.get_or_create_local()
    shown = question.text
    if question.type == QuestionType.MULTIPLE_CHOICE:
        shown += "\n" + "\n".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(question.options))
    answer = (
        question.options[chosen]
        if question.type == QuestionType.TRUE_FALSE
        else f"{LETTERS[chosen]}) {question.options[chosen]}"
    )
    return repos.attempts.record(
        user.id,
        item.point.id,
        shown,
        question.type,
        answer,
        grade_choice(question, chosen),
        question.explanation,
        now,
        lambda attempts: replay(attempts, config),
        question_data=question.data(chosen),
        issued_question_id=issued_question_id,
    )


def find_rubric(start: Path | None = None) -> Path:
    """docs/grading-rubric.md in the Learnova project this code runs from."""
    here = (start or Path(__file__)).resolve()
    for directory in here.parents:
        candidate = directory / RUBRIC_FILE
        if candidate.is_file():
            return candidate
    raise RubricMissing(f"Grading rubric {RUBRIC_FILE} not found above {here}.")


def load_rubric() -> str:
    """Read on every grading call, so edits to the file apply straight away."""
    return find_rubric().read_text(encoding="utf-8")


# --- the real model calls ------------------------------------------------------------------


def _notes_block(notes: list[NoteText]) -> str:
    if not notes:
        return "(no note text available)"
    return "\n\n".join(f'<note source="{n.path}">\n{n.text}\n</note>' for n in notes)


def llm_questioner(model) -> Questioner:
    def ask(req: QuestionRequest) -> str:
        prompt = prompts.load("question").format(
            point_name=req.point.name,
            point_summary=req.point.summary,
            notes=_notes_block(req.notes),
        )
        return factory.ask_structured(model, GeneratedQuestion, prompt).question.strip()

    return ask


def _with_retry(make):
    """Ask the model again once if its question fails the checks."""
    try:
        return make()
    except InvalidQuestion:
        try:
            return make()
        except InvalidQuestion as e:
            raise InvalidQuestion(f"the model's question was invalid twice ({e})") from None


def llm_mc_questioner(model, rng: random.Random) -> ChoiceQuestioner:
    def ask(req: QuestionRequest) -> ChoiceQuestion:
        prompt = prompts.load("question_mc").format(
            point_name=req.point.name,
            point_summary=req.point.summary,
            notes=_notes_block(req.notes),
        )
        return _with_retry(
            lambda: checked_mc(factory.ask_structured(model, MCQuestion, prompt), rng)
        )

    return ask


def llm_tf_questioner(model, rng: random.Random) -> ChoiceQuestioner:
    def ask(req: QuestionRequest) -> ChoiceQuestion:
        # Learnova picks true or false 50/50; left alone, models write mostly true statements.
        want_true = rng.random() < 0.5
        prompt = prompts.load("question_tf").format(
            point_name=req.point.name,
            point_summary=req.point.summary,
            notes=_notes_block(req.notes),
            truth="TRUE" if want_true else "FALSE",
            false_hint=""
            if want_true
            else " Make it plausible: change one important detail of something the notes say.",
        )
        return _with_retry(lambda: checked_tf(factory.ask_structured(model, TFStatement, prompt)))

    return ask


def llm_grader(model) -> Grader:
    def grade(req: GradeRequest) -> Grade:
        prompt = prompts.load("grade").format(
            rubric=load_rubric(),
            point_name=req.point.name,
            notes=_notes_block(req.notes),
            question=req.question,
            answer=req.answer.strip() or "(no answer)",
        )
        return normalize(factory.ask_structured(model, Grade, prompt))

    return grade


# --- asking and answering, one question at a time (used by the CLI and the API) ----------------


@dataclass(frozen=True)
class QuizModels:
    """The model calls a quiz needs; only those for the chosen question type are set."""

    short_question: Questioner | None = None
    grader: Grader | None = None
    mc_question: ChoiceQuestioner | None = None
    tf_question: ChoiceQuestioner | None = None


def quiz_models(model, kind: QuestionType, rng: random.Random) -> QuizModels:
    if kind == QuestionType.SHORT_ANSWER:
        load_rubric()  # fail now, not after the first answer
        return QuizModels(short_question=llm_questioner(model), grader=llm_grader(model))
    if kind == QuestionType.MULTIPLE_CHOICE:
        return QuizModels(mc_question=llm_mc_questioner(model, rng))
    if kind == QuestionType.TRUE_FALSE:
        return QuizModels(tf_question=llm_tf_questioner(model, rng))
    raise QuizError(f"Question type {kind} is not available yet.")


def grader_for(model) -> QuizModels:
    """Answering needs only the grader (short answers); choices are checked locally."""
    return QuizModels(grader=llm_grader(model))


def issue_question(
    repos: Repositories,
    models: QuizModels,
    kind: QuestionType,
    now: datetime,
    config: weights_mod.WeightConfig,
    rng: random.Random,
    workspace_name: str | None = None,
    exclude: frozenset | set | list = (),
) -> IssuedQuestion:
    """Draw one point by weight (skipping `exclude`, the points already asked this session),
    have the model write a question, and store it until it is answered."""
    repos.issued.purge_open(now - QUESTION_TTL)
    excluded = set(exclude)
    items = [
        i for i in candidates(repos, now, config, workspace_name) if i.point.id not in excluded
    ]
    if not items:
        where = f" in {workspace_name!r}" if workspace_name else ""
        more = " more" if excluded else ""
        raise NoPointsLeft(f"No{more} knowledge points to ask{where}.")
    item = draw(items, 1, rng)[0]
    notes = note_texts(repos, item)
    request = QuestionRequest(item.point, notes)
    if kind == QuestionType.SHORT_ANSWER:
        text, options, hidden = models.short_question(request), None, {}
    else:
        make = models.mc_question if kind == QuestionType.MULTIPLE_CHOICE else models.tf_question
        q = make(request)
        text, options = q.text, q.options
        hidden = {"correct": q.correct, "explanation": q.explanation}
    user = repos.users.get_or_create_local()
    return repos.issued.create(
        user.id, item.point.id, kind, text, options, hidden, [asdict(n) for n in notes], now
    )


@dataclass(frozen=True)
class AnswerResult:
    question: IssuedQuestion
    point: KnowledgePoint
    attempt: Attempt
    score: float
    correct: bool | None  # multiple choice / true-false only
    correct_option: str | None  # e.g. "B) A log of writes" or "False"
    feedback: str
    missing: str
    reference_answer: str
    explanation: str
    mastery_before: float
    mastery_after: float


def get_question(repos: Repositories, question_id) -> IssuedQuestion:
    question = repos.issued.get(question_id)
    if question is None:
        raise QuestionNotFound(f"No question with id {question_id}.")
    return question


def _open(repos: Repositories, question_id) -> IssuedQuestion:
    question = get_question(repos, question_id)
    if question.status != IssuedStatus.OPEN:
        raise QuestionClosed(f"This question was {question.status.value} already.")
    return question


def choice_of(question: IssuedQuestion) -> ChoiceQuestion:
    return ChoiceQuestion(
        type=question.question_type,
        text=question.text,
        options=list(question.options or []),
        correct=int(question.hidden["correct"]),
        explanation=question.hidden.get("explanation", ""),
    )


def option_label(choice: ChoiceQuestion, index: int) -> str:
    option = choice.options[index]
    return option if choice.type == QuestionType.TRUE_FALSE else f"{LETTERS[index]}) {option}"


def answer_question(
    repos: Repositories,
    models: QuizModels,
    question_id,
    answer: str,
    now: datetime,
    config: MasteryConfig,
) -> AnswerResult:
    """Grade the answer (the model for short answers, locally for choices), record the attempt
    and close the question, all or nothing."""
    question = _open(repos, question_id)
    point = repos.knowledge.get(question.point_id)
    user = repos.users.get_or_create_local()
    previous = repos.attempts.mastery(user.id).get(point.id)
    before = previous.mastery if previous else 0.0
    item = QuizItem(point, [], previous, 0.0)
    try:
        if question.question_type == QuestionType.SHORT_ANSWER:
            if not answer.strip():
                raise InvalidAnswer("The answer is empty; skip the question instead.")
            notes = [NoteText(**n) for n in question.notes]
            grade = models.grader(GradeRequest(point, notes, question.text, answer))
            attempt, mastery = record(
                repos, item, question.text, answer, grade, now, config, question.id
            )
            return AnswerResult(
                question, point, attempt, grade.score, None, None, grade.feedback.strip(),
                grade.missing.strip(), grade.reference_answer.strip(), "", before, mastery.mastery,
            )  # fmt: skip
        choice = choice_of(question)
        chosen = parse_choice(choice, answer)
        if chosen is None:
            is_tf = choice.type == QuestionType.TRUE_FALSE
            raise InvalidAnswer("Answer t or f." if is_tf else "Answer A, B, C or D.")
        attempt, mastery = record_choice(repos, item, choice, chosen, now, config, question.id)
    except QuestionAlreadyClosed:
        raise QuestionClosed("This question was answered or skipped already.") from None
    score = grade_choice(choice, chosen)
    return AnswerResult(
        question, point, attempt, score, score > 0, option_label(choice, choice.correct), "", "",
        "", choice.explanation, before, mastery.mastery,
    )  # fmt: skip


def skip_question(repos: Repositories, question_id, now: datetime) -> None:
    _open(repos, question_id)
    try:
        repos.issued.skip(question_id, now)
    except QuestionAlreadyClosed:
        raise QuestionClosed("This question was answered or skipped already.") from None
