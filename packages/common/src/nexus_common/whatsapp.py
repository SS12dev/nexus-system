"""WhatsApp notices through Meta's WhatsApp Business Cloud API.

A business may only start a conversation with a template Meta has approved, so every notice is
the template in WHATSAPP_TEMPLATE with two body parameters: the routine's name and a one-line
preview of what it found. Meta refuses parameters with newlines, tabs or long runs of spaces,
so the preview is flattened to one short line first.
"""

import re

import httpx

from nexus_common.settings import Settings

PREVIEW_CHARS = 180
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")


class WhatsAppError(Exception):
    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


def one_line(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Markdown flattened to a single line Meta accepts as a template parameter."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links: keep the label
    text = re.sub(r"[*_`#>|~]+", "", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.M)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text or "-"


def template_body(settings: Settings, phone: str, params: list[str]) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone.lstrip("+"),
        "type": "template",
        "template": {
            "name": settings.whatsapp_template,
            "language": {"code": settings.whatsapp_template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": one_line(p, 900)} for p in params],
                }
            ],
        },
    }


async def send_template(
    settings: Settings, phone: str, params: list[str], http: httpx.AsyncClient | None = None
) -> str:
    """Send the notice template to `phone` (E.164). Returns Meta's message id."""
    if not settings.whatsapp_enabled:
        raise WhatsAppError("WhatsApp is not configured", transient=False)
    if not PHONE.match(phone or ""):
        raise WhatsAppError(f"{phone!r} isn't a phone number in +<country><number> form", False)
    url = f"{settings.whatsapp_api_url.rstrip('/')}/{settings.whatsapp_phone_number_id}/messages"
    client = http or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.post(
            url,
            json=template_body(settings, phone, params),
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        )
    except httpx.HTTPError as exc:
        raise WhatsAppError(f"WhatsApp unreachable: {exc}") from exc
    finally:
        if http is None:
            await client.aclose()
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {})
            detail = f"{err.get('message', '')} (code {err.get('code')})"
        except ValueError:
            detail = resp.text[:200]
        raise WhatsAppError(
            f"WhatsApp refused the message: {detail}", transient=resp.status_code >= 500
        )
    messages = resp.json().get("messages") or [{}]
    return str(messages[0].get("id", ""))
