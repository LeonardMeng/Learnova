"""Review issues: list with filters, and fix / dismiss / reopen."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from learnova.api import schemas
from learnova.api.app import get_repos, get_settings, workspace_named
from learnova.config import Settings
from learnova.models import IssueKind, IssueStatus
from learnova.services import issues as issues_service
from learnova.storage.database import Repositories

router = APIRouter(prefix="/api/issues", tags=["issues"])
Repos = Annotated[Repositories, Depends(get_repos)]
Config = Annotated[Settings, Depends(get_settings)]
ACTION = {"fixed": "fix", "dismissed": "dismiss", "open": "reopen"}


def workspace_names(repos: Repositories) -> dict[uuid.UUID, str]:
    user = repos.users.get_or_create_local()
    return {w.id: w.name for w in repos.workspaces.list(user.id)}


def issue_out(r: issues_service.RankedIssue, spaces: dict[uuid.UUID, str]) -> schemas.IssueOut:
    i = r.issue
    return schemas.IssueOut(
        id=i.id, kind=i.kind.value, status=i.status.value, quote=i.quote, topic=i.topic,
        problem=i.problem, workspace=spaces.get(i.workspace_id), note_path=list(i.note_path),
        point=schemas.PointRefOut(id=r.point.id, name=r.point.name) if r.point else None,
        priority=round(r.priority, 2), url=r.url, first_seen=i.first_seen,
        last_seen=i.last_seen, closed_at=i.closed_at, closed_by=i.closed_by,
    )  # fmt: skip


@router.get("", response_model=list[schemas.IssueOut])
def list_issues(
    repos: Repos,
    settings: Config,
    workspace: str | None = None,
    status: Literal["open", "fixed", "dismissed", "obsolete", "all"] = "open",
    kind: Literal["factual_error", "unclear", "missing"] | None = None,
):
    """Highest priority first. Issues of notes hidden by .learnovaignore are left out."""
    ws_id = workspace_named(repos, workspace).id if workspace is not None else None
    statuses = list(IssueStatus) if status == "all" else [IssueStatus(status)]
    kinds = [IssueKind(kind)] if kind else None
    spaces = workspace_names(repos)
    ranked = issues_service.ranked(repos, settings.issues, ws_id, statuses, kinds)
    return [issue_out(r, spaces) for r in ranked]


@router.patch("/{issue_id}", response_model=schemas.IssueOut)
def change_status(issue_id: uuid.UUID, body: schemas.IssuePatchIn, repos: Repos, settings: Config):
    """`fixed` or `dismissed` for an open issue, `open` to reopen one. A dismissed issue is not
    raised again by later reviews. 409 if the issue's status doesn't allow the change."""
    changed = issues_service.change_status(repos, str(issue_id), ACTION[body.status],
                                           datetime.now(UTC))  # fmt: skip
    ranked = issues_service.ranked(repos, settings.issues, statuses=list(IssueStatus))
    match = next((r for r in ranked if r.issue.id == changed.id), None)
    if match is None:  # its note is hidden by .learnovaignore
        match = issues_service.RankedIssue(changed, 0.0, None, None)
    return issue_out(match, workspace_names(repos))
