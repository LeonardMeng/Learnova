"""The NoteSource interface. Sync and later services only talk to this, so a FolderSource (or
any other note app) can be added without touching them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from learnova.models import ImageRef, NoteContent, NoteSummary


@dataclass(frozen=True)
class IgnoredPage:
    """A page left out by .learnovaignore. Pages under it aren't read, so they aren't listed."""

    source_id: str
    path: tuple[str, ...]  # titles from the top page down to this page, inclusive


class NoteSource(ABC):
    @abstractmethod
    def list_notes(self) -> list[NoteSummary]:
        """Every note under the workspace's root, parents before children."""

    @abstractmethod
    def fetch(self, note_id: str) -> NoteContent:
        """One note's full content as Markdown, plus the images it contains."""

    @abstractmethod
    def read_image(self, ref: ImageRef) -> bytes:
        """Download one image found by fetch()."""

    def ignored_pages(self) -> list[IgnoredPage]:
        """Pages list_notes() left out because of .learnovaignore (none by default)."""
        return []
