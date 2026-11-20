"""Search across everything: the Cmd-K bar and the Search page (nexus_common/find.py)."""

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from nexus_api.deps import CurrentUser, DbDep, EmbedderDep, RedisDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import find

router = APIRouter(prefix="/api/find", tags=["search"])

# Typing in the bar sends a request per pause; this is far above that and stops a runaway loop.
SEARCHES_PER_MINUTE = 240


class FindResultOut(BaseModel):
    kind: str
    id: str
    title: str
    url: str
    snippet: str
    detail: str
    at: datetime | None
    score: float
    matched: list[str]


class FindOut(BaseModel):
    query: str
    results: list[FindResultOut]
    semantic: bool
    took_ms: int
    more: list[str]


@router.get("", response_model=FindOut)
async def search_everything(
    user: CurrentUser,
    db: DbDep,
    redis: RedisDep,
    embedder: EmbedderDep,
    q: str = Query(min_length=1, max_length=find.MAX_QUERY_CHARS),
    kinds: list[str] = Query(default_factory=list),
    limit: int = Query(default=5, ge=1, le=50),
    meaning: bool = Query(default=True),
):
    """Results from every kind of thing, best first; `limit` is per kind. `meaning=false`
    skips the embedding step for the fastest as-you-type answer."""
    if wait := await hit(redis, "find", str(user.id), SEARCHES_PER_MINUTE, 60):
        raise too_many(wait, "searches")
    found = await find.search(
        db,
        user.id,
        q,
        embedder=embedder if meaning else None,
        kinds=[k for k in kinds if k in find.KINDS] or None,
        limit=limit,
        assistant=user.assistant,
    )
    return found.as_dict()
