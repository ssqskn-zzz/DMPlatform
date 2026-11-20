from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DatasetCreate(BaseModel):
    data_source_id: int
    schema_name: str = ""
    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = None
    dataset_type: str = "table"
    description: str | None = None
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    row_count: int | None = None


class DatasetUpdate(BaseModel):
    schema_name: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = None
    dataset_type: str | None = None
    description: str | None = None
    owner: str | None = None
    tags: list[str] | None = None
    row_count: int | None = None
    status: str | None = None


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    data_source_id: int
    schema_name: str
    name: str
    display_name: str | None
    dataset_type: str
    description: str | None
    owner: str | None
    tags: list[str]
    row_count: int | None
    status: str
    created_at: datetime
    updated_at: datetime


class ColumnCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = None
    data_type: str = "string"
    ordinal: int = 0
    nullable: bool = True
    is_primary_key: bool = False
    description: str | None = None
    standard_id: int | None = None


class ColumnUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = None
    data_type: str | None = None
    ordinal: int | None = None
    nullable: bool | None = None
    is_primary_key: bool | None = None
    description: str | None = None
    standard_id: int | None = None


class ColumnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    name: str
    display_name: str | None
    data_type: str
    ordinal: int
    nullable: bool
    is_primary_key: bool
    description: str | None
    standard_id: int | None
    created_at: datetime
    updated_at: datetime


class StandardCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    category: str | None = None
    data_type: str = "string"
    rule: str | None = None
    description: str | None = None
    extra: dict[str, Any] | None = None


class StandardUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    data_type: str | None = None
    rule: str | None = None
    description: str | None = None
    status: str | None = None
    extra: dict[str, Any] | None = None


class StandardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    category: str | None
    data_type: str
    rule: str | None
    description: str | None
    status: str
    extra: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
