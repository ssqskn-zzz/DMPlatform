from __future__ import annotations

from fastapi import APIRouter, Query, status

from dgov.api.deps import CollectionRunnerDep, ConfigManagerDep, SessionDep
from dgov.core.exceptions import BadRequestError, NotFoundError
from dgov.models.datasource import DataSource
from dgov.models.metadata import CollectionJob, IngestRun
from dgov.schemas.collection import (
    ApiSourceConfig,
    CollectionJobCreate,
    CollectionJobRead,
    CollectionJobUpdate,
    CollectRequest,
    IngestRunRead,
)
from dgov.services import logs
from dgov.services.ingest import next_run_at, run_collection
from dgov.services.metadata_client import (
    MetadataApiClient,
    auth_headers,
    resolve_base_url,
    resolve_endpoints,
    source_config,
)
from dgov.services.scheduler import ensure_schedule

router = APIRouter(prefix="/collection-jobs", tags=["collection"])
datasource_router = APIRouter(prefix="/datasources", tags=["datasources"])


def _to_read(job: CollectionJob, datasource_name: str | None) -> CollectionJobRead:
    multiplier = {"minute": 60, "hour": 3600, "day": 86400}.get(job.interval_unit, 0)
    return CollectionJobRead(
        id=job.id,
        name=job.name,
        datasource_id=job.datasource_id,
        datasource_name=datasource_name,
        assets=list(job.assets or []),
        interval_unit=job.interval_unit,
        interval_value=job.interval_value,
        interval_seconds=multiplier * job.interval_value,
        enabled=job.enabled,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        last_status=job.last_status,
        last_error=job.last_error,
        created_by=job.created_by,
    )


async def _get_job(session, job_id: int) -> CollectionJob:
    job = await session.get(CollectionJob, job_id)
    if job is None:
        raise NotFoundError("采集任务不存在")
    return job


@router.get("", response_model=list[CollectionJobRead])
async def list_jobs(
    session: SessionDep,
    datasource_id: int | None = None,
) -> list[CollectionJobRead]:
    from sqlalchemy import select

    stmt = select(CollectionJob).order_by(CollectionJob.id)
    if datasource_id:
        stmt = stmt.where(CollectionJob.datasource_id == datasource_id)
    jobs = list((await session.execute(stmt)).scalars())
    sources = {
        source.id: source.name
        for source in (await session.execute(select(DataSource))).scalars()
    }
    return [_to_read(job, sources.get(job.datasource_id)) for job in jobs]


@router.post("", response_model=CollectionJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: CollectionJobCreate,
    session: SessionDep,
    manager: ConfigManagerDep,
) -> CollectionJobRead:
    datasource = await session.get(DataSource, payload.datasource_id)
    if datasource is None:
        raise NotFoundError("数据源不存在")
    if datasource.type != "api":
        raise BadRequestError("采集调度当前仅支持 API 类型数据源")
    job = CollectionJob(
        **payload.model_dump(),
        created_by=manager.username,
        last_status="never",
    )
    if job.enabled:
        job.next_run_at = next_run_at(job.interval_unit, job.interval_value)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return _to_read(job, datasource.name)


@router.patch("/{job_id}", response_model=CollectionJobRead)
async def update_job(
    job_id: int,
    payload: CollectionJobUpdate,
    session: SessionDep,
    manager: ConfigManagerDep,
) -> CollectionJobRead:
    job = await _get_job(session, job_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(job, field, value)
    if job.datasource_id:
        datasource = await session.get(DataSource, job.datasource_id)
        if datasource is None:
            raise NotFoundError("数据源不存在")
    else:
        datasource = None
    if "enabled" in changes:
        job.next_run_at = (
            next_run_at(job.interval_unit, job.interval_value) if job.enabled else None
        )
    elif job.enabled and job.next_run_at is None:
        await ensure_schedule(job)
    await session.commit()
    await session.refresh(job)
    await logs.write_log(
        session,
        category="datasource_schedule",
        action="schedule.updated",
        message=f"采集任务「{job.name}」配置已更新",
        actor=manager.username,
        resource_type="collection_job",
        resource_id=job.id,
        detail=changes,
    )
    return _to_read(job, datasource.name if datasource else None)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: int, session: SessionDep, manager: ConfigManagerDep) -> None:
    job = await _get_job(session, job_id)
    await session.delete(job)
    await session.commit()
    await logs.write_log(
        session,
        category="datasource_schedule",
        action="schedule.deleted",
        message=f"采集任务「{job.name}」已删除",
        actor=manager.username,
        resource_type="collection_job",
        resource_id=job_id,
    )


@router.post("/{job_id}/run")
async def run_job(
    job_id: int,
    session: SessionDep,
    runner: CollectionRunnerDep,
) -> dict:
    job = await _get_job(session, job_id)
    datasource = await session.get(DataSource, job.datasource_id)
    if datasource is None:
        raise NotFoundError("数据源不存在")
    job.last_run_at = None
    run, summary = await run_collection(
        session,
        datasource,
        list(job.assets or []),
        trigger_type="manual",
        job_id=job.id,
        actor=runner.username,
    )
    job = await _get_job(session, job.id)
    job.last_run_at = run.started_at
    job.last_status = summary.get("status", "unknown")
    job.last_error = summary.get("error")
    if job.enabled and job.next_run_at is None:
        job.next_run_at = next_run_at(job.interval_unit, job.interval_value)
    await session.commit()
    return {"summary": summary, "run": IngestRunRead.model_validate(run).model_dump(mode="json")}


