"""Chat history imports on the Knowledge page: upload a ChatGPT or Claude export, see how far
the local model has read it, and delete it, which takes back everything it taught the graph.
The reading happens in the orchestrator (chat_import), and the raw export is deleted once read.
"""

import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select

from nexus_api.deps import DbDep, MemberUser, QueueDep, RedisDep, SettingsDep, StorageDep
from nexus_api.routes.memories import announce_change
from nexus_common import graph
from nexus_common.models import (
    AuditLog,
    ChatImport,
    ImportedChat,
    Relation,
    RelationEvidence,
)

router = APIRouter(prefix="/api/imports", tags=["memories"])

# A ChatGPT export with years of chats and pictures can be large; only its text is read.
MAX_MB = 1024
STORAGE_KEY = "imports/{}"  # nexus_orchestrator.chat_import.storage_key


async def import_out(db, imp: ChatImport) -> dict:
    read = await db.scalar(
        select(func.count())
        .select_from(ImportedChat)
        .where(ImportedChat.import_id == imp.id, ImportedChat.graph_at.is_not(None))
    )
    return {
        "id": str(imp.id),
        "name": imp.name,
        "source": imp.source,
        "status": imp.status,
        "conversations": imp.conversations,
        "kept": imp.kept,
        "read": read or 0,
        "error": imp.error,
        "created_at": imp.created_at,
    }


@router.get("")
async def list_imports(user: MemberUser, db: DbDep):
    rows = await db.scalars(
        select(ChatImport).where(ChatImport.user_id == user.id).order_by(ChatImport.created_at)
    )
    return [await import_out(db, imp) for imp in rows.all()]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_import(
    file: UploadFile,
    user: MemberUser,
    db: DbDep,
    storage: StorageDep,
    queue: QueueDep,
    settings: SettingsDep,
):
    if not settings.graph_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "The knowledge graph is turned off.")
    name = (file.filename or "export").rsplit("/", 1)[-1][:255]
    if not name.lower().endswith((".zip", ".json")):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Upload the export's .zip file, or the conversations.json inside it.",
        )
    parts: list[bytes] = []
    size = 0
    while block := await file.read(1 << 20):
        size += len(block)
        if size > MAX_MB * 1024 * 1024:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"Exports can be up to {MAX_MB} MB."
            )
        parts.append(block)
    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    imp = ChatImport(id=uuid.uuid4(), user_id=user.id, name=name)
    await storage.put(STORAGE_KEY.format(imp.id), b"".join(parts), "application/octet-stream")
    db.add(imp)
    db.add(AuditLog(actor_id=user.id, action="import.uploaded", target=str(imp.id)))
    await db.commit()
    await queue.enqueue_chat_import(imp.id)
    return await import_out(db, imp)


@router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import(
    import_id: uuid.UUID, user: MemberUser, db: DbDep, storage: StorageDep, redis: RedisDep
):
    imp = await db.scalar(
        select(ChatImport).where(ChatImport.id == import_id, ChatImport.user_id == user.id)
    )
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    # The links this import made, before its conversations (and their evidence) go.
    taught = (
        await db.scalars(
            select(RelationEvidence.relation_id)
            .join(ImportedChat, ImportedChat.id == RelationEvidence.imported_chat_id)
            .where(ImportedChat.import_id == imp.id)
            .distinct()
        )
    ).all()
    await db.delete(imp)
    await db.flush()
    if taught:
        # Links only the import knew go; ones also said elsewhere stay.
        await db.execute(
            delete(Relation).where(
                Relation.id.in_(taught),
                Relation.origin == "import",
                ~select(RelationEvidence.id)
                .where(RelationEvidence.relation_id == Relation.id)
                .exists(),
            )
        )
    await graph.prune(db, user.id)
    db.add(AuditLog(actor_id=user.id, action="import.deleted", target=str(import_id)))
    await db.commit()
    try:
        await storage.delete(STORAGE_KEY.format(import_id))
    except Exception:
        pass
    await announce_change(redis, user, "delete", import_id)
