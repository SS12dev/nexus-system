"""Text embeddings through LiteLLM, with a cache keyed by a hash of the text.

The default `nexus-embed` alias runs bge-m3 in Ollama on the host, so embedding is free and
local. Every stored vector records the model that made it; when the model changes, the
backfill jobs re-embed whatever was made with the old one.
"""

import hashlib
import logging
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import EMBEDDING_DIM, EmbeddingCache
from nexus_common.settings import Settings

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """Vectors for `texts`, in order, and the name of the model that made them."""
        ...

    async def aclose(self) -> None: ...


class LiteLLMEmbedder:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        key = (
            settings.litellm_master_key
            if settings.litellm_virtual_keys
            else settings.litellm_api_key
        )
        self._client = client or httpx.AsyncClient(
            base_url=settings.litellm_base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        try:
            resp = await self._client.post(
                "/v1/embeddings",
                # Embedded text is everyone's memories and documents, under the master key
                # rather than theirs, so it never goes to Langfuse while TRACE_MASKING is on.
                json={
                    "model": self.settings.embedding_model,
                    "input": texts,
                    "metadata": (
                        {"mask_input": True, "mask_output": True}
                        if self.settings.trace_masking
                        else {}
                    ),
                },
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Embedding gateway unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise EmbeddingError(f"Embedding failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        rows = sorted(body["data"], key=lambda d: d["index"])
        vectors = [r["embedding"] for r in rows]
        if any(len(v) != EMBEDDING_DIM for v in vectors):
            raise EmbeddingError(
                f"{self.settings.embedding_model} returned {len(vectors[0])}-dimensional vectors; "
                f"Nexus stores {EMBEDDING_DIM}. Use a model with that width."
            )
        return vectors, body.get("model") or self.settings.embedding_model

    async def aclose(self) -> None:
        await self._client.aclose()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class CachedEmbedder:
    """Wraps an embedder with the shared embedding_cache table."""

    def __init__(self, inner: Embedder, batch_size: int = 32):
        self.inner = inner
        self.batch_size = batch_size
        # Learned from the first call: the cache is keyed by the model that actually answered.
        self.model: str | None = None

    async def embed(self, db: AsyncSession, texts: list[str]) -> tuple[list[list[float]], str]:
        hashes = [text_hash(t) for t in texts]
        found: dict[str, list[float]] = {}
        if self.model and texts:
            rows = await db.execute(
                select(EmbeddingCache.content_hash, EmbeddingCache.embedding).where(
                    EmbeddingCache.model == self.model,
                    EmbeddingCache.content_hash.in_(set(hashes)),
                )
            )
            found = {h: list(e) for h, e in rows.all()}
        missing = [i for i, h in enumerate(hashes) if h not in found]
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            vectors, model = await self.inner.embed([texts[i] for i in batch])
            if self.model != model:
                self.model = model
            for i, vec in zip(batch, vectors, strict=True):
                found[hashes[i]] = vec
            await db.execute(
                insert(EmbeddingCache)
                .values(
                    [
                        {"content_hash": hashes[i], "model": model, "embedding": found[hashes[i]]}
                        for i in batch
                    ]
                )
                .on_conflict_do_nothing()
            )
        if self.model is None:
            # Nothing to embed and nothing learned yet; ask once so callers know the model.
            _vectors, self.model = await self.inner.embed(["ping"])
        return [found[h] for h in hashes], self.model

    async def embed_one(self, db: AsyncSession, text: str) -> tuple[list[float], str]:
        vectors, model = await self.embed(db, [text])
        return vectors[0], model

    async def current_model(self, db: AsyncSession) -> str:
        if self.model is None:
            await self.embed(db, [])
        return self.model

    async def try_embed_one(self, db: AsyncSession, text: str) -> tuple[list[float] | None, str]:
        """Like embed_one, but a gateway failure gives (None, "") so callers can fall back to
        keyword search and let the backfill embed it later."""
        try:
            return await self.embed_one(db, text)
        except EmbeddingError as exc:
            log.warning("Embedding unavailable: %s", exc)
            return None, ""
