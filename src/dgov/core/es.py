import logging
from typing import Any

from elasticsearch import AsyncElasticsearch

from dgov.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncElasticsearch | None = None


def get_es() -> AsyncElasticsearch:
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {
            "hosts": [settings.es_url],
            "request_timeout": settings.es_request_timeout,
            "max_retries": 3,
            "retry_on_timeout": True,
        }
        if settings.es_username and settings.es_password:
            kwargs["basic_auth"] = (settings.es_username, settings.es_password)
        _client = AsyncElasticsearch(**kwargs)
    return _client


async def close_es() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def semantic_index_mapping() -> dict[str, Any]:
    """Mapping for semantic assets: metadata fields plus an externally supplied dense vector."""
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "asset_id": {"type": "long"},
                "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "asset_type": {"type": "keyword"},
                "dataset_id": {"type": "long"},
                "description": {"type": "text"},
                "synonyms": {"type": "text"},
                "tags": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                "updated_at": {"type": "date"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.embedding_dim,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    }


async def ensure_semantic_index() -> None:
    es = get_es()
    if await es.indices.exists(index=settings.es_semantic_index):
        return
    await es.indices.create(index=settings.es_semantic_index, **semantic_index_mapping())
    logger.info(
        "created Elasticsearch index %s (dims=%d)",
        settings.es_semantic_index,
        settings.embedding_dim,
    )
