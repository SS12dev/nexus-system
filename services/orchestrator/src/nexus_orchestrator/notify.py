"""Emails about access requests, sent from a job so a slow or down mail server never holds up
the person asking or the admin deciding. Transient failures are retried with backoff; the job
re-reads the request each time, so an email about a request that has since been decided is
dropped rather than sent late."""

import logging
import uuid
from email.message import EmailMessage

from arq import Retry

from nexus_common import mail
from nexus_common.access import deciders
from nexus_common.models import AccessRequest, AccessRequestStatus
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

MAX_TRIES = 5
APP = "Nexus"


def _who(req: AccessRequest) -> str:
    return f"{req.name} <{req.email}>" if req.name else req.email


def requested_email(settings: Settings, req: AccessRequest, to: list[str]) -> EmailMessage:
    note = (req.note or "").strip() or "(no note)"
    body = (
        f"{_who(req)} asked for access to {APP}.\n\n"
        f"Their note:\n{note}\n\n"
        f"Approve or deny it on the People page:\n{settings.nexus_url.rstrip('/')}/people\n\n"
        f"Nothing happens until you decide. You get this email because you are an owner or "
        f"admin of {APP}."
    )
    return mail.message(settings, to, f"{req.name or req.email} asked to join {APP}", body)


def approved_email(settings: Settings, req: AccessRequest) -> EmailMessage:
    body = (
        f"Hi{' ' + req.name.split()[0] if req.name else ''},\n\n"
        f"Your request to join {APP} was approved. Sign in with Google as {req.email}:\n"
        f"{settings.nexus_url.rstrip('/')}/login\n\n"
        f"The first time you sign in you'll name your assistant, and you're ready to go."
    )
    return mail.message(settings, [req.email], f"You're in: your {APP} access was approved", body)


async def access(deps, kind: str, request_id: uuid.UUID, *, attempt: int = 1) -> None:
    """kind is "requested" (tell the deciders) or "approved" (tell the person)."""
    settings: Settings = deps.settings
    fields = {"kind": kind, "access_request_id": str(request_id), "attempt": attempt}
    if not settings.smtp_enabled:
        log.info("access.notify_skipped: SMTP is not configured", extra=fields)
        return
    async with deps.sessionmaker() as db:
        req = await db.get(AccessRequest, request_id)
        if req is None:
            return
        if kind == "requested" and req.status == AccessRequestStatus.pending:
            to = settings.access_request_notify_list or sorted(u.email for u in await deciders(db))
            msg = requested_email(settings, req, to) if to else None
        elif kind == "approved" and req.status == AccessRequestStatus.approved:
            msg = approved_email(settings, req)
        else:
            log.info("access.notify_dropped: request no longer %s", kind, extra=fields)
            return
    if msg is None:
        log.warning("access.notify_skipped: nobody to tell", extra=fields)
        return
    try:
        await mail.send(settings, msg)
    except mail.MailError as exc:
        if exc.transient and attempt < MAX_TRIES:
            delay = 30 * 2 ** (attempt - 1)
            log.warning("access.notify_retry in %ds: %s", delay, exc, extra=fields)
            raise Retry(defer=delay) from exc
        log.error("access.notify_failed: %s", exc, extra=fields)
        return
    log.info("access.notified", extra={**fields, "recipients": len(msg["To"].split(","))})
