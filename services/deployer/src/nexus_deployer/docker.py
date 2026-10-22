"""The few Docker Engine API calls the deployer makes, over HTTP to the Docker gate."""

import json
import struct
from typing import IO

import httpx

API = "/v1.44"


class DockerError(RuntimeError):
    pass


def demux(raw: bytes) -> str:
    """Container logs without a TTY come in frames: an 8-byte header (stream, size), then data."""
    out, i = [], 0
    while i + 8 <= len(raw):
        size = struct.unpack(">I", raw[i + 4 : i + 8])[0]
        out.append(raw[i + 8 : i + 8 + size])
        i += 8 + size
    if i == 0 and raw:
        return raw.decode("utf-8", errors="replace")
    return b"".join(out).decode("utf-8", errors="replace")


class Docker:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(base_url=url, timeout=60)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, ok=(200, 201, 204), **kw) -> httpx.Response:
        try:
            resp = await self._client.request(method, API + path, **kw)
        except httpx.HTTPError as exc:
            raise DockerError(f"Docker isn't reachable: {exc.__class__.__name__}") from exc
        if resp.status_code not in ok:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise DockerError(f"Docker {method} {path.split('?')[0]}: {message[:300]}")
        return resp

    async def build(
        self,
        tag: str,
        context: IO[bytes],
        labels: dict,
        args: dict | None = None,
        memory_mb: int = 2048,
        limit_s: float = 900,
    ) -> str:
        """Build an image with no network. Returns the build log; raises with its tail."""
        params = {
            "t": tag,
            "networkmode": "none",
            "forcerm": "1",
            "labels": json.dumps(labels),
            "memory": str(memory_mb * 1024 * 1024),
            "version": "1",
        }
        if args:
            params["buildargs"] = json.dumps(args)

        async def chunks():
            while data := context.read(1024 * 1024):
                yield data

        log, error = [], None
        try:
            async with self._client.stream(
                "POST",
                API + "/build",
                params=params,
                content=chunks(),
                headers={"Content-Type": "application/x-tar"},
                timeout=limit_s,
            ) as resp:
                if resp.status_code != 200:
                    raise DockerError(f"Docker refused the build: {(await resp.aread())[:300]!r}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    log.append(item.get("stream", ""))
                    error = item.get("error") or error
        except httpx.HTTPError as exc:
            raise DockerError(f"The build stopped: {exc.__class__.__name__}") from exc
        text = "".join(log)
        if error:
            raise DockerError(f"The build failed: {error.strip()}\n{text[-3000:]}")
        return text

    async def image_id(self, ref: str) -> str | None:
        resp = await self._call("GET", f"/images/{ref}/json", ok=(200, 403, 404))
        return resp.json().get("Id") if resp.status_code == 200 else None

    async def remove_image(self, ref: str) -> None:
        await self._call("DELETE", f"/images/{ref}", ok=(200, 403, 404, 409))

    async def containers(self, label: str) -> list[dict]:
        filters = json.dumps({"label": [label]})
        return (
            await self._call("GET", "/containers/json", params={"all": "1", "filters": filters})
        ).json()

    async def inspect(self, name: str) -> dict | None:
        resp = await self._call("GET", f"/containers/{name}/json", ok=(200, 403, 404))
        return resp.json() if resp.status_code == 200 else None

    async def create(self, name: str, spec: dict) -> str:
        resp = await self._call("POST", "/containers/create", params={"name": name}, json=spec)
        return resp.json()["Id"]

    async def start(self, name: str) -> None:
        await self._call("POST", f"/containers/{name}/start", ok=(204, 304))

    async def stop(self, name: str) -> None:
        await self._call("POST", f"/containers/{name}/stop", params={"t": "10"}, ok=(204, 304, 404))

    async def restart(self, name: str) -> None:
        await self._call("POST", f"/containers/{name}/restart", params={"t": "10"}, ok=(204,))

    async def remove(self, name: str) -> None:
        await self._call("DELETE", f"/containers/{name}", params={"force": "1"}, ok=(204, 404))

    async def wait(self, name: str, limit_s: float) -> int:
        resp = await self._call("POST", f"/containers/{name}/wait", timeout=limit_s)
        return resp.json().get("StatusCode", -1)

    async def logs(self, name: str, tail: int | str = 80) -> str:
        resp = await self._call(
            "GET",
            f"/containers/{name}/logs",
            params={"stdout": "1", "stderr": "1", "tail": str(tail)},
            ok=(200, 404),
        )
        return demux(resp.content) if resp.status_code == 200 else ""

    async def connect(self, network: str, container: str) -> None:
        await self._call("POST", f"/networks/{network}/connect", json={"Container": container})
