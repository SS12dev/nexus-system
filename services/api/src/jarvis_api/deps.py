from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.queue import TurnQueue
from jarvis_api.sessions import user_for_token
from jarvis_common.litellm import LiteLLMAdmin
from jarvis_common.models import RoleName, User
from jarvis_common.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_queue(request: Request) -> TurnQueue:
    return request.app.state.queue


def get_gateway(request: Request) -> LiteLLMAdmin:
    return request.app.state.gateway


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
QueueDep = Annotated[TurnQueue, Depends(get_queue)]
GatewayDep = Annotated[LiteLLMAdmin, Depends(get_gateway)]


async def current_user(request: Request, db: DbDep, settings: SettingsDep) -> User:
    user = await user_for_token(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(minimum: RoleName) -> Callable:
    async def check(user: CurrentUser) -> User:
        if not user.has_role(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum} role")
        return user

    return check


MemberUser = Annotated[User, Depends(require_role(RoleName.member))]
AdminUser = Annotated[User, Depends(require_role(RoleName.admin))]
