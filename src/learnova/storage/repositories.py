"""Repository interfaces: the only storage API that services and the CLI know about. Swapping
SQLite for Postgres, or for a fake in tests, means providing another implementation of these."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from learnova.models import (
    Attempt,
    BlockText,
    ImageRef,
    IssuedQuestion,
    IssueKind,
    IssueStatus,
    KnowledgePoint,
    Mastery,
    Note,
    NoteImage,
    NoteSummary,
    ParentLink,
    PointSource,
    QuestionType,
    ReviewIssue,
    User,
    WebArticle,
    WebArticleState,
    WebImport,
    Workspace,
)
from learnova.scheduler.mastery import MasteryState


class WorkspaceNotFound(LookupError):
    pass


class WorkspaceNameTaken(ValueError):
    pass


class UserRepository(ABC):
    @abstractmethod
    def get_or_create_local(self) -> User:
        """Version 1 has a single local user; create it on first use."""


class WorkspaceRepository(ABC):
    @abstractmethod
    def add(
        self,
        user_id: uuid.UUID,
        name: str,
        notion_page_id: str,
        path: Path,
        workspace_id: uuid.UUID | None = None,
    ) -> Workspace:
        """Register a workspace. The user's first workspace becomes the default.
        Raises WorkspaceNameTaken if the user already has one with this name."""

    @abstractmethod
    def list(self, user_id: uuid.UUID) -> list[Workspace]: ...

    @abstractmethod
    def get(self, workspace_id: uuid.UUID) -> Workspace | None: ...

    @abstractmethod
    def get_by_name(self, user_id: uuid.UUID, name: str) -> Workspace | None: ...

    @abstractmethod
    def get_default(self, user_id: uuid.UUID) -> Workspace | None: ...

    @abstractmethod
    def set_default(self, user_id: uuid.UUID, name: str) -> Workspace:
        """Raises WorkspaceNotFound."""

    @abstractmethod
    def remove(self, user_id: uuid.UUID, name: str) -> Workspace:
        """Unregister and return the removed workspace. Raises WorkspaceNotFound."""


class NoteRepository(ABC):
    @abstractmethod
    def upsert(
        self,
        workspace_id: uuid.UUID,
        summary: NoteSummary,
        content_hash: str,
        synced_at: datetime,
    ) -> Note:
        """Insert the note, or update it if this workspace already has one with that source_id."""

    @abstractmethod
    def list(self, workspace_id: uuid.UUID) -> list[Note]: ...

    @abstractmethod
    def get_by_source_id(self, workspace_id: uuid.UUID, source_id: str) -> Note | None: ...

    @abstractmethod
    def delete(self, workspace_id: uuid.UUID, source_id: str) -> None:
        """Remove the note if it exists."""

    @abstractmethod
    def mark_reviewed(self, note_id: uuid.UUID, content_hash: str) -> None: ...

    @abstractmethod
    def set_icon(self, note_id: uuid.UUID, icon: str | None) -> None: ...

    @abstractmethod
    def mark_issues_reviewed(self, note_id: uuid.UUID, content_hash: str) -> None:
        """Record that the note's issues were updated for this version of its text."""

    @abstractmethod
    def set_ignored(self, note_id: uuid.UUID, ignored: bool) -> None: ...

    @abstractmethod
    def replace_blocks(self, note_id: uuid.UUID, blocks: list[BlockText]) -> None:
        """Store the note's text blocks (in reading order), replacing the previous ones."""

    @abstractmethod
    def blocks(self, note_id: uuid.UUID) -> list[BlockText]:
        """The note's text blocks in reading order."""


