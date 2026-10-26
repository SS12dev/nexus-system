import os
import tarfile

import pytest

from nexus_deployer.build import BuildProblem, context, detect_stack, parts


def names(ctx) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=ctx) as tar:
        return {m.name: m for m in tar.getmembers()}


def test_stack_comes_from_the_hint_then_the_brief_then_the_folders(tmp_path):
    assert detect_stack(tmp_path, "node") == "node"
    (tmp_path / "BRIEF.md").write_text("Stack: web_fastapi, because it stores scores.")
    assert detect_stack(tmp_path, None) == "web_fastapi"
    assert detect_stack(tmp_path, "nonsense") == "web_fastapi"
    (tmp_path / "BRIEF.md").unlink()
    (tmp_path / "server").mkdir()
    assert detect_stack(tmp_path) == "node"
    assert detect_stack(tmp_path / "server") == "static"


def test_parts_per_stack(tmp_path):
    (tmp_path / "index.html").write_text("<h1>2048</h1>")
    assert [(p.name, p.folder) for p in parts(tmp_path, "static")] == [("web", ".")]
    (tmp_path / "index.html").unlink()
    node = parts(tmp_path, "node")
    assert [(p.name, p.folder, p.template, p.prefix) for p in node] == [
        ("web", "web", "static", None),
        ("api", "server", "node", "/api"),
    ]
    with pytest.raises(BuildProblem, match="api/main.py"):
        parts(tmp_path, "web_fastapi")
    (tmp_path / "api" / "app").mkdir(parents=True)
    (tmp_path / "api" / "app" / "main.py").write_text("app = None")
    api = parts(tmp_path, "web_fastapi")[1]
    assert api.build_args == {"ENTRY": "app.main:app"} and api.health == "/api/health"


def test_a_static_app_built_in_its_own_folder_is_found(tmp_path):
    """The solitaire team ran npm create vite@latest solitaire-app: serving the root put up
    nginx's welcome page instead of the game."""
    with pytest.raises(BuildProblem, match="no app to serve"):
        parts(tmp_path, "static")
    (tmp_path / "BRIEF.md").write_text("Stack: static")
    (tmp_path / "package-lock.json").write_text('{"packages": {}}')
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "package.json").write_text("{}")
    (tmp_path / "solitaire-app").mkdir()
    (tmp_path / "solitaire-app" / "package.json").write_text('{"scripts": {"build": "vite"}}')
    assert parts(tmp_path, "static")[0].folder == "solitaire-app"
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "index.html").write_text("<p>")
    with pytest.raises(BuildProblem, match="more than one app folder"):
        parts(tmp_path, "static")
    # An app at the root always wins.
    (tmp_path / "index.html").write_text("<h1>")
    assert parts(tmp_path, "static")[0].folder == "."


def test_context_has_the_template_and_the_part_only(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("outside")
    root = tmp_path / "p"
    (root / "web" / "node_modules" / "vite").mkdir(parents=True)
    (root / "web" / "node_modules" / "vite" / "index.js").write_text("x")
    (root / "web" / "index.html").write_text("<h1>hi</h1>")
    (root / "web" / ".git").mkdir()
    (root / "web" / ".git" / "config").write_text("token")
    (root / ".home").mkdir()
    os.symlink(secret, root / "web" / "leak.txt")
    os.mkfifo(root / "web" / "pipe")
    web = parts(root, "node")[0]
    members = names(context(root, web))
    assert {"Dockerfile", "nginx.conf", "src/index.html", "src/node_modules/vite/index.js"} <= set(
        members
    )
    assert not any(".git" in n or ".home" in n or "pipe" in n for n in members)
    # A link goes in as a link, never as the file it points to.
    assert members["src/leak.txt"].issym() and members["src/index.html"].uid == 0


def test_context_refuses_missing_or_empty_folders(tmp_path):
    (tmp_path / "web").mkdir()
    with pytest.raises(BuildProblem, match="empty"):
        context(tmp_path, parts(tmp_path, "node")[0])
    with pytest.raises(BuildProblem, match="no server/"):
        context(tmp_path, parts(tmp_path, "node")[1])
