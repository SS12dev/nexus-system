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
from nexus_common.models import (
    AccessRequest,
    AccessRequestStatus,
    AccountRecoveryRequest,
    AccountRecoveryStatus,
    Feedback,
    RoleName,
    User,
    UserStatus,
)
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
    return mail.message(
        settings, to, f"{req.name or req.email} asked to join {APP}", body, reply_to=req.email
    )


def approved_email(settings: Settings, req: AccessRequest) -> EmailMessage:
    body = (
        f"Hi{' ' + req.name.split()[0] if req.name else ''},\n\n"
        f"Your request to join {APP} was approved. Sign in with Google as {req.email}:\n"
        f"{settings.nexus_url.rstrip('/')}/login\n\n"
        f"The first time you sign in you'll name your assistant and answer a few quick "
        f"questions (two minutes, all skippable) so it can set itself up for you.\n\n"
        f"Google will say the app isn't verified, because it's a private app: choose "
        f"Advanced, then Go to {APP}."
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


async def account_recovery(deps, kind: str, request_id: uuid.UUID, *, attempt: int = 1) -> None:
    """Email admins about a recovery request, or the person after approval."""
    settings: Settings = deps.settings
    fields = {"kind": kind, "request_id": str(request_id), "attempt": attempt}
    if not settings.smtp_enabled:
        log.info("account_recovery.notify_skipped: SMTP is not configured", extra=fields)
        return
    async with deps.sessionmaker() as db:
        req = await db.get(AccountRecoveryRequest, request_id)
        if req is None:
            return
        if kind == "requested" and req.status == AccountRecoveryStatus.pending:
            recipients = settings.access_request_notify_list or sorted(
                user.email for user in await deciders(db)
            )
            body = (
                f"{req.name or req.email} requested recovery of a deleted {APP} account.\n\n"
                f"Review or deny the request on the People page:\n"
                f"{settings.nexus_url.rstrip('/')}/people\n\n"
                "The account is retained for 30 days after deletion; recovery requires admin "
                "approval."
            )
            msg = (
                mail.message(
                    settings,
                    recipients,
                    f"{req.name or req.email} requested account recovery",
                    body,
                    reply_to=req.email,
                )
                if recipients
                else None
            )
        elif kind == "approved" and req.status == AccountRecoveryStatus.approved:
            body = (
                f"Hi{' ' + req.name.split()[0] if req.name else ''},\n\n"
                f"Your {APP} account recovery request was approved. You can sign in again here:\n"
                f"{settings.nexus_url.rstrip('/')}/login"
            )
            msg = mail.message(settings, [req.email], f"Your {APP} account was restored", body)
        else:
            log.info("account_recovery.notify_dropped", extra=fields)
            return
    if msg is None:
        log.warning("account_recovery.notify_skipped: nobody to tell", extra=fields)
        return
    try:
        await mail.send(settings, msg)
    except mail.MailError as exc:
        if exc.transient and attempt < MAX_TRIES:
            delay = 30 * 2 ** (attempt - 1)
            log.warning("account_recovery.notify_retry in %ds: %s", delay, exc, extra=fields)
            raise Retry(defer=delay) from exc
        log.error("account_recovery.notify_failed: %s", exc, extra=fields)
        return
    log.info("account_recovery.notified", extra={**fields, "recipients": len(msg["To"].split(","))})


async def invite(deps, user_id: uuid.UUID, *, attempt: int = 1) -> None:
    """Email a newly invited person; the job drops the message if they already joined."""
    settings: Settings = deps.settings
    fields = {"user_id": str(user_id), "attempt": attempt}
    if not settings.smtp_enabled:
        log.info("invite.notify_skipped: SMTP is not configured", extra=fields)
        return
    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.invited:
            log.info("invite.notify_dropped: invitation is no longer pending", extra=fields)
            return
        inviter = await db.get(User, user.invited_by) if user.invited_by else None
        inviter_name = inviter.name if inviter else None
        name = user.name
        email = user.email
    body = (
        f"Hi{' ' + name.split()[0] if name else ''},\n\n"
        f"{inviter_name or 'An administrator'} invited you to {APP}. Sign in with Google as "
        f"{email} here:\n{settings.nexus_url.rstrip('/')}/login\n\n"
        "If you weren't expecting this invitation, you can ignore this message."
    )
    msg = mail.message(settings, [email], f"You're invited to {APP}", body)
    try:
        await mail.send(settings, msg)
    except mail.MailError as exc:
        if exc.transient and attempt < MAX_TRIES:
            delay = 30 * 2 ** (attempt - 1)
            log.warning("invite.notify_retry in %ds: %s", delay, exc, extra=fields)
            raise Retry(defer=delay) from exc
        log.error("invite.notify_failed: %s", exc, extra=fields)
        return
    log.info("invite.notified", extra=fields)


FEEDBACK_KIND = {"problem": "a problem", "idea": "an idea", "praise": "some praise"}


def _shots_line(n: int) -> str:
    if not n:
        return ""
    return f"{n} screenshot{'s' if n > 1 else ''} attached.\n\n"


async def feedback(deps, feedback_id: uuid.UUID, *, attempt: int = 1) -> None:
    """Email active owners about feedback someone sent from the app."""
    settings: Settings = deps.settings
    fields = {"feedback_id": str(feedback_id), "attempt": attempt}
    if not settings.smtp_enabled:
        log.info("feedback.notify_skipped: SMTP is not configured", extra=fields)
        return
    async with deps.sessionmaker() as db:
        f = await db.get(Feedback, feedback_id)
        if f is None:
            return
        sender = await db.get(User, f.user_id) if f.user_id else None
        to = sorted(u.email for u in await deciders(db) if u.role == RoleName.owner)
    if not to:
        return
    who = (
        f"{sender.name} <{sender.email}>"
        if sender and sender.name
        else (sender.email if sender else "Someone")
    )
    body = (
        f"{who} sent {FEEDBACK_KIND.get(f.kind, 'feedback')}"
        f"{f' from {f.page}' if f.page else ''}:\n\n{f.message}\n\n"
        f"{_shots_line(len(f.screenshots or []))}"
        f"All feedback: {settings.nexus_url.rstrip('/')}/admin?tab=feedback"
    )
    first = (f.message.splitlines() or [""])[0][:60]

    msg = mail.message(
        settings,
        to,
        f"Nexus feedback: {first}",
        body,
        reply_to=sender.email if sender else None,
    )
    # Screenshots go along as attachments; one that can't be read is left out, not fatal.
    for shot in f.screenshots or []:
        if deps.storage is None:
            break
        try:
            data = await deps.storage.get(shot["key"])
        except Exception:
            log.warning("feedback.screenshot_missing", extra={**fields, "key": shot["key"]})
            continue
        maintype, _, subtype = shot["content_type"].partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=shot["name"])

    try:
        await mail.send(settings, msg)
    except mail.MailError as exc:
        if exc.transient and attempt < MAX_TRIES:
            raise Retry(defer=30 * 2 ** (attempt - 1)) from exc
        log.error("feedback.notify_failed: %s", exc, extra=fields)
        return
    log.info("feedback.notified", extra=fields)
