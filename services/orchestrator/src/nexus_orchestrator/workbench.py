"""Client for the workbench service, where dev teams keep a project folder between steps."""

from dataclasses import dataclass

import httpx

from nexus_common.settings import Settings


class WorkbenchError(RuntimeError):
    """Safe to show to the agent."""


@dataclass
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


def _detail(resp: httpx.Response) -> str | None:
    """The service's own explanation, written to be shown to the agent."""
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return None
    return detail if isinstance(detail, str) else None


class WorkbenchClient:
    """Every call names the user and project, and the project is created the first time it
    is used, so tools never have to set one up."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        headers = {}
        if settings.workbench_token:
            headers["Authorization"] = f"Bearer {settings.workbench_token}"
        self._client = client or httpx.AsyncClient(
            base_url=settings.workbench_url,
            headers=headers,
            timeout=settings.workbench_max_timeout_s + 30,
        )
        self._ready: set[tuple[str, str]] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            return await self._client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise WorkbenchError("The workbench isn't reachable right now.") from exc

    async def ensure(self, user: str, project: str) -> None:
        if (user, project) in self._ready:
            return
        resp = await self._send("PUT", f"/projects/{user}/{project}")
        if resp.status_code >= 400:
            raise WorkbenchError(f"Couldn't open the project ({resp.status_code}).")
        self._ready.add((user, project))

    async def _call(self, method: str, user: str, project: str, path: str, **kw) -> dict:
        await self.ensure(user, project)
        url = f"/projects/{user}/{project}{path}"
        resp = await self._send(method, url, **kw)
        if resp.status_code == 404 and _detail(resp) == "No such project.":
            # Deleted since we last looked: make it again.
            self._ready.discard((user, project))
            await self.ensure(user, project)
            resp = await self._send(method, url, **kw)
        if resp.status_code >= 400:
            raise WorkbenchError(_detail(resp) or f"The workbench refused ({resp.status_code}).")
        return resp.json()

    async def tree(self, user: str, project: str, path: str = "", depth: int = 3) -> dict:
        return await self._call(
            "GET", user, project, "/tree", params={"path": path, "depth": depth}
        )

    async def read(self, user: str, project: str, path: str) -> dict:
        return await self._call("GET", user, project, "/file", params={"path": path})

    async def write(self, user: str, project: str, path: str, content: str) -> dict:
        return await self._call(
            "PUT", user, project, "/file", json={"path": path, "content": content}
        )

    async def edit(
        self, user: str, project: str, path: str, old: str, new: str, replace_all: bool = False
    ) -> dict:
        body = {"path": path, "old": old, "new": new, "replace_all": replace_all}
        return await self._call("POST", user, project, "/edit", json=body)

    async def run(
        self, user: str, project: str, command: str, timeout_s: int | None = None
    ) -> CommandResult:
        timeout = min(
            timeout_s or self.settings.workbench_timeout_s, self.settings.workbench_max_timeout_s
        )
        data = await self._call(
            "POST", user, project, "/run", json={"command": command, "timeout_s": timeout}
        )
        return CommandResult(**data)

    async def diff(self, user: str, project: str, ref: str = "HEAD") -> dict:
        return await self._call("GET", user, project, "/git/diff", params={"ref": ref})

    async def commit(self, user: str, project: str, message: str) -> dict:
        return await self._call("POST", user, project, "/git/commit", json={"message": message})

    async def identity(self, user: str, project: str, name: str, email: str) -> dict:
        return await self._call(
            "PUT", user, project, "/git/identity", json={"name": name, "email": email}
        )

    async def push(self, user: str, project: str, url: str, branch: str, token: str) -> dict:
        body = {"url": url, "branch": branch, "token": token}
        return await self._call("POST", user, project, "/git/push", json=body)
