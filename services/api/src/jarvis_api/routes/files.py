"""The document library: upload, list, search, read, download, rename and delete files. Every
file belongs to one user; other users get a 404."""

import hashlib
import logging
import uuid
from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.deps import (
    CurrentUser,
    DbDep,
    EmbedderDep,
    MemberUser,
    QueueDep,
    SettingsDep,
    StorageDep,
)
from jarvis_api.routes.chats import own_chat
from jarvis_api.schemas import FileBrief, FileOut, FileTextOut, FileUpdateIn, SearchHitOut
from jarvis_common.library import SUPPORTED_EXTENSIONS, content_type_for, document_text
from jarvis_common.models import File, FileChunk, FileStatus, User
from jarvis_common.search import hybrid_search
from jarvis_common.storage import file_key

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

# Shown in the browser rather than downloaded. Everything else (HTML above all) is served as an
# attachment so an upload can never run script on Jarvis's origin.
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}



async def own_file(db: AsyncSession, user: User, file_id: uuid.UUID) -> File:
    f = await db.scalar(select(File).where(File.id == file_id, File.user_id == user.id))
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return f


def clean_name(name: str | None) -> str:
    base = PurePath((name or "").replace("\\", "/")).name.strip()
    return base[:255] or "upload"


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload(
    user: MemberUser,
    db: DbDep,
    queue: QueueDep,
    storage: StorageDep,
    settings: SettingsDep,
    file: UploadFile,
    chat_id: uuid.UUID | None = Form(default=None),
):
    name = clean_name(file.filename)
    ext = PurePath(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Jarvis can't read {ext or 'files without an extension'} files yet.",
        )
    if chat_id is not None:
        await own_chat(db, user, chat_id)

    limit = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    parts: list[bytes] = []
    size = 0
    while block := await file.read(1 << 20):
        size += len(block)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"Files can be up to {settings.max_upload_mb} MB.",
            )
        digest.update(block)
        parts.append(block)
    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")

    content_type = content_type_for(name, file.content_type)
    f = File(
        id=uuid.uuid4(),
        user_id=user.id,
        chat_id=chat_id,
        name=name,
        content_type=content_type,
        size=size,
        sha256=digest.hexdigest(),
        status=FileStatus.processing,
    )
    f.storage_key = file_key(user.id, f.id)
    await storage.put(f.storage_key, b"".join(parts), content_type)
    db.add(f)
    await db.commit()
    try:
        await queue.enqueue_parse(f.id)
    except Exception:
        log.exception("Could not queue parse of %s", f.id)
        f.status, f.error = FileStatus.failed, "Jarvis couldn't start reading this file. Retry."
        await db.commit()
    return f


@router.get("", response_model=list[FileOut])
async def list_files(
    user: CurrentUser,
    db: DbDep,
    chat_id: uuid.UUID | None = None,
    limit: int = Query(default=200, le=1000),
):
    q = select(File).where(File.user_id == user.id)
    if chat_id is not None:
        q = q.where(File.chat_id == chat_id)
    rows = await db.scalars(q.order_by(File.created_at.desc()).limit(limit))
    return rows.all()


@router.get("/search", response_model=list[SearchHitOut])
async def search(
    user: CurrentUser,
    db: DbDep,
    embedder: EmbedderDep,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, le=50),
):
    vec, _model = await embedder.try_embed_one(db, q)
    hits = await hybrid_search(db, "file_chunks", user.id, q, vec, limit)
    if not hits:
        return []
    rows = await db.execute(
        select(FileChunk, File)
        .join(File, File.id == FileChunk.file_id)
        .where(FileChunk.id.in_([h.id for h in hits]), File.user_id == user.id)
    )
    by_id = {c.id: (c, f) for c, f in rows.all()}
    out = []
    for h in hits:
        if h.id not in by_id:
            continue
        chunk, f = by_id[h.id]
        out.append(
            SearchHitOut(
                file=FileBrief.model_validate(f),
                heading=chunk.heading,
                snippet=chunk.content[:500],
                score=h.score,
            )
        )
    return out


@router.get("/{file_id}", response_model=FileOut)
async def get_file(file_id: uuid.UUID, user: CurrentUser, db: DbDep):
    return await own_file(db, user, file_id)


@router.get("/{file_id}/text", response_model=FileTextOut)
async def file_text(
    file_id: uuid.UUID,
    user: CurrentUser,
    db: DbDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50000, ge=1, le=200000),
):
    f = await own_file(db, user, file_id)
    text = await document_text(db, f.id, offset, limit)
    return FileTextOut(text=text, offset=offset, total_chars=f.char_count)


@router.get("/{file_id}/content")
async def download(
    file_id: uuid.UUID, user: CurrentUser, db: DbDep, storage: StorageDep, inline: bool = False
):
    f = await own_file(db, user, file_id)
    show_inline = inline and f.content_type in INLINE_TYPES
    disposition = "inline" if show_inline else "attachment"
    ascii_name = f.name.encode("ascii", "replace").decode().replace('"', "")
    headers = {
        "Content-Disposition": (
            f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f.name)}"
        ),
        "Content-Length": str(f.size),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "private, max-age=300",
    }
    return StreamingResponse(
        storage.stream(f.storage_key), media_type=f.content_type, headers=headers
    )


@router.patch("/{file_id}", response_model=FileOut)
async def rename(file_id: uuid.UUID, body: FileUpdateIn, user: MemberUser, db: DbDep):
    f = await own_file(db, user, file_id)
    new_name = clean_name(body.name)
    # Keep the extension: the parser and the download type depend on it.
    old_ext = PurePath(f.name).suffix
    if PurePath(new_name).suffix.lower() != old_ext.lower():
        new_name += old_ext
    f.name = new_name[:255]
    await db.commit()
    return f


@router.post("/{file_id}/retry", response_model=FileOut)
async def retry(file_id: uuid.UUID, user: MemberUser, db: DbDep, queue: QueueDep):
    f = await own_file(db, user, file_id)
    if f.status != FileStatus.failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed files can be retried")
    f.status, f.error = FileStatus.processing, None
    await db.commit()
    await queue.enqueue_parse(f.id)
    return f


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: uuid.UUID, user: MemberUser, db: DbDep, storage: StorageDep):
    """Delete the file, its text and vectors, and its attachments to messages."""
    f = await own_file(db, user, file_id)
    key = f.storage_key
    await db.delete(f)
    await db.commit()
    await storage.delete(key)
