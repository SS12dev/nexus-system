"""Email from Nexus's own account over SMTP (SMTP_* in .env), for agents' send_email and for
notices such as access requests. smtplib blocks, so sending runs in a thread."""

import asyncio
import smtplib
from email.message import EmailMessage

from nexus_common.settings import Settings


class MailError(Exception):
    """Sending failed. `transient` is true when trying again later may work (server busy,
    connection dropped); false when the message or the account is the problem."""

    def __init__(self, message: str, *, transient: bool):
        super().__init__(message)
        self.transient = transient


def _header(value: str) -> str:
    # Values such as a person's name end up in headers; a line break there would add headers.
    return " ".join(value.split())


def message(settings: Settings, to: list[str], subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_username
    msg["To"] = ", ".join(to)
    msg["Subject"] = _header(subject)
    msg.set_content(body)
    return msg


def _send(settings: Settings, msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


async def send(settings: Settings, msg: EmailMessage) -> None:
    """Send `msg`, raising MailError if it didn't go."""
    if not settings.smtp_enabled:
        raise MailError("SMTP is not configured", transient=False)
    try:
        await asyncio.to_thread(_send, settings, msg)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as exc:
        raise MailError(f"{exc.__class__.__name__}: {str(exc)[:200]}", transient=False) from exc
    except smtplib.SMTPResponseException as exc:
        # 4xx replies are "try again later"; 5xx are permanent.
        raise MailError(
            f"{exc.__class__.__name__}: {str(exc)[:200]}", transient=400 <= exc.smtp_code < 500
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"{exc.__class__.__name__}: {str(exc)[:200]}", transient=True) from exc
