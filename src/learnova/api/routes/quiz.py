"""A quiz over HTTP: one request issues a question, a second one answers (or skips) it. Issued
questions are stored, so nothing is kept in memory between the two requests."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from learnova.api import schemas
from learnova.api.app import get_repos, get_settings, workspace_named
from learnova.config import Settings
from learnova.llm import factory
from learnova.models import IssuedQuestion, QuestionType
from learnova.services import quiz as quiz_service
from learnova.storage.database import Repositories

router = APIRouter(prefix="/api/quiz", tags=["quiz"])
Repos = Annotated[Repositories, Depends(get_repos)]
Config = Annotated[Settings, Depends(get_settings)]

TYPES = {
    "short": QuestionType.SHORT_ANSWER,
    "mc": QuestionType.MULTIPLE_CHOICE,
    "tf": QuestionType.TRUE_FALSE,
}
NAMES = {v: k for k, v in TYPES.items()}


def _out(q: IssuedQuestion) -> schemas.QuestionOut:
    return schemas.QuestionOut(
        id=q.id,
        type=NAMES[q.question_type],
        text=q.text,
        options=q.options,
        source=q.notes[0]["path"] if q.notes else None,
        point_id=q.point_id,
        status=q.status.value,
        created_at=q.created_at,
    )


@router.post("/questions", response_model=schemas.QuestionOut, status_code=201)
def issue(body: schemas.QuestionIn, repos: Repos, settings: Config):
    if body.workspace is not None:
        workspace_named(repos, body.workspace)
    kind = TYPES[body.type]
    rng = random.Random()
    models = quiz_service.quiz_models(factory.main_model(settings.models), kind, rng)
    question = quiz_service.issue_question(
        repos, models, kind, datetime.now(UTC), settings.weights, rng, body.workspace,
        set(body.exclude),
    )  # fmt: skip
    return _out(question)


@router.get("/questions/{question_id}", response_model=schemas.QuestionOut)
def get(question_id: uuid.UUID, repos: Repos):
    return _out(quiz_service.get_question(repos, question_id))


@router.post("/questions/{question_id}/answer", response_model=schemas.AnswerOut)
def answer(question_id: uuid.UUID, body: schemas.AnswerIn, repos: Repos, settings: Config):
    question = quiz_service.get_question(repos, question_id)
    # Only short answers need the model; multiple choice and true/false are checked locally.
    models = (
        quiz_service.grader_for(factory.main_model(settings.models))
        if question.question_type == QuestionType.SHORT_ANSWER
        else quiz_service.QuizModels()
    )
    result = quiz_service.answer_question(
        repos, models, question_id, body.answer, datetime.now(UTC), settings.mastery
    )
    return schemas.AnswerOut(
        question_id=question_id,
        point=schemas.PointRefOut(id=result.point.id, name=result.point.name),
        score=result.score,
        correct=result.correct,
        correct_option=result.correct_option,
        feedback=result.feedback,
        missing=result.missing,
        reference_answer=result.reference_answer,
        explanation=result.explanation,
        mastery_before=result.mastery_before,
        mastery_after=result.mastery_after,
    )


@router.post("/questions/{question_id}/skip", response_model=schemas.SkippedOut)
def skip(question_id: uuid.UUID, repos: Repos):
    quiz_service.skip_question(repos, question_id, datetime.now(UTC))
    return schemas.SkippedOut(question_id=question_id, status="skipped")
