"""Error handling shared by all commands: turns known exceptions into one red line on stderr and
exit code 1, instead of a stack trace."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

import httpx
import typer
from notion_client.errors import APIErrorCode, APIResponseError, RequestTimeoutError

from learnova.config import load_settings
from learnova.llm.factory import LLMCallError, LLMConfigError
from learnova.services.workspaces import WorkspaceError
from learnova.sources import notion
from learnova.storage.database import Repositories, open_repositories


def short_path(path: Path) -> str:
    """Show paths under the home folder as ~/..."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def repositories() -> Repositories:
    return open_repositories(load_settings().database_url)


def fail(message: str) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@contextmanager
def user_errors() -> Iterator[None]:
    try:
        yield
    except (notion.NotionConfigError, WorkspaceError, LLMConfigError) as e:
        fail(str(e))
    except LLMCallError as e:
        fail(f"The model call failed. {e}")
    except APIResponseError as e:
        fail(_explain(e))
    except RequestTimeoutError:
        fail("Notion did not respond in time. Check your network and try again.")
    except httpx.TransportError as e:
        fail(f"Could not reach Notion: {e}. Check your network or proxy.")


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
