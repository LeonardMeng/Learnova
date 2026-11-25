"""Typer entry point. Only input parsing and output live here; the work happens in learnova.*."""

from __future__ import annotations

from typing import Annotated

import typer

from learnova.cli import workspace
from learnova.cli.common import user_errors
from learnova.sources import notion

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


if __name__ == "__main__":
    app()
