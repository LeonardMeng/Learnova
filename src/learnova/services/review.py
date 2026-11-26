"""Gap review: for each changed note, one model call extracts knowledge points, flags problems and
says which of the note's open issues are fixed; a second call (only when there are new points)
checks the new points against all of the user's existing ones. Notes are never edited; results go
to the database (points, issues) and the report.

The two model calls are passed in as functions, so tests can replace them with fakes."""

from __future__ import annotations

import difflib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from learnova.anchors import DEFAULT_THRESHOLD, find_block
from learnova.llm import factory, prompts
from learnova.models import (
    IssueKind,
    IssueStatus,
    KnowledgePoint,
    Note,
    PointSource,
    ReviewIssue,
    Workspace,
)
from learnova.services import hierarchy
from learnova.services.issues import fingerprint, is_same_issue
from learnova.storage.database import Repositories
from learnova.sync import CACHE_DIR, cache_filename

MAX_CANDIDATES = 500  # existing points sent to the duplicate check
MIN_CONTENT_CHARS = 40  # notes shorter than this (after removing sub-page links) are skipped


# --- what the model returns ----------------------------------------------------------------


class ExistingPoint(BaseModel):
    ref: str  # "P1"
    status: Literal["kept", "updated", "removed"]
    name: str | None = None  # only for "updated"
    summary: str | None = None  # only for "updated"


class NewPoint(BaseModel):
    name: str
    summary: str
    importance: int  # 1-5
    parent: str | None = None  # T-, P- or N-label of the point it is a kind or part of


class Issue(BaseModel):
    kind: Literal["factual_error", "unclear"]
    quote: str
    problem: str
    point: str | None = None  # label of the point it's about: "P2", "N1", or None


class Missing(BaseModel):
    topic: str
    why: str
    point: str | None = None


class IssueCheck(BaseModel):
    ref: str  # "I1"
    status: Literal["still_present", "fixed"]


class NoteReview(BaseModel):
    existing: list[ExistingPoint]
    new_points: list[NewPoint]
    issue_status: list[IssueCheck] = []  # one per open issue (I-label)
    issues: list[Issue]
    missing: list[Missing]


class DuplicateMatch(BaseModel):
    new_ref: str  # "N1"
    match: str | None  # "C12", or None


class DuplicateCheck(BaseModel):
    matches: list[DuplicateMatch]


# --- what the service passes to the model calls ----------------------------------------------


@dataclass(frozen=True)
class ReviewRequest:
    note: Note
    markdown: str
    other_notes: list[str]
    existing: dict[str, KnowledgePoint]  # "P1" -> point
    open_issues: dict[str, ReviewIssue] = field(default_factory=dict)  # "I1" -> issue
    dismissed: dict[str, ReviewIssue] = field(default_factory=dict)  # "D1": don't raise again
    topics: dict[str, KnowledgePoint] = field(default_factory=dict)  # "T1": possible parents


@dataclass(frozen=True)
class Candidate:
    point: KnowledgePoint
    sources: list[PointSource]


@dataclass(frozen=True)
class DuplicateRequest:
    new_points: dict[str, NewPoint]  # "N1" -> point
    candidates: dict[str, Candidate]  # "C1" -> existing point


Reviewer = Callable[[ReviewRequest], NoteReview]
Deduper = Callable[[DuplicateRequest], DuplicateCheck]


# --- results ---------------------------------------------------------------------------------


