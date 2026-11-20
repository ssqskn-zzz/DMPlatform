import logging
from datetime import UTC, datetime

from elasticsearch import AsyncElasticsearch
from elasticsearch import NotFoundError as EsNotFoundError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.config import settings
from dgov.core.exceptions import BadRequestError, ConflictError, NotFoundError
from dgov.models.catalog import Dataset
from dgov.models.semantic import SemanticAsset
from dgov.schemas.semantic import (
    EmbeddingUpsert,
    SemanticAssetCreate,
    SemanticAssetUpdate,
    SemanticSearchHit,
    SemanticSearchRequest,
    SemanticSearchResponse,
)

logger = logging.getLogger(__name__)


def _validate_dimension(vector: list[float]) -> None:
    if len(vector) != settings.embedding_dim:
        raise BadRequestError(
            f"embedding dimension mismatch: got {len(vector)}, expected {settings.embedding_dim}"
        )


def _metadata_doc(asset: SemanticAsset) -> dict:
    return {
        "asset_id": asset.id,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "dataset_id": asset.dataset_id,
        "description": asset.description,
        "synonyms": asset.synonyms,
        "tags": asset.tags,
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def list_assets(
    session: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    asset_type: str | None = None,
) -> tuple[list[SemanticAsset], int]:
    stmt = select(SemanticAsset)
    count_stmt = select(func.count()).select_from(SemanticAsset)
    if keyword:
        condition = or_(
            SemanticAsset.name.ilike(f"%{keyword}%"),
            SemanticAsset.description.ilike(f"%{keyword}%"),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if asset_type is not None:
        stmt = stmt.where(SemanticAsset.asset_type == asset_type)
        count_stmt = count_stmt.where(SemanticAsset.asset_type == asset_type)
    total = (await session.execute(count_stmt)).scalar_one()
    result = await session.execute(
        stmt.order_by(SemanticAsset.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def get_asset(session: AsyncSession, asset_id: int) -> SemanticAsset:
    asset = await session.get(SemanticAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"semantic asset {asset_id} not found")
    return asset


async def create_asset(session: AsyncSession, payload: SemanticAssetCreate) -> SemanticAsset:
    if payload.dataset_id is not None and await session.get(Dataset, payload.dataset_id) is None:
        raise NotFoundError(f"dataset {payload.dataset_id} not found")
    asset = SemanticAsset(**payload.model_dump())
    session.add(asset)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"semantic asset '{payload.name}' already exists") from exc
    await session.refresh(asset)
    return asset


async def update_asset(
    session: AsyncSession, es: AsyncElasticsearch, asset_id: int, payload: SemanticAssetUpdate
) -> SemanticAsset:
    asset = await get_asset(session, asset_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("semantic asset name already exists") from exc
    await session.refresh(asset)

    # Keep searchable metadata in sync; the embedding itself is untouched.
    try:
        await es.update(
            index=settings.es_semantic_index,
            id=str(asset.id),
            doc=_metadata_doc(asset),
            refresh="wait_for",
        )
    except EsNotFoundError:
        logger.debug("semantic asset %s has no embedding yet, skip search sync", asset.id)
    except Exception as exc:  # search sync must not block metadata updates
        logger.warning("failed to sync semantic asset %s to Elasticsearch: %s", asset.id, exc)
    return asset


async def delete_asset(session: AsyncSession, es: AsyncElasticsearch, asset_id: int) -> None:
    asset = await get_asset(session, asset_id)
    await session.delete(asset)
    await session.commit()
    try:
        await es.delete(index=settings.es_semantic_index, id=str(asset_id), refresh="wait_for")
    except EsNotFoundError:
        pass
    except Exception as exc:  # keep the governance write path resilient to search outages
        logger.warning("failed to delete semantic asset %s from Elasticsearch: %s", asset_id, exc)


async def upsert_embedding(
    session: AsyncSession,
    es: AsyncElasticsearch,
    asset_id: int,
    payload: EmbeddingUpsert,
) -> SemanticAsset:
    """Write a vector produced by an external embedding pipeline and index it."""
    _validate_dimension(payload.vector)
    asset = await get_asset(session, asset_id)

    doc = _metadata_doc(asset)
    doc["embedding"] = payload.vector
    doc["embedding_model"] = payload.model
    await es.index(
        index=settings.es_semantic_index,
        id=str(asset.id),
        document=doc,
        refresh="wait_for",
    )

    asset.embedding_model = payload.model
    asset.embedded_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(asset)
    return asset


async def search(es: AsyncElasticsearch, payload: SemanticSearchRequest) -> SemanticSearchResponse:
    """Approximate kNN recall over externally supplied query embeddings."""
    _validate_dimension(payload.vector)
    knn: dict = {
        "field": "embedding",
        "query_vector": payload.vector,
        "k": payload.top_k,
        "num_candidates": max(payload.top_k * 10, 100),
    }
    filters = []
    if payload.asset_type:
        filters.append({"term": {"asset_type": payload.asset_type}})
    if payload.dataset_id is not None:
        filters.append({"term": {"dataset_id": payload.dataset_id}})
    if filters:
        knn["filter"] = filters

    response = await es.search(
        index=settings.es_semantic_index,
        knn=knn,
        size=payload.top_k,
        source={"excludes": ["embedding"]},
    )
    hits = [
        SemanticSearchHit(
            asset_id=hit["_source"]["asset_id"],
            score=float(hit["_score"]),
            name=hit["_source"]["name"],
            asset_type=hit["_source"].get("asset_type"),
            dataset_id=hit["_source"].get("dataset_id"),
            description=hit["_source"].get("description"),
            tags=hit["_source"].get("tags") or [],
        )
        for hit in response["hits"]["hits"]
    ]
    return SemanticSearchResponse(total=len(hits), hits=hits)
