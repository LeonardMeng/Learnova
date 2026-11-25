"""Model factory and `learnova ask`, with LangChain's fake chat model: no network, keys or cost."""

import langchain.chat_models
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from learnova.cli.main import app
from learnova.config import ModelSettings, load_settings
from learnova.home import ensure_home
from learnova.llm import factory
from learnova.llm.factory import LLMCallError, LLMConfigError, ModelSpec, parse_spec


@pytest.fixture
def captured(monkeypatch):
    """Replace init_chat_model; record what it was called with and return a fake model."""
    calls = []

    def fake_init(model, model_provider, **kwargs):
        calls.append({"model": model, "provider": model_provider, **kwargs})
        reply = AIMessage(
            content=f"hello from {model_provider}",
            usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        )
        return GenericFakeChatModel(messages=iter([reply]))

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init)
    return calls


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("openai:gpt-6.1-terra", ModelSpec("openai", "gpt-6.1-terra")),
        ("Anthropic:some-model", ModelSpec("anthropic", "some-model")),
        ("google:gemini-x", ModelSpec("google_genai", "gemini-x")),
        ("gemini:gemini-x", ModelSpec("google_genai", "gemini-x")),
        ("deepseek:deepseek-flash", ModelSpec("deepseek", "deepseek-flash")),
    ],
)
def test_parse_spec(spec, expected):
    assert parse_spec(spec) == expected


@pytest.mark.parametrize(
    "spec, message",
    [
        ("gpt-6.1-terra", "provider:model"),
        ("openai:", "provider:model"),
        ("mistral:large", "Unknown provider"),
        ("ollama:llama", "not added yet"),
    ],
)
def test_parse_spec_errors(spec, message):
    with pytest.raises(LLMConfigError, match=message):
        parse_spec(spec)


def test_key_comes_from_configured_env_var(captured, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ModelSettings(main="openai:gpt-6.1-terra", api_key_env={"openai": "MODEL_API_KEY"})
    factory.main_model(settings)
    assert captured == [{"model": "gpt-6.1-terra", "provider": "openai", "api_key": "sk-test"}]


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY is not set"):
        factory.chat_model("anthropic:x", ModelSettings())
    monkeypatch.delenv("MY_GEMINI", raising=False)
    with pytest.raises(LLMConfigError, match="MY_GEMINI is not set"):
        factory.chat_model("gemini:x", ModelSettings(api_key_env={"google": "MY_GEMINI"}))


def test_unconfigured_model():
    with pytest.raises(LLMConfigError, match="No vision model configured"):
        factory.vision_model(ModelSettings(main="openai:x"))


def test_provider_errors_become_llm_call_error():
    class Boom(GenericFakeChatModel):
        def invoke(self, *args, **kwargs):
            raise RuntimeError("Error code: 401 - invalid api key\nmore detail")

    with pytest.raises(LLMCallError, match="RuntimeError: Error code: 401 - invalid api key$"):
        factory.ask(Boom(messages=iter([])), "hi")


def test_models_section_in_config(learnova_home):
    ensure_home()
    (learnova_home / "config.toml").write_text(
        '[models]\nmain = "openai:a"\nvision = "google:b"\n'
        '[models.api_key_env]\nopenai = "MODEL_API_KEY"\n'
    )
    models = load_settings().models
    assert (models.main, models.vision) == ("openai:a", "google:b")
    assert models.api_key_env == {"openai": "MODEL_API_KEY"}


def test_ask_command(captured, monkeypatch, learnova_home):
    ensure_home()
    (learnova_home / "config.toml").write_text(
        '[models]\nmain = "openai:gpt-6.1-terra"\nvision = "anthropic:v"\n'
        '[models.api_key_env]\nopenai = "MODEL_API_KEY"\n'
    )
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    runner = CliRunner()

    r = runner.invoke(app, ["ask", "hello"])
    assert r.exit_code == 0, r.output
    assert "hello from openai" in r.output
    assert "openai:gpt-6.1-terra · " in r.output and "3 in / 4 out tokens" in r.output

    r = runner.invoke(app, ["ask", "hello", "--vision"])
    assert "hello from anthropic" in r.output

    r = runner.invoke(app, ["ask", "hello", "--model", "ollama:llama"])
    assert r.exit_code == 1 and "not added yet" in r.output


def test_ask_without_models_section_explains_what_to_add():
    r = CliRunner().invoke(app, ["ask", "hello"])
    assert r.exit_code == 1
    assert "No main model configured" in r.output
