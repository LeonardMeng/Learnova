"""Writes review results as a Markdown report in the workspace's reports/ folder. Only reports
problems; it never suggests rewritten text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from learnova.errors import NotFoundError
from learnova.models import IssueKind, KnowledgePoint, Workspace
from learnova.services.issues import RankedIssue
from learnova.services.review import ReviewRun, Status, where

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
    return sorted(out, key=_report_order, reverse=True)


_REVIEW_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-review(?:-(\d+))?\.md$")


def _report_order(r: ReportFile) -> tuple:
    """Order by the date and run number in the name (review, review-2, …, review-10); file
    times can be reset by copying, so they only order files with other names."""
    m = _REVIEW_NAME.match(r.name)
    if m:
        return (m[1], int(m[2] or 1), r.modified)
    return (r.modified.date().isoformat(), 0, r.modified)


def read_report(workspace: Workspace, name: str) -> str:
    """One report's Markdown. Only plain file names inside reports/ are accepted."""
    folder = (workspace.path / REPORTS_DIR).resolve()
    path = (folder / name).resolve()
    if not _SAFE_NAME.match(name) or path.parent != folder or not path.is_file():
        raise NoSuchReport(f"No report named {name!r}.")
    return path.read_text(encoding="utf-8")


def write_review_report(
    run: ReviewRun,
    model_label: str,
    all_points: list[KnowledgePoint],
    open_issues: list[RankedIssue],
) -> Path:
    folder = run.workspace.path / REPORTS_DIR
    folder.mkdir(exist_ok=True)
    now = datetime.now().astimezone()
    path = folder / f"{now:%Y-%m-%d}-review.md"
    n = 2
    while path.exists():
        path = folder / f"{now:%Y-%m-%d}-review-{n}.md"
        n += 1
    path.write_text(render_review(run, model_label, all_points, open_issues, now), encoding="utf-8")
    return path


FIX_FIRST = 5
KIND_LABEL = {
    IssueKind.FACTUAL_ERROR: "Factual error",
    IssueKind.UNCLEAR: "Unclear",
    IssueKind.MISSING: "Missing topic",
}


def render_review(
    run: ReviewRun,
    model_label: str,
    all_points: list[KnowledgePoint],
    open_issues: list[RankedIssue],
    now: datetime,
) -> str:
    """The report after a review: this run's changes, then every open issue of the workspace
    by priority (from the database, so it also shows issues from earlier runs)."""
    names = [p.name.lower() for p in all_points]

    def ref(point: KnowledgePoint) -> str:
        # The name if it's unique among your points, otherwise a short id.
        return f'"{point.name}"' if names.count(point.name.lower()) == 1 else str(point.id)[:8]

    outcomes = [o for r in run.reviewed for o in r.outcomes]
    count = lambda status: sum(1 for o in outcomes if o.status == status)  # noqa: E731
    new_issues = [i for r in run.reviewed for i in r.new_issues]
    fixed = [(r.note, i) for r in run.reviewed for i in r.fixed_issues]
    dups = [o for o in outcomes if o.possible_duplicate]

    out = [
        f"# Review: {run.workspace.name}",
        "",
        f"{now:%Y-%m-%d %H:%M} · model {model_label}",
        "",
        f"- This run: {len(run.reviewed)} note(s) reviewed"
        + (f", {len(run.skipped)} skipped without content" if run.skipped else "")
        + f" · {len(new_issues)} new issue(s) · {len(fixed)} fixed · "
        f"{len(open_issues)} open in this workspace",
        f"- Knowledge points: {count(Status.NEW)} new, {count(Status.UPDATED)} updated, "
        f"{count(Status.REMOVED)} removed"
        + (f" · {len(dups)} possible duplicate(s)" if dups else ""),
    ]
    for result in run.reviewed:
        out += [f"- ⚠ {' / '.join(result.note.path)}: {w}" for w in result.warnings]
    if run.failed:
        out += ["", "## Not reviewed (will be retried next time)", ""]
        out += [f"- {' / '.join(note.path)}: {error}" for note, error in run.failed]

    new_ids = {i.id for i in new_issues}
    top, rest = open_issues[:FIX_FIRST], open_issues[FIX_FIRST:]
    out += ["", "## Fix first", ""]
    if not top:
        out.append("No open issues.")
    for n, ranked_issue in enumerate(top, 1):
        out += _issue_entry(ranked_issue, new_ids, number=n)

    out += ["", "## Fixed this run", ""]
    if not fixed:
        out.append("Nothing was fixed this run.")
    for note, issue in fixed:
        out.append(
            f"- **{KIND_LABEL[issue.kind]}** in {' / '.join(note.path)}: "
            f"{_one_line(issue.quote or issue.topic)} `{str(issue.id)[:8]}`"
        )

    if rest:
        out += ["", "## Other open issues"]
        by_note: dict[tuple[str, ...], list[RankedIssue]] = {}
        for ranked_issue in rest:
            by_note.setdefault(ranked_issue.issue.note_path, []).append(ranked_issue)
        for path in sorted(by_note):
            out += ["", f"### {' / '.join(path)}", ""]
            for ranked_issue in by_note[path]:
                out += _issue_entry(ranked_issue, new_ids, show_note=False)

    if dups:
        out += ["", "## Possible duplicates", "", "If they are the same, merge them:", ""]
        for o in dups:
            cand = o.possible_duplicate
            out += [
                f"- **{o.point.name}** may duplicate **{cand.point.name}** ({where(cand.sources)})",
                f"  `learnova kp merge {ref(o.point)} --into {ref(cand.point)}`",
            ]
    return "\n".join(out).rstrip() + "\n"


def _issue_entry(
    r: RankedIssue, new_ids: set, number: int = 0, show_note: bool = True
) -> list[str]:
    issue = r.issue
    bullet = f"{number}." if number else "-"
    where_ = f" in {' / '.join(issue.note_path)}" if show_note else ""
    about = ""
    if r.point is not None:
        mastery = f"{r.mastery.mastery:.2f}" if r.mastery else "not quizzed"
        about = f" · {r.point.name} (importance {r.point.importance}, mastery {mastery})"
    new = " · new" if issue.id in new_ids else ""
    head = (
        f"{bullet} **{KIND_LABEL[issue.kind]}**{where_} · priority {r.priority:.1f}{about}{new}"
        f" · `{str(issue.id)[:8]}`"
    )
    body = (
        f"   **{_one_line(issue.topic)}**: {issue.problem.strip()}"
        if issue.kind == IssueKind.MISSING
        else f"   > {_one_line(issue.quote)}\n\n   {issue.problem.strip()}"
    )
    return [head, "", body, ""]


def _one_line(text: str) -> str:
    return " ".join(text.split())
