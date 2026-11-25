"""Builds chat models from "provider:model" strings via LangChain's init_chat_model, so switching
provider is a config change. Services ask this module for a model and never import a provider
package themselves."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from learnova.config import ModelSettings
from learnova.home import home_dir

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

# Provider name as init_chat_model knows it -> the env var its key is normally in.
DEFAULT_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
ALIASES = {"google": "google_genai", "gemini": "google_genai", "google-genai": "google_genai"}
NOT_YET = {"ollama": "Ollama support is planned but not added yet."}


class LLMConfigError(Exception):
    """The model string, config or API key is missing or wrong."""


class LLMCallError(Exception):
    """The provider returned an error or couldn't be reached."""


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def _provider(name: str) -> str:
    name = name.strip().lower()
    return ALIASES.get(name, name)


def parse_spec(spec: str) -> ModelSpec:
    provider, sep, model = spec.strip().partition(":")
    if not sep or not provider or not model:
        raise LLMConfigError(f"Model {spec!r} should look like 'provider:model', e.g. 'openai:…'.")
    provider = _provider(provider)
    if provider in NOT_YET:
        raise LLMConfigError(NOT_YET[provider])
    if provider not in DEFAULT_KEY_ENV:
        known = ", ".join(DEFAULT_KEY_ENV)
        raise LLMConfigError(f"Unknown provider {provider!r}. Supported: {known}.")
    return ModelSpec(provider, model.strip())


def chat_model(spec: str, settings: ModelSettings, **kwargs) -> BaseChatModel:
    """A ready-to-use chat model. The key is read from the env var named in config (or the
    provider's standard one) and passed in explicitly."""
    parsed = parse_spec(spec)
    overrides = {_provider(p): env for p, env in settings.api_key_env.items()}
    env = overrides.get(parsed.provider, DEFAULT_KEY_ENV[parsed.provider])
    key = os.environ.get(env)
    if not key:
        raise LLMConfigError(
            f"{env} is not set (needed for {parsed.provider}). Add it to .env and run with "
            "`uv run --env-file .env …`, or name another variable under [models.api_key_env] "
            f"in {home_dir() / 'config.toml'}."
        )
    # Imported here: LangChain takes ~1s to load, and most commands never need a model.
    from langchain.chat_models import init_chat_model

    return init_chat_model(parsed.model, model_provider=parsed.provider, api_key=key, **kwargs)


def main_model(settings: ModelSettings) -> BaseChatModel:
    return chat_model(_required(settings.main, "main"), settings)


def vision_model(settings: ModelSettings) -> BaseChatModel:
    return chat_model(_required(settings.vision, "vision"), settings)


def _required(spec: str | None, name: str) -> str:
    if not spec:
        raise LLMConfigError(
            f'No {name} model configured. Add `{name} = "provider:model"` under [models] in '
            f"{home_dir() / 'config.toml'}."
        )
    return spec


@dataclass(frozen=True)
class Answer:
    text: str
    seconds: float
    input_tokens: int | None
    output_tokens: int | None


def ask(model: BaseChatModel, question: str) -> Answer:
    """Send one question and time it. Provider errors become LLMCallError with a short message."""
    start = time.perf_counter()
    try:
        reply = model.invoke(question)
    except Exception as e:  # each provider SDK has its own exception types
        message = str(e).strip().splitlines()[0][:300] if str(e).strip() else ""
        raise LLMCallError(f"{type(e).__name__}: {message}") from e
    usage = reply.usage_metadata or {}
    return Answer(
        text=str(reply.text),
        seconds=time.perf_counter() - start,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )
