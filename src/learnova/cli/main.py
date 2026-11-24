"""Typer entry point. Only input parsing and output live here; the work happens in learnova.*."""

from __future__ import annotations

import httpx
import typer
from notion_client.errors import APIErrorCode, APIResponseError, RequestTimeoutError

from learnova.sources import notion

app = typer.Typer(
    help="Learnova: an AI teaching assistant for your study notes.", no_args_is_help=True
)


@app.callback()
def _root() -> None:
    """Keeps Typer in multi-command mode so `learnova ping` stays a subcommand."""


@app.command()
def ping(page: str = typer.Argument(..., help="Notion page ID or page URL.")) -> None:
    """Check the Notion connection by listing every sub-page under PAGE."""
    try:
        client = notion.make_client()
        page_id = notion.parse_page_id(page)
        root_title = notion.page_title(client, page_id)
        typer.echo(root_title)
        count = 0
        for node in notion.walk_subpages(client, page_id):
            label = f"[database] {node.title}" if node.kind == "database" else node.title
            typer.echo(f"{'  ' * (node.depth + 1)}- {label}")
            count += 1
    except notion.NotionConfigError as e:
        _fail(str(e))
    except APIResponseError as e:
        _fail(_explain(e))
    except RequestTimeoutError:
        _fail("Notion did not respond in time. Check your network and try again.")
    except httpx.TransportError as e:
        _fail(f"Could not reach Notion: {e}. Check your network or proxy.")
    typer.echo(f"\n{count} sub-page(s) found.")


def _explain(e: APIResponseError) -> str:
    if e.code in (APIErrorCode.ObjectNotFound, APIErrorCode.RestrictedResource):
        return (
            "Notion can't see that page. Open it in Notion, click ••• → Connections, and add "
            "your integration. Also check the page ID.\n"
            f"(Notion said: {e})"
        )
    if e.code == APIErrorCode.Unauthorized:
        return (
            f"Notion rejected the token in {notion.TOKEN_ENV}. Check it is the integration's "
            "Internal Integration Secret."
        )
    return f"Notion API error ({e.code}): {e}"


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
