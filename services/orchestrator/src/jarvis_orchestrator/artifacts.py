"""Files Jarvis and its agents make. They go into the user's library like uploads (origin
"generated"), are parsed so they can be searched, and a file saved again under the same name
becomes a new version of it."""

import hashlib
import uuid
from pathlib import PurePath

from sqlalchemy import func, select

from jarvis_common.events import Event, publish
from jarvis_common.library import SUPPORTED_EXTENSIONS, content_type_for, file_payload
from jarvis_common.models import File, FileOrigin, FileStatus
from jarvis_common.queues import DOCS_QUEUE
from jarvis_common.storage import file_key


def clean_name(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name.strip()
    return base[:255] or "file"


async def save_generated(
    deps,
    user_id: uuid.UUID,
    name: str,
    data: bytes,
    *,
    chat_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> File:
    if deps.storage is None:
        raise RuntimeError("No object storage configured")
    name = clean_name(name)
    readable = PurePath(name).suffix.lower() in SUPPORTED_EXTENSIONS
    async with deps.sessionmaker() as db:
        first = await db.scalar(
            select(File)
            .where(
                File.user_id == user_id,
                File.name == name,
                File.origin == FileOrigin.generated,
                File.version_of_id.is_(None),
            )
            .order_by(File.created_at)
            .limit(1)
        )
        version = 1
        if first is not None:
            latest = await db.scalar(
                select(func.max(File.version)).where(
                    (File.version_of_id == first.id) | (File.id == first.id)
                )
            )
            version = (latest or 1) + 1
        f = File(
            id=uuid.uuid4(),
            user_id=user_id,
            chat_id=chat_id,
            name=name,
            content_type=content_type_for(name),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            # Formats the doc-worker can't read are kept as they are, ready to download.
            status=FileStatus.processing if readable else FileStatus.ready,
            origin=FileOrigin.generated,
            run_id=run_id,
            version_of_id=first.id if first is not None else None,
            version=version,
        )
        f.storage_key = file_key(user_id, f.id)
        await deps.storage.put(f.storage_key, data, f.content_type)
        db.add(f)
        await db.commit()
    if readable and deps.enqueue is not None:
        await deps.enqueue("parse_file", str(f.id), _job_id=f"parse:{f.id}", _queue_name=DOCS_QUEUE)
    payload = file_payload(f)
    await publish(deps.redis, user_id, Event(type="file.updated", data={"file": payload}))
    await publish(
        deps.redis,
        user_id,
        Event(type="artifact.created", chat_id=chat_id, data={"file": payload}),
    )
    return f
