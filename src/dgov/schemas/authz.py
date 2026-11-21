from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dgov.core.modules import PERMISSION_CODES

Effect = Literal["allow", "deny"]


class PermissionItem(BaseModel):
    code: str
    name: str
    group: str


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    is_system: bool
    user_count: int = 0
    permissions: list[str] = []


class RoleCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = []

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - PERMISSION_CODES)
        if unknown:
            raise ValueError(f"未知权限项: {', '.join(unknown)}")
        return sorted(set(value))


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - PERMISSION_CODES)
        if unknown:
            raise ValueError(f"未知权限项: {', '.join(unknown)}")
        return sorted(set(value))


class PermissionOverride(BaseModel):
    permission: str
    effect: Effect = "allow"

    @field_validator("permission")
    @classmethod
    def validate_permission(cls, value: str) -> str:
        if value not in PERMISSION_CODES:
            raise ValueError(f"未知权限项: {value}")
        return value


class UserPermissionUpdate(BaseModel):
    overrides: list[PermissionOverride] = []


class UserPermissionRead(BaseModel):
    user_id: int
    username: str
    role: str
    role_permissions: list[str]
    overrides: list[PermissionOverride]
    effective_permissions: list[str]
