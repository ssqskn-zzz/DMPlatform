"""Seed the default API data source and its collection job."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.config import settings
from dgov.models.datasource import DataSource
from dgov.models.governance import GovernanceAgent, GovernanceService
from dgov.models.lineage_insight import AgentEndpoint
from dgov.models.metadata import CollectionJob, MetadataTable
from dgov.services.ingest import next_run_at, run_collection
from dgov.services.lineage_insight import ensure_default_flow
from dgov.services.lineage_spec import ensure_default_spec
from dgov.services.metadata_client import DEFAULT_ENDPOINTS, SOURCE_ASSETS
from dgov.services.quality_meta import seed_quality_rules

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_NAME = "元数据测试 API"
DEFAULT_JOB_NAME = "全量元数据采集"
LINEAGE_INCREMENTAL_JOB_NAME = "血缘关系增量采集"
LINEAGE_AGENT_CODE = "AG-LINEAGE-01"
LINEAGE_SERVICE_CODE = "SVC-LINEAGE-01"


async def seed_lineage_insight_defaults(session, source: DataSource) -> None:
    """Seed everything the lineage insight pipeline needs on first boot."""
    await ensure_default_spec(session)
    await ensure_default_flow(session)

    service = (
        await session.execute(
            select(GovernanceService).where(GovernanceService.code == LINEAGE_SERVICE_CODE)
        )
    ).scalar_one_or_none()
    if service is None:
        service = GovernanceService(
            code=LINEAGE_SERVICE_CODE,
            name="血缘解析与问题识别",
            engine="Agent 就绪引擎",
            input_desc="血缘开发规范 + 表级血缘数据",
            output_desc="任务依赖图 + 结构化问题清单",
            evidence="请求指纹、运行档案与复核记录",
            allow_system="✓",
            allow_retrieval="✓",
            allow_training="✕",
            allow_output="✕",
            description="血缘智能巡检专用服务契约：只读诊断，不对外输出血缘明细",
        )
        session.add(service)
        await session.flush()

    agent = (
        await session.execute(
            select(GovernanceAgent).where(GovernanceAgent.code == LINEAGE_AGENT_CODE)
        )
    ).scalar_one_or_none()
    if agent is None:
        agent = GovernanceAgent(
            code=LINEAGE_AGENT_CODE,
            name="血缘巡检智能体",
            kind="治理",
            owner="数据治理平台",
            version="1.0",
            status="on",
            boundary="复核",
            description="按血缘开发规范解析任务依赖图并识别问题，结论需人工复核",
        )
        agent.services = [service]
        session.add(agent)
        await session.flush()

    endpoint = (
        await session.execute(
            select(AgentEndpoint).where(AgentEndpoint.agent_code == LINEAGE_AGENT_CODE)
        )
    ).scalar_one_or_none()
    if endpoint is None:
        session.add(
            AgentEndpoint(
                agent_code=LINEAGE_AGENT_CODE,
                name="内置血缘巡检智能体（Mock）",
                base_url=settings.agent_default_base_url,
                auth_type="none",
                model="mock-lineage-agent",
                timeout_ms=settings.agent_timeout_ms,
                max_retries=settings.agent_max_retries,
                enabled=True,
                health_status="unknown",
            )
        )

    job = (
        await session.execute(
            select(CollectionJob).where(CollectionJob.name == LINEAGE_INCREMENTAL_JOB_NAME)
        )
    ).scalar_one_or_none()
    if job is None:
        session.add(
            CollectionJob(
                datasource_id=source.id,
                name=LINEAGE_INCREMENTAL_JOB_NAME,
                assets=["table_lineage"],
                interval_unit="hour",
                interval_value=1,
                enabled=True,
                next_run_at=next_run_at("hour", 1),
                last_status="never",
                created_by="system",
            )
        )


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
    await seed_lineage_insight_defaults(session, source)
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

    try:
        from dgov.services.metadata_client import MetadataApiClient

        await MetadataApiClient(source).health()
    except Exception as exc:  # fixture API is optional during local development
        logger.warning("metadata fixture API not reachable: %s", exc)
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
