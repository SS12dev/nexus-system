import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.deps import CurrentUser, DbDep, MemberUser, QueueDep, SettingsDep
from jarvis_api.schemas import (
    ChatCreateIn,
    ChatOut,
    ChatUpdateIn,
    FileBrief,
    MessageOut,
    SendMessageIn,
    SendMessageOut,
)
from jarvis_common.models import (
    Attachment,
    Chat,
    File,
    Message,
    MessageRole,
    MessageStatus,
    User,
    utcnow,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chats", tags=["chats"])


async def own_chat(db: AsyncSession, user: User, chat_id: uuid.UUID) -> Chat:
    """Load a chat only if it belongs to the user. Other users' chats look like they don't exist,
    admins included: each user's conversations are private to them."""
    chat = await db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user.id))
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return chat


async def attachments_for(
    db: AsyncSession, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[File]]:
    if not message_ids:
        return {}
    rows = await db.execute(
        select(Attachment.message_id, File)
        .join(File, File.id == Attachment.file_id)
        .where(Attachment.message_id.in_(message_ids))
        .order_by(File.created_at)
    )
    out: dict[uuid.UUID, list[File]] = {}
    for message_id, f in rows.all():
        out.setdefault(message_id, []).append(f)
    return out


def message_out(msg: Message, files: list[File] | None = None) -> MessageOut:
    out = MessageOut.model_validate(msg)
    out.attachments = [FileBrief.model_validate(f) for f in files or []]
    return out


@router.get("", response_model=list[ChatOut])
async def list_chats(user: CurrentUser, db: DbDep):
    rows = await db.scalars(
        select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at.desc()).limit(200)
    )
    return rows.all()


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
async def create_chat(body: ChatCreateIn, user: MemberUser, db: DbDep):
    chat = Chat(user_id=user.id, title=body.title)
    db.add(chat)
    await db.commit()
    return chat


@router.patch("/{chat_id}", response_model=ChatOut)
async def rename_chat(chat_id: uuid.UUID, body: ChatUpdateIn, user: MemberUser, db: DbDep):
    chat = await own_chat(db, user, chat_id)
    chat.title = body.title
    await db.commit()
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: uuid.UUID, user: MemberUser, db: DbDep):
    chat = await own_chat(db, user, chat_id)
    await db.delete(chat)
    await db.commit()


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def list_messages(chat_id: uuid.UUID, user: CurrentUser, db: DbDep):
    await own_chat(db, user, chat_id)
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    messages = rows.all()
    files = await attachments_for(db, [m.id for m in messages if m.role == MessageRole.user])
    return [message_out(m, files.get(m.id)) for m in messages]


@router.post(
    "/{chat_id}/messages", response_model=SendMessageOut, status_code=status.HTTP_202_ACCEPTED
)
async def send_message(
    chat_id: uuid.UUID,
    body: SendMessageIn,
    user: MemberUser,
    db: DbDep,
    queue: QueueDep,
    settings: SettingsDep,
):
    """Store the user's message and a placeholder for Jarvis's reply, then queue the turn.
    The reply streams over the WebSocket as message.delta events."""
    chat = await own_chat(db, user, chat_id)
    busy = await db.scalar(
        select(Message.id).where(
            Message.chat_id == chat_id, Message.status == MessageStatus.streaming
        )
    )
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "Jarvis is still answering in this chat")
    files: list[File] = []
    if body.file_ids:
        files = list(
            (
                await db.scalars(
                    select(File).where(File.id.in_(body.file_ids), File.user_id == user.id)
                )
            ).all()
        )
        if len(files) != len(set(body.file_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    now = utcnow()
    user_msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content=body.content,
        status=MessageStatus.complete,
        created_at=now,
    )
    assistant_msg = Message(
        chat_id=chat.id,
        role=MessageRole.assistant,
        content="",
        status=MessageStatus.streaming,
        model=settings.jarvis_chat_model,
        trace_id=uuid.uuid4().hex,
        # Ordered after the user's message even when both land in the same clock tick.
        created_at=now + timedelta(microseconds=1),
    )
    if not chat.title:
        chat.title = body.content.strip().splitlines()[0][:80] or "New chat"
    chat.updated_at = now
    db.add_all([user_msg, assistant_msg])
    await db.flush()
    for f in files:
        db.add(Attachment(message_id=user_msg.id, file_id=f.id))
        f.chat_id = f.chat_id or chat.id
    await db.commit()

    try:
        await queue.enqueue_turn(chat.id, assistant_msg.id)
    except Exception as exc:
        log.exception("Could not queue turn")
        assistant_msg.status = MessageStatus.failed
        assistant_msg.error = "Jarvis is unavailable right now. Try again in a moment."
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, assistant_msg.error) from exc

    return SendMessageOut(
        chat=ChatOut.model_validate(chat),
        user_message=message_out(user_msg, files),
        assistant_message=message_out(assistant_msg),
    )
