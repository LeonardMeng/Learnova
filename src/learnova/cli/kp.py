"""`learnova kp ...` subcommands for managing knowledge points by hand."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from learnova.cli.common import repositories, user_errors
from learnova.services import knowledge

app = typer.Typer(help="Manage knowledge points.", no_args_is_help=True)


@app.command("list")
def list_points(
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only points from this workspace."),
    ] = None,
) -> None:
    """List your knowledge points, most important first, with the notes they come from."""
    with user_errors():
        listings = knowledge.list_points(repositories(), workspace_name)
    if not listings:
        where = f" from workspace {workspace_name!r}" if workspace_name else " yet"
        typer.echo(f"No knowledge points{where}. Run `learnova review` in a workspace first.")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("Point", overflow="fold")
    table.add_column("Imp.", justify="right", no_wrap=True)
    table.add_column("From", overflow="fold")
    for item in listings:
        notes = (
            "\n".join(f"{s.workspace_name}: {' / '.join(s.note_path)}" for s in item.sources)
            or "[dim](no notes)[/dim]"
        )
        table.add_row(item.point.name, str(item.point.importance), notes)
    Console().print(table)
    typer.echo(f"\n{len(listings)} point(s).")


@app.command()
def importance(
    point: Annotated[str, typer.Argument(help="Name or id of the point.")],
    value: Annotated[int, typer.Argument(min=1, max=5, help="1 (trivia) to 5 (core idea).")],
) -> None:
    """Set a point's importance. Reviews never change it again; it weights quiz questions."""
    with user_errors():
        updated = knowledge.set_importance(repositories(), point, value)
    typer.echo(f"Importance of {updated.name!r} is now {updated.importance}.")


@app.command()
def merge(
    point: Annotated[str, typer.Argument(help="The duplicate: name or id. It is removed.")],
    into: Annotated[str, typer.Option("--into", help="The point to keep: name or id.")],
) -> None:
    """Merge a duplicate knowledge point into another; the kept point gets all its notes."""
    with user_errors():
        result = knowledge.merge_points(repositories(), point, into)
    typer.echo(
        f"Merged {result.merged.name!r} into {result.into.name!r}. "
        f"It now comes from {result.source_count} note(s)."
    )