@router.get("/{job_id}/runs", response_model=list[IngestRunRead])
async def job_runs(
    job_id: int,
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[IngestRunRead]:
    from sqlalchemy import select

    await _get_job(session, job_id)
    runs = list(
        (
            await session.execute(
                select(IngestRun)
                .where(IngestRun.job_id == job_id)
                .order_by(IngestRun.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return [IngestRunRead.model_validate(run) for run in runs]


@router.get("/runs/all", response_model=list[IngestRunRead])
async def all_runs(
    session: SessionDep,
    datasource_id: int | None = None,
    limit: int = Query(default=30, ge=1, le=200),
) -> list[IngestRunRead]:
    from sqlalchemy import select

    stmt = select(IngestRun).order_by(IngestRun.id.desc()).limit(limit)
    if datasource_id:
        stmt = stmt.where(IngestRun.datasource_id == datasource_id)
    return [IngestRunRead.model_validate(run) for run in (await session.execute(stmt)).scalars()]


@datasource_router.put("/{datasource_id}/api-config")
async def update_api_config(
    datasource_id: int,
    payload: ApiSourceConfig,
    session: SessionDep,
    manager: ConfigManagerDep,
) -> dict:
    datasource = await session.get(DataSource, datasource_id)
    if datasource is None:
        raise NotFoundError("数据源不存在")
    if datasource.type != "api":
        raise BadRequestError("仅 API 类型数据源支持该配置")
    config = dict(datasource.config or {})
    config.update(payload.model_dump(exclude_none=True))
    if payload.mode == "test" and not config.get("test_base_url"):
        raise BadRequestError("测试环境地址不能为空")
    if payload.mode == "production" and not config.get("production_base_url"):
        raise BadRequestError("切换到生产环境前必须配置 production_base_url")
    datasource.config = config
    if payload.mode == "production":
        datasource.host = str(config.get("production_base_url") or "")
    await session.commit()
    await session.refresh(datasource)
    await logs.write_log(
        session,
        category="datasource_schedule",
        action="datasource.api_config_updated",
        message=f"数据源「{datasource.name}」API 配置已更新，当前环境 {payload.mode}",
        actor=manager.username,
        resource_type="data_source",
        resource_id=datasource.id,
        detail={"mode": payload.mode, "test_base_url": config.get("test_base_url")},
    )
    return {
        "id": datasource.id,
        "name": datasource.name,
        "mode": config.get("mode", "test"),
        "resolved_base_url": resolve_base_url(datasource),
        "endpoints": resolve_endpoints(datasource),
        "auth_type": config.get("auth_type", "none"),
    }


@datasource_router.get("/{datasource_id}/api-config")
async def get_api_config(session: SessionDep, datasource_id: int) -> dict:
    datasource = await session.get(DataSource, datasource_id)
    if datasource is None:
        raise NotFoundError("数据源不存在")
    if datasource.type != "api":
        raise BadRequestError("仅 API 类型数据源支持该配置")
    config = source_config(datasource)
    return {
        "id": datasource.id,
        "name": datasource.name,
        "mode": config.get("mode", "test"),
        "test_base_url": config.get("test_base_url", ""),
        "production_base_url": config.get("production_base_url", ""),
        "endpoints": resolve_endpoints(datasource),
        "auth_type": config.get("auth_type", "none"),
        "auth_header": config.get("auth_header", "X-API-Key"),
        "page_size": config.get("page_size", 500),
        "has_auth_token": bool(config.get("auth_token")),
        "resolved_base_url": resolve_base_url(datasource),
        "request_headers": sorted(auth_headers(datasource)),
    }


@datasource_router.post("/{datasource_id}/api-test")
async def test_api_connection(
    datasource_id: int,
    session: SessionDep,
    manager: ConfigManagerDep,
) -> dict:
    datasource = await session.get(DataSource, datasource_id)
    if datasource is None:
        raise NotFoundError("数据源不存在")
    try:
        client = MetadataApiClient(datasource)
        health = await client.health()
        return {
            "reachable": True,
            "mode": source_config(datasource).get("mode", "test"),
            "base_url": client.base_url,
            "health": health,
        }
    except Exception as exc:
        return {
            "reachable": False,
            "mode": source_config(datasource).get("mode", "test"),
            "error": str(exc)[:300],
        }


@datasource_router.post("/{datasource_id}/collect")
async def collect_datasource(
    datasource_id: int,
    payload: CollectRequest,
    session: SessionDep,
    runner: CollectionRunnerDep,
) -> dict:
    datasource = await session.get(DataSource, datasource_id)
    if datasource is None:
        raise NotFoundError("数据源不存在")
    run, summary = await run_collection(
        session,
        datasource,
        payload.assets,
        trigger_type="manual",
        actor=runner.username,
    )
    return {"summary": summary, "run": IngestRunRead.model_validate(run).model_dump(mode="json")}
