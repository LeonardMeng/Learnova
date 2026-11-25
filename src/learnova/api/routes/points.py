"""Knowledge points: list, change importance, merge."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from learnova.api import schemas
from learnova.api.app import get_repos, workspace_named
from learnova.models import KnowledgePoint, Mastery, PointSource
from learnova.services import knowledge
from learnova.storage.database import Repositories

router = APIRouter(prefix="/api/points", tags=["knowledge points"])
Repos = Annotated[Repositories, Depends(get_repos)]


def point_out(
    point: KnowledgePoint, sources: list[PointSource], mastery: Mastery | None
) -> schemas.PointOut:
    return schemas.PointOut(
        id=point.id,
        name=point.name,
        summary=point.summary,
        importance=point.importance,
        sources=[
            schemas.SourceOut(workspace=s.workspace_name, path=list(s.note_path)) for s in sources
        ],
        mastery=mastery.mastery if mastery else None,
        review_count=mastery.review_count if mastery else 0,
        last_reviewed=mastery.last_reviewed if mastery else None,
    )


def _one(repos: Repositories, point_id: uuid.UUID) -> schemas.PointOut:
    user = repos.users.get_or_create_local()
    point = repos.knowledge.get(point_id)
    if point is None:
        raise knowledge.NoSuchPoint(f"No knowledge point with id {point_id}.")
    sources = [s for s in repos.knowledge.sources(user.id) if s.point_id == point_id]
    return point_out(point, sources, repos.attempts.mastery(user.id).get(point_id))


@router.get("", response_model=list[schemas.PointOut])
def list_points(repos: Repos, workspace: str | None = None):
    if workspace is not None:
        workspace_named(repos, workspace)  # 404 for an unknown name
    user = repos.users.get_or_create_local()
    mastery = repos.attempts.mastery(user.id)
    return [
        point_out(item.point, item.sources, mastery.get(item.point.id))
        for item in knowledge.list_points(repos, workspace)
    ]


@router.get("/{point_id}", response_model=schemas.PointOut)
def get_point(point_id: uuid.UUID, repos: Repos):
    return _one(repos, point_id)


@router.patch("/{point_id}", response_model=schemas.PointOut)
def set_importance(point_id: uuid.UUID, body: schemas.ImportanceIn, repos: Repos):
    knowledge.set_importance(repos, str(point_id), body.importance)
    return _one(repos, point_id)


@router.post("/{point_id}/merge", response_model=schemas.MergeOut)
def merge(point_id: uuid.UUID, body: schemas.MergeIn, repos: Repos):
    result = knowledge.merge_points(repos, str(point_id), str(body.into))
    return schemas.MergeOut(kept=_one(repos, result.into.id), removed=result.merged.id)
