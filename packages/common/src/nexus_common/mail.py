"""Email from Nexus's own account over SMTP (SMTP_* in .env), for agents' send_email and for
notices such as access requests. smtplib blocks, so sending runs in a thread.

Bodies are written in Markdown (that's what models write), so every email goes out as HTML
with a plain-text alternative: `set_body` does that for this module and for Gmail."""

import asyncio
import re
import smtplib
from email.message import EmailMessage

from markdown_it import MarkdownIt

from nexus_common.settings import Settings

# Raw HTML in a body is shown as text, never rendered: bodies come from models and web pages.
# `breaks` keeps single line breaks, which is how people (and models) lay out short emails.
_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True}).enable(
    ["table", "strikethrough", "linkify"]
)

# Mail clients ignore or strip <style>, so the look is inline on each tag.
_STYLES = {
    "a": "color:#2563eb",
    "h1": "font-size:20px;margin:20px 0 8px",
    "h2": "font-size:18px;margin:18px 0 8px",
    "h3": "font-size:16px;margin:16px 0 6px",
    "p": "margin:0 0 12px",
    "ul": "margin:0 0 12px;padding-left:24px",
    "ol": "margin:0 0 12px;padding-left:24px",
    "li": "margin:0 0 8px",
    "blockquote": "margin:0 0 12px;padding-left:12px;border-left:3px solid #d1d5db;color:#4b5563",
    "code": "font-family:Menlo,Consolas,monospace;font-size:13px;background:#f3f4f6;"
    "padding:1px 4px;border-radius:4px",
    "pre": "background:#f3f4f6;padding:12px;border-radius:6px;overflow-x:auto;margin:0 0 12px",
    "table": "border-collapse:collapse;margin:0 0 12px",
    "th": "border:1px solid #d1d5db;padding:6px 10px;background:#f9fafb;text-align:left",
    "td": "border:1px solid #d1d5db;padding:6px 10px",
    "hr": "border:0;border-top:1px solid #e5e7eb;margin:20px 0",
}
_WRAP = (
    '<!DOCTYPE html><html><body style="margin:0;padding:0">'
    "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
    "Arial,sans-serif;font-size:15px;line-height:1.55;color:#111827;max-width:680px;"
    'padding:8px 4px">{}</div></body></html>'
)


def to_html(body: str) -> str:
    """The email's HTML part: `body` rendered as Markdown, styled inline."""
    tokens = _md.parse(body)
    for tok in tokens:
        _style(tok)
        for child in tok.children or []:
            _style(child)
    return _WRAP.format(_md.renderer.render(tokens, _md.options, {}))


def _style(tok) -> None:
    if tok.nesting == -1 or tok.tag not in _STYLES:
        return
    extra = _STYLES[tok.tag]
    if tok.tag == "code" and tok.type == "fence":
        extra = "font-family:Menlo,Consolas,monospace;font-size:13px"
    # Table cells already carry their column's text-align; ours goes first so theirs wins.
    own = tok.attrGet("style")
    tok.attrSet("style", f"{extra};{own}" if own else extra)


_LINK = re.compile(r"!?\[([^\]]+)\]\((\S+?)\)")
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_HEADING = re.compile(r"^#{1,6}\s+", re.M)


def to_text(body: str) -> str:
    """The plain-text part: the Markdown with its emphasis and heading marks taken out and
    links written as "text (url)", so it reads cleanly where HTML isn't shown."""
    text = _LINK.sub(lambda m: m[1] if m[1] == m[2] else f"{m[1]} ({m[2]})", body)
    text = _STRONG.sub(r"\2", text)
    return _HEADING.sub("", text)


def set_body(msg: EmailMessage, body: str) -> None:
    """Give `msg` a plain-text part and an HTML alternative, both from Markdown `body`.
    Attachments can be added afterwards."""
    msg.set_content(to_text(body))
    msg.add_alternative(to_html(body), subtype="html")


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
    set_body(msg, body)
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
