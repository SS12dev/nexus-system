from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.queue import JobQueue
from nexus_api.sessions import user_for_token
from nexus_common import logs
from nexus_common.deployer import DeployerClient
from nexus_common.embeddings import CachedEmbedder
from nexus_common.litellm import LiteLLMAdmin
from nexus_common.llmstats import SpendLogs
from nexus_common.models import RoleName, User
from nexus_common.settings import Settings
from nexus_common.storage import Storage


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_embedder(request: Request) -> CachedEmbedder:
    return request.app.state.embedder


def get_gateway(request: Request) -> LiteLLMAdmin:
    return request.app.state.gateway


def get_deployer(request: Request) -> DeployerClient:
    return request.app.state.deployer


def get_spend_logs(request: Request) -> SpendLogs:
    return request.app.state.spend_logs


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
StorageDep = Annotated[Storage, Depends(get_storage)]
EmbedderDep = Annotated[CachedEmbedder, Depends(get_embedder)]
GatewayDep = Annotated[LiteLLMAdmin, Depends(get_gateway)]
DeployerDep = Annotated[DeployerClient, Depends(get_deployer)]
SpendLogsDep = Annotated[SpendLogs, Depends(get_spend_logs)]


async def current_user(request: Request, db: DbDep, settings: SettingsDep) -> User:
    user = await user_for_token(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    logs.bind(user_id=user.id)
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
# The Admin page (health, usage, audit log, backups, maintenance) is for owners only: admins
# can manage people, but only the owner sees how Nexus runs and what everyone costs.
OwnerUser = Annotated[User, Depends(require_role(RoleName.owner))]

RedisDep = Annotated[Redis, Depends(get_redis)]
