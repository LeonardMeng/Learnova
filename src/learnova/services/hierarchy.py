"""The knowledge hierarchy: points under other points.

Every point has at most one primary parent (where it lives; none = top level) and up to
MAX_SHORTCUTS secondary parents ("also under"). There are no loops, and the primary tree is at
most MAX_DEPTH levels deep. A point with children is a topic: it is not quizzed, and its mastery
is worked out from the quizzed points under it. Every change goes through a `Graph`, which checks
the rules before anything is written."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from learnova.errors import ConflictError
from learnova.models import KnowledgePoint, Mastery, ParentLink
from learnova.storage.database import Repositories

MAX_DEPTH = 4
MAX_SHORTCUTS = 2


class HierarchyError(ConflictError):
    """A change that would break a rule (the API answers 409)."""


@dataclass
class Graph:
    """The user's points and the links between them. Changing methods check the rules and
    raise HierarchyError, leaving the graph as it was."""

    points: dict[uuid.UUID, KnowledgePoint]
    links: dict[tuple[uuid.UUID, uuid.UUID], bool] = field(default_factory=dict)  # → primary

    # --- reading ------------------------------------------------------------------------------

    def name(self, point_id: uuid.UUID) -> str:
        return self.points[point_id].name

    def primary(self, point_id: uuid.UUID) -> uuid.UUID | None:
        return next((p for (c, p), prim in self.links.items() if c == point_id and prim), None)

    def shortcuts(self, point_id: uuid.UUID) -> list[uuid.UUID]:
        found = [p for (c, p), prim in self.links.items() if c == point_id and not prim]
        return sorted(found, key=lambda p: self.name(p).lower())

    def children(self, point_id: uuid.UUID, primary_only: bool = False) -> list[uuid.UUID]:
        kids = [c for (c, p), prim in self.links.items() if p == point_id and (prim or not
                primary_only)]  # fmt: skip
        return sorted(kids, key=lambda c: (-self.points[c].importance, self.name(c).lower()))

    def is_topic(self, point_id: uuid.UUID) -> bool:
        return any(p == point_id for (_, p) in self.links)

    def topics(self) -> list[KnowledgePoint]:
        return [p for p in self.points.values() if self.is_topic(p.id)]

    def descendants(self, point_id: uuid.UUID) -> list[uuid.UUID]:
        """Everything under the point, shortcuts included, each once (never the point itself)."""
        kids: dict[uuid.UUID, list[uuid.UUID]] = {}
        for c, p in self.links:
            kids.setdefault(p, []).append(c)
        seen: dict[uuid.UUID, None] = {}
        todo = list(kids.get(point_id, []))
        while todo:
            c = todo.pop()
            if c not in seen:
                seen[c] = None
                todo += kids.get(c, [])
        seen.pop(point_id, None)
        return list(seen)

    def _has_loop(self, point_id: uuid.UUID) -> bool:
        return any(point_id in self.descendants(c) or c == point_id for c in
                   self.children(point_id))  # fmt: skip

    def path(self, point_id: uuid.UUID) -> list[uuid.UUID]:
        """The primary parents from the top level down to the point itself."""
        out = [point_id]
        while (parent := self.primary(out[0])) is not None and parent not in out:
            out.insert(0, parent)
        return out

    def depth(self, point_id: uuid.UUID) -> int:
        return len(self.path(point_id))

    def link_list(self) -> list[ParentLink]:
        return [ParentLink(child_id=c, parent_id=p, primary=prim) for (c, p), prim in
                self.links.items()]  # fmt: skip

    def copy(self) -> Graph:
        return Graph(self.points, dict(self.links))

    # --- changing -----------------------------------------------------------------------------

    def move(self, child: uuid.UUID, parent: uuid.UUID | None) -> None:
        """Make `parent` the primary parent of `child` (None: top level). A shortcut to the same
        parent becomes the primary link."""
        new = self.copy()
        old = new.primary(child)
        if old is not None:
            del new.links[(child, old)]
        if parent is not None:
            new._check_new_link(child, parent)
            new.links.pop((child, parent), None)
            new.links[(child, parent)] = True
            new._check_depth(child)
        self.links = new.links

    def add_shortcut(self, child: uuid.UUID, parent: uuid.UUID) -> None:
        names = f"{self.name(child)!r}", f"{self.name(parent)!r}"
        if self.primary(child) == parent:
            raise HierarchyError(f"{names[0]} already lives under {names[1]}.")
        if (child, parent) in self.links:
            raise HierarchyError(f"{names[0]} is already also under {names[1]}.")
        shortcuts = self.shortcuts(child)
        if len(shortcuts) >= MAX_SHORTCUTS:
            listed = ", ".join(repr(self.name(s)) for s in shortcuts)
            raise HierarchyError(
                f"{self.name(child)!r} already has {MAX_SHORTCUTS} shortcuts ({listed}). "
                "Remove one first with `learnova kp unlink`."
            )
        self._check_new_link(child, parent)
        self.links[(child, parent)] = False

    def remove_link(self, child: uuid.UUID, parent: uuid.UUID) -> bool:
        """Returns whether it was the primary link (the point is now at the top level)."""
        if (child, parent) not in self.links:
            raise HierarchyError(f"{self.name(child)!r} isn't under {self.name(parent)!r}.")
        return self.links.pop((child, parent))

    def check(self) -> None:
        """The whole graph: no loops, no point deeper than MAX_DEPTH, the link limits."""
        for pid in self.points:
            if self._has_loop(pid):
                raise HierarchyError(f"{self.name(pid)!r} would end up under itself.")
        for pid in self.points:
            prim = [p for (c, p), x in self.links.items() if c == pid and x]
            if len(prim) > 1:
                raise HierarchyError(f"{self.name(pid)!r} would have two primary parents.")
            if len(self.shortcuts(pid)) > MAX_SHORTCUTS:
                raise HierarchyError(
                    f"{self.name(pid)!r} would have more than {MAX_SHORTCUTS} shortcuts."
                )
            if self.depth(pid) > MAX_DEPTH:
                raise HierarchyError(self._too_deep(self.path(pid)))

    def _check_new_link(self, child: uuid.UUID, parent: uuid.UUID) -> None:
        if child == parent:
            raise HierarchyError(f"{self.name(child)!r} can't be under itself.")
        if parent in self.descendants(child):
            chain = " > ".join(self.name(p) for p in self._chain_down(child, parent))
            raise HierarchyError(
                f"{self.name(parent)!r} is already under {self.name(child)!r} ({chain}): "
                "that would make a loop."
            )

    def _check_depth(self, child: uuid.UUID) -> None:
        """After `child` moved: its deepest primary descendant must stay within MAX_DEPTH."""
        deepest = self._deepest(child)
        if self.depth(deepest) > MAX_DEPTH:
            raise HierarchyError(self._too_deep(self.path(deepest)))

    def _deepest(self, point_id: uuid.UUID) -> uuid.UUID:
        best = point_id
        for c in self.children(point_id, primary_only=True):
            d = self._deepest(c)
            if self.depth(d) > self.depth(best):
                best = d
        return best

    def _too_deep(self, path: list[uuid.UUID]) -> str:
        chain = " > ".join(self.name(p) for p in path)
        return f"That would put {self.name(path[-1])!r} {len(path)} levels deep ({chain}); " \
               f"at most {MAX_DEPTH} levels are allowed."  # fmt: skip

    def _chain_down(self, top: uuid.UUID, bottom: uuid.UUID) -> list[uuid.UUID]:
        """A path of links from `top` down to `bottom` (which is under it)."""
        todo, came = [top], {top: top}
        while todo:
            node = todo.pop(0)
            if node == bottom:
                break
            for c in self.children(node):
                if c not in came:
                    came[c] = node
                    todo.append(c)
        out = [bottom]
        while out[0] != top:
            out.insert(0, came[out[0]])
        return out


def load(repos: Repositories, user_id: uuid.UUID) -> Graph:
    points = {p.id: p for p in repos.knowledge.list_for_user(user_id)}
    links = {(x.child_id, x.parent_id): x.primary for x in repos.knowledge.parents(user_id)}
    return Graph(points, links)


def save(repos: Repositories, user_id: uuid.UUID, graph: Graph,
         new_points: Iterable[KnowledgePoint] = ()) -> None:  # fmt: skip
    repos.knowledge.restructure(user_id, list(new_points), graph.link_list())


# --- changes by hand -------------------------------------------------------------------------


def move(repos: Repositories, point: KnowledgePoint, parent: KnowledgePoint | None) -> Graph:
    user = repos.users.get_or_create_local()
    graph = load(repos, user.id)
    graph.move(point.id, parent.id if parent else None)
    save(repos, user.id, graph)
    return graph


def add_shortcut(repos: Repositories, point: KnowledgePoint, parent: KnowledgePoint) -> Graph:
    user = repos.users.get_or_create_local()
    graph = load(repos, user.id)
    graph.add_shortcut(point.id, parent.id)
    save(repos, user.id, graph)
    return graph


def remove_link(repos: Repositories, point: KnowledgePoint, parent: KnowledgePoint) -> bool:
    """Returns whether that was the point's primary parent (so it is now at the top level)."""
    user = repos.users.get_or_create_local()
    graph = load(repos, user.id)
    was_primary = graph.remove_link(point.id, parent.id)
    save(repos, user.id, graph)
    return was_primary


