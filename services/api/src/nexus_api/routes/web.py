"""The web, without leaving Nexus: a search whose results show in the app (the search box
widget, a custom widget's nexus.data("web.search")) and a reading view that opens a result's
page as clean text over the orb (nexus_common/web.py). Both are rate-limited per person."""

from fastapi import APIRouter, HTTPException, Query, status

from nexus_api.deps import CurrentUser, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common.web import WebError, cached_search, read_page

router = APIRouter(prefix="/api/web", tags=["web"])

SEARCHES_PER_HOUR = 120
READS_PER_HOUR = 120


@router.get("/search")
async def web_search(
    user: CurrentUser,
    redis: RedisDep,
    settings: SettingsDep,
    q: str = Query(min_length=1, max_length=300),
    sites: list[str] = Query(default_factory=list),
) -> list[dict]:
    if wait := await hit(redis, "web-search", str(user.id), SEARCHES_PER_HOUR, 3600):
        raise too_many(wait, "searches")
    try:
        return await cached_search(redis, settings, q, sites)
    except WebError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/read")
async def web_read(
    user: CurrentUser,
    redis: RedisDep,
    settings: SettingsDep,
    url: str = Query(min_length=8, max_length=2000),
) -> dict:
    if wait := await hit(redis, "web-read", str(user.id), READS_PER_HOUR, 3600):
        raise too_many(wait, "pages")
    try:
        return await read_page(settings, url)
    except WebError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
