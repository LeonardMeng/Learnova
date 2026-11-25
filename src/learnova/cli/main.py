"""Typer entry point. Only input parsing and output live here; the work happens in learnova.*."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from learnova.cli import workspace
from learnova.cli.common import repositories, short_path, user_errors
from learnova.home import WORKSPACE_FILE
from learnova.services.workspaces import WorkspaceError, WorkspaceService
from learnova.sources import notion
from learnova.sources.notion_markdown import PLACEHOLDER_TYPES, SUPPORTED_TYPES
from learnova.sync import sync_workspace

app = typer.Typer(
    help="Learnova: an AI teaching assistant for your study notes.", no_args_is_help=True
)
app.add_typer(workspace.app, name="workspace")


@app.command()
def ping(page: Annotated[str, typer.Argument(help="Notion page ID or page URL.")]) -> None:
    """Check the Notion connection by listing every sub-page under PAGE."""
    with user_errors():
        client = notion.make_client()
        page_id = notion.parse_page_id(page)
        typer.echo(notion.page_title(client, page_id))
        count = 0
        for node in notion.walk_subpages(client, page_id):
            label = f"[database] {node.title}" if node.kind == "database" else node.title
            typer.echo(f"{'  ' * (node.depth + 1)}- {label}")
            count += 1
    typer.echo(f"\n{count} sub-page(s) found.")


@app.command()
def sync(
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Workspace name. Default: the one you're in."),
    ] = None,
) -> None:
    """Read every note in the workspace's Notion page into its cache/ folder."""
    with user_errors():
        repos = repositories()
        ws = WorkspaceService(repos).current(Path.cwd(), workspace_name)
        if not (ws.path / WORKSPACE_FILE).is_file():
            raise WorkspaceError(f"Workspace directory {ws.path} not found (moved or deleted?).")
        source = notion.NotionSource(notion.make_client(), ws.notion_page_id)
        typer.echo(f"Syncing {ws.name!r} from Notion…")
        result = sync_workspace(
            ws, source, repos.notes, on_note=lambda s: typer.echo(f"  ✓ {' / '.join(s.path)}")
        )
    typer.echo(f"\nSynced {len(result.notes)} note(s) into {short_path(result.cache_dir)}")
    typer.echo("Block types: " + _block_types(source.block_types))
    if source.skipped_databases:
        names = ", ".join(repr(n) for n in source.skipped_databases)
        typer.echo(f"Skipped {len(source.skipped_databases)} database(s): {names}")


def _block_types(counts) -> str:
    def label(btype: str) -> str:
        if btype in PLACEHOLDER_TYPES:
            return f"{btype} (placeholder)"
        if btype not in SUPPORTED_TYPES:
            return f"{btype} (unsupported)"
        return btype

    return ", ".join(f"{label(t)} {n}" for t, n in counts.most_common()) or "(none)"


if __name__ == "__main__":
    app()
