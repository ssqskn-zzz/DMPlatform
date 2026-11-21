from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import BadRequestError, ConflictError, NotFoundError
from dgov.core.modules import PERMISSION_CATALOG, ROLE_DEFAULTS
from dgov.models.auth import (
    PlatformRole,
    PlatformUser,
    RolePermission,
    UserPermissionOverride,
)
from dgov.schemas.authz import RoleCreate, RoleRead, RoleUpdate, UserPermissionRead


async def seed_roles(session: AsyncSession) -> None:
    for code, spec in ROLE_DEFAULTS.items():
        existing = (
            await session.execute(select(PlatformRole).where(PlatformRole.code == code))
        ).scalar_one_or_none()
        if existing is not None:
            continue
        role = PlatformRole(
            code=code,
            name=spec["name"],
            description=spec["description"],
            is_system=spec["is_system"],
        )
        session.add(role)
        await session.flush()
        session.add_all(
            RolePermission(role_id=role.id, permission=permission)
            for permission in spec["permissions"]
        )
    await session.commit()


async def role_permissions(session: AsyncSession, role_code: str) -> list[str]:
    role = (
        await session.execute(select(PlatformRole).where(PlatformRole.code == role_code))
    ).scalar_one_or_none()
    if role is None:
        return []
    result = await session.execute(
        select(RolePermission.permission)
        .where(RolePermission.role_id == role.id)
        .order_by(RolePermission.permission)
    )
    return list(result.scalars().all())


async def effective_permissions(session: AsyncSession, user: PlatformUser) -> list[str]:
    permissions = set(await role_permissions(session, user.role))
    overrides = (
        await session.execute(
            select(UserPermissionOverride).where(UserPermissionOverride.user_id == user.id)
        )
    ).scalars()
    for override in overrides:
        if override.effect == "deny":
            permissions.discard(override.permission)
        else:
            permissions.add(override.permission)
    return sorted(permissions)


async def has_permission(session: AsyncSession, user: PlatformUser, permission: str) -> bool:
    return permission in await effective_permissions(session, user)


async def list_roles(session: AsyncSession) -> list[RoleRead]:
    roles = list((await session.execute(select(PlatformRole).order_by(PlatformRole.id))).scalars())
    permission_rows = (
        await session.execute(
            select(RolePermission.role_id, RolePermission.permission).order_by(
                RolePermission.permission
            )
        )
    ).all()
    permission_map: dict[int, list[str]] = {}
    for role_id, permission in permission_rows:
        permission_map.setdefault(role_id, []).append(permission)

    user_rows = (
        await session.execute(
            select(PlatformUser.role, func.count()).group_by(PlatformUser.role)
        )
    ).all()
    user_counts = dict(user_rows)

    return [
        RoleRead(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            user_count=user_counts.get(role.code, 0),
            permissions=permission_map.get(role.id, []),
        )
        for role in roles
    ]


async def get_role(session: AsyncSession, role_id: int) -> PlatformRole:
    role = await session.get(PlatformRole, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    return role


async def create_role(session: AsyncSession, payload: RoleCreate) -> PlatformRole:
    existing = (
        await session.execute(select(PlatformRole).where(PlatformRole.code == payload.code))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("角色编码已存在")
    role = PlatformRole(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        is_system=False,
    )
    session.add(role)
    await session.flush()
    session.add_all(
        RolePermission(role_id=role.id, permission=permission)
        for permission in payload.permissions
    )
    await session.commit()
    await session.refresh(role)
    return role


async def update_role(
    session: AsyncSession, role: PlatformRole, payload: RoleUpdate
) -> PlatformRole:
    changes = payload.model_dump(exclude_unset=True, exclude={"permissions"})
    for field, value in changes.items():
        setattr(role, field, value)
    if payload.permissions is not None:
        if role.code == "admin" and "users:manage" not in payload.permissions:
            raise BadRequestError("系统管理员角色必须保留用户管理权限")
        await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        session.add_all(
            RolePermission(role_id=role.id, permission=permission)
            for permission in payload.permissions
        )
    await session.commit()
    await session.refresh(role)
    return role


async def delete_role(session: AsyncSession, role: PlatformRole) -> None:
    if role.is_system:
        raise BadRequestError("系统内置角色不能删除")
    in_use = (
        await session.execute(
            select(func.count()).select_from(PlatformUser).where(PlatformUser.role == role.code)
        )
    ).scalar_one()
    if in_use:
        raise ConflictError("该角色仍有用户使用，无法删除")
    await session.delete(role)
    await session.commit()


async def get_user_permissions(session: AsyncSession, user: PlatformUser) -> UserPermissionRead:
    role_perms = await role_permissions(session, user.role)
    overrides = list(
        (
            await session.execute(
                select(UserPermissionOverride)
                .where(UserPermissionOverride.user_id == user.id)
                .order_by(UserPermissionOverride.permission)
            )
        ).scalars()
    )
    return UserPermissionRead(
        user_id=user.id,
        username=user.username,
        role=user.role,
        role_permissions=role_perms,
        overrides=[
            {"permission": override.permission, "effect": override.effect} for override in overrides
        ],
        effective_permissions=await effective_permissions(session, user),
    )


async def set_user_overrides(
    session: AsyncSession, user: PlatformUser, overrides: list[dict[str, str]]
) -> UserPermissionRead:
    await session.execute(
        delete(UserPermissionOverride).where(UserPermissionOverride.user_id == user.id)
    )
    session.add_all(
        UserPermissionOverride(
            user_id=user.id,
            permission=item["permission"],
            effect=item["effect"],
        )
        for item in overrides
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("存在重复的权限覆盖项") from exc
    return await get_user_permissions(session, user)


def permission_catalog() -> dict[str, object]:
    return {
        "items": PERMISSION_CATALOG,
        "groups": sorted({item["group"] for item in PERMISSION_CATALOG}),
        "codes": sorted(item["code"] for item in PERMISSION_CATALOG),
    }
