"""Error handling shared by all commands: turns known exceptions into one red line on stderr and
exit code 1, instead of a stack trace."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

import typer

from learnova.config import load_settings
from learnova.errors import describe
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
    """Known errors become one red line and exit code 1; bugs still show a traceback."""
    try:
        yield
    except typer.Exit:
        raise
    except Exception as e:
        described = describe(e)
        if described is None:
            raise
        fail(described[1])
