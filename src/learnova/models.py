"""Domain objects shared by all layers. Repositories return these, never database rows, so
nothing above the storage layer depends on SQLAlchemy."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    notion_page_id: str
    path: Path
    is_default: bool
    created_at: datetime


class NoteSummary(BaseModel):
    """What a Source knows about a note without reading its content."""

    model_config = ConfigDict(frozen=True)

    source_id: str  # e.g. the Notion page ID
    title: str
    path: tuple[str, ...]  # titles from the workspace's top page down to this note, inclusive
    last_edited: datetime
    parent_id: str | None = None  # source_id of the note this one sits under; None for the top
    icon: str | None = None  # the page's emoji, or an image URL; None = no icon


class Note(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_id: str
    title: str
    path: tuple[str, ...]
    last_edited: datetime
    content_hash: str
    synced_at: datetime
    reviewed_hash: str | None = None  # content_hash at the last review; None = never reviewed
    issues_hash: str | None = None  # content_hash when its issues were last updated; None = never
    icon: str | None = None  # the Notion page's emoji or icon URL
    ignored: bool = False  # hidden by .learnovaignore: kept, but not reviewed or quizzed


class ImageRef(BaseModel):
    """An image found in a note, before it is downloaded."""

    model_config = ConfigDict(frozen=True)

    block_id: str  # where the image sits in the note
    source_key: str  # identifies the file itself; stays the same while the image is unchanged
    url: str  # where to download it now (Notion's links expire after an hour)
    caption: str = ""


class BlockText(BaseModel):
    """The plain text of one block of a note, in reading order: what issue quotes are matched to,
    so an issue can link to its paragraph in Notion."""

    model_config = ConfigDict(frozen=True)

    block_id: str
    type: str  # Notion block type, e.g. "paragraph", "bulleted_list_item"
    text: str


class NoteContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    markdown: str  # images appear as <!-- image:BLOCK_ID --> placeholders
    images: tuple[ImageRef, ...] = ()
    blocks: tuple[BlockText, ...] = ()


class NoteImage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    note_id: uuid.UUID
    block_id: str
    source_key: str
    caption: str
    path: str | None  # relative to the workspace, e.g. images/3fa9c1d2e4b5a6f7.png; None if failed
    image_hash: str | None  # sha256 of the file
    mime_type: str | None
    description: str | None
    described_by: str | None  # "provider:model" that wrote the description


class KnowledgePoint(BaseModel):
    """The smallest testable unit. Belongs to the user; linked to one or more notes."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    summary: str
    importance: int  # 1-5; the AI suggests it once, re-reviews never change it
    created_at: datetime
    updated_at: datetime


class ParentLink(BaseModel):
    """A knowledge point under another one. The primary parent is where the point lives;
    secondary ones are shortcuts ("also under")."""

    model_config = ConfigDict(frozen=True)

    child_id: uuid.UUID
    parent_id: uuid.UUID
    primary: bool


class PointSource(BaseModel):
    """Where a knowledge point comes from: one note, in one workspace."""

    model_config = ConfigDict(frozen=True)

    point_id: uuid.UUID
    note_id: uuid.UUID
    note_path: tuple[str, ...]
    workspace_name: str


class QuestionType(StrEnum):
    SHORT_ANSWER = "short_answer"  # the only type used in version 1
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    EXPLAIN = "explain"
    CODE = "code"


class Attempt(BaseModel):
    """One answer to one quiz question."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    point_id: uuid.UUID
    question: str
    question_type: QuestionType
    answer: str
    score: float  # 0–5, one decimal
    feedback: str
    created_at: datetime
    # Multiple choice / true-false: what was shown, the right answer and the choice made.
    question_data: dict | None = None


class Mastery(BaseModel):
    """Cached result of replaying a point's attempts (see scheduler/mastery.py)."""

    model_config = ConfigDict(frozen=True)

    point_id: uuid.UUID
    mastery: float  # 0–1
    review_count: int
    last_reviewed: datetime | None


class IssuedStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class IssuedQuestion(BaseModel):
    """A question that has been asked and is waiting for its answer (or was answered/skipped).
    `hidden` holds what the asker must not see yet, e.g. the correct option."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    point_id: uuid.UUID
    question_type: QuestionType
    text: str
    options: list[str] | None  # multiple choice / true-false, in the order shown
    hidden: dict  # correct answer, explanation, ...
    notes: list[dict]  # [{"path": ..., "text": ...}] the note text the question was written from
    status: IssuedStatus
    created_at: datetime
    closed_at: datetime | None
    attempt_id: uuid.UUID | None


class WebImport(BaseModel):
    """A web tutorial imported into Notion once by `learnova web import`. Later changes on the
    site are only compared with the local cache (`web check`), never written to Notion. The
    `--into` page is laid out like the index page; `cursor` counts the steps of that layout
    already written and `layout_hash` identifies the layout they belong to."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    index_url: str
    target_page_id: str
    slug: str  # folder name under <user home>/web/
    cursor: int
    layout_hash: str | None
    created_at: datetime
    completed_at: datetime | None  # when every listed article was in Notion


class WebArticleState(StrEnum):
    IMPORTED = "imported"  # in Notion and in the cache (or being imported)
    NEW = "new"  # appeared in the index after the import; reported, not imported
    REMOVED = "removed"  # was imported, then left the index


class WebArticle(BaseModel):
    """One article of a web import."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    import_id: uuid.UUID
    url: str
    section: tuple[str, ...]  # headings in the index, top first
    title: str
    state: WebArticleState
    notion_page_id: str | None
    source_modified: str | None  # article:modified_time of the cached version
    etag: str | None
    content_hash: str | None  # of the cached Markdown; None until the import of it finished
    cache_path: str | None  # relative to the import's folder
    first_seen_at: datetime
    imported_at: datetime | None
    checked_at: datetime | None


class IssueKind(StrEnum):
    FACTUAL_ERROR = "factual_error"
    UNCLEAR = "unclear"
    MISSING = "missing"


class IssueStatus(StrEnum):
    OPEN = "open"
    FIXED = "fixed"
    DISMISSED = "dismissed"
    OBSOLETE = "obsolete"  # its note was deleted


class ReviewIssue(BaseModel):
    """A problem a review found in a note. It stays open until a later review sees it fixed, or
    you fix or dismiss it by hand."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    note_id: uuid.UUID | None  # None once the note is deleted
    note_path: tuple[str, ...]  # where it was found, kept for obsolete issues
    kind: IssueKind
    quote: str  # the passage, empty for `missing`
    topic: str  # for `missing`: the topic that's missing; empty otherwise
    problem: str  # what's wrong; for `missing`: why the note should cover it
    point_id: uuid.UUID | None
    status: IssueStatus
    fingerprint: str
    first_seen: datetime
    last_seen: datetime
    closed_at: datetime | None
    closed_by: str | None  # "review", "user" or "notion"
    block_id: str | None = None  # the Notion block the quote was found in, if any
