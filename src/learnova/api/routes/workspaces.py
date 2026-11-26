"""Workspaces, sync, review and reports."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends

from learnova.api import schemas
from learnova.api.app import WorkspaceLocks, get_locks, get_repos, workspace_named
from learnova.models import IssueKind, Workspace
from learnova.report import list_reports, read_report
from learnova.services import review as review_service
from learnova.services.workspace_sync import run_sync
from learnova.services.workspaces import WorkspaceService
from learnova.sources import notion
from learnova.storage.database import Repositories
from learnova.sync import Change

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
Repos = Annotated[Repositories, Depends(get_repos)]
Locks = Annotated[WorkspaceLocks, Depends(get_locks)]


def _out(ws: Workspace, found: bool) -> schemas.WorkspaceOut:
    return schemas.WorkspaceOut(
        name=ws.name,
        path=str(ws.path),
        notion_page_id=ws.notion_page_id,
        is_default=ws.is_default,
        found=found,
    )


@router.get("", response_model=list[schemas.WorkspaceOut])
def list_workspaces(repos: Repos):
    return [_out(item.workspace, item.found) for item in WorkspaceService(repos).list()]


@router.post("", response_model=schemas.CreatedWorkspaceOut, status_code=201)
def create_workspace(body: schemas.WorkspaceIn, repos: Repos):
    page_id = notion.parse_page_id(body.notion_page)
    title = notion.page_title(notion.make_client(), page_id)  # Notion must see the page
    ws = WorkspaceService(repos).create(body.name, page_id, Path(body.path))
    return schemas.CreatedWorkspaceOut(**_out(ws, True).model_dump(), notion_title=title)


@router.put("/{name}/default", response_model=schemas.WorkspaceOut)
def set_default(name: str, repos: Repos):
    return _out(WorkspaceService(repos).use(name), True)


@router.delete("/{name}", response_model=schemas.RemovedWorkspaceOut)
def remove_workspace(name: str, repos: Repos):
    ws = WorkspaceService(repos).remove(name)
    return schemas.RemovedWorkspaceOut(removed=ws.name, was_default=ws.is_default)


@router.post("/{name}/sync", response_model=schemas.SyncOut)
def sync(name: str, body: schemas.SyncIn, repos: Repos, locks: Locks):
    ws = workspace_named(repos, name)
    with locks.hold(ws.name, "sync"):
        run = run_sync(repos, ws, full=body.full, describe=body.describe)
    result = run.result
    described = run.described
    return schemas.SyncOut(
        changes=[
            schemas.ChangeOut(
                kind=c.kind.value,
                path=list(c.path),
                previous_path=list(c.previous_path) if c.previous_path else None,
            )
            for c in result.changes
            if c.kind != Change.UNCHANGED
        ],
        counts={kind.value: len(result.of(kind)) for kind in Change},
        notes_downloaded=result.downloaded,
        images_downloaded=result.images_downloaded,
        image_errors=list(result.image_errors),
        block_types=dict(run.block_types),
        skipped_databases=run.skipped_databases,
        images_described=described.described if described else 0,
        images_reused=described.reused if described else 0,
        describe_errors=list(described.errors) if described else [],
        pending_images=run.pending_images,
        not_described_because=run.not_described_because,
        ignored=[list(p.path) for p in result.ignored],
    )


@router.post("/{name}/review", response_model=schemas.ReviewOut)
def review(name: str, body: schemas.ReviewIn, repos: Repos, locks: Locks):
    ws = workspace_named(repos, name)
    with locks.hold(ws.name, "review"):
        outcome = review_service.run_review(repos, ws, body.all)
    if outcome is None:
        return schemas.ReviewOut(nothing_to_review=True)
    run = outcome.run
    outcomes = [o for r in run.reviewed for o in r.outcomes]
    return schemas.ReviewOut(
        nothing_to_review=False,
        reviewed=len(run.reviewed),
        skipped=len(run.skipped),
        failed=[schemas.FailedNoteOut(path=list(n.path), error=e) for n, e in run.failed],
        new_points=sum(1 for o in outcomes if o.status == review_service.Status.NEW),
        issues=sum(1 for r in run.reviewed for i in r.new_issues if i.kind != IssueKind.MISSING),
        missing_topics=sum(
            1 for r in run.reviewed for i in r.new_issues if i.kind == IssueKind.MISSING
        ),
        fixed=sum(len(r.fixed_issues) for r in run.reviewed),
        open=outcome.open_issues,
        possible_duplicates=sum(1 for o in outcomes if o.possible_duplicate),
        report=outcome.report.name,
        model=outcome.model,
        pending_images=outcome.pending_images,
    )


@router.get("/{name}/reports", response_model=list[schemas.ReportFileOut])
def reports(name: str, repos: Repos):
    return [
        schemas.ReportFileOut(name=r.name, size=r.size, modified=r.modified)
        for r in list_reports(workspace_named(repos, name))
    ]


@router.get("/{name}/reports/{file}", response_model=schemas.ReportOut)
def report(name: str, file: str, repos: Repos):
    return schemas.ReportOut(name=file, markdown=read_report(workspace_named(repos, name), file))
