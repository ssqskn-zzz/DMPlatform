from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.models.base import utcnow
from dgov.models.metadata import OperationLog

logger = logging.getLogger(__name__)

LOG_CATEGORIES = {
    "datasource_schedule": "数据源调度",
    "asset_inventory": "数据资产盘点",
    "metadata_change": "元数据变更",
    "lineage_change": "数据血缘变更",
    "quality_check": "数据质量巡检",
    "access_control": "权限配置",
    "lineage_insight": "血缘智能巡检",
    "agent_call": "智能体调用",
    "lineage_spec": "血缘开发规范",
}


async def write_log(
    session: AsyncSession,
    *,
    category: str,
    action: str,
    message: str,
    level: str = "info",
    actor: str | None = None,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> OperationLog:
    entry = OperationLog(
        category=category,
        action=action,
        level=level,
        actor=actor,
        resource_type=resource_type,
        resource_id=None if resource_id is None else str(resource_id),
        message=message[:512],
        detail=detail,
    )
    session.add(entry)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return entry


async def query_logs(
    session: AsyncSession,
    *,
    category: str | None = None,
    level: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[OperationLog], int]:
    stmt = select(OperationLog)
    count_stmt = select(func.count()).select_from(OperationLog)
    conditions = []
    if category:
        conditions.append(OperationLog.category == category)
    if level:
        conditions.append(OperationLog.level == level)
    if keyword:
        conditions.append(
            OperationLog.message.ilike(f"%{keyword}%")
            | OperationLog.action.ilike(f"%{keyword}%")
        )
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return list(rows), total


async def category_stats(session: AsyncSession, days: int = 7) -> list[dict[str, Any]]:
    since = utcnow() - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                OperationLog.category,
                func.count(),
                func.sum(case((OperationLog.level.in_(["error", "warning"]), 1), else_=0)),
            )
            .where(OperationLog.created_at >= since)
            .group_by(OperationLog.category)
        )
    ).all()
    counts = {category: (total, problems or 0) for category, total, problems in rows}
    return [
        {
            "category": category,
            "name": name,
            "count": counts.get(category, (0, 0))[0],
            "problems": counts.get(category, (0, 0))[1],
            "days": days,
        }
        for category, name in LOG_CATEGORIES.items()
    ]


async def cleanup_logs(
    session: AsyncSession,
    *,
    retention_days: int,
    max_rows: int,
) -> dict[str, int]:
    """Prune by age first, then by hard row cap (industry-standard dual guard)."""

    started = time.perf_counter()
    cutoff = utcnow() - timedelta(days=max(retention_days, 1))
    expired = await session.execute(
        delete(OperationLog).where(OperationLog.created_at < cutoff)
    )
    deleted_expired = expired.rowcount or 0

    total = (
        await session.execute(select(func.count()).select_from(OperationLog))
    ).scalar_one()
    deleted_overflow = 0
    if total > max_rows:
        boundary = (
            await session.execute(
                select(OperationLog.id)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .offset(max_rows)
                .limit(1)
            )
        ).scalar_one_or_none()
        if boundary is not None:
            overflow = await session.execute(
                delete(OperationLog).where(OperationLog.id <= boundary)
            )
            deleted_overflow = overflow.rowcount or 0

    await session.commit()
    result = {
        "deleted_expired": deleted_expired,
        "deleted_overflow": deleted_overflow,
        "remaining": max(total - deleted_overflow, 0),
        "retention_days": retention_days,
        "max_rows": max_rows,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    logger.info(
        "operation log retention completed: expired=%s overflow=%s remaining=%s",
        deleted_expired,
        deleted_overflow,
        result["remaining"],
    )
    return result
