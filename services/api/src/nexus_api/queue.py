import uuid
from typing import Protocol

from arq import ArqRedis

from nexus_common.queues import DOCS_QUEUE


class JobQueue(Protocol):
    async def enqueue_turn(self, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None: ...
    async def enqueue_parse(self, file_id: uuid.UUID) -> None: ...
    async def enqueue_profile_rebuild(self, user_id: uuid.UUID) -> None: ...
    async def enqueue_run(self, function: str, run_id: uuid.UUID) -> None: ...
    async def enqueue_deploy(self, project_id: uuid.UUID) -> None: ...
    async def enqueue_link_preview(self, link_id: uuid.UUID) -> None: ...
    async def enqueue_access_notice(self, kind: str, request_id: uuid.UUID) -> None: ...


class ArqJobQueue:
    """Hands work to the orchestrator and doc-worker through the arq Redis queues."""

    def __init__(self, pool: ArqRedis):
        self.pool = pool

    async def enqueue_turn(self, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None:
        await self.pool.enqueue_job(
            "run_turn",
            str(chat_id),
            str(assistant_message_id),
            _job_id=f"turn:{assistant_message_id}",
        )

    async def enqueue_parse(self, file_id: uuid.UUID) -> None:
        await self.pool.enqueue_job(
            "parse_file", str(file_id), _job_id=f"parse:{file_id}", _queue_name=DOCS_QUEUE
        )

    async def enqueue_profile_rebuild(self, user_id: uuid.UUID) -> None:
        # No fixed job id: arq keeps finished job ids around, which would block a second rebuild.
        await self.pool.enqueue_job("rebuild_profile", str(user_id))

    async def enqueue_run(self, function: str, run_id: uuid.UUID) -> None:
        """run_system or continue_run. One job per run at a time (see the orchestrator)."""
        await self.pool.enqueue_job(function, str(run_id), _job_id=f"run:{run_id}")

    async def enqueue_deploy(self, project_id: uuid.UUID) -> None:
        """A redeploy from the Apps page; one at a time per project."""
        await self.pool.enqueue_job("deploy_app", str(project_id), _job_id=f"deploy:{project_id}")

    async def enqueue_link_preview(self, link_id: uuid.UUID) -> None:
        """Fetch a link's preview in the orchestrator, which can read Nexus's private repos."""
        await self.pool.enqueue_job("link_preview", str(link_id))

    async def enqueue_access_notice(self, kind: str, request_id: uuid.UUID) -> None:
        """Email about an access request: "requested" to the deciders, "approved" to the person."""
        await self.pool.enqueue_job(
            "notify_access", kind, str(request_id), _job_id=f"access:{kind}:{request_id}"
        )
