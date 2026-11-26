"""Knowledge points: the tree, one point in detail, changing importance and parents, merging."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from learnova.anchors import notion_url
from learnova.api import schemas
from learnova.api.app import get_repos, get_settings, workspace_named
from learnova.api.routes.issues import issue_out, workspace_names
from learnova.config import Settings
from learnova.models import IssueStatus, KnowledgePoint, Mastery, PointSource
from learnova.services import hierarchy, knowledge
from learnova.services import issues as issues_service
from learnova.storage.database import Repositories

router = APIRouter(prefix="/api/points", tags=["knowledge points"])
Repos = Annotated[Repositories, Depends(get_repos)]
Config = Annotated[Settings, Depends(get_settings)]
MAX_ATTEMPTS = 20


def point_out(
    point: KnowledgePoint, sources: list[PointSource], mastery: Mastery | None
) -> schemas.PointOut:
    return schemas.PointOut(
        id=point.id,
        name=point.name,
        summary=point.summary,
        importance=point.importance,
        sources=[_source(s) for s in sources],
        mastery=mastery.mastery if mastery else None,
        review_count=mastery.review_count if mastery else 0,
        last_reviewed=mastery.last_reviewed if mastery else None,
    )


def _source(s: PointSource) -> schemas.SourceOut:
    return schemas.SourceOut(workspace=s.workspace_name, path=list(s.note_path))


def _topic(t: hierarchy.TopicMastery | None) -> schemas.TopicOut | None:
    return schemas.TopicOut(mastery=t.mastery, quizzed=t.quizzed, total=t.total) if t else None


def _ref(p: KnowledgePoint) -> schemas.PointRefOut:
    return schemas.PointRefOut(id=p.id, name=p.name)


def _get(repos: Repositories, point_id: uuid.UUID) -> KnowledgePoint:
    point = repos.knowledge.get(point_id)
    if point is None:
        raise knowledge.NoSuchPoint(f"No knowledge point with id {point_id}.")
    return point


def _one(repos: Repositories, point_id: uuid.UUID) -> schemas.PointOut:
    user = repos.users.get_or_create_local()
    point = _get(repos, point_id)
    sources = [s for s in repos.knowledge.sources(user.id) if s.point_id == point_id]
    return point_out(point, sources, repos.attempts.mastery(user.id).get(point_id))


def _detail(repos: Repositories, settings: Settings, point_id: uuid.UUID):
    user = repos.users.get_or_create_local()
    base = _one(repos, point_id)
    graph = hierarchy.load(repos, user.id)
    mastery = repos.attempts.mastery(user.id)

    def names(ids: list[uuid.UUID]) -> list[str]:
        return [graph.name(i) for i in ids]

    shortcuts = graph.shortcuts(point_id)
    paths = [names(graph.path(point_id))] + [
        names(graph.path(s)) + [graph.name(point_id)] for s in shortcuts
    ]
    topic = hierarchy.topic_mastery(graph, point_id, mastery) if graph.is_topic(point_id) else None
    pages = {}  # note id -> Notion page id
    for ws in repos.workspaces.list(user.id):
        pages.update({n.id: n.source_id for n in repos.notes.list(ws.id)})
    notes = [
        schemas.NoteRefOut(
            workspace=s.workspace_name,
            path=list(s.note_path),
            notion_url=notion_url(pages[s.note_id]),
        )
        for s in repos.knowledge.sources(user.id)
        if s.point_id == point_id and s.note_id in pages
    ]
    attempts = sorted(repos.attempts.list_for_point(point_id), key=lambda a: a.created_at)
    every = list(IssueStatus)
    spaces = workspace_names(repos)
    issues = [
        issue_out(r, spaces)
        for r in issues_service.ranked(repos, settings.issues, statuses=every)
        if r.issue.point_id == point_id
    ]
    issues.sort(key=lambda i: i.status != "open")
    return schemas.PointDetailOut(
        **base.model_dump(),
        parent_id=graph.primary(point_id),
        shortcuts=[_ref(graph.points[s]) for s in shortcuts],
        paths=paths,
        topic=_topic(topic),
        children=[_ref(graph.points[c]) for c in graph.children(point_id)],
        notes=notes,
        attempts=[
            schemas.AttemptOut(
                question=a.question,
                type=a.question_type.value,
                answer=a.answer,
                score=a.score,
                feedback=a.feedback,
                created_at=a.created_at,
            )  # fmt: skip
            for a in reversed(attempts[-MAX_ATTEMPTS:])
        ],
        issues=issues,
    )


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


@router.get("/tree", response_model=list[schemas.TreePointOut])
def tree(repos: Repos, workspace: str | None = None):
    """Every point under its primary parent, depth first, most important first. With a
    workspace: its points and the topics above them."""
    if workspace is not None:
        workspace_named(repos, workspace)
    user = repos.users.get_or_create_local()
    graph = hierarchy.load(repos, user.id)
    mastery = repos.attempts.mastery(user.id)
    open_by_point: dict[uuid.UUID, int] = {}
    for issue in repos.issues.list(user.id, statuses=[IssueStatus.OPEN]):
        if issue.point_id:
            open_by_point[issue.point_id] = open_by_point.get(issue.point_id, 0) + 1
    out = []
    for item in knowledge.point_tree(repos, workspace):
        row, point = item.row, item.row.point
        m = mastery.get(point.id)
        out.append(
            schemas.TreePointOut(
                id=point.id,
                name=point.name,
                summary=point.summary,
                importance=point.importance,
                level=row.level,
                parent_id=graph.primary(point.id),
                shortcuts=[p.id for p in row.also_under],
                path=[graph.name(p) for p in graph.path(point.id)],
                topic=_topic(row.topic),
                mastery=m.mastery if m else None,
                review_count=m.review_count if m else 0,
                last_reviewed=m.last_reviewed if m else None,
                open_issues=open_by_point.get(point.id, 0),
                sources=[_source(s) for s in item.sources],
            )  # fmt: skip
        )
    return out


@router.get("/{point_id}", response_model=schemas.PointDetailOut)
def get_point(point_id: uuid.UUID, repos: Repos, settings: Config):
    return _detail(repos, settings, point_id)


@router.patch("/{point_id}", response_model=schemas.PointDetailOut)
def update_point(point_id: uuid.UUID, body: schemas.PointPatchIn, repos: Repos, settings: Config):
    """Change importance, the primary parent (`parent_id`, null = top level) and the shortcuts.
    A change that breaks a rule (a loop, more than 4 levels, more than 2 shortcuts) is refused
    with 409 and nothing changes."""
    point = _get(repos, point_id)
    fields = body.model_fields_set
    if "parent_id" in fields or body.shortcuts is not None:
        user = repos.users.get_or_create_local()
        graph = hierarchy.load(repos, user.id)
        for other in [body.parent_id, *(body.shortcuts or [])]:
            if other is not None and other not in graph.points:
                raise knowledge.NoSuchPoint(f"No knowledge point with id {other}.")
        if "parent_id" in fields:
            graph.move(point.id, body.parent_id)
        if body.shortcuts is not None:
            wanted = list(dict.fromkeys(body.shortcuts))
            for s in graph.shortcuts(point.id):
                if s not in wanted:
                    graph.remove_link(point.id, s)
            for s in wanted:
                if s not in graph.shortcuts(point.id):
                    graph.add_shortcut(point.id, s)
        hierarchy.save(repos, user.id, graph)
    if body.importance is not None:
        knowledge.set_importance(repos, str(point_id), body.importance)
    return _detail(repos, settings, point_id)


@router.post("/{point_id}/merge", response_model=schemas.MergeOut)
def merge(point_id: uuid.UUID, body: schemas.MergeIn, repos: Repos):
    result = knowledge.merge_points(repos, str(point_id), str(body.into))
    return schemas.MergeOut(kept=_one(repos, result.into.id), removed=result.merged.id)
