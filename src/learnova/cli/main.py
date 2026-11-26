"""Typer entry point. Only input parsing and output live here; the work happens in learnova.*."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from learnova.cli import issues as issues_cli
from learnova.cli import kp, web, workspace
from learnova.cli.common import repositories, short_path, user_errors
from learnova.config import load_settings
from learnova.llm import factory
from learnova.models import QuestionType
from learnova.services import quiz as quiz_service
from learnova.services import review as review_service
from learnova.services import status as status_service
from learnova.services.workspace_sync import run_sync
from learnova.services.workspaces import WorkspaceService
from learnova.sources import notion
from learnova.sources.notion_markdown import PLACEHOLDER_TYPES, SUPPORTED_TYPES
from learnova.sync import Change, NoteChange

app = typer.Typer(
    help="Learnova: an AI teaching assistant for your study notes.", no_args_is_help=True
)
app.add_typer(workspace.app, name="workspace")
app.add_typer(kp.app, name="kp")
app.add_typer(issues_cli.app, name="issues")
app.add_typer(web.app, name="web")


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
    Change.IGNORED: "×",
    Change.RESTORED: "↩",
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
        typer.echo(f"Syncing {ws.name!r} from Notion…")
        run = run_sync(
            repos,
            ws,
            full=full,
            describe=not no_describe,
            on_change=_print_change,
            on_describe_start=lambda n, model: typer.echo(
                f"\nDescribing {n} image(s) with {model}…"
            ),
            on_image=lambda img, outcome: typer.echo(
                f"  {'✓' if outcome != 'failed' else '!'} {img.path} {outcome}"
            ),
        )
    result = run.result
    if run.described is not None:
        typer.echo(f"Images: {run.described.described} described, {run.described.reused} reused.")
        for error in run.described.errors:
            typer.secho(f"  ! {error}", fg=typer.colors.YELLOW)
    optional = (Change.IGNORED, Change.RESTORED)  # only shown when they happened
    counts = ", ".join(
        f"{len(result.of(kind))} {kind}"
        for kind in Change
        if kind not in optional or result.of(kind)
    )
    typer.echo(f"\n{counts}.")
    typer.echo(f"Cache: {short_path(result.cache_dir)}")
    if result.downloaded:
        typer.echo("Block types (downloaded notes only): " + _block_types(run.block_types))
    if result.ignored:
        names = ", ".join(" / ".join(p.path[1:]) for p in result.ignored)
        typer.echo(f"Ignored by .learnovaignore: {names}")
    if run.skipped_databases:
        names = ", ".join(repr(n) for n in run.skipped_databases)
        typer.echo(f"Skipped {len(run.skipped_databases)} database(s): {names}")
    if result.images_downloaded:
        typer.echo(f"Images: {result.images_downloaded} downloaded")
    for error in result.image_errors:
        typer.secho(f"  ! {error}", fg=typer.colors.YELLOW)
    if run.pending_images and run.not_described_because and not no_describe:
        typer.secho(
            f"{run.pending_images} image(s) waiting for a description. {run.not_described_because}",
            fg=typer.colors.YELLOW,
        )


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

    def start(count: int, model: str, pending: int) -> None:
        if pending:
            typer.secho(
                f"Note: {pending} image(s) have no description yet; run `learnova sync` "
                "to describe them. The review won't see what they show.",
                fg=typer.colors.YELLOW,
            )
        typer.echo(f"Reviewing {count} note(s) in {ws.name!r} with {model}…")

    with user_errors():
        repos = repositories()
        ws = WorkspaceService(repos).current(Path.cwd(), workspace_name)
        outcome = review_service.run_review(
            repos,
            ws,
            everything,
            on_start=start,
            on_note=lambda note, what: typer.echo(f"  {' / '.join(note.path)}: {what}"),
        )
    if outcome is None:
        typer.echo(
            "Nothing to review: no note changed since its last review. "
            "Run `learnova sync` first, or use --all."
        )
        return
    run = outcome.run
    outcomes = [o for r in run.reviewed for o in r.outcomes]
    new = sum(1 for o in outcomes if o.status == review_service.Status.NEW)
    new_issues = sum(len(r.new_issues) for r in run.reviewed)
    fixed = sum(len(r.fixed_issues) for r in run.reviewed)
    dups = sum(1 for o in outcomes if o.possible_duplicate)
    typer.echo(
        f"\n{len(run.reviewed)} reviewed · {new} new point(s) · {new_issues} new issue(s) · "
        f"{fixed} fixed · {outcome.open_issues} open"
        + (f" · {dups} possible duplicate(s)" if dups else "")
    )
    kept = sum(1 for r in run.reviewed if r.issues_kept)
    if kept:
        typer.echo(
            f"{kept} note(s) unchanged since their last issue review: their issues were left "
            "as they were. Edit a note to have its issues checked again."
        )
    if run.failed:
        typer.secho(
            f"{len(run.failed)} note(s) failed and will be retried.", fg=typer.colors.YELLOW
        )
    typer.echo(f"Report: {short_path(outcome.report)}")


class QuizType(StrEnum):
    short = "short"
    mc = "mc"
    tf = "tf"


QUIZ_KINDS = {
    QuizType.short: QuestionType.SHORT_ANSWER,
    QuizType.mc: QuestionType.MULTIPLE_CHOICE,
    QuizType.tf: QuestionType.TRUE_FALSE,
}


@app.command()
def quiz(
    count: Annotated[int, typer.Option("--count", "-n", min=1, help="How many questions.")] = 5,
    workspace_name: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only points from this workspace. Default: all."),
    ] = None,
    kind: Annotated[
        QuizType,
        typer.Option(
            "--type", "-t", help="short (graded by the model), mc (multiple choice) or tf."
        ),
    ] = QuizType.short,
    points: Annotated[
        list[str] | None,
        typer.Option("--point", "-p", help="Only this point (name or id); repeat for more."),
    ] = None,
    topic: Annotated[
        str | None,
        typer.Option("--topic", help="Only the points under this topic (name or id)."),
    ] = None,
) -> None:
    """Answer questions on knowledge points drawn by weight. Short answers are graded by the
    model; multiple choice and true/false are checked locally. With --point or --topic, only
    those points are asked (again, if there are fewer than --count)."""
    qtype = QUIZ_KINDS[kind]
    rng = random.Random()
    if (points or topic) and workspace_name is not None:
        raise typer.BadParameter("--point / --topic can't be combined with --workspace.")
    with user_errors():
        repos = repositories()
        if workspace_name is not None:
            WorkspaceService(repos).current(name=workspace_name)  # checks the name exists
        selection = quiz_service.select(repos, points or [], topic) if (points or topic) else None
        settings = load_settings()
        model = factory.main_model(settings.models)
        models = quiz_service.quiz_models(model, qtype, rng)
    how = {
        QuizType.short: "Answer in a few sentences and finish with an empty line.",
        QuizType.mc: "Answer with A, B, C or D.",
        QuizType.tf: "Answer with t (true) or f (false).",
    }[kind]
    scope = f" on {selection.label}" if selection else ""
    typer.echo(
        f"Quiz{scope}: {count} question(s) · {settings.models.main}\n{how} "
        "Type s to skip, q to quit."
    )

    asked: list = []
    spaces: set[str] = set()
    results = []  # (point name, score or None, mastery before, mastery after)
    for number in range(1, count + 1):
        try:
            question = quiz_service.issue_question(
                repos, models, qtype, datetime.now(UTC), settings.weights, rng,
                workspace_name, asked, selection,
            )  # fmt: skip
        except quiz_service.NoPointsLeft as e:
            typer.echo(f"\n{e}")
            break
        except (factory.LLMCallError, quiz_service.InvalidQuestion) as e:
            typer.secho(f"\nCouldn't write a question: {e}", fg=typer.colors.YELLOW)
            continue
        asked.append(question.point_id)
        source = question.notes[0]["path"] if question.notes else ""
        spaces.add(source.split(":", 1)[0])
        typer.secho(f"\nQuestion {number}/{count} · {source}", bold=True)
        outcome = _ask(repos, models, question, settings)
        if outcome == "quit":
            break
        if outcome is not None:
            results.append(outcome)

    if results:
        typer.echo(f"\nSummary ({len(results)} question(s) from {len(spaces)} workspace(s))")
        for name, score, before, after in results:
            shown = "skipped" if score is None else f"{score:.1f}/5"
            typer.echo(f"  {shown:>7}  {name}  ({before:.2f} → {after:.2f})")


def _ask(repos, models, question, settings):
    """Show one issued question, read the answer, grade it. Returns a summary row, None (nothing
    recorded) or "quit"."""
    is_short = question.question_type == QuestionType.SHORT_ANSWER
    is_tf = question.question_type == QuestionType.TRUE_FALSE
    point = repos.knowledge.get(question.point_id)
    user = repos.users.get_or_create_local()
    previous = repos.attempts.mastery(user.id).get(point.id)
    before = previous.mastery if previous else 0.0

    typer.echo(f"True or false: {question.text}" if is_tf else question.text)
    if question.question_type == QuestionType.MULTIPLE_CHOICE:
        for i, option in enumerate(question.options):
            typer.echo(f"  {quiz_service.LETTERS[i]}) {option}")

    while True:
        answer = _read_answer() if is_short else _read_line()
        if answer is None:
            return "quit"
        if not answer.strip():
            quiz_service.skip_question(repos, question.id, datetime.now(UTC))
            typer.echo("Skipped.")
            return (point.name, None, before, before)
        try:
            result = quiz_service.answer_question(
                repos, models, question.id, answer, datetime.now(UTC), settings.mastery
            )
            break
        except quiz_service.InvalidAnswer as e:
            typer.echo(str(e))
        except factory.LLMCallError as e:
            typer.secho(f"Couldn't grade the answer (not recorded): {e}", fg=typer.colors.YELLOW)
            return None

    if result.correct is not None:
        typer.secho(
            "Correct." if result.correct else f"Not quite: the answer is {result.correct_option}.",
            fg=typer.colors.GREEN if result.correct else typer.colors.YELLOW,
        )
    typer.secho(
        f"\nScore {result.score:.1f} / 5 · {point.name} · "
        f"mastery {result.mastery_before:.2f} → {result.mastery_after:.2f}",
        fg=typer.colors.GREEN if result.score >= 3.5 else typer.colors.YELLOW,
    )
    if result.feedback:
        typer.echo(result.feedback)
    if result.missing:
        typer.echo(f"Missing: {result.missing}")
    if result.reference_answer:
        typer.secho(f"Reference answer: {result.reference_answer}", dim=True)
    if result.explanation:
        typer.echo(result.explanation)
    return (point.name, result.score, result.mastery_before, result.mastery_after)


def _read_line() -> str | None:
    """One line for multiple choice / true-false. "" = skip, None = quit."""
    try:
        line = input("> ")
    except EOFError:
        return None
    command = line.strip().lower()
    if command in ("q", "quit"):
        return None
    if command in ("s", "skip"):
        return ""
    return line if line.strip() else " "  # an empty line isn't a skip here: ask again


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
def serve(
    port: Annotated[int, typer.Option("--port", "-p", help="Port on 127.0.0.1.")] = 8000,
) -> None:
    """Start the HTTP API on 127.0.0.1 (this computer only, no authentication)."""
    import uvicorn

    from learnova.api.app import create_app

    typer.echo(f"Learnova API on http://127.0.0.1:{port} · interactive docs at /docs")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="info")


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
