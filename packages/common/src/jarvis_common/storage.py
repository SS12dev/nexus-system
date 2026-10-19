"""Object storage for uploads: MinIO locally, any S3-compatible store (such as R2) later."""

import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Protocol

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import ClientError

from jarvis_common.settings import Settings


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    def stream(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def aclose(self) -> None: ...


def file_key(user_id: uuid.UUID, file_id: uuid.UUID) -> str:
    """Where an upload's bytes live. The name is kept in the database, not in the key."""
    return f"users/{user_id}/files/{file_id}"


class S3Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.s3_bucket
        self._stack = AsyncExitStack()
        self._client = None

    async def _c(self):
        if self._client is None:
            session = get_session()
            self._client = await self._stack.enter_async_context(
                session.create_client(
                    "s3",
                    endpoint_url=self.settings.s3_endpoint_url,
                    aws_access_key_id=self.settings.s3_access_key,
                    aws_secret_access_key=self.settings.s3_secret_key,
                    region_name=self.settings.s3_region,
                    config=AioConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
                )
            )
        return self._client

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        client = await self._c()
        await client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        client = await self._c()
        resp = await client.get_object(Bucket=self.bucket, Key=key)
        async with resp["Body"] as body:
            return await body.read()

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        client = await self._c()
        resp = await client.get_object(Bucket=self.bucket, Key=key)
        async with resp["Body"] as body:
            async for chunk in body.iter_chunks(1 << 16):
                yield chunk

    async def delete(self, key: str) -> None:
        client = await self._c()
        try:
            await client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._client = None


class MemoryStorage:
    """In-process storage for tests."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        data = self.objects[key][0]
        for i in range(0, len(data), 1 << 16):
            yield data[i : i + (1 << 16)]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def aclose(self) -> None:
        pass