# --- merging ---------------------------------------------------------------------------------


def merged_links(graph: Graph, point_id: uuid.UUID, into_id: uuid.UUID) -> list[ParentLink]:
    """The links after merging `point_id` into `into_id`: its children move to the kept point,
    the kept point keeps its primary parent (or takes the merged point's if it has none), and
    shortcuts are combined up to the limit. Raises HierarchyError if the result breaks a rule."""
    new = graph.copy()
    old = {k: v for k, v in new.links.items() if point_id in k}
    for key in old:
        del new.links[key]
    # 1. The merged point's children move to the kept point (a primary link stays primary).
    for (child, parent), primary in old.items():
        if parent == point_id and child != into_id:
            new.links[(child, into_id)] = primary or new.links.get((child, into_id), False)
    # 2. A parent of the kept point that is now under it would make a loop: drop that link.
    under = set(new.descendants(into_id))
    for key in [k for k in new.links if k[0] == into_id and k[1] in under]:
        del new.links[key]
    # 3. The merged point's parents: its primary one if the kept point has none, else shortcuts.
    for (child, parent), primary in sorted(old.items(), key=lambda kv: not kv[1]):
        if child != point_id or parent == into_id or parent in under:
            continue
        if (into_id, parent) in new.links:
            continue
        if primary and new.primary(into_id) is None:
            new.links[(into_id, parent)] = True
        elif len(new.shortcuts(into_id)) < MAX_SHORTCUTS:
            new.links[(into_id, parent)] = False
    points = {k: v for k, v in graph.points.items() if k != point_id}
    result = Graph(points, new.links)
    try:
        result.check()
    except HierarchyError as e:
        raise HierarchyError(f"Can't merge: {e}") from None
    return result.link_list()


