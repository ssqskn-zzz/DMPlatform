from fastapi import APIRouter, status

from dgov.api.deps import SessionDep, UserManagerDep
from dgov.core.exceptions import ConflictError
from dgov.schemas.auth import MessageResponse, UserCreate, UserRead, UserUpdate
from dgov.services import auth as service
from dgov.services.auth import DEFAULT_PASSWORD

router = APIRouter(
    prefix="/users",
    tags=["users"],
)


@router.get("", response_model=list[UserRead])
async def list_users(session: SessionDep, manager: UserManagerDep) -> list[UserRead]:
    users = await service.list_users(session)
    return [service.to_user_read(user) for user in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, session: SessionDep, manager: UserManagerDep
) -> UserRead:
    user = await service.create_user(session, payload)
    return service.to_user_read(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int, payload: UserUpdate, session: SessionDep, manager: UserManagerDep
) -> UserRead:
    user = await service.get_user(session, user_id)
    changes = payload.model_dump(exclude_unset=True)
    removes_admin = user.id == manager.id and changes.get("role", user.role) != "admin"
    disables_self = user.id == manager.id and changes.get("is_active") is False
    if removes_admin or disables_self:
        raise ConflictError("不能停用或降级当前登录的管理员账号")
    if changes.get("is_active") is False:
        await service.logout_user_sessions(session, user.id)
    user = await service.update_user(session, user, payload)
    return service.to_user_read(user)


@router.post("/{user_id}/reset-password", response_model=MessageResponse)
async def reset_password(user_id: int, session: SessionDep, manager: UserManagerDep) -> MessageResponse:
    user = await service.get_user(session, user_id)
    await service.reset_password(session, user)
    return MessageResponse(detail=f"密码已重置为 {DEFAULT_PASSWORD}，该用户所有会话已失效")
