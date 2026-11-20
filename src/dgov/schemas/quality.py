from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QualityRuleCreate(BaseModel):
    dataset_id: int
    column_id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    rule_type: str = Field(max_length=32, description="not_null / unique / range / regex / sql / custom")
    expression: str = Field(min_length=1)
    severity: str = "medium"
    description: str | None = None
    enabled: bool = True


class QualityRuleUpdate(BaseModel):
    column_id: int | None = None
    name: str | None = None
    rule_type: str | None = None
    expression: str | None = None
    severity: str | None = None
    description: str | None = None
    enabled: bool | None = None


class QualityRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    column_id: int | None
    name: str
    rule_type: str
    expression: str
    severity: str
    description: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class QualityResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    status: str
    score: float | None
    detail: dict[str, Any] | None
    executed_at: datetime
