from sqlalchemy import select

from nexus_common.models import (
    ChatImport,
    Entity,
    ImportedChat,
    Memory,
    MemoryKind,
    Mention,
    Relation,
    RelationEvidence,
)


async def test_an_export_is_queued_to_read_and_deleting_it_takes_back_what_it_taught(
    owner, queue, storage, sessionmaker
):
    bad = await owner.post("/api/imports", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert bad.status_code == 415
    up = await owner.post(
        "/api/imports", files={"file": ("export.zip", b"PK fake", "application/zip")}
    )
    assert up.status_code == 202 and up.json()["status"] == "reading"
    imp_id = up.json()["id"]
    assert [str(i) for i in queue.chat_imports] == [imp_id]
    assert f"imports/{imp_id}" in storage.objects

    # As if the orchestrator read it and the local model built two links from it, one of them
    # also said in a memory.
    me = (await owner.get("/api/me")).json()
    async with sessionmaker() as db:
        imp = await db.get(ChatImport, imp_id)
        imp.status, imp.conversations, imp.kept = "ready", 3, 1
        chat = ImportedChat(
            import_id=imp.id, user_id=me["id"], external_id="c1", title="Moving", text="USER: hi"
        )
        you = Entity(
            user_id=me["id"], type="person", name="You", aliases=[], summary="", is_self=True
        )
        pune = Entity(
            user_id=me["id"], type="place", name="Pune", aliases=[], summary="", origin="import"
        )
        ust = Entity(user_id=me["id"], type="organisation", name="UST", aliases=[], summary="")
        db.add_all([chat, you, pune, ust])
        await db.flush()
        rels = {}
        for name, dst in (("lives in", pune), ("works at", ust)):
            r = Relation(
                user_id=me["id"],
                src_id=you.id,
                dst_id=dst.id,
                predicate=name,
                fact="",
                origin="import",
            )
            db.add(r)
            await db.flush()
            rels[name] = r.id
            db.add(RelationEvidence(user_id=me["id"], relation_id=r.id, imported_chat_id=chat.id))
            db.add(Mention(user_id=me["id"], entity_id=dst.id, imported_chat_id=chat.id))
        said = Memory(user_id=me["id"], kind=MemoryKind.fact, content="Works at UST")
        db.add(said)
        await db.flush()
        db.add(Mention(user_id=me["id"], entity_id=ust.id, memory_id=said.id))
        db.add(RelationEvidence(user_id=me["id"], relation_id=rels["works at"], memory_id=said.id))
        await db.commit()

    listed = (await owner.get("/api/imports")).json()
    assert [(i["status"], i["kept"], i["read"]) for i in listed] == [("ready", 1, 0)]

    assert (await owner.delete(f"/api/imports/{imp_id}")).status_code == 204
    assert (await owner.get("/api/imports")).json() == []
    async with sessionmaker() as db:
        left = {e.name for e in (await db.scalars(select(Entity))).all()}
        rels_left = set((await db.scalars(select(Relation.id))).all())
    # What only the import said is gone, entities and links alike; what a memory also says
    # stays.
    assert left == {"You", "UST"} and rels_left == {rels["works at"]}
