"""Typer entry point. Only input parsing and output live here; the work happens in learnova.*."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from learnova.cli import workspace
from learnova.cli.common import repositories, short_path, user_errors
from learnova.config import load_settings
from learnova.home import WORKSPACE_FILE
from learnova.llm import factory
from learnova.services import images as images_service
from learnova.services.workspaces import WorkspaceError, WorkspaceService
from learnova.sources import notion
from learnova.sources.notion_markdown import PLACEHOLDER_TYPES, SUPPORTED_TYPES
from learnova.sync import Change, NoteChange, sync_workspace

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


_SYMBOLS = {
    Change.ADDED: "+",
    Change.CHANGED: "~",
    Change.RENAMED: ">",
    Change.DELETED: "-",
}


@app.command()
def sync(
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Workspace name. Default: the one you're in."),
    ] = None,
    full: Annotated[
        bool, typer.Option("--full", help="Re-download every note, even unchanged ones.")
    ] = False,
    no_describe: Annotated[
        bool, typer.Option("--no-describe", help="Don't send new images to the vision model.")
    ] = False,
) -> None:
    """Read the workspace's Notion pages into cache/ and report what changed."""
    with user_errors():
        repos = repositories()
        ws = WorkspaceService(repos).current(Path.cwd(), workspace_name)
        if not (ws.path / WORKSPACE_FILE).is_file():
            raise WorkspaceError(f"Workspace directory {ws.path} not found (moved or deleted?).")
        source = notion.NotionSource(notion.make_client(), ws.notion_page_id)
        typer.echo(f"Syncing {ws.name!r} from Notion…")
        result = sync_workspace(
            ws, source, repos.notes, repos.images, on_change=_print_change, full=full
        )

    counts = ", ".join(f"{len(result.of(kind))} {kind}" for kind in Change)
    typer.echo(f"\n{counts}.")
    typer.echo(f"Cache: {short_path(result.cache_dir)}")
    if result.downloaded:
        typer.echo("Block types (downloaded notes only): " + _block_types(source.block_types))
    if source.skipped_databases:
        names = ", ".join(repr(n) for n in source.skipped_databases)
        typer.echo(f"Skipped {len(source.skipped_databases)} database(s): {names}")
    if result.images_downloaded:
        typer.echo(f"Images: {result.images_downloaded} downloaded")
    for error in result.image_errors:
        typer.secho(f"  ! {error}", fg=typer.colors.YELLOW)
    if not no_describe:
        with user_errors():
            _describe_images(ws, repos)


def _describe_images(ws, repos) -> None:
    pending = images_service.pending_images(repos, ws)
    if not pending:
        return
    settings = load_settings().models
    try:
        model = factory.vision_model(settings)
    except factory.LLMConfigError as e:
        typer.secho(
            f"{len(pending)} image(s) waiting for a description. {e}", fg=typer.colors.YELLOW
        )
        return
    typer.echo(f"\nDescribing {len(pending)} image(s) with {settings.vision}…")
    result = images_service.describe_pending(
        ws,
        repos,
        model,
        settings.vision,
        on_image=lambda img, outcome: typer.echo(
            f"  {'✓' if outcome != 'failed' else '!'} {img.path} {outcome}"
        ),
    )
    typer.echo(f"Images: {result.described} described, {result.reused} reused.")
    for error in result.errors:
        typer.secho(f"  ! {error}", fg=typer.colors.YELLOW)


def _print_change(change: NoteChange) -> None:
    if change.kind == Change.UNCHANGED:
        return
    line = f"  {_SYMBOLS[change.kind]} {' / '.join(change.path):<50} {change.kind}"
    if change.previous_path and change.previous_path != change.path:
        line += f" (was: {' / '.join(change.previous_path)})"
    typer.echo(line)


def _block_types(counts) -> str:
    def label(btype: str) -> str:
        if btype in PLACEHOLDER_TYPES:
            return f"{btype} (placeholder)"
        if btype not in SUPPORTED_TYPES:
            return f"{btype} (unsupported)"
        return btype

    return ", ".join(f"{label(t)} {n}" for t, n in counts.most_common()) or "(none)"


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="What to ask.")],
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="provider:model to use instead of the configured one."),
    ] = None,
    vision: Annotated[
        bool, typer.Option("--vision", help="Ask the configured vision model instead.")
    ] = False,
) -> None:
    """Debug: ask a model one question to check the model setup works."""
    with user_errors():
        settings = load_settings().models
        if model:
            chat, label = factory.chat_model(model, settings), model
        elif vision:
            chat, label = factory.vision_model(settings), settings.vision
        else:
            chat, label = factory.main_model(settings), settings.main
        answer = factory.ask(chat, question)
    typer.echo(answer.text)
    tokens = (
        f" · {answer.input_tokens} in / {answer.output_tokens} out tokens"
        if answer.input_tokens is not None
        else ""
    )
    typer.secho(f"\n{label} · {answer.seconds:.1f}s{tokens}", dim=True)


if __name__ == "__main__":
    app()
