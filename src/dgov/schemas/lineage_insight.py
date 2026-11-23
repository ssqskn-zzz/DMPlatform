from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------- agent endpoints ----------

class AgentEndpointBase(BaseModel):
    agent_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    auth_type: Literal["none", "api_key", "bearer"] = "none"
    auth_token: str | None = None
    model: str | None = None
    timeout_ms: int = Field(default=20_000, ge=1000, le=120_000)
    max_retries: int = Field(default=2, ge=0, le=5)
    enabled: bool = True
    extra: dict[str, Any] | None = None


class AgentEndpointCreate(AgentEndpointBase):
    pass


class AgentEndpointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    auth_type: Literal["none", "api_key", "bearer"] | None = None
    auth_token: str | None = None
    model: str | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=120_000)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    enabled: bool | None = None
    extra: dict[str, Any] | None = None


class AgentEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_code: str
    name: str
    base_url: str
    auth_type: str
    has_token: bool = False
    model: str | None
    timeout_ms: int
    max_retries: int
    enabled: bool
    health_status: str
    last_checked_at: datetime | None
    last_error: str | None
    extra: dict[str, Any] | None
    created_at: datetime


# ---------- lineage specs (reuses DataStandard) ----------

class LineageSpecRead(BaseModel):
    id: int
    code: str
    version: str
    name: str
    status: str
    full_text: str
    category: str
    updated_at: datetime


# ---------- flows ----------

class LineageFlowBase(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    trigger_type: Literal["manual", "event", "schedule"] = "event"
    interval_unit: Literal["minute", "hour", "day"] = "hour"
    interval_value: int = Field(default=1, ge=1, le=1440)
    enabled: bool = True
    scope: dict[str, Any] = Field(default_factory=dict)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    alert_policy: dict[str, Any] = Field(default_factory=dict)


class LineageFlowCreate(LineageFlowBase):
    pass


class LineageFlowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    trigger_type: Literal["manual", "event", "schedule"] | None = None
    interval_unit: Literal["minute", "hour", "day"] | None = None
    interval_value: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool | None = None
    scope: dict[str, Any] | None = None
    stages: list[dict[str, Any]] | None = None
    alert_policy: dict[str, Any] | None = None


class LineageFlowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    version: int
    enabled: bool
    trigger_type: str
    interval_unit: str
    interval_value: int
    next_run_at: datetime | None
    scope: dict[str, Any]
    stages: list[dict[str, Any]]
    alert_policy: dict[str, Any]
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class FlowRunRequest(BaseModel):
    force: bool = False
    since: str | None = None


class LineageFlowRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    flow_id: int
    flow_code: str
    flow_version: int
    trigger_type: str
    actor: str | None
    status: str
    spec_version: str
    ingest_run_id: int | None
    cursor_from: str | None
    cursor_to: str | None
    input_hash: str
    snapshot: dict[str, Any]
    stages_result: list[dict[str, Any]]
    degraded: bool
    issue_count: int
    alert_count: int
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None


# ---------- issues / alerts ----------

class IssueReview(BaseModel):
    status: Literal["open", "confirmed", "ignored", "resolved"]
    assignee: str | None = None
    review_note: str | None = None


class LineageIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    code: str
    category: str
    severity: str
    confidence: float
    source: str
    title: str
    description: str | None
    affected: list[Any]
    evidence: dict[str, Any] | None
    suggestion: str | None
    status: str
    assignee: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    dedupe_key: str
    created_at: datetime


class LineageAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    issue_id: int
    channel: str
    level: str
    status: str
    payload: dict[str, Any] | None
    dedupe_key: str
    sent_at: datetime | None
    retry_count: int
    error: str | None
    created_at: datetime
