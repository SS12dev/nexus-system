"""Split Markdown into chunks for search. Chunks follow headings (or spreadsheet sheets), keep
tables whole where they fit and repeat a table's header when they don't, and never overlap, so
joining them gives back the document (apart from those repeated headers)."""

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    heading: str | None
    content: str

    def embedding_text(self) -> str:
        """Text to embed: the heading path gives a chunk the context its words may lack."""
        return f"{self.heading}\n\n{self.content}" if self.heading else self.content


def _blocks(markdown: str) -> list[tuple[str | None, str]]:
    """(heading path, block) pairs, where a block is a paragraph, a list, a table or a heading."""
    path: list[tuple[int, str]] = []
    out: list[tuple[str | None, str]] = []
    buf: list[str] = []
    in_table = False

    def heading() -> str | None:
        return " > ".join(t for _, t in path) or None

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            out.append((heading(), text))
        buf = []

    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and (m := HEADING.match(line)):
            flush()
            level = len(m.group(1))
            path = [(lv, t) for lv, t in path if lv < level] + [(level, m.group(2)[:200])]
            out.append((heading(), line.strip()))
            continue
        is_table = line.lstrip().startswith("|")
        if not in_code and (not line.strip() or is_table != in_table):
            flush()
        in_table = is_table
        if line.strip() or in_code:
            buf.append(line)
    flush()
    return out


def _split_oversized(block: str, max_chars: int) -> list[str]:
    lines = block.splitlines()
    if lines and lines[0].lstrip().startswith("|") and len(lines) > 2:
        header, rows = lines[:2], lines[2:]
        parts, cur = [], list(header)
        for row in rows:
            if len("\n".join([*cur, row])) > max_chars and len(cur) > 2:
                parts.append("\n".join(cur))
                cur = list(header)
            cur.append(row)
        parts.append("\n".join(cur))
        return parts
    parts, cur = [], ""
    for piece in SENTENCE_END.split(block):
        while len(piece) > max_chars:
            parts.append(piece[:max_chars])
            piece = piece[max_chars:]
        if cur and len(cur) + 1 + len(piece) > max_chars:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}" if cur else piece
    if cur:
        parts.append(cur)
    return parts


def chunk_markdown(markdown: str, target: int = 1500, max_chars: int = 3000) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_heading: str | None = None
    size = 0

    def flush() -> None:
        nonlocal cur, size
        if cur:
            chunks.append(Chunk(cur_heading, "\n\n".join(cur)))
        cur, size = [], 0

    for heading, block in _blocks(markdown):
        # A new section starts a new chunk once the current one has some substance.
        if heading != cur_heading and size >= target // 3:
            flush()
        if not cur:
            cur_heading = heading
        pieces = [block] if len(block) <= max_chars else _split_oversized(block, target)
        for piece in pieces:
            # Headings stay with the text under them.
            if cur and size + len(piece) > target and not all(c.startswith("#") for c in cur):
                flush()
                cur_heading = heading
            cur.append(piece)
            size += len(piece) + 2
    flush()
    return chunks
