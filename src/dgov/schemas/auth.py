from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

Role = str
ROLE_PATTERN = r"^[a-z][a-z0-9_-]{1,31}$"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    permissions: list[str]


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserRead


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,32}$")
    display_name: str = Field(min_length=1, max_length=64)
    role: Role = Field(default="viewer", pattern=ROLE_PATTERN)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    role: Role | None = Field(default=None, pattern=ROLE_PATTERN)
    is_active: bool | None = None


class MessageResponse(BaseModel):
    detail: str
