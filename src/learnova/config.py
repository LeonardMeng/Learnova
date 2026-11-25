"""Global settings from <user home>/config.toml. Secrets never live here; they come from
environment variables (loaded from .env by `uv run --env-file .env`)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from learnova.home import ensure_home
from learnova.scheduler.mastery import DEFAULT_TYPE_WEIGHTS, MasteryConfig
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
    mastery: MasteryConfig = field(default_factory=MasteryConfig)


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
        mastery=MasteryConfig(
            alpha=float(data.get("mastery", {}).get("alpha", MasteryConfig.alpha)),
            type_weights={
                **DEFAULT_TYPE_WEIGHTS,
                **{k: float(v) for k, v in data.get("mastery", {}).get("type_weights", {}).items()},
            },
        ),
    )
