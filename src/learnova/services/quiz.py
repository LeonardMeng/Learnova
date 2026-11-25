"""Quiz: draw knowledge points by weight (across all workspaces unless one is named), have the
model write a short-answer question from the notes, grade the answer against the rubric in
docs/grading-rubric.md, and record the attempt, which rebuilds the point's mastery.

Writing questions and grading are passed in as functions, so tests can replace them with fakes.
The interactive part (asking and reading answers) lives in the CLI."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from learnova.llm import factory, prompts
from learnova.models import Attempt, KnowledgePoint, Mastery, PointSource, QuestionType
from learnova.scheduler import weights as weights_mod
from learnova.scheduler.mastery import MasteryConfig, replay, round_score
from learnova.services.review import read_note
from learnova.storage.database import Repositories

MAX_NOTES = 2  # notes shown to the model per question
MAX_NOTE_CHARS = 6000
RUBRIC_FILE = Path("docs") / "grading-rubric.md"


class QuizError(Exception):
    """A problem worth showing to the user as-is."""


# --- what the model returns ----------------------------------------------------------------


class GeneratedQuestion(BaseModel):
    question: str


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


Questioner = Callable[[QuestionRequest], str]
Grader = Callable[[GradeRequest], Grade]


# --- drawing -------------------------------------------------------------------------------


def candidates(
    repos: Repositories,
    now: datetime,
    config: weights_mod.WeightConfig,
    workspace_name: str | None = None,
) -> list[QuizItem]:
    """Points that come from at least one note (in that workspace, if named), with weights."""
    user = repos.users.get_or_create_local()
    if workspace_name is not None and workspace_name not in {
        w.name for w in repos.workspaces.list(user.id)
    }:
        raise QuizError(f"No workspace named {workspace_name!r}. See `learnova workspace list`.")
    by_point: dict = {}
    for s in repos.knowledge.sources(user.id):
        by_point.setdefault(s.point_id, []).append(s)
    mastery = repos.attempts.mastery(user.id)
    items = []
    for point in repos.knowledge.list_for_user(user.id):
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
        lambda pairs: replay(pairs, config),
    )


def find_rubric(start: Path | None = None) -> Path:
    """docs/grading-rubric.md in the Learnova project this code runs from."""
    here = (start or Path(__file__)).resolve()
    for directory in here.parents:
        candidate = directory / RUBRIC_FILE
        if candidate.is_file():
            return candidate
    raise QuizError(f"Grading rubric {RUBRIC_FILE} not found above {here}.")


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
