"""Client for the sandbox service, where agent code runs with no network."""

import base64
from dataclasses import dataclass, field

import httpx

from jarvis_common.settings import Settings


class SandboxError(RuntimeError):
    """Safe to show to the agent."""


@dataclass
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    # out/ files by path (e.g. "out/report.pdf").
    files: dict[str, bytes] = field(default_factory=dict)
    skipped_files: list[str] = field(default_factory=list)


class SandboxClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        headers = {}
        if settings.sandbox_token:
            headers["Authorization"] = f"Bearer {settings.sandbox_token}"
        self._client = client or httpx.AsyncClient(
            base_url=settings.sandbox_url,
            headers=headers,
            timeout=settings.sandbox_timeout_s + 30,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run(
        self, code: str, language: str = "python", files: dict[str, bytes] | None = None
    ) -> SandboxResult:
        body = {
            "language": language,
            "code": code,
            "files": {k: base64.b64encode(v).decode() for k, v in (files or {}).items()},
            "timeout_s": self.settings.sandbox_timeout_s,
        }
        try:
            resp = await self._client.post("/run", json=body)
        except httpx.HTTPError as exc:
            raise SandboxError("The sandbox isn't reachable right now.") from exc
        if resp.status_code >= 400:
            raise SandboxError(
                f"The sandbox refused the job ({resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()
        return SandboxResult(
            exit_code=data["exit_code"],
            stdout=data["stdout"],
            stderr=data["stderr"],
            timed_out=data["timed_out"],
            duration_ms=data["duration_ms"],
            files={k: base64.b64decode(v) for k, v in data["files"].items()},
            skipped_files=data.get("skipped_files", []),
        )