class ImageRepository(ABC):
    @abstractmethod
    def list_for_note(self, note_id: uuid.UUID) -> list[NoteImage]: ...

    @abstractmethod
    def list_for_workspace(
        self, workspace_id: uuid.UUID, include_ignored: bool = True
    ) -> list[NoteImage]: ...

    @abstractmethod
    def save(
        self,
        note_id: uuid.UUID,
        ref: ImageRef,
        path: str | None,
        image_hash: str | None,
        mime_type: str | None,
    ) -> NoteImage:
        """Insert or update the image at (note, block). The description is kept when the image
        hash is unchanged and cleared when it changes."""

    @abstractmethod
    def delete(self, image_id: uuid.UUID) -> None: ...

    @abstractmethod
    def find_description(self, image_hash: str) -> tuple[str, str | None] | None:
        """(description, described_by) from any image with this hash, in any workspace."""

    @abstractmethod
    def set_description(self, image_id: uuid.UUID, description: str, described_by: str) -> None: ...


class PointNotFound(LookupError):
    pass


class QuestionAlreadyClosed(LookupError):
    """The issued question was answered or skipped already (or doesn't exist)."""


# Turns (time, score, question type) triples into a mastery state, e.g. scheduler.mastery.replay
# with the user's settings. Passed in so the repositories don't need to know the mastery rule.
MasteryFn = Callable[[list[tuple[datetime, float, str]]], MasteryState]


class KnowledgeRepository(ABC):
    @abstractmethod
    def get(self, point_id: uuid.UUID) -> KnowledgePoint | None: ...

    @abstractmethod
    def list_for_user(self, user_id: uuid.UUID) -> list[KnowledgePoint]: ...

    @abstractmethod
    def list_for_note(self, note_id: uuid.UUID) -> list[KnowledgePoint]: ...

    @abstractmethod
    def sources(self, user_id: uuid.UUID) -> list[PointSource]:
        """Every (point, note) link of the user's points, with note path and workspace name.
        Links to notes hidden by .learnovaignore are left out."""

    @abstractmethod
    def create(
        self, user_id: uuid.UUID, name: str, summary: str, importance: int
    ) -> KnowledgePoint: ...

    @abstractmethod
    def update(self, point_id: uuid.UUID, name: str, summary: str) -> KnowledgePoint:
        """Change name and summary; importance is left alone."""

    @abstractmethod
    def set_importance(self, point_id: uuid.UUID, importance: int) -> KnowledgePoint: ...

    @abstractmethod
    def link(self, point_id: uuid.UUID, note_id: uuid.UUID) -> None:
        """Idempotent."""

    @abstractmethod
    def unlink(self, point_id: uuid.UUID, note_id: uuid.UUID) -> None: ...

    @abstractmethod
    def merge(
        self,
        point_id: uuid.UUID,
        into_id: uuid.UUID,
        mastery_from: MasteryFn,
        parents: list[ParentLink] | None = None,
    ) -> KnowledgePoint:
        """In one transaction: move point_id's note links and attempts onto into_id, rebuild
        into_id's mastery from all its attempts, and delete point_id. Raises PointNotFound.
        If `parents` is given, it replaces all of the user's parent links (see restructure)."""

    @abstractmethod
    def parents(self, user_id: uuid.UUID) -> list[ParentLink]:
        """All parent links between the user's points."""

    @abstractmethod
    def restructure(
        self, user_id: uuid.UUID, new_points: list[KnowledgePoint], parents: list[ParentLink]
    ) -> None:
        """In one transaction: add `new_points` (with their ids) and replace all of the user's
        parent links with `parents`. The caller has checked the rules."""


class AttemptRepository(ABC):
    @abstractmethod
    def record(
        self,
        user_id: uuid.UUID,
        point_id: uuid.UUID,
        question: str,
        question_type: QuestionType,
        answer: str,
        score: float,
        feedback: str,
        created_at: datetime,
        mastery_from: MasteryFn,
        question_data: dict | None = None,
        issued_question_id: uuid.UUID | None = None,
    ) -> tuple[Attempt, Mastery]:
        """Save the attempt and rebuild the point's mastery, in one transaction. With
        issued_question_id, that question is closed as answered in the same transaction; if it
        is not open any more, nothing is saved and QuestionAlreadyClosed is raised."""

    @abstractmethod
    def list_for_point(self, point_id: uuid.UUID) -> list[Attempt]:
        """Oldest first."""

    @abstractmethod
    def mastery(self, user_id: uuid.UUID) -> dict[uuid.UUID, Mastery]:
        """Mastery of every point that has been quizzed, by point id."""


