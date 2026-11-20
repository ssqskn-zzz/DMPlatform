from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import EsDep, SessionDep
from dgov.schemas import (
    EmbeddingUpsert,
    Page,
    SemanticAssetCreate,
    SemanticAssetRead,
    SemanticAssetUpdate,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from dgov.services import semantic as service

router = APIRouter(prefix="/semantic", tags=["semantic"])


@router.get("/assets", response_model=Page[SemanticAssetRead])
async def list_assets(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    keyword: str | None = None,
    asset_type: str | None = None,
) -> Page[SemanticAssetRead]:
    items, total = await service.list_assets(session, page, page_size, keyword, asset_type)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/assets", response_model=SemanticAssetRead, status_code=status.HTTP_201_CREATED)
async def create_asset(payload: SemanticAssetCreate, session: SessionDep) -> SemanticAssetRead:
    return await service.create_asset(session, payload)


@router.get("/assets/{asset_id}", response_model=SemanticAssetRead)
async def get_asset(asset_id: int, session: SessionDep) -> SemanticAssetRead:
    return await service.get_asset(session, asset_id)


@router.patch("/assets/{asset_id}", response_model=SemanticAssetRead)
async def update_asset(
    asset_id: int, payload: SemanticAssetUpdate, session: SessionDep, es: EsDep
) -> SemanticAssetRead:
    return await service.update_asset(session, es, asset_id, payload)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: int, session: SessionDep, es: EsDep) -> Response:
    await service.delete_asset(session, es, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/assets/{asset_id}/embedding", response_model=SemanticAssetRead)
async def upsert_embedding(
    asset_id: int, payload: EmbeddingUpsert, session: SessionDep, es: EsDep
) -> SemanticAssetRead:
    """Write an externally computed embedding vector and make it searchable."""
    return await service.upsert_embedding(session, es, asset_id, payload)


@router.post("/search", response_model=SemanticSearchResponse)
async def search(payload: SemanticSearchRequest, es: EsDep) -> SemanticSearchResponse:
    """kNN recall over the semantic layer using an externally computed query vector."""
    return await service.search(es, payload)
