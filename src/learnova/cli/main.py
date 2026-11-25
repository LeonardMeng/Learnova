"""Typer entry point. Only input parsing and output live here; the work happens in learnova.*."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from learnova.cli import kp, workspace
from learnova.cli.common import repositories, short_path, user_errors
from learnova.config import load_settings
from learnova.home import WORKSPACE_FILE
from learnova.llm import factory
from learnova.report import write_review_report
from learnova.services import images as images_service
from learnova.services import quiz as quiz_service
from learnova.services import review as review_service
from learnova.services import status as status_service
from learnova.services.workspaces import WorkspaceError, WorkspaceService
from learnova.sources import notion
from learnova.sources.notion_markdown import PLACEHOLDER_TYPES, SUPPORTED_TYPES
from learnova.sync import Change, NoteChange, sync_workspace

app = typer.Typer(
    help="Learnova: an AI teaching assistant for your study notes.", no_args_is_help=True
)
app.add_typer(workspace.app, name="workspace")
app.add_typer(kp.app, name="kp")


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
def review(
    everything: Annotated[
        bool, typer.Option("--all", help="Review every note, not only changed ones.")
    ] = False,
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Workspace name. Default: the one you're in."),
    ] = None,
) -> None:
    """Extract knowledge points from changed notes and flag errors, gaps and unclear parts."""
    with user_errors():
        repos = repositories()
        ws = WorkspaceService(repos).current(Path.cwd(), workspace_name)
        notes = review_service.notes_to_review(repos, ws, everything)
        if not notes:
            typer.echo(
                "Nothing to review: no note changed since its last review. "
                "Run `learnova sync` first, or use --all."
            )
            return
        settings = load_settings().models
        model = factory.main_model(settings)
        pending = images_service.pending_images(repos, ws)
        if pending:
            typer.secho(
                f"Note: {len(pending)} image(s) have no description yet; run `learnova sync` "
                "to describe them. The review won't see what they show.",
                fg=typer.colors.YELLOW,
            )
        typer.echo(f"Reviewing {len(notes)} note(s) in {ws.name!r} with {settings.main}…")
        run = review_service.review_notes(
            ws,
            repos,
            notes,
            review_service.llm_reviewer(model),
            review_service.llm_deduper(model),
            on_note=lambda note, what: typer.echo(f"  {' / '.join(note.path)}: {what}"),
        )
        user = repos.users.get_or_create_local()
        path = write_review_report(run, settings.main, repos.knowledge.list_for_user(user.id))

    outcomes = [o for r in run.reviewed for o in r.outcomes]
    new = sum(1 for o in outcomes if o.status == review_service.Status.NEW)
    issues = sum(len(r.issues) for r in run.reviewed)
    missing = sum(len(r.missing) for r in run.reviewed)
    dups = sum(1 for o in outcomes if o.possible_duplicate)
    typer.echo(
        f"\n{len(run.reviewed)} reviewed · {new} new point(s) · {issues} issue(s) · "
        f"{missing} missing topic(s)" + (f" · {dups} possible duplicate(s)" if dups else "")
    )
    if run.failed:
        typer.secho(
            f"{len(run.failed)} note(s) failed and will be retried.", fg=typer.colors.YELLOW
        )
    typer.echo(f"Report: {short_path(path)}")


@app.command()
def quiz(
    count: Annotated[int, typer.Option("--count", "-n", min=1, help="How many questions.")] = 5,
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only points from this workspace. Default: all."),
    ] = None,
) -> None:
    """Answer short questions on knowledge points drawn by weight; the model grades them."""
    with user_errors():
        repos = repositories()
        if workspace_name is not None:
            WorkspaceService(repos).current(name=workspace_name)  # checks the name exists
        settings = load_settings()
        now = datetime.now(UTC)
        items = quiz_service.candidates(repos, now, settings.weights, workspace_name)
        if not items:
            typer.echo(
                "No knowledge points to quiz yet. Run `learnova review` in a workspace first."
            )
            return
        model = factory.main_model(settings.models)
        quiz_service.load_rubric()  # fail now, not after the first answer
        questioner = quiz_service.llm_questioner(model)
        grader = quiz_service.llm_grader(model)
    drawn = quiz_service.draw(items, count, random.Random())
    spaces = len({s.workspace_name for i in drawn for s in i.sources})
    typer.echo(
        f"Quiz: {len(drawn)} question(s) from {spaces} workspace(s) · {settings.models.main}\n"
        "Answer in a few sentences and finish with an empty line. Type s to skip, q to quit."
    )

    results = []  # (point name, score or None, mastery before, mastery after)
    for number, item in enumerate(drawn, 1):
        notes = quiz_service.note_texts(repos, item)
        typer.secho(
            f"\nQuestion {number}/{len(drawn)} · {notes[0].path if notes else ''}", bold=True
        )
        try:
            question = questioner(quiz_service.QuestionRequest(item.point, notes))
        except factory.LLMCallError as e:
            typer.secho(f"Couldn't write a question: {e}", fg=typer.colors.YELLOW)
            continue
        typer.echo(question)
        answer = _read_answer()
        if answer is None:
            break
        before = item.mastery.mastery if item.mastery else 0.0
        if not answer:
            results.append((item.point.name, None, before, before))
            typer.echo("Skipped.")
            continue
        try:
            grade = grader(quiz_service.GradeRequest(item.point, notes, question, answer))
        except factory.LLMCallError as e:
            typer.secho(f"Couldn't grade the answer (not recorded): {e}", fg=typer.colors.YELLOW)
            continue
        _, mastery = quiz_service.record(
            repos, item, question, answer, grade, datetime.now(UTC), settings.mastery
        )
        results.append((item.point.name, grade.score, before, mastery.mastery))
        typer.secho(
            f"\nScore {grade.score:.1f} / 5 · {item.point.name} · "
            f"mastery {before:.2f} → {mastery.mastery:.2f}",
            fg=typer.colors.GREEN if grade.score >= 3.5 else typer.colors.YELLOW,
        )
        typer.echo(grade.feedback.strip())
        if grade.missing.strip():
            typer.echo(f"Missing: {grade.missing.strip()}")
        typer.secho(f"Reference answer: {grade.reference_answer.strip()}", dim=True)

    if results:
        typer.echo("\nSummary")
        for name, score, before, after in results:
            shown = "skipped" if score is None else f"{score:.1f}/5"
            typer.echo(f"  {shown:>7}  {name}  ({before:.2f} → {after:.2f})")


def _read_answer() -> str | None:
    """Lines until an empty line. "" = skip, None = quit (or end of input)."""
    lines: list[str] = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            return "\n".join(lines) if lines else None
        command = line.strip().lower()
        if not lines and command in ("q", "quit"):
            return None
        if not lines and command in ("s", "skip"):
            return ""
        if not line.strip():
            if lines:
                return "\n".join(lines)
            continue  # an empty line before any text doesn't end the answer
        lines.append(line)


@app.command()
def status(
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only points from this workspace. Default: all."),
    ] = None,
    weakest: Annotated[int, typer.Option("--weakest", min=1, help="How many weak points.")] = 10,
) -> None:
    """Show mastery, when you last quizzed, and your weakest knowledge points."""
    with user_errors():
        repos = repositories()
        if workspace_name is not None:
            WorkspaceService(repos).current(name=workspace_name)
        st = status_service.status(
            repos, datetime.now(UTC), load_settings().weights, workspace_name, weakest
        )
    if not st.overall.points:
        typer.echo("No knowledge points yet. Run `learnova review` in a workspace first.")
        return

    def line(s: status_service.Summary) -> str:
        return (
            f"{s.points} point(s) · {s.quizzed} quizzed · average mastery "
            f"{s.average_mastery:.2f} · last quiz {_when(s.last_quiz)}"
        )

    title = "All workspaces" if workspace_name is None else f"Workspace {workspace_name!r}"
    typer.secho(f"{title}: {line(st.overall)}", bold=True)
    if workspace_name is None:
        for s in st.workspaces:
            typer.echo(f"  {s.name}: {line(s)}")

    table = Table(box=None, pad_edge=False, title_justify="left")
    for col, kw in (
        ("Point", {"overflow": "fold"}),
        ("Imp.", {"justify": "right"}),
        ("Mastery", {"justify": "right"}),
        ("Quizzed", {"justify": "right"}),
        ("Last quiz", {}),
        ("From", {"overflow": "fold"}),
    ):
        table.add_column(col, **kw)
    for r in st.weakest:
        m = r.mastery
        source = review_service.where(r.sources) if r.sources else "[dim](no notes)[/dim]"
        table.add_row(
            r.point.name,
            str(r.point.importance),
            f"{m.mastery:.2f}" if m else "–",
            str(m.review_count) if m else "0",
            _when(m.last_reviewed if m else None),
            source,
        )
    typer.echo(f"\nWeakest {len(st.weakest)}:")
    Console().print(table)


def _when(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else "never"


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
