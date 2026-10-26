"""Pictures in a chat: the ones on the user's latest messages are shown to the model, older ones
become a line of text, and Nexus can look again with view_image."""

import base64
import hashlib
import uuid

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from nexus_common.models import (
    Attachment,
    Chat,
    File,
    FileStatus,
    Message,
    MessageRole,
    MessageStatus,
    RoleName,
    UserStatus,
)
from nexus_orchestrator.turn import run_turn

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
async def chat(sessionmaker, deps, storage):
    from nexus_api.users import create_user

    deps.storage = storage
    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        c = Chat(user_id=user.id, title="t")
        db.add(c)
        await db.commit()
        return user.id, c.id


async def say(sessionmaker, storage, user_id, chat_id, text: str, pictures=()) -> uuid.UUID:
    """A user message with pictures attached, and a placeholder reply; the reply's id."""
    async with sessionmaker() as db:
        m = Message(
            chat_id=chat_id, role=MessageRole.user, content=text, status=MessageStatus.complete
        )
        db.add(m)
        await db.flush()
        for name in pictures:
            key = f"files/{uuid.uuid4()}"
            await storage.put(key, PNG, "image/png")
            f = File(
                user_id=user_id,
                chat_id=chat_id,
                name=name,
                content_type="image/png",
                size=len(PNG),
                sha256=hashlib.sha256(PNG).hexdigest(),
                storage_key=key,
                # Reading text out of a screenshot can fail; the picture is still shown.
                status=FileStatus.failed,
                error="No text found",
            )
            db.add(f)
            await db.flush()
            db.add(Attachment(message_id=m.id, file_id=f.id))
        reply = Message(
            chat_id=chat_id,
            role=MessageRole.assistant,
            content="",
            status=MessageStatus.streaming,
            model="nexus-mini",
            trace_id=uuid.uuid4().hex,
        )
        db.add(reply)
        await db.commit()
        return reply.id


async def answer(deps, sessionmaker, chat_id, reply_id) -> None:
    await run_turn(deps, chat_id, reply_id, learn=False)
    async with sessionmaker() as db:
        assert (await db.get(Message, reply_id)).status == MessageStatus.complete


def pictures_in(message) -> list[str]:
    return [p["image_url"]["url"] for p in message.content if p.get("type") == "image_url"]


async def test_a_screenshot_is_shown_to_the_model(chat, deps, model, sessionmaker, storage):
    user_id, chat_id = chat
    reply = await say(sessionmaker, storage, user_id, chat_id, "", ["screenshot.png"])
    await answer(deps, sessionmaker, chat_id, reply)

    sent = model.seen[0]
    asked = sent[-1]
    assert isinstance(asked, HumanMessage) and isinstance(asked.content, list)
    assert pictures_in(asked) == ["data:image/png;base64," + base64.b64encode(PNG).decode()]
    assert {"type": "text", "text": "Attached picture screenshot.png:"} in asked.content
    # Listed as a picture, not as a document that failed to read.
    assert '<picture id="' in sent[0].content and "No text found" not in sent[0].content
    assert "view_image" in model.tools


async def test_only_the_latest_pictures_are_shown(chat, deps, model, sessionmaker, storage):
    user_id, chat_id = chat
    for i in range(4):
        reply = await say(sessionmaker, storage, user_id, chat_id, f"look {i}", [f"s{i}.png"])
        await answer(deps, sessionmaker, chat_id, reply)

    users = [m for m in model.seen[-1] if isinstance(m, HumanMessage)]
    assert [isinstance(m.content, list) for m in users] == [False, True, True, True]
    assert "s0.png. Call view_image to look again." in users[0].content


async def test_nexus_can_look_again_with_view_image(chat, deps, model, sessionmaker, storage):
    user_id, chat_id = chat
    first = await say(sessionmaker, storage, user_id, chat_id, "what's this?", ["error.png"])
    await answer(deps, sessionmaker, chat_id, first)
    model.script = [{"tool": "view_image", "args": {"name": "error.png"}}, "It's a 500 page."]
    model.seen.clear()
    reply = await say(sessionmaker, storage, user_id, chat_id, "look again")
    await answer(deps, sessionmaker, chat_id, reply)

    after = model.seen[1]
    assert isinstance(after[-2], ToolMessage) and "shown to you below" in after[-2].content
    shown = after[-1]
    assert shown.name == "view_image" and pictures_in(shown)
