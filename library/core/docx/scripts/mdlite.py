"""A small Markdown block parser shared by the docx and pptx scripts: enough of Markdown for
reports (headings, paragraphs, lists, tables, quotes, code, images, page breaks), with inline
bold, italic, code and links."""

import re
from dataclasses import dataclass, field


@dataclass
class Block:
    kind: str  # heading, para, bullet, number, table, quote, code, image, pagebreak
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    src: str = ""


INLINE = re.compile(
    r"(\*\*(?P<b>.+?)\*\*|__(?P<b2>.+?)__|\*(?P<i>[^*]+?)\*|_(?P<i2>[^_]+?)_"
    r"|`(?P<c>[^`]+)`|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)]+)\))"
)


def inline(text: str) -> list[tuple[str, dict]]:
    """'a **b** c' -> [('a ', {}), ('b', {'bold': True}), (' c', {})]"""
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], {}))
        if m.group("b") or m.group("b2"):
            out.append((m.group("b") or m.group("b2"), {"bold": True}))
        elif m.group("i") or m.group("i2"):
            out.append((m.group("i") or m.group("i2"), {"italic": True}))
        elif m.group("c"):
            out.append((m.group("c"), {"code": True}))
        else:
            out.append((m.group("lt"), {"link": m.group("lu")}))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], {}))
    return out


def plain(text: str) -> str:
    return "".join(t for t, _ in inline(text))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(md: str) -> list[Block]:
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    para: list[str] = []

    def flush():
        if para:
            blocks.append(Block("para", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        if s.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
            i += 1
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)$", s):
            flush()
            blocks.append(Block("heading", m.group(2).strip(), level=len(m.group(1))))
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", s) or "page-break" in s:
            flush()
            blocks.append(Block("pagebreak"))
        elif m := re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", s):
            flush()
            blocks.append(Block("image", m.group(1), src=m.group(2)))
        elif (
            s.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip())
        ):
            flush()
            rows = [_cells(s)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            blocks.append(Block("table", rows=rows))
            continue
        elif m := re.match(r"^(\s*)([-*+])\s+(.*)$", line):
            flush()
            blocks.append(Block("bullet", m.group(3), level=len(m.group(1)) // 2))
        elif m := re.match(r"^(\s*)\d+[.)]\s+(.*)$", line):
            flush()
            blocks.append(Block("number", m.group(2), level=len(m.group(1)) // 2))
        elif s.startswith(">"):
            flush()
            blocks.append(Block("quote", s.lstrip("> ").strip()))
        else:
            para.append(s)
        i += 1
    flush()
    return blocks
