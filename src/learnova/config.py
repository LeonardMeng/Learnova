"""Global settings from <user home>/config.toml. Secrets never live here; they come from
environment variables (loaded from .env by `uv run --env-file .env`)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from learnova.home import ensure_home


@dataclass(frozen=True)
class Settings:
    database_url: str


def load_settings() -> Settings:
    home = ensure_home()
    data = tomllib.loads((home / "config.toml").read_text())
    url = data.get("database", {}).get("url") or f"sqlite:///{home / 'learnova.db'}"
    return Settings(database_url=url)
