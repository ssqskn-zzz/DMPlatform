from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.config import settings
from dgov.core.db import SessionFactory
from dgov.models.base import utcnow
from dgov.models.datasource import DataSource
from dgov.models.metadata import CollectionJob
from dgov.services import logs
from dgov.services.ingest import next_run_at, run_collection
from dgov.services.quality_meta import prune_quality_results

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 60
_scheduler_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_last_maintenance = None


async def ensure_schedule(job: CollectionJob, *, from_now: bool = True) -> None:
    if job.enabled and job.next_run_at is None:
        job.next_run_at = next_run_at(job.interval_unit, job.interval_value)


async def run_due_jobs(session: AsyncSession) -> int:
    due = list(
        (
            await session.execute(
                select(CollectionJob)
                .where(
                    CollectionJob.enabled.is_(True),
                    CollectionJob.next_run_at.is_not(None),
                    CollectionJob.next_run_at <= utcnow(),
                )
                .order_by(CollectionJob.next_run_at)
                .limit(20)
            )
        ).scalars()
    )
    if not due:
        return 0
    for job in due:
        datasource = await session.get(DataSource, job.datasource_id)
        job.last_run_at = utcnow()
        job.next_run_at = next_run_at(job.interval_unit, job.interval_value)
        job.last_status = "running"
        await session.commit()
        if datasource is None or datasource.status != "active":
            job.last_status = "failed"
            job.last_error = "数据源不存在或已停用"
            await logs.write_log(
                session,
                category="datasource_schedule",
                action="schedule.skipped",
                message=f"调度任务「{job.name}」跳过：数据源不可用",
                level="warning",
                actor="scheduler",
                resource_type="collection_job",
                resource_id=job.id,
            )
            continue
        run, summary = await run_collection(
            session,
            datasource,
            list(job.assets or []),
            trigger_type="schedule",
            job_id=job.id,
            actor="scheduler",
        )
        job = await session.get(CollectionJob, job.id)
        job.last_status = summary.get("status", "unknown")
        job.last_error = summary.get("error")
        await session.commit()
    return len(due)


async def _maintenance(session: AsyncSession) -> None:
    global _last_maintenance
    now = utcnow()
    if _last_maintenance and now - _last_maintenance < timedelta(hours=6):
        return
    _last_maintenance = now
    await logs.cleanup_logs(
        session,
        retention_days=settings.log_retention_days,
        max_rows=settings.log_max_rows,
    )
    await prune_quality_results(session, settings.quality_result_retention_days)


async def _loop() -> None:
    logger.info("metadata scheduler started")
    while True:
        try:
            async with _lock:
                async with SessionFactory() as session:
                    await run_due_jobs(session)
                    await _maintenance(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the scheduler alive on transient failures
            logger.exception("scheduler tick failed")
        await asyncio.sleep(max(settings.scheduler_tick_seconds, 5))


def start_scheduler() -> None:
    global _scheduler_task
    if not settings.scheduler_enabled or _scheduler_task is not None:
        return
    _scheduler_task = asyncio.create_task(_loop())


async def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None
