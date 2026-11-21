from fastapi import APIRouter

from dgov.api.deps import CurrentUserDep, SessionDep, TokenDep
from dgov.schemas.auth import LoginRequest, LoginResponse, MessageResponse, PasswordChangeRequest, UserRead
from dgov.services import auth as service

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: SessionDep) -> LoginResponse:
    token, user, expires_at = await service.authenticate(session, payload.username, payload.password)
    return LoginResponse(token=token, expires_at=expires_at, user=service.to_user_read(user))


@router.post("/logout", response_model=MessageResponse)
async def logout(session: SessionDep, token: TokenDep) -> MessageResponse:
    await service.logout(session, token)
    return MessageResponse(detail="已退出登录")


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUserDep) -> UserRead:
    return service.to_user_read(user)


@router.post("/password", response_model=MessageResponse)
async def change_password(
    payload: PasswordChangeRequest, session: SessionDep, user: CurrentUserDep
) -> MessageResponse:
    await service.change_password(session, user, payload.current_password, payload.new_password)
    return MessageResponse(detail="密码已更新，请重新登录")
