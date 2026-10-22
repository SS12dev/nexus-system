import base64
import uuid

import httpx
import pytest
from sqlalchemy import select

import nexus_orchestrator.links as orch_links
from nexus_common.links import LinkError, github_preview, parse_link
from nexus_common.models import File, Link
from nexus_orchestrator.artifacts import save_generated
from nexus_orchestrator.tools import ToolContext, build_tools


def responses(table: dict[str, object]):
    """A GitHubGet answering from a table of path -> JSON (or text for raw reads)."""

    async def get(path: str, raw: bool):
        if path not in table:
            return httpx.Response(404, json={"message": "Not Found"})
        body = table[path]
        return httpx.Response(200, text=body) if raw else httpx.Response(200, json=body)

    return get


@pytest.mark.parametrize(
    "url,kind,title",
    [
        ("github.com/SS12dev/nexus-system", "github_repo", "SS12dev/nexus-system"),
        ("https://www.github.com/a/b.git/", "github_repo", "a/b"),
        ("https://github.com/a/b/blob/main/src/x.py", "github_file", "b/src/x.py"),
        ("https://github.com/a/b/tree/main/docs", "github_folder", "b/docs"),
        ("https://github.com/a/b/pull/32", "github_pr", "a/b #32"),
        ("https://github.com/a/b/issues/7", "github_issue", "a/b #7"),
        ("https://github.com/a/b/commit/abc1234def", "github_commit", "a/b @ abc1234"),
        ("https://github.com/orgs/acme", "web", "github.com/orgs/acme"),
        ("https://docs.google.com/document/d/1x/edit", "google_doc", "Google Doc"),
        ("https://docs.google.com/spreadsheets/d/1x/edit", "google_sheet", "Google Sheet"),
        ("https://youtu.be/abc", "video", "youtu.be/abc"),
        ("https://example.com/", "web", "example.com"),
    ],
)
def test_addresses_say_what_they_point_to(url, kind, title):
    t = parse_link(url)
    assert (t.kind, t.default_title()) == (kind, title)


@pytest.mark.parametrize(
    "url", ["", "javascript:alert(1)", "ftp://x.com/a", "https://me:pw@x.com", "notahost"]
)
def test_only_plain_web_addresses(url):
    with pytest.raises(LinkError):
        parse_link(url)


async def test_repo_preview_has_the_readme():
    get = responses(
        {
            "/repos/a/b": {"description": "A game", "default_branch": "main", "private": True},
            "/repos/a/b/readme": "# B\n\nPlay it.",
        }
    )
    p = await github_preview(parse_link("https://github.com/a/b"), get)
    assert p["description"] == "A game" and p["readme"].startswith("# B")


async def test_file_folder_and_pr_previews():
    text = base64.b64encode(b"print('hi')\n").decode()
    get = responses(
        {
            "/repos/a/b/contents/x.py?ref=main": {
                "name": "x.py", "size": 12, "encoding": "base64", "content": text,
            },
            "/repos/a/b/contents/src?ref=main": [
                {"name": "z.py", "type": "file", "size": 3},
                {"name": "lib", "type": "dir"},
            ],
            "/repos/a/b/pulls/3": {
                "title": "Add score", "state": "closed", "merged": True, "body": "Adds it.",
                "user": {"login": "nexus[bot]"}, "head": {"ref": "nexus/1"},
                "base": {"ref": "main"},
            },
        }
    )  # fmt: skip
    f = await github_preview(parse_link("https://github.com/a/b/blob/main/x.py"), get)
    assert f["text"] == "print('hi')\n"
    d = await github_preview(parse_link("https://github.com/a/b/tree/main/src"), get)
    assert [e["name"] for e in d["entries"]] == ["lib", "z.py"]
    pr = await github_preview(parse_link("https://github.com/a/b/pull/3"), get)
    assert (pr["state"], pr["author"], pr["head"]) == ("merged", "nexus[bot]", "nexus/1")


async def test_an_unreadable_link_still_saves(deps, owner, monkeypatch):
    user, chat_id = owner
    monkeypatch.setattr(orch_links, "reader", lambda deps, target: responses({}))
    link = await orch_links.keep_link(deps, user.id, "https://github.com/acme/secret")
    assert link.kind == "github_repo" and "private repo" in link.preview["error"]


def link_tools(deps, user_id, chat_id, run_id=None):
    async def emit(type_, **data):
        pass

    ctx = ToolContext(deps=deps, user_id=user_id, chat_id=chat_id, emit=emit, run_id=run_id)
    return ctx, {t.name: t for t in build_tools(ctx, ["save_link", "list_documents"])}


async def test_save_link_tool_keeps_one_row_per_address(deps, owner, monkeypatch):
    user, chat_id = owner
    monkeypatch.setattr(
        orch_links, "reader", lambda deps, target: responses({"/repos/acme/game": {}})
    )
    ctx, tools = link_tools(deps, user.id, chat_id)
    out = await tools["save_link"].ainvoke(
        {"url": "https://github.com/acme/game", "title": "Game", "note": "The 2048 code."}
    )
    assert out.startswith("Saved the link Game") and "github_repo" in out
    await tools["save_link"].ainvoke(
        {"url": "https://github.com/acme/game/", "note": "Now with a leaderboard."}
    )
    async with deps.sessionmaker() as db:
        rows = (await db.scalars(select(Link).where(Link.user_id == user.id))).all()
    assert len(rows) == 1 and rows[0].title == "Game"
    assert rows[0].note == "Now with a leaderboard." and rows[0].source == "assistant"
    assert [x.id for x in ctx.links] == [rows[0].id]
    listing = await tools["list_documents"].ainvoke({})
    assert "Links:" in listing and "https://github.com/acme/game" in listing
    bad = await tools["save_link"].ainvoke({"url": "javascript:alert(1)"})
    assert bad.startswith("Not saved")


async def test_assistant_decides_new_version_or_separate_file(deps, owner, storage):
    user, chat_id = owner
    deps.storage = storage
    first = await save_generated(deps, user.id, "README.md", b"# Game")
    again = await save_generated(deps, user.id, "README.md", b"# Game v2")
    other = await save_generated(deps, user.id, "README.md", b"# Notes", new_version=False)
    assert (again.version, again.version_of_id) == (2, first.id)
    assert (other.version, other.version_of_id) == (1, None)
    # Later versions go to the newest file of that name, the one being worked on.
    newer = await save_generated(deps, user.id, "README.md", b"# Notes v2")
    assert newer.version_of_id == other.id
    async with deps.sessionmaker() as db:
        assert len((await db.scalars(select(File).where(File.user_id == user.id))).all()) == 4


async def test_links_are_per_user(deps, owner, monkeypatch):
    user, _ = owner
    monkeypatch.setattr(orch_links, "reader", lambda deps, target: responses({}))
    await orch_links.keep_link(deps, user.id, "https://example.com/a")
    stranger = uuid.uuid4()
    _, tools = link_tools(deps, stranger, None)
    assert "no documents or links" in await tools["list_documents"].ainvoke({})
