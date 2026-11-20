"""Search across everything (nexus_common/find.py, /api/find)."""

from sqlalchemy import select

from nexus_common import find, trash
from nexus_common.models import (
    Chat,
    Entity,
    File,
    FileChunk,
    FileStatus,
    Message,
    MessageRole,
    MessageStatus,
    Routine,
    User,
)


async def seed(sessionmaker, email="owner@example.com"):
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        chat = Chat(user_id=user.id, title="Planning the flat move")
        db.add(chat)
        await db.flush()
        db.add_all(
            [
                Message(
                    chat_id=chat.id,
                    role=MessageRole.user,
                    content="Which removals company quoted the lowest invoice for Pune?",
                    status=MessageStatus.complete,
                ),
                Message(
                    chat_id=chat.id,
                    role=MessageRole.assistant,
                    content="Agarwal Packers quoted 18,000 rupees, the lowest of the three.",
                    status=MessageStatus.complete,
                ),
            ]
        )
        f = File(
            user_id=user.id,
            name="Rental agreement.pdf",
            content_type="application/pdf",
            size=10,
            sha256="0" * 64,
            storage_key="k",
            status=FileStatus.ready,
        )
        db.add(f)
        await db.flush()
        db.add(
            FileChunk(
                file_id=f.id,
                user_id=user.id,
                idx=0,
                content="The tenant pays a security deposit of two months' rent.",
            )
        )
        person = Entity(
            user_id=user.id, type="person", name="Vishal Rao", aliases=[], summary="Flatmate"
        )
        db.add(person)
        db.add(
            Routine(
                user_id=user.id,
                name="Weekly invoice check",
                prompt="Look for unpaid invoices in Gmail",
                schedule="0 9 * * 1",
            )
        )
        await db.commit()
        return user.id, chat.id, f.id, person.id


async def test_finds_messages_files_people_and_routines(owner, sessionmaker):
    _uid, chat_id, file_id, person_id = await seed(sessionmaker)

    # Prefix matching: half a word already finds "invoice" in a message and a routine.
    body = (await owner.get("/api/find", params={"q": "invo"})).json()
    kinds = {(r["kind"], r["id"]) for r in body["results"]}
    assert ("chat", str(chat_id)) in kinds
    assert any(k == "routine" for k, _ in kinds)
    chat_hit = next(r for r in body["results"] if r["kind"] == "chat")
    # The link opens the chat at the message that matched, with the words highlighted.
    assert chat_hit["url"].startswith(f"/c/{chat_id}?m=")
    assert find.MARK_START in chat_hit["snippet"] and chat_hit["detail"].startswith("You")

    # Text inside a file.
    body = (await owner.get("/api/find", params={"q": "security deposit"})).json()
    assert body["results"][0]["kind"] == "file"
    assert body["results"][0]["url"] == f"/library?file={file_id}"

    # A person by name, ranked first because the name starts with the query.
    body = (await owner.get("/api/find", params={"q": "vish"})).json()
    assert body["results"][0] == {**body["results"][0], "kind": "person", "id": str(person_id)}

    # A chat by its title.
    body = (await owner.get("/api/find", params={"q": "flat move"})).json()
    assert body["results"][0]["kind"] == "chat" and "title" in body["results"][0]["matched"]


async def test_kinds_filter_limit_and_more(owner, sessionmaker):
    await seed(sessionmaker)
    body = (await owner.get("/api/find", params={"q": "invoice", "kinds": ["routine"]})).json()
    assert {r["kind"] for r in body["results"]} == {"routine"}
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        for i in range(4):
            db.add(Chat(user_id=user.id, title=f"Invoice question {i}"))
        await db.commit()
    body = (
        await owner.get("/api/find", params={"q": "invoice", "kinds": ["chat"], "limit": 2})
    ).json()
    assert len(body["results"]) == 2 and body["more"] == ["chat"]


async def test_meaning_matches_and_fallback_to_words(owner, sessionmaker, fake_embedder):
    await owner.post("/api/memories", json={"content": "Allergic to peanuts"})
    # The fake embedder points texts sharing words the same way.
    body = (await owner.get("/api/find", params={"q": "allergic peanuts"})).json()
    assert body["semantic"] is True
    assert any(r["kind"] == "memory" and "meaning" in r["matched"] for r in body["results"])

    fake_embedder.down = True
    body = (await owner.get("/api/find", params={"q": "allergic peanut"})).json()
    assert body["semantic"] is False
    assert [r["kind"] for r in body["results"]] == ["memory"]
    # meaning=false skips the embedder outright.
    calls = fake_embedder.calls
    fake_embedder.down = False
    body = (await owner.get("/api/find", params={"q": "allergic", "meaning": False})).json()
    assert body["semantic"] is False and fake_embedder.calls == calls


async def test_trash_and_other_people_are_hidden(owner, sessionmaker, client_for, invite):
    uid, chat_id, file_id, _ = await seed(sessionmaker)
    async with sessionmaker() as db:
        await trash.add(db, uid, "chat", chat_id, "Planning the flat move")
        await trash.add(db, uid, "file", file_id, "Rental agreement.pdf")
        await db.commit()
    body = (await owner.get("/api/find", params={"q": "invoice"})).json()
    assert all(r["kind"] != "chat" for r in body["results"])
    body = (await owner.get("/api/find", params={"q": "deposit"})).json()
    assert body["results"] == []

    await invite(owner, "friend@example.com", "member")
    friend = await client_for("friend@example.com")
    assert (await friend.get("/api/find", params={"q": "vishal"})).json()["results"] == []


async def test_odd_queries_are_safe(owner, sessionmaker):
    await seed(sessionmaker)
    for q in ["'", "a & b | !c", "100%", "under_score", ":*", "(((", "🙂", "x" * 200]:
        resp = await owner.get("/api/find", params={"q": q})
        assert resp.status_code == 200, (q, resp.text)
    assert (await owner.get("/api/find", params={"q": "x" * 201})).status_code == 422
    assert (await owner.get("/api/find")).status_code == 422


def test_parse_and_scores():
    q = find.parse("  Invo  march!  ")
    assert q.raw == "Invo march!" and q.tsquery == "invo:* & march:*"
    assert find.parse("50%_off").like == "%50\\%\\_off%"
    assert find.parse("&|!:").tsquery == ""
    assert find.title_score("Invo march!", q) == 1.0
    assert find.title_score("invo march! notes", q) == 0.9
    assert find.title_score("March invoice", find.parse("invoice march")) == 0.6
    assert find.title_score("Other", q) == 0.0
    assert find.clean_snippet("a" * 300, 10).endswith("…")
    assert find.clean_snippet(find.MARK_START + "b" * 300, 10).endswith(find.MARK_END)
