import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import AuthenticationError, BadRequestError, ConflictError, NotFoundError
from dgov.models.auth import AuthSession, PlatformRole, PlatformUser
from dgov.models.base import utcnow
from dgov.schemas.auth import UserCreate, UserRead, UserUpdate
from dgov.services import authz

DEFAULT_PASSWORD = "abc123456789"
PASSWORD_ITERATIONS = 240_000
SESSION_TTL = timedelta(hours=12)
DEFAULT_USERS = (
    ("admin", "系统管理员", "admin"),
    ("governance", "治理专员", "governance"),
    ("viewer", "业务访客", "viewer"),
)


async def to_user_read(session: AsyncSession, user: PlatformUser) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        permissions=await authz.effective_permissions(session, user),
    )


def _password_hash(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS
    )
    return digest.hex()


def set_password(user: PlatformUser, password: str) -> None:
    user.password_salt = secrets.token_hex(16)
    user.password_hash = _password_hash(password, user.password_salt)


def verify_password(user: PlatformUser, password: str) -> bool:
    candidate = _password_hash(password, user.password_salt)
    return hmac.compare_digest(candidate, user.password_hash)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def seed_default_users(session: AsyncSession) -> None:
    await authz.seed_roles(session)
    for username, display_name, role in DEFAULT_USERS:
        result = await session.execute(select(PlatformUser).where(PlatformUser.username == username))
        if result.scalar_one_or_none() is not None:
            continue
        user = PlatformUser(username=username, display_name=display_name, role=role)
        set_password(user, DEFAULT_PASSWORD)
        session.add(user)
    await session.commit()


async def authenticate(
    session: AsyncSession, username: str, password: str
) -> tuple[str, PlatformUser, datetime]:
    result = await session.execute(select(PlatformUser).where(PlatformUser.username == username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(user, password):
        raise AuthenticationError("用户名或密码错误")
    if not user.is_active:
        raise AuthenticationError("账号已停用，请联系管理员")

    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires_at = now + SESSION_TTL
    session.add(
        AuthSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            created_at=now,
            expires_at=expires_at,
        )
    )
    user.last_login_at = now
    await session.commit()
    await session.refresh(user)
    return token, user, expires_at


async def resolve_user(session: AsyncSession, token: str) -> PlatformUser:
    result = await session.execute(
        select(PlatformUser)
        .join(AuthSession, AuthSession.user_id == PlatformUser.id)
        .where(AuthSession.token_hash == _token_hash(token))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("登录状态无效，请重新登录")

    result = await session.execute(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    auth_session = result.scalar_one()
    if auth_session.expires_at <= utcnow():
        raise AuthenticationError("登录已过期，请重新登录")
    return user


async def logout(session: AsyncSession, token: str) -> None:
    await session.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    await session.commit()


async def logout_user_sessions(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    await session.commit()


async def change_password(session: AsyncSession, user: PlatformUser, current: str, new: str) -> None:
    if not verify_password(user, current):
        raise BadRequestError("当前密码不正确")
    if current == new:
        raise BadRequestError("新密码不能与当前密码相同")
    set_password(user, new)
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    await session.commit()


async def list_users(session: AsyncSession) -> list[PlatformUser]:
    result = await session.execute(select(PlatformUser).order_by(PlatformUser.id))
    return list(result.scalars().all())


async def get_user(session: AsyncSession, user_id: int) -> PlatformUser:
    user = await session.get(PlatformUser, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    return user


async def create_user(session: AsyncSession, payload: UserCreate) -> PlatformUser:
    result = await session.execute(select(PlatformUser).where(PlatformUser.username == payload.username))
    if result.scalar_one_or_none() is not None:
        raise ConflictError("用户名已存在")
    role = (
        await session.execute(select(PlatformRole).where(PlatformRole.code == payload.role))
    ).scalar_one_or_none()
    if role is None:
        raise BadRequestError("角色不存在")
    user = PlatformUser(
        username=payload.username,
        display_name=payload.display_name,
        role=payload.role,
    )
    set_password(user, DEFAULT_PASSWORD)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user: PlatformUser, payload: UserUpdate) -> PlatformUser:
    changes = payload.model_dump(exclude_unset=True)
    if "role" in changes:
        role = (
            await session.execute(select(PlatformRole).where(PlatformRole.code == changes["role"]))
        ).scalar_one_or_none()
        if role is None:
            raise BadRequestError("角色不存在")
    for field, value in changes.items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return user


async def reset_password(session: AsyncSession, user: PlatformUser) -> None:
    set_password(user, DEFAULT_PASSWORD)
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    await session.commit()
