"""One `sync` run for a workspace: read Notion, then describe new images if a vision model is
set up. Used by `learnova sync` and by the API."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from learnova import ignore
from learnova.config import load_settings
from learnova.home import WORKSPACE_FILE
from learnova.llm import factory
from learnova.models import NoteImage, Workspace
from learnova.services import images as images_service
from learnova.services.workspaces import WorkspaceError
from learnova.sources import notion
from learnova.storage.database import Repositories
from learnova.sync import NoteChange, SyncResult, sync_workspace


@dataclass
class SyncRun:
    result: SyncResult
    block_types: Counter = field(default_factory=Counter)
    skipped_databases: list[str] = field(default_factory=list)
    described: images_service.DescribeResult | None = None
    vision_model: str | None = None
    # Images still without a description, and why they weren't described (no model, or skipped).
    pending_images: int = 0
    not_described_because: str | None = None


def run_sync(
    repos: Repositories,
    workspace: Workspace,
    full: bool = False,
    describe: bool = True,
    on_change: Callable[[NoteChange], None] | None = None,
    on_describe_start: Callable[[int, str], None] | None = None,
    on_image: Callable[[NoteImage, str], None] | None = None,
) -> SyncRun:
    if not (workspace.path / WORKSPACE_FILE).is_file():
        raise WorkspaceError(f"Workspace directory {workspace.path} not found (moved or deleted?).")
    rules = ignore.load(
        workspace.path
    )  # a mistake in .learnovaignore stops here, before any change
    source = notion.NotionSource(notion.make_client(), workspace.notion_page_id, rules)
    result = sync_workspace(
        workspace, source, repos.notes, repos.images, on_change=on_change, full=full
    )
    run = SyncRun(result, Counter(source.block_types), list(source.skipped_databases))

    pending = images_service.pending_images(repos, workspace)
    if not pending:
        return run
    if not describe:
        run.pending_images, run.not_described_because = len(pending), "skipped (--no-describe)"
        return run
    settings = load_settings().models
    try:
        model = factory.vision_model(settings)
    except factory.LLMConfigError as e:
        run.pending_images, run.not_described_because = len(pending), str(e)
        return run
    run.vision_model = settings.vision
    if on_describe_start:
        on_describe_start(len(pending), settings.vision)
    run.described = images_service.describe_pending(
        workspace, repos, model, settings.vision, on_image=on_image
    )
    run.pending_images = len(images_service.pending_images(repos, workspace))
    return run
