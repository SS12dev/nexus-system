import io
import json
import uuid

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from nexus_common.events import user_channel
from nexus_common.models import File, FileChunk, FileStatus, RoleName, UserStatus
from nexus_common.storage import file_key
from nexus_doc_worker.chunk import chunk_markdown
from nexus_doc_worker.ingest import DocDeps, embed_missing, ingest_file


@pytest.fixture
async def user_id(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db, email="shivam@example.com", role=RoleName.owner, status=UserStatus.active
        )
        await db.commit()
        return user.id


@pytest.fixture
def deps(settings, sessionmaker, redis, storage, embedder):
    return DocDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=redis,
        storage=storage,
        embedder=embedder,
    )


@pytest.fixture
def upload(sessionmaker, storage, user_id):
    async def make(name: str, data: bytes, sha: str | None = None) -> uuid.UUID:
        fid = uuid.uuid4()
        key = file_key(user_id, fid)
        await storage.put(key, data, "application/octet-stream")
        async with sessionmaker() as db:
            db.add(
                File(
                    id=fid,
                    user_id=user_id,
                    name=name,
                    content_type="application/octet-stream",
                    size=len(data),
                    sha256=sha or uuid.uuid4().hex,
                    storage_key=key,
                    status=FileStatus.processing,
                )
            )
            await db.commit()
        return fid

    return make


def xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Q3 sales"
    ws.append(["Region", "Revenue"])
    ws.append(["North", 1200])
    ws.append(["South", 950])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_markdown_is_chunked_embedded_and_announced(
    deps, upload, sessionmaker, redis, user_id
):
    text = "# Plan\n\nShip phase two.\n\n## Risks\n\nThe embedder may be slow on a laptop."
    fid = await upload("plan.md", text.encode())
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    await pubsub.get_message(timeout=1)

    await ingest_file(deps, fid)

    async with sessionmaker() as db:
        f = await db.get(File, fid)
        chunks = (await db.scalars(select(FileChunk).where(FileChunk.file_id == fid))).all()
    assert f.status == FileStatus.ready and f.parser == "text"
    assert f.chunk_count == len(chunks) >= 1
    assert all(c.embedding is not None and c.embedding_model == "fake-embed" for c in chunks)
    event = json.loads(
        (await pubsub.get_message(ignore_subscribe_messages=True, timeout=1))["data"]
    )
    assert event["type"] == "file.updated" and event["data"]["file"]["status"] == "ready"
    await pubsub.aclose()


async def test_spreadsheets_become_tables(deps, upload, sessionmaker):
    fid = await upload("sales.xlsx", xlsx_bytes())
    await ingest_file(deps, fid)
    async with sessionmaker() as db:
        f = await db.get(File, fid)
        chunks = (await db.scalars(select(FileChunk).where(FileChunk.file_id == fid))).all()
    assert f.status == FileStatus.ready and f.parser == "markitdown"
    text = "\n".join(c.content for c in chunks)
    assert "Q3 sales" in text and "| North" in text and "1200" in text


async def test_same_upload_again_reuses_the_parse(deps, upload, sessionmaker, fake_embedder):
    first = await upload("a.txt", b"Nexus remembers things.", sha="same")
    await ingest_file(deps, first)
    calls = fake_embedder.calls
    second = await upload("b.txt", b"Nexus remembers things.", sha="same")
    await ingest_file(deps, second)
    assert fake_embedder.calls == calls
    async with sessionmaker() as db:
        f = await db.get(File, second)
        assert f.status == FileStatus.ready and f.chunk_count == 1


async def test_embedder_outage_leaves_file_searchable_and_backfill_fixes_it(
    deps, upload, sessionmaker, fake_embedder
):
    fake_embedder.down = True
    fid = await upload("notes.txt", b"Remember the budget review on Friday.")
    await ingest_file(deps, fid)
    async with sessionmaker() as db:
        assert (await db.get(File, fid)).status == FileStatus.ready
        chunk = await db.scalar(select(FileChunk).where(FileChunk.file_id == fid))
        assert chunk.embedding is None

    fake_embedder.down = False
    assert await embed_missing(deps) == 1
    async with sessionmaker() as db:
        chunk = await db.scalar(select(FileChunk).where(FileChunk.file_id == fid))
        assert chunk.embedding is not None


async def test_unreadable_files_fail_with_a_reason(deps, upload, sessionmaker):
    empty = await upload("empty.txt", b"   \n")
    broken = await upload("broken.pdf", bytes(range(256)) * 8)
    await ingest_file(deps, empty)
    await ingest_file(deps, broken)
    async with sessionmaker() as db:
        e, b = await db.get(File, empty), await db.get(File, broken)
    assert e.status == FileStatus.failed and "no text" in e.error
    assert b.status == FileStatus.failed and b.error


async def test_ready_files_are_not_reprocessed(deps, upload, sessionmaker, fake_embedder):
    fid = await upload("a.txt", b"hello world")
    await ingest_file(deps, fid)
    calls = fake_embedder.calls
    await ingest_file(deps, fid)
    assert fake_embedder.calls == calls


def test_chunks_follow_headings_and_split_big_tables():
    rows = "\n".join(f"| {i} | {i * 2} |" for i in range(300))
    md = f"# Report\n\nIntro.\n\n## Sales\n\n| a | b |\n|---|---|\n{rows}\n\n## Notes\n\nDone."
    chunks = chunk_markdown(md)
    tables = [c for c in chunks if c.content.startswith("| a | b |")]
    assert len(tables) > 1 and all(c.heading == "Report > Sales" for c in tables)
    assert all(len(c.content) <= 3000 for c in chunks)
    assert chunks[-1].heading == "Report > Notes" and "Done." in chunks[-1].content
    assert chunks[-1].embedding_text().startswith("Report > Notes")
