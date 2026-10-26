"""Project folders in the library (nexus_common.folders): list them with their file counts,
make one to move files into, rename one, or permanently remove one and its files.
Each belongs to one user; other users get a 404."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.deps import CurrentUser, DbDep, MemberUser, RedisDep
from nexus_api.schemas import FolderIn, FolderOut
from nexus_common.events import Event, publish
from nexus_common.folders import file_counts, folder_payload
from nexus_common.models import File, Folder, User
from nexus_common.trash import add as add_to_trash
from nexus_common.trash import visible

router = APIRouter(prefix="/api/folders", tags=["folders"])


async def own_folder(db: AsyncSession, user: User, folder_id: uuid.UUID) -> Folder:
    f = await db.scalar(
        select(Folder).where(
            Folder.id == folder_id, Folder.user_id == user.id, visible("folder", Folder.id)
        )
    )
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return f


async def announce(redis, folder: Folder, files: int) -> None:
    await publish(
        redis,
        folder.user_id,
        Event(type="folder.updated", data={"folder": folder_payload(folder, files)}),
    )


@router.get("", response_model=list[FolderOut])
async def list_folders(user: CurrentUser, db: DbDep):
    rows = await db.scalars(
        select(Folder)
        .where(
            Folder.user_id == user.id,
            visible("folder", Folder.id),
            visible("project", Folder.project_id),
            visible("system", Folder.system_id),
        )
        .order_by(Folder.updated_at.desc())
    )
    counts = await file_counts(db, user.id)
    return [
        FolderOut.model_validate(f).model_copy(update={"files": counts.get(f.id, 0)})
        for f in rows.all()
    ]


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(body: FolderIn, user: MemberUser, db: DbDep, redis: RedisDep):
    f = Folder(id=uuid.uuid4(), user_id=user.id, name=" ".join(body.name.split())[:200])
    db.add(f)
    await db.commit()
    await announce(redis, f, 0)
    return FolderOut.model_validate(f)


@router.patch("/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: uuid.UUID, body: FolderIn, user: MemberUser, db: DbDep, redis: RedisDep
):
    f = await own_folder(db, user, folder_id)
    f.name = " ".join(body.name.split())[:200]
    await db.commit()
    files = (await file_counts(db, user.id)).get(f.id, 0)
    await announce(redis, f, files)
    return FolderOut.model_validate(f).model_copy(update={"files": files})


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: uuid.UUID,
    user: MemberUser,
    db: DbDep,
    redis: RedisDep,
):
    """Move a folder and all its files to Trash together."""
    f = await own_folder(db, user, folder_id)
    rows = await db.scalars(
        select(File).where(File.user_id == user.id, File.folder_id == folder_id)
    )
    files = rows.all()
    folder_item = await add_to_trash(db, user.id, "folder", f.id, f.name)
    await db.flush()
    for file in files:
        await add_to_trash(
            db,
            user.id,
            "file",
            file.id,
            file.name,
            parent_id=folder_item.id,
        )
    await db.commit()
    for file in files:
        await publish(redis, user.id, Event(type="file.deleted", data={"id": str(file.id)}))
    await publish(redis, user.id, Event(type="folder.deleted", data={"id": str(folder_id)}))
