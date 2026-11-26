"""`learnova kp organize`: the model proposes a hierarchy for your points (new topics, moves,
shortcuts), every proposed change is checked against the rules in hierarchy.py, and the result is
written as a report. Nothing changes until `apply` is called, and then all at once."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from learnova.errors import LearnovaError, NotFoundError
from learnova.llm import factory, prompts
from learnova.models import KnowledgePoint, Workspace
from learnova.services import hierarchy
from learnova.storage.database import Repositories

# --- what the model returns ------------------------------------------------------------------


class ProposedTopic(BaseModel):
    ref: str  # X1, X2, … so moves and shortcuts can use it
    name: str
    summary: str
    importance: int = 3


class ProposedMove(BaseModel):
    point: str  # K- or X-label
    parent: str | None  # K-, T- or X-label; null = top level


class ProposedShortcut(BaseModel):
    point: str
    parent: str
    action: Literal["add", "remove"]


class OrganizeReply(BaseModel):
    new_topics: list[ProposedTopic] = []
    moves: list[ProposedMove] = []
    shortcuts: list[ProposedShortcut] = []


@dataclass
class OrganizeRequest:
    points: dict[str, KnowledgePoint]  # "K1": the points to organize
    topics: dict[str, KnowledgePoint]  # "T1": topics elsewhere, usable as parents only
    under: dict[str, str]  # label -> "under K3; also under T2" (current parents)


Organizer = Callable[[OrganizeRequest], OrganizeReply]


# --- the proposal ----------------------------------------------------------------------------


@dataclass
class Change:
    kind: Literal["topic", "move", "add_shortcut", "remove_shortcut"]
    text: str  # Markdown, e.g. "**Eviction**: top level → under **Redis memory**"
    refused: str | None = None  # why it can't be applied


@dataclass
class Proposal:
    workspace: Workspace | None
    point_count: int
    changes: list[Change] = field(default_factory=list)
    graph: hierarchy.Graph | None = None  # the hierarchy after the accepted changes
    new_topics: list[KnowledgePoint] = field(default_factory=list)  # accepted ones only

    @property
    def accepted(self) -> list[Change]:
        return [c for c in self.changes if c.refused is None]


class OrganizeError(LearnovaError):
    pass


class NoSuchWorkspace(OrganizeError, NotFoundError):
    pass


def scope(repos: Repositories, workspace_name: str | None):
    """(workspace, points to organize, topics outside them). With a workspace: its points, and
    the note-less topics (made by an earlier organize) that have one of them underneath."""
    user = repos.users.get_or_create_local()
    graph = hierarchy.load(repos, user.id)
    if workspace_name is None:
        return None, list(graph.points.values()), [], graph
    workspace = next((w for w in repos.workspaces.list(user.id) if w.name == workspace_name), None)
    if workspace is None:
        raise NoSuchWorkspace(
            f"No workspace named {workspace_name!r}. See `learnova workspace list`."
        )
    sources = repos.knowledge.sources(user.id)
    with_notes = {s.point_id for s in sources}
    mine = {s.point_id for s in sources if s.workspace_name == workspace_name}
    for topic in graph.topics():
        if topic.id not in with_notes and mine & set(graph.descendants(topic.id)):
            mine.add(topic.id)
    points = [p for p in graph.points.values() if p.id in mine]
    others = [t for t in graph.topics() if t.id not in mine]
    return workspace, points, others, graph


def propose(
    repos: Repositories, organizer: Organizer, workspace_name: str | None = None
) -> Proposal:
    workspace, points, others, graph = scope(repos, workspace_name)
    proposal = Proposal(workspace, len(points))
    if not points:
        proposal.graph = graph
        return proposal
    points.sort(key=lambda p: (graph.path(p.id), p.name.lower()))  # parents near their children
    labels: dict[str, KnowledgePoint] = {f"K{i}": p for i, p in enumerate(points, 1)}
    labels |= {f"T{i}": p for i, p in enumerate(others, 1)}
    by_id = {p.id: ref for ref, p in labels.items()}

    def under(p: KnowledgePoint) -> str:
        parts = []
        if (primary := graph.primary(p.id)) is not None and primary in by_id:
            parts.append(f"under {by_id[primary]}")
        also = [by_id[s] for s in graph.shortcuts(p.id) if s in by_id]
        if also:
            parts.append("also under " + ", ".join(also))
        return "; ".join(parts) or "top level"

    request = OrganizeRequest(
        points={r: p for r, p in labels.items() if r.startswith("K")},
        topics={r: p for r, p in labels.items() if r.startswith("T")},
        under={r: under(p) for r, p in labels.items()},
    )
    reply = organizer(request)
    _check(proposal, reply, labels, graph.copy())
    return proposal


def _check(proposal: Proposal, reply: OrganizeReply, labels, graph: hierarchy.Graph) -> None:
    """Try every proposed change in order on a copy of the hierarchy; record the refusals."""
    now = datetime.now(UTC)
    user_id = next(iter(labels.values())).user_id
    movable = {r for r in labels if r.startswith("K")}
    new_topics: dict[str, KnowledgePoint] = {}
    topic_changes: dict[str, Change] = {}
    for t in reply.new_topics:
        ref, name = t.ref.strip(), t.name.strip()
        text = f"**{name}** (importance {max(1, min(5, t.importance))}): {t.summary.strip()}"
        change = Change("topic", text)
        proposal.changes.append(change)
        if not ref or ref in labels or ref in new_topics or not name:
            change.refused = "it has no usable label or name."
            continue
        point = KnowledgePoint(
            id=uuid.uuid4(), user_id=user_id, name=name, summary=t.summary.strip(),
            importance=max(1, min(5, t.importance)), created_at=now, updated_at=now,
        )  # fmt: skip
        new_topics[ref] = point
        topic_changes[ref] = change
        graph.points = {**graph.points, point.id: point}
    known = {**labels, **new_topics}
    movable |= set(new_topics)

    def bold(ref: str | None) -> str:
        return f"**{known[ref].name}**" if ref in known else f"`{ref}`"

    def unknown(*refs: str | None) -> str | None:
        missing = [r for r in refs if r is not None and r not in known]
        return f"unknown label {', '.join(missing)}." if missing else None

    for move in reply.moves:
        ref, parent = move.point.strip(), (move.parent or "").strip() or None
        target = known[parent].id if parent in known else None
        if ref in known and (parent is None or parent in known):
            if graph.primary(known[ref].id) == target:
                continue  # already there
        old = graph.primary(known[ref].id) if ref in known else None
        old_name = f"under **{graph.name(old)}**" if old else "top level"
        change = Change("move", f"{bold(ref)}: {old_name} → "
                        + (f"under {bold(parent)}" if parent else "top level"))  # fmt: skip
        proposal.changes.append(change)
        if (problem := unknown(ref, parent)) is not None:
            change.refused = problem
        elif ref not in movable:
            change.refused = "that point isn't part of this workspace."
        else:
            change.refused = _try(graph.move, known[ref].id, target)

    for s in reply.shortcuts:
        ref, parent = s.point.strip(), s.parent.strip()
        verb = "Add" if s.action == "add" else "Remove"
        change = Change(
            "add_shortcut" if s.action == "add" else "remove_shortcut",
            f"{verb}: {bold(ref)} also under {bold(parent)}",
        )
        proposal.changes.append(change)
        if (problem := unknown(ref, parent)) is not None:
            change.refused = problem
        elif ref not in movable:
            change.refused = "that point isn't part of this workspace."
        elif s.action == "add":
            change.refused = _try(graph.add_shortcut, known[ref].id, known[parent].id)
        elif (known[ref].id, known[parent].id) not in graph.links or graph.primary(
            known[ref].id
        ) == known[parent].id:
            change.refused = "it isn't a shortcut."
        else:
            change.refused = _try(graph.remove_link, known[ref].id, known[parent].id)

    # A new topic nothing was put under is not worth creating.
    for ref, point in new_topics.items():
        if not graph.is_topic(point.id):
            topic_changes[ref].refused = "no point was put under it."
            for key in [k for k in graph.links if k[0] == point.id]:
                del graph.links[key]
            graph.points = {k: v for k, v in graph.points.items() if k != point.id}
        else:
            proposal.new_topics.append(point)
    proposal.graph = graph


def _try(change: Callable[..., object], *args) -> str | None:
    try:
        change(*args)
    except hierarchy.HierarchyError as e:
        return str(e)
    return None


def apply(repos: Repositories, proposal: Proposal, report: Path | None = None) -> None:
    """All accepted changes in one transaction; the report then says they were applied."""
    user = repos.users.get_or_create_local()
    hierarchy.save(repos, user.id, proposal.graph, proposal.new_topics)
    if report is not None:
        now = datetime.now().astimezone()
        with report.open("a", encoding="utf-8") as f:
            f.write(f"\n**Applied** {now:%Y-%m-%d %H:%M}: the {len(proposal.accepted)} "
                    "change(s) not marked refused.\n")  # fmt: skip


# --- the model -------------------------------------------------------------------------------


def llm_organizer(model) -> Organizer:
    def organize(req: OrganizeRequest) -> OrganizeReply:
        def line(ref: str, p: KnowledgePoint) -> str:
            return f"{ref}  {p.name}: {p.summary[:160]}  [{req.under[ref]}]"

        prompt = prompts.load("organize").format(
            points="\n".join(line(r, p) for r, p in req.points.items()),
            topics="\n".join(line(r, p) for r, p in req.topics.items()) or "(none)",
        )
        return factory.ask_structured(model, OrganizeReply, prompt)

    return organize


def run_organize(repos: Repositories, workspace_name: str | None = None):
    """Propose with the main model and write the report. Returns (proposal, report, model)."""
    from learnova.config import load_settings

    settings = load_settings().models
    proposal = propose(repos, llm_organizer(factory.main_model(settings)), workspace_name)
    return proposal, write_report(proposal, settings.main), settings.main


# --- the report ------------------------------------------------------------------------------


def report_folder(workspace: Workspace | None) -> Path:
    from learnova.home import home_dir
    from learnova.report import REPORTS_DIR

    return (workspace.path if workspace else home_dir()) / REPORTS_DIR


def write_report(proposal: Proposal, model_label: str) -> Path:
    folder = report_folder(proposal.workspace)
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    path = folder / f"{now:%Y-%m-%d}-organize.md"
    n = 2
    while path.exists():
        path = folder / f"{now:%Y-%m-%d}-organize-{n}.md"
        n += 1
    path.write_text(render(proposal, model_label, now), encoding="utf-8")
    return path


SECTIONS = [
    ("topic", "New topics"),
    ("move", "Moves"),
    ("add_shortcut", "Shortcuts to add"),
    ("remove_shortcut", "Shortcuts to remove"),
]


def numbered(proposal: Proposal) -> list[tuple[int, Change]]:
    """The changes in report order, numbered from 1."""
    ordered = [c for kind, _ in SECTIONS for c in proposal.changes if c.kind == kind]
    return list(enumerate(ordered, 1))


def render(proposal: Proposal, model_label: str, now: datetime) -> str:
    where = f"workspace {proposal.workspace.name}" if proposal.workspace else "all points"
    refused = len(proposal.changes) - len(proposal.accepted)
    out = [
        f"# Organize: {where}",
        "",
        f"{now:%Y-%m-%d %H:%M} · model {model_label}",
        "",
        f"- {proposal.point_count} point(s) sent · {len(proposal.changes)} change(s) proposed · "
        f"{len(proposal.accepted)} can be applied · {refused} refused",
        "- Not applied when written: `learnova kp organize` applies the changes that can be "
        "applied, all together, after you confirm.",
    ]
    items = numbered(proposal)
    if not items:
        out += ["", "The model proposed no changes."]
    for kind, title in SECTIONS:
        section = [(n, c) for n, c in items if c.kind == kind]
        if not section:
            continue
        out += ["", f"## {title}", ""]
        for n, c in section:
            line = f"{n}. {c.text}"
            if c.refused:
                line = f"{n}. ~~{c.text}~~ — **refused:** {c.refused}"
            out.append(line)
    return "\n".join(out) + "\n"
