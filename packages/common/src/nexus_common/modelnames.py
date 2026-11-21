"""The real models behind Nexus's model aliases (nexus-nano, nexus-mini, nexus-pro, nexus-max),
named the way people know them ("GPT-5.4 mini", "Claude Sonnet 5.5"), so the app never shows
an alias without saying what it is. LiteLLM's /model/info is the source of truth: swapping a
model in infra/litellm/config.yaml changes what the app shows with no code change."""

import json
import logging
import re
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from nexus_common.litellm import LiteLLMAdmin

log = logging.getLogger(__name__)

NAMES_TTL_S = 300
NAMES_KEY = "nexus:cache:model-names"
# Chat-facing aliases, cheapest first. Fallback aliases (nexus-pro-fallback) are left out.
TIERS = ("nexus-nano", "nexus-mini", "nexus-pro", "nexus-max")

# Provider prefixes LiteLLM uses, and how each provider writes its own model names.
_PROVIDERS = {
    "openai": "OpenAI",
    "azure": "OpenAI",
    "anthropic": "Anthropic",
    "bedrock": "Anthropic",
    "vertex_ai": "Google",
    "gemini": "Google",
    "ollama": "local (Ollama)",
    "ollama_chat": "local (Ollama)",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "mistral": "Mistral",
    "xai": "xAI",
    "groq": "Groq",
    "together_ai": "Together",
    "fireworks_ai": "Fireworks",
}
_WORDS = {"gpt": "GPT", "oss": "OSS", "o": "o"}
_DATE = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def provider(model: str) -> str:
    """The company or runtime behind a LiteLLM model string, e.g. "openai/gpt-5.4" -> OpenAI."""
    prefix, _, rest = model.partition("/")
    if not rest:
        if model.startswith(("gpt-", "o1", "o3", "o4")):
            return "OpenAI"
        if model.startswith("claude"):
            return "Anthropic"
        if model.startswith("gemini"):
            return "Google"
        return ""
    return _PROVIDERS.get(prefix, prefix)


def label(model: str) -> str:
    """A model id as people write it: openai/gpt-5.4-mini -> GPT-5.4 mini,
    anthropic/claude-sonnet-5-5 -> Claude Sonnet 5.5, gemini/gemini-3.8-flash -> Gemini 3.8
    Flash, ollama/qwen3.5:9b -> qwen3.5:9b. Unknown shapes come back as they are."""
    name = model.rsplit("/", 1)[-1]
    name = _DATE.sub("", name)
    if ":" in name:  # Ollama tags are already how people refer to them.
        return name
    if name.startswith("gpt-"):
        # gpt-5.4-mini -> GPT-5.4 mini; gpt-oss-120b -> GPT-OSS 120b
        parts = name.split("-")
        head = "GPT-" + parts[1].upper() if parts[1] == "oss" else "GPT-" + parts[1]
        return " ".join([head, *parts[2:]])
    if name.startswith("claude-"):
        # claude-sonnet-5-5 -> Claude Sonnet 5.5; claude-3-5-haiku -> Claude 3.5 Haiku
        parts = name.split("-")[1:]
        out: list[str] = []
        for p in parts:
            if p.isdigit() and out and out[-1].replace(".", "").isdigit():
                out[-1] += "." + p
            else:
                out.append(p if p.replace(".", "").isdigit() else p.capitalize())
        return "Claude " + " ".join(out)
    if name.startswith("gemini-"):
        parts = name.split("-")[1:]
        return "Gemini " + " ".join(p if p[0].isdigit() else p.capitalize() for p in parts if p)
    return name


def names_from_model_info(data: dict) -> dict[str, dict[str, str]]:
    """LiteLLM's /model/info answer as {alias: {"model", "label", "provider"}}. An alias
    with fallbacks lists one entry per deployment; the first one is the one named."""
    out: dict[str, dict[str, str]] = {}
    for row in data.get("data") or []:
        alias = row.get("model_name")
        model = (row.get("litellm_params") or {}).get("model")
        if not alias or not model or alias in out:
            continue
        out[alias] = {
            "model": model.rsplit("/", 1)[-1] if "/" in model else model,
            "label": label(model),
            "provider": provider(model),
        }
    return out


async def cached_names(redis: "Redis", gateway: "LiteLLMAdmin") -> dict[str, dict[str, str]]:
    """The real model behind each alias, at most five minutes old. Empty when LiteLLM can't be
    reached; callers then show the aliases."""
    try:
        raw = await redis.get(NAMES_KEY)
        if raw is not None:
            return json.loads(raw)
    except RedisError as exc:
        log.warning("Model name cache unavailable: %s", exc)
    info = await gateway.model_info()
    if info is None:
        return {}
    names = names_from_model_info(info)
    try:
        await redis.set(NAMES_KEY, json.dumps(names), ex=NAMES_TTL_S)
    except RedisError:
        pass
    return names


def describe(names: dict[str, dict[str, str]], chat_alias: str) -> str:
    """A few lines for the assistant's prompt so it can say which model it runs on. Empty when
    the names are unknown, so the assistant never guesses."""
    me = names.get(chat_alias)
    if not me:
        return ""
    lineup = [f"{names[t]['label']} ({names[t]['provider']})" for t in TIERS if t in names]
    fallbacks = [names[f"{t}-fallback"]["label"] for t in TIERS if f"{t}-fallback" in names]
    lines = [
        "## Models",
        f"You are answering on {me['label']} by {me['provider']}, through Nexus's LiteLLM "
        "gateway. When asked which model you are, say so plainly.",
        "Team agents are given one of these, cheapest first: " + ", ".join(lineup) + ".",
    ]
    if fallbacks:
        lines.append(
            "If a provider fails, LiteLLM answers with the OpenAI fallback instead ("
            + ", ".join(fallbacks)
            + "); the footer under each reply names the model that answered."
        )
    return "\n".join(lines)
