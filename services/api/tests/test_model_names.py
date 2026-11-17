"""The app names real models, not aliases: LiteLLM's model list becomes labels people know."""

import pytest

from nexus_common.modelnames import label, names_from_model_info, provider


@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/gpt-5.4-nano", "GPT-5.4 nano"),
        ("openai/gpt-5.4", "GPT-5.4"),
        ("gpt-5.5", "GPT-5.5"),
        ("openai/gpt-oss-120b", "GPT-OSS 120b"),
        ("anthropic/claude-sonnet-5-5", "Claude Sonnet 5.5"),
        ("anthropic/claude-opus-5-5-20261101", "Claude Opus 5.5"),
        ("anthropic/claude-3-5-haiku", "Claude 3.5 Haiku"),
        ("gemini/gemini-3.8-flash", "Gemini 3.8 Flash"),
        ("ollama/qwen3.5:9b", "qwen3.5:9b"),
        ("deepseek/deepseek-chat", "deepseek-chat"),
    ],
)
def test_labels(model, expected):
    assert label(model) == expected


def test_providers():
    assert provider("openai/gpt-5.4") == "OpenAI"
    assert provider("anthropic/claude-sonnet-5-5") == "Anthropic"
    assert provider("gemini/gemini-3.8-flash") == "Google"
    assert provider("ollama/bge-m3") == "local (Ollama)"
    assert provider("gpt-5.4") == "OpenAI"


def test_first_deployment_names_an_alias():
    info = {
        "data": [
            {"model_name": "nexus-mini", "litellm_params": {"model": "openai/gpt-5.4-mini"}},
            {"model_name": "nexus-mini", "litellm_params": {"model": "openai/gpt-5.4-nano"}},
            {"model_name": "broken"},
        ]
    }
    assert names_from_model_info(info) == {
        "nexus-mini": {"model": "gpt-5.4-mini", "label": "GPT-5.4 mini", "provider": "OpenAI"}
    }


async def test_me_lists_the_real_models(client_for):
    owner = await client_for("owner@example.com")
    me = (await owner.get("/api/me")).json()
    pro = {"model": "gpt-5.4", "label": "GPT-5.4", "provider": "OpenAI"}
    assert me["models"]["nexus-pro"] == pro
    assert me["models"]["nexus-max"]["label"] == "GPT-5.5"
