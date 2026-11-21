from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from dgov.models.base import Base, IdMixin, TimestampMixin


class PlatformUser(IdMixin, TimestampMixin, Base):
    __tablename__ = "platform_user"

    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))
    password_salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(24), default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class AuthSession(IdMixin, Base):
    __tablename__ = "auth_session"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("platform_user.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
