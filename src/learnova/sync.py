"""Reads every note from a Source into the workspace and reports what changed since last time.

Each note is one Markdown file in cache/ and one row in the database; its images are files in
images/ (named by content hash) and rows in note_images. A note is downloaded only when it might
have changed; see `_can_skip` for why that needs a one-minute margin."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from learnova.models import ImageRef, Note, NoteImage, NoteSummary, Workspace
from learnova.sources.base import IgnoredPage, NoteSource
from learnova.storage.repositories import ImageRepository, NoteRepository

CACHE_DIR = "cache"
IMAGES_DIR = "images"
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
    IGNORED = "ignored"  # newly hidden by .learnovaignore (kept, not reviewed or quizzed)
    RESTORED = "restored"  # no longer ignored
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
    images_downloaded: int = 0
    image_errors: tuple[str, ...] = ()
    ignored: tuple[IgnoredPage, ...] = ()  # pages .learnovaignore left out (top of each subtree)

    def of(self, kind: Change) -> list[NoteChange]:
        return [c for c in self.changes if c.kind == kind]


def cache_filename(title: str, source_id: str) -> str:
    """Flat, readable and stable: 'redis-persistence-3e6135e3.md'. \\w keeps non-Latin letters."""
    slug = re.sub(r"[^\w]+", "-", title.lower()).strip("-_")[:60] or "untitled"
    return f"{slug}-{source_id.replace('-', '')[:8]}.md"


def content_hash(markdown: str, image_keys: tuple[str, ...] = ()) -> str:
    """Hash of the note's text plus which image files it shows, so replacing an image counts as
    a change even when the text is identical. Notes without images hash their text alone."""
    data = markdown + "".join(f"\n{key}" for key in image_keys)
    return hashlib.sha256(data.encode()).hexdigest()


# --- images ----------------------------------------------------------------------------------

_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def sniff_mime(data: bytes) -> str:
    """Image type from the file's first bytes (more reliable than a URL or a header)."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if b"<svg" in data[:1024].lower():
        return "image/svg+xml"
    return "application/octet-stream"


_REGION = re.compile(r"<!-- image:(?P<id>[\w-]+) -->(?:.*?<!-- /image:(?P=id) -->)?", re.S)


def render_images(markdown: str, images: dict[str, NoteImage]) -> str:
    """Expand each image placeholder (or a block written by an earlier run) into the image link
    and its description. Running it again on its own output gives the same result."""
    return _REGION.sub(lambda m: _image_block(m["id"], images.get(m["id"])), markdown)


def _image_block(block_id: str, image: NoteImage | None) -> str:
    lines = [f"<!-- image:{block_id} -->"]
    if image is None or image.path is None:
        caption = f": {image.caption}" if image and image.caption else ""
        lines.append(f"*[image could not be downloaded{caption}]*")
    else:
        alt = (image.caption or "image").replace("]", "\\]").replace("\n", " ")
        lines.append(f"![{alt}](../{image.path})")
        if image.description:
            by = f" ({image.described_by})" if image.described_by else ""
            body = "\n".join(f"> {line}".rstrip() for line in image.description.strip().split("\n"))
            lines += ["", f"> **Image description**{by}:", body]
        else:
            lines.append("*(not described yet)*")
    lines.append(f"<!-- /image:{block_id} -->")
    return "\n".join(lines)


def write_cache_file(
    workspace: Workspace, summary: NoteSummary, markdown: str, images: list[NoteImage]
) -> Path:
    path = workspace.path / CACHE_DIR / cache_filename(summary.title, summary.source_id)
    body = render_images(markdown, {i.block_id: i for i in images})
    path.write_text(front_matter(summary) + body, encoding="utf-8")
    return path


def refresh_image_blocks(workspace: Workspace, note: Note, images: list[NoteImage]) -> None:
    """Rewrite the image blocks of an existing cache file, e.g. after a description arrives."""
    path = workspace.path / CACHE_DIR / cache_filename(note.title, note.source_id)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        path.write_text(render_images(text, {i.block_id: i for i in images}), encoding="utf-8")


