"""`learnova workspace ...` subcommands. Each one parses input, calls WorkspaceService, and
prints the result; the rules live in the service."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from learnova.cli.common import repositories, short_path, user_errors
from learnova.services.workspaces import WorkspaceService
from learnova.sources import notion

app = typer.Typer(help="Create and manage workspaces.", no_args_is_help=True)


def _service() -> WorkspaceService:
    return WorkspaceService(repositories())


def check_notion_page(page_id: str) -> str:
    """Return the page title, or raise if Notion can't see the page. Replaced in tests."""
    return notion.page_title(notion.make_client(), page_id)


@app.command()
def new(
    name: Annotated[str, typer.Argument(help="Workspace name, unique among your workspaces.")],
    notion_page: Annotated[str, typer.Option("--notion", help="Notion page ID or URL.")],
    path: Annotated[
        Path | None,
        typer.Option(help="Workspace directory (created if missing). Default: current."),
    ] = None,
) -> None:
    """Create a workspace for one Notion page and register it."""
    with user_errors():
        page_id = notion.parse_page_id(notion_page)
        title = check_notion_page(page_id)
        ws = _service().create(name, page_id, path or Path.cwd())
    typer.echo(f"Created workspace {ws.name!r} at {ws.path}")
    typer.echo(f"  Notion page: {title}")
    if ws.is_default:
        typer.echo("  This is your default workspace.")


@app.command("list")
def list_workspaces() -> None:
    """List all workspaces. ★ = default, → = the one used from here."""
    with user_errors():
        listings = _service().list(Path.cwd())
    if not listings:
        typer.echo(
            "No workspaces yet. Create one with `learnova workspace new <name> --notion <page>`."
        )
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("")
    table.add_column("Name")
    table.add_column("Path", overflow="fold")  # long paths wrap rather than get cut off
    table.add_column("Notion page", no_wrap=True, min_width=36)
    table.add_column("Status", no_wrap=True)
    for item in listings:
        ws = item.workspace
        marks = ("→" if item.current else " ") + ("★" if ws.is_default else " ")
        status = "ok" if item.found else "[red]not found[/red]"
        table.add_row(marks, ws.name, short_path(ws.path), ws.notion_page_id, status)
    Console().print(table)


@app.command()
def use(name: Annotated[str, typer.Argument(help="Workspace to make the default.")]) -> None:
    """Set the default workspace, used when you're not inside a workspace directory."""
    with user_errors():
        ws = _service().use(name)
    typer.echo(f"Default workspace is now {ws.name!r}.")


@app.command()
def remove(name: Annotated[str, typer.Argument(help="Workspace to unregister.")]) -> None:
    """Unregister a workspace. Its directory, Notion and your knowledge points are untouched."""
    with user_errors():
        ws = _service().remove(name)
    typer.echo(f"Removed workspace {ws.name!r}. Files in {ws.path} were left as they are.")
    if ws.is_default:
        typer.echo("It was the default; pick a new one with `learnova workspace use <name>`.")