# --- topics ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicMastery:
    mastery: float | None  # 0–1; None while none of its points has been quizzed
    quizzed: int
    total: int  # the points under it that are not topics themselves

    def label(self) -> str:
        if self.mastery is None:
            return f"not quizzed yet · 0 of {self.total} quizzed"
        return f"{self.mastery:.0%} · {self.quizzed} of {self.total} quizzed"


def topic_mastery(
    graph: Graph, topic_id: uuid.UUID, mastery: dict[uuid.UUID, Mastery]
) -> TopicMastery:
    """The importance-weighted average mastery of the quizzed points under the topic (shortcuts
    included, each point once). Topics under it count only through their own points."""
    points = [graph.points[p] for p in graph.descendants(topic_id) if not graph.is_topic(p)]
    quizzed = [p for p in points if p.id in mastery and mastery[p.id].review_count > 0]
    if not quizzed:
        return TopicMastery(None, 0, len(points))
    total = sum(p.importance for p in quizzed)
    value = sum(p.importance * mastery[p.id].mastery for p in quizzed) / total
    return TopicMastery(value, len(quizzed), len(points))


@dataclass(frozen=True)
class TreeRow:
    point: KnowledgePoint
    level: int  # 0 = top level
    also_under: list[KnowledgePoint]  # shortcut parents
    topic: TopicMastery | None  # set for topics


def tree(
    graph: Graph, mastery: dict[uuid.UUID, Mastery], only: set[uuid.UUID] | None = None
) -> list[TreeRow]:
    """Every point under its primary parent, depth first, most important first. With `only`,
    just those points and the topics above them."""
    keep = None
    if only is not None:
        keep = {a for p in only if p in graph.points for a in graph.path(p)}
    roots = sorted(
        (p for p in graph.points.values() if graph.primary(p.id) is None),
        key=lambda p: (-p.importance, p.name.lower()),
    )
    rows: list[TreeRow] = []

    def walk(point_id: uuid.UUID, level: int) -> None:
        if keep is not None and point_id not in keep:
            return
        topic = topic_mastery(graph, point_id, mastery) if graph.is_topic(point_id) else None
        also = [graph.points[s] for s in graph.shortcuts(point_id)]
        rows.append(TreeRow(graph.points[point_id], level, also, topic))
        for c in graph.children(point_id, primary_only=True):
            walk(c, level + 1)

    for root in roots:
        walk(root.id, 0)
    return rows