def _sync_images(
    workspace: Workspace,
    note: Note,
    refs: tuple[ImageRef, ...],
    source: NoteSource,
    images: ImageRepository,
    errors: list[str],
) -> tuple[list[NoteImage], int]:
    """Download new or replaced images of one note and forget removed ones."""
    existing = {i.block_id: i for i in images.list_for_note(note.id)}
    folder = workspace.path / IMAGES_DIR
    folder.mkdir(exist_ok=True)
    result, downloaded = [], 0
    for ref in refs:
        old = existing.pop(ref.block_id, None)
        if (
            old is not None
            and old.source_key == ref.source_key
            and old.path
            and (workspace.path / old.path).is_file()
        ):
            # Same file as last time: keep it (and its description), just refresh the caption.
            result.append(images.save(note.id, ref, old.path, old.image_hash, old.mime_type))
            continue
        try:
            data = source.read_image(ref)
        except Exception as e:  # network or HTTP error; the note is still synced
            errors.append(f"{' / '.join(note.path)}: image {ref.block_id}: {e}")
            result.append(images.save(note.id, ref, None, None, None))
            continue
        digest = hashlib.sha256(data).hexdigest()
        mime = sniff_mime(data)
        rel = f"{IMAGES_DIR}/{digest[:16]}{_EXTENSIONS.get(mime, '.bin')}"
        if not (workspace.path / rel).exists():
            (workspace.path / rel).write_bytes(data)
        result.append(images.save(note.id, ref, rel, digest, mime))
        downloaded += 1
    for gone in existing.values():
        images.delete(gone.id)
    return result, downloaded


def _remove_unused_image_files(workspace: Workspace, images: ImageRepository) -> None:
    folder = workspace.path / IMAGES_DIR
    if not folder.is_dir():
        return
    used = {i.path for i in images.list_for_workspace(workspace.id) if i.path}
    for file in folder.iterdir():
        if file.is_file() and f"{IMAGES_DIR}/{file.name}" not in used:
            file.unlink()


# --- notes -----------------------------------------------------------------------------------


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
        if previous.ignored:
            continue  # already hidden: nothing changes for its parent
        if source_id not in current and previous.path[:-1] in by_path:
            refresh.add(by_path[previous.path[:-1]])  # deleted or ignored from here
    return refresh


def sync_workspace(
    workspace: Workspace,
    source: NoteSource,
    notes: NoteRepository,
    images: ImageRepository,
    on_change: Callable[[NoteChange], None] | None = None,
    full: bool = False,
) -> SyncResult:
    """Bring cache/, images/ and the database in line with the source. `full` re-downloads every
    note (images whose file is unchanged are still not downloaded again)."""
    cache = workspace.path / CACHE_DIR
    cache.mkdir(exist_ok=True)
    stored = {n.source_id: n for n in notes.list(workspace.id)}
    changes: list[NoteChange] = []
    image_errors: list[str] = []
    downloaded = images_downloaded = 0

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
            if previous.icon != summary.icon:  # e.g. notes synced before icons were stored
                notes.set_icon(previous.id, summary.icon)
            if previous.ignored:  # un-ignored and unchanged: just show it again
                notes.set_ignored(previous.id, False)
                emit(NoteChange(Change.RESTORED, summary.source_id, summary.path))
            else:
                emit(NoteChange(Change.UNCHANGED, summary.source_id, summary.path))
            continue

        content = source.fetch(summary.source_id)
        downloaded += 1
        new_hash = content_hash(content.markdown, tuple(r.source_key for r in content.images))
        if previous is not None and previous.title != summary.title:
            (cache / cache_filename(previous.title, previous.source_id)).unlink(missing_ok=True)
        note = notes.upsert(workspace.id, summary, new_hash, synced_at=datetime.now(UTC))
        note_images, count = _sync_images(
            workspace, note, content.images, source, images, image_errors
        )
        images_downloaded += count
        notes.replace_blocks(note.id, list(content.blocks))
        write_cache_file(workspace, summary, content.markdown, note_images)

        if previous is None:
            emit(NoteChange(Change.ADDED, summary.source_id, summary.path))
            continue
        moved = previous.path != summary.path or previous.title != summary.title
        if previous.ignored:
            kind = Change.RESTORED
        elif previous.content_hash != new_hash:
            kind = Change.CHANGED
        elif moved:
            kind = Change.RENAMED
        else:
            kind = Change.UNCHANGED  # edited and changed back, or only page properties changed
        emit(NoteChange(kind, summary.source_id, summary.path, previous.path if moved else None))

    ignored = source.ignored_pages()
    ignored_ids = {p.source_id for p in ignored}
    ignored_paths = [p.path for p in ignored]
    for source_id, previous in stored.items():
        if source_id in seen:
            continue
        hidden = source_id in ignored_ids or any(
            previous.path[: len(p)] == p for p in ignored_paths
        )
        if hidden:  # left out by .learnovaignore: keep it, but hide it
            if not previous.ignored:
                notes.set_ignored(previous.id, True)
                emit(NoteChange(Change.IGNORED, source_id, previous.path))
            continue
        (cache / cache_filename(previous.title, source_id)).unlink(missing_ok=True)
        notes.delete(workspace.id, source_id)  # its note_images rows go with it
        emit(NoteChange(Change.DELETED, source_id, previous.path))

    _remove_unused_image_files(workspace, images)
    return SyncResult(
        ignored=tuple(ignored),
        changes=changes,
        cache_dir=cache,
        downloaded=downloaded,
        images_downloaded=images_downloaded,
        image_errors=tuple(image_errors),
    )
