import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from nexus_common.models import (
    ChatImport,
    Entity,
    ImportedChat,
    Mention,
    Relation,
    RelationEvidence,
)
from nexus_common.storage import MemoryStorage
from nexus_orchestrator import chat_import, graph_local
from nexus_orchestrator.chat_import import ExportError, parse

PERSONAL = "I moved to Pune last year and my sister Ana still lives in Delhi with her cat."
TASK = "Translate this into French: good morning."


def chatgpt_export() -> list:
    """One conversation whose first answer was regenerated after an edit: only the branch the
    user ended on counts."""

    def node(id_, parent, role, text, kind="text"):
        msg = {"author": {"role": role}, "content": {"content_type": kind, "parts": [text]}}
        return {"id": id_, "parent": parent, "message": msg}

    mapping = {
        "root": {"id": "root", "parent": None, "message": None},
        "u1": node("u1", "root", "user", "I live in Mumbai."),
        "u1b": node("u1b", "root", "user", PERSONAL),
        "a1": node("a1", "u1b", "assistant", "Lovely! Ana sounds great."),
        "u2": node(
            "u2",
            "a1",
            "user",
            "Also my manager Raj wants this:\n\n```python\nprint('Elon Musk is my brother')\n```",
        ),
    }
    return [
        {
            "id": "c1",
            "title": "Moving",
            "create_time": 1710000000.0,
            "current_node": "u2",
            "mapping": mapping,
        },
        {
            "id": "c2",
            "title": "French",
            "create_time": 1710000500.0,
            "current_node": "t1",
            "mapping": {"t1": node("t1", None, "user", TASK)},
        },
    ]


def claude_export() -> list:
    def msg(uuid, parent, sender, text, at):
        return {
            "uuid": uuid,
            "parent_message_uuid": parent,
            "sender": sender,
            "text": text,
            "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": text}],
            "created_at": at,
        }

    return [
        {
            "uuid": "k1",
            "name": "Home server",
            "created_at": "2025-03-01T10:00:00Z",
            "chat_messages": [
                msg("m1", None, "human", PERSONAL, "2025-03-01T10:00:00Z"),
                msg("m2", "m1", "assistant", "Noted.", "2025-03-01T10:00:05Z"),
                msg(
                    "m3", "m2", "human", "I'm also building my home server.", "2025-03-01T10:01:00Z"
                ),
            ],
        }
    ]


def zipped(convs: list) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export/conversations.json", json.dumps(convs))
        z.writestr("export/user.json", "{}")
    return buf.getvalue()


def test_a_chatgpt_export_keeps_the_branch_the_user_ended_on():
    source, convs = parse(zipped(chatgpt_export()))
    assert source == "chatgpt" and [c.title for c in convs] == ["Moving", "French"]
    moving = convs[0]
    assert moving.said[0] == PERSONAL and "Mumbai" not in " ".join(moving.said)
    assert moving.started_at == datetime.fromtimestamp(1710000000, UTC)
    words = chat_import.own_words(moving)
    # The user's own words, with the paste left out; the assistant's never.
    assert "Pune" in words and "Raj" in words and "Elon" not in words and "Lovely" not in words
    # A task with nothing about the user is dropped.
    assert chat_import.own_words(convs[1]) is None


def test_a_claude_export_reads_the_users_text_parts():
    source, convs = parse(json.dumps(claude_export()).encode())
    assert source == "claude"
    assert convs[0].said == [PERSONAL, "I'm also building my home server."]
    assert convs[0].started_at == datetime(2025, 3, 1, 10, tzinfo=UTC)


def test_anything_else_is_refused_plainly():
    for data, says in [
        (b"PK\x03\x04broken", "damaged"),
        (zipped([])[:0] or b"[]", "no conversations"),
        (b'{"a": 1}', "isn't a ChatGPT or Claude export"),
        (json.dumps([{"x": 1}]).encode(), "isn't a ChatGPT or Claude export"),
    ]:
        with pytest.raises(ExportError, match=says):
            parse(data)


class FakeLocal:
    def __init__(self, script):
        self.script, self.calls = script, []

    async def ask(self, system, prompt, schema):
        self.calls.append(prompt)
        return self.script.pop(0)

    async def aclose(self):
        pass


async def test_an_import_is_read_then_built_into_the_graph_with_its_own_dates(
    deps, sessionmaker, owner
):
    user, _chat = owner
    deps.storage = MemoryStorage()
    deps.settings = deps.settings.model_copy(update={"graph_extractor": "local"})
    async with sessionmaker() as db:
        imp = ChatImport(user_id=user.id, name="chatgpt.zip")
        db.add(imp)
        await db.commit()
    await deps.storage.put(chat_import.storage_key(imp.id), zipped(chatgpt_export()), "x")
    await chat_import.read_import(deps, imp.id)
    # The raw export is gone once read.
    assert deps.storage.objects == {}
    async with sessionmaker() as db:
        imp = await db.get(ChatImport, imp.id)
        kept = (await db.scalars(select(ImportedChat))).all()
    assert (imp.status, imp.source, imp.conversations, imp.kept) == ("ready", "chatgpt", 2, 1)
    assert [c.title for c in kept] == ["Moving"]

    fake = FakeLocal(
        [
            {
                "entities": [
                    {
                        "name": "Pune",
                        "type": "place",
                        "domain": "home",
                        "summary": "",
                        "items": [0],
                    },
                    {
                        "name": "Ana",
                        "type": "person",
                        "domain": "social",
                        "summary": "sister",
                        "items": [0],
                    },
                ]
            },
            {
                "relations": [
                    {
                        "from": "USER",
                        "predicate": "lives in",
                        "to": "Pune",
                        "label": "moved to",
                        "weight": 0.9,
                        "ended": False,
                    },
                    {
                        "from": "USER",
                        "predicate": "sibling of",
                        "to": "Ana",
                        "label": "my sister",
                        "weight": 0.9,
                        "ended": False,
                    },
                ]
            },
        ]
    )
    stats = await graph_local.extract_pending(deps, model=fake)
    assert stats["items"] == 1 and "from March 2024" in fake.calls[0]
    async with sessionmaker() as db:
        rels = (await db.scalars(select(Relation))).all()
        evidence = (await db.scalars(select(RelationEvidence))).all()
        ents = {e.name: e for e in (await db.scalars(select(Entity))).all()}
        mentions = (await db.scalars(select(Mention))).all()
    assert {r.origin for r in rels} == {"import"}
    # Said in March 2024, so it enters with that date and the nightly pass treats it as old.
    assert {r.valid_from.year for r in rels} == {2024}
    assert {e.imported_chat_id for e in evidence} == {kept[0].id}
    assert {m.imported_chat_id for m in mentions} == {kept[0].id}
    assert ents["Ana"].origin == "import"
