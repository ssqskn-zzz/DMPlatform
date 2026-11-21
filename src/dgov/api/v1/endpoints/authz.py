from fastapi import APIRouter, status

from dgov.api.deps import AuthzManagerDep, SessionDep
from dgov.schemas.authz import (
    PermissionItem,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    UserPermissionRead,
    UserPermissionUpdate,
)
from dgov.services import auth as auth_service
from dgov.services import authz as service

router = APIRouter(tags=["authorization"])


@router.get("/permissions", response_model=dict)
async def permission_catalog(user: AuthzManagerDep) -> dict:
    return service.permission_catalog()


@router.get("/permissions/items", response_model=list[PermissionItem])
async def permission_items(user: AuthzManagerDep) -> list[PermissionItem]:
    return [PermissionItem(**item) for item in service.permission_catalog()["items"]]


@router.get("/roles", response_model=list[RoleRead])
async def list_roles(session: SessionDep, user: AuthzManagerDep) -> list[RoleRead]:
    return await service.list_roles(session)


@router.post("/roles", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate, session: SessionDep, user: AuthzManagerDep
) -> RoleRead:
    role = await service.create_role(session, payload)
    roles = await service.list_roles(session)
    return next(item for item in roles if item.id == role.id)


@router.patch("/roles/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: int, payload: RoleUpdate, session: SessionDep, user: AuthzManagerDep
) -> RoleRead:
    role = await service.get_role(session, role_id)
    await service.update_role(session, role, payload)
    roles = await service.list_roles(session)
    return next(item for item in roles if item.id == role_id)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: int, session: SessionDep, user: AuthzManagerDep) -> None:
    role = await service.get_role(session, role_id)
    await service.delete_role(session, role)


@router.get("/users/{user_id}/permissions", response_model=UserPermissionRead)
async def user_permissions(
    user_id: int, session: SessionDep, user: AuthzManagerDep
) -> UserPermissionRead:
    target = await auth_service.get_user(session, user_id)
    return await service.get_user_permissions(session, target)


@router.put("/users/{user_id}/permissions", response_model=UserPermissionRead)
async def update_user_permissions(
    user_id: int,
    payload: UserPermissionUpdate,
    session: SessionDep,
    user: AuthzManagerDep,
) -> UserPermissionRead:
    target = await auth_service.get_user(session, user_id)
    return await service.set_user_overrides(
        session, target, [item.model_dump() for item in payload.overrides]
    )
