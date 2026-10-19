"""Ingest one upload: parse it, chunk it, embed the chunks and mark it ready."""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_common.embeddings import CachedEmbedder, EmbeddingError
from jarvis_common.events import Event, publish
from jarvis_common.library import file_payload
from jarvis_common.models import File, FileChunk, FileStatus
from jarvis_common.settings import Settings
from jarvis_common.storage import Storage
from jarvis_doc_worker.chunk import chunk_markdown
from jarvis_doc_worker.parse import ParseError, parse

log = logging.getLogger(__name__)


@dataclass
class DocDeps:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    storage: Storage
    embedder: CachedEmbedder


async def _announce(deps: DocDeps, f: File) -> None:
    await publish(deps.redis, f.user_id, Event(type="file.updated", data={"file": file_payload(f)}))


async def ingest_file(deps: DocDeps, file_id: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        f = await db.get(File, file_id)
        if f is None or f.status != FileStatus.processing:
            return
        try:
            chunks = await _reuse_or_parse(deps, db, f)
        except ParseError as exc:
            f.status, f.error = FileStatus.failed, str(exc)
            await db.commit()
            await _announce(deps, f)
            return
        except Exception:
            log.exception("Ingest of %s failed", file_id)
            await db.rollback()
            f = await db.get(File, file_id)
            f.status, f.error = FileStatus.failed, "Jarvis couldn't read this file."
            await db.commit()
            await _announce(deps, f)
            return

        f.status = FileStatus.ready
        f.error = None
        f.chunk_count = len(chunks)
        await db.commit()
        await _announce(deps, f)
        log.info("Ingested %s (%s, %d chunks)", f.name, f.parser, len(chunks))


async def _reuse_or_parse(deps: DocDeps, db: AsyncSession, f: File) -> list[FileChunk]:
    # The same bytes uploaded again by the same user: copy the earlier parse and vectors.
    twin = await db.scalar(
        select(File).where(
            File.user_id == f.user_id,
            File.sha256 == f.sha256,
            File.status == FileStatus.ready,
            File.id != f.id,
        )
    )
    if twin is not None:
        source = (
            await db.scalars(
                select(FileChunk).where(FileChunk.file_id == twin.id).order_by(FileChunk.idx)
            )
        ).all()
        chunks = [
            FileChunk(
                file_id=f.id,
                user_id=f.user_id,
                idx=c.idx,
                heading=c.heading,
                content=c.content,
                embedding=c.embedding,
                embedding_model=c.embedding_model,
            )
            for c in source
        ]
        db.add_all(chunks)
        f.parser, f.char_count = twin.parser, twin.char_count
        return chunks

    data = await deps.storage.get(f.storage_key)
    markdown, parser = await asyncio.to_thread(parse, data, f.name)
    pieces = chunk_markdown(markdown)
    if not pieces:
        raise ParseError("Jarvis found no text in this file.")
    chunks = [
        FileChunk(file_id=f.id, user_id=f.user_id, idx=i, heading=p.heading, content=p.content)
        for i, p in enumerate(pieces)
    ]
    db.add_all(chunks)
    f.parser = parser
    f.char_count = sum(len(p.content) for p in pieces)
    await db.flush()
    try:
        vectors, model = await deps.embedder.embed(db, [p.embedding_text() for p in pieces])
    except EmbeddingError as exc:
        # Keyword search still works; the backfill job embeds the chunks once the embedder is up.
        log.warning("Embedding %s failed, left for the backfill: %s", f.id, exc)
    else:
        for c, vec in zip(chunks, vectors, strict=True):
            c.embedding, c.embedding_model = vec, model
    return chunks


async def embed_missing(deps: DocDeps, limit: int = 512) -> int:
    """Embed chunks that have no vector yet, or one from a model no longer in use."""
    async with deps.sessionmaker() as db:
        try:
            model = await deps.embedder.current_model(db)
        except EmbeddingError as exc:
            log.info("Backfill skipped, embedder unavailable: %s", exc)
            return 0
        rows = (
            await db.scalars(
                select(FileChunk)
                .where(or_(FileChunk.embedding.is_(None), FileChunk.embedding_model != model))
                .limit(limit)
            )
        ).all()
        if not rows:
            return 0
        texts = [f"{c.heading}\n\n{c.content}" if c.heading else c.content for c in rows]
        try:
            vectors, model = await deps.embedder.embed(db, texts)
        except EmbeddingError as exc:
            log.warning("Backfill failed: %s", exc)
            return 0
        for c, vec in zip(rows, vectors, strict=True):
            await db.execute(
                update(FileChunk)
                .where(FileChunk.id == c.id)
                .values(embedding=vec, embedding_model=model)
            )
        await db.commit()
        log.info("Backfilled %d chunk embeddings with %s", len(rows), model)
        return len(rows)
