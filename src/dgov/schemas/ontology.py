from typing import Any

from pydantic import BaseModel, Field


class OntologyNodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    node_type: str = Field(default="entity", description="class / entity / property / concept")
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class OntologyNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    properties: dict[str, Any] | None = None


class OntologyNode(BaseModel):
    uid: str
    name: str
    node_type: str
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class OntologyRelationCreate(BaseModel):
    source_uid: str
    target_uid: str
    relation_type: str = Field(min_length=1, max_length=64)
    description: str | None = None


class OntologyRelation(BaseModel):
    uid: str
    source_uid: str
    target_uid: str
    relation_type: str
    description: str | None = None
    created_at: str | None = None


class OntologyGraph(BaseModel):
    nodes: list[OntologyNode] = Field(default_factory=list)
    relations: list[OntologyRelation] = Field(default_factory=list)
