from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    engine: str
    input_desc: str
    output_desc: str
    evidence: str
    allow_system: str
    allow_retrieval: str
    allow_training: str
    allow_output: str
    description: str | None


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    kind: str
    owner: str
    version: str
    status: str
    boundary: str
    description: str
    services: list[ServiceRead]


class SemanticTermRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    domain: str
    owner: str
    version: str
    status: str
    definition: str | None
    effective_date: str | None


class WorkOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str
    subtitle: str | None
    domain: str
    source: str
    owner: str
    action_label: str
    status: str
    kind: str
    description: str | None
    evidence: str | None
    impact: str | None
    closed_at: datetime | None


class ChangeRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str
    subtitle: str | None
    term_name: str
    owner: str
    from_version: str
    to_version: str
    description: str
    basis: str
    status: str
    approved_by: str | None
    approved_at: datetime | None
    affected_agents: list[str]
    affected_objects: list[str]


class EvaluationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_code: str
    agent_code: str
    status: str
    metrics: dict[str, Any]
    feedback: str
    steps: list[dict[str, str]]
    evidence: dict[str, str]
    created_at: datetime


class RuntimeLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None
    level: str
    message: str
    created_at: datetime


class TodoItem(BaseModel):
    code: str
    title: str
    domain: str
    source: str
    owner: str
    action: str
    status: str
    kind: str
    subtitle: str | None = None


class RunResult(BaseModel):
    run: EvaluationRunRead
    logs: list[RuntimeLogRead]


class ApprovalResult(BaseModel):
    change_request: ChangeRequestRead
    term: SemanticTermRead
    affected_agents: list[str]
    regression_run: EvaluationRunRead | None


class SimulateResult(BaseModel):
    blocked: bool
    logs: list[RuntimeLogRead]


class ChangeApproval(BaseModel):
    approved_by: str = "孙瑜"
