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


class NotionCheckIn(BaseModel):
    notion_page: str = Field(description="Notion page ID or URL")


class NotionCheckOut(BaseModel):
    page_id: str
    title: str
    suggested_name: str  # from the title: lower case, words joined by "-"


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
    ignored: list[list[str]] = Field(
        default=[], description="Pages .learnovaignore left out (top of each subtree), as paths"
    )


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
    issues: int = 0  # new factual errors and unclear passages this run
    missing_topics: int = 0  # new missing topics this run
    fixed: int = 0  # issues a re-review saw fixed this run
    open: int = 0  # open issues in the workspace after this run
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


class PointPatchIn(BaseModel):
    """Only the fields you send change. `parent_id: null` moves the point to the top level;
    `shortcuts` replaces its shortcut parents (at most 2)."""

    importance: int | None = Field(default=None, ge=1, le=5)
    parent_id: uuid.UUID | None = None
    shortcuts: list[uuid.UUID] | None = None


class TopicOut(BaseModel):
    mastery: float | None  # importance-weighted, of the quizzed points under it
    quizzed: int
    total: int


class TreePointOut(BaseModel):
    """One row of the knowledge tree, in tree order (depth first)."""

    id: uuid.UUID
    name: str
    summary: str
    importance: int
    level: int  # 0 = top level
    parent_id: uuid.UUID | None  # primary parent
    shortcuts: list[uuid.UUID]  # the other parents it also appears under
    path: list[str]  # names from the top level down to this point
    topic: TopicOut | None  # set when other points are under it
    mastery: float | None
    review_count: int
    last_reviewed: datetime | None
    open_issues: int
    sources: list[SourceOut]


class AttemptOut(BaseModel):
    question: str
    type: str
    answer: str
    score: float
    feedback: str
    created_at: datetime


class NoteRefOut(BaseModel):
    workspace: str
    path: list[str]
    notion_url: str


class PointDetailOut(PointOut):
    parent_id: uuid.UUID | None
    shortcuts: list[PointRefOut]
    paths: list[list[str]]  # the primary path first, then one per shortcut
    topic: TopicOut | None
    children: list[PointRefOut]
    notes: list[NoteRefOut]
    attempts: list[AttemptOut]  # newest first, at most 20
    issues: list[IssueOut]  # open ones first


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
    exclude: list[uuid.UUID] = Field(
        default=[], description="Points already asked in this session, in the order asked"
    )
    points: list[uuid.UUID] = Field(
        default=[], description="Only these points (a topic stands for the points under it)"
    )
    topic: uuid.UUID | None = Field(default=None, description="Only the points under this topic")


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
    scope: str | None = None  # what the quiz is limited to, e.g. "topic Redis (6 point(s))"


class AnswerIn(BaseModel):
    answer: str = Field(description="Text for short answers; A–D for mc; t/f for tf")


class PointRefOut(BaseModel):
    id: uuid.UUID
    name: str


# --- issues --------------------------------------------------------------------------------

IssueStatusName = Literal["open", "fixed", "dismissed", "obsolete"]


class IssueOut(BaseModel):
    id: uuid.UUID
    kind: Literal["factual_error", "unclear", "missing"]
    status: IssueStatusName
    quote: str
    topic: str
    problem: str
    workspace: str | None
    note_path: list[str]
    point: PointRefOut | None
    priority: float
    url: str | None  # Notion, at the quoted paragraph when known
    first_seen: datetime
    last_seen: datetime
    closed_at: datetime | None
    closed_by: str | None


class IssuePatchIn(BaseModel):
    status: Literal["open", "fixed", "dismissed"] = Field(
        description="fixed / dismissed for an open issue; open to reopen a fixed or dismissed one"
    )


# --- notes and web imports -----------------------------------------------------------------


class NoteOut(BaseModel):
    id: uuid.UUID
    title: str
    path: list[str]
    icon: str | None  # emoji or image URL
    notion_url: str
    ignored: bool
    points: int
    open_issues: int
    last_edited: datetime
    reviewed: bool  # reviewed since its last change


class NoteDetailOut(NoteOut):
    knowledge_points: list[PointOut]
    issues: list[IssueOut]  # every status, open first


class WebImportOut(BaseModel):
    index_url: str
    notion_url: str
    folder: str
    created_at: datetime
    completed_at: datetime | None
    imported: int
    new: int
    removed: int
    last_checked: datetime | None


class MeOut(BaseModel):
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


PointDetailOut.model_rebuild()
