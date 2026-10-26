"""Files Nexus and its agents make. They go into the user's library like uploads (origin
"generated"), are parsed so they can be searched, and a file saved again under the same name
becomes a new version of it, unless the caller says it is a different file that shares the name
(Nexus decides; see save_file). Each file is filed into its work's project folder
(nexus_common.folders)."""

import hashlib
import uuid
from pathlib import PurePath

from sqlalchemy import func, select

from nexus_common.events import Event, publish
from nexus_common.folders import folder_for, folder_payload, touch
from nexus_common.library import SUPPORTED_EXTENSIONS, content_type_for, file_payload
from nexus_common.models import File, FileOrigin, FileStatus
from nexus_common.queues import DOCS_QUEUE
from nexus_common.storage import file_key
from nexus_common.trash import visible_file


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
    new_version: bool = True,
    project_id: uuid.UUID | None = None,
) -> File:
    if deps.storage is None:
        raise RuntimeError("No object storage configured")
    name = clean_name(name)
    readable = PurePath(name).suffix.lower() in SUPPORTED_EXTENSIONS
    async with deps.sessionmaker() as db:
        first = None
        if new_version:
            # The newest file of that name: with separate files sharing a name, the latest one
            # is the one being worked on.
            first = await db.scalar(
                select(File)
                .where(
                    File.user_id == user_id,
                    File.name == name,
                    File.origin == FileOrigin.generated,
                    File.version_of_id.is_(None),
                    visible_file(File.id, File.folder_id),
                )
                .order_by(File.created_at.desc())
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
        folder = await folder_for(
            db,
            user_id,
            name,
            run_id=run_id,
            chat_id=chat_id,
            version_of_id=f.version_of_id,
            project_id=project_id,
        )
        if folder is not None:
            f.folder_id = folder.id
            touch(folder)
        f.storage_key = file_key(user_id, f.id)
        await deps.storage.put(f.storage_key, data, f.content_type)
        db.add(f)
        await db.commit()
    if folder is not None:
        await publish(
            deps.redis,
            user_id,
            Event(type="folder.updated", data={"folder": folder_payload(folder)}),
        )
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
