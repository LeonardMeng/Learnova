"""Figures for `learnova status`: mastery overall and per workspace, and the weakest points."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from learnova.models import KnowledgePoint, Mastery, PointSource
from learnova.scheduler import weights as weights_mod
from learnova.storage.database import Repositories


@dataclass(frozen=True)
class Summary:
    name: str  # "All" or a workspace name
    points: int
    quizzed: int
    average_mastery: float  # never-quizzed points count as 0
    last_quiz: datetime | None


@dataclass(frozen=True)
class PointStatus:
    point: KnowledgePoint
    mastery: Mastery | None
    weight: float
    sources: list[PointSource]


@dataclass(frozen=True)
class Status:
    overall: Summary
    workspaces: list[Summary]
    weakest: list[PointStatus]


def status(
    repos: Repositories,
    now: datetime,
    config: weights_mod.WeightConfig,
    workspace_name: str | None = None,
    weakest: int = 10,
) -> Status:
    user = repos.users.get_or_create_local()
    mastery = repos.attempts.mastery(user.id)
    by_point: dict = {}
    for s in repos.knowledge.sources(user.id):
        by_point.setdefault(s.point_id, []).append(s)

    rows = []
    for point in repos.knowledge.list_for_user(user.id):
        m = mastery.get(point.id)
        state = weights_mod.PointState(
            point.importance, m.mastery if m else 0.0, m.last_reviewed if m else None
        )
        rows.append(
            PointStatus(
                point, m, weights_mod.weight(state, now, config), by_point.get(point.id, [])
            )
        )

    def summary(name: str, items: list[PointStatus]) -> Summary:
        quizzed = [i.mastery for i in items if i.mastery]
        last = max((m.last_reviewed for m in quizzed if m.last_reviewed), default=None)
        avg = sum(m.mastery for m in quizzed) / len(items) if items else 0.0
        return Summary(name, len(items), len(quizzed), avg, last)

    names = sorted({w.name for w in repos.workspaces.list(user.id)})
    per_ws = {n: [r for r in rows if any(s.workspace_name == n for s in r.sources)] for n in names}
    if workspace_name is not None:
        rows = per_ws.get(workspace_name, [])
        names = [workspace_name] if workspace_name in per_ws else []

    ranked = sorted(rows, key=lambda r: (r.mastery.mastery if r.mastery else 0.0, -r.weight))
    return Status(
        overall=summary(workspace_name or "All", rows),
        workspaces=[summary(n, per_ws[n]) for n in names],
        weakest=ranked[:weakest],
    )
