from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dgov.services.metadata_client import SOURCE_ASSETS

IntervalUnit = Literal["minute", "hour", "day"]


class CollectionJobBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    datasource_id: int
    assets: list[str] = Field(default_factory=lambda: list(SOURCE_ASSETS))
    interval_unit: IntervalUnit = "hour"
    interval_value: int = Field(default=1, ge=1, le=1440)
    enabled: bool = True

    @field_validator("assets")
    @classmethod
    def validate_assets(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("至少选择一项采集资产")
        unknown = sorted(set(value) - set(SOURCE_ASSETS))
        if unknown:
            raise ValueError(f"不支持的采集资产: {', '.join(unknown)}")
        return sorted(set(value), key=list(SOURCE_ASSETS).index)


class CollectionJobCreate(CollectionJobBase):
    pass


class CollectionJobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    datasource_id: int | None = None
    assets: list[str] | None = None
    interval_unit: IntervalUnit | None = None
    interval_value: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool | None = None

    @field_validator("assets")
    @classmethod
    def validate_assets(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("至少选择一项采集资产")
        unknown = sorted(set(value) - set(SOURCE_ASSETS))
        if unknown:
            raise ValueError(f"不支持的采集资产: {', '.join(unknown)}")
        return sorted(set(value), key=list(SOURCE_ASSETS).index)


class CollectionJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    datasource_id: int
    datasource_name: str | None = None
    assets: list[str]
    interval_unit: str
    interval_value: int
    interval_seconds: int
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str
    last_error: str | None
    created_by: str | None


class IngestRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int | None
    datasource_id: int
    trigger_type: str
    status: str
    assets: list[str]
    started_at: datetime
    finished_at: datetime | None
    rows_read: int
    rows_written: int
    tables_new: int
    tables_changed: int
    tables_removed: int
    columns_new: int
    columns_changed: int
    lineage_new: int
    lineage_removed: int
    column_lineage_new: int
    error_message: str | None


class CollectRequest(BaseModel):
    assets: list[str] = Field(default_factory=lambda: list(SOURCE_ASSETS))

    @field_validator("assets")
    @classmethod
    def validate_assets(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(SOURCE_ASSETS))
        if unknown:
            raise ValueError(f"不支持的采集资产: {', '.join(unknown)}")
        return list(value)


class ApiSourceConfig(BaseModel):
    mode: Literal["test", "production"] = "test"
    test_base_url: str = ""
    production_base_url: str = ""
    endpoints: dict[str, str] | None = None
    auth_type: Literal["none", "api_key", "bearer"] = "none"
    auth_token: str | None = None
    auth_header: str = "X-API-Key"
    page_size: int = Field(default=500, ge=50, le=5000)
