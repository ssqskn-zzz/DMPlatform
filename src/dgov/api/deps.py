from collections.abc import Callable
from typing import Annotated

from elasticsearch import AsyncElasticsearch
from fastapi import Depends, Header
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.db import get_session
from dgov.core.es import get_es
from dgov.core.exceptions import AuthenticationError, PermissionDeniedError
from dgov.core.graph import get_driver
from dgov.models.auth import PlatformUser
from dgov.services import authz
from dgov.services.auth import resolve_user

SessionDep = Annotated[AsyncSession, Depends(get_session)]
EsDep = Annotated[AsyncElasticsearch, Depends(get_es)]
GraphDep = Annotated[AsyncDriver, Depends(get_driver)]


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("请先登录")
    return authorization.split(" ", 1)[1].strip()


TokenDep = Annotated[str, Depends(bearer_token)]


async def get_current_user(session: SessionDep, token: TokenDep) -> PlatformUser:
    return await resolve_user(session, token)


CurrentUserDep = Annotated[PlatformUser, Depends(get_current_user)]


def require_permission(permission: str) -> Callable:
    async def checker(session: SessionDep, user: CurrentUserDep) -> PlatformUser:
        if not await authz.has_permission(session, user, permission):
            raise PermissionDeniedError("当前角色无权执行此操作")
        return user

    return checker


def require_any_permission(*permissions: str) -> Callable:
    async def checker(session: SessionDep, user: CurrentUserDep) -> PlatformUser:
        granted = await authz.effective_permissions(session, user)
        if not set(permissions) & set(granted):
            raise PermissionDeniedError("当前角色无权执行此操作")
        return user

    return checker


UserManagerDep = Annotated[PlatformUser, Depends(require_permission("users:manage"))]
AuthzManagerDep = Annotated[PlatformUser, Depends(require_permission("authz:manage"))]
ConfigManagerDep = Annotated[PlatformUser, Depends(require_permission("config:write"))]
GovernanceWriterDep = Annotated[PlatformUser, Depends(require_permission("governance:write"))]
CollectionRunnerDep = Annotated[PlatformUser, Depends(require_permission("collection:run"))]
LogReaderDep = Annotated[PlatformUser, Depends(require_permission("logs:read"))]
