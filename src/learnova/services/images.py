"""Gives every downloaded image a text description from the vision model, once per image: a
description is stored with the image's hash and reused wherever the same image appears again,
in any workspace. The descriptions are then written into the notes' cache files."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field

from learnova.llm import factory, prompts
from learnova.models import NoteImage, Workspace
from learnova.storage.database import Repositories
from learnova.sync import refresh_image_blocks

# What vision models accept. Other formats (SVG, ...) stay "not described".
DESCRIBABLE = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@dataclass
class DescribeResult:
    described: int = 0  # new model calls
    reused: int = 0  # same image already described before
    errors: list[str] = field(default_factory=list)


def pending_images(repos: Repositories, workspace: Workspace) -> list[NoteImage]:
    return [
        i
        for i in repos.images.list_for_workspace(workspace.id, include_ignored=False)
        if i.path and i.description is None and i.mime_type in DESCRIBABLE
    ]


def describe_pending(
    workspace: Workspace,
    repos: Repositories,
    model,
    model_label: str,
    on_image: Callable[[NoteImage, str], None] | None = None,
) -> DescribeResult:
    """Describe every image in the workspace that has none yet. A failed call leaves the image
    pending, so the next sync tries again."""
    result = DescribeResult()
    notes = {n.id: n for n in repos.notes.list(workspace.id)}
    touched = set()
    for image in pending_images(repos, workspace):
        note = notes[image.note_id]
        cached = repos.images.find_description(image.image_hash)
        if cached is not None:
            description, by = cached
            repos.images.set_description(image.id, description, by or model_label)
            result.reused += 1
            outcome = "reused"
        else:
            try:
                description = _describe(model, workspace, image, note.title)
            except factory.LLMCallError as e:
                result.errors.append(f"{' / '.join(note.path)}: {e}")
                if on_image:
                    on_image(image, "failed")
                continue
            repos.images.set_description(image.id, description, model_label)
            result.described += 1
            outcome = "described"
        touched.add(image.note_id)
        if on_image:
            on_image(image, outcome)

    for note_id in touched:
        refresh_image_blocks(workspace, notes[note_id], repos.images.list_for_note(note_id))
    return result


def _describe(model, workspace: Workspace, image: NoteImage, note_title: str) -> str:
    data = (workspace.path / image.path).read_bytes()
    caption_line = f' Its caption is "{image.caption}".' if image.caption else ""
    prompt = prompts.load("image").format(note_title=note_title, caption_line=caption_line)
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "base64": base64.b64encode(data).decode(),
                "mime_type": image.mime_type,
            },
        ],
    }
    return factory.ask(model, [message]).text.strip()
