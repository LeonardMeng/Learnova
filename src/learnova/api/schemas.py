"""Request and response bodies. Kept separate from learnova.models so the API contract stays
stable when internal models change, and so hidden fields (like correct answers) can't leak."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ErrorOut(BaseModel):
    error: str
    code: str


# --- workspaces ----------------------------------------------------------------------------


class WorkspaceIn(BaseModel):
    name: str
    notion_page: str = Field(description="Notion page ID or URL")
    path: str = Field(description="Workspace directory; created if missing")


class WorkspaceOut(BaseModel):
    name: str
    path: str
    notion_page_id: str
    is_default: bool
    found: bool


class CreatedWorkspaceOut(WorkspaceOut):
    notion_title: str


class RemovedWorkspaceOut(BaseModel):
    removed: str
    was_default: bool


class SyncIn(BaseModel):
    full: bool = False
    describe: bool = True


class ChangeOut(BaseModel):
    kind: str
    path: list[str]
    previous_path: list[str] | None = None


class SyncOut(BaseModel):
    changes: list[ChangeOut]  # everything except "unchanged"
    counts: dict[str, int]
    notes_downloaded: int
    images_downloaded: int
    image_errors: list[str]
    block_types: dict[str, int]
    skipped_databases: list[str]
    images_described: int
    images_reused: int
    describe_errors: list[str]
    pending_images: int
    not_described_because: str | None


class ReviewIn(BaseModel):
    all: bool = False


class FailedNoteOut(BaseModel):
    path: list[str]
    error: str


class ReviewOut(BaseModel):
    nothing_to_review: bool
    reviewed: int = 0
    skipped: int = 0
    failed: list[FailedNoteOut] = []
    new_points: int = 0
    issues: int = 0
    missing_topics: int = 0
    possible_duplicates: int = 0
    report: str | None = None  # file name under the workspace's reports/
    model: str | None = None
    pending_images: int = 0


class ReportFileOut(BaseModel):
    name: str
    size: int
    modified: datetime


class ReportOut(BaseModel):
    name: str
    markdown: str


# --- knowledge points ----------------------------------------------------------------------


class SourceOut(BaseModel):
    workspace: str
    path: list[str]


class PointOut(BaseModel):
    id: uuid.UUID
    name: str
    summary: str
    importance: int
    sources: list[SourceOut]
    mastery: float | None
    review_count: int
    last_reviewed: datetime | None


class ImportanceIn(BaseModel):
    importance: int = Field(ge=1, le=5)


class MergeIn(BaseModel):
    into: uuid.UUID


class MergeOut(BaseModel):
    kept: PointOut
    removed: uuid.UUID


# --- status --------------------------------------------------------------------------------


class SummaryOut(BaseModel):
    name: str
    points: int
    quizzed: int
    average_mastery: float
    last_quiz: datetime | None


class WeakPointOut(BaseModel):
    point: PointOut
    weight: float


class StatusOut(BaseModel):
    overall: SummaryOut
    workspaces: list[SummaryOut]
    weakest: list[WeakPointOut]


# --- quiz ----------------------------------------------------------------------------------

QuizType = Literal["short", "mc", "tf"]


class QuestionIn(BaseModel):
    type: QuizType = "short"
    workspace: str | None = None
    exclude: list[uuid.UUID] = Field(default=[], description="Points already asked in this session")


class QuestionOut(BaseModel):
    """Never contains the correct answer or the point's name."""

    id: uuid.UUID
    type: QuizType
    text: str
    options: list[str] | None
    source: str | None  # the note it comes from, e.g. "interview: Interview Questions / Redis"
    point_id: uuid.UUID  # pass back in `exclude` for the next question
    status: Literal["open", "answered", "skipped"]
    created_at: datetime


class AnswerIn(BaseModel):
    answer: str = Field(description="Text for short answers; A–D for mc; t/f for tf")


class PointRefOut(BaseModel):
    id: uuid.UUID
    name: str


class AnswerOut(BaseModel):
    question_id: uuid.UUID
    point: PointRefOut
    score: float
    correct: bool | None  # mc / tf only
    correct_option: str | None
    feedback: str
    missing: str
    reference_answer: str
    explanation: str
    mastery_before: float
    mastery_after: float


class SkippedOut(BaseModel):
    question_id: uuid.UUID
    status: Literal["skipped"]