class Status(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    KEPT = "kept"
    REMOVED = "removed"


@dataclass(frozen=True)
class PointOutcome:
    status: Status
    point: KnowledgePoint
    previous: KnowledgePoint | None = None  # before an update
    other_sources: int = 0  # other notes this point also comes from
    possible_duplicate: Candidate | None = None
    note: str = ""  # e.g. "not mentioned by the model"


@dataclass
class NoteResult:
    note: Note
    outcomes: list[PointOutcome] = field(default_factory=list)
    new_issues: list[ReviewIssue] = field(default_factory=list)
    fixed_issues: list[ReviewIssue] = field(default_factory=list)
    still_open: list[ReviewIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues_kept: bool = (
        False  # note unchanged since its issues were last reviewed: left as they were
    )


@dataclass
class ReviewRun:
    workspace: Workspace
    reviewed: list[NoteResult] = field(default_factory=list)
    skipped: list[Note] = field(default_factory=list)  # no real content
    failed: list[tuple[Note, str]] = field(default_factory=list)


# --- the service -----------------------------------------------------------------------------


def notes_to_review(repos: Repositories, workspace: Workspace, everything: bool) -> list[Note]:
    notes = [n for n in repos.notes.list(workspace.id) if not n.ignored]
    todo = [n for n in notes if everything or n.reviewed_hash != n.content_hash]
    return sorted(todo, key=lambda n: n.path)


def review_notes(
    workspace: Workspace,
    repos: Repositories,
    notes: list[Note],
    reviewer: Reviewer,
    deduper: Deduper,
    on_note: Callable[[Note, str], None] | None = None,
    anchor_threshold: float = DEFAULT_THRESHOLD,
) -> ReviewRun:
    run = ReviewRun(workspace)
    user = repos.users.get_or_create_local()
    visible = [n for n in repos.notes.list(workspace.id) if not n.ignored]
    titles = [" / ".join(n.path) for n in sorted(visible, key=lambda n: n.path)]
    for note in notes:
        markdown = read_note(workspace, note)
        if len(_meaningful(markdown)) < MIN_CONTENT_CHARS:
            repos.notes.mark_reviewed(note.id, note.content_hash)
            run.skipped.append(note)
            _report(on_note, note, "skipped (no content)")
            continue
        try:
            result = _review_one(
                repos, user.id, note, markdown, titles, reviewer, deduper, anchor_threshold
            )
        except factory.LLMCallError as e:
            run.failed.append((note, str(e)))
            _report(on_note, note, "failed")
            continue
        repos.notes.mark_reviewed(note.id, note.content_hash)
        run.reviewed.append(result)
        _report(on_note, note, "reviewed")
    return run


def _review_one(
    repos, user_id, note, markdown, titles, reviewer, deduper, anchor_threshold
) -> NoteResult:
    existing = {f"P{i}": p for i, p in enumerate(repos.knowledge.list_for_note(note.id), 1)}
    others = [t for t in titles if t != " / ".join(note.path)]
    open_issues = {
        f"I{i}": x for i, x in enumerate(repos.issues.list_for_note(note.id, [IssueStatus.OPEN]), 1)
    }
    dismissed = {
        f"D{i}": x
        for i, x in enumerate(repos.issues.list_for_note(note.id, [IssueStatus.DISMISSED]), 1)
    }
    graph = hierarchy.load(repos, user_id)
    topics = {f"T{i}": p for i, p in enumerate(_topics(graph), 1)}
    review = reviewer(
        ReviewRequest(note, markdown, others, existing, open_issues, dismissed, topics)
    )
    result = NoteResult(note)

    # 1. Decide what happens to each existing point. Anything not mentioned is kept.
    decisions: dict[str, ExistingPoint] = {}
    for item in review.existing:
        if item.ref in existing and item.ref not in decisions:
            decisions[item.ref] = item
    unmentioned = [ref for ref in existing if ref not in decisions]

    # 2. Check genuinely new points against everything the user already has.
    new = {f"N{i}": p for i, p in enumerate(review.new_points, 1)}
    matches: dict[str, Candidate] = {}
    if new:
        candidates = _candidates(repos, user_id, new)
        try:
            check = deduper(DuplicateRequest(new, candidates))
            matches = {
                m.new_ref: candidates[m.match] for m in check.matches if m.match in candidates
            }
        except factory.LLMCallError as e:
            result.warnings.append(f"Duplicate check failed, new points were not checked: {e}")

    # A "new" point that is really one of this note's own points: keep that point instead.
    own_ids = {p.id: ref for ref, p in existing.items()}
    revived = set()
    for new_ref, cand in list(matches.items()):
        if cand.point.id in own_ids:
            revived.add(own_ids[cand.point.id])
            del new[new_ref]

    sources_by_point = _sources_by_point(repos, user_id)

    def other_sources(point: KnowledgePoint) -> int:
        return sum(1 for s in sources_by_point.get(point.id, []) if s.note_id != note.id)

    def keep(point: KnowledgePoint, why: str = "") -> PointOutcome:
        return PointOutcome(Status.KEPT, point, note=why, other_sources=other_sources(point))

    for ref, point in existing.items():
        item = decisions.get(ref)
        if ref in revived:
            result.outcomes.append(keep(point))
        elif item is None:
            result.outcomes.append(keep(point, "not mentioned by the model"))
        elif item.status == "updated" and item.name and item.summary:
            updated = repos.knowledge.update(point.id, item.name.strip(), item.summary.strip())
            result.outcomes.append(
                PointOutcome(Status.UPDATED, updated, point, other_sources(point))
            )
        elif item.status == "removed":
            repos.knowledge.unlink(point.id, note.id)
            result.outcomes.append(
                PointOutcome(Status.REMOVED, point, other_sources=other_sources(point))
            )
        else:  # kept, or "updated" without new text
            result.outcomes.append(keep(point))
    unmentioned = [ref for ref in unmentioned if ref not in revived]
    if unmentioned:
        result.warnings.append(f"{len(unmentioned)} existing point(s) not mentioned; kept.")

    # Points the issues can refer to: P-labels, and N-labels of new (or revived) points.
    by_label: dict[str, KnowledgePoint] = dict(existing)
    for new_ref, cand in matches.items():
        if cand.point.id in own_ids:
            by_label[new_ref] = cand.point
    for new_ref, item in new.items():
        point = repos.knowledge.create(
            user_id, item.name.strip(), item.summary.strip(), max(1, min(5, item.importance))
        )
        repos.knowledge.link(point.id, note.id)
        by_label[new_ref] = point
        result.outcomes.append(
            PointOutcome(Status.NEW, point, possible_duplicate=matches.get(new_ref))
        )

    _place_new_points(repos, user_id, new, by_label, topics, result)

    blocks = repos.notes.blocks(note.id)
    if note.issues_hash is not None and note.issues_hash == note.content_hash:
        # The text hasn't changed since its issues were last reviewed, so whatever the model says
        # about issues now would only be noise: keep them as they are, just re-link their blocks.
        result.issues_kept = True
        result.still_open = list(open_issues.values())
        _reanchor(repos, result.still_open, blocks, anchor_threshold)
    else:
        _update_issues(repos, user_id, note, review, open_issues, dismissed, by_label, result,
                       blocks, anchor_threshold)  # fmt: skip
        repos.notes.mark_issues_reviewed(note.id, note.content_hash)
    return result


MAX_TOPICS = 100  # topics offered as parents in one review


def _topics(graph: hierarchy.Graph) -> list[KnowledgePoint]:
    topics = sorted(graph.topics(), key=lambda p: (graph.depth(p.id), p.name.lower()))
    return topics[:MAX_TOPICS]


def _place_new_points(repos, user_id, new, by_label, topics, result) -> None:
    """Put each new point under the parent the model chose, if that is allowed."""
    wanted = [
        (by_label[ref], (item.parent or "").strip())
        for ref, item in new.items()
        if (item.parent or "").strip()
    ]
    if not wanted:
        return
    graph = hierarchy.load(repos, user_id)
    labels = {**by_label, **topics}
    changed = False
    for point, label in wanted:
        parent = labels.get(label)
        if parent is None:
            result.warnings.append(f"Unknown parent {label!r} for {point.name!r}; left at the top.")
            continue
        try:
            graph.move(point.id, parent.id)
            changed = True
        except hierarchy.HierarchyError as e:
            result.warnings.append(f"{point.name!r} not put under {parent.name!r}: {e}")
    if changed:
        hierarchy.save(repos, user_id, graph)


def _reanchor(repos, issues, blocks, threshold) -> None:
    """Link each open issue with a quote to the block it comes from, if that link is missing or
    points to a block that no longer exists."""
    block_ids = {b.block_id for b in blocks}
    for issue in issues:
        if issue.quote and issue.block_id not in block_ids:  # older issue, or block replaced
            block = find_block(issue.quote, blocks, threshold)
            if block != issue.block_id:
                repos.issues.set_block(issue.id, block)


def _update_issues(
    repos, user_id, note, review, open_issues, dismissed, by_label, result, blocks, threshold
) -> None:
    """Close the issues the model saw fixed, keep the rest open, and add genuinely new ones.
    Every open issue with a quote is linked to the block it comes from, when one matches."""
    now = datetime.now(UTC)
    verdicts: dict[str, str] = {}
    for check in review.issue_status:
        if check.ref in open_issues and check.ref not in verdicts:
            verdicts[check.ref] = check.status
    still = []
    for ref, issue in open_issues.items():
        if verdicts.get(ref) == "fixed":
            result.fixed_issues.append(
                repos.issues.set_status(issue.id, IssueStatus.FIXED, now, "review")
            )
        else:  # still present, or not mentioned: an issue is only closed on a clear "fixed"
            still.append(issue)
    _reanchor(repos, still, blocks, threshold)
    unmentioned = sum(1 for ref in open_issues if ref not in verdicts)
    if unmentioned:
        result.warnings.append(f"{unmentioned} open issue(s) not mentioned; kept open.")

    # Never raise an issue that is already open or that you dismissed, even reworded (the model
    # is told so; this is the safety net). Issues added earlier in this run count too.
    known = [*still, *dismissed.values()]
    reseen = {x.id for x in still}
    found = [
        (IssueKind(i.kind), i.quote.strip(), "", i.problem.strip(), i.point) for i in review.issues
    ] + [(IssueKind.MISSING, "", m.topic.strip(), m.why.strip(), m.point) for m in review.missing]
    for kind, quote, topic, problem, label in found:
        if not (quote or topic) or not problem:
            continue
        fp = fingerprint(kind, quote, topic)
        block = find_block(quote, blocks, threshold) if quote else None
        same = next((x for x in known if x.fingerprint == fp), None) or next(
            (x for x in known if is_same_issue(kind, quote, topic, block, x)), None
        )
        if same is not None:
            if same.status == IssueStatus.OPEN:
                reseen.add(same.id)
            continue
        point = by_label.get(label or "")
        issue = repos.issues.create(
            user_id, note.workspace_id, note.id, note.path, kind, quote, topic, problem,
            point.id if point else None, fp, now, block,
        )  # fmt: skip
        known.append(issue)
        result.new_issues.append(issue)
    repos.issues.seen(list(reseen), now)
    result.still_open = still


def _candidates(repos, user_id, new: dict[str, NewPoint]) -> dict[str, Candidate]:
    points = repos.knowledge.list_for_user(user_id)
    if len(points) > MAX_CANDIDATES:
        names = [n.name.lower() for n in new.values()]

        def closeness(p: KnowledgePoint) -> float:
            return max(difflib.SequenceMatcher(None, p.name.lower(), n).ratio() for n in names)

        points = sorted(points, key=closeness, reverse=True)[:MAX_CANDIDATES]
    sources = _sources_by_point(repos, user_id)
    return {f"C{i}": Candidate(p, sources.get(p.id, [])) for i, p in enumerate(points, 1)}


def _sources_by_point(repos, user_id) -> dict[uuid.UUID, list[PointSource]]:
    out: dict[uuid.UUID, list[PointSource]] = {}
    for s in repos.knowledge.sources(user_id):
        out.setdefault(s.point_id, []).append(s)
    return out


def read_note(workspace: Workspace, note: Note) -> str:
    """The cache file without its header: the note's Markdown, image descriptions included."""
    path = workspace.path / CACHE_DIR / cache_filename(note.title, note.source_id)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        text = parts[1] if len(parts) == 2 else text
    return text.strip()


def _meaningful(markdown: str) -> str:
    lines = [
        line
        for line in markdown.splitlines()
        if line.strip() and not line.startswith("→ ") and not line.startswith("<!--")
    ]
    return "\n".join(lines).strip()


def _report(on_note, note, what) -> None:
    if on_note:
        on_note(note, what)


# --- the real model calls --------------------------------------------------------------------


def llm_reviewer(model) -> Reviewer:
    def review(req: ReviewRequest) -> NoteReview:
        existing = "\n".join(f"{ref}  {p.name}: {p.summary}" for ref, p in req.existing.items())
        prompt = prompts.load("review").format(
            title=req.note.title,
            path=" / ".join(req.note.path),
            note=req.markdown,
            other_notes="\n".join(f"- {t}" for t in req.other_notes) or "(none)",
            existing_points=existing or "(none yet: return an empty `existing` list)",
            open_issues="\n".join(_issue_line(r, x) for r, x in req.open_issues.items())
            or "(none: return an empty `issue_status` list)",
            dismissed_issues="\n".join(_issue_line(r, x) for r, x in req.dismissed.items())
            or "(none)",
            topics="\n".join(f"{ref}  {p.name}: {p.summary[:120]}" for ref, p in req.topics.items())
            or "(none yet)",
        )
        return factory.ask_structured(model, NoteReview, prompt)

    return review


def llm_deduper(model) -> Deduper:
    def check(req: DuplicateRequest) -> DuplicateCheck:
        if not req.candidates:
            return DuplicateCheck(matches=[])
        new = "\n".join(f"{ref}  {p.name}: {p.summary}" for ref, p in req.new_points.items())
        cands = "\n".join(
            f"{ref}  {c.point.name}: {c.point.summary[:120]}{_where(c.sources)}"
            for ref, c in req.candidates.items()
        )
        prompt = prompts.load("duplicates").format(new_points=new, candidates=cands)
        return factory.ask_structured(model, DuplicateCheck, prompt)

    return check


def _issue_line(ref: str, issue: ReviewIssue) -> str:
    if issue.kind == IssueKind.MISSING:
        return f"{ref}  missing topic: {issue.topic} ({issue.problem})"
    return f'{ref}  {issue.kind.value}: "{issue.quote}" ({issue.problem})'


def where(sources: list[PointSource]) -> str:
    """'ml-notes: ML / Optimisation' for the first source, '+2 more' after that."""
    if not sources:
        return "no notes"
    first = sources[0]
    more = f" (+{len(sources) - 1} more)" if len(sources) > 1 else ""
    return f"{first.workspace_name}: {' / '.join(first.note_path)}{more}"


def _where(sources: list[PointSource]) -> str:
    return f"  [{where(sources)}]"


# --- one `review` run (used by the CLI and the API) ------------------------------------------


@dataclass
class ReviewOutcome:
    run: ReviewRun
    report: Path
    model: str
    pending_images: int  # images without a description: the review couldn't see them
    open_issues: int = 0  # open issues in the workspace after this run


def run_review(
    repos: Repositories,
    workspace: Workspace,
    everything: bool = False,
    on_start: Callable[[int, str, int], None] | None = None,
    on_note: Callable[[Note, str], None] | None = None,
) -> ReviewOutcome | None:
    """Review changed notes (or all) with the main model and write the report. None when there is
    nothing to review."""
    from learnova.config import load_settings
    from learnova.report import write_review_report
    from learnova.services import issues as issues_service
    from learnova.services.images import pending_images

    notes = notes_to_review(repos, workspace, everything)
    if not notes:
        return None
    settings = load_settings().models
    model = factory.main_model(settings)
    pending = len(pending_images(repos, workspace))
    if on_start:
        on_start(len(notes), settings.main, pending)
    full_settings = load_settings()
    run = review_notes(
        workspace, repos, notes, llm_reviewer(model), llm_deduper(model), on_note=on_note,
        anchor_threshold=full_settings.anchor_threshold,
    )  # fmt: skip
    user = repos.users.get_or_create_local()
    open_issues = issues_service.ranked(repos, full_settings.issues, workspace.id)
    path = write_review_report(
        run, settings.main, repos.knowledge.list_for_user(user.id), open_issues
    )
    return ReviewOutcome(run, path, settings.main, pending, len(open_issues))
