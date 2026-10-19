"""Helpers for the document library shared by the API, the doc-worker and the orchestrator."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_common.models import File, FileChunk

# What the doc-worker can read. Checked at upload so unreadable files are refused up front.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".log", ".py", ".js", ".ts",
    ".tsx", ".sql", ".sh", ".xml",
}  # fmt: skip
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
DOCLING_EXTENSIONS = {".pdf"} | IMAGE_EXTENSIONS
MARKITDOWN_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".html", ".htm", ".epub", ".ipynb",
}  # fmt: skip
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCLING_EXTENSIONS | MARKITDOWN_EXTENSIONS


def file_payload(f: File) -> dict:
    return {
        "id": str(f.id),
        "name": f.name,
        "content_type": f.content_type,
        "size": f.size,
        "status": f.status,
        "error": f.error,
        "parser": f.parser,
        "chunk_count": f.chunk_count,
        "char_count": f.char_count,
        "chat_id": str(f.chat_id) if f.chat_id else None,
        "created_at": f.created_at.isoformat(),
    }


async def document_text(
    db: AsyncSession, file_id: uuid.UUID, start: int = 0, limit: int | None = None
) -> str:
    """The parsed Markdown of a file, rebuilt from its chunks (which don't overlap), sliced by
    character offset."""
    rows = await db.scalars(
        select(FileChunk.content).where(FileChunk.file_id == file_id).order_by(FileChunk.idx)
    )
    full = "\n\n".join(rows.all())
    end = None if limit is None else start + limit
    return full[start:end]
