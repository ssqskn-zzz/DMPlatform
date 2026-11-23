"""Strict platform contract for the lineage parse / diagnose agent APIs.

Every response is validated before anything is written.  Unknown enums,
out-of-range confidence or unparseable payloads make the stage fail (and fall
back to the local rule engine) instead of leaking hallucinated data into the DB.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LineageEdgePayload(BaseModel):
    source_table_id: str
    target_table_id: str
    source_table: str = ""
    target_table: str = ""
    source_schema: str = ""
    target_schema: str = ""
    layer: str = ""
    type: str = "0"
    op: Literal["upsert", "delete"] = "upsert"


class SpecPayload(BaseModel):
    version: str = "v1"
    clauses: list[str] = Field(default_factory=list)
    full_text: str = ""


class ParseRequest(BaseModel):
    request_id: str
    spec: SpecPayload
    edges: list[LineageEdgePayload] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)


class TaskGraphNode(BaseModel):
    task: str
    table: str = ""
    layer: str = ""


class TaskGraphEdge(BaseModel):
    source_task: str
    target_task: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class TaskGraph(BaseModel):
    nodes: list[TaskGraphNode] = Field(default_factory=list)
    edges: list[TaskGraphEdge] = Field(default_factory=list)


class ParseResult(BaseModel):
    task_graph: TaskGraph = Field(default_factory=TaskGraph)
    violations: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


class ParseResponse(BaseModel):
    request_id: str
    result: ParseResult = Field(default_factory=ParseResult)
    model: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class DiagnosePolicy(BaseModel):
    categories: list[str] = Field(default_factory=list)
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    thresholds: dict[str, float] = Field(default_factory=dict)


class DiagnoseRequest(BaseModel):
    request_id: str
    spec: SpecPayload
    edges: list[LineageEdgePayload] = Field(default_factory=list)
    task_graph: TaskGraph = Field(default_factory=TaskGraph)
    policy: DiagnosePolicy = Field(default_factory=DiagnosePolicy)


class AgentIssue(BaseModel):
    code: str
    category: str = ""
    severity: Literal["high", "medium", "low"] = "medium"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    affected: list[str] = Field(default_factory=list)
    evidence: str = ""
    suggestion: str = ""


class DiagnoseResult(BaseModel):
    issues: list[AgentIssue] = Field(default_factory=list)


class DiagnoseResponse(BaseModel):
    request_id: str
    result: DiagnoseResult = Field(default_factory=DiagnoseResult)
    model: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
