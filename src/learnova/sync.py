"""Reads every note from a Source into the workspace and reports what changed since last time.

Each note is one Markdown file in cache/ and one row in the database. A note is downloaded only
when it might have changed; see `_can_skip` for why that needs a one-minute margin."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from learnova.models import Note, NoteSummary, Workspace
from learnova.sources.base import NoteSource
from learnova.storage.repositories import NoteRepository

CACHE_DIR = "cache"
# A parent's Markdown lists its sub-pages by title, but Notion doesn't bump the parent's
# last-edited time when a sub-page is renamed. So whenever a note is added, renamed, moved or
# deleted, its parent (old and new) is downloaded again too; see `_parents_to_refresh`.
#
# Notion rounds last-edited times down to the minute, so an edit made in the same minute as the
# previous sync carries the same timestamp. Only trust the timestamp once a full minute has
# passed between it and the last download.
SKIP_MARGIN = timedelta(minutes=1)


class Change(StrEnum):
    ADDED = "added"
    CHANGED = "changed"  # content differs (it may also have been renamed or moved)
    RENAMED = "renamed/moved"  # same content, new title or position in the tree
    DELETED = "deleted"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class NoteChange:
    kind: Change
    source_id: str
    path: tuple[str, ...]
    previous_path: tuple[str, ...] | None = None  # set when the title or position changed


@dataclass(frozen=True)
class SyncResult:
    changes: list[NoteChange]
    cache_dir: Path
    downloaded: int  # notes whose content was fetched

    def of(self, kind: Change) -> list[NoteChange]:
        return [c for c in self.changes if c.kind == kind]


def cache_filename(title: str, source_id: str) -> str:
    """Flat, readable and stable: 'redis-persistence-3e6135e3.md'. \\w keeps non-Latin letters."""
    slug = re.sub(r"[^\w]+", "-", title.lower()).strip("-_")[:60] or "untitled"
    return f"{slug}-{source_id.replace('-', '')[:8]}.md"


def content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()


def front_matter(summary: NoteSummary) -> str:
    # JSON strings and lists are valid YAML, so titles with quotes or colons stay safe.
    return (
        "---\n"
        f"title: {json.dumps(summary.title, ensure_ascii=False)}\n"
        f"source_id: {summary.source_id}\n"
        f"path: {json.dumps(list(summary.path), ensure_ascii=False)}\n"
        f"last_edited: {summary.last_edited.astimezone(UTC).isoformat()}\n"
        "---\n\n"
    )


def _can_skip(previous: Note, summary: NoteSummary, cache_file: Path) -> bool:
    return (
        previous.last_edited == summary.last_edited
        and previous.title == summary.title
        and previous.path == summary.path
        and previous.synced_at >= summary.last_edited + SKIP_MARGIN
        and cache_file.is_file()
    )


def _parents_to_refresh(summaries: list[NoteSummary], stored: dict[str, Note]) -> set[str]:
    """Notes whose list of sub-pages changed, worked out from the listing alone."""
    by_path = {n.path: n.source_id for n in stored.values()}  # to find a stored note's parent
    current = {s.source_id for s in summaries}
    refresh: set[str] = set()
    for summary in summaries:
        previous = stored.get(summary.source_id)
        if previous is not None and (previous.title, previous.path) == (
            summary.title,
            summary.path,
        ):
            continue
        if summary.parent_id:
            refresh.add(summary.parent_id)
        if previous is not None and previous.path[:-1] in by_path:
            refresh.add(by_path[previous.path[:-1]])  # moved away from here
    for source_id, previous in stored.items():
        if source_id not in current and previous.path[:-1] in by_path:
            refresh.add(by_path[previous.path[:-1]])  # deleted from here
    return refresh


def sync_workspace(
    workspace: Workspace,
    source: NoteSource,
    notes: NoteRepository,
    on_change: Callable[[NoteChange], None] | None = None,
    full: bool = False,
) -> SyncResult:
    """Bring cache/ and the notes table in line with the source. `full` re-downloads every note."""
    cache = workspace.path / CACHE_DIR
    cache.mkdir(exist_ok=True)
    stored = {n.source_id: n for n in notes.list(workspace.id)}
    changes: list[NoteChange] = []
    downloaded = 0

    def emit(change: NoteChange) -> None:
        changes.append(change)
        if on_change:
            on_change(change)

    summaries = source.list_notes()
    refresh = _parents_to_refresh(summaries, stored)
    seen: set[str] = set()
    for summary in summaries:
        seen.add(summary.source_id)
        previous = stored.get(summary.source_id)
        cache_file = cache / cache_filename(summary.title, summary.source_id)

        if (
            previous is not None
            and not full
            and summary.source_id not in refresh
            and _can_skip(previous, summary, cache_file)
        ):
            emit(NoteChange(Change.UNCHANGED, summary.source_id, summary.path))
            continue

        body = source.fetch(summary.source_id)
        downloaded += 1
        new_hash = content_hash(body)
        if previous is not None and previous.title != summary.title:
            (cache / cache_filename(previous.title, previous.source_id)).unlink(missing_ok=True)
        cache_file.write_text(front_matter(summary) + body, encoding="utf-8")
        notes.upsert(workspace.id, summary, new_hash, synced_at=datetime.now(UTC))

        if previous is None:
            emit(NoteChange(Change.ADDED, summary.source_id, summary.path))
            continue
        moved = previous.path != summary.path or previous.title != summary.title
        if previous.content_hash != new_hash:
            kind = Change.CHANGED
        elif moved:
            kind = Change.RENAMED
        else:
            kind = Change.UNCHANGED  # edited and changed back, or only page properties changed
        emit(NoteChange(kind, summary.source_id, summary.path, previous.path if moved else None))

    for source_id, previous in stored.items():
        if source_id in seen:
            continue
        (cache / cache_filename(previous.title, source_id)).unlink(missing_ok=True)
        notes.delete(workspace.id, source_id)
        emit(NoteChange(Change.DELETED, source_id, previous.path))

    return SyncResult(changes=changes, cache_dir=cache, downloaded=downloaded)
