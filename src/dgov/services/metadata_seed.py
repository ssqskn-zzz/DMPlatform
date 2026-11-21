"""Seed the default API data source and its collection job."""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.config import settings
from dgov.models.datasource import DataSource
from dgov.models.metadata import CollectionJob, MetadataTable
from dgov.services.ingest import run_collection
from dgov.services.metadata_client import DEFAULT_ENDPOINTS, SOURCE_ASSETS
from dgov.services.quality_meta import seed_quality_rules

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_NAME = "元数据测试 API"
DEFAULT_JOB_NAME = "全量元数据采集"


def default_api_config() -> dict:
    return {
        "mode": "test",
        "test_base_url": settings.mock_metadata_api_url,
        "production_base_url": "",
        "endpoints": dict(DEFAULT_ENDPOINTS),
        "auth_type": "none",
        "auth_token": "",
        "auth_header": "X-API-Key",
        "page_size": 500,
    }


async def seed_metadata_source(session: AsyncSession) -> DataSource | None:
    if not settings.seed_metadata_source:
        return None

    source = (
        await session.execute(
            select(DataSource).where(DataSource.name == DEFAULT_SOURCE_NAME)
        )
    ).scalar_one_or_none()
    if source is None:
        source = DataSource(
            name=DEFAULT_SOURCE_NAME,
            type="api",
            host=settings.mock_metadata_api_url,
            port=80,
            database=None,
            config=default_api_config(),
            description="测试环境内置的元数据 API，数据来自 tests/mock_metadata_api 中的 CSV。",
        )
        session.add(source)
        await session.flush()

    job = (
        await session.execute(
            select(CollectionJob).where(
                CollectionJob.datasource_id == source.id,
                CollectionJob.name == DEFAULT_JOB_NAME,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        session.add(
            CollectionJob(
                datasource_id=source.id,
                name=DEFAULT_JOB_NAME,
                assets=list(SOURCE_ASSETS),
                interval_unit="hour",
                interval_value=1,
                enabled=False,
                last_status="never",
                created_by="system",
            )
        )
    await seed_quality_rules(session)
    await session.commit()
    return source


async def bootstrap_collection(session: AsyncSession) -> dict | None:
    """Populate metadata on first boot when the fixture API is reachable."""

    source = (
        await session.execute(
            select(DataSource).where(DataSource.name == DEFAULT_SOURCE_NAME)
        )
    ).scalar_one_or_none()
    if source is None:
        return None
    existing = (
        await session.execute(select(func.count()).select_from(MetadataTable))
    ).scalar_one()
    if existing:
        return None

    base_url = str((source.config or {}).get("test_base_url") or "").rstrip("/")
    if not base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/health")
            response.raise_for_status()
    except Exception as exc:  # fixture API is optional during local development
        logger.warning("metadata fixture API not reachable at %s: %s", base_url, exc)
        return None

    _, summary = await run_collection(
        session,
        source,
        list(SOURCE_ASSETS),
        trigger_type="bootstrap",
        actor="system",
    )
    logger.info("bootstrap metadata collection finished: %s", summary.get("status"))
    return summary
