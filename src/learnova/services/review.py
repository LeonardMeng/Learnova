"""Gap review: for each changed note, one model call extracts knowledge points and flags
problems, and a second call (only when there are new points) checks the new points against all of
the user's existing ones. Notes are never edited; results go to the database and the report.

The two model calls are passed in as functions, so tests can replace them with fakes."""

from __future__ import annotations

import difflib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from learnova.llm import factory, prompts
from learnova.models import KnowledgePoint, Note, PointSource, Workspace
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


class Issue(BaseModel):
    kind: Literal["factual_error", "unclear"]
    quote: str
    problem: str


class Missing(BaseModel):
    topic: str
    why: str


class NoteReview(BaseModel):
    existing: list[ExistingPoint]
    new_points: list[NewPoint]
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
    issues: list[Issue] = field(default_factory=list)
    missing: list[Missing] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
            result = _review_one(repos, user.id, note, markdown, titles, reviewer, deduper)
        except factory.LLMCallError as e:
            run.failed.append((note, str(e)))
            _report(on_note, note, "failed")
            continue
        repos.notes.mark_reviewed(note.id, note.content_hash)
        run.reviewed.append(result)
        _report(on_note, note, "reviewed")
    return run


def _review_one(repos, user_id, note, markdown, titles, reviewer, deduper) -> NoteResult:
    existing = {f"P{i}": p for i, p in enumerate(repos.knowledge.list_for_note(note.id), 1)}
    others = [t for t in titles if t != " / ".join(note.path)]
    review = reviewer(ReviewRequest(note, markdown, others, existing))
    result = NoteResult(note, issues=review.issues, missing=review.missing)

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

    for new_ref, item in new.items():
        point = repos.knowledge.create(
            user_id, item.name.strip(), item.summary.strip(), max(1, min(5, item.importance))
        )
        repos.knowledge.link(point.id, note.id)
        result.outcomes.append(
            PointOutcome(Status.NEW, point, possible_duplicate=matches.get(new_ref))
        )
    return result


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
    from learnova.services.images import pending_images

    notes = notes_to_review(repos, workspace, everything)
    if not notes:
        return None
    settings = load_settings().models
    model = factory.main_model(settings)
    pending = len(pending_images(repos, workspace))
    if on_start:
        on_start(len(notes), settings.main, pending)
    run = review_notes(
        workspace, repos, notes, llm_reviewer(model), llm_deduper(model), on_note=on_note
    )
    user = repos.users.get_or_create_local()
    path = write_review_report(run, settings.main, repos.knowledge.list_for_user(user.id))
    return ReviewOutcome(run, path, settings.main, pending)
