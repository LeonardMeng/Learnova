"""A workspace's notes (its Notion page tree), one note with its points and issues, checking a
Notion page before adding a workspace, the web imports, and who the local user is."""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from learnova.anchors import notion_url
from learnova.api import schemas
from learnova.api.app import get_repos, get_settings, workspace_named
from learnova.api.routes.issues import issue_out, workspace_names
from learnova.api.routes.points import point_out
from learnova.config import Settings
from learnova.errors import NotFoundError
from learnova.models import IssueStatus, Note, WebArticleState
from learnova.services import issues as issues_service
from learnova.sources import notion
from learnova.storage.database import Repositories

router = APIRouter(prefix="/api", tags=["notes"])
Repos = Annotated[Repositories, Depends(get_repos)]
Config = Annotated[Settings, Depends(get_settings)]


class NoSuchNote(NotFoundError):
    pass


def _note_out(note: Note, points: int, open_issues: int) -> schemas.NoteOut:
    return schemas.NoteOut(
        id=note.id, title=note.title, path=list(note.path), icon=note.icon,
        notion_url=notion_url(note.source_id), ignored=note.ignored, points=points,
        open_issues=open_issues, last_edited=note.last_edited,
        reviewed=note.reviewed_hash == note.content_hash,
    )  # fmt: skip


@router.get("/workspaces/{name}/notes", response_model=list[schemas.NoteOut])
def list_notes(name: str, repos: Repos):
    """Every note in Notion order of their paths (parents before their sub-pages), ignored ones
    included and marked."""
    ws = workspace_named(repos, name)
    user = repos.users.get_or_create_local()
    points: dict[uuid.UUID, int] = {}
    for s in repos.knowledge.sources(user.id):
        points[s.note_id] = points.get(s.note_id, 0) + 1
    issues: dict[uuid.UUID, int] = {}
    for i in repos.issues.list(user.id, ws.id, [IssueStatus.OPEN]):
        if i.note_id:
            issues[i.note_id] = issues.get(i.note_id, 0) + 1
    notes = sorted(repos.notes.list(ws.id), key=lambda n: [p.lower() for p in n.path])
    return [_note_out(n, points.get(n.id, 0), issues.get(n.id, 0)) for n in notes]


@router.get("/workspaces/{name}/notes/{note_id}", response_model=schemas.NoteDetailOut)
def get_note(name: str, note_id: uuid.UUID, repos: Repos, settings: Config):
    ws = workspace_named(repos, name)
    note = next((n for n in repos.notes.list(ws.id) if n.id == note_id), None)
    if note is None:
        raise NoSuchNote(f"No note with id {note_id} in workspace {name!r}.")
    user = repos.users.get_or_create_local()
    mastery = repos.attempts.mastery(user.id)
    sources = repos.knowledge.sources(user.id)
    points = [
        point_out(p, [s for s in sources if s.point_id == p.id], mastery.get(p.id))
        for p in sorted(repos.knowledge.list_for_note(note.id), key=lambda p: -p.importance)
    ]
    spaces = workspace_names(repos)
    issues = [
        issue_out(r, spaces)
        for r in issues_service.ranked(repos, settings.issues, ws.id, list(IssueStatus))
        if r.issue.note_id == note.id
    ]
    issues.sort(key=lambda i: i.status != "open")
    open_count = sum(1 for i in issues if i.status == "open")
    return schemas.NoteDetailOut(
        **_note_out(note, len(points), open_count).model_dump(),
        knowledge_points=points,
        issues=issues,
    )


@router.post("/notion/check", response_model=schemas.NotionCheckOut, tags=["workspaces"])
def check_notion_page(body: schemas.NotionCheckIn):
    """Whether Learnova can see a Notion page, before creating a workspace for it. If it can't,
    the error says how to share the page with the integration."""
    page_id = notion.parse_page_id(body.notion_page)
    title = notion.page_title(notion.make_client(), page_id)
    words = re.findall(r"\w+", title.lower())
    return schemas.NotionCheckOut(page_id=page_id, title=title, suggested_name="-".join(words))


@router.get("/web-imports", response_model=list[schemas.WebImportOut], tags=["web imports"])
def list_web_imports(repos: Repos):
    """The sites imported with `learnova web import`, newest first."""
    user = repos.users.get_or_create_local()
    out = []
    for imp in sorted(repos.web_imports.list(user.id), key=lambda i: i.created_at, reverse=True):
        articles = repos.web_imports.articles(imp.id)
        checked = [a.checked_at for a in articles if a.checked_at]

        def count(state: WebArticleState) -> int:
            return sum(1 for a in articles if a.state == state)  # noqa: B023

        out.append(
            schemas.WebImportOut(
                index_url=imp.index_url,
                notion_url=notion_url(imp.target_page_id),
                folder=imp.slug,
                created_at=imp.created_at,
                completed_at=imp.completed_at,
                imported=count(WebArticleState.IMPORTED),
                new=count(WebArticleState.NEW),
                removed=count(WebArticleState.REMOVED),
                last_checked=max(checked) if checked else None,
            )  # fmt: skip
        )
    return out


@router.get("/me", response_model=schemas.MeOut, tags=["meta"])
def me(repos: Repos):
    """The local user (there are no accounts yet)."""
    return schemas.MeOut(name=repos.users.get_or_create_local().name)
