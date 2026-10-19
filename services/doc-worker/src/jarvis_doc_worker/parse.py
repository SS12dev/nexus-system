"""Turn an upload into Markdown. Docling handles PDFs and images (layout-aware tables, OCR for
scans) when it is installed, as it is in the Docker image; MarkItDown handles Office files,
spreadsheets, HTML and the rest, and is the PDF fallback."""

import logging
import os
import tempfile
from functools import cache
from pathlib import Path

from jarvis_common.library import (
    DOCLING_EXTENSIONS,
    MARKITDOWN_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    TEXT_EXTENSIONS,
)

log = logging.getLogger(__name__)


class ParseError(Exception):
    """A failure whose message is safe to show the user."""


@cache
def _docling():
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError:
        return None
    options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
    # The Docker image downloads the models at build time so parsing works offline.
    if artifacts := os.environ.get("DOCLING_ARTIFACTS_PATH"):
        options.artifacts_path = artifacts
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


@cache
def _markitdown():
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False)


def docling_available() -> bool:
    return _docling() is not None


def parse(data: bytes, name: str) -> tuple[str, str]:
    """Return (markdown, parser name). Blocking: run it in a thread."""
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"Jarvis can't read {ext or 'files without an extension'} files yet.")
    if ext in TEXT_EXTENSIONS:
        return data.decode("utf-8", errors="replace"), "text"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{ext}"
        path.write_bytes(data)
        if ext in DOCLING_EXTENSIONS and (converter := _docling()) is not None:
            try:
                result = converter.convert(path)
                return result.document.export_to_markdown(), "docling"
            except Exception:
                if ext not in MARKITDOWN_EXTENSIONS:
                    log.exception("Docling failed on %s", name)
                    raise ParseError("Jarvis couldn't read this file.") from None
                log.warning("Docling failed on %s; trying MarkItDown", name, exc_info=True)
        if ext not in MARKITDOWN_EXTENSIONS:
            raise ParseError("Reading images needs Docling, which isn't installed here.")
        try:
            return _markitdown().convert(str(path)).text_content, "markitdown"
        except Exception as exc:
            log.warning("MarkItDown failed on %s: %s", name, exc)
            raise ParseError(
                "Jarvis couldn't read this file. Is it damaged or encrypted?"
            ) from None
