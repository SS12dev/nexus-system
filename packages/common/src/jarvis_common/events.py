"""Events streamed to the web app. The orchestrator publishes them on a per-user Redis channel and
every API replica holding a WebSocket for that user forwards them unchanged."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from redis.asyncio import Redis

EventType = Literal[
    "jarvis.state",
    "message.delta",
    "message.completed",
    "message.failed",
    "chat.updated",
    "tool.call",
    "tool.done",
    "file.updated",
    "file.deleted",
    "memory.updated",
    "profile.updated",
    # Agent systems. system.* carry the nodes and edges the UI draws.
    "system.created",
    "system.updated",
    "system.deleted",
    "run.updated",
    "agent.status",
    "approval.requested",
    "approval.resolved",
    "artifact.created",
    "skill.updated",
]

JarvisState = Literal["idle", "thinking", "speaking"]


class Event(BaseModel):
    type: EventType
    chat_id: uuid.UUID | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"jarvis:user:{user_id}"


async def publish(redis: Redis, user_id: uuid.UUID | str, event: Event) -> None:
    await redis.publish(user_channel(user_id), event.model_dump_json())
