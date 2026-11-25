"""Reads every note from a Source into the workspace: one Markdown file per note in cache/, and
one row per note in the database. Milestone 4 adds reporting what was added, changed or deleted."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from learnova.models import Note, NoteSummary, Workspace
from learnova.sources.base import NoteSource
from learnova.storage.repositories import NoteRepository

CACHE_DIR = "cache"


@dataclass(frozen=True)
class SyncResult:
    notes: list[Note]
    cache_dir: Path


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


def sync_workspace(
    workspace: Workspace,
    source: NoteSource,
    notes: NoteRepository,
    on_note: Callable[[NoteSummary], None] | None = None,
) -> SyncResult:
    cache = workspace.path / CACHE_DIR
    cache.mkdir(exist_ok=True)
    synced: list[Note] = []
    for summary in source.list_notes():
        body = source.fetch(summary.source_id)
        previous = notes.get_by_source_id(workspace.id, summary.source_id)
        if previous is not None and previous.title != summary.title:
            # Renamed in Notion: drop the file under the old name.
            (cache / cache_filename(previous.title, previous.source_id)).unlink(missing_ok=True)
        (cache / cache_filename(summary.title, summary.source_id)).write_text(
            front_matter(summary) + body, encoding="utf-8"
        )
        synced.append(
            notes.upsert(workspace.id, summary, content_hash(body), synced_at=datetime.now(UTC))
        )
        if on_note:
            on_note(summary)
    return SyncResult(notes=synced, cache_dir=cache)
