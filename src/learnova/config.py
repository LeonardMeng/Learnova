"""Global settings from <user home>/config.toml. Secrets never live here; they come from
environment variables (loaded from .env by `uv run --env-file .env`)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from learnova.anchors import DEFAULT_THRESHOLD
from learnova.home import ensure_home
from learnova.scheduler.mastery import DEFAULT_TYPE_WEIGHTS, MasteryConfig
from learnova.scheduler.priority import IssueWeights
from learnova.scheduler.weights import WeightConfig


@dataclass(frozen=True)
class ModelSettings:
    main: str | None = None  # "provider:model"
    vision: str | None = None
    api_key_env: dict[str, str] = field(default_factory=dict)  # provider -> env var name


DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # the Vite dev server


@dataclass(frozen=True)
class ApiSettings:
    # Browser origins allowed to call the API (CORS). Anything else is refused by the browser.
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS


@dataclass(frozen=True)
class ImportWebSettings:
    # Seconds between two requests to the website, so an import never hammers it.
    min_interval_seconds: float = 2.0


@dataclass(frozen=True)
class Settings:
    database_url: str
    models: ModelSettings = field(default_factory=ModelSettings)
    weights: WeightConfig = field(default_factory=WeightConfig)
    mastery: MasteryConfig = field(default_factory=MasteryConfig)
    api: ApiSettings = field(default_factory=ApiSettings)
    issues: IssueWeights = field(default_factory=IssueWeights)
    # How similar a quote must be to a block to link the issue there (0–1); see anchors.py.
    anchor_threshold: float = DEFAULT_THRESHOLD
    import_web: ImportWebSettings = field(default_factory=ImportWebSettings)


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
        api=ApiSettings(
            allowed_origins=tuple(data.get("api", {}).get("allowed_origins", DEFAULT_ORIGINS))
        ),
        anchor_threshold=_threshold(data.get("issues", {}).get("anchor_threshold")),
        issues=IssueWeights(
            **{
                k: float(v)
                for k, v in data.get("issues", {}).items()
                if k in ("factual_error", "unclear", "missing")
            }
        ),
        import_web=ImportWebSettings(
            min_interval_seconds=float(
                data.get("import_web", {}).get(
                    "min_interval_seconds", ImportWebSettings.min_interval_seconds
                )
            )
        ),
    )


def _threshold(value) -> float:
    if value is None:
        return DEFAULT_THRESHOLD
    value = float(value)
    if not 0 < value <= 1:
        raise ValueError(f"[issues] anchor_threshold must be between 0 and 1, got {value}")
    return value
