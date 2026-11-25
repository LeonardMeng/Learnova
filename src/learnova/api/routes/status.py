"""Mastery overview, the same figures as `learnova status`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from learnova.api import schemas
from learnova.api.app import get_repos, get_settings, workspace_named
from learnova.api.routes.points import point_out
from learnova.config import Settings
from learnova.services import status as status_service
from learnova.storage.database import Repositories

router = APIRouter(prefix="/api/status", tags=["status"])


def _summary(s: status_service.Summary) -> schemas.SummaryOut:
    return schemas.SummaryOut(
        name=s.name,
        points=s.points,
        quizzed=s.quizzed,
        average_mastery=s.average_mastery,
        last_quiz=s.last_quiz,
    )


@router.get("", response_model=schemas.StatusOut)
def get_status(
    repos: Annotated[Repositories, Depends(get_repos)],
    settings: Annotated[Settings, Depends(get_settings)],
    workspace: str | None = None,
    weakest: Annotated[int, Query(ge=1, le=200)] = 10,
):
    if workspace is not None:
        workspace_named(repos, workspace)
    st = status_service.status(repos, datetime.now(UTC), settings.weights, workspace, weakest)
    return schemas.StatusOut(
        overall=_summary(st.overall),
        workspaces=[_summary(s) for s in st.workspaces],
        weakest=[
            schemas.WeakPointOut(point=point_out(r.point, r.sources, r.mastery), weight=r.weight)
            for r in st.weakest
        ],
    )
