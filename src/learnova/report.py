"""Writes review results as a Markdown report in the workspace's reports/ folder. Only reports
problems; it never suggests rewritten text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from learnova.errors import NotFoundError
from learnova.models import KnowledgePoint, Workspace
from learnova.services.review import NoteResult, PointOutcome, ReviewRun, Status, where

REPORTS_DIR = "reports"
_SAFE_NAME = re.compile(r"^[\w.-]+\.md$")


class NoSuchReport(NotFoundError):
    pass


@dataclass(frozen=True)
class ReportFile:
    name: str
    size: int
    modified: datetime


def list_reports(workspace: Workspace) -> list[ReportFile]:
    """The workspace's reports, newest first."""
    folder = workspace.path / REPORTS_DIR
    if not folder.is_dir():
        return []
    files = [f for f in folder.iterdir() if f.is_file() and _SAFE_NAME.match(f.name)]
    out = [
        ReportFile(f.name, f.stat().st_size, datetime.fromtimestamp(f.stat().st_mtime, UTC))
        for f in files
    ]
    return sorted(out, key=lambda r: (r.modified, r.name), reverse=True)


def read_report(workspace: Workspace, name: str) -> str:
    """One report's Markdown. Only plain file names inside reports/ are accepted."""
    folder = (workspace.path / REPORTS_DIR).resolve()
    path = (folder / name).resolve()
    if not _SAFE_NAME.match(name) or path.parent != folder or not path.is_file():
        raise NoSuchReport(f"No report named {name!r}.")
    return path.read_text(encoding="utf-8")


def write_review_report(run: ReviewRun, model_label: str, all_points: list[KnowledgePoint]) -> Path:
    folder = run.workspace.path / REPORTS_DIR
    folder.mkdir(exist_ok=True)
    now = datetime.now().astimezone()
    path = folder / f"{now:%Y-%m-%d}-review.md"
    n = 2
    while path.exists():
        path = folder / f"{now:%Y-%m-%d}-review-{n}.md"
        n += 1
    path.write_text(render_review(run, model_label, all_points, now), encoding="utf-8")
    return path


def render_review(
    run: ReviewRun, model_label: str, all_points: list[KnowledgePoint], now: datetime
) -> str:
    names = [p.name.lower() for p in all_points]

    def ref(point: KnowledgePoint) -> str:
        # The name if it's unique among your points, otherwise a short id.
        return f'"{point.name}"' if names.count(point.name.lower()) == 1 else str(point.id)[:8]

    outcomes = [o for r in run.reviewed for o in r.outcomes]
    count = lambda status: sum(1 for o in outcomes if o.status == status)  # noqa: E731
    issues = [i for r in run.reviewed for i in r.issues]
    errors = sum(1 for i in issues if i.kind == "factual_error")
    dups = sum(1 for o in outcomes if o.possible_duplicate)

    out = [
        f"# Review: {run.workspace.name}",
        "",
        f"{now:%Y-%m-%d %H:%M} · model {model_label}",
        "",
        f"- Notes reviewed: {len(run.reviewed)}"
        + (f" (skipped {len(run.skipped)} without content)" if run.skipped else ""),
        f"- Factual errors: {errors} · unclear explanations: {len(issues) - errors} · "
        f"missing topics: {sum(len(r.missing) for r in run.reviewed)}",
        f"- Knowledge points: {count(Status.NEW)} new, {count(Status.UPDATED)} updated, "
        f"{count(Status.KEPT)} kept, {count(Status.REMOVED)} removed"
        + (f" · {dups} possible duplicate(s)" if dups else ""),
    ]
    if run.failed:
        out += ["", "## Not reviewed (will be retried next time)", ""]
        out += [f"- {' / '.join(note.path)}: {error}" for note, error in run.failed]

    for result in run.reviewed:
        out += ["", *_note_section(result, ref)]
    return "\n".join(out).rstrip() + "\n"


def _note_section(result: NoteResult, ref) -> list[str]:
    out = [f"## {' / '.join(result.note.path)}"]
    for warning in result.warnings:
        out += ["", f"> ⚠ {warning}"]

    errors = [i for i in result.issues if i.kind == "factual_error"]
    unclear = [i for i in result.issues if i.kind == "unclear"]
    for title, items in (("Factual errors", errors), ("Unclear or incomplete", unclear)):
        if items:
            out += ["", f"### {title}", ""]
            for i in items:
                out.append(f"- > {_one_line(i.quote)}\n\n  {i.problem.strip()}")
    if result.missing:
        out += ["", "### Missing topics", ""]
        out += [f"- **{m.topic.strip()}**: {m.why.strip()}" for m in result.missing]
    if not (result.issues or result.missing):
        out += ["", "No problems found."]

    shown = [o for o in result.outcomes if o.status != Status.REMOVED]
    if shown:
        out += [
            "",
            "### Knowledge points",
            "",
            "| | Point | Importance | Notes |",
            "|---|---|---|---|",
        ]
        order = [Status.NEW, Status.UPDATED, Status.KEPT]
        for o in sorted(shown, key=lambda o: (order.index(o.status), -o.point.importance)):
            out.append(
                f"| {o.status} | **{_cell(o.point.name)}**: {_cell(o.point.summary)} "
                f"| {o.point.importance} | {_cell(_notes(o))} |"
            )
        dups = [o for o in shown if o.possible_duplicate]
        if dups:
            out += ["", "Possible duplicates: if they are the same, merge them:", ""]
            for o in dups:
                cand = o.possible_duplicate
                out += [
                    f"- **{o.point.name}** may duplicate **{cand.point.name}** "
                    f"({where(cand.sources)})",
                    f"  `learnova kp merge {ref(o.point)} --into {ref(cand.point)}`",
                ]
    removed = [o for o in result.outcomes if o.status == Status.REMOVED]
    if removed:
        out += ["", "### No longer in this note", ""]
        for o in removed:
            kept_by = f" (still in {o.other_sources} other note(s))" if o.other_sources else ""
            out.append(f"- {o.point.name}{kept_by}")
    return out


def _notes(o: PointOutcome) -> str:
    parts = []
    if o.status == Status.UPDATED and o.previous and o.previous.name != o.point.name:
        parts.append(f"was “{o.previous.name}”")
    if o.other_sources:
        parts.append(f"also in {o.other_sources} other note(s)")
    if o.possible_duplicate:
        parts.append(f"may duplicate “{o.possible_duplicate.point.name}”")
    if o.note:
        parts.append(o.note)
    return "; ".join(parts)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _cell(text: str) -> str:
    return _one_line(text).replace("|", "\\|")
