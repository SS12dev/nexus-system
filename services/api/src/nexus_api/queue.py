import uuid
from typing import Protocol

from arq import ArqRedis

from nexus_common.logs import with_request_id
from nexus_common.queues import DOCS_QUEUE


class JobQueue(Protocol):
    async def enqueue_turn(self, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None: ...
    async def enqueue_parse(self, file_id: uuid.UUID) -> None: ...
    async def enqueue_profile_rebuild(self, user_id: uuid.UUID) -> None: ...
    async def enqueue_run(self, function: str, run_id: uuid.UUID) -> None: ...
    async def enqueue_deploy(self, project_id: uuid.UUID) -> None: ...
    async def enqueue_link_preview(self, link_id: uuid.UUID) -> None: ...
    async def enqueue_access_notice(self, kind: str, request_id: uuid.UUID) -> None: ...
    async def enqueue_review(self, review_id: uuid.UUID) -> None: ...
    async def enqueue_proposal_pr(self, proposal_id: uuid.UUID) -> None: ...
    async def enqueue_recall_check(self, eval_id: uuid.UUID) -> None: ...


class ArqJobQueue:
    """Hands work to the orchestrator and doc-worker through the arq Redis queues. Each job
    carries the request id of the request that queued it, for the logs."""

    def __init__(self, pool: ArqRedis):
        self.pool = pool
        self.enqueue_job = with_request_id(pool.enqueue_job)

    async def enqueue_turn(self, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None:
        await self.enqueue_job(
            "run_turn",
            str(chat_id),
            str(assistant_message_id),
            _job_id=f"turn:{assistant_message_id}",
        )

    async def enqueue_parse(self, file_id: uuid.UUID) -> None:
        await self.enqueue_job(
            "parse_file", str(file_id), _job_id=f"parse:{file_id}", _queue_name=DOCS_QUEUE
        )

    async def enqueue_profile_rebuild(self, user_id: uuid.UUID) -> None:
        # No fixed job id: arq keeps finished job ids around, which would block a second rebuild.
        await self.enqueue_job("rebuild_profile", str(user_id))

    async def enqueue_run(self, function: str, run_id: uuid.UUID) -> None:
        """run_system or continue_run. One job per run at a time (see the orchestrator)."""
        await self.enqueue_job(function, str(run_id), _job_id=f"run:{run_id}")

    async def enqueue_deploy(self, project_id: uuid.UUID) -> None:
        """A redeploy from the Apps page; one at a time per project."""
        await self.enqueue_job("deploy_app", str(project_id), _job_id=f"deploy:{project_id}")

    async def enqueue_link_preview(self, link_id: uuid.UUID) -> None:
        """Fetch a link's preview in the orchestrator, which can read Nexus's private repos."""
        await self.enqueue_job("link_preview", str(link_id))

    async def enqueue_access_notice(self, kind: str, request_id: uuid.UUID) -> None:
        """Email about an access request: "requested" to the deciders, "approved" to the person."""
        await self.enqueue_job(
            "notify_access", kind, str(request_id), _job_id=f"access:{kind}:{request_id}"
        )

    async def enqueue_review(self, review_id: uuid.UUID) -> None:
        """A self-review the owner asked for on the Improvements tab."""
        await self.enqueue_job("self_review", str(review_id), _job_id=f"review:{review_id}")

    async def enqueue_proposal_pr(self, proposal_id: uuid.UUID) -> None:
        """Open the pull request for an approved code or tool proposal."""
        await self.enqueue_job(
            "proposal_pr", str(proposal_id), _job_id=f"proposal-pr:{proposal_id}"
        )

    async def enqueue_recall_check(self, eval_id: uuid.UUID) -> None:
        """A recall check the owner started on the Admin page."""
        await self.enqueue_job("recall_check", str(eval_id), _job_id=f"recall:{eval_id}")
