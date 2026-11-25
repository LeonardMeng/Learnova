"""The user home (~/.learnova) and the per-workspace marker file (learnova.toml)."""

from __future__ import annotations

import os
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

import tomli_w

HOME_ENV = "LEARNOVA_HOME"  # override the user home, e.g. for tests
WORKSPACE_FILE = "learnova.toml"

DEFAULT_CONFIG = """\
# Learnova global settings.

[database]
# Leave unset to use SQLite at <user home>/learnova.db.
# url = "postgresql+psycopg://user:password@localhost/learnova"

[models]
# Format: "provider:model". Providers: openai, anthropic, google_genai (or google), deepseek.
# main = "openai:<model-name>"
# vision = "openai:<model-name>"   # used to describe images; may differ from main

[models.api_key_env]
# Which environment variable holds each provider's key. Unlisted providers use the standard
# name: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, DEEPSEEK_API_KEY.
# openai = "MODEL_API_KEY"

[weights]
# How quiz questions are drawn: w = importance × max(1 − mastery, epsilon) × (1 + ln(1 + days)).
# epsilon = 0.1          # floor, so mastered points still come up now and then
# new_point_days = 7     # "days since last review" for a point never quizzed

[mastery]
# Mastery is a moving average of quiz scores / 5: m <- m + alpha * (score/5 - m).
# alpha = 0.3
"""


def home_dir() -> Path:
    return Path(os.environ.get(HOME_ENV) or "~/.learnova").expanduser().resolve()


def ensure_home() -> Path:
    """Create the user home and a default config.toml on first use."""
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    if not config.exists():
        config.write_text(DEFAULT_CONFIG)
    return home


@dataclass(frozen=True)
class WorkspaceFile:
    """What learnova.toml holds. The database is the source of truth; this file marks the
    directory as a workspace and links it back to its database row by id."""

    id: uuid.UUID
    name: str
    notion_page_id: str


def write_workspace_file(directory: Path, wf: WorkspaceFile) -> Path:
    path = directory / WORKSPACE_FILE
    data = {
        "workspace": {"id": str(wf.id), "name": wf.name},
        "notion": {"page_id": wf.notion_page_id},
    }
    path.write_bytes(tomli_w.dumps(data).encode())
    return path


def read_workspace_file(path: Path) -> WorkspaceFile:
    data = tomllib.loads(path.read_text())
    return WorkspaceFile(
        id=uuid.UUID(data["workspace"]["id"]),
        name=data["workspace"]["name"],
        notion_page_id=data["notion"]["page_id"],
    )


def find_workspace_file(start: Path | None = None) -> Path | None:
    """Look for learnova.toml in `start` and then each parent directory, like Git does."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / WORKSPACE_FILE
        if candidate.is_file():
            return candidate
    return None
