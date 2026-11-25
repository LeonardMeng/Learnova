"""SQLAlchemy implementation of the repository interfaces. Each method runs in its own
transaction (`with self._sessions.begin()` commits on success, rolls back on error) and
converts rows to domain models before returning."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from learnova.models import (
    Attempt,
    ImageRef,
    IssuedQuestion,
    IssuedStatus,
    KnowledgePoint,
    Mastery,
    Note,
    NoteImage,
    NoteSummary,
    PointSource,
    QuestionType,
    User,
    Workspace,
)
from learnova.storage.repositories import (
    AttemptRepository,
    ImageRepository,
    IssuedQuestionRepository,
    KnowledgeRepository,
    MasteryFn,
    NoteRepository,
    PointNotFound,
    QuestionAlreadyClosed,
    UserRepository,
    WorkspaceNameTaken,
    WorkspaceNotFound,
    WorkspaceRepository,
)
from learnova.storage.tables import (
    AttemptRow,
    IssuedQuestionRow,
    KnowledgePointRow,
    KpSourceRow,
    MasteryRow,
    NoteImageRow,
    NoteRow,
    UserRow,
    WorkspaceRow,
    utcnow,
)

LOCAL_USER_NAME = "local"


def _user(row: UserRow) -> User:
    return User(id=row.id, name=row.name, created_at=row.created_at)


def _workspace(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        notion_page_id=row.notion_page_id,
        path=Path(row.path),
        is_default=row.is_default,
        created_at=row.created_at,
    )


def _note(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        workspace_id=row.workspace_id,
        source_id=row.source_id,
        title=row.title,
        path=tuple(row.path),
        last_edited=row.last_edited_at,
        content_hash=row.content_hash,
        synced_at=row.synced_at,
        reviewed_hash=row.reviewed_hash,
        ignored=row.ignored,
    )


def _image(row: NoteImageRow) -> NoteImage:
    return NoteImage(
        id=row.id,
        note_id=row.note_id,
        block_id=row.block_id,
        source_key=row.source_key,
        caption=row.caption,
        path=row.path,
        image_hash=row.image_hash,
        mime_type=row.mime_type,
        description=row.description,
        described_by=row.described_by,
    )


def _point(row: KnowledgePointRow) -> KnowledgePoint:
    return KnowledgePoint(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        summary=row.summary,
        importance=row.importance,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlUserRepository(UserRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def get_or_create_local(self) -> User:
        with self._sessions.begin() as s:
            row = s.scalars(select(UserRow).where(UserRow.name == LOCAL_USER_NAME)).first()
            if row is None:
                row = UserRow(name=LOCAL_USER_NAME)
                s.add(row)
                s.flush()  # fills in id and created_at
            return _user(row)


class SqlWorkspaceRepository(WorkspaceRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def add(self, user_id, name, notion_page_id, path, workspace_id=None) -> Workspace:
        with self._sessions.begin() as s:
            if self._by_name(s, user_id, name) is not None:
                raise WorkspaceNameTaken(name)
            count = s.scalar(
                select(func.count())
                .select_from(WorkspaceRow)
                .where(WorkspaceRow.user_id == user_id)
            )
            row = WorkspaceRow(
                id=workspace_id or uuid.uuid4(),
                user_id=user_id,
                name=name,
                notion_page_id=notion_page_id,
                path=str(path),
                is_default=count == 0,
            )
            s.add(row)
            s.flush()
            return _workspace(row)

    def list(self, user_id) -> list[Workspace]:
        with self._sessions() as s:
            rows = s.scalars(
                select(WorkspaceRow)
                .where(WorkspaceRow.user_id == user_id)
                .order_by(WorkspaceRow.created_at)
            )
            return [_workspace(r) for r in rows]

    def get(self, workspace_id) -> Workspace | None:
        with self._sessions() as s:
            row = s.get(WorkspaceRow, workspace_id)
            return _workspace(row) if row else None

    def get_by_name(self, user_id, name) -> Workspace | None:
        with self._sessions() as s:
            row = self._by_name(s, user_id, name)
            return _workspace(row) if row else None

    def get_default(self, user_id) -> Workspace | None:
        with self._sessions() as s:
            row = s.scalars(
                select(WorkspaceRow).where(
                    WorkspaceRow.user_id == user_id, WorkspaceRow.is_default.is_(True)
                )
            ).first()
            return _workspace(row) if row else None

    def set_default(self, user_id, name) -> Workspace:
        with self._sessions.begin() as s:
            row = self._by_name(s, user_id, name)
            if row is None:
                raise WorkspaceNotFound(name)
            s.execute(
                update(WorkspaceRow).where(WorkspaceRow.user_id == user_id).values(is_default=False)
            )
            row.is_default = True
            s.flush()
            return _workspace(row)

    def remove(self, user_id, name) -> Workspace:
        with self._sessions.begin() as s:
            row = self._by_name(s, user_id, name)
            if row is None:
                raise WorkspaceNotFound(name)
            removed = _workspace(row)
            s.delete(row)
            return removed

    @staticmethod
    def _by_name(s: Session, user_id: uuid.UUID, name: str) -> WorkspaceRow | None:
        return s.scalars(
            select(WorkspaceRow).where(WorkspaceRow.user_id == user_id, WorkspaceRow.name == name)
        ).first()


class SqlNoteRepository(NoteRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def upsert(self, workspace_id, summary: NoteSummary, content_hash, synced_at) -> Note:
        with self._sessions.begin() as s:
            row = self._by_source(s, workspace_id, summary.source_id)
            if row is None:
                row = NoteRow(workspace_id=workspace_id, source_id=summary.source_id)
                s.add(row)
            row.title = summary.title
            row.path = list(summary.path)
            row.last_edited_at = summary.last_edited
            row.content_hash = content_hash
            row.synced_at = synced_at
            row.ignored = False  # listed by the source, so not ignored
            s.flush()
            return _note(row)

    def list(self, workspace_id) -> list[Note]:
        with self._sessions() as s:
            rows = s.scalars(select(NoteRow).where(NoteRow.workspace_id == workspace_id))
            return [_note(r) for r in rows]

    def get_by_source_id(self, workspace_id, source_id) -> Note | None:
        with self._sessions() as s:
            row = self._by_source(s, workspace_id, source_id)
            return _note(row) if row else None

    def delete(self, workspace_id, source_id) -> None:
        with self._sessions.begin() as s:
            row = self._by_source(s, workspace_id, source_id)
            if row is not None:
                s.delete(row)

    def set_ignored(self, note_id, ignored) -> None:
        with self._sessions.begin() as s:
            row = s.get(NoteRow, note_id)
            if row is not None:
                row.ignored = ignored

    def mark_reviewed(self, note_id, content_hash) -> None:
        with self._sessions.begin() as s:
            row = s.get(NoteRow, note_id)
            if row is not None:
                row.reviewed_hash = content_hash

    @staticmethod
    def _by_source(s: Session, workspace_id: uuid.UUID, source_id: str) -> NoteRow | None:
        return s.scalars(
            select(NoteRow).where(
                NoteRow.workspace_id == workspace_id, NoteRow.source_id == source_id
            )
        ).first()


class SqlImageRepository(ImageRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def list_for_note(self, note_id) -> list[NoteImage]:
        with self._sessions() as s:
            rows = s.scalars(select(NoteImageRow).where(NoteImageRow.note_id == note_id))
            return [_image(r) for r in rows]

    def list_for_workspace(self, workspace_id, include_ignored=True) -> list[NoteImage]:
        with self._sessions() as s:
            query = (
                select(NoteImageRow)
                .join(NoteRow, NoteRow.id == NoteImageRow.note_id)
                .where(NoteRow.workspace_id == workspace_id)
            )
            if not include_ignored:
                query = query.where(NoteRow.ignored.is_(False))
            rows = s.scalars(query)
            return [_image(r) for r in rows]

    def save(self, note_id, ref: ImageRef, path, image_hash, mime_type) -> NoteImage:
        with self._sessions.begin() as s:
            row = s.scalars(
                select(NoteImageRow).where(
                    NoteImageRow.note_id == note_id, NoteImageRow.block_id == ref.block_id
                )
            ).first()
            if row is None:
                row = NoteImageRow(note_id=note_id, block_id=ref.block_id)
                s.add(row)
            if row.image_hash != image_hash:
                row.description = None
                row.described_by = None
            row.source_key = ref.source_key
            row.caption = ref.caption
            row.path = path
            row.image_hash = image_hash
            row.mime_type = mime_type
            s.flush()
            return _image(row)

    def delete(self, image_id) -> None:
        with self._sessions.begin() as s:
            row = s.get(NoteImageRow, image_id)
            if row is not None:
                s.delete(row)

    def find_description(self, image_hash) -> tuple[str, str | None] | None:
        with self._sessions() as s:
            row = s.scalars(
                select(NoteImageRow).where(
                    NoteImageRow.image_hash == image_hash,
                    NoteImageRow.description.is_not(None),
                )
            ).first()
            return (row.description, row.described_by) if row else None

    def set_description(self, image_id, description, described_by) -> None:
        with self._sessions.begin() as s:
            row = s.get(NoteImageRow, image_id)
            if row is not None:
                row.description = description
                row.described_by = described_by


class SqlKnowledgeRepository(KnowledgeRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def get(self, point_id) -> KnowledgePoint | None:
        with self._sessions() as s:
            row = s.get(KnowledgePointRow, point_id)
            return _point(row) if row else None

    def list_for_user(self, user_id) -> list[KnowledgePoint]:
        with self._sessions() as s:
            rows = s.scalars(
                select(KnowledgePointRow)
                .where(KnowledgePointRow.user_id == user_id)
                .order_by(KnowledgePointRow.created_at)
            )
            return [_point(r) for r in rows]

    def list_for_note(self, note_id) -> list[KnowledgePoint]:
        with self._sessions() as s:
            rows = s.scalars(
                select(KnowledgePointRow)
                .join(KpSourceRow, KpSourceRow.point_id == KnowledgePointRow.id)
                .where(KpSourceRow.note_id == note_id)
                .order_by(KnowledgePointRow.created_at)
            )
            return [_point(r) for r in rows]

    def sources(self, user_id) -> list[PointSource]:
        with self._sessions() as s:
            rows = s.execute(
                select(KpSourceRow.point_id, NoteRow.id, NoteRow.path, WorkspaceRow.name)
                .join(NoteRow, NoteRow.id == KpSourceRow.note_id)
                .join(WorkspaceRow, WorkspaceRow.id == NoteRow.workspace_id)
                .join(KnowledgePointRow, KnowledgePointRow.id == KpSourceRow.point_id)
                .where(KnowledgePointRow.user_id == user_id, NoteRow.ignored.is_(False))
            )
            return [
                PointSource(point_id=p, note_id=n, note_path=tuple(path), workspace_name=w)
                for p, n, path, w in rows
            ]

    def create(self, user_id, name, summary, importance) -> KnowledgePoint:
        with self._sessions.begin() as s:
            row = KnowledgePointRow(
                user_id=user_id, name=name, summary=summary, importance=importance
            )
            s.add(row)
            s.flush()
            return _point(row)

    def update(self, point_id, name, summary) -> KnowledgePoint:
        with self._sessions.begin() as s:
            row = s.get(KnowledgePointRow, point_id)
            if row is None:
                raise PointNotFound(str(point_id))
            row.name, row.summary, row.updated_at = name, summary, utcnow()
            s.flush()
            return _point(row)

    def set_importance(self, point_id, importance) -> KnowledgePoint:
        with self._sessions.begin() as s:
            row = s.get(KnowledgePointRow, point_id)
            if row is None:
                raise PointNotFound(str(point_id))
            row.importance, row.updated_at = importance, utcnow()
            s.flush()
            return _point(row)

    def link(self, point_id, note_id) -> None:
        with self._sessions.begin() as s:
            if s.get(KpSourceRow, (point_id, note_id)) is None:
                s.add(KpSourceRow(point_id=point_id, note_id=note_id))

    def unlink(self, point_id, note_id) -> None:
        with self._sessions.begin() as s:
            row = s.get(KpSourceRow, (point_id, note_id))
            if row is not None:
                s.delete(row)

    def merge(self, point_id, into_id, mastery_from: MasteryFn) -> KnowledgePoint:
        with self._sessions.begin() as s:
            point, into = s.get(KnowledgePointRow, point_id), s.get(KnowledgePointRow, into_id)
            if point is None or into is None:
                raise PointNotFound(str(point_id if point is None else into_id))
            kept_notes = set(
                s.scalars(select(KpSourceRow.note_id).where(KpSourceRow.point_id == into_id))
            )
            for link in s.scalars(select(KpSourceRow).where(KpSourceRow.point_id == point_id)):
                if link.note_id not in kept_notes:
                    s.add(KpSourceRow(point_id=into_id, note_id=link.note_id))
                s.delete(link)
            s.execute(
                update(AttemptRow).where(AttemptRow.point_id == point_id).values(point_id=into_id)
            )
            s.execute(
                update(IssuedQuestionRow)
                .where(IssuedQuestionRow.point_id == point_id)
                .values(point_id=into_id)
            )
            old_mastery = s.get(MasteryRow, point_id)
            if old_mastery is not None:
                s.delete(old_mastery)
            s.flush()
            _rebuild_mastery(s, into.user_id, into_id, mastery_from)
            s.delete(point)
            into.updated_at = utcnow()
            s.flush()
            return _point(into)


def _attempt(row: AttemptRow) -> Attempt:
    return Attempt(
        id=row.id,
        user_id=row.user_id,
        point_id=row.point_id,
        question=row.question,
        question_type=QuestionType(row.question_type),
        answer=row.answer,
        score=row.score,
        feedback=row.feedback,
        created_at=row.created_at,
        question_data=row.question_data,
    )


def _mastery(row: MasteryRow) -> Mastery:
    return Mastery(
        point_id=row.point_id,
        mastery=row.mastery,
        review_count=row.review_count,
        last_reviewed=row.last_reviewed,
    )


def _rebuild_mastery(s: Session, user_id, point_id, mastery_from: MasteryFn) -> MasteryRow | None:
    """Replay all of the point's attempts into its mastery row (inside the caller's transaction)."""
    triples = [
        (when, score, qtype)
        for when, score, qtype in s.execute(
            select(AttemptRow.created_at, AttemptRow.score, AttemptRow.question_type).where(
                AttemptRow.point_id == point_id
            )
        )
    ]
    row = s.get(MasteryRow, point_id)
    if not triples:
        if row is not None:
            s.delete(row)
        return None
    state = mastery_from(triples)
    if row is None:
        row = MasteryRow(point_id=point_id, user_id=user_id)
        s.add(row)
    row.mastery, row.review_count, row.last_reviewed = (
        state.mastery,
        state.review_count,
        state.last_reviewed,
    )
    s.flush()
    return row


class SqlAttemptRepository(AttemptRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def record(
        self,
        user_id,
        point_id,
        question,
        question_type,
        answer,
        score,
        feedback,
        created_at,
        mastery_from: MasteryFn,
        question_data=None,
        issued_question_id=None,
    ) -> tuple[Attempt, Mastery]:
        with self._sessions.begin() as s:
            row = AttemptRow(
                user_id=user_id,
                point_id=point_id,
                question=question,
                question_type=QuestionType(question_type).value,
                answer=answer,
                score=score,
                feedback=feedback,
                created_at=created_at,
                question_data=question_data,
            )
            s.add(row)
            s.flush()
            if issued_question_id is not None:
                closed = s.execute(
                    update(IssuedQuestionRow)
                    .where(
                        IssuedQuestionRow.id == issued_question_id,
                        IssuedQuestionRow.status == IssuedStatus.OPEN.value,
                    )
                    .values(
                        status=IssuedStatus.ANSWERED.value, closed_at=created_at, attempt_id=row.id
                    )
                )
                if closed.rowcount != 1:
                    raise QuestionAlreadyClosed(str(issued_question_id))  # rolls everything back
            mastery = _rebuild_mastery(s, user_id, point_id, mastery_from)
            return _attempt(row), _mastery(mastery)

    def list_for_point(self, point_id) -> list[Attempt]:
        with self._sessions() as s:
            rows = s.scalars(
                select(AttemptRow)
                .where(AttemptRow.point_id == point_id)
                .order_by(AttemptRow.created_at)
            )
            return [_attempt(r) for r in rows]

    def mastery(self, user_id) -> dict[uuid.UUID, Mastery]:
        with self._sessions() as s:
            rows = s.scalars(select(MasteryRow).where(MasteryRow.user_id == user_id))
            return {r.point_id: _mastery(r) for r in rows}


def _issued(row: IssuedQuestionRow) -> IssuedQuestion:
    return IssuedQuestion(
        id=row.id,
        user_id=row.user_id,
        point_id=row.point_id,
        question_type=QuestionType(row.question_type),
        text=row.text,
        options=row.options,
        hidden=row.hidden,
        notes=row.notes,
        status=IssuedStatus(row.status),
        created_at=row.created_at,
        closed_at=row.closed_at,
        attempt_id=row.attempt_id,
    )


class SqlIssuedQuestionRepository(IssuedQuestionRepository):
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def create(
        self, user_id, point_id, question_type, text, options, hidden, notes, created_at
    ) -> IssuedQuestion:
        with self._sessions.begin() as s:
            row = IssuedQuestionRow(
                user_id=user_id,
                point_id=point_id,
                question_type=QuestionType(question_type).value,
                text=text,
                options=options,
                hidden=hidden,
                notes=notes,
                status=IssuedStatus.OPEN.value,
                created_at=created_at,
            )
            s.add(row)
            s.flush()
            return _issued(row)

    def get(self, question_id) -> IssuedQuestion | None:
        with self._sessions() as s:
            row = s.get(IssuedQuestionRow, question_id)
            return _issued(row) if row else None

    def skip(self, question_id, closed_at) -> None:
        with self._sessions.begin() as s:
            closed = s.execute(
                update(IssuedQuestionRow)
                .where(
                    IssuedQuestionRow.id == question_id,
                    IssuedQuestionRow.status == IssuedStatus.OPEN.value,
                )
                .values(status=IssuedStatus.SKIPPED.value, closed_at=closed_at)
            )
            if closed.rowcount != 1:
                raise QuestionAlreadyClosed(str(question_id))

    def purge_open(self, older_than) -> int:
        with self._sessions.begin() as s:
            result = s.execute(
                delete(IssuedQuestionRow).where(
                    IssuedQuestionRow.status == IssuedStatus.OPEN.value,
                    IssuedQuestionRow.created_at < older_than,
                )
            )
            return result.rowcount
