"""`learnova kp ...` subcommands for managing knowledge points by hand."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from learnova.cli.common import repositories, short_path, user_errors
from learnova.services import hierarchy, knowledge
from learnova.services import organize as organize_service

app = typer.Typer(help="Manage knowledge points.", no_args_is_help=True)


@app.command("list")
def list_points(
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only points from this workspace."),
    ] = None,
    flat: Annotated[
        bool, typer.Option("--flat", help="One list, most important first, without the tree.")
    ] = False,
) -> None:
    """List your knowledge points as a tree (topics with their points), with their notes."""
    with user_errors():
        repos = repositories()
        listings = knowledge.list_points(repos, workspace_name)
        rows = [] if flat else knowledge.point_tree(repos, workspace_name)
    if not listings:
        where = f" from workspace {workspace_name!r}" if workspace_name else " yet"
        typer.echo(f"No knowledge points{where}. Run `learnova review` in a workspace first.")
        return
    if not flat:
        _print_tree(rows)
        typer.echo(f"\n{len(listings)} point(s).")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("Point", overflow="fold")
    table.add_column("Imp.", justify="right", no_wrap=True)
    table.add_column("From", overflow="fold")
    for item in listings:
        table.add_row(item.point.name, str(item.point.importance), _from(item.sources))
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


def _from(sources) -> str:
    return (
        "\n".join(f"{s.workspace_name}: {' / '.join(s.note_path)}" for s in sources)
        or "[dim](no notes)[/dim]"
    )


def _print_tree(rows: list[knowledge.TreeListing]) -> None:
    table = Table(box=None, pad_edge=False)
    table.add_column("Point", overflow="fold")
    table.add_column("Imp.", justify="right", no_wrap=True)
    table.add_column("From", overflow="fold")
    for item in rows:
        row = item.row
        text = "  " * row.level + (
            f"[bold]{row.point.name}[/bold]" if row.topic else row.point.name
        )
        if row.topic:
            text += f"\n{'  ' * row.level}[dim]topic · {row.topic.label()}[/dim]"
        if row.also_under:
            names = ", ".join(p.name for p in row.also_under)
            text += f"\n{'  ' * row.level}[dim]↗ also under {names}[/dim]"
        table.add_row(text, str(row.point.importance), _from(item.sources))
    Console().print(table)


@app.command()
def move(
    point: Annotated[str, typer.Argument(help="Name or id of the point to move.")],
    under: Annotated[
        str | None, typer.Option("--under", help="The new parent: name or id.")
    ] = None,
    top: Annotated[bool, typer.Option("--top", help="Move it to the top level.")] = False,
) -> None:
    """Put a point under another one (its primary parent), or at the top level."""
    if (under is None) == (not top):
        raise typer.BadParameter("Give either --under <parent> or --top.")
    with user_errors():
        repos = repositories()
        child = knowledge.find_point(repos, point)
        parent = knowledge.find_point(repos, under) if under else None
        hierarchy.move(repos, child, parent)
    where = f"under {parent.name!r}" if parent else "at the top level"
    typer.echo(f"{child.name!r} is now {where}.")


@app.command()
def link(
    point: Annotated[str, typer.Argument(help="Name or id of the point.")],
    also_under: Annotated[str, typer.Option("--also-under", help="The extra parent: name or id.")],
) -> None:
    """Also show a point under another parent (a shortcut; at most 2 per point)."""
    with user_errors():
        repos = repositories()
        child = knowledge.find_point(repos, point)
        parent = knowledge.find_point(repos, also_under)
        hierarchy.add_shortcut(repos, child, parent)
    typer.echo(f"{child.name!r} is now also under {parent.name!r}.")


@app.command()
def unlink(
    point: Annotated[str, typer.Argument(help="Name or id of the point.")],
    from_: Annotated[str, typer.Option("--from", help="The parent to take it out of.")],
) -> None:
    """Take a point out of one of its parents (a shortcut, or its primary parent)."""
    with user_errors():
        repos = repositories()
        child = knowledge.find_point(repos, point)
        parent = knowledge.find_point(repos, from_)
        was_primary = hierarchy.remove_link(repos, child, parent)
    more = " It is now at the top level." if was_primary else ""
    typer.echo(f"{child.name!r} is no longer under {parent.name!r}.{more}")


@app.command()
def organize(
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only this workspace's points (topics from "
                     "other workspaces can still be parents)."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Apply without asking.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only write the proposal; change nothing.")
    ] = False,
) -> None:  # fmt: skip
    """Let the model propose topics, moves and shortcuts; apply them all after you confirm."""
    with user_errors():
        repos = repositories()
        typer.echo("Asking the model for a proposal…")
        proposal, report, _model = organize_service.run_organize(repos, workspace_name)
    if not proposal.point_count:
        typer.echo("No knowledge points to organize yet.")
        return
    for n, change in organize_service.numbered(proposal):
        text = change.text.replace("**", "")
        if change.refused:
            typer.secho(f"{n:>3}. {text}  (refused: {change.refused})", fg=typer.colors.YELLOW)
        else:
            typer.echo(f"{n:>3}. {text}")
    refused = len(proposal.changes) - len(proposal.accepted)
    typer.echo(
        f"\n{len(proposal.accepted)} change(s) can be applied"
        + (f", {refused} refused" if refused else "")
        + f". Proposal: {short_path(report)}"
    )
    if not proposal.accepted or dry_run:
        typer.echo("Nothing changed." if not proposal.accepted else "Dry run: nothing changed.")
        return
    if not yes and not typer.confirm("Apply all of them?", default=False):
        typer.echo("Nothing changed.")
        return
    with user_errors():
        organize_service.apply(repos, proposal, report)
    typer.echo(f"Applied {len(proposal.accepted)} change(s).")
