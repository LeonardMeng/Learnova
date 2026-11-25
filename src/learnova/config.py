"""Global settings from <user home>/config.toml. Secrets never live here; they come from
environment variables (loaded from .env by `uv run --env-file .env`)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from learnova.home import ensure_home
from learnova.scheduler.weights import WeightConfig


@dataclass(frozen=True)
class ModelSettings:
    main: str | None = None  # "provider:model"
    vision: str | None = None
    api_key_env: dict[str, str] = field(default_factory=dict)  # provider -> env var name


@dataclass(frozen=True)
class Settings:
    database_url: str
    models: ModelSettings = field(default_factory=ModelSettings)
    weights: WeightConfig = field(default_factory=WeightConfig)


def load_settings() -> Settings:
    home = ensure_home()
    data = tomllib.loads((home / "config.toml").read_text())
    url = data.get("database", {}).get("url") or f"sqlite:///{home / 'learnova.db'}"
    models = data.get("models", {})
    weights = data.get("weights", {})
    return Settings(
        database_url=url,
        models=ModelSettings(
            main=models.get("main"),
            vision=models.get("vision"),
            api_key_env=dict(models.get("api_key_env", {})),
        ),
        weights=WeightConfig(
            epsilon=float(weights.get("epsilon", WeightConfig.epsilon)),
            new_point_days=float(weights.get("new_point_days", WeightConfig.new_point_days)),
        ),
    )
