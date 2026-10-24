import json
import re

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from nexus_common import logs
from nexus_common.models import RoleName, User
from nexus_common.settings import Settings


def chat_model(settings: Settings, *, model: str, api_key: str, metadata: dict) -> BaseChatModel:
    """A chat model bound to one turn. Calls go to LiteLLM under a nexus-* alias; the metadata
    lets LiteLLM's Langfuse callback group generations into a trace per turn and a session per
    chat, attributed to the user. The request id lets a Langfuse trace be found in the logs."""
    if request_id := logs.get("request_id"):
        metadata = {**metadata, "request_id": request_id}
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


def private(settings: Settings, user: User) -> dict:
    """Metadata keys that make LiteLLM's Langfuse callback store "redacted-by-litellm" in place
    of the prompt and reply, for everyone but owners while TRACE_MASKING is on. Tokens, cost,
    timing and the trace's user id are kept, so usage and the self-review still work."""
    if settings.trace_masking and user.role != RoleName.owner:
        return {"mask_input": True, "mask_output": True}
    return {}


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}
