"""Managing knowledge points by hand: listing them, finding one by name or id, changing its
importance, and merging duplicates (children and parents included, see hierarchy.py)."""

from __future__ import annotations

from dataclasses import dataclass

from learnova.config import load_settings
from learnova.errors import LearnovaError, NotFoundError
from learnova.models import KnowledgePoint, PointSource
from learnova.scheduler.mastery import replay
from learnova.services import hierarchy
from learnova.services.review import where
from learnova.storage.database import Repositories


class KnowledgeError(LearnovaError):
    pass


class NoSuchPoint(KnowledgeError, NotFoundError):
    pass


@dataclass(frozen=True)
class MergeResult:
    merged: KnowledgePoint  # the point that no longer exists
    into: KnowledgePoint
    source_count: int  # notes the kept point now comes from


@dataclass(frozen=True)
class PointListing:
    point: KnowledgePoint
    sources: list[PointSource]


def list_points(repos: Repositories, workspace_name: str | None = None) -> list[PointListing]:
    """All your points, most important first; only those from one workspace if it is named.
    Points no note uses any more are included (they keep their mastery)."""
    user = repos.users.get_or_create_local()
    by_point: dict = {}
    for s in repos.knowledge.sources(user.id):
        by_point.setdefault(s.point_id, []).append(s)
    listings = [
        PointListing(
            p, sorted(by_point.get(p.id, []), key=lambda s: (s.workspace_name, s.note_path))
        )
        for p in repos.knowledge.list_for_user(user.id)
    ]
    if workspace_name is not None:
        listings = [
            item
            for item in listings
            if any(s.workspace_name == workspace_name for s in item.sources)
        ]
    return sorted(listings, key=lambda item: (-item.point.importance, item.point.name.lower()))


@dataclass(frozen=True)
class TreeListing:
    row: hierarchy.TreeRow
    sources: list[PointSource]


def point_tree(repos: Repositories, workspace_name: str | None = None) -> list[TreeListing]:
    """Your points as a tree (see hierarchy.tree); with a workspace, its points and the topics
    above them."""
    listings = list_points(repos, workspace_name)
    user = repos.users.get_or_create_local()
    graph = hierarchy.load(repos, user.id)
    only = {item.point.id for item in listings} if workspace_name is not None else None
    sources = {item.point.id: item.sources for item in list_points(repos)}
    rows = hierarchy.tree(graph, repos.attempts.mastery(user.id), only)
    return [TreeListing(row, sources.get(row.point.id, [])) for row in rows]


def set_importance(repos: Repositories, text: str, importance: int) -> KnowledgePoint:
    if not 1 <= importance <= 5:
        raise KnowledgeError("Importance must be from 1 to 5.")
    point = find_point(repos, text)
    return repos.knowledge.set_importance(point.id, importance)


def find_point(repos: Repositories, text: str) -> KnowledgePoint:
    """By exact name (any case) or by id / id prefix of at least 8 characters."""
    user = repos.users.get_or_create_local()
    points = repos.knowledge.list_for_user(user.id)
    needle = text.strip().lower()
    matches = [p for p in points if len(needle) >= 8 and str(p.id).startswith(needle)]
    matches = matches or [p for p in points if p.name.lower() == needle]
    if not matches:
        raise NoSuchPoint(f"No knowledge point named or with id {text!r}.")
    if len(matches) > 1:
        sources = {}
        for s in repos.knowledge.sources(user.id):
            sources.setdefault(s.point_id, []).append(s)
        lines = "\n".join(
            f"  {str(p.id)[:8]}  {p.name}  ({where(sources.get(p.id, []))})" for p in matches
        )
        raise KnowledgeError(f"{len(matches)} points match {text!r}; use an id instead:\n{lines}")
    return matches[0]


def merge_points(repos: Repositories, point_text: str, into_text: str) -> MergeResult:
    point, into = find_point(repos, point_text), find_point(repos, into_text)
    if point.id == into.id:
        raise KnowledgeError("That's the same knowledge point.")
    config = load_settings().mastery
    user = repos.users.get_or_create_local()
    links = hierarchy.merged_links(hierarchy.load(repos, user.id), point.id, into.id)
    kept = repos.knowledge.merge(
        point.id, into.id, lambda attempts: replay(attempts, config), links
    )
    count = sum(1 for s in repos.knowledge.sources(user.id) if s.point_id == kept.id)
    return MergeResult(merged=point, into=kept, source_count=count)
