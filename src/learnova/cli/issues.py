"""`learnova issues ...`: the problems reviews found, by priority, and changing their status."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from learnova.cli.common import repositories, user_errors
from learnova.config import load_settings
from learnova.models import IssueKind, IssueStatus
from learnova.services import issues as issues_service
from learnova.services.workspaces import WorkspaceService

app = typer.Typer(
    help="Problems found by `review`, most important first. Without a subcommand: list them.",
    invoke_without_command=True,
)


class StatusFilter(StrEnum):
    open = "open"
    fixed = "fixed"
    dismissed = "dismissed"
    obsolete = "obsolete"
    all = "all"


KIND_SHORT = {
    IssueKind.FACTUAL_ERROR: "error",
    IssueKind.UNCLEAR: "unclear",
    IssueKind.MISSING: "missing",
}


@app.callback()
def list_issues(
    ctx: typer.Context,
    workspace_name: Annotated[
        str | None, typer.Option("--workspace", "-w", help="Only this workspace. Default: all.")
    ] = None,
    kind: Annotated[IssueKind | None, typer.Option("--kind", "-k", help="Only this kind.")] = None,
    status: Annotated[
        StatusFilter, typer.Option("--status", "-s", help="Which issues to show.")
    ] = StatusFilter.open,
) -> None:
    """List issues by priority (open ones unless --status says otherwise)."""
    if ctx.invoked_subcommand is not None:
        return
    with user_errors():
        repos = repositories()
        ws = WorkspaceService(repos).current(name=workspace_name) if workspace_name else None
        statuses = list(IssueStatus) if status == StatusFilter.all else [IssueStatus(status.value)]
        ranked = issues_service.ranked(
            repos, load_settings().issues, ws.id if ws else None, statuses,
            [kind] if kind else None,
        )  # fmt: skip
    if not ranked:
        typer.echo(f"No {'' if status == StatusFilter.all else status.value + ' '}issues.")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("ID", no_wrap=True)
    table.add_column("Prio", justify="right", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    if status != StatusFilter.open:
        table.add_column("Status", no_wrap=True)
    table.add_column("Note", overflow="fold")
    table.add_column("Point", overflow="fold")
    table.add_column("Passage / topic", overflow="fold", max_width=40)
    table.add_column("Problem", overflow="fold", max_width=50)
    for r in ranked:
        i = r.issue
        short = str(i.id)[:8]
        # A clickable link to the paragraph in Notion, in terminals that support links.
        row = [f"[link={r.url}]{short}[/link]" if r.url else short, f"{r.priority:.1f}",
               KIND_SHORT[i.kind]]  # fmt: skip
        if status != StatusFilter.open:
            row.append(i.status.value)
        row += [
            " / ".join(i.note_path[1:] or i.note_path),
            r.point.name if r.point else "",
            i.topic if i.kind == IssueKind.MISSING else f"“{i.quote}”",
            i.problem,
        ]
        table.add_row(*row)
    Console().print(table)
    typer.echo(
        f"\n{len(ranked)} issue(s). Change one with `learnova issues fix|dismiss|reopen <id>`."
    )


def _change(action: str, issue_id: str) -> None:
    with user_errors():
        issue = issues_service.change_status(repositories(), issue_id, action, datetime.now(UTC))
    what = issue.topic if issue.kind == IssueKind.MISSING else issue.quote
    typer.echo(
        f"Issue {str(issue.id)[:8]} is now {issue.status.value}: {' '.join(what.split())[:80]}"
    )


@app.command()
def fix(issue_id: Annotated[str, typer.Argument(help="Issue id (first 8+ characters).")]) -> None:
    """Mark an open issue as fixed (a later review would also notice)."""
    _change("fix", issue_id)


@app.command()
def dismiss(
    issue_id: Annotated[str, typer.Argument(help="Issue id (first 8+ characters).")],
) -> None:
    """Dismiss an open issue: not a problem. Reviews won't raise it again."""
    _change("dismiss", issue_id)


@app.command()
def reopen(
    issue_id: Annotated[str, typer.Argument(help="Issue id (first 8+ characters).")],
) -> None:
    """Reopen a fixed or dismissed issue."""
    _change("reopen", issue_id)
