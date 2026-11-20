from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SemanticAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    asset_type: str = Field(max_length=32, description="entity / metric / dimension / measure")
    dataset_id: int | None = None
    definition: str | None = None
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str = "draft"


class SemanticAssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    asset_type: str | None = None
    dataset_id: int | None = None
    definition: str | None = None
    description: str | None = None
    synonyms: list[str] | None = None
    owner: str | None = None
    tags: list[str] | None = None
    status: str | None = None


class SemanticAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    asset_type: str
    dataset_id: int | None
    definition: str | None
    description: str | None
    synonyms: list[str]
    owner: str | None
    tags: list[str]
    status: str
    embedding_model: str | None
    embedded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EmbeddingUpsert(BaseModel):
    """Vector computed by an external embedding service."""

    vector: list[float] = Field(min_length=1)
    model: str = Field(min_length=1, max_length=128)


class SemanticSearchRequest(BaseModel):
    vector: list[float] = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    asset_type: str | None = None
    dataset_id: int | None = None


class SemanticSearchHit(BaseModel):
    asset_id: int
    score: float
    name: str
    asset_type: str | None = None
    dataset_id: int | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class SemanticSearchResponse(BaseModel):
    total: int
    hits: list[SemanticSearchHit]
