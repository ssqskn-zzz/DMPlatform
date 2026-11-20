from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataSourceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(max_length=32, description="mysql / postgresql / hive / clickhouse / ...")
    host: str = Field(max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    database: str | None = None
    username: str | None = None
    extra: dict[str, Any] | None = None
    description: str | None = None


class DataSourceCreate(DataSourceBase):
    password: str | None = None


class DataSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    type: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = None
    username: str | None = None
    password: str | None = None
    extra: dict[str, Any] | None = None
    description: str | None = None
    status: str | None = None


class DataSourceRead(DataSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    updated_at: datetime
