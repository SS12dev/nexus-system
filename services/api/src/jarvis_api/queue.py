import uuid
from typing import Protocol

from arq import ArqRedis


class TurnQueue(Protocol):
    async def enqueue_turn(self, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None: ...


class ArqTurnQueue:
    """Hands a Jarvis turn to the orchestrator workers through the arq Redis queue."""

    def __init__(self, pool: ArqRedis):
        self.pool = pool

    async def enqueue_turn(self, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None:
        await self.pool.enqueue_job(
            "run_turn",
            str(chat_id),
            str(assistant_message_id),
            _job_id=f"turn:{assistant_message_id}",
        )
