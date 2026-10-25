"""Events streamed to the web app. The orchestrator publishes them on a per-user Redis channel and
every API replica holding a WebSocket for that user forwards them unchanged."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from redis.asyncio import Redis

EventType = Literal[
    "assistant.state",
    "message.delta",
    "message.completed",
    "message.failed",
    "chat.updated",
    "tool.call",
    "tool.done",
    "file.updated",
    "file.deleted",
    # Links in the library (nexus_common.links).
    "link.updated",
    "link.deleted",
    "memory.updated",
    # Which memories and entities a turn recalled, for the chat and orb to show.
    "memory.recalled",
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
    # Sent to owners and admins: how many access requests are waiting.
    "access.pending",
    # A routine was added, changed or ran (data: routine, and status after a run).
    "routine.updated",
    # A notice for the main screen's toasts and bell (nexus_common.notices), and the tray
    # being read on another device.
    "notification.created",
    "notification.read",
]

AssistantState = Literal["idle", "thinking", "speaking"]


class Event(BaseModel):
    type: EventType
    chat_id: uuid.UUID | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"nexus:user:{user_id}"


async def publish(redis: Redis, user_id: uuid.UUID | str, event: Event) -> None:
    await redis.publish(user_channel(user_id), event.model_dump_json())
