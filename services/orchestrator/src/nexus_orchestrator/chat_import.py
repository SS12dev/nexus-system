"""Reading a chat history export from another assistant into the knowledge graph.

The user uploads the export (the zip, or conversations.json from it). This job reads it once:
it works out whether it is ChatGPT's or Claude's, walks each conversation along the branch the
user ended on (edited and regenerated turns count once), and keeps only the user's own words,
with pasted code and logs stripped, as the bake-off showed pastes are where the wrong entities
come from. Conversations that are pure tasks with nothing about the user ("translate this")
are dropped. The raw export is then deleted; nothing in it goes to a cloud model.

The local graph builder (graph_local) then reads the kept conversations a few at a time, after
anything newer, and their facts enter the graph with the conversation's own date.
"""

import io
import json
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from nexus_common.models import ChatImport, ImportedChat, utcnow
from nexus_orchestrator.graph_local import strip_pastes
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

# What a conversation keeps of the user's words; the extractor reads the latest part of it.
KEEP_CHARS = 20000
# Fewer of the user's own words than this, or none in the first person, is a task, not a talk.
MIN_WORDS = 15
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|mine|myself|we|our|us)\b", re.I)


def storage_key(import_id: uuid.UUID) -> str:
    """Where the uploaded export waits in storage until it is read; deleted after."""
    return f"imports/{import_id}"


@dataclass
class Conversation:
    external_id: str
    title: str
    started_at: datetime | None
    said: list[str] = field(default_factory=list)


class ExportError(ValueError):
    pass


# ChatGPT's newer exports split the conversations over conversations-000.json,
# conversations-001.json...; older ones and Claude's have a single conversations.json.
_CONV_FILE = re.compile(r"conversations(-\d+)?\.json")


def _conversations_json(data: bytes) -> list:
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ExportError("That zip file is damaged.") from exc
        names = sorted(n for n in z.namelist() if _CONV_FILE.fullmatch(n.rsplit("/", 1)[-1]))
        if not names:
            raise ExportError("There is no conversations.json in that zip.")
        out: list = []
        for name in names:
            out.extend(_json_list(z.read(name)))
        return out
    return _json_list(data)


def _json_list(data: bytes) -> list:
    try:
        parsed = json.loads(data)
    except ValueError as exc:
        raise ExportError("That isn't a ChatGPT or Claude export.") from exc
    if not isinstance(parsed, list):
        raise ExportError("That isn't a ChatGPT or Claude export.")
    return parsed


def _when(value) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _chatgpt(c: dict) -> Conversation:
    """ChatGPT: a tree of messages in `mapping`; the branch the user ended on runs from
    `current_node` back to the root."""
    mapping = c.get("mapping") or {}
    chain, node, seen = [], c.get("current_node"), set()
    while node and node in mapping and node not in seen:
        seen.add(node)
        chain.append(mapping[node])
        node = mapping[node].get("parent")
    said = []
    for n in reversed(chain):
        m = n.get("message") or {}
        if (m.get("author") or {}).get("role") != "user":
            continue
        content = m.get("content") or {}
        if content.get("content_type") not in ("text", "multimodal_text"):
            continue
        parts = [p for p in content.get("parts") or [] if isinstance(p, str) and p.strip()]
        if parts:
            said.append("\n".join(parts))
    return Conversation(
        str(c.get("conversation_id") or c.get("id") or uuid.uuid4()),
        str(c.get("title") or ""),
        _when(c.get("create_time")),
        said,
    )


def _claude(c: dict) -> Conversation:
    """Claude: a list of messages, each naming its parent; the branch the user ended on runs
    from the latest message back."""
    msgs = [m for m in c.get("chat_messages") or [] if isinstance(m, dict)]
    by_id = {m.get("uuid"): m for m in msgs}
    if msgs and all(m.get("parent_message_uuid") for m in msgs[1:]):
        chain, node, seen = [], max(msgs, key=lambda m: m.get("created_at") or ""), set()
        while node is not None and node.get("uuid") not in seen:
            seen.add(node.get("uuid"))
            chain.append(node)
            node = by_id.get(node.get("parent_message_uuid"))
        msgs = list(reversed(chain))
    said = []
    for m in msgs:
        if m.get("sender") != "human":
            continue
        parts = [
            p.get("text") or ""
            for p in m.get("content") or []
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        words = "\n".join(p for p in parts if p.strip()) or str(m.get("text") or "")
        if words.strip():
            said.append(words)
    return Conversation(
        str(c.get("uuid") or uuid.uuid4()),
        str(c.get("name") or ""),
        _when(c.get("created_at")),
        said,
    )


def parse(data: bytes) -> tuple[str, list[Conversation]]:
    """Which assistant the export is from, and its conversations with the user's words only."""
    convs = [c for c in _conversations_json(data) if isinstance(c, dict)]
    if not convs:
        raise ExportError("That export has no conversations.")
    if any("mapping" in c for c in convs[:20]):
        return "chatgpt", [_chatgpt(c) for c in convs]
    if any("chat_messages" in c for c in convs[:20]):
        return "claude", [_claude(c) for c in convs]
    raise ExportError("That isn't a ChatGPT or Claude export.")


def own_words(conv: Conversation) -> str | None:
    """The user's words with pastes stripped, or None when there is nothing about them."""
    kept = [strip_pastes(s) for s in conv.said]
    text = "\n\n".join(f"USER: {k}" for k in kept if k.strip())
    plain = re.sub(r"\[pasted[^\]]*\]|\[… long paste trimmed\]", " ", text)
    if len(plain.split()) < MIN_WORDS or not FIRST_PERSON.search(plain):
        return None
    return text[-KEEP_CHARS:]


async def read_import(deps: TurnDeps, import_id: uuid.UUID) -> None:
    """Read an uploaded export into ImportedChat rows, then delete the export, whatever
    happened."""
    key = storage_key(import_id)
    try:
        async with deps.sessionmaker() as db:
            imp = await db.get(ChatImport, import_id)
            if imp is None or imp.status != "reading":
                return
            try:
                source, convs = parse(await deps.storage.get(key))
            except Exception as exc:
                imp.status, imp.finished_at = "failed", utcnow()
                imp.error = str(exc) if isinstance(exc, ExportError) else "Couldn't read that file."
                await db.commit()
                if not isinstance(exc, ExportError):
                    log.exception("Reading import %s failed", import_id)
                return
            kept = 0
            for c in convs:
                text = own_words(c)
                if text is None:
                    continue
                kept += 1
                await db.execute(
                    insert(ImportedChat)
                    .values(
                        id=uuid.uuid4(),
                        import_id=imp.id,
                        user_id=imp.user_id,
                        external_id=c.external_id[:64],
                        title=c.title[:255],
                        started_at=c.started_at,
                        text=text,
                        messages=len(c.said),
                        created_at=utcnow(),
                    )
                    .on_conflict_do_nothing(constraint="uq_imported_chats_external")
                )
            imp.source, imp.conversations, imp.kept = source, len(convs), kept
            imp.status, imp.finished_at = "ready", utcnow()
            await db.commit()
            log.info(
                "Import %s: %d of %d %s conversations kept", import_id, kept, len(convs), source
            )
    finally:
        try:
            await deps.storage.delete(key)
        except Exception:
            log.warning("Couldn't delete the export %s", key, exc_info=True)
