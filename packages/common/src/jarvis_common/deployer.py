"""Client for the deployer service, shared by the orchestrator (deploys) and the API (the Apps
page's stop, restart and delete)."""

from dataclasses import dataclass, field

import httpx

from jarvis_common.settings import Settings


class DeployerError(RuntimeError):
    """Safe to show to the user."""


@dataclass
class DeployResult:
    ok: bool
    url: str
    stack: str | None = None
    error: str | None = None
    logs: str = ""
    screenshot: str | None = None
    console_errors: list[str] = field(default_factory=list)


class DeployerClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        headers = {}
        if settings.deployer_token:
            headers["Authorization"] = f"Bearer {settings.deployer_token}"
        self._client = client or httpx.AsyncClient(
            base_url=settings.deployer_url, headers=headers, timeout=settings.deploy_timeout_s
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, **kw) -> dict | list:
        try:
            resp = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise DeployerError("The deployer isn't reachable right now.") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = None
            raise DeployerError(detail or f"The deployer refused ({resp.status_code}).")
        return resp.json()

    async def deploy(
        self, slug: str, *, app_id: str, owner: str, user: str, project: str,
        stack: str | None = None, internet: bool = False,
    ) -> DeployResult:  # fmt: skip
        body = {"app_id": app_id, "owner": owner, "user": user, "project": project,
                "stack": stack, "internet": internet}  # fmt: skip
        return DeployResult(**await self._call("POST", f"/apps/{slug}/deploy", json=body))

    async def apps(self) -> list[dict]:
        return await self._call("GET", "/apps")

    async def stop(self, slug: str) -> None:
        await self._call("POST", f"/apps/{slug}/stop")

    async def restart(self, slug: str) -> None:
        await self._call("POST", f"/apps/{slug}/restart")

    async def delete(self, slug: str) -> None:
        await self._call("DELETE", f"/apps/{slug}")
