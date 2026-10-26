"""What to build from a project folder: its stack, the parts that run (a web front end and,
for web_fastapi and node, a back end), and each part's build context, a tar of the house
template plus the part's folder."""

import os
import re
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"
STACKS = ("static", "web_fastapi", "node")
STACK_WORD = re.compile(r"\b(web_fastapi|static|node)\b")
# Left out of a build context: git's folder, the commands' home, caches and test output.
# node_modules and .venv stay in: builds have no network, so they bring the dependencies.
SKIP = {".git", ".home", "__pycache__", ".pytest_cache", "test-results", "playwright-report"}
# FastAPI entry points tried in order: (file, module:attribute).
FASTAPI_ENTRIES = [
    ("main.py", "main:app"),
    ("app/main.py", "app.main:app"),
    ("src/main.py", "src.main:app"),
    ("app.py", "app:app"),
]


class BuildProblem(ValueError):
    """Why a project can't be built, in words a builder agent can act on."""


@dataclass
class Part:
    name: str  # web or api
    folder: str  # inside the project
    template: str  # static, fastapi or node
    port: int
    # The path its health check asks for, and the path prefix Traefik sends to it.
    health: str
    prefix: str | None = None
    build_args: dict | None = None


def detect_stack(root: Path, hint: str | None = None) -> str:
    """The stack the team chose: the run's own word for it, else what BRIEF.md or PLAN.md
    say, else the folders that exist."""
    if hint in STACKS:
        return hint
    for doc in ("BRIEF.md", "PLAN.md"):
        try:
            text = (root / doc).read_text("utf-8", errors="replace")[:20000]
        except OSError:
            continue
        if m := STACK_WORD.search(text):
            return m.group(1)
    if (root / "api").is_dir():
        return "web_fastapi"
    if (root / "server").is_dir():
        return "node"
    return "static"


# Folders that are never a static app's own folder.
NOT_APP = {"node_modules", "web", "api", "server", "dist", "tests"}


def static_folder(root: Path) -> str:
    """Where a static app is: the project root, or the one subfolder holding it when a builder
    made it in its own folder (npm create vite@latest my-app) and left the root empty. Serving
    the root then would put up the web server's own welcome page, not the app."""
    if (root / "package.json").is_file() or (root / "index.html").is_file():
        return "."
    found = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir()
        and not d.is_symlink()
        and not d.name.startswith(".")
        and d.name not in NOT_APP
        and ((d / "package.json").is_file() or (d / "index.html").is_file())
    )
    if len(found) == 1:
        return found[0]
    if (root / "web" / "package.json").is_file() or (root / "web" / "index.html").is_file():
        return "web"
    if found:
        raise BuildProblem(
            f"There is more than one app folder ({', '.join(found)}): a static app goes at the "
            "project root, with its package.json or index.html there."
        )
    raise BuildProblem(
        "There is no app to serve: a static app needs a package.json with a build script, or an "
        "index.html, at the project root."
    )


def parts(root: Path, stack: str) -> list[Part]:
    if stack == "static":
        return [Part("web", static_folder(root), "static", 8080, "/")]
    front = Part("web", "web", "static", 8080, "/")
    if stack == "node":
        return [front, Part("api", "server", "node", 3000, "/api/health", "/api")]
    api = root / "api"
    entry = next((e for f, e in FASTAPI_ENTRIES if (api / f).is_file()), None)
    if entry is None:
        raise BuildProblem(
            "The back end has no FastAPI entry point: put `app = FastAPI()` in api/main.py."
        )
    return [front, Part("api", "api", "fastapi", 8000, "/api/health", "/api", {"ENTRY": entry})]


def context(root: Path, part: Part) -> tempfile.SpooledTemporaryFile:
    """The part's build context: Dockerfile and friends from the template, and the part's
    folder under src/. Symlinks go in as links, never followed, so nothing outside the
    project can end up in an image."""
    src = (root / part.folder).resolve()
    if not src.is_dir() or not src.is_relative_to(root.resolve()):
        raise BuildProblem(f"The project has no {part.folder}/ folder for its {part.name}.")
    if not any(src.iterdir()):
        raise BuildProblem(f"{part.folder}/ is empty.")
    out = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with tarfile.open(fileobj=out, mode="w") as tar:
        for f in sorted((TEMPLATES / part.template).iterdir()):
            tar.add(f, arcname=f.name)
        for dirpath, dirnames, filenames in os.walk(src):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP)
            for name in [*dirnames, *sorted(filenames)]:
                path = here / name
                mode = path.lstat().st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                    continue  # sockets, fifos, devices
                arc = "src/" + path.relative_to(src).as_posix()
                info = tar.gettarinfo(str(path), arcname=arc)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if info.isreg():
                    with path.open("rb") as fh:
                        tar.addfile(info, fh)
                else:
                    tar.addfile(info)
    out.seek(0)
    return out
