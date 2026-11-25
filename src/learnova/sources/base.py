"""The NoteSource interface. Sync and later services only talk to this, so a FolderSource (or
any other note app) can be added without touching them."""

from __future__ import annotations

from abc import ABC, abstractmethod

from learnova.models import NoteSummary


class NoteSource(ABC):
    @abstractmethod
    def list_notes(self) -> list[NoteSummary]:
        """Every note under the workspace's root, parents before children."""

    @abstractmethod
    def fetch(self, note_id: str) -> str:
        """One note's full content as Markdown."""
