"""`learnova web import` and `learnova web check`: copy a web tutorial into Notion once, then
check the site for changes against the local cache. Option parsing and output only; the work is
in learnova.webimport."""

from __future__ import annotations

from typing import Annotated

import typer

from learnova.cli.common import repositories, short_path, user_errors
from learnova.config import load_settings
from learnova.errors import NotFoundError
from learnova.sources import notion
from learnova.webimport import gfg
from learnova.webimport.checker import CheckOutcome, CheckResult, CheckRun
from learnova.webimport.fetch import Fetcher
from learnova.webimport.importer import ImportOutcome, ImportResult, ImportRun, forget, register
from learnova.webimport.notion_writer import NotionWriter

app = typer.Typer(help="Web tutorials: import into Notion once, then check for changes.")


def _fetcher() -> Fetcher:
    return Fetcher(min_interval=load_settings().import_web.min_interval_seconds)


@app.command("import")
def import_(
    index_url: Annotated[str, typer.Argument(help="The tutorial's index page.")],
    into: Annotated[
        str | None,
        typer.Option("--into", help="Notion page (ID or URL) to import under."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit", min=1, help="Import only the next N articles (run again for more)."
        ),
    ] = None,
) -> None:
    """Copy a tutorial into Notion once, laid out like its index, and cache it locally.

    Afterwards use `learnova web check`."""
    with user_errors():
        repos = repositories()
        user = repos.users.get_or_create_local()
        imp = register(repos.web_imports, user.id, index_url, into)
        typer.echo(f"Importing {imp.index_url}")
        run = ImportRun(
            repos.web_imports,
            _fetcher(),
            NotionWriter(notion.make_client()),
            limit=limit,
            on_result=_print_import,
        )
        report = run.run(imp)
    ok = sum(1 for r in report.results if r.outcome == ImportOutcome.IMPORTED)
    typer.echo(f"\n{ok} imported.")
    if report.stopped:
        typer.secho(
            "Stopped at the error above so the page order stays right. Fix it (or just retry) "
            "and run the same command again: it continues from there.",
            fg=typer.colors.YELLOW,
        )
    elif report.completed:
        typer.echo("Import complete. From now on, run `learnova web check` to see changes.")
    else:
        typer.echo(f"{report.remaining} article(s) still to import: run the same command again.")


@app.command()
def check(
    index_url: Annotated[
        str | None,
        typer.Argument(help="Only this import. Default: every import."),
    ] = None,
) -> None:
    """Compare the site with the local cache and write a report of what changed.

    Notion is not touched; the cache moves on to the new version."""
    with user_errors():
        repos = repositories()
        user = repos.users.get_or_create_local()
        imports = repos.web_imports.list(user.id)
        if index_url:
            url = gfg.normalize_url(index_url, index_url)
            imports = [i for i in imports if i.index_url == url]
            if not imports:
                raise NotFoundError(f"{url} hasn't been imported. Use `learnova web import`.")
        if not imports:
            typer.echo("Nothing imported yet. Start with: learnova web import <url> --into <page>")
            return
        fetcher = _fetcher()
        for imp in imports:
            typer.echo(f"Checking {imp.index_url}")
            report = CheckRun(repos.web_imports, fetcher, on_result=_print_check).run(imp)
            counts = ", ".join(
                f"{len(report.of(o))} {o}"
                for o in CheckOutcome
                if report.of(o) or o in (CheckOutcome.CHANGED, CheckOutcome.UNCHANGED)
            )
            typer.echo(f"\n{counts}.")
            if report.path:
                typer.echo(f"Report: {short_path(report.path)}")
            else:
                typer.echo("Nothing changed; no report written.")


@app.command("forget")
def forget_(
    index_url: Annotated[str, typer.Argument(help="The tutorial's index page.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Forget an import so it can be imported again from scratch.

    Deletes its records and local cache; keeps its reports. Notion is not touched: delete the
    pages there yourself."""
    with user_errors():
        repos = repositories()
        user = repos.users.get_or_create_local()
        url = gfg.normalize_url(index_url, index_url)
        imp = repos.web_imports.get_by_url(user.id, url)
        if imp is None:
            raise NotFoundError(f"{url} hasn't been imported.")
        if not yes:
            typer.confirm(
                f"Forget the import of {url} and delete its local cache? Reports are kept, "
                "Notion is not touched.",
                abort=True,
            )
        folder = forget(repos.web_imports, imp)
    typer.echo(f"Forgotten. Reports kept in {short_path(folder / 'reports')}.")
    typer.echo("Delete the imported pages in Notion yourself before importing again.")


def _print_import(r: ImportResult) -> None:
    line = f"  {'+' if r.outcome == ImportOutcome.IMPORTED else '!'} {r.title}"
    line += f"  ({' / '.join(r.section)})"
    if r.detail:
        typer.secho(f"{line}: {r.detail}", fg=typer.colors.YELLOW)
    else:
        typer.echo(line)


_SYMBOLS = {
    CheckOutcome.CHANGED: "~",
    CheckOutcome.UNCHANGED: "=",
    CheckOutcome.NEW: "+",
    CheckOutcome.REMOVED: "-",
    CheckOutcome.RESTORED: "↩",
    CheckOutcome.ERROR: "!",
}


def _print_check(r: CheckResult) -> None:
    line = f"  {_SYMBOLS[r.outcome]} {r.title}"
    if r.diff is not None:
        line += f": {r.detail or r.diff.describe()}"
    elif r.detail:
        line += f": {r.detail}"
    if r.outcome == CheckOutcome.ERROR:
        typer.secho(line, fg=typer.colors.YELLOW)
    elif r.outcome == CheckOutcome.UNCHANGED:
        typer.secho(line, dim=True)
    else:
        typer.echo(line)
