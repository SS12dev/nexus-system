import base64
import email
import json
from datetime import timedelta
from email import policy

import httpx
import pytest

from nexus_common.google import SERVICE_SCOPES, encrypt
from nexus_common.models import (
    Chat,
    GoogleGrant,
    Message,
    MessageRole,
    MessageStatus,
    RoleName,
    UserStatus,
    utcnow,
)
from nexus_orchestrator.google_tools import message_body, parse_when
from nexus_orchestrator.tools import ToolContext, build_tools
from nexus_orchestrator.turn import run_turn

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"


async def grant(sessionmaker, settings, user_id, services, *, expired=False):
    async with sessionmaker() as db:
        db.add(
            GoogleGrant(
                user_id=user_id,
                scopes=" ".join(["openid", *(SERVICE_SCOPES[s] for s in services)]),
                refresh_token=encrypt(settings, "refresh-1"),
                access_token=encrypt(settings, "access-1"),
                access_expires_at=utcnow()
                + (timedelta(minutes=-5) if expired else timedelta(hours=1)),
            )
        )
        await db.commit()


class Google:
    """A fake Google: routes by (method, url), records each request."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        handler = self.routes.get((request.method, url))
        if handler is None:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        body = handler(request) if callable(handler) else handler
        return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)

    def sent(self, method: str, url: str) -> httpx.Request:
        return next(r for r in self.requests if r.method == method and str(r.url).startswith(url))


@pytest.fixture
async def user(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        u = await create_user(
            db, email="shivam@example.com", role=RoleName.owner, status=UserStatus.active
        )
        await db.commit()
        return u.id


@pytest.fixture
async def turn(sessionmaker, user):
    """A chat holding one question and a streaming placeholder for the reply."""
    async with sessionmaker() as db:
        chat = Chat(user_id=user, title="t")
        db.add(chat)
        await db.flush()
        db.add(
            Message(
                chat_id=chat.id,
                role=MessageRole.user,
                content="What's on today?",
                status=MessageStatus.complete,
            )
        )
        reply = Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="",
            status=MessageStatus.streaming,
            model="nexus-mini",
            trace_id="t1",
        )
        db.add(reply)
        await db.commit()
        return chat.id, reply.id


def tools_for(deps, user_id, google: Google) -> dict:
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(google))

    async def emit(*args, **kwargs):
        pass

    ctx = ToolContext(deps=deps, user_id=user_id, chat_id=None, emit=emit)
    names = [
        "gmail_search",
        "gmail_read",
        "gmail_draft",
        "gmail_send",
        "gmail_update_labels",
        "calendar_list_events",
        "calendar_create_event",
        "calendar_update_event",
        "contacts_search",
        "tasks_list",
        "tasks_add",
    ]
    return {t.name: t for t in build_tools(ctx, names)}


ZONE = {"value": "Asia/Kolkata"}


async def test_chat_offers_only_granted_services(deps, model, user, turn, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["gmail", "calendar"])
    await run_turn(deps, *turn)

    offered = set(model.tools)
    assert {"gmail_search", "gmail_read", "gmail_draft", "calendar_list_events"} <= offered
    assert {"calendar_create_event", "calendar_update_event"} <= offered
    # Not granted, or outbound/destructive (agents only, with approval).
    assert not {"tasks_list", "contacts_search", "gmail_send", "calendar_invite"} & offered
    assert "calendar_delete_event" not in offered


async def test_no_grant_offers_no_google_tools(deps, model, turn):
    await run_turn(deps, *turn)
    assert not any(
        t.startswith(("gmail_", "calendar_", "tasks_", "contacts_")) for t in model.tools
    )


async def test_ungranted_service_says_how_to_allow_it(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["gmail"])
    tools = tools_for(deps, user, Google({}))
    out = await tools["tasks_list"].ainvoke({})
    assert "Google Tasks isn't connected" in out and "Settings page" in out


async def test_gmail_search_and_read(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["gmail"])
    text = base64.urlsafe_b64encode(b"Hi Shivam,\nThe invoice is attached.").decode().rstrip("=")
    google = Google(
        {
            ("GET", f"{GMAIL}/messages"): {"messages": [{"id": "m1"}]},
            ("GET", f"{GMAIL}/messages/m1"): lambda r: {
                "id": "m1",
                "threadId": "t1",
                "labelIds": ["INBOX", "UNREAD"],
                "snippet": "The invoice &amp; more",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": [
                        {"name": "From", "value": "Alice <alice@example.com>"},
                        {"name": "Subject", "value": "Invoice"},
                    ],
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": text}},
                        {"mimeType": "application/pdf", "filename": "inv.pdf", "body": {}},
                    ],
                },
            },
        }
    )
    tools = tools_for(deps, user, google)

    found = await tools["gmail_search"].ainvoke({"query": "is:unread"})
    assert "Invoice [unread]" in found and "alice@example.com" in found and "(id m1)" in found
    assert "invoice & more" in found
    assert google.sent("GET", f"{GMAIL}/messages").url.params["q"] == "is:unread"
    assert google.requests[0].headers["Authorization"] == "Bearer access-1"

    read = await tools["gmail_read"].ainvoke({"message_id": "m1"})
    assert read.startswith('<email id="m1" thread="t1">')
    assert "The invoice is attached." in read and "Attachments: inv.pdf" in read


async def test_gmail_reply_threads_and_refreshes_token(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["gmail"], expired=True)
    google = Google(
        {
            ("POST", "https://oauth2.googleapis.com/token"): {
                "access_token": "access-2",
                "expires_in": 3600,
            },
            ("GET", f"{GMAIL}/messages/m1"): {
                "id": "m1",
                "threadId": "t1",
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<abc@mail>"},
                        {"name": "Subject", "value": "Lunch?"},
                    ]
                },
            },
            ("POST", f"{GMAIL}/messages/send"): {"id": "m2"},
        }
    )
    tools = tools_for(deps, user, google)
    out = await tools["gmail_send"].ainvoke(
        {"to": ["alice@example.com"], "subject": "", "body": "Yes!", "reply_to_message_id": "m1"}
    )
    assert out == "Sent to alice@example.com (message id m2)."
    send = google.sent("POST", f"{GMAIL}/messages/send")
    assert send.headers["Authorization"] == "Bearer access-2"
    payload = json.loads(send.content)
    assert payload["threadId"] == "t1"
    msg = email.message_from_bytes(base64.urlsafe_b64decode(payload["raw"]), policy=policy.default)
    assert msg["Subject"] == "Re: Lunch?" and msg["In-Reply-To"] == "<abc@mail>"
    assert msg.get_content().strip() == "Yes!"


async def test_labels_cannot_trash(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["gmail"])
    google = Google(
        {
            ("GET", f"{GMAIL}/labels"): {
                "labels": [{"id": "INBOX", "name": "INBOX"}, {"id": "TRASH", "name": "TRASH"}]
            },
            ("POST", f"{GMAIL}/messages/m1/modify"): {},
        }
    )
    tools = tools_for(deps, user, google)
    assert "isn't allowed" in await tools["gmail_update_labels"].ainvoke(
        {"message_id": "m1", "add": ["trash"]}
    )
    assert (
        await tools["gmail_update_labels"].ainvoke({"message_id": "m1", "remove": ["inbox"]})
        == "Labels updated."
    )
    body = json.loads(google.sent("POST", f"{GMAIL}/messages/m1/modify").content)
    assert body == {"addLabelIds": [], "removeLabelIds": ["INBOX"]}


async def test_calendar_create_uses_calendar_zone_without_invites(
    deps, user, sessionmaker, settings
):
    await grant(sessionmaker, settings, user, ["calendar"])

    def created(request):
        return {"id": "e1", **json.loads(request.content)}

    google = Google(
        {
            ("GET", f"{CAL}/users/me/settings/timezone"): ZONE,
            ("POST", f"{CAL}/calendars/primary/events"): created,
        }
    )
    tools = tools_for(deps, user, google)
    out = await tools["calendar_create_event"].ainvoke(
        {"title": "Dentist", "start": "2026-10-21T15:00"}
    )
    req = google.sent("POST", f"{CAL}/calendars/primary/events")
    assert req.url.params["sendUpdates"] == "none"
    body = json.loads(req.content)
    assert body["start"] == {"dateTime": "2026-10-21T15:00:00+05:30", "timeZone": "Asia/Kolkata"}
    assert body["end"]["dateTime"] == "2026-10-21T16:00:00+05:30"
    assert "attendees" not in body
    assert "Dentist: Wed 21 Oct 15:00 to 16:00 (id e1)" in out


async def test_calendar_update_refuses_events_with_guests(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["calendar"])
    google = Google(
        {
            ("GET", f"{CAL}/users/me/settings/timezone"): ZONE,
            ("GET", f"{CAL}/calendars/primary/events/e1"): {
                "id": "e1",
                "attendees": [{"email": "me@x", "self": True}, {"email": "bob@x"}],
            },
        }
    )
    tools = tools_for(deps, user, google)
    out = await tools["calendar_update_event"].ainvoke({"event_id": "e1", "title": "New"})
    assert "other guests" in out
    assert not any(r.method == "PATCH" for r in google.requests)


async def test_calendar_list_defaults_to_next_week(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["calendar"])
    google = Google(
        {
            ("GET", f"{CAL}/users/me/settings/timezone"): ZONE,
            ("GET", f"{CAL}/calendars/primary/events"): {
                "items": [
                    {
                        "id": "e1",
                        "summary": "Standup",
                        "start": {"dateTime": "2026-10-21T04:30:00Z"},
                        "end": {"dateTime": "2026-10-21T04:45:00Z"},
                    },
                    {"id": "e2", "summary": "Holiday", "start": {"date": "2026-10-22"}, "end": {}},
                ]
            },
        }
    )
    tools = tools_for(deps, user, google)
    out = await tools["calendar_list_events"].ainvoke({"start": "2026-10-21", "end": "2026-10-22"})
    assert "Standup: Wed 21 Oct 10:00 to 10:15 (id e1)" in out
    assert "Holiday: Thu 22 Oct (all day)" in out
    params = google.sent("GET", f"{CAL}/calendars/primary/events").url.params
    assert params["timeMin"] == "2026-10-21T00:00:00+05:30"
    assert params["timeMax"] == "2026-10-23T00:00:00+05:30"  # the end day is included


async def test_tasks_add_and_contacts(deps, user, sessionmaker, settings):
    await grant(sessionmaker, settings, user, ["tasks", "contacts"])
    tasks = "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks"
    google = Google(
        {
            ("POST", tasks): lambda r: {"id": "k1", **json.loads(r.content)},
            ("GET", "https://people.googleapis.com/v1/people:searchContacts"): lambda r: {
                "results": [
                    {
                        "person": {
                            "names": [{"displayName": "Alice Smith"}],
                            "emailAddresses": [{"value": "alice@example.com"}],
                        }
                    }
                ]
                if r.url.params["query"]
                else []
            },
        }
    )
    tools = tools_for(deps, user, google)
    assert await tools["tasks_add"].ainvoke({"title": "Pay rent", "due": "2026-11-01"}) == (
        "Added task Pay rent (id k1)."
    )
    assert json.loads(google.sent("POST", tasks).content)["due"] == "2026-11-01T00:00:00.000Z"
    assert "Couldn't read" in await tools["tasks_add"].ainvoke({"title": "x", "due": "soon"})
    out = await tools["contacts_search"].ainvoke({"query": "alice"})
    assert out == "- Alice Smith; email alice@example.com"


def test_message_body_falls_back_to_html():
    html = base64.urlsafe_b64encode(b"<p>Hello</p><script>x</script><p>there</p>").decode()
    text, files = message_body({"mimeType": "text/html", "body": {"data": html}})
    assert text == "Hello\nthere" and files == []


def test_parse_when():
    from datetime import date
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/London")
    assert parse_when("2026-10-21", tz) == date(2026, 10, 21)
    assert parse_when("2026-10-21T09:00", tz).utcoffset() == timedelta(hours=1)
    assert parse_when("2026-10-21T09:00Z", tz).utcoffset() == timedelta(0)
