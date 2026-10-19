from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from jarvis_common.settings import Settings


def chat_model(settings: Settings, *, model: str, api_key: str, metadata: dict) -> BaseChatModel:
    """A chat model bound to one turn. Calls go to LiteLLM under a jarvis-* alias; the metadata
    lets LiteLLM's Langfuse callback group generations into a trace per turn and a session per
    chat, attributed to the user."""
    return ChatOpenAI(
        model=model,
        base_url=f"{settings.litellm_base_url.rstrip('/')}/v1",
        api_key=api_key or "unset",
        streaming=True,
        stream_usage=True,
        max_retries=2,
        timeout=120,
        extra_body={"metadata": metadata},
    )