class IssuedQuestionRepository(ABC):
    @abstractmethod
    def create(
        self,
        user_id: uuid.UUID,
        point_id: uuid.UUID,
        question_type: QuestionType,
        text: str,
        options: list[str] | None,
        hidden: dict,
        notes: list[dict],
        created_at: datetime,
    ) -> IssuedQuestion: ...

    @abstractmethod
    def get(self, question_id: uuid.UUID) -> IssuedQuestion | None: ...

    @abstractmethod
    def skip(self, question_id: uuid.UUID, closed_at: datetime) -> None:
        """Raises QuestionAlreadyClosed if it isn't open."""

    @abstractmethod
    def purge_open(self, older_than: datetime) -> int:
        """Delete open questions created before `older_than`; returns how many."""


class WebImportRepository(ABC):
    @abstractmethod
    def add(
        self, user_id: uuid.UUID, index_url: str, target_page_id: str, slug: str
    ) -> WebImport: ...

    @abstractmethod
    def get_by_url(self, user_id: uuid.UUID, index_url: str) -> WebImport | None: ...

    @abstractmethod
    def list(self, user_id: uuid.UUID) -> list[WebImport]: ...

    @abstractmethod
    def set_progress(self, import_id: uuid.UUID, cursor: int, layout_hash: str) -> None: ...

    @abstractmethod
    def mark_completed(self, import_id: uuid.UUID, at: datetime) -> None: ...

    @abstractmethod
    def delete(self, import_id: uuid.UUID) -> None:
        """The import and its articles."""

    @abstractmethod
    def articles(self, import_id: uuid.UUID) -> list[WebArticle]: ...

    @abstractmethod
    def new_article(
        self,
        import_id: uuid.UUID,
        url: str,
        section: tuple[str, ...],
        title: str,
        state: WebArticleState,
        seen_at: datetime,
    ) -> WebArticle: ...

    @abstractmethod
    def save_article(self, article: WebArticle) -> WebArticle:
        """Write every field of an existing article (matched by id)."""


class IssueRepository(ABC):
    @abstractmethod
    def create(
        self,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        note_id: uuid.UUID,
        note_path: tuple[str, ...],
        kind: IssueKind,
        quote: str,
        topic: str,
        problem: str,
        point_id: uuid.UUID | None,
        fingerprint: str,
        now: datetime,
        block_id: str | None = None,
    ) -> ReviewIssue: ...

    @abstractmethod
    def get(self, issue_id: uuid.UUID) -> ReviewIssue | None: ...

    @abstractmethod
    def list_for_note(
        self, note_id: uuid.UUID, statuses: list[IssueStatus] | None = None
    ) -> list[ReviewIssue]: ...

    @abstractmethod
    def list(
        self,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        statuses: list[IssueStatus] | None = None,
        kinds: list[IssueKind] | None = None,
    ) -> list[ReviewIssue]:
        """Issues of notes hidden by .learnovaignore are left out."""

    @abstractmethod
    def set_block(self, issue_id: uuid.UUID, block_id: str | None) -> None: ...

    @abstractmethod
    def seen(self, issue_ids: list[uuid.UUID], now: datetime) -> None:
        """A review saw these issues still present."""

    @abstractmethod
    def set_status(
        self,
        issue_id: uuid.UUID,
        status: IssueStatus,
        now: datetime,
        closed_by: str | None,
    ) -> ReviewIssue:
        """OPEN clears the closed time; any other status sets it."""
